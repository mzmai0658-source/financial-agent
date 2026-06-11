"""OCR JSON 解析器。

将 PaddleOCR 异步返回的 JSON 结果解析为：
1. 全文文本（用于元数据提取）
2. 带标题/单位上下文的表格 chunk（用于字段抽取）
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple


UNIT_PATTERNS: List[Tuple[str, float]] = [
    (r"单位[：:]\s*亿元", 10000.0),
    (r"单位[：:]\s*百万元", 100.0),
    (r"单位[：:]\s*万元", 1.0),
    (r"单位[：:]\s*千元", 0.1),
    (r"单位[：:]\s*元", 0.0001),
]

FINANCIAL_TITLE_KEYWORDS = [
    "主要会计数据和财务指标",
    "报告期分季度的主要会计数据",
    "合并资产负债表",
    "合并利润表",
    "合并现金流量表",
    "资产负债表",
    "利润表",
    "现金流量表",
]

TITLE_LIKE_BLOCK_LABELS = {
    "doc_title",
    "paragraph_title",
}

CONTEXT_TEXT_BLOCK_LABELS = {
    "vision_footnote",
    "text",
}

NOTE_LIKE_FINANCIAL_MARKERS = [
    "主营业务分析",
    "项目变动分析表",
    "项目变动情况",
    "现金流量表项目",
    "利润表项目",
    "资产负债表项目",
    "现金流量表补充资料",
    "利润表补充资料",
    "资产负债表补充资料",
    "补充资料",
]

FINANCIAL_ROW_KEYWORDS = [
    "资产总计", "总资产", "货币资金", "应收账款", "存货",
    "负债合计", "总负债", "股东权益", "未分配利润",
    "营业收入", "营业总收入", "营业成本", "销售费用", "管理费用", "财务费用",
    "营业利润", "利润总额", "净利润", "扣除非经常性损益",
    "经营活动产生的现金流量净额", "投资活动产生的现金流量净额", "筹资活动产生的现金流量净额",
    "销售商品、提供劳务收到的现金", "销售商品和提供劳务收到的现金",
    "取得借款收到的现金", "偿还债务支付的现金",
    "现金及现金等价物净增加额", "基本每股收益", "净资产收益率", "毛利率", "净利率",
]

TABLE_TYPE_MARKERS: Dict[str, List[str]] = {
    "primary_balance": ["合并资产负债表", "资产负债表"],
    "primary_income": ["合并利润表", "利润表"],
    "primary_cashflow": ["合并现金流量表", "现金流量表"],
    "core_metrics": ["主要会计数据和财务指标", "主要财务指标", "主要会计数据"],
    "quarter_summary": ["报告期分季度的主要会计数据", "分季度主要财务数据", "分季度主要会计数据"],
    "explanatory_table": ["主要会计数据和财务指标发生变动的情况及原因", "变动比率", "变动原因"],
    "subsidiary_table": ["主要控股参股公司分析", "重要子公司", "主要参股公司"],
    "mna_note": ["同一控制下企业合并", "非同一控制下企业合并", "合并成本", "被购买方于购买日可辨认资产和负债"],
}


def read_ocr_json(json_path: str) -> Any:
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)


PADDLE_OCR_JSON_SUFFIX = "_by_PaddleOCR-VL-1.5.json"

# Task 1 now uses a single OCR cache source to avoid cross-model merge noise.
OCR_JSON_SUFFIXES = [PADDLE_OCR_JSON_SUFFIX]


def get_json_cache_path_for_pdf(pdf_path: str, suffix: Optional[str] = None) -> str:
    return pdf_path + (suffix or OCR_JSON_SUFFIXES[0])


def find_json_cache_for_pdf(pdf_path: str) -> Optional[str]:
    for suffix in OCR_JSON_SUFFIXES:
        json_path = get_json_cache_path_for_pdf(pdf_path, suffix=suffix)
        if os.path.exists(json_path):
            return json_path
    return None


def parse_ocr_json_to_content_and_chunks(payload: Any) -> Tuple[str, List[Dict[str, Any]]]:
    """把 OCR JSON 转成全文文本和结构化表格 chunks。"""
    full_text_parts: List[str] = []
    chunks: List[Dict[str, Any]] = []
    doc_unit = 1.0
    carry_titles: List[str] = []
    carry_unit: Optional[float] = None
    carry_header_html = ""
    carry_header_fingerprint = ""
    carry_table_family: Optional[str] = None
    carry_table_type: str = ""
    carry_page_index = 0
    carry_chunk_index: Optional[int] = None

    for page_index, page in enumerate(iter_layout_pages(payload), start=1):
        page_markdown = page.get("markdown", {}).get("text", "") or ""
        if page_markdown.strip():
            full_text_parts.append(page_markdown.strip())

        pruned = page.get("prunedResult", {}) or {}
        parsing_res_list = pruned.get("parsing_res_list", []) or []

        page_titles: List[str] = []
        page_unit: Optional[float] = detect_unit(page_markdown)
        if page_unit is not None:
            doc_unit = page_unit

        for block in parsing_res_list:
            label = block.get("block_label", "")
            content = (block.get("block_content") or "").strip()
            if not content:
                continue

            if label in TITLE_LIKE_BLOCK_LABELS or label == "table_title":
                cleaned_title = _clean_title(content)
                should_record_title = (
                    cleaned_title
                    and not _is_running_report_header(cleaned_title)
                    and (
                        label in TITLE_LIKE_BLOCK_LABELS
                        or _looks_like_financial_title(cleaned_title)
                    )
                )
                if should_record_title:
                    page_titles.append(cleaned_title)
                if len(page_titles) > 6:
                    page_titles = page_titles[-6:]
                detected = detect_unit(content)
                if detected is not None:
                    page_unit = detected
                    doc_unit = detected
                continue

            if label in CONTEXT_TEXT_BLOCK_LABELS:
                cleaned_title = _clean_title(content)
                if cleaned_title and not _is_running_report_header(cleaned_title) and _looks_like_financial_title(cleaned_title):
                    page_titles.append(cleaned_title)
                    if len(page_titles) > 6:
                        page_titles = page_titles[-6:]
                detected = detect_unit(content)
                if detected is not None:
                    page_unit = detected
                    doc_unit = detected
                continue

            if label == "table" and "<table" in content.lower():
                candidate_titles = page_titles[-3:] or carry_titles[-3:]
                if not is_financial_table(content, candidate_titles):
                    continue
                context_parts = candidate_titles
                table_context = "\n".join(context_parts)
                table_family = classify_table_family(content, table_context)
                table_type = classify_chunk_table_type(content, table_context)
                statement_scope = classify_statement_scope(table_context, content)
                table_prototype = classify_table_prototype(content, table_context, table_type)
                header_html = extract_table_header_html(content)
                header_rows_present = count_header_rows_from_html(header_html)
                current_header_fingerprint = header_fingerprint(header_html)
                if should_inherit_table_header(
                    content,
                    page_titles,
                    page_index,
                    carry_page_index,
                    table_family,
                    carry_table_family,
                    carry_header_html,
                    current_header_fingerprint,
                    carry_header_fingerprint,
                    table_type=table_type,
                    carry_table_type=carry_table_type,
                ):
                    content = inject_table_header(content, carry_header_html)
                    header_html = extract_table_header_html(content)
                    header_rows_present = count_header_rows_from_html(header_html)
                    current_header_fingerprint = header_fingerprint(header_html)

                effective_unit = page_unit if page_unit is not None else (carry_unit if carry_unit is not None else doc_unit)
                if page_unit is not None:
                    context_parts.append(unit_multiplier_to_text(page_unit))
                elif carry_unit is not None:
                    context_parts.append(unit_multiplier_to_text(carry_unit))

                chunk_text = "\n".join(p for p in context_parts if p).strip()
                if chunk_text:
                    chunk_text += "\n"
                chunk_text += content
                chunk_is_combined = classify_table_type(content, chunk_text)
                should_merge = (
                    carry_chunk_index is not None
                    and carry_page_index
                    and page_index == carry_page_index + 1
                    and table_family is not None
                    and table_family == carry_table_family
                    and not page_titles
                )

                if should_merge:
                    existing_text = chunks[carry_chunk_index]["text"]
                    chunks[carry_chunk_index]["text"] = f"{existing_text}\n{content}"
                    page_indices = chunks[carry_chunk_index].setdefault("page_indices", [chunks[carry_chunk_index]["page_index"]])
                    if page_index not in page_indices:
                        page_indices.append(page_index)
                    chunks[carry_chunk_index]["header_rows_present"] = max(
                        int(chunks[carry_chunk_index].get("header_rows_present", 0) or 0),
                        header_rows_present,
                    )
                    if statement_scope == "combined":
                        chunks[carry_chunk_index]["statement_scope"] = "combined"
                    if not chunks[carry_chunk_index].get("table_prototype") or chunks[carry_chunk_index].get("table_prototype") == "generic_table":
                        chunks[carry_chunk_index]["table_prototype"] = table_prototype
                    if current_header_fingerprint:
                        chunks[carry_chunk_index]["header_fingerprint"] = current_header_fingerprint
                else:
                    chunks.append({
                        "text": chunk_text,
                        "unit_multiplier": effective_unit,
                        "is_combined": chunk_is_combined,
                        "statement_scope": statement_scope,
                        "table_type": table_type,
                        "table_family": table_family,
                        "statement_kind": table_family or table_type,
                        "table_prototype": table_prototype,
                        "has_table": True,
                        "page_index": page_index,
                        "page_indices": [page_index],
                        "header_rows_present": header_rows_present,
                        "header_fingerprint": current_header_fingerprint,
                        "title_context_confidence": estimate_title_context_confidence(context_parts, table_type, table_family),
                        "title_context": context_parts[-3:],
                    })
                    carry_chunk_index = len(chunks) - 1

                if context_parts:
                    carry_titles = [p for p in context_parts if p and not p.startswith("单位：")][-3:]
                if effective_unit is not None:
                    carry_unit = effective_unit
                if header_html:
                    carry_header_html = header_html
                    carry_header_fingerprint = current_header_fingerprint
                if table_family:
                    carry_table_family = table_family
                if table_type:
                    carry_table_type = table_type
                carry_page_index = page_index

    return "\n\n".join(full_text_parts), chunks


def iter_layout_pages(payload: Any):
    """兼容 JSON 数组 / JSONL 解析结果。"""
    if isinstance(payload, dict):
        payload = [payload]

    for item in payload or []:
        if isinstance(item, dict) and ("prunedResult" in item or "markdown" in item):
            yield item
            continue
        result = item.get("result", {}) if isinstance(item, dict) else {}
        layout_results = result.get("layoutParsingResults", []) or []
        for page in layout_results:
            yield page


def detect_unit(text: str) -> Optional[float]:
    for pattern, multiplier in UNIT_PATTERNS:
        if re.search(pattern, text):
            return multiplier
    return None


def unit_multiplier_to_text(multiplier: float) -> str:
    unit_name_map = {
        0.0001: "单位：元",
        0.1: "单位：千元",
        1.0: "单位：万元",
        100.0: "单位：百万元",
        10000.0: "单位：亿元",
    }
    return unit_name_map.get(multiplier, "单位：万元")


def classify_table_type(table_html: str, context: str) -> Optional[bool]:
    combined_keywords = ["合并资产负债表", "合并利润表", "合并现金流量表", "合并报表", "合并财务报表"]
    parent_keywords = ["母公司资产负债表", "母公司利润表", "母公司现金流量表", "母公司报表", "母公司财务报表"]

    for kw in combined_keywords:
        if kw in context:
            return True
    for kw in parent_keywords:
        if kw in context:
            return False

    table_head = table_html[:800]
    if "合并" in table_head:
        return True
    if "母公司" in table_head and "合并" not in table_head:
        return False
    return None


def classify_statement_scope(context: str, table_html: str) -> str:
    classified = classify_table_type(table_html, context)
    if classified is True:
        return "combined"
    if classified is False:
        return "parent"
    return "unknown"


def classify_table_family(table_html: str, context: str) -> Optional[str]:
    text = f"{context}\n{table_html[:1200]}"
    if any(keyword in text for keyword in ["现金流量表", "经营活动产生的现金流量净额", "现金及现金等价物净增加额"]):
        return "cashflow"
    if any(keyword in text for keyword in ["利润表", "营业收入", "净利润", "利润总额"]):
        return "income"
    if any(keyword in text for keyword in ["资产负债表", "资产总计", "总资产", "负债合计", "总负债"]):
        return "balance"
    if any(keyword in text for keyword in ["主要会计数据", "主要财务指标", "每股收益", "净资产收益率"]):
        return "metrics"
    return None


def classify_chunk_table_type(table_html: str, context: str) -> str:
    joined = f"{context}\n{table_html[:2000]}".strip()

    for marker in TABLE_TYPE_MARKERS["explanatory_table"]:
        if marker in joined:
            return "explanatory_table"
    for marker in TABLE_TYPE_MARKERS["subsidiary_table"]:
        if marker in joined:
            return "subsidiary_table"
    for marker in TABLE_TYPE_MARKERS["mna_note"]:
        if marker in joined:
            return "mna_note"
    for marker in TABLE_TYPE_MARKERS["quarter_summary"]:
        if marker in joined:
            return "quarter_summary"
    for marker in TABLE_TYPE_MARKERS["core_metrics"]:
        if marker in joined:
            return "core_metrics"

    if is_note_like_financial_context(context, table_html):
        return "supporting_financial"

    eps_markers = ["每股收益", "净资产收益率", "每股净资产", "每股经营现金流量"]
    if any(marker in joined for marker in eps_markers):
        if not any(
            marker in joined
            for marker in ["合并利润表", "母公司利润表", "利润表", "营业总收入", "营业成本", "利润总额"]
        ):
            return "core_metrics"

    table_family = classify_table_family(table_html, context)
    if table_family == "balance":
        return "primary_balance"
    if table_family == "income":
        return "primary_income"
    if table_family == "cashflow":
        return "primary_cashflow"

    if any(keyword in joined for keyword in FINANCIAL_ROW_KEYWORDS):
        return "supporting_financial"
    return "non_financial"


def is_note_like_financial_context(context: str, table_html: str) -> bool:
    joined = f"{context}\n{table_html[:1200]}".strip()
    if any(marker in joined for marker in NOTE_LIKE_FINANCIAL_MARKERS):
        if not any(primary_title in joined for primary_title in ["合并资产负债表", "合并利润表", "合并现金流量表"]):
            return True
    return False


def classify_table_prototype(table_html: str, context: str, table_type: str) -> str:
    joined = f"{context}\n{table_html[:2400]}".strip()
    normalized = re.sub(r"\s+", "", joined)
    year_hits = re.findall(r"20\d{2}", normalized)
    quarter_hits = sum(1 for keyword in ["第一季度", "第二季度", "第三季度", "第四季度"] if keyword in normalized)
    has_current_period = "本报告期" in normalized or "本期" in normalized
    has_ytd = "年初至报告期末" in normalized or "本年累计" in normalized
    has_point_in_time = "本报告期末" in normalized or "期末" in normalized or "上年度末" in normalized
    has_previous = "上年同期" in normalized or "上期发生额" in normalized or "上期" in normalized
    has_adjustment = "调整前" in normalized or "调整后" in normalized

    if table_type == "quarter_summary":
        return "quarter_summary"
    if table_type == "core_metrics":
        if has_adjustment:
            return "metrics_adjusted_compare"
        if len(set(year_hits)) >= 3:
            return "metrics_multi_year"
        if has_current_period and has_ytd:
            return "metrics_dual_period"
        if has_point_in_time and ("上年度末" in normalized or "期初" in normalized):
            return "metrics_point_in_time"
        if has_current_period and ("同比" in normalized or "增减" in normalized):
            return "metrics_single_period"
        return "metrics_generic"
    if table_type in {"primary_balance", "primary_income", "primary_cashflow"}:
        if quarter_hits >= 3:
            return "quarter_columns"
        if has_adjustment:
            return "statement_adjusted_compare"
        if has_current_period and has_ytd:
            return "statement_dual_period"
        if has_point_in_time and ("上年度末" in normalized or "年初" in normalized):
            return "statement_point_in_time"
        if has_previous:
            return "statement_current_previous"
        if len(set(year_hits)) >= 2:
            return "statement_year_columns"
        return "statement_standard"
    if len(set(year_hits)) >= 3:
        return "multi_year_compare"
    if quarter_hits >= 3:
        return "quarter_columns"
    return "generic_table"


def extract_table_header_html(table_html: str, max_rows: int = 2) -> str:
    rows = re.findall(r"(<tr\b[^>]*>.*?</tr>)", table_html, flags=re.IGNORECASE | re.DOTALL)
    if not rows:
        return ""
    header_rows: List[str] = []
    for row in rows[:max_rows]:
        row_text = re.sub(r"<[^>]+>", " ", row)
        row_text = re.sub(r"\s+", " ", row_text).strip()
        if looks_like_header_text(row_text):
            header_rows.append(row)
    return "".join(header_rows)


def count_header_rows_from_html(header_html: str) -> int:
    if not header_html:
        return 0
    return len(re.findall(r"<tr\b[^>]*>.*?</tr>", header_html, flags=re.IGNORECASE | re.DOTALL))


def header_fingerprint(header_html: str) -> str:
    if not header_html:
        return ""
    normalized = re.sub(r"<[^>]+>", " ", header_html)
    normalized = re.sub(r"\s+", "", normalized)
    return normalized[:160]


def looks_like_header_text(text: str) -> bool:
    header_markers = [
        "项目", "附注", "本期", "本年", "上期", "上年", "期末", "本报告期",
        "2025", "2024", "2023", "2022", "2021",
        "第一季度", "第二季度", "第三季度", "第四季度", "增减", "同比",
    ]
    return any(marker in text for marker in header_markers)


def should_inherit_table_header(
    table_html: str,
    page_titles: List[str],
    page_index: int,
    carry_page_index: int,
    table_family: Optional[str],
    carry_table_family: Optional[str],
    carry_header_html: str,
    current_header_fingerprint: str = "",
    carry_header_fingerprint: str = "",
    table_type: str = "",
    carry_table_type: str = "",
) -> bool:
    if not carry_header_html:
        return False
    if page_titles:
        return False
    if carry_page_index and page_index != carry_page_index + 1:
        return False
    both_core = table_type == "core_metrics" and carry_table_type == "core_metrics"
    if table_family and carry_table_family and table_family != carry_table_family and not both_core:
        return False
    current_head = extract_table_header_html(table_html)
    if current_head:
        return False
    if current_header_fingerprint and carry_header_fingerprint and current_header_fingerprint == carry_header_fingerprint:
        return False
    return not current_head


def estimate_title_context_confidence(context_parts: List[str], table_type: str, table_family: Optional[str]) -> float:
    score = 0.0
    joined = " ".join(context_parts[-3:])
    if context_parts:
        score += min(len(context_parts), 3) * 0.2
    if table_family and table_family in joined:
        score += 0.2
    title_hits = sum(
        1 for keyword in FINANCIAL_TITLE_KEYWORDS
        if keyword in joined
    )
    score += min(title_hits, 2) * 0.2
    if table_type in {"primary_balance", "primary_income", "primary_cashflow", "core_metrics", "quarter_summary"}:
        score += 0.2
    return round(min(score, 1.0), 3)


def inject_table_header(table_html: str, header_html: str) -> str:
    if not header_html:
        return table_html
    match = re.search(r"<table\b[^>]*>", table_html, flags=re.IGNORECASE)
    if not match:
        return table_html
    insert_pos = match.end()
    return table_html[:insert_pos] + header_html + table_html[insert_pos:]


def _clean_title(text: str) -> str:
    text = re.sub(r"^#+\s*", "", text).strip()
    return text


def _looks_like_financial_title(text: str) -> bool:
    normalized = re.sub(r"\s+", "", str(text or ""))
    if not normalized:
        return False
    if any(keyword in normalized for keyword in FINANCIAL_TITLE_KEYWORDS):
        return True
    if normalized in {"一、审计报告", "二、财务报表", "财务报告"}:
        return True
    if normalized.startswith("单位："):
        return True
    return False


def _is_running_report_header(text: str) -> bool:
    normalized = re.sub(r"\s+", "", str(text or ""))
    if not normalized:
        return False
    patterns = [
        r"^\d{4}年(第一季度|半年度|第三季度|年度)报告$",
        r"^\d{4}年第[一二三四1-4]季度报告$",
        r"^.+\d{4}年(第一季度|半年度|第三季度|年度)报告(全文)?$",
    ]
    return any(re.match(pattern, normalized) for pattern in patterns)


def is_financial_table(table_html: str, page_titles: List[str]) -> bool:
    title_text = " ".join(page_titles[-5:])
    if any(keyword in title_text for keyword in FINANCIAL_TITLE_KEYWORDS):
        return True

    table_head = table_html[:2500]
    row_hits = sum(1 for keyword in FINANCIAL_ROW_KEYWORDS if keyword in table_head)
    if row_hits >= 2:
        return True

    return False
