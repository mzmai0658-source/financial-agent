"""
RAG 知识库构建脚本。

当前策略：
  1. 统一使用 PaddleOCR JSON 作为 PDF 类文档的知识源
  2. 入库前按 source/source_json 清理旧切片，保证可重建
  3. 为财报和研报补充章节、页码、报告类型等 metadata
  4. 将表格摘要类 xlsx 作为辅助知识源保留
"""

import os
import sys
import re
import json
import html
import time
import hashlib
import shutil
import pandas as pd
import chromadb
from loguru import logger
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any

# 路径修正：从 src/agent → src → 项目根
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from src.agent.dashscope_embedding import DashScopeEmbeddingFunction
from config.rag_config import (
    RAG_SEMANTIC_CHUNK_OVERLAP,
    RAG_SEMANTIC_CHUNK_SIZE,
)
from src.utils.ocr_json_parser import (
    OCR_JSON_SUFFIXES,
    find_json_cache_for_pdf,
    iter_layout_pages,
    parse_ocr_json_to_content_and_chunks,
    read_ocr_json,
)
from src.utils.data_paths import find_financial_reports_root, find_research_reports_root

# ── 配置 ─────────────────────────────────────────────────────────────────────

CHROMA_DB_PATH = str(ROOT_DIR / "data" / "chroma_db")
COLLECTION_NAME = "financial_reports"

# 附件5：研报数据目录（相对项目根）
REPORTS_DIR = find_research_reports_root() or (ROOT_DIR / "正式数据" / "附件5：研报数据")

# 附件2：财报目录（用于 MD&A 提取）
FINANCIAL_REPORTS_DIR = find_financial_reports_root() or (ROOT_DIR / "正式数据" / "附件2：财务报告")

# MD&A 相关章节关键词（匹配标题）
MDA_SECTION_KEYWORDS = [
    '管理层讨论', '经营情况讨论', '经营情况', '报告期内经营情况', '主要业务情况',
    '主营业务情况', '报告期内业绩', '业绩驱动', '重要事项', '未来发展',
    '经营成果', '公司主要业务', '行业情况', '市场分析', '风险因素',
    '主要会计数据和财务指标发生变动的情况及原因',
    '主要财务指标发生变动的情况及原因',
    '主要会计数据和财务指标',
    '主要财务指标',
    '变动原因',
]
REASONING_SECTION_KEYWORDS = [
    '变动原因', '发生变动的情况及原因', '营收增长原因', '收入增长原因',
    '利润变化原因', '现金流变化原因', '业绩变动原因', '业绩驱动',
]
OPERATION_SECTION_KEYWORDS = [
    '经营情况', '经营成果', '主要业务情况', '主营业务情况', '业务回顾',
    '报告期内经营情况', '核心竞争力', '行业情况', '市场分析',
]
ANALYTIC_BODY_KEYWORDS = [
    '主要原因', '同比', '较上年', '增长', '下降', '分析', '影响',
    '带动', '推动', '受益于', '由于', '得益于', '恢复', '改善',
]
REASONING_BODY_KEYWORDS = [
    '变动原因', '主要原因', '带动', '推动', '受益于', '由于', '得益于',
    '并表', '协同', '恢复性增长', '品牌力', '渠道', '新品', '产品线',
]
RESEARCH_NOISE_KEYWORDS = [
    "目录", "目 录", "释义", "备查文件", "公司简介", "联系方式", "信息披露",
    "分析师声明", "法律声明", "免责声明", "评级说明", "重要声明",
    "请务必阅读正文之后的信息披露和免责申明", "请务必阅读正文后的重要声明",
    "证券研究报告", "投资评级说明", "图表目录",
]
RESEARCH_POLICY_KEYWORDS = [
    "医保", "医保目录", "国家医保", "集采", "带量采购", "国谈",
    "政策", "监管", "审批", "招标", "支付端", "商保",
]
RESEARCH_RISK_KEYWORDS = [
    "风险提示", "风险因素", "核心风险", "不确定性", "假设", "敏感性",
]
RESEARCH_CONCLUSION_KEYWORDS = [
    "投资要点", "核心观点", "投资建议", "结论", "摘要", "要点", "观点",
]
RESEARCH_OPERATION_KEYWORDS = [
    "业务回顾", "经营情况", "经营分析", "成长逻辑", "产品线", "渠道",
    "品类", "板块", "竞争格局", "业绩驱动", "催化", "看点",
]
RESEARCH_ANALYTIC_KEYWORDS = [
    "预计", "维持", "上调", "下调", "驱动", "受益于", "主要原因", "同比",
    "增长", "下降", "恢复", "改善", "带动", "推动",
]

SEMANTIC_TITLE_LABELS = {"doc_title", "paragraph_title"}
SEMANTIC_TEXT_LABELS = {"text", "content", "vision_footnote", "chart"}
SEMANTIC_IGNORED_LABELS = {"header", "footer", "number", "image"}
SEMANTIC_OVERLAP_CHARS = RAG_SEMANTIC_CHUNK_OVERLAP

# ── Embedding 初始化 ──────────────────────────────────────────────────────────

def get_embedding_function():
    """
    获取 Embedding 函数。
    优先使用 DashScope 千问 text-embedding-v4，确保构建/查询使用相同模型。
    """
    try:
        ef = DashScopeEmbeddingFunction(model_name="text-embedding-v4")
        if ef.dashscope is not None and ef.api_key:
            logger.info("使用 DashScope text-embedding-v4")
            return ef
    except Exception as e:
        logger.warning(f"DashScope init failed: {e}")

    logger.warning("DashScope Embedding 不可用，回退到 all-MiniLM-L6-v2")
    logger.warning("⚠ 注意：RAG 构建和查询必须使用相同的 Embedding 模型！")
    from chromadb.utils import embedding_functions
    return embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="all-MiniLM-L6-v2"
    )


def init_chroma() -> Any:
    """初始化 ChromaDB 集合。"""
    Path(CHROMA_DB_PATH).mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    ef = get_embedding_function()
    try:
        return client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )
    except Exception as e:
        if "Embedding function conflict" not in str(e):
            raise
        logger.warning(f"检测到已存在向量库嵌入配置冲突，改用兼容的 sentence-transformer: {e}")
        from chromadb.utils import embedding_functions
        fallback_ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )
        return client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=fallback_ef,
            metadata={"hnsw:space": "cosine"},
        )


def reset_chroma_db() -> None:
    """清空整个 ChromaDB 目录，确保重建结果不混入历史数据。"""
    db_path = Path(CHROMA_DB_PATH)
    if db_path.exists():
        shutil.rmtree(db_path)
        logger.info(f"已清空旧知识库目录: {db_path}")
    db_path.mkdir(parents=True, exist_ok=True)


# ── 文本切片 ──────────────────────────────────────────────────────────────────

def _is_top_level_title(title: str) -> bool:
    return bool(re.match(r"^第[一二三四五六七八九十百]+节", title.strip()))


def _normalize_block_text(text: str) -> str:
    text = html.unescape(str(text or ""))
    text = text.replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _html_block_to_text(text: str) -> str:
    normalized = str(text or "")
    normalized = re.sub(r"</?(table|tbody|thead|html|body)[^>]*>", "\n", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"<tr[^>]*>", "\n", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"</tr>", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"<t[dh][^>]*>", " | ", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"</t[dh]>", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"<br\\s*/?>", "\n", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"<[^>]+>", " ", normalized)
    normalized = html.unescape(normalized)
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r" *\n *", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip(" | \n")


def _split_semantic_units(text: str) -> List[str]:
    raw_parts = [part.strip() for part in re.split(r"\n{2,}", text) if part.strip()]
    units: List[str] = []
    for part in raw_parts:
        if len(part) <= 220:
            units.append(part)
            continue
        sentences = [seg.strip() for seg in re.split(r"(?<=[。！？；])", part) if seg.strip()]
        if not sentences:
            sentences = [part]
        for sentence in sentences:
            if len(sentence) <= 220:
                units.append(sentence)
                continue
            slices = re.split(r"(?<=[，、：:])", sentence)
            buffer = ""
            for item in slices:
                item = item.strip()
                if not item:
                    continue
                if len(buffer) + len(item) <= 180:
                    buffer += item
                else:
                    if buffer:
                        units.append(buffer)
                    buffer = item
            if buffer:
                units.append(buffer)
    return units


def _build_semantic_chunks(
    title_path: List[str],
    body_parts: List[str],
    target_size: int = RAG_SEMANTIC_CHUNK_SIZE,
) -> List[str]:
    clean_parts = [part.strip() for part in body_parts if part and part.strip()]
    if not clean_parts:
        return []

    units: List[str] = []
    for part in clean_parts:
        units.extend(_split_semantic_units(part))
    units = [u for u in units if u]
    if not units:
        return []

    title_lines = [line.strip() for line in title_path if line and line.strip()]
    title_prefix = "\n".join(title_lines[-3:]).strip()
    prefix_len = len(title_prefix) + (1 if title_prefix else 0)
    chunks: List[str] = []
    current_units: List[str] = []
    current_len = prefix_len

    for unit in units:
        unit_len = len(unit) + (1 if current_units else 0)
        if current_units and current_len + unit_len > target_size:
            body = "\n".join(current_units).strip()
            chunk = f"{title_prefix}\n{body}".strip() if title_prefix else body
            if len(chunk) > 50:
                chunks.append(chunk)

            overlap_units: List[str] = []
            overlap_len = 0
            for prev_unit in reversed(current_units):
                added_len = len(prev_unit) + (1 if overlap_units else 0)
                if overlap_len + added_len > SEMANTIC_OVERLAP_CHARS:
                    break
                overlap_units.insert(0, prev_unit)
                overlap_len += added_len

            current_units = overlap_units + [unit]
            current_len = prefix_len + sum(len(item) for item in current_units) + max(len(current_units) - 1, 0)
        else:
            current_units.append(unit)
            current_len += unit_len

    if current_units:
        body = "\n".join(current_units).strip()
        chunk = f"{title_prefix}\n{body}".strip() if title_prefix else body
        if len(chunk) > 50:
            chunks.append(chunk)

    deduped: List[str] = []
    for chunk in chunks:
        if not deduped or deduped[-1] != chunk:
            deduped.append(chunk)
    return deduped


def _contains_any(text: str, keywords: List[str]) -> bool:
    return any(keyword in str(text or "") for keyword in keywords)


def _join_title_path(title_path: List[str]) -> str:
    return " > ".join([item.strip() for item in title_path if str(item or "").strip()])


def _infer_report_period(text: str) -> str:
    raw = str(text or "")
    if any(k in raw for k in ["一季度", "第一季度", "Q1"]):
        return "Q1"
    if any(k in raw for k in ["半年度", "半年报", "中报", "上半年", "HY"]):
        return "HY"
    if any(k in raw for k in ["三季度", "第三季度", "前三季度", "Q3"]):
        return "Q3"
    if any(k in raw for k in ["年度报告", "年报", "全年", "年度"]):
        return "FY"
    return "UNKNOWN"


def _infer_report_kind(text: str) -> str:
    period = _infer_report_period(text)
    if period == "FY":
        return "annual"
    if period in {"Q1", "HY", "Q3"}:
        return "interim"
    return "unknown"


def _hash_identity(*parts: Any) -> str:
    raw = "||".join(str(part or "") for part in parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def _build_chunk_records(
    title_path: List[str],
    body_parts: List[str],
    target_size: int = 900,
    base_meta: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    chunk_texts = _build_semantic_chunks(title_path, body_parts, target_size=target_size)
    title_string = _join_title_path(title_path)
    section_title = next((item for item in reversed(title_path) if str(item or "").strip()), "")
    records: List[Dict[str, Any]] = []
    for idx, chunk in enumerate(chunk_texts):
        record = dict(base_meta or {})
        record.update({
            "text": chunk,
            "chunk_index": idx,
            "section_title": section_title,
            "title_path": title_string,
        })
        records.append(record)
    return records


def _looks_like_explanatory_table(body: str) -> bool:
    text = str(body or "")
    return (
        "表格标题" in text and _contains_any(text, REASONING_SECTION_KEYWORDS)
    ) or (
        "|" in text and _contains_any(text, ["变动原因", "原因说明", "项目", "同比变动"])
    )


def chunk_text(
    text: str,
    chunk_size: int = RAG_SEMANTIC_CHUNK_SIZE,
    overlap: int = RAG_SEMANTIC_CHUNK_OVERLAP,
) -> List[str]:
    """
    纯文本兜底切分：
    先按段落/句子做语义切分，再保留少量重叠。
    """
    global SEMANTIC_OVERLAP_CHARS
    previous_overlap = SEMANTIC_OVERLAP_CHARS
    SEMANTIC_OVERLAP_CHARS = overlap
    try:
        return _build_semantic_chunks([], [text], target_size=chunk_size)
    finally:
        SEMANTIC_OVERLAP_CHARS = previous_overlap


# ── 元数据提取 ────────────────────────────────────────────────────────────────

def extract_meta_from_filename(name: str) -> Tuple[Optional[str], Optional[int]]:
    stock_code, report_year = None, None
    m_code = re.search(r"(\d{6})", name)
    if m_code:
        stock_code = m_code.group(1)
    m_year = re.search(r"(20\d{2})", name)
    if m_year:
        report_year = int(m_year.group(1))
    return stock_code, report_year


def extract_meta_from_content(text: str) -> Tuple[Optional[str], Optional[int]]:
    """从文档正文提取股票代码和报告年份（比文件名更可靠）。"""
    stock_code = None
    report_year = None

    m_code = re.search(r'(?:公司代码|证券代码)\s*[:：]?\s*(\d{6})', text[:3000])
    if m_code:
        stock_code = m_code.group(1)

    m_year = re.search(r'(20\d{2})\s*年\s*(?:年度|半年度|一季度|三季度)', text[:3000])
    if m_year:
        report_year = int(m_year.group(1))

    return stock_code, report_year


# ── MD&A 章节提取 ─────────────────────────────────────────────────────────────

def extract_mda_sections(markdown_text: str) -> List[str]:
    """
    从财报 Markdown 中提取管理层讨论与分析（MD&A）等叙述性章节。
    排除纯财务数据表格，保留定性分析文字。
    """
    # 按 Markdown 标题分割
    section_pattern = re.compile(r'(#{1,4}\s+.+)', re.MULTILINE)
    parts = section_pattern.split(markdown_text)

    mda_sections: List[str] = []
    current_title = ""
    current_body = ""

    for part in parts:
        if re.match(r'#{1,4}\s+', part):
            # 保存上一个章节
            if current_body.strip() and _is_mda_section(current_title, current_body):
                mda_sections.append(f"{current_title}\n{current_body.strip()}")
            current_title = part.strip()
            current_body = ""
        else:
            current_body += part

    # 最后一个章节
    if current_body.strip() and _is_mda_section(current_title, current_body):
        mda_sections.append(f"{current_title}\n{current_body.strip()}")

    return mda_sections


def _is_mda_section(title: str, body: str) -> bool:
    """判断是否为叙述性 MD&A 章节（排除纯表格内容）。"""
    clean_title = str(title or "")
    clean_body = re.sub(r'<[^>]+>', '', str(body or ''))
    text_chars = len(clean_body)

    if _contains_any(clean_title, MDA_SECTION_KEYWORDS):
        return True

    if _looks_like_explanatory_table(clean_body) and text_chars >= 30:
        return True

    if _contains_any(clean_body, ANALYTIC_BODY_KEYWORDS):
        if text_chars > 100:
            return True
        if _contains_any(clean_title, REASONING_SECTION_KEYWORDS + OPERATION_SECTION_KEYWORDS) and text_chars > 30:
            return True
    return False


def _classify_financial_section(title_path: List[str], body_parts: List[str]) -> Optional[str]:
    title = "\n".join([item for item in title_path if item]).strip()
    body = "\n".join([item for item in body_parts if item]).strip()
    if not title and not body:
        return None
    if not _is_mda_section(title, body):
        return None
    if _contains_any(title, REASONING_SECTION_KEYWORDS) or _looks_like_explanatory_table(body):
        return "financial_report_reasoning"
    if _contains_any(title, OPERATION_SECTION_KEYWORDS):
        return "financial_report_operation_note"
    if _contains_any(body, REASONING_BODY_KEYWORDS):
        return "financial_report_reasoning"
    return "financial_report_mda"


def _is_research_noise_section(title_path: List[str], body_parts: List[str]) -> bool:
    title = _join_title_path(title_path)
    body = "\n".join([item for item in body_parts if item]).strip()
    body_head = body[:1200]
    if _contains_any(title, RESEARCH_NOISE_KEYWORDS):
        return True
    if _contains_any(body_head, RESEARCH_NOISE_KEYWORDS) and len(body_head) < 900:
        return True
    if len(body.strip()) < 40 and not _contains_any(title, RESEARCH_CONCLUSION_KEYWORDS + RESEARCH_POLICY_KEYWORDS):
        return True
    return False


def _classify_research_section(
    source_path: Path,
    title_path: List[str],
    body_parts: List[str],
) -> Optional[str]:
    if _is_research_noise_section(title_path, body_parts):
        return None

    title = _join_title_path(title_path)
    body = "\n".join([item for item in body_parts if item]).strip()
    text = f"{title}\n{body}"
    source_text = str(source_path)
    is_industry = "行业研报" in source_text

    if _contains_any(text, RESEARCH_RISK_KEYWORDS):
        return "research_report_risk"
    if _contains_any(text, RESEARCH_POLICY_KEYWORDS):
        return "research_report_policy"
    if _contains_any(title, RESEARCH_CONCLUSION_KEYWORDS):
        return "research_report_conclusion"
    if _contains_any(text, RESEARCH_OPERATION_KEYWORDS + RESEARCH_ANALYTIC_KEYWORDS):
        return "research_report_industry" if is_industry else "research_report_equity"
    return "research_report_industry" if is_industry else "research_report_equity"


def _build_typed_financial_documents_from_sections(
    sections: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    typed_documents: List[Dict[str, Any]] = []
    for section in sections:
        title_path = section.get("title_path", [])
        body_parts = section.get("body_parts", [])
        doc_type = _classify_financial_section(title_path, body_parts)
        if not doc_type:
            continue
        typed_documents.extend(
            _build_chunk_records(
                title_path,
                body_parts,
                base_meta={
                    "doc_type": doc_type,
                    "page_start": section.get("page_start") or 0,
                    "doc_category": "financial",
                },
            )
        )
    return typed_documents


def _build_typed_research_documents_from_sections(
    source_path: Path,
    sections: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    typed_documents: List[Dict[str, Any]] = []
    for section in sections:
        title_path = section.get("title_path", [])
        body_parts = section.get("body_parts", [])
        doc_type = _classify_research_section(source_path, title_path, body_parts)
        if not doc_type:
            continue
        typed_documents.extend(
            _build_chunk_records(
                title_path,
                body_parts,
                base_meta={
                    "doc_type": doc_type,
                    "page_start": section.get("page_start") or 0,
                    "doc_category": "research",
                },
            )
        )
    return typed_documents


def _extract_semantic_sections_from_ocr_json(json_path: Path) -> List[Dict[str, Any]]:
    payload = read_ocr_json(str(json_path))
    sections: List[Dict[str, Any]] = []
    current_section: Optional[Dict[str, Any]] = None
    top_level_title = ""
    pending_table_title = ""

    def flush_current():
        nonlocal current_section
        if not current_section:
            return
        body_parts = [part for part in current_section.get("body_parts", []) if part.strip()]
        if body_parts:
            sections.append({
                "title_path": current_section.get("title_path", []),
                "body_parts": body_parts,
                "page_start": current_section.get("page_start"),
            })
        current_section = None

    for page_index, page in enumerate(iter_layout_pages(payload), start=1):
        pruned = page.get("prunedResult", {}) or {}
        parsing_res_list = pruned.get("parsing_res_list", []) or []
        for block in parsing_res_list:
            label = str(block.get("block_label") or "")
            raw_content = block.get("block_content") or ""
            if not raw_content or label in SEMANTIC_IGNORED_LABELS:
                continue

            if label in SEMANTIC_TITLE_LABELS:
                title = _normalize_block_text(raw_content)
                if not title:
                    continue
                flush_current()
                if _is_top_level_title(title):
                    top_level_title = title
                    title_path = [title]
                else:
                    title_path = [top_level_title, title] if top_level_title and title != top_level_title else [title]
                current_section = {
                    "title_path": [item for item in title_path if item],
                    "body_parts": [],
                    "page_start": page_index,
                }
                pending_table_title = ""
                continue

            if label == "table_title":
                pending_table_title = _normalize_block_text(raw_content)
                continue

            if label == "table":
                content = _html_block_to_text(raw_content)
            elif label in SEMANTIC_TEXT_LABELS:
                content = _normalize_block_text(raw_content)
            else:
                continue

            if not content or content == "目录":
                continue

            if current_section is None:
                current_section = {
                    "title_path": [top_level_title] if top_level_title else [],
                    "body_parts": [],
                    "page_start": page_index,
                }

            if pending_table_title:
                current_section["body_parts"].append(f"表格标题：{pending_table_title}")
                pending_table_title = ""
            current_section["body_parts"].append(content)

    flush_current()
    return sections


def _build_semantic_chunks_from_ocr_json(json_path: Path) -> List[str]:
    documents: List[str] = []
    for section in _extract_semantic_sections_from_ocr_json(json_path):
        documents.extend(
            _build_semantic_chunks(
                section.get("title_path", []),
                section.get("body_parts", []),
            )
        )
    return documents


# ── 研报处理 ──────────────────────────────────────────────────────────────────

def _init_build_stats() -> Dict[str, int]:
    return {
        "research_pdf_found": 0,
        "research_pdf_ingested": 0,
        "research_pdf_failed": 0,
        "research_json_found": 0,
        "research_json_ingested": 0,
        "research_json_failed": 0,
        "research_pdf_skipped_has_json": 0,
        "research_tabular_found": 0,
        "research_tabular_ingested": 0,
        "research_tabular_failed": 0,
        "financial_pdf_found": 0,
        "financial_json_found": 0,
        "financial_mda_ingested": 0,
        "financial_mda_failed": 0,
    }


def _get_pdf_path_from_json_path(json_path: Path) -> Path:
    text = str(json_path)
    for suffix in OCR_JSON_SUFFIXES:
        if text.endswith(suffix):
            return Path(text[: -len(suffix)])
    return json_path


def _normalize_pdf_source_key(path_value: Path) -> str:
    return str(_get_pdf_path_from_json_path(path_value))


def _collect_pdf_and_json_sources(base_dir: Path) -> Dict[str, Dict[str, Optional[Path]]]:
    sources: Dict[str, Dict[str, Optional[Path]]] = {}

    def _ensure_entry(pdf_key: str) -> Dict[str, Optional[Path]]:
        return sources.setdefault(pdf_key, {"pdf_path": None, "json_path": None})

    for pdf_path in sorted(base_dir.rglob("*.pdf")):
        entry = _ensure_entry(_normalize_pdf_source_key(pdf_path))
        entry["pdf_path"] = pdf_path
        json_cache = find_json_cache_for_pdf(str(pdf_path))
        if json_cache:
            entry["json_path"] = Path(json_cache)

    for suffix in OCR_JSON_SUFFIXES:
        for json_path in sorted(base_dir.rglob(f"*{suffix}")):
            pdf_path = _get_pdf_path_from_json_path(json_path)
            entry = _ensure_entry(_normalize_pdf_source_key(pdf_path))
            if entry["json_path"] is None:
                entry["json_path"] = json_path
            if entry["pdf_path"] is None and pdf_path.exists():
                entry["pdf_path"] = pdf_path

    return sources


def _is_financial_summary_pdf(pdf_path: Path) -> bool:
    return "摘要" in pdf_path.name


def _looks_like_summary_text(text: str) -> bool:
    text = str(text or "")[:4000]
    summary_markers = [
        "年度报告摘要",
        "半年度报告摘要",
        "季度报告摘要",
        "第一季度报告摘要",
        "第三季度报告摘要",
        "报告摘要",
    ]
    return any(marker in text for marker in summary_markers)


def _is_financial_summary_source(pdf_path: Path, json_path: Optional[Path]) -> bool:
    if _is_financial_summary_pdf(pdf_path):
        return True
    try:
        if json_path and json_path.exists():
            text = _load_text_from_ocr_json(json_path)
            if _looks_like_summary_text(text):
                return True
    except Exception as e:
        logger.warning(f"摘要识别失败，回退为非摘要处理: {pdf_path.name} | {e}")
    return False


def _prepare_financial_sources(base_dir: Path) -> Dict[str, Dict[str, Optional[Path]]]:
    raw_sources = _collect_pdf_and_json_sources(base_dir)
    filtered: Dict[str, Dict[str, Optional[Path]]] = {}

    for key, item in raw_sources.items():
        pdf_path = item.get("pdf_path")
        json_path = item.get("json_path")
        if pdf_path is None and json_path is not None:
            pdf_path = _get_pdf_path_from_json_path(json_path)
            item["pdf_path"] = pdf_path
        if pdf_path is None:
            continue
        if _is_financial_summary_source(pdf_path, json_path):
            logger.info(f"跳过财报摘要文件: {pdf_path.name}")
            continue

        filtered[key] = item

    return filtered


def _load_text_from_ocr_json(json_path: Path) -> str:
    payload = read_ocr_json(str(json_path))
    full_text, _ = parse_ocr_json_to_content_and_chunks(payload)
    return full_text or ""


def _delete_existing_source_chunks(
    collection,
    source: Optional[str] = None,
    source_json: Optional[str] = None,
) -> None:
    ids_to_delete: List[str] = []
    seen_ids = set()
    for key, value in (("source", source), ("source_json", source_json)):
        if not value:
            continue
        try:
            payload = collection.get(where={key: value})
            for row_id in payload.get("ids") or []:
                if row_id not in seen_ids:
                    seen_ids.add(row_id)
                    ids_to_delete.append(row_id)
        except Exception as e:
            logger.warning(f"清理旧知识库分片失败 | {key}={value}: {e}")
    if ids_to_delete:
        collection.delete(ids=ids_to_delete)
        logger.info(f"已删除旧分片 {len(ids_to_delete)} 条 | source={source or '-'}")


def _build_source_metadata(
    source_pdf_path: Path,
    source_json_path: Optional[Path],
    stock_code: Optional[str],
    report_year: Optional[int],
    doc_category: str,
) -> Dict[str, Any]:
    source_text = str(source_pdf_path).replace("\\", "/")
    source_json_text = str(source_json_path).replace("\\", "/") if source_json_path else ""
    basis_text = f"{source_pdf_path.name}\n{source_json_path.name if source_json_path else ''}"
    return {
        "source": source_text,
        "source_json": source_json_text,
        "stock_code": stock_code or "",
        "report_year": report_year or 0,
        "paper_image": None,
        "doc_name": source_pdf_path.stem,
        "doc_category": doc_category,
        "report_period": _infer_report_period(basis_text),
        "report_kind": _infer_report_kind(basis_text),
    }


def _upsert_document_records(
    collection,
    records: List[Dict[str, Any]],
    source_pdf_path: Path,
    source_json_path: Optional[Path],
    stock_code: Optional[str],
    report_year: Optional[int],
    doc_category: str,
) -> int:
    if not records:
        return 0

    source_meta = _build_source_metadata(
        source_pdf_path=source_pdf_path,
        source_json_path=source_json_path,
        stock_code=stock_code,
        report_year=report_year,
        doc_category=doc_category,
    )
    _delete_existing_source_chunks(
        collection,
        source=source_meta["source"],
        source_json=source_meta["source_json"] or None,
    )

    documents: List[str] = []
    metadatas: List[Dict[str, Any]] = []
    ids: List[str] = []
    for idx, record in enumerate(records):
        text = str(record.get("text") or "").strip()
        if len(text) < 30:
            continue
        meta = dict(source_meta)
        meta.update({
            "type": str(record.get("doc_type") or ""),
            "chunk_index": int(record.get("chunk_index") or idx),
            "page_start": int(record.get("page_start") or 0),
            "section_title": str(record.get("section_title") or ""),
            "title_path": str(record.get("title_path") or ""),
        })
        record_id = _hash_identity(
            source_meta["source_json"] or source_meta["source"],
            meta["type"],
            meta["page_start"],
            meta["section_title"],
            meta["title_path"],
            idx,
            text[:160],
        )
        documents.append(text)
        metadatas.append(meta)
        ids.append(record_id)

    _batched_upsert(collection, documents, metadatas, ids)
    return len(documents)


def process_pdf_reports(
    collection,
    base_dir: Path,
    stats: Optional[Dict[str, int]] = None,
    skip_if_json_exists: bool = True,
):
    """处理 PDF 研报（个股研报 / 行业研报）。若已存在 OCR JSON，默认跳过。"""
    pdf_files = list(base_dir.rglob("*.pdf"))
    logger.info(f"发现 {len(pdf_files)} 个研报 PDF")
    if stats is not None:
        stats["research_pdf_found"] += len(pdf_files)

    for pdf_path in pdf_files:
        json_cache = find_json_cache_for_pdf(str(pdf_path))
        if not json_cache:
            logger.warning(f"研报 PDF 无 JSON 缓存，跳过: {pdf_path.name}")
            continue
        if skip_if_json_exists and json_cache:
            logger.info(f"跳过研报 PDF（已有 OCR JSON）: {pdf_path.name}")
            if stats is not None:
                stats["research_pdf_skipped_has_json"] += 1
            continue
        logger.info(f"处理研报 PDF: {pdf_path.name}")
        try:
            payload = read_ocr_json(json_cache)
            full_text, _ = parse_ocr_json_to_content_and_chunks(payload)
            if not full_text:
                logger.warning(f"无法提取文本: {pdf_path.name}")
                continue

            chunks = _build_semantic_chunks([pdf_path.stem], [full_text])
            if not chunks:
                continue

            stock_code, report_year = extract_meta_from_filename(pdf_path.name)
            content_code, content_year = extract_meta_from_content(full_text)
            if content_code:
                stock_code = content_code
            if content_year:
                report_year = content_year

            ids = [f"{pdf_path.stem}_chunk_{i}" for i in range(len(chunks))]
            metadatas = [
                {
                    "source": str(pdf_path),
                    "type": "pdf_report",
                    "page_chunk": i,
                    "stock_code": stock_code or "",
                    "report_year": report_year or 0,
                    "paper_image": None,
                    "doc_name": pdf_path.stem,
                }
                for i in range(len(chunks))
            ]

            _batched_upsert(collection, chunks, metadatas, ids)
            logger.info(f"研报入库: {pdf_path.name} → {len(chunks)} 个片段")
            if stats is not None:
                stats["research_pdf_ingested"] += 1

        except Exception as e:
            logger.error(f"处理研报 PDF 失败 {pdf_path.name}: {e}")
            if stats is not None:
                stats["research_pdf_failed"] += 1


def process_json_reports(
    collection,
    base_dir: Path,
    stats: Optional[Dict[str, int]] = None,
):
    """统一使用 PaddleOCR JSON 处理研报。"""
    json_files = [
        p for p in base_dir.rglob("*.json")
        if any(p.name.endswith(suffix) for suffix in OCR_JSON_SUFFIXES)
    ]
    logger.info(f"发现 {len(json_files)} 个研报 JSON")
    if stats is not None:
        stats["research_json_found"] += len(json_files)

    for json_path in json_files:
        source_pdf_path = _get_pdf_path_from_json_path(json_path)
        logger.info(f"处理研报 JSON: {json_path.name}")
        try:
            full_text = _load_text_from_ocr_json(json_path)
            if not full_text or len(full_text.strip()) < 50:
                logger.warning(f"JSON 文本过少，跳过: {json_path.name}")
                continue

            sections = _extract_semantic_sections_from_ocr_json(json_path)
            records = _build_typed_research_documents_from_sections(source_pdf_path, sections)
            if not records:
                records = _build_chunk_records(
                    [source_pdf_path.stem],
                    [full_text],
                    base_meta={
                        "doc_type": "research_report_industry" if "行业研报" in str(source_pdf_path) else "research_report_equity",
                        "page_start": 1,
                        "doc_category": "research",
                    },
                )
            if not records:
                continue

            stock_code, report_year = extract_meta_from_filename(json_path.name)
            content_code, content_year = extract_meta_from_content(full_text)
            if content_code:
                stock_code = content_code
            if content_year:
                report_year = content_year

            inserted = _upsert_document_records(
                collection,
                records=records,
                source_pdf_path=source_pdf_path,
                source_json_path=json_path,
                stock_code=stock_code,
                report_year=report_year,
                doc_category="research",
            )
            logger.info(f"JSON研报入库: {json_path.name} → {inserted} 个片段")
            if stats is not None and inserted:
                stats["research_json_ingested"] += 1
        except Exception as e:
            logger.error(f"处理研报 JSON 失败 {json_path.name}: {e}")
            if stats is not None:
                stats["research_json_failed"] += 1


def process_tabular_reports(collection, base_dir: Path, stats: Optional[Dict[str, int]] = None):
    """处理研报摘要表（xlsx），仅作辅助知识源。"""
    tabular_files = list(base_dir.rglob("*.xlsx"))
    logger.info(f"发现 {len(tabular_files)} 个研报表格文件")
    if stats is not None:
        stats["research_tabular_found"] += len(tabular_files)

    for table_path in tabular_files:
        if "字段说明" in table_path.name:
            continue
        logger.info(f"处理研报表格: {table_path.name}")
        try:
            df = pd.read_excel(table_path)
            source_type = "xlsx_summary"
            records: List[Dict[str, Any]] = []
            stock_code_hint: Optional[str] = None
            report_year_hint: Optional[int] = None

            for idx, row in df.iterrows():
                content_parts = []
                row_stock_code = ""
                row_report_year = 0
                for col in df.columns:
                    val = str(row[col]).strip()
                    if val and val.lower() != 'nan':
                        content_parts.append(f"{col}: {val}")
                        if "代码" in col:
                            row_stock_code = val
                        if "年份" in col or "日期" in col:
                            m = re.search(r'(20\d{2})', val)
                            if m:
                                row_report_year = int(m.group(1))

                content = "\n".join(content_parts)
                if content:
                    if row_stock_code and not stock_code_hint:
                        stock_code_hint = row_stock_code
                    if row_report_year and not report_year_hint:
                        report_year_hint = row_report_year
                    records.append({
                        "text": content,
                        "doc_type": source_type,
                        "chunk_index": idx,
                        "page_start": 0,
                        "section_title": table_path.stem,
                        "title_path": table_path.stem,
                        "doc_category": "tabular",
                    })

            if records:
                inserted = _upsert_document_records(
                    collection,
                    records=records,
                    source_pdf_path=table_path,
                    source_json_path=None,
                    stock_code=stock_code_hint,
                    report_year=report_year_hint,
                    doc_category="tabular",
                )
                logger.info(f"研报表格入库: {table_path.name} → {inserted} 条")
                if stats is not None and inserted:
                    stats["research_tabular_ingested"] += 1

        except Exception as e:
            logger.error(f"处理研报表格失败 {table_path.name}: {e}")
            if stats is not None:
                stats["research_tabular_failed"] += 1


# ── 财报 MD&A 处理 ─────────────────────────────────────────────────────────────

def process_financial_report_mda(
    collection,
    base_dir: Path,
    stats: Optional[Dict[str, int]] = None,
    source_override: Optional[Dict[str, Dict[str, Optional[Path]]]] = None,
):
    """统一使用 PaddleOCR JSON 提取财报叙述性章节入库。"""
    if source_override is None and not base_dir.exists():
        logger.warning(f"财报目录不存在，跳过 MD&A 提取: {base_dir}")
        return

    sources = source_override if source_override is not None else _prepare_financial_sources(base_dir)
    pdf_count = sum(1 for item in sources.values() if item.get("pdf_path"))
    json_count = sum(1 for item in sources.values() if item.get("json_path"))
    logger.info(f"发现 {pdf_count} 个财报 PDF，{json_count} 个财报 OCR JSON，提取 MD&A 章节")
    if stats is not None:
        stats["financial_pdf_found"] += pdf_count
        stats["financial_json_found"] += json_count

    for item in sources.values():
        pdf_path = item.get("pdf_path")
        json_path = item.get("json_path")
        if pdf_path is None and json_path is not None:
            pdf_path = _get_pdf_path_from_json_path(json_path)
        if pdf_path is None:
            continue

        if not json_path or not json_path.exists():
            logger.warning(f"财报缺少 PaddleOCR JSON，跳过: {pdf_path.name}")
            if stats is not None:
                stats["financial_mda_failed"] += 1
            continue

        logger.info(f"财报 MD&A 使用 OCR JSON: {json_path.name}")
        markdown_text = _load_text_from_ocr_json(json_path)
        if not markdown_text:
            if stats is not None:
                stats["financial_mda_failed"] += 1
            continue

        stock_code, report_year = extract_meta_from_filename(pdf_path.name)
        content_code, content_year = extract_meta_from_content(markdown_text[:5000])
        if content_code:
            stock_code = content_code
        if content_year:
            report_year = content_year

        typed_documents = _build_typed_financial_documents_from_sections(
            _extract_semantic_sections_from_ocr_json(json_path)
        )
        if not typed_documents:
            mda_sections = extract_mda_sections(markdown_text)
            for section in mda_sections:
                doc_type = _classify_financial_section([], [section]) or "financial_report_mda"
                typed_documents.extend(
                    _build_chunk_records(
                        [],
                        [section],
                        base_meta={
                            "doc_type": doc_type,
                            "page_start": 1,
                            "doc_category": "financial",
                        },
                    )
                )

        inserted = _upsert_document_records(
            collection,
            records=typed_documents,
            source_pdf_path=pdf_path,
            source_json_path=json_path,
            stock_code=stock_code,
            report_year=report_year,
            doc_category="financial",
        )
        if stats is not None and inserted:
            stats["financial_mda_ingested"] += 1

        if stats is not None and not inserted:
            stats["financial_mda_failed"] += 1


def _extract_narrative_from_large_pdf(pdf_path: str) -> Optional[str]:
    """
    大 PDF 专用：用 pdfplumber 提取非财务表格页的叙述文字。
    先解析目录确定财务页范围，然后提取其余页面。
    """
    try:
        from src.utils.pdf_splitter import find_financial_pages, extract_text_pages
        fin_start, fin_end = find_financial_pages(pdf_path)
        text = extract_text_pages(pdf_path, exclude_range=(fin_start, fin_end))
        if text and len(text.strip()) > 200:
            logger.info(f"[RAG] pdfplumber 提取叙述文字: {len(text)} chars (排除页 {fin_start+1}~{fin_end})")
            return text
        logger.warning(f"[RAG] pdfplumber 提取文字过少: {len(text)} chars")
        return None
    except Exception as e:
        logger.error(f"[RAG] pdfplumber extract error: {e}")
        return None


def _load_markdown_for_pdf(pdf_path: Path) -> Optional[str]:
    """尝试从缓存或 OCR 获取 Markdown 文本。"""
    json_path = find_json_cache_for_pdf(str(pdf_path))
    if json_path:
        try:
            content = _load_text_from_ocr_json(Path(json_path))
            if content.strip():
                return content
        except Exception as e:
            logger.error(f"读取 OCR JSON 失败: {e}")
    for md_path in [
        Path(str(pdf_path) + "_by_PaddleOCR-VL-1.5.md"),
        pdf_path.with_suffix(".md"),
    ]:
        if md_path.exists():
            try:
                with open(md_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                if content.strip():
                    return content
            except Exception as e:
                logger.error(f"读取 MD 文件失败: {e}")
    logger.warning(f"财报 PDF 无 JSON/MD 缓存，跳过: {pdf_path.name}")
    return ""


def _batched_upsert(
    collection,
    documents: List[str],
    metadatas: List[Dict[str, Any]],
    ids: List[str],
    batch_size: int = 10,
    max_retries: int = 3,
    retry_base_delay: float = 2.0,
) -> None:
    """按小批次写入，兼容当前 embedding 接口单次最多 10 条的限制，并带失败重试。"""
    total = len(documents)
    if not total:
        return
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        attempt = 0
        while True:
            try:
                collection.upsert(
                    documents=documents[start:end],
                    metadatas=metadatas[start:end],
                    ids=ids[start:end],
                )
                break
            except Exception as e:
                attempt += 1
                if attempt > max_retries:
                    raise
                delay = retry_base_delay * (2 ** (attempt - 1))
                logger.warning(
                    f"向量入库失败，准备重试 {attempt}/{max_retries} | "
                    f"batch={start}:{end} | delay={delay:.1f}s | error={e}"
                )
                time.sleep(delay)


def _upsert_chunks(
    collection,
    chunks: List[str],
    pdf_path: Path,
    stock_code: Optional[str],
    report_year: Optional[int],
    doc_type: str,
):
    """将文本切片批量 upsert 到 ChromaDB。"""
    if not chunks:
        return
    ids = [f"{pdf_path.stem}_{doc_type}_{i}" for i in range(len(chunks))]
    metadatas = [
        {
            "source": str(pdf_path),
            "type": doc_type,
            "stock_code": stock_code or "",
            "report_year": report_year or 0,
            "paper_image": None,
            "doc_name": pdf_path.stem,
        }
        for _ in range(len(chunks))
    ]
    _batched_upsert(collection, chunks, metadatas, ids)
    logger.info(f"MD&A 入库: {pdf_path.name} → {len(chunks)} 个片段")


def _upsert_typed_chunks(
    collection,
    typed_documents: List[Tuple[str, str]],
    pdf_path: Path,
    stock_code: Optional[str],
    report_year: Optional[int],
):
    if not typed_documents:
        return
    grouped: Dict[str, List[str]] = {}
    for doc_type, chunk in typed_documents:
        grouped.setdefault(doc_type, []).append(chunk)
    for doc_type, chunks in grouped.items():
        _upsert_chunks(collection, chunks, pdf_path, stock_code, report_year, doc_type)


def process_selected_financial_report_mda(
    collection,
    pdf_paths: List[Path],
    stats: Optional[Dict[str, int]] = None,
):
    """仅对指定 PDF 列表提取并入库财报 MD&A，用于抽样检查。"""
    selected: List[Path] = []
    for path_value in pdf_paths:
        path = Path(path_value)
        if path.exists():
            selected.append(path)
    if not selected:
        logger.warning("未找到可用的财报 PDF 样本，跳过抽样 MD&A 入库")
        return

    sources: Dict[str, Dict[str, Optional[Path]]] = {}
    for pdf_path in selected:
        key = _normalize_pdf_source_key(pdf_path)
        json_cache = find_json_cache_for_pdf(str(pdf_path))
        sources[key] = {
            "pdf_path": pdf_path,
            "json_path": Path(json_cache) if json_cache else None,
        }
    sources = {
        key: item
        for key, item in sources.items()
        if item.get("pdf_path") is not None
        and not _is_financial_summary_source(
            item["pdf_path"],
            item.get("json_path"),
        )
    }

    logger.info(f"开始抽样财报 MD&A 入库，共 {len(selected)} 份 PDF")
    process_financial_report_mda(
        collection,
        selected[0].parent,
        stats=stats,
        source_override=sources,
    )


# ── 主入口 ────────────────────────────────────────────────────────────────────

def build_knowledge_base(
    include_research_reports: bool = True,
    include_financial_mda: bool = True,
    sample_financial_paths: Optional[List[str]] = None,
    reset: bool = True,
):
    """
    构建 RAG 知识库。
    Args:
        include_research_reports: 是否处理研报（附件5）
        include_financial_mda: 是否提取财报 MD&A 章节（附件2）
        sample_financial_paths: 若传入，则仅对这些财报 PDF 做抽样 MD&A 入库
    """
    if reset:
        reset_chroma_db()
    else:
        logger.info("Skip ChromaDB reset; updating existing sources incrementally.")
    logger.info(f"开始构建知识库 | ChromaDB: {CHROMA_DB_PATH}")
    collection = init_chroma()
    stats = _init_build_stats()

    if include_research_reports:
        if REPORTS_DIR.exists():
            process_json_reports(collection, REPORTS_DIR, stats=stats)
            process_tabular_reports(collection, REPORTS_DIR, stats=stats)
        else:
            logger.error(f"研报目录不存在: {REPORTS_DIR}")

    if include_financial_mda:
        if sample_financial_paths:
            process_selected_financial_report_mda(
                collection,
                [Path(p) for p in sample_financial_paths],
                stats=stats,
            )
        else:
            process_financial_report_mda(collection, FINANCIAL_REPORTS_DIR, stats=stats)

    total = collection.count()
    logger.info(f"知识库构建统计: {json.dumps(stats, ensure_ascii=False)}")
    logger.info(f"✅ 知识库构建完成！共 {total} 条记录")
    return total


class RAGKnowledgeBase:
    """供批量构建流程调用的知识库构建接口。"""

    def __init__(self, reset_on_first_build: bool = False):
        self.collection = None
        self._initialized = False
        self.reset_on_first_build = reset_on_first_build

    def build_from_directory(self, directory: str, reset: Optional[bool] = None) -> int:
        base_dir = Path(directory)
        logger.info(f"开始构建目录知识库: {base_dir}")
        if not self._initialized:
            should_reset = self.reset_on_first_build if reset is None else bool(reset)
            if should_reset:
                reset_chroma_db()
            else:
                logger.info("Skip ChromaDB reset; updating directory incrementally.")
            self._initialized = True
        collection = init_chroma()
        self.collection = collection
        stats = _init_build_stats()

        if not base_dir.exists():
            logger.warning(f"知识库目录不存在: {base_dir}")
            return collection.count()

        dir_name = base_dir.name
        dir_text = str(base_dir)
        is_research_dir = "研报" in dir_name or "研报" in dir_text
        is_financial_dir = "财务报告" in dir_name or "财务报告" in dir_text or "reports-" in dir_text

        if is_research_dir:
            process_json_reports(collection, base_dir, stats=stats)
            process_tabular_reports(collection, base_dir, stats=stats)
        elif is_financial_dir:
            process_financial_report_mda(collection, base_dir, stats=stats)
        else:
            # 未知目录时做保守兼容：优先 JSON + 表格摘要，再尝试财报语义提取。
            process_json_reports(collection, base_dir, stats=stats)
            process_tabular_reports(collection, base_dir, stats=stats)
            process_financial_report_mda(collection, base_dir, stats=stats)

        total = collection.count()
        logger.info(f"目录知识库构建统计: {json.dumps(stats, ensure_ascii=False)}")
        logger.info(f"目录知识库构建完成: {base_dir} | total={total}")
        return total


if __name__ == "__main__":
    build_knowledge_base()
