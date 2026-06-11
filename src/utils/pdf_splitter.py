# -*- coding: utf-8 -*-
"""
PDF 智能分页工具：
  - 解析目录定位财务报表页码
  - pdfplumber 直接提取表格 + 质量检测
  - 按页码范围拆分 PDF 为小文件（bytes）供 OCR 兜底
  - pdfplumber 提取纯文字（供 RAG 使用）
"""
import io
import re
import os
from typing import List, Tuple, Optional, Dict, Any
from loguru import logger


# ── 目录关键词 → 需要 OCR 的财务报表章节 ─────────────────────────────────────

FINANCIAL_SECTION_KEYWORDS = [
    '财务报告', '财务报表',
]

FINANCIAL_TABLE_KEYWORDS = [
    '合并资产负债表', '合并利润表', '合并现金流量表',
    '合并所有者权益变动表', '母公司资产负债表', '母公司利润表',
    '母公司现金流量表', '资产负债表', '利润表', '现金流量表',
]

# 目录行：关键词 + 省略号/空格 + 页码
TOC_LINE_RE = re.compile(
    r'(' + '|'.join(re.escape(kw) for kw in FINANCIAL_SECTION_KEYWORDS + FINANCIAL_TABLE_KEYWORDS) + r')'
    r'[\s.·…\-_]*(\d{1,3})',
    re.MULTILINE,
)


def find_financial_pages(pdf_path: str, scan_pages: int = 15) -> Tuple[int, int]:
    """
    解析 PDF 前 N 页的目录，定位财务报表页码范围。

    Returns:
        (start_page, end_page) — 0-based 页码范围（含首不含尾）。
        如果解析失败，返回整个文档范围的后半部分作为兜底。
    """
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)

        # 从前 scan_pages 页中搜索目录
        toc_entries: Dict[str, int] = {}
        for i in range(min(scan_pages, total_pages)):
            text = pdf.pages[i].extract_text() or ""
            for m in TOC_LINE_RE.finditer(text):
                keyword = m.group(1)
                page_num = int(m.group(2))
                if 1 <= page_num <= total_pages:
                    toc_entries[keyword] = page_num

    if not toc_entries:
        logger.warning(f"[Splitter] TOC parsing failed for {os.path.basename(pdf_path)}, using fallback")
        # 兜底：取后 60% 的页面（财务报告通常在后半部分）
        start = int(total_pages * 0.4)
        return start, total_pages

    logger.info(f"[Splitter] TOC entries found: {toc_entries}")

    # 找到最小的页码作为起始（"财务报告"章节开始）
    fin_section_page = None
    for kw in FINANCIAL_SECTION_KEYWORDS:
        if kw in toc_entries:
            fin_section_page = toc_entries[kw]
            break

    # 找到具体财务表格的页码
    table_pages = [v for k, v in toc_entries.items() if k in FINANCIAL_TABLE_KEYWORDS]

    if fin_section_page is not None:
        start = fin_section_page - 1  # 转为 0-based
    elif table_pages:
        start = min(table_pages) - 1
    else:
        start = int(total_pages * 0.4)

    # 结束页：财务表格最大页码 + 30 页缓冲（覆盖附注等），但不超过总页数
    if table_pages:
        end = min(max(table_pages) + 30, total_pages)
    else:
        end = min(start + 50, total_pages)

    # 安全边界
    start = max(0, start)
    end = min(total_pages, end)

    logger.info(f"[Splitter] Financial pages: {start+1}~{end} (of {total_pages})")
    return start, end


def split_pdf_to_bytes(pdf_path: str, start_page: int, end_page: int, batch_size: int = 15) -> List[bytes]:
    """
    将 PDF 的指定页码范围按 batch_size 拆分为多个小 PDF bytes。
    使用 PyPDF2 做页面拆分。

    Args:
        pdf_path: PDF 文件路径
        start_page: 起始页（0-based，含）
        end_page: 结束页（0-based，不含）
        batch_size: 每批页数

    Returns:
        List[bytes] — 每个元素是一个小 PDF 的二进制内容
    """
    from PyPDF2 import PdfReader, PdfWriter

    reader = PdfReader(pdf_path)
    total = len(reader.pages)
    start_page = max(0, start_page)
    end_page = min(total, end_page)

    batches: List[bytes] = []
    for batch_start in range(start_page, end_page, batch_size):
        batch_end = min(batch_start + batch_size, end_page)
        writer = PdfWriter()
        for page_idx in range(batch_start, batch_end):
            writer.add_page(reader.pages[page_idx])

        buf = io.BytesIO()
        writer.write(buf)
        pdf_bytes = buf.getvalue()
        batches.append(pdf_bytes)
        logger.debug(f"[Splitter] Batch: pages {batch_start+1}~{batch_end}, size={len(pdf_bytes)/1024:.0f}KB")

    logger.info(f"[Splitter] Split into {len(batches)} batches ({start_page+1}~{end_page}, batch_size={batch_size})")
    return batches


def extract_text_pages(
    pdf_path: str,
    exclude_range: Optional[Tuple[int, int]] = None,
) -> str:
    """
    用 pdfplumber 直接提取纯文字（跳过已由 OCR 处理的页面范围）。
    适合提取管理层讨论、业务概要等叙述性内容给 RAG。

    Args:
        pdf_path: PDF 文件路径
        exclude_range: 要排除的页码范围 (start, end)，0-based

    Returns:
        提取的纯文字内容
    """
    import pdfplumber

    parts: List[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            # 跳过已由 OCR 处理的财务表格页
            if exclude_range and exclude_range[0] <= i < exclude_range[1]:
                continue
            text = page.extract_text() or ""
            if text.strip():
                parts.append(text.strip())

    return "\n\n".join(parts)


# ── pdfplumber 表格提取 + 质量检测 ─────────────────────────────────────────────

# 关键字段：存在其中任意若干个即认为提取质量合格
QUALITY_KEYWORDS = [
    '资产总计', '负债合计', '负债和所有者权益总计', '所有者权益合计',
    '营业总收入', '营业收入', '净利润', '营业总成本', '营业利润',
    '经营活动产生的现金流量净额', '投资活动产生的现金流量净额',
    '筹资活动产生的现金流量净额', '期末现金及现金等价物余额',
    '货币资金', '应收账款', '存货', '短期借款', '应付账款',
    '基本每股收益', '每股收益',
]

# 页面级关键词（至少匹配 2 个才算有效财务表格页）
PAGE_MIN_KEYWORDS = 2
# 全部财务页至少匹配到的关键字段数
GLOBAL_MIN_KEYWORDS = 8


def extract_tables_with_pdfplumber(
    pdf_path: str,
    start_page: int,
    end_page: int,
) -> Tuple[str, List[int]]:
    """
    用 pdfplumber 直接提取财务页面的表格数据，转换为 Markdown 文本。
    同时进行质量检测，返回提取失败的页码列表。

    Args:
        pdf_path: PDF 文件路径
        start_page: 起始页（0-based，含）
        end_page: 结束页（0-based，不含）

    Returns:
        (markdown_text, failed_pages):
          - markdown_text: 提取成功的页面生成的 Markdown
          - failed_pages: 提取失败（质量不合格）的页码列表（0-based）
    """
    import pdfplumber

    all_md_parts: List[str] = []
    failed_pages: List[int] = []
    total_keywords_found: set = set()

    with pdfplumber.open(pdf_path) as pdf:
        for page_idx in range(start_page, min(end_page, len(pdf.pages))):
            page = pdf.pages[page_idx]
            page_md, kw_found = _extract_page_tables(page, page_idx)

            if page_md and len(kw_found) >= PAGE_MIN_KEYWORDS:
                all_md_parts.append(page_md)
                total_keywords_found.update(kw_found)
            elif page_md and kw_found:
                # 有一些内容但关键词不足，也保留但标记为可能需要复核
                all_md_parts.append(page_md)
                total_keywords_found.update(kw_found)
            else:
                # 无表格或完全无关键词 → 标记为失败
                failed_pages.append(page_idx)

    merged_md = "\n\n".join(all_md_parts)
    kw_count = len(total_keywords_found)

    logger.info(
        f"[Splitter] pdfplumber extracted {len(all_md_parts)} pages, "
        f"{len(failed_pages)} failed, "
        f"{kw_count} keywords matched: {sorted(total_keywords_found)[:10]}"
    )

    # 全局质量判断：如果总关键词匹配过少，说明 pdfplumber 整体不可靠
    if kw_count < GLOBAL_MIN_KEYWORDS:
        logger.warning(
            f"[Splitter] Global quality too low ({kw_count}/{GLOBAL_MIN_KEYWORDS}), "
            f"falling back to full OCR"
        )
        return "", list(range(start_page, end_page))

    return merged_md, failed_pages


def _extract_page_tables(page, page_idx: int) -> Tuple[str, set]:
    """
    从单页提取所有表格，转为 Markdown 文本。

    Returns:
        (markdown_text, keywords_found)
    """
    text = page.extract_text() or ""
    tables = page.extract_tables()
    kw_found: set = set()

    if not tables:
        # 无表格但有文字（可能是标题或附注）
        for kw in QUALITY_KEYWORDS:
            if kw in text:
                kw_found.add(kw)
        if kw_found:
            return text.strip(), kw_found
        return "", kw_found

    parts: List[str] = []

    # 提取页面标题（表格前的文字可能包含"合并资产负债表"等标题）
    page_header = ""
    for line in text.split("\n")[:5]:
        line = line.strip()
        if any(kw in line for kw in FINANCIAL_TABLE_KEYWORDS + FINANCIAL_SECTION_KEYWORDS):
            page_header = f"## {line}\n"
            break
        if '单位' in line and '元' in line:
            page_header += f"{line}\n"

    if page_header:
        parts.append(page_header)

    for table in tables:
        table_md = _table_to_markdown(table)
        if table_md:
            parts.append(table_md)
            for kw in QUALITY_KEYWORDS:
                if kw in table_md:
                    kw_found.add(kw)

    page_text = "\n".join(parts)
    return page_text, kw_found


def _table_to_markdown(table: List[List]) -> str:
    """
    将 pdfplumber 提取的表格（二维列表）转为 HTML table 格式
    （与 OCR 输出格式一致，方便下游 etl_worker 统一处理）。
    """
    if not table or not any(table):
        return ""

    rows_html: List[str] = []
    for row in table:
        cells = []
        for cell in row:
            val = str(cell).strip() if cell else ""
            if val == "None":
                val = ""
            cells.append(f"<td style='text-align: center; word-wrap: break-word;'>{val}</td>")
        if any(c for c in row if c and str(c).strip() and str(c).strip() != "None"):
            rows_html.append(f"<tr>{''.join(cells)}</tr>")

    if not rows_html:
        return ""

    return f"<table border=1 style='margin: auto; word-wrap: break-word;'>{''.join(rows_html)}</table>"


def split_failed_pages_to_bytes(
    pdf_path: str,
    failed_pages: List[int],
    batch_size: int = 15,
) -> List[bytes]:
    """
    将指定的离散页码列表拆分为 PDF bytes 批次（仅包含失败页）。
    """
    if not failed_pages:
        return []

    from PyPDF2 import PdfReader, PdfWriter

    reader = PdfReader(pdf_path)
    sorted_pages = sorted(failed_pages)

    batches: List[bytes] = []
    for i in range(0, len(sorted_pages), batch_size):
        batch_page_indices = sorted_pages[i:i + batch_size]
        writer = PdfWriter()
        for idx in batch_page_indices:
            if idx < len(reader.pages):
                writer.add_page(reader.pages[idx])

        buf = io.BytesIO()
        writer.write(buf)
        pdf_bytes = buf.getvalue()
        batches.append(pdf_bytes)
        logger.debug(
            f"[Splitter] OCR-fallback batch: {len(batch_page_indices)} pages "
            f"({batch_page_indices[0]+1}~{batch_page_indices[-1]+1}), "
            f"size={len(pdf_bytes)/1024:.0f}KB"
        )

    logger.info(f"[Splitter] {len(failed_pages)} failed pages → {len(batches)} OCR batches")
    return batches


def get_pdf_info(pdf_path: str) -> Dict[str, Any]:
    """获取 PDF 基本信息。"""
    import pdfplumber

    size_mb = os.path.getsize(pdf_path) / 1024 / 1024
    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
    return {
        "path": pdf_path,
        "size_mb": round(size_mb, 1),
        "total_pages": total_pages,
    }
