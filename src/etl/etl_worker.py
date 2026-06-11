import re
import os
import json
import html
import pandas as pd
from io import StringIO
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from loguru import logger
from sqlalchemy import create_engine
from bs4 import BeautifulSoup

from .base import BaseAgent
from src.agent.llm_client import LLMClient
from config.db_config import get_db_config
from src.init_db import IncomeSheet, BalanceSheet, CashFlowSheet, CorePerformanceIndicatorsSheet
from src.mysql_import import import_sheet
from src.utils.ocr_json_parser import (
    OCR_JSON_SUFFIXES,
    classify_statement_scope,
    classify_table_family,
    classify_table_prototype,
    count_header_rows_from_html,
    estimate_title_context_confidence,
    extract_table_header_html,
    find_json_cache_for_pdf,
    header_fingerprint,
    is_note_like_financial_context,
    parse_ocr_json_to_content_and_chunks,
    read_ocr_json,
)
from src.utils.company_registry import (
    get_code_to_name, get_name_to_code, resolve_stock_code, resolve_stock_abbr,
)

# ── 全局常量 ────────────────────────────────────────────────────────────────

# (正则, 相对于"万元"的乘数)
UNIT_PATTERNS: List[Tuple[str, float]] = [
    (r'单位[：:]\s*亿元', 10000.0),
    (r'单位[：:]\s*百万元', 100.0),
    (r'单位[：:]\s*万元', 1.0),
    (r'单位[：:]\s*千元', 0.1),
    (r'单位[：:]\s*元', 0.0001),
]

# 不需要单位换算的比率/百分比/每股字段
RATIO_FIELDS = {
    'eps', 'roe', 'gross_profit_margin', 'net_profit_margin',
    'asset_liability_ratio', 'net_profit_yoy_growth', 'net_profit_qoq_growth',
    'operating_revenue_yoy_growth', 'operating_revenue_qoq_growth',
    'asset_total_assets_yoy_growth', 'liability_total_liabilities_yoy_growth',
    'net_cash_flow_yoy_growth', 'operating_cf_ratio_of_net_cf',
    'investing_cf_ratio_of_net_cf', 'financing_cf_ratio_of_net_cf',
    'net_profit_excl_non_recurring_yoy', 'roe_weighted_excl_non_recurring',
    'net_asset_per_share', 'operating_cf_per_share', 'share_capital',
}

ZERO_AS_MISSING_FIELDS = {
    'gross_profit_margin', 'net_profit_margin', 'asset_liability_ratio',
    'operating_cf_ratio_of_net_cf', 'investing_cf_ratio_of_net_cf', 'financing_cf_ratio_of_net_cf',
    'net_profit_10k_yuan', 'net_asset_per_share', 'operating_cf_per_share',
    'net_profit_yoy_growth', 'net_profit_qoq_growth', 'operating_revenue_yoy_growth',
    'operating_revenue_qoq_growth', 'asset_total_assets_yoy_growth',
    'liability_total_liabilities_yoy_growth', 'net_cash_flow_yoy_growth',
    'net_profit_excl_non_recurring_yoy',
}

FIRST_WINS_FIELDS = {
    'asset_total_assets', 'liability_total_liabilities', 'equity_total_equity',
    'total_operating_revenue', 'net_profit', 'net_cash_flow',
    'eps', 'roe', 'gross_profit_margin', 'net_profit_margin',
    'operating_revenue_yoy_growth', 'net_profit_yoy_growth',
    'asset_total_assets_yoy_growth', 'liability_total_liabilities_yoy_growth',
    'net_cash_flow_yoy_growth', 'share_capital',
}

SOURCE_SCORED_FIELDS = {
    "asset_total_assets",
    "asset_cash_and_cash_equivalents",
    "asset_accounts_receivable",
    "asset_inventory",
    "liability_total_liabilities",
    "liability_accounts_payable",
    "liability_contract_liabilities",
    "liability_short_term_loans",
    "equity_total_equity",
    "equity_parent_attributable",
    "equity_minority_interest",
    "total_operating_revenue",
    "net_profit",
    "parent_net_profit",
    "net_profit_excl_non_recurring",
    "operating_cf_net_amount",
    "investing_cf_net_amount",
    "financing_cf_net_amount",
    "net_profit_10k_yuan",
    "net_cash_flow",
    "operating_profit",
    "total_profit",
    "share_capital",
    "eps",
    "roe",
    "roe_weighted_excl_non_recurring",
    "net_asset_per_share",
    "operating_cf_per_share",
}

SUSPICIOUS_SMALL_FIELDS = {
    "asset_total_assets": 1000.0,
    "liability_total_liabilities": 1000.0,
    "equity_total_equity": 1000.0,
    "total_operating_revenue": 1000.0,
    "net_cash_flow": 1000.0,
    "share_capital": 1000000.0,
    # 上市公司财报口径下，净利润小于 1 万元通常是误提到每股/附注/杂项值。
    "net_profit": 1.0,
    "parent_net_profit": 1.0,
    "net_profit_excl_non_recurring": 1.0,
    "net_profit_10k_yuan": 1.0,
}

NON_PRIMARY_FINANCIAL_TABLE_MARKERS = [
    '同一控制下企业合并',
    '非同一控制下企业合并',
    '合并成本',
    '被购买方于购买日可辨认资产和负债',
    '主要控股参股公司分析',
    '重要子公司',
    '企业合并中取得的权益比例',
    '主要会计数据和财务指标发生变动的情况及原因',
    '变动比率',
    '变动原因',
]

EXPLANATORY_TABLE_MARKERS = [
    '同一控制下企业合并',
    '非同一控制下企业合并',
    '合并成本',
    '被购买方于购买日可辨认资产和负债',
    '主要控股参股公司分析',
    '重要子公司',
    '企业合并中取得的权益比例',
    '主要会计数据和财务指标发生变动的情况及原因',
    '变动比率',
    '变动原因',
]

PRIMARY_TABLE_TYPES = {"primary_balance", "primary_income", "primary_cashflow"}
SECONDARY_TABLE_TYPES = {"core_metrics", "quarter_summary"}
REJECTED_TABLE_TYPES = {"explanatory_table", "subsidiary_table", "mna_note", "non_financial"}

BALANCE_SOURCE_FIELDS = {
    "asset_cash_and_cash_equivalents",
    "asset_accounts_receivable",
    "asset_inventory",
    "asset_trading_financial_assets",
    "asset_construction_in_progress",
    "asset_total_assets",
    "liability_accounts_payable",
    "liability_advance_from_customers",
    "liability_contract_liabilities",
    "liability_short_term_loans",
    "liability_total_liabilities",
    "equity_unappropriated_profit",
    "equity_total_equity",
    "equity_parent_attributable",
    "equity_minority_interest",
    "share_capital",
    "asset_total_assets_yoy_growth",
    "liability_total_liabilities_yoy_growth",
    "asset_liability_ratio",
}

INCOME_SOURCE_FIELDS = {
    "total_operating_revenue",
    "operating_expense_cost_of_sales",
    "operating_expense_selling_expenses",
    "operating_expense_administrative_expenses",
    "operating_expense_financial_expenses",
    "operating_expense_rnd_expenses",
    "operating_expense_taxes_and_surcharges",
    "total_operating_expenses",
    "operating_profit",
    "total_profit",
    "net_profit",
    "other_income",
    "asset_impairment_loss",
    "credit_impairment_loss",
    "net_profit_10k_yuan",
    "operating_revenue_yoy_growth",
    "net_profit_yoy_growth",
}

CASHFLOW_SOURCE_FIELDS = {
    "operating_cf_net_amount",
    "operating_cf_cash_from_sales",
    "investing_cf_net_amount",
    "investing_cf_cash_for_investments",
    "investing_cf_cash_from_investment_recovery",
    "financing_cf_net_amount",
    "financing_cf_cash_from_borrowing",
    "financing_cf_cash_for_debt_repayment",
    "net_cash_flow",
    "net_cash_flow_yoy_growth",
    "operating_cf_ratio_of_net_cf",
    "investing_cf_ratio_of_net_cf",
    "financing_cf_ratio_of_net_cf",
}

CORE_SOURCE_FIELDS = {
    "total_operating_revenue",
    "operating_revenue_yoy_growth",
    "asset_total_assets",
    "asset_total_assets_yoy_growth",
    "equity_parent_attributable",
    "parent_net_profit",
    "net_profit",
    "net_profit_10k_yuan",
    "net_profit_yoy_growth",
    "operating_cf_net_amount",
    "eps",
    "net_asset_per_share",
    "operating_cf_per_share",
    "roe",
    "roe_weighted_excl_non_recurring",
    "gross_profit_margin",
    "net_profit_margin",
    "net_profit_excl_non_recurring",
    "net_profit_excl_non_recurring_yoy",
}

QUARTER_SOURCE_FIELDS = {
    "net_profit_qoq_growth",
    "operating_revenue_qoq_growth",
}

PRIMARY_STATEMENT_PRIORITY_FIELDS = {
    "asset_total_assets",
    "asset_cash_and_cash_equivalents",
    "asset_accounts_receivable",
    "asset_inventory",
    "asset_trading_financial_assets",
    "asset_construction_in_progress",
    "liability_total_liabilities",
    "liability_accounts_payable",
    "liability_advance_from_customers",
    "liability_contract_liabilities",
    "liability_short_term_loans",
    "equity_total_equity",
    "equity_unappropriated_profit",
    "share_capital",
    "total_operating_revenue",
    "operating_expense_cost_of_sales",
    "operating_expense_selling_expenses",
    "operating_expense_administrative_expenses",
    "operating_expense_financial_expenses",
    "operating_expense_rnd_expenses",
    "operating_expense_taxes_and_surcharges",
    "total_operating_expenses",
    "operating_profit",
    "total_profit",
    "net_profit",
    "other_income",
    "asset_impairment_loss",
    "credit_impairment_loss",
    "operating_cf_net_amount",
    "operating_cf_cash_from_sales",
    "investing_cf_net_amount",
    "investing_cf_cash_for_investments",
    "investing_cf_cash_from_investment_recovery",
    "financing_cf_net_amount",
    "financing_cf_cash_from_borrowing",
    "financing_cf_cash_for_debt_repayment",
    "net_cash_flow",
}

CORE_METRIC_PRIORITY_FIELDS = {
    "eps",
    "roe",
    "roe_weighted_excl_non_recurring",
    "net_asset_per_share",
    "operating_cf_per_share",
    "operating_revenue_yoy_growth",
    "net_profit_yoy_growth",
    "net_profit_excl_non_recurring",
    "net_profit_excl_non_recurring_yoy",
    "asset_total_assets_yoy_growth",
    "liability_total_liabilities_yoy_growth",
}

INTERMEDIATE_DECIMALS = 8
PERCENT_PRECISION_FIELDS = {
    "roe",
    "roe_weighted_excl_non_recurring",
    "gross_profit_margin",
    "net_profit_margin",
    "asset_liability_ratio",
    "net_profit_yoy_growth",
    "net_profit_qoq_growth",
    "operating_revenue_yoy_growth",
    "operating_revenue_qoq_growth",
    "asset_total_assets_yoy_growth",
    "liability_total_liabilities_yoy_growth",
    "net_cash_flow_yoy_growth",
    "operating_cf_ratio_of_net_cf",
    "investing_cf_ratio_of_net_cf",
    "financing_cf_ratio_of_net_cf",
    "net_profit_excl_non_recurring_yoy",
}
PER_SHARE_PRECISION_FIELDS = {
    "eps",
    "net_asset_per_share",
    "operating_cf_per_share",
}
STORAGE_PRECISION_FIELDS = {
    **{field: 4 for field in PERCENT_PRECISION_FIELDS},
    **{field: 4 for field in PER_SHARE_PRECISION_FIELDS},
    "share_capital": 4,
}

# OCR 生成的 HTML 表格匹配
TABLE_RE = re.compile(r'(<table\b[^>]*>.*?</table>)', re.DOTALL | re.IGNORECASE)


LLM_MAX_WORKERS = 4  # LLM 并行提取线程数


class ETLWorker(BaseAgent):
    """
    ETL Worker Agent:
    1. PDF → Markdown (OCR 缓存)
    2. 提取元数据
    3. HTML 表格分块（保留单位上下文）
    4. LLM 并行语义提取
    5. 单位统一换算（代码层，不依赖 LLM）
    6. 合并报表 > 未知 > 母公司报表 优先级合并
    7. 入库
    """

    def __init__(self, llm_client: LLMClient):
        super().__init__("ETLWorker", llm_client)
        self._db_engine = None  # 懒加载，复用连接池
        self.period_patterns = [
            (r'(20\d{2})\s*年\s*年度报告', 'FY'),
            (r'(20\d{2})\s*年\s*(半年度报告|中期报告)', 'HY'),
            (r'(20\d{2})\s*年\s*(第一季度报告|一季度报告)', 'Q1'),
            (r'(20\d{2})\s*年\s*(第三季度报告|三季度报告)', 'Q3'),
        ]
        self.stock_patterns = [
            (r'(?:公司代码|证券代码)\s*[:：]?\s*(\d{6})', 1),
            (r'(?:股票代码|A股代码)\s*[:：]?\s*(\d{6})', 1),
        ]

    # ── 入口 ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_stock_code(value: Any) -> Optional[str]:
        digits = re.sub(r"\D", "", str(value or ""))
        if len(digits) == 6:
            return digits
        return None

    def _is_registry_stock_code(self, stock_code: Any) -> bool:
        normalized = self._normalize_stock_code(stock_code)
        if not normalized:
            return False
        return normalized in get_code_to_name()

    def run(self, file_path: str, save_to_db: bool = True) -> Dict[str, Any]:
        logger.info(f"[ETL] Processing: {file_path}")
        content, prebuilt_chunks = self._load_source(file_path)
        if not content:
            return {"status": "error", "message": "Empty or missing file", "file": file_path}

        meta = self._extract_metadata(content, file_path)
        if not meta.get("stock_code"):
            return {"status": "error", "message": "Cannot resolve allowed stock_code", "file": file_path}

        self.state.update_metadata(
            meta.get('stock_code'),
            meta.get('stock_abbr'),
            meta.get('report_year'),
            meta.get('report_period'),
        )

        if prebuilt_chunks is not None:
            fallback_chunks = self._chunk_markdown(content)
            seen_texts = {str(c.get("text", "")).strip() for c in prebuilt_chunks if str(c.get("text", "")).strip()}
            chunks = list(prebuilt_chunks)
            for chunk in fallback_chunks:
                chunk_text = str(chunk.get("text", "")).strip()
                if chunk_text and chunk_text not in seen_texts:
                    chunks.append(chunk)
                    seen_texts.add(chunk_text)
        else:
            chunks = self._chunk_markdown(content)
        normalized_chunks = [self._normalize_chunk_for_processing(chunk) for chunk in chunks]
        relevant_chunks = [(i, c) for i, c in enumerate(normalized_chunks) if self._is_relevant_chunk(c)]
        logger.info(f"[ETL] {len(chunks)} chunks, {len(relevant_chunks)} relevant from {os.path.basename(file_path)}")

        # 并行 LLM 提取
        combined_data: Dict[str, Any] = {}
        unknown_data: Dict[str, Any] = {}
        parent_data: Dict[str, Any] = {}

        chunk_results: List[Tuple[int, Dict, Any]] = []
        workers = min(LLM_MAX_WORKERS, max(1, len(relevant_chunks)))

        def _extract_one(idx: int, chunk: Dict) -> Tuple[int, Optional[Dict], Any]:
            logger.debug(
                f"[ETL] chunk {idx+1}/{len(chunks)} | type={chunk.get('table_type')} | "
                f"combined={chunk['is_combined']} | unit={chunk['unit_multiplier']}"
            )
            raw_data, extraction_mode = self._extract_chunk_data(chunk)
            if not raw_data:
                return idx, None, chunk['is_combined']
            converted = self._apply_unit_conversion(raw_data, chunk['unit_multiplier'], chunk.get('text', ''))
            converted = self._filter_fields_by_table_type(converted, str(chunk.get("table_type") or ""))
            converted = self._annotate_field_sources(converted, chunk, idx, extraction_mode)
            return idx, converted, chunk['is_combined']

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_extract_one, i, c): i for i, c in relevant_chunks}
            for future in as_completed(futures):
                try:
                    idx, converted, flag = future.result()
                    chunk_results.append((idx, converted, flag))
                except Exception as e:
                    logger.error(f"[ETL] chunk extraction error: {e}")

        for idx, converted, flag in sorted(chunk_results, key=lambda item: item[0]):
            if converted is None:
                continue
            if flag is True:
                combined_data = self._merge_last_wins(combined_data, converted)
            elif flag is False:
                parent_data = self._merge_last_wins(parent_data, converted)
            else:
                unknown_data = self._merge_last_wins(unknown_data, converted)

        # 跨来源合并时，优先级应为：合并报表 > 其他可信块 > 母公司报表。
        extracted_data: Dict[str, Any] = {}
        for source in [combined_data, unknown_data, parent_data]:
            extracted_data = self._merge_last_wins(extracted_data, source)

        extracted_data = self._retry_incomplete_required_fields(extracted_data, relevant_chunks)
        if extracted_data.get("_missing_required_fields"):
            logger.warning(
                f"[ETL] Incomplete required fields after retry: "
                f"{os.path.basename(file_path)} -> {extracted_data.get('_missing_required_fields')}"
            )
        if extracted_data.get("_retry_recovered_fields"):
            logger.info(
                f"[ETL] Recovered fields by retry: "
                f"{os.path.basename(file_path)} -> {extracted_data.get('_retry_recovered_fields')}"
            )

        final_data = self._post_process(extracted_data, meta, save_to_db=save_to_db)
        return final_data

    # ── 文件读取 ──────────────────────────────────────────────────────────────

    def _load_source(self, path: str) -> Tuple[str, Optional[List[Dict[str, Any]]]]:
        """优先读取单份 PaddleOCR JSON 缓存；否则回退到普通文本。"""
        json_path = self._resolve_ocr_json_path(path)
        if json_path:
            content, chunks = self._load_from_single_ocr_json(json_path)
            if content.strip():
                return content, chunks

        content = self._read_file(path)
        return content, None

    def _resolve_ocr_json_path(self, path: str) -> Optional[str]:
        normalized = str(path or "")
        lower_path = normalized.lower()

        if lower_path.endswith(".pdf"):
            return find_json_cache_for_pdf(normalized)

        if lower_path.endswith(".json"):
            matched_suffix = next((suffix for suffix in OCR_JSON_SUFFIXES if normalized.endswith(suffix)), None)
            if matched_suffix:
                return normalized if os.path.exists(normalized) else None
            return normalized if os.path.exists(normalized) else None

        return None

    def _load_from_single_ocr_json(self, json_path: str) -> Tuple[str, Optional[List[Dict[str, Any]]]]:
        try:
            payload = read_ocr_json(json_path)
            content, chunks = parse_ocr_json_to_content_and_chunks(payload)
        except Exception as e:
            logger.warning(f"[ETL] OCR JSON parse failed: {os.path.basename(json_path)} -> {e}")
            return "", None

        for chunk in chunks or []:
            chunk["_ocr_json_path"] = json_path

        logger.info(f"[ETL] Using OCR JSON cache: {os.path.basename(json_path)}")
        return content, chunks

    def _read_file(self, path: str) -> str:
        if path.lower().endswith('.pdf'):
            logger.warning(f"[ETL] PDF 无 JSON 缓存，跳过: {path}")
            return ""
        if not os.path.exists(path):
            return ""
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Error reading file: {e}")
            return ""

    # ── 元数据提取 ─────────────────────────────────────────────────────────────

    def _extract_metadata(self, content: str, file_path: str) -> Dict[str, Any]:
        meta: Dict[str, Any] = {
            "stock_code": None,
            "stock_abbr": None,
            "report_year": datetime.now().year,
            "report_period": "FY",
            "source_file": file_path,
            "source_is_summary": False,
        }
        head = content[:8000]
        fname = os.path.basename(file_path)
        summary_fname_tokens = ["报告摘要", "年度报告摘要", "半年度报告摘要", "摘要版"]
        is_summary = any(token in fname for token in summary_fname_tokens)
        if not is_summary:
            # 仅依据首页标题区域判定“摘要”，避免正文/备查文件目录中出现
            # “半年度报告摘要及全文”等描述时误把正式报告识别成摘要。
            title_lines: List[str] = []
            for raw_line in content[:1200].splitlines():
                clean_line = re.sub(r"^[#\s]+", "", str(raw_line or "")).strip()
                if not clean_line:
                    continue
                title_lines.append(clean_line)
                if len(title_lines) >= 8:
                    break
            title_blob = "\n".join(title_lines)
            if re.search(r"(年度|半年度|季度)报告摘要", title_blob):
                is_summary = True
            elif any(line == "报告摘要" for line in title_lines[:5]):
                is_summary = True
        meta["source_is_summary"] = is_summary

        # 先从正文提取
        for pattern, p_type in self.period_patterns:
            m = re.search(pattern, head)
            if m:
                meta["report_year"] = int(m.group(1))
                meta["report_period"] = p_type
                break

        # 从文件名兜底提取年份和报告期
        if meta["report_year"] == datetime.now().year:
            for pattern, p_type in self.period_patterns:
                m = re.search(pattern, fname)
                if m:
                    meta["report_year"] = int(m.group(1))
                    meta["report_period"] = p_type
                    break
            if meta["report_year"] == datetime.now().year:
                date_match = re.search(r'(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])', fname)
                if date_match:
                    year_from_date = int(date_match.group(1))
                    month = int(date_match.group(2))
                    if month <= 4:
                        meta["report_year"] = year_from_date - 1
                        meta["report_period"] = "FY"
                    elif month <= 8:
                        meta["report_year"] = year_from_date
                        meta["report_period"] = "HY"
                    elif month <= 10:
                        meta["report_year"] = year_from_date
                        meta["report_period"] = "Q3"
                    else:
                        meta["report_year"] = year_from_date
                else:
                    m = re.search(r'(20\d{2})', fname)
                    if m:
                        meta["report_year"] = int(m.group(1))

        for pattern, group_idx in self.stock_patterns:
            m = re.search(pattern, head)
            if m:
                meta["stock_code"] = m.group(group_idx)
                break

        # 从文件名兜底：6位代码
        if not meta["stock_code"]:
            m = re.search(r'(\d{6})', os.path.basename(file_path))
            if m:
                meta["stock_code"] = m.group(1)

        # 优先使用文件名中的公司名精确反查，避免正文前几页出现其他公司名称时误判。
        if not meta["stock_code"]:
            company_prefix = ""
            for sep in ("：", ":"):
                if sep in fname:
                    company_prefix = fname.split(sep, 1)[0]
                    break
            company_prefix = re.sub(r"\s+", "", company_prefix).strip()
            if company_prefix:
                exact_code = get_name_to_code().get(company_prefix)
                if exact_code:
                    meta["stock_code"] = exact_code

        # 从文件名/内容兜底：公司名称反查代码
        if not meta["stock_code"]:
            search_text = os.path.basename(file_path) + head[:3000]
            resolved = resolve_stock_code(search_text)
            if resolved:
                meta["stock_code"] = resolved
                logger.info(f"[ETL] Resolved stock_code by company name: {resolved}")

        # LLM 兜底
        if not meta["stock_code"]:
            llm_meta = self._extract_metadata_with_llm(content[:6000])
            meta.update({k: v for k, v in llm_meta.items() if v})

        # 从动态注册表获取简称
        if meta.get("stock_code"):
            meta["stock_code"] = self._normalize_stock_code(meta["stock_code"])
        if meta.get("stock_code") and not self._is_registry_stock_code(meta["stock_code"]):
            logger.warning(
                f"[ETL] Reject non-registry stock_code: {meta.get('stock_code')} | file={os.path.basename(file_path)}"
            )
            meta["stock_code"] = None
        if meta["stock_code"]:
            meta["stock_abbr"] = resolve_stock_abbr(meta["stock_code"])
        else:
            meta["stock_abbr"] = None

        return meta

    def _extract_metadata_with_llm(self, text: str) -> Dict[str, Any]:
        if self.llm is None or not hasattr(self.llm, "chat"):
            return {}
        prompt = (
            "Extract metadata from this Chinese annual/interim report snippet.\n"
            "Return JSON only with keys:\n"
            "- stock_code: 6-digit string or null\n"
            "- report_year: integer year or null\n"
            "- report_period: one of Q1, HY, Q3, FY or null\n"
            f"Text:\n{text}"
        )
        response = self.llm.chat([{"role": "user", "content": prompt}], temperature=0.1)
        if not response:
            return {}
        try:
            clean = re.sub(r'```json|```', '', response).strip()
            data = json.loads(clean)
            return {
                "stock_code": data.get("stock_code"),
                "report_year": data.get("report_year"),
                "report_period": data.get("report_period"),
            }
        except Exception:
            return {}

    # ── 分块（核心重构）────────────────────────────────────────────────────────

    def _chunk_markdown(self, content: str) -> List[Dict[str, Any]]:
        """
        针对 PaddleOCR 输出的 HTML 表格格式进行分块。
        每个 chunk 包含：
          - text: 上下文文本 + HTML 表格
          - unit_multiplier: 换算到"万元"的乘数
          - is_combined: True=合并报表, False=母公司报表, None=未知
        """
        chunks: List[Dict[str, Any]] = []
        prev_end = 0
        doc_unit = 1.0  # 文档级别的默认单位（随扫描推进更新）

        for m in TABLE_RE.finditer(content):
            table_html = m.group(1)
            table_start = m.start()

            # 取表格前最多 600 字符作为上下文（包含单位声明、标题）
            ctx_start = max(prev_end, table_start - 600)
            context_text = content[ctx_start:table_start]

            # 更新文档级别单位
            detected = self._detect_unit_in_text(context_text)
            if detected is not None:
                doc_unit = detected

            is_combined = self._classify_table_type(table_html, context_text)
            table_type = self._infer_chunk_table_type(table_html, context_text)
            table_family = classify_table_family(table_html, context_text)
            statement_scope = classify_statement_scope(context_text, table_html)
            table_prototype = classify_table_prototype(table_html, context_text, table_type)
            header_html = extract_table_header_html(table_html)
            header_rows_present = count_header_rows_from_html(header_html)
            header_fp = header_fingerprint(header_html)
            context_lines = [line.strip() for line in context_text.splitlines() if line.strip()]

            chunks.append({
                'text': context_text.strip() + '\n' + table_html,
                'unit_multiplier': doc_unit,
                'is_combined': is_combined,
                'statement_scope': statement_scope,
                'table_type': table_type,
                'table_family': table_family,
                'statement_kind': table_family or table_type,
                'table_prototype': table_prototype,
                'has_table': True,
                'header_rows_present': header_rows_present,
                'header_fingerprint': header_fp,
                'title_context_confidence': estimate_title_context_confidence(context_lines[-3:], table_type, table_family),
                'title_context': context_lines[-3:],
            })
            prev_end = m.end()

        # 如果没有找到任何 HTML 表格，回退到按标题分块（兼容普通 Markdown）
        if not chunks:
            for part in re.split(r'\n#{1,6}\s+', content):
                part = part.strip()
                if len(part) > 80:
                    chunks.append({
                        'text': part,
                        'unit_multiplier': self._detect_unit_in_text(part) or 1.0,
                        'is_combined': None,
                        'statement_scope': 'unknown',
                        'table_type': 'plain_text',
                        'table_family': None,
                        'statement_kind': 'plain_text',
                        'table_prototype': 'plain_text',
                        'has_table': False,
                    })

        return chunks

    def _detect_unit_in_text(self, text: str) -> Optional[float]:
        for pattern, multiplier in UNIT_PATTERNS:
            if re.search(pattern, text):
                return multiplier
        return None

    def _classify_table_type(self, table_html: str, context: str) -> Optional[bool]:
        """
        返回 True=合并报表, False=仅母公司报表, None=不确定
        合并报表优先：只要发现"合并"字样（且不是母公司专属章节），返回 True
        """
        combined_keywords = ['合并资产负债表', '合并利润表', '合并现金流量表', '合并报表', '合并财务报表']
        parent_keywords = ['母公司资产负债表', '母公司利润表', '母公司现金流量表', '母公司报表', '母公司财务报表']

        for kw in combined_keywords:
            if kw in context:
                return True
        for kw in parent_keywords:
            if kw in context:
                return False

        # 查表格内容头部（前 800 字符包含表头行）
        table_head = table_html[:800]
        if '合并' in table_head:
            return True
        # 表头出现"母公司"列但无"合并"列 → 母公司专用表
        if '母公司' in table_head and '合并' not in table_head:
            return False

        return None

    def _infer_chunk_table_type(self, table_html: str, context: str) -> str:
        joined = f"{context}\n{table_html[:2000]}".strip()
        explanatory_markers = [
            "主要会计数据和财务指标发生变动的情况及原因",
            "变动比率",
            "变动原因",
        ]
        subsidiary_markers = ["主要控股参股公司分析", "重要子公司", "主要参股公司"]
        mna_markers = ["同一控制下企业合并", "非同一控制下企业合并", "合并成本", "被购买方于购买日可辨认资产和负债"]
        quarter_markers = ["报告期分季度的主要会计数据", "分季度主要财务数据", "分季度主要会计数据"]
        core_markers = ["主要会计数据和财务指标", "主要财务指标", "主要会计数据"]

        if any(marker in joined for marker in explanatory_markers):
            return "explanatory_table"
        if any(marker in joined for marker in subsidiary_markers):
            return "subsidiary_table"
        if any(marker in joined for marker in mna_markers):
            return "mna_note"
        if any(marker in joined for marker in quarter_markers):
            return "quarter_summary"
        if any(marker in joined for marker in core_markers):
            return "core_metrics"
        if is_note_like_financial_context(context, table_html):
            return "supporting_financial"
        eps_markers = ["每股收益", "净资产收益率", "每股净资产", "每股经营现金流量"]
        if any(marker in joined for marker in eps_markers):
            if not any(marker in joined for marker in ["合并利润表", "母公司利润表", "利润表", "营业总收入", "营业成本", "利润总额"]):
                return "core_metrics"
        if any(marker in joined for marker in ["资产负债表", "资产总计", "负债合计"]):
            return "primary_balance"
        if any(marker in joined for marker in ["利润表", "营业收入", "利润总额", "净利润"]):
            return "primary_income"
        if any(marker in joined for marker in ["现金流量表", "现金及现金等价物净增加额", "经营活动产生的现金流量净额"]):
            return "primary_cashflow"
        if any(keyword in joined for keyword in ["营业收入", "净利润", "总资产", "负债合计", "每股收益", "净资产收益率"]):
            return "supporting_financial"
        return "non_financial"

    def _has_combined_statement_signal(self, chunk: Dict[str, Any]) -> bool:
        if chunk.get("is_combined") is True:
            return True
        if str(chunk.get("statement_scope") or "") == "combined":
            return True
        table_type = str(chunk.get("table_type") or "")
        title_context = " ".join(chunk.get("title_context") or [])
        text = str(chunk.get("text", ""))
        if "合并" in title_context:
            return True
        if any(marker in text[:800] for marker in ["合并资产负债表", "合并利润表", "合并现金流量表", "合并年初到报告期末现金流量表"]):
            return True
        joined_context = f"{title_context}\n{text[:800]}"
        if "母公司" in joined_context:
            return False
        if table_type in PRIMARY_TABLE_TYPES and any(marker in joined_context for marker in ["财务报表", "财务报告"]):
            return True
        return False

    def _repair_mislabeled_cashflow_chunk(self, chunk: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if chunk.get("is_combined") is not False:
            return None
        text = str(chunk.get("text", ""))
        if "<table" not in text.lower():
            return None

        title_context = " ".join(chunk.get("title_context") or [])
        if "母公司现金流量表" in title_context:
            return None

        cashflow_markers = [
            "经营活动产生的现金流量净额",
            "投资活动产生的现金流量净额",
            "筹资活动产生的现金流量净额",
            "现金及现金等价物净增加额",
            "投资支付的现金",
            "收回投资收到的现金",
            "取得借款收到的现金",
            "偿还债务支付的现金",
        ]
        cashflow_hits = sum(1 for marker in cashflow_markers if marker in text)
        if cashflow_hits < 2:
            return None

        has_parent_title_mismatch = any(
            marker in title_context
            for marker in ["母公司资产负债表", "母公司利润表", "母公司"]
        )
        if not has_parent_title_mismatch:
            return None

        repaired = dict(chunk)
        repaired["table_type"] = "primary_cashflow"
        repaired["table_family"] = "cashflow"
        repaired["is_combined"] = True
        repaired["statement_scope"] = "combined"
        repaired["title_context"] = [
            title for title in (chunk.get("title_context") or [])
            if "母公司" not in str(title or "")
        ] or ["合并现金流量表"]
        repaired["_repaired_combined_cashflow"] = True
        return repaired

    def _repair_mislabeled_income_chunk(self, chunk: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if chunk.get("is_combined") is not False:
            return None
        text = str(chunk.get("text", ""))
        if "<table" not in text.lower():
            return None

        title_context = " ".join(chunk.get("title_context") or [])
        # 真正的母公司利润表先不动，避免误把 parent 表强行抬成 combined。
        if "母公司利润表" in title_context:
            return None

        income_markers = [
            "营业总收入",
            "营业收入",
            "营业总成本",
            "营业利润",
            "利润总额",
            "净利润",
        ]
        income_hits = sum(1 for marker in income_markers if marker in text)
        if income_hits < 3:
            return None

        combined_specific_markers = [
            "归属于上市公司股东的净利润",
            "归属于母公司股东的净利润",
            "归属于母公司所有者的净利润",
            "少数股东损益",
        ]
        if not any(marker in text for marker in combined_specific_markers):
            return None

        has_parent_title_mismatch = any(
            marker in title_context
            for marker in ["母公司资产负债表", "母公司现金流量表", "母公司"]
        )
        if not has_parent_title_mismatch:
            return None

        repaired = dict(chunk)
        repaired["table_type"] = "primary_income"
        repaired["table_family"] = "income"
        repaired["is_combined"] = True
        repaired["statement_scope"] = "combined"
        repaired["title_context"] = [
            title for title in (chunk.get("title_context") or [])
            if "母公司" not in str(title or "")
        ] or ["合并利润表"]
        repaired["_repaired_combined_income"] = True
        return repaired

    def _normalize_chunk_for_processing(self, chunk: Dict[str, Any]) -> Dict[str, Any]:
        repaired = self._repair_mislabeled_cashflow_chunk(chunk)
        if repaired is not None:
            return repaired
        repaired = self._repair_mislabeled_income_chunk(chunk)
        return repaired or chunk

    def _should_accept_chunk(self, chunk: Dict[str, Any]) -> bool:
        if chunk.get('is_combined') is False:
            return False
        table_type = str(chunk.get("table_type") or "")
        if table_type in PRIMARY_TABLE_TYPES and not self._has_combined_statement_signal(chunk):
            return False
        if table_type in REJECTED_TABLE_TYPES:
            return False
        if table_type in PRIMARY_TABLE_TYPES or table_type in SECONDARY_TABLE_TYPES:
            return True
        if table_type in {"financial_other", "supporting_financial"}:
            return True
        text = str(chunk.get("text", ""))
        return any(
            keyword in text
            for keyword in [
                '营业收入', '净利润', '总资产', '资产总计', '负债', '现金流量',
                '每股收益', '净资产收益率', '利润', '<table', '毛利率', '营业成本',
                '销售商品、提供劳务收到的现金', '销售商品和提供劳务收到的现金',
                '取得借款收到的现金', '现金及现金等价物净增加额',
            ]
        )

    # ── 相关性判断 ─────────────────────────────────────────────────────────────

    def _is_relevant_chunk(self, chunk: Dict[str, Any]) -> bool:
        return self._should_accept_chunk(chunk)

    def _is_chunk_usable_for_required_fields(self, chunk: Dict[str, Any]) -> bool:
        if chunk.get("is_combined") is False:
            return False
        table_type = str(chunk.get("table_type") or "")
        return table_type not in REJECTED_TABLE_TYPES

    def _expected_required_field_groups(self, relevant_chunks: List[Tuple[int, Dict[str, Any]]]) -> List[Dict[str, Any]]:
        groups: List[Dict[str, Any]] = []

        def has_group(match_fn) -> bool:
            return any(match_fn(chunk) for _, chunk in relevant_chunks if self._is_chunk_usable_for_required_fields(chunk))

        if has_group(lambda chunk: str(chunk.get("table_type") or "") == "primary_balance" or str(chunk.get("table_family") or "") == "balance"):
            groups.append({
                "name": "balance",
                "family": "balance",
                "required_fields": ["asset_total_assets", "liability_total_liabilities"],
                "critical_fields": ["asset_total_assets", "liability_total_liabilities"],
            })
        if has_group(lambda chunk: str(chunk.get("table_type") or "") == "primary_income" or str(chunk.get("table_family") or "") == "income"):
            groups.append({
                "name": "income",
                "family": "income",
                "required_fields": ["total_operating_revenue", "net_profit"],
                "critical_fields": ["total_operating_revenue", "net_profit"],
            })
        if has_group(lambda chunk: str(chunk.get("table_type") or "") == "primary_cashflow" or str(chunk.get("table_family") or "") == "cashflow"):
            groups.append({
                "name": "cashflow",
                "family": "cashflow",
                "required_fields": [
                    "operating_cf_net_amount",
                    "investing_cf_net_amount",
                    "financing_cf_net_amount",
                    "net_cash_flow",
                ],
                "critical_fields": ["net_cash_flow"],
            })
        return groups

    def _extract_for_family_retry(self, family: str, text: str) -> Dict[str, Any]:
        if family == "balance":
            return self._extract_balance_table(text)
        if family == "income":
            return self._extract_income_table(text)
        if family == "cashflow":
            return self._extract_cashflow_table(text)
        return {}

    def _chunk_matches_family(self, chunk: Dict[str, Any], family: str) -> bool:
        table_type = str(chunk.get("table_type") or "")
        table_family = str(chunk.get("table_family") or "")
        text = str(chunk.get("text", ""))
        if table_family == family:
            return True
        if family == "balance":
            return table_type == "primary_balance" or "资产负债表" in text or "资产总计" in text or "负债合计" in text
        if family == "income":
            return table_type == "primary_income" or "利润表" in text or "营业收入" in text or "净利润" in text
        if family == "cashflow":
            return table_type == "primary_cashflow" or "现金流量表" in text or "现金及现金等价物净增加额" in text or "经营活动产生的现金流量净额" in text
        return False

    def _retry_incomplete_required_fields(
        self,
        data: Dict[str, Any],
        relevant_chunks: List[Tuple[int, Dict[str, Any]]],
    ) -> Dict[str, Any]:
        retried = dict(data)
        completeness_flags = list(retried.get("_completeness_flags") or [])
        missing_required_fields = set(retried.get("_missing_required_fields") or [])
        retried_fields = set(retried.get("_retry_attempted_fields") or [])
        recovered_fields = set(retried.get("_retry_recovered_fields") or [])

        for group in self._expected_required_field_groups(relevant_chunks):
            required_fields = list(group.get("required_fields") or [])
            critical_fields = set(group.get("critical_fields") or [])
            present_fields = [field for field in required_fields if self._to_number(retried.get(field)) is not None]
            missing_fields = [field for field in required_fields if field not in present_fields]
            if not missing_fields:
                continue

            missing_ratio = len(missing_fields) / max(len(required_fields), 1)
            if missing_ratio >= 0.5:
                completeness_flags.append(f"{group['name']}_incomplete({len(missing_fields)}/{len(required_fields)})")
            if any(field in critical_fields for field in missing_fields):
                completeness_flags.append(f"{group['name']}_critical_missing")

            retry_candidates = [
                (idx, chunk) for idx, chunk in relevant_chunks
                if self._is_chunk_usable_for_required_fields(chunk) and self._chunk_matches_family(chunk, str(group.get("family") or ""))
            ]
            for field in missing_fields:
                missing_required_fields.add(field)

            for idx, chunk in retry_candidates:
                forced = self._extract_for_family_retry(str(group.get("family") or ""), str(chunk.get("text", "")))
                if not forced:
                    continue
                converted = self._apply_unit_conversion(forced, chunk.get("unit_multiplier", 1.0), str(chunk.get("text", "")))
                converted = self._filter_fields_by_table_type(converted, str(chunk.get("table_type") or ""))
                converted = {k: v for k, v in converted.items() if k in missing_fields and v is not None}
                if not converted:
                    continue
                annotated = self._annotate_field_sources(converted, chunk, idx, "rules_retry")
                before = {field: retried.get(field) for field in converted.keys()}
                retried = self._merge_last_wins(retried, annotated)
                for field, old_value in before.items():
                    retried_fields.add(field)
                    if self._to_number(old_value) is None and self._to_number(retried.get(field)) is not None:
                        recovered_fields.add(field)

            for field in missing_fields:
                if self._to_number(retried.get(field)) is None:
                    missing_required_fields.add(field)

        if completeness_flags:
            deduped_flags: List[str] = []
            seen = set()
            for flag in completeness_flags:
                if flag not in seen:
                    deduped_flags.append(flag)
                    seen.add(flag)
            retried["_completeness_flags"] = deduped_flags
        if missing_required_fields:
            retried["_missing_required_fields"] = sorted(missing_required_fields)
        if retried_fields:
            retried["_retry_attempted_fields"] = sorted(retried_fields)
        if recovered_fields:
            retried["_retry_recovered_fields"] = sorted(recovered_fields)
        return retried

    # ── LLM 提取 ──────────────────────────────────────────────────────────────

    def _needs_llm_supplement(self, chunk: Dict[str, Any], rule_data: Dict[str, Any]) -> bool:
        if not rule_data:
            return False
        chunk_text = str(chunk.get("text", ""))
        if "<table" not in chunk_text.lower():
            return False
        has_income_context = any(marker in chunk_text for marker in ["利润表", "营业利润", "利润总额", "净利润"])
        if not has_income_context:
            return False
        missing_fields = [field for field in ["operating_profit", "total_profit"] if rule_data.get(field) is None]
        if not missing_fields:
            return False
        support_hits = sum(
            1 for field in [
                "total_operating_revenue", "operating_expense_cost_of_sales",
                "operating_expense_selling_expenses", "operating_expense_administrative_expenses",
                "net_profit",
            ]
            if rule_data.get(field) is not None
        )
        return support_hits >= 2

    def _extract_chunk_data(self, chunk: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
        if not self._should_accept_chunk(chunk):
            return {}, "rejected"
        rule_data = self._extract_with_rules(chunk)
        if rule_data and not self._needs_llm_supplement(chunk, rule_data):
            return rule_data, "rules"

        chunk_text = str(chunk.get("text", ""))
        table_type = str(chunk.get("table_type") or "")
        if table_type in REJECTED_TABLE_TYPES or any(marker in chunk_text for marker in NON_PRIMARY_FINANCIAL_TABLE_MARKERS):
            return rule_data or {}, "rules"
        if "<table" not in chunk_text.lower():
            numeric_hits = len(re.findall(r"-?\d[\d,]*\.?\d*", chunk_text))
            field_hits = sum(
                1 for keyword in ["营业收入", "净利润", "总资产", "负债合计", "每股收益", "现金流量净额"]
                if keyword in chunk_text
            )
            if numeric_hits < 6 or field_hits < 2:
                return {}, "rejected"

        unit_multiplier = chunk['unit_multiplier']
        unit_name_map = {0.0001: '元', 0.1: '千元', 1.0: '万元', 100.0: '百万元', 10000.0: '亿元'}
        unit_name = unit_name_map.get(unit_multiplier, '万元')

        prompt = f"""你是财务数据提取助手。从下方内容中提取财务数据，映射到标准数据库字段。

## 重要：单位信息
本段数据的单位为【{unit_name}】。请提取原始数值（不要自行换算单位）。

## 目标数据库字段

### 利润表字段（金额类）
- total_operating_revenue (营业收入 / 营业总收入)
- operating_expense_cost_of_sales (营业成本)
- operating_expense_selling_expenses (销售费用)
- operating_expense_administrative_expenses (管理费用)
- operating_expense_financial_expenses (财务费用)
- operating_expense_rnd_expenses (研发费用)
- operating_expense_taxes_and_surcharges (税金及附加)
- total_operating_expenses (营业总支出 / 营业总成本)
- operating_profit (营业利润)
- total_profit (利润总额)
- net_profit (净利润，不是归属于上市公司/母公司股东的净利润)
- other_income (其他收益)
- asset_impairment_loss (资产减值损失)
- credit_impairment_loss (信用减值损失)

### 利润表字段（比率类，%，不换算）
- net_profit_yoy_growth (净利润同比增长率%)
- operating_revenue_yoy_growth (营业总收入同比增长率%)

### 资产负债表字段（金额类）
- asset_cash_and_cash_equivalents (货币资金)
- asset_accounts_receivable (应收账款)
- asset_inventory (存货)
- asset_trading_financial_assets (交易性金融资产)
- asset_construction_in_progress (在建工程)
- asset_total_assets (资产总计 / 总资产)
- liability_accounts_payable (应付账款)
- liability_advance_from_customers (预收账款)
- liability_contract_liabilities (合同负债)
- liability_short_term_loans (短期借款)
- liability_total_liabilities (负债合计 / 总负债)
- equity_unappropriated_profit (未分配利润)
- equity_total_equity (股东权益合计 / 所有者权益合计)
- equity_parent_attributable (归属于上市公司股东/母公司股东的净资产或所有者权益)
- equity_minority_interest (少数股东权益)
- share_capital (实收资本/股本，股本原始数值，不换算)

### 资产负债表字段（比率类，%，不换算）
- asset_total_assets_yoy_growth (总资产同比增长率%)
- liability_total_liabilities_yoy_growth (总负债同比增长率%)
- asset_liability_ratio (资产负债率%)

### 现金流量表字段（金额类）
- operating_cf_net_amount (经营活动产生的现金流量净额)
- operating_cf_cash_from_sales (销售商品、提供劳务收到的现金)
- investing_cf_net_amount (投资活动产生的现金流量净额)
- investing_cf_cash_for_investments (投资支付的现金)
- investing_cf_cash_from_investment_recovery (收回投资收到的现金)
- financing_cf_net_amount (筹资活动产生的现金流量净额)
- financing_cf_cash_from_borrowing (取得借款收到的现金)
- financing_cf_cash_for_debt_repayment (偿还债务支付的现金)
- net_cash_flow (现金及现金等价物净增加额)

### 现金流量表字段（比率类，%，不换算）
- net_cash_flow_yoy_growth (净现金流同比增长率%)
- operating_cf_ratio_of_net_cf (经营性现金流占净现金流比例%)
- investing_cf_ratio_of_net_cf (投资性现金流占净现金流比例%)
- financing_cf_ratio_of_net_cf (筹资性现金流占净现金流比例%)

### 核心业绩指标字段（每股/比率类，不换算）
- eps (基本每股收益，元/股)
- net_asset_per_share (每股净资产，元)
- operating_cf_per_share (每股经营现金流量，元)
- roe (加权平均净资产收益率，%)
- roe_weighted_excl_non_recurring (扣非后加权平均净资产收益率，%)
- gross_profit_margin (销售毛利率，%)
- net_profit_margin (销售净利率，%)
- net_profit_excl_non_recurring (归属于上市公司股东的扣除非经常性损益的净利润，金额类)
- net_profit_excl_non_recurring_yoy (扣非净利润同比增长率，%)
- net_profit_yoy_growth (净利润同比增长率，%)
- net_profit_qoq_growth (净利润季度环比增长率，%)
- operating_revenue_yoy_growth (营业总收入同比增长率，%)
- operating_revenue_qoq_growth (营业总收入季度环比增长率，%)

## 提取规则
1. 若表格有多列（当期/上期/增减），只提取【当期 / 本年 / 本报告期】对应列的值。
2. 去除数值中的逗号和空格，保留负号。
3. 所有百分比(%)、每股(元)指标，以及 share_capital(股本) 保持原值，不受单位影响。
4. 若某字段在本段内容中不存在、为空、写着“不适用”，不要猜测，直接省略，绝对不要填 0。
5. 只提取当前这一个表块中能明确定位到的值，不要引用其他表、其他年份、其他列。
6. 如果同一行存在“本年/上年/增减%”，金额字段取“本年/本期”，增长率字段取“增减(%)”。
7. 主三张报表只提取合并报表口径；如果当前内容看起来像母公司报表或无法确认是合并报表，不要提取主三表字段。
8. `net_profit_qoq_growth`、`operating_revenue_qoq_growth` 只在“分季度主要财务数据”里可提取；不能用累计口径、同比列或跨年列代替。
9. 只返回 JSON 对象，不要解释。

## 内容
{chunk['text'][:5000]}
"""
        response = self.llm.chat([{"role": "user", "content": prompt}], temperature=0.1)
        if not response:
            return rule_data or {}, "rules" if rule_data else "llm"
        try:
            clean = re.sub(r'```json|```', '', response).strip()
            # 尝试提取 JSON 对象（防止 LLM 多余文字包裹）
            json_match = re.search(r'\{[\s\S]*\}', clean)
            if json_match:
                data = json.loads(json_match.group())
            else:
                data = json.loads(clean)
            normalized = self._normalize_field_aliases(data)
            if rule_data:
                merged = dict(rule_data)
                for key, value in normalized.items():
                    if merged.get(key) is None and value is not None:
                        merged[key] = value
                return merged, "rules+llm"
            return normalized, "llm"
        except json.JSONDecodeError:
            logger.warning(f"[ETL] JSON parse failed: {response[:200]}")
            return rule_data or {}, "rules" if rule_data else "llm"

    def _process_chunk_with_llm(self, chunk: Dict[str, Any]) -> Dict[str, Any]:
        data, _ = self._extract_chunk_data(chunk)
        return data

    def _extract_with_rules(self, chunk: Dict[str, Any]) -> Dict[str, Any]:
        """对标准财务指标表优先走规则抽取，减少 LLM 调用。"""
        text = chunk.get("text", "")
        table_type = str(chunk.get("table_type") or "")
        if "<table" not in text:
            return {}
        if table_type in REJECTED_TABLE_TYPES:
            return {}
        if table_type == "core_metrics" and any(marker in text for marker in [
            "主要会计数据和财务指标发生变动的情况及原因",
            "主要会计数据、财务指标发生变动的情况、原因",
            "主要财务指标发生变动的情况及原因",
            "变动比例",
            "变动比率",
            "变动原因",
            "主要原因",
        ]):
            return {}
        if table_type == "core_metrics":
            return self._extract_core_metrics_table(text)
        if table_type == "quarter_summary":
            return self._extract_quarter_summary_table(text)
        if table_type == "primary_cashflow":
            return self._extract_cashflow_table(text, allow_split_alias_fallback=bool(chunk.get("_repaired_combined_cashflow")))
        if table_type == "primary_income":
            return self._extract_income_table(text)
        if table_type == "primary_balance":
            return self._extract_balance_table(text)

        core_metric_markers = [
            "主要会计数据和财务指标",
            "主要财务指标",
            "主要会计数据",
            "报告期分季度的主要会计数据",
            "分季度主要财务数据",
            "分季度主要会计数据",
            "季度主要财务指标",
            "每股收益",
            "每股净资产",
            "净资产收益率",
        ]
        if any(marker in text for marker in core_metric_markers):
            if any(marker in text for marker in ["报告期分季度的主要会计数据", "分季度主要财务数据", "分季度主要会计数据", "季度主要财务指标"]):
                return self._extract_quarter_summary_table(text)
            return self._extract_core_metrics_table(text)
        extracted: Dict[str, Any] = {}
        cashflow_markers = [
            "现金流量表",
            "经营活动产生的现金流量净额",
            "销售商品、提供劳务收到的现金",
            "销售商品和提供劳务收到的现金",
            "取得借款收到的现金",
            "现金及现金等价物净增加额",
            "现金及现金等价物净变动情况",
        ]
        income_markers = [
            "利润表",
            "营业收入",
            "营业成本",
            "利润总额",
            "净利润",
        ]
        balance_markers = [
            "资产负债表",
            "资产及负债状况",
            "总资产",
            "资产总计",
            "总负债",
            "负债合计",
            "短期借款",
        ]

        if any(marker in text for marker in cashflow_markers):
            extracted.update(self._extract_cashflow_table(text))
        if any(marker in text for marker in income_markers):
            extracted.update({k: v for k, v in self._extract_income_table(text).items() if extracted.get(k) is None and v is not None})
        if any(marker in text for marker in balance_markers):
            extracted.update({k: v for k, v in self._extract_balance_table(text).items() if extracted.get(k) is None and v is not None})

        return extracted

    def _extract_core_metrics_table(self, text: str) -> Dict[str, Any]:
        extracted: Dict[str, Any] = {}
        state_period = getattr(self.state, "report_period", "") or ""
        try:
            tables = pd.read_html(StringIO(text))
        except Exception:
            tables = []
        for df in tables:
            if df.empty or df.shape[1] < 2:
                continue
            df = df.fillna("")
            headers = [self._flatten_header(c) for c in df.columns]
            first_col = df.iloc[:, 0].astype(str).tolist()
            table_text = " ".join(first_col)
            report_period = state_period or self._infer_metric_report_period(text, headers)

            if self._looks_like_roe_eps_special_table(table_text, headers):
                special_data = self._extract_roe_eps_special_table(df, headers)
                extracted.update({k: v for k, v in special_data.items() if extracted.get(k) is None and v is not None})

            if self._looks_like_core_metrics_table(table_text, headers):
                extracted.update(self._extract_key_value_rows(df, headers, report_period))

            if self._looks_like_quarterly_summary(df, table_text, headers):
                q_data = self._extract_quarterly_summary(df)
                extracted.update({k: v for k, v in q_data.items() if k not in extracted})

        fallback = self._extract_core_metrics_table_from_html(text)
        extracted.update({k: v for k, v in fallback.items() if extracted.get(k) is None and v is not None})
        return extracted

    def _extract_quarter_summary_table(self, text: str) -> Dict[str, Any]:
        extracted: Dict[str, Any] = {}
        try:
            tables = pd.read_html(StringIO(text))
        except Exception:
            tables = []
        for df in tables:
            if df.empty or df.shape[1] < 2:
                continue
            df = df.fillna("")
            headers = [self._flatten_header(c) for c in df.columns]
            first_col = df.iloc[:, 0].astype(str).tolist()
            table_text = " ".join(first_col)
            if self._looks_like_quarterly_summary(df, table_text, headers):
                q_data = self._extract_quarterly_summary(df)
                extracted.update({k: v for k, v in q_data.items() if extracted.get(k) is None and v is not None})
        fallback = self._extract_quarter_summary_table_from_html(text)
        extracted.update({k: v for k, v in fallback.items() if extracted.get(k) is None and v is not None})
        return extracted

    def _looks_like_core_metrics_table(self, table_text: str, headers: List[str]) -> bool:
        content = " ".join(headers) + " " + table_text
        if any(marker in content for marker in NON_PRIMARY_FINANCIAL_TABLE_MARKERS):
            return False
        keywords = [
            "营业收入", "营业总收入", "净利润", "扣除非经常性损益",
            "经营活动产生的现金流量净额", "每股收益", "每股净资产",
            "净资产收益率", "总资产", "资产总计",
        ]
        core_row_keywords = [
            "营业收入", "营业总收入", "净利润", "每股收益", "净资产收益率",
            "经营活动产生的现金流量净额",
        ]
        return (
            sum(1 for keyword in keywords if keyword in content) >= 2
            and any(keyword in content for keyword in core_row_keywords)
        )

    def _looks_like_roe_eps_special_table(self, table_text: str, headers: List[str]) -> bool:
        content = " ".join(headers) + " " + table_text
        if "加权平均净资产收益率" not in content:
            return False
        if "每股收益" not in content and "基本每股收益" not in content and "稀释每股收益" not in content:
            return False
        row_markers = [
            "归属于公司普通股股东的净利润",
            "归属于上市公司股东的净利润",
            "归属于母公司股东的净利润",
            "扣除非经常性损益后归属于公司普通股股东的净利润",
            "归属于公司普通股股东的扣除非经常性损益后的净利润",
        ]
        return any(marker in content for marker in row_markers)

    def _special_metric_col_idx(self, headers: List[str], *keywords: str) -> Optional[int]:
        for idx, header in enumerate(headers):
            if idx == 0:
                continue
            if all(keyword in header for keyword in keywords):
                return idx
        return None

    def _is_roe_base_profit_row(self, key: str) -> bool:
        normalized = re.sub(r"\s+", "", key)
        if "扣除非经常性损益" in normalized:
            return False
        return (
            "净利润" in normalized
            and ("归属于公司普通股股东" in normalized or "归属于上市公司股东" in normalized or "归属于母公司股东" in normalized)
        )

    def _is_roe_excl_profit_row(self, key: str) -> bool:
        normalized = re.sub(r"\s+", "", key)
        return "净利润" in normalized and "扣除非经常性损益" in normalized

    def _extract_roe_eps_special_table(self, df: pd.DataFrame, headers: List[str]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        roe_idx = self._special_metric_col_idx(headers, "加权平均净资产收益率")
        basic_eps_idx = self._special_metric_col_idx(headers, "基本每股收益")
        diluted_eps_idx = self._special_metric_col_idx(headers, "稀释每股收益")
        fallback_eps_idx = self._special_metric_col_idx(headers, "每股收益")

        for _, row in df.iterrows():
            key = str(row.iloc[0]).strip()
            if not key:
                continue
            if self._is_roe_base_profit_row(key):
                roe_value = self._safe_series_value(row, roe_idx)
                eps_value = self._coalesce_numeric_values(
                    self._safe_series_value(row, basic_eps_idx),
                    self._safe_series_value(row, diluted_eps_idx),
                    self._safe_series_value(row, fallback_eps_idx),
                )
                if roe_value is not None:
                    result["roe"] = roe_value
                if eps_value is not None:
                    result["eps"] = eps_value
            elif self._is_roe_excl_profit_row(key):
                roe_excl_value = self._safe_series_value(row, roe_idx)
                if roe_excl_value is not None:
                    result["roe_weighted_excl_non_recurring"] = roe_excl_value

        return result

    def _looks_like_quarterly_summary(self, df: pd.DataFrame, table_text: str, headers: List[str]) -> bool:
        quarter_col_indices, _ = self._pick_quarter_value_cols(headers, df)
        return len(quarter_col_indices) >= 3 and "营业收入" in table_text

    def _core_metric_row_map(self) -> Dict[str, str]:
        return {
            "营业总收入": "total_operating_revenue",
            "营业收入": "total_operating_revenue",
            "资产总计": "asset_total_assets",
            "总资产": "asset_total_assets",
            "净利润": "net_profit",
            "归属于上市公司股东的净利润": "parent_net_profit",
            "归属于母公司所有者的净利润": "parent_net_profit",
            "归属于母公司股东的净利润": "parent_net_profit",
            "归属于上市公司股东的扣除非经常性损益的净利润": "net_profit_excl_non_recurring",
            "归属于母公司股东的扣除非经常性损益的净利润": "net_profit_excl_non_recurring",
            "扣除非经常性损益后的净利润": "net_profit_excl_non_recurring",
            "归属于上市公司股东的所有者权益": "equity_parent_attributable",
            "归属于上市公司股东的所有者权益(元)": "equity_parent_attributable",
            "归属于上市公司股东的净资产": "equity_parent_attributable",
            "归属于上市公司股东的净资产(元)": "equity_parent_attributable",
            "归属于母公司所有者权益": "equity_parent_attributable",
            "归属于母公司股东权益": "equity_parent_attributable",
            "归属于母公司所有者净资产": "equity_parent_attributable",
            "归属于母公司股东净资产": "equity_parent_attributable",
            "归属于上市公司股东的每股净资产": "net_asset_per_share",
            "每股净资产": "net_asset_per_share",
            "每股经营活动产生的现金流量净额": "operating_cf_per_share",
            "每股经营现金流量净额": "operating_cf_per_share",
            "每股经营现金流量": "operating_cf_per_share",
            "每股经营活动现金流量净额": "operating_cf_per_share",
            "经营活动产生的现金流量净额": "operating_cf_net_amount",
            "加权平均净资产收益率（%）": "roe",
            "加权平均净资产收益率(%)": "roe",
            "加权平均净资产收益率": "roe",
            "扣除非经常性损益后的加权平均净资产收益率": "roe_weighted_excl_non_recurring",
            "加权平均净资产收益率（扣非）": "roe_weighted_excl_non_recurring",
            "加权平均净资产收益率(扣非)": "roe_weighted_excl_non_recurring",
            "基本每股收益（元/股）": "eps",
            "基本每股收益（元／股）": "eps",
            "基本每股收益(元/股)": "eps",
            "基本每股收益(元／股)": "eps",
            "基本每股收益": "eps",
            "稀释每股收益（元/股）": "eps",
            "稀释每股收益（元／股）": "eps",
            "稀释每股收益(元/股)": "eps",
            "稀释每股收益(元／股)": "eps",
            "稀释每股收益": "eps",
            "毛利率": "gross_profit_margin",
            "销售毛利率": "gross_profit_margin",
            "净利率": "net_profit_margin",
            "销售净利率": "net_profit_margin",
        }

    def _core_metric_growth_field(self, target_field: str) -> Optional[str]:
        return {
            "total_operating_revenue": "operating_revenue_yoy_growth",
            "asset_total_assets": "asset_total_assets_yoy_growth",
            "net_profit": "net_profit_yoy_growth",
            "net_profit_excl_non_recurring": "net_profit_excl_non_recurring_yoy",
        }.get(target_field)

    def _infer_metric_report_period(self, text: str, headers: List[str]) -> str:
        content = f"{text} {' '.join(headers)}"
        normalized = re.sub(r"[—–－至~]", "-", content)
        normalized = re.sub(r"\s+", "", normalized)
        if any(keyword in normalized for keyword in ["分季度主要财务数据", "分季度主要会计数据"]):
            return "FY"
        if any(keyword in normalized for keyword in ["第三季度报告", "三季度报告", "1-9月", "前9月", "前三季度"]):
            return "Q3"
        if any(keyword in normalized for keyword in ["半年度报告", "中期报告", "1-6月", "上半年", "半年度"]):
            return "HY"
        if any(keyword in normalized for keyword in ["第一季度报告", "一季度报告", "1-3月"]):
            return "Q1"
        if "年度报告" in normalized:
            return "FY"
        return ""

    def _core_metric_field_kind(self, target_field: str) -> str:
        if target_field in {
            "total_operating_revenue",
            "net_profit",
            "parent_net_profit",
            "net_profit_excl_non_recurring",
            "operating_cf_net_amount",
        }:
            return "period_cumulative"
        if target_field in {"asset_total_assets", "equity_parent_attributable", "net_asset_per_share"}:
            return "point_in_time"
        if target_field in {
            "eps", "roe", "roe_weighted_excl_non_recurring",
            "gross_profit_margin", "net_profit_margin",
            "operating_cf_per_share",
        }:
            return "period_ratio"
        return "generic"

    def _metric_value_preference_groups(self, report_period: str, target_field: str) -> List[List[str]]:
        field_kind = self._core_metric_field_kind(target_field)
        if field_kind in ("period_cumulative", "period_ratio"):
            if report_period == "Q1":
                return [
                    ["本报告期", "本期", "第一季度", "1-3月"],
                    ["年初至报告期末", "本年累计", "本期累计", "累计"],
                ]
            return [
                ["年初至报告期末", "本年累计", "本期累计", "前9月", "1-9月", "1-6月", "前三季度", "累计"],
                ["本报告期", "本期", "本年"],
            ]

        if field_kind == "point_in_time":
            return [
                ["本报告期末", "期末余额", "期末", "本年末"],
                ["本报告期", "本期"],
            ]

        return []

    def _header_has_growth_marker(self, header: Any) -> bool:
        normalized = re.sub(r"\s+", "", str(header or ""))
        return any(keyword in normalized for keyword in ["同比", "增减", "增长", "变动幅度", "百分点"])

    def _header_is_previous_metric_column(self, header: Any) -> bool:
        normalized = re.sub(r"\s+", "", str(header or ""))
        return any(keyword in normalized for keyword in ["上年同期", "上年", "上期", "上年度末", "期初", "年初"])

    def _header_has_cumulative_marker(self, header: Any) -> bool:
        normalized = re.sub(r"\s+", "", str(header or ""))
        return any(keyword in normalized for keyword in ["年初至报告期末", "本年累计", "本期累计", "累计", "前三季度", "1-9月", "1-6月"])

    def _looks_like_metric_data_row(self, values: List[Any]) -> bool:
        cells = [str(v).strip() for v in values if str(v).strip() and str(v).strip().lower() != "nan"]
        if not cells:
            return False
        metric_markers = [
            "营业收入", "净利润", "扣除非经常性损益", "每股收益", "净资产收益率",
            "总资产", "所有者权益", "现金流量净额", "经营活动产生的现金流量净额",
        ]
        label_window = " ".join(cells[:2])
        has_metric_label = any(marker in label_window for marker in metric_markers)
        numeric_count = sum(
            1 for cell in cells[1:]
            if self._to_number(cell) is not None or "%" in cell or "百分点" in cell
        )
        return has_metric_label and numeric_count >= 1

    def _looks_like_metric_subheader_row(self, row: List[Any]) -> bool:
        cells = [str(v).strip() for v in row]
        non_empty = [cell for cell in cells if cell and cell.lower() != "nan"]
        if not non_empty or self._looks_like_metric_data_row(row):
            return False
        header_markers = ["本报告期", "年初至报告期末", "本报告期末", "上年度末", "上年同期", "本期", "期末", "增减", "同比"]
        marker_hits = sum(1 for cell in non_empty if any(marker in cell for marker in header_markers))
        numeric_hits = sum(1 for cell in non_empty if self._to_number(cell) is not None)
        first_cell = cells[0] if cells else ""
        return (not first_cell or first_cell.lower() == "nan") and marker_hits >= 2 and numeric_hits <= 1

    def _effective_metric_period(self, headers: List[str], report_period: str) -> str:
        if report_period:
            return report_period
        joined = " ".join(str(h or "") for h in headers)
        if any(keyword in joined for keyword in ["年初至报告期末", "前三季度", "1-9月", "1-6月", "累计"]):
            return "Q3"
        return report_period

    def _metric_cell_looks_like_growth(
        self,
        target_field: str,
        header: Any,
        raw_value: Any,
        prev_raw: Any = None,
        next_raw: Any = None,
    ) -> bool:
        raw_text = str(raw_value or "").strip()
        if not raw_text:
            return False
        if "%" in raw_text or "百分点" in raw_text:
            return True

        numeric = self._to_number(raw_text)
        if numeric is None:
            return False

        if self._header_has_growth_marker(header):
            field_kind = self._core_metric_field_kind(target_field)
            if field_kind in {"period_cumulative", "point_in_time"}:
                return abs(numeric) <= 1000
            prev_num = self._to_number(prev_raw)
            next_num = self._to_number(next_raw)
            neighbors = [abs(v) for v in [prev_num, next_num] if v is not None and abs(v) > 1e-8]
            if neighbors and abs(numeric) > min(neighbors) * 5:
                return True
            if abs(numeric) >= 50 and any(abs(v) < 20 for v in neighbors):
                return True
        return False

    def _pick_metric_value_col_from_row_pattern(
        self,
        headers: List[str],
        row: List[Any],
        target_field: str,
        report_period: str = "",
    ) -> Optional[int]:
        if not row or len(row) <= 1:
            return None

        field_kind = self._core_metric_field_kind(target_field)
        effective_period = self._effective_metric_period(headers, report_period)
        upper = min(len(headers), len(row))
        candidate_indices = [idx for idx in range(1, upper) if self._to_number(row[idx]) is not None]
        if not candidate_indices:
            return None

        def is_growth_idx(idx: int) -> bool:
            prev_raw = row[idx - 1] if idx - 1 >= 0 else None
            next_raw = row[idx + 1] if idx + 1 < len(row) else None
            header = headers[idx] if idx < len(headers) else ""
            return self._metric_cell_looks_like_growth(target_field, header, row[idx], prev_raw, next_raw)

        if field_kind == "point_in_time":
            for idx in candidate_indices:
                header = headers[idx] if idx < len(headers) else ""
                if not self._header_is_previous_metric_column(header) and not is_growth_idx(idx):
                    return idx
            return candidate_indices[0]

        if effective_period != "Q1":
            default_idx = self._pick_metric_current_col(headers)
            for idx in candidate_indices:
                header = headers[idx] if idx < len(headers) else ""
                if self._header_has_cumulative_marker(header) and not self._header_is_previous_metric_column(header) and not is_growth_idx(idx):
                    return idx

            if default_idx is not None and default_idx in candidate_indices and not is_growth_idx(default_idx):
                return default_idx

            for idx in candidate_indices:
                if is_growth_idx(idx):
                    for next_idx in candidate_indices:
                        if next_idx > idx and not is_growth_idx(next_idx):
                            return next_idx

            non_growth = [idx for idx in candidate_indices if not is_growth_idx(idx)]
            if non_growth:
                return non_growth[0]

        for idx in candidate_indices:
            header = headers[idx] if idx < len(headers) else ""
            if not self._header_is_previous_metric_column(header) and not is_growth_idx(idx):
                return idx
        return candidate_indices[0]

    def _pick_metric_value_col_for_field(
        self,
        headers: List[str],
        target_field: str,
        report_period: str = "",
        row: Optional[List[Any]] = None,
    ) -> Optional[int]:
        if row:
            row_based_idx = self._pick_metric_value_col_from_row_pattern(headers, row, target_field, report_period)
            if row_based_idx is not None:
                return row_based_idx

        default_idx = self._pick_metric_current_col(headers)
        preference_groups = self._metric_value_preference_groups(report_period, target_field)
        if not preference_groups:
            return default_idx

        normalized_headers = [re.sub(r"\s+", "", h) for h in headers]

        for group in preference_groups:
            for keyword in group:
                for idx, norm_h in enumerate(normalized_headers):
                    if idx == 0:
                        continue
                    if keyword in norm_h and not self._header_is_previous_metric_column(norm_h) and not self._header_has_growth_marker(norm_h):
                        return idx
        return default_idx

    def _recover_headers_from_data_rows(self, df: pd.DataFrame, headers: List[str]) -> Tuple[List[str], int]:
        """When pd.read_html produces numeric headers, scan top rows to find real header text."""
        meaningful_keywords = ["本报告期", "年初至报告期末", "本期", "期末", "上年", "累计"]
        norm_headers = [re.sub(r"\s+", "", h) for h in headers]
        if any(any(kw in h for kw in meaningful_keywords) for h in norm_headers):
            return headers, 0
        last_header_ri = -1
        for ri in range(min(3, len(df))):
            raw_vals = [str(df.iloc[ri, j]).strip() for j in range(df.shape[1])]
            row_vals = [re.sub(r"\s+", "", value) for value in raw_vals]
            if any(any(kw in v for kw in meaningful_keywords) for v in row_vals) and not self._looks_like_metric_data_row(raw_vals):
                last_header_ri = ri
            elif last_header_ri >= 0:
                break
        if last_header_ri < 0:
            return headers, 0
        merged = []
        for ci in range(df.shape[1]):
            parts = []
            for sri in range(last_header_ri + 1):
                v = str(df.iloc[sri, ci]).strip()
                if v and v != "nan":
                    parts.append(v)
            merged.append("".join(parts) if parts else headers[ci])
        return merged, last_header_ri + 1

    def _extract_key_value_rows(self, df: pd.DataFrame, headers: Optional[List[str]] = None, report_period: str = "") -> Dict[str, Any]:
        row_map = self._core_metric_row_map()
        result: Dict[str, Any] = {}
        headers = headers or [self._flatten_header(c) for c in df.columns]
        headers, skip_rows = self._recover_headers_from_data_rows(df, headers)
        if skip_rows > 0:
            df = df.iloc[skip_rows:].reset_index(drop=True)
        active_headers = list(headers)

        for _, row in df.iterrows():
            row_values = row.tolist()
            if self._looks_like_metric_subheader_row(row_values):
                active_headers = [str(value).strip() for value in row_values]
                if active_headers:
                    active_headers[0] = active_headers[0] or "项目"
                continue
            key = str(row.iloc[0]).strip()
            if not key:
                continue
            target_field = self._match_row_alias(key, row_map)
            if not target_field:
                continue

            current_col_idx = self._pick_metric_value_col_for_field(active_headers, target_field, report_period, row=row_values)
            yoy_col_idx = self._pick_metric_yoy_col(active_headers, current_col_idx)
            current_value = self._to_number(row.iloc[current_col_idx]) if current_col_idx is not None and current_col_idx < len(row) else None
            yoy_value = self._to_number(row.iloc[yoy_col_idx]) if yoy_col_idx is not None and yoy_col_idx < len(row) else None
            self._record_extracted_field(result, target_field, current_value, yoy_value)

        return result

    def _extract_quarterly_summary(self, df: pd.DataFrame) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        headers = [self._flatten_header(c) for c in df.columns]
        quarter_col_indices, data_start_row = self._pick_quarter_value_cols(headers, df)
        if len(quarter_col_indices) < 2:
            return result
        prev_idx, current_idx = quarter_col_indices[-2], quarter_col_indices[-1]
        quarter_qoq_backfill: Dict[str, Dict[str, float]] = {}

        for row_idx in range(data_start_row, len(df)):
            row = df.iloc[row_idx]
            key = str(row.iloc[0]).strip()
            if "营业收入" in key and current_idx < len(row):
                val = self._to_number(row.iloc[current_idx])
                prev_val = self._to_number(row.iloc[prev_idx]) if prev_idx < len(row) else None
                growth = self._calc_growth(val, prev_val)
                if growth is not None:
                    result["operating_revenue_qoq_growth"] = growth
                period_map = self._build_quarter_qoq_map_from_series(row, headers, "operating_revenue_qoq_growth")
                for period, values in period_map.items():
                    quarter_qoq_backfill.setdefault(period, {}).update(values)
            elif self._is_parent_net_profit_row(key) and current_idx < len(row):
                val = self._to_number(row.iloc[current_idx])
                prev_val = self._to_number(row.iloc[prev_idx]) if prev_idx < len(row) else None
                growth = self._calc_growth(val, prev_val)
                if growth is not None:
                    result["net_profit_qoq_growth"] = growth
                period_map = self._build_quarter_qoq_map_from_series(row, headers, "net_profit_qoq_growth")
                for period, values in period_map.items():
                    quarter_qoq_backfill.setdefault(period, {}).update(values)
            elif self._is_actual_net_profit_row(key) and current_idx < len(row) and result.get("net_profit_qoq_growth") is None:
                val = self._to_number(row.iloc[current_idx])
                prev_val = self._to_number(row.iloc[prev_idx]) if prev_idx < len(row) else None
                growth = self._calc_growth(val, prev_val)
                if growth is not None:
                    result["net_profit_qoq_growth"] = growth
                period_map = self._build_quarter_qoq_map_from_series(row, headers, "net_profit_qoq_growth")
                for period, values in period_map.items():
                    quarter_qoq_backfill.setdefault(period, {}).update(values)
        if quarter_qoq_backfill:
            result["_quarter_qoq_backfill"] = quarter_qoq_backfill
        return result

    def _calc_growth(self, current: Optional[float], previous: Optional[float]) -> Optional[float]:
        if current is None or previous is None:
            return None
        if abs(previous) < 1e-8:
            return None
        return round((current - previous) / abs(previous) * 100, 4)

    def _quarter_header_index_map(self, headers: List[str]) -> Dict[str, int]:
        quarter_order = ["第一季度", "第二季度", "第三季度", "第四季度"]
        header_map: Dict[str, int] = {}
        for quarter in quarter_order:
            for idx, header in enumerate(headers):
                if idx == 0:
                    continue
                if quarter in header:
                    header_map[quarter] = idx
                    break
        return header_map

    def _growth_limit_for_field(self, field: str) -> float:
        limit = {
            "operating_revenue_yoy_growth": 1000.0,
            "net_profit_yoy_growth": 1000.0,
            "net_profit_excl_non_recurring_yoy": 1000.0,
        }.get(field)
        return float("inf") if limit is None else limit

    def _build_quarter_qoq_map_from_values(self, quarter_values: Dict[str, Optional[float]], field: str) -> Dict[str, Dict[str, float]]:
        period_pairs = [
            ("HY", "第一季度", "第二季度"),
            ("Q3", "第二季度", "第三季度"),
            ("FY", "第三季度", "第四季度"),
        ]
        result: Dict[str, Dict[str, float]] = {}
        limit = self._growth_limit_for_field(field)
        for target_period, prev_quarter, current_quarter in period_pairs:
            growth = self._calc_growth(quarter_values.get(current_quarter), quarter_values.get(prev_quarter))
            if growth is None or abs(growth) > limit:
                continue
            result[target_period] = {field: growth}
        return result

    def _build_quarter_qoq_map_from_series(self, row: pd.Series, headers: List[str], field: str) -> Dict[str, Dict[str, float]]:
        header_map = self._quarter_header_index_map(headers)
        quarter_values = {
            quarter: self._to_number(row.iloc[idx]) if idx < len(row) else None
            for quarter, idx in header_map.items()
        }
        return self._build_quarter_qoq_map_from_values(quarter_values, field)

    def _pick_metric_current_col(self, headers: List[str]) -> Optional[int]:
        if len(headers) <= 1:
            return None
        skip_keywords = ["上年", "上期", "同比", "增减", "增长"]
        preferred_keywords = [
            "年初至报告期末", "本报告期末", "期末余额", "期末", "本年末",
            "本报告期", "本期", "本年", "2025", "2024", "2023", "2022",
        ]
        normalized_headers = [re.sub(r"\s+", "", h) for h in headers]
        for keyword in preferred_keywords:
            for idx, norm_h in enumerate(normalized_headers):
                if idx == 0:
                    continue
                if keyword in norm_h and not any(skip in norm_h for skip in skip_keywords):
                    return idx

        year_cols = []
        for idx in range(1, len(headers)):
            header = str(headers[idx] or "")
            years = re.findall(r'20\d{2}', header)
            if years:
                year_cols.append((idx, int(years[0]), header))
        
        if year_cols:
            year_cols.sort(key=lambda x: x[1], reverse=True)
            return year_cols[0][0]

        for idx, header in enumerate(headers):
            if idx == 0:
                continue
            if any(skip in header for skip in skip_keywords):
                continue
            return idx
        return 1 if len(headers) > 1 else None

    def _pick_metric_yoy_col(self, headers: List[str], current_col_idx: Optional[int] = None) -> Optional[int]:
        growth_keywords = [
            "同比", "增减", "增长率", "本年比上年增减",
            "本报告期比上年同期增减", "本期比上年同期增减",
            "本期末比上年同期末增减", "比上年同期增减",
        ]
        normalized_headers = [re.sub(r"\s+", "", h) for h in headers]
        if current_col_idx is not None and 0 <= current_col_idx < len(normalized_headers):
            current_header = normalized_headers[current_col_idx]
            preferred_markers: List[str] = []
            if "年初至报告期末" in current_header:
                preferred_markers.extend(["年初至报告期末", "累计", "前9月", "1-9月", "1-6月"])
            elif "本报告期末" in current_header or "期末" in current_header or "年末" in current_header:
                preferred_markers.extend(["期末", "本报告期末", "本期末", "年末"])
            elif "本报告期" in current_header:
                preferred_markers.append("本报告期")
            elif "本期" in current_header:
                preferred_markers.append("本期")

            if preferred_markers:
                for idx, norm_h in enumerate(normalized_headers):
                    if idx == 0:
                        continue
                    if idx == current_col_idx:
                        continue
                    if any(keyword in norm_h for keyword in growth_keywords) and any(marker in norm_h for marker in preferred_markers):
                        return idx

            for idx in range(current_col_idx + 1, len(normalized_headers)):
                if idx == current_col_idx:
                    continue
                if any(keyword in normalized_headers[idx] for keyword in growth_keywords):
                    return idx

        for idx, norm_h in enumerate(normalized_headers):
            if idx == 0:
                continue
            if idx == current_col_idx:
                continue
            if any(keyword in norm_h for keyword in growth_keywords):
                return idx
        return None

    def _pick_quarter_value_cols(self, headers: List[str], df: pd.DataFrame) -> Tuple[List[int], int]:
        quarter_order = ["第一季度", "第二季度", "第三季度", "第四季度"]
        header_map: Dict[str, int] = {}
        for quarter in quarter_order:
            for idx, header in enumerate(headers):
                if idx == 0:
                    continue
                if quarter in header:
                    header_map[quarter] = idx
                    break
        if len(header_map) >= 3:
            return [header_map[q] for q in quarter_order if q in header_map], 0

        if not df.empty:
            first_row = [str(x).strip() for x in df.iloc[0].tolist()]
            row_map: Dict[str, int] = {}
            for quarter in quarter_order:
                for idx, cell in enumerate(first_row):
                    if idx == 0:
                        continue
                    if quarter in cell:
                        row_map[quarter] = idx
                        break
            if len(row_map) >= 3:
                return [row_map[q] for q in quarter_order if q in row_map], 1

        return [], 0

    def _extract_core_metrics_table_from_html(self, text: str) -> Dict[str, Any]:
        extracted: Dict[str, Any] = {}
        state_period = getattr(self.state, "report_period", "") or ""
        for rows in self._extract_html_tables_basic(text):
            if len(rows) < 2:
                continue
            headers, data_rows = self._pick_matrix_headers_and_rows(rows)
            if len(headers) < 2 or not data_rows:
                continue
            table_text = " ".join(row[0] for row in data_rows if row)
            report_period = state_period or self._infer_metric_report_period(text, headers)
            if self._looks_like_roe_eps_special_table(table_text, headers):
                special_data = self._extract_roe_eps_special_rows(data_rows, headers)
                extracted.update({k: v for k, v in special_data.items() if extracted.get(k) is None and v is not None})
            if self._looks_like_core_metrics_table(table_text, headers):
                row_data = self._extract_key_value_rows_from_matrix(data_rows, headers, report_period)
                extracted.update({k: v for k, v in row_data.items() if extracted.get(k) is None and v is not None})
            if self._looks_like_quarterly_summary_rows(data_rows, headers):
                q_data = self._extract_quarterly_summary_from_matrix(data_rows, headers)
                extracted.update({k: v for k, v in q_data.items() if extracted.get(k) is None and v is not None})
        return extracted

    def _extract_quarter_summary_table_from_html(self, text: str) -> Dict[str, Any]:
        extracted: Dict[str, Any] = {}
        for rows in self._extract_html_tables_basic(text):
            if len(rows) < 2:
                continue
            headers, data_rows = self._pick_matrix_headers_and_rows(rows)
            if len(headers) < 2 or not data_rows:
                continue
            if self._looks_like_quarterly_summary_rows(data_rows, headers):
                q_data = self._extract_quarterly_summary_from_matrix(data_rows, headers)
                extracted.update({k: v for k, v in q_data.items() if extracted.get(k) is None and v is not None})
        return extracted

    def _extract_roe_eps_special_rows(self, rows: List[List[str]], headers: List[str]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        roe_idx = self._special_metric_col_idx(headers, "加权平均净资产收益率")
        basic_eps_idx = self._special_metric_col_idx(headers, "基本每股收益")
        diluted_eps_idx = self._special_metric_col_idx(headers, "稀释每股收益")
        fallback_eps_idx = self._special_metric_col_idx(headers, "每股收益")

        for row in rows:
            if not row:
                continue
            key = row[0].strip()
            if not key:
                continue
            if self._is_roe_base_profit_row(key):
                roe_value = self._safe_matrix_value(row, roe_idx)
                eps_value = self._coalesce_numeric_values(
                    self._safe_matrix_value(row, basic_eps_idx),
                    self._safe_matrix_value(row, diluted_eps_idx),
                    self._safe_matrix_value(row, fallback_eps_idx),
                )
                if roe_value is not None:
                    result["roe"] = roe_value
                if eps_value is not None:
                    result["eps"] = eps_value
            elif self._is_roe_excl_profit_row(key):
                roe_excl_value = self._safe_matrix_value(row, roe_idx)
                if roe_excl_value is not None:
                    result["roe_weighted_excl_non_recurring"] = roe_excl_value

        return result

    def _safe_series_value(self, row: pd.Series, idx: Optional[int]) -> Optional[float]:
        if idx is None or idx >= len(row):
            return None
        return self._to_number(row.iloc[idx])

    def _coalesce_numeric_values(self, *values: Optional[float]) -> Optional[float]:
        for value in values:
            if value is not None:
                return value
        return None

    def _extract_html_tables_basic(self, text: str) -> List[List[List[str]]]:
        tables: List[List[List[str]]] = []
        for table_html in TABLE_RE.findall(text):
            soup = BeautifulSoup(table_html, "html.parser")
            row_defs: List[List[Tuple[str, int, int]]] = []
            for tr in soup.find_all("tr"):
                row_cells: List[Tuple[str, int, int]] = []
                for cell in tr.find_all(["td", "th"], recursive=False):
                    cell_text = self._clean_html_cell(str(cell))
                    rowspan = int(cell.get("rowspan", 1) or 1)
                    colspan = int(cell.get("colspan", 1) or 1)
                    row_cells.append((cell_text, rowspan, colspan))
                if row_cells:
                    row_defs.append(row_cells)
            rows = self._expand_table_spans(row_defs)
            if rows:
                tables.append(rows)
        return tables

    def _expand_table_spans(self, row_defs: List[List[Tuple[str, int, int]]]) -> List[List[str]]:
        expanded_rows: List[List[str]] = []
        pending: Dict[int, Tuple[str, int]] = {}
        max_cols = 0
        for row_cells in row_defs:
            row: List[str] = []
            col = 0

            while True:
                if col in pending:
                    text, remain = pending[col]
                    row.append(text)
                    if remain <= 1:
                        del pending[col]
                    else:
                        pending[col] = (text, remain - 1)
                    col += 1
                    continue
                break

            for text, rowspan, colspan in row_cells:
                while col in pending:
                    pending_text, remain = pending[col]
                    row.append(pending_text)
                    if remain <= 1:
                        del pending[col]
                    else:
                        pending[col] = (pending_text, remain - 1)
                    col += 1
                for offset in range(colspan):
                    row.append(text)
                    if rowspan > 1 and not self._looks_like_amount_text(text):
                        pending[col + offset] = (text, rowspan - 1)
                col += colspan

            while col in pending:
                pending_text, remain = pending[col]
                row.append(pending_text)
                if remain <= 1:
                    del pending[col]
                else:
                    pending[col] = (pending_text, remain - 1)
                col += 1

            max_cols = max(max_cols, len(row))
            expanded_rows.append(row)

        normalized: List[List[str]] = []
        carry = dict(pending)
        for row in expanded_rows:
            normalized.append(row + [""] * (max_cols - len(row)))
        if carry:
            # rare fallback: keep width stable even if tail spans remain
            max_cols = max(max_cols, max(carry.keys()) + 1)
            normalized = [row + [""] * (max_cols - len(row)) for row in normalized]
        return normalized

    def _clean_html_cell(self, cell: str) -> str:
        text = re.sub(r"<br\s*/?>", " ", cell, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _pick_matrix_headers_and_rows(self, rows: List[List[str]]) -> Tuple[List[str], List[List[str]]]:
        header_markers = ["本报告期", "年初至报告期末", "第一季度", "第二季度", "第三季度", "第四季度", "期末", "项目", "主要财务指标"]
        first = rows[0]
        second = rows[1] if len(rows) > 1 else []
        if second and any(marker in " ".join(first) for marker in header_markers) and any(
            marker in " ".join(second) for marker in ["调整前", "调整后", "同比", "增减", "第一季度", "第二季度", "第三季度", "第四季度"]
        ) and not self._looks_like_metric_data_row(second):
            merged = []
            width = max(len(first), len(second))
            first = first + [""] * (width - len(first))
            second = second + [""] * (width - len(second))
            for a, b in zip(first, second):
                merged.append(" ".join(part for part in [a, b] if part).strip())
            return merged, rows[2:]
        if any(marker in " ".join(first) for marker in header_markers):
            return first, rows[1:]
        if second and any(marker in " ".join(second) for marker in header_markers):
            return second, rows[2:]
        return first, rows[1:]

    def _extract_key_value_rows_from_matrix(self, rows: List[List[str]], headers: List[str], report_period: str = "") -> Dict[str, Any]:
        row_map = self._core_metric_row_map()
        result: Dict[str, Any] = {}
        active_headers = list(headers)
        for row in rows:
            if not row:
                continue
            if self._looks_like_metric_subheader_row(row):
                active_headers = [str(value).strip() for value in row]
                if active_headers:
                    active_headers[0] = active_headers[0] or "项目"
                continue
            key = row[0].strip()
            if not key:
                continue
            target_field = self._match_row_alias(key, row_map)
            if not target_field:
                continue
            current_col_idx = self._pick_metric_value_col_for_field(active_headers, target_field, report_period, row=row)
            yoy_col_idx = self._pick_metric_yoy_col(active_headers, current_col_idx)
            current_value = self._safe_matrix_value(row, current_col_idx)
            yoy_value = self._safe_matrix_value(row, yoy_col_idx)
            self._record_extracted_field(result, target_field, current_value, yoy_value)
        return result

    def _looks_like_quarterly_summary_rows(self, rows: List[List[str]], headers: List[str]) -> bool:
        quarter_headers = " ".join(headers)
        quarter_hits = sum(1 for keyword in ["第一季度", "第二季度", "第三季度", "第四季度"] if keyword in quarter_headers)
        table_text = " ".join(row[0] for row in rows if row)
        return quarter_hits >= 3 and "营业收入" in table_text

    def _extract_quarterly_summary_from_matrix(self, rows: List[List[str]], headers: List[str]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        quarter_indices = [idx for idx, header in enumerate(headers) if any(q in header for q in ["第一季度", "第二季度", "第三季度", "第四季度"])]
        if len(quarter_indices) < 2:
            return result
        prev_idx, current_idx = quarter_indices[-2], quarter_indices[-1]
        quarter_qoq_backfill: Dict[str, Dict[str, float]] = {}
        for row in rows:
            if not row:
                continue
            key = row[0].strip()
            current_value = self._safe_matrix_value(row, current_idx)
            prev_value = self._safe_matrix_value(row, prev_idx)
            if "营业收入" in key and current_value is not None:
                growth = self._calc_growth(current_value, prev_value)
                if growth is not None:
                    result["operating_revenue_qoq_growth"] = growth
                quarter_values = {
                    quarter: self._safe_matrix_value(row, idx)
                    for quarter, idx in self._quarter_header_index_map(headers).items()
                }
                period_map = self._build_quarter_qoq_map_from_values(quarter_values, "operating_revenue_qoq_growth")
                for period, values in period_map.items():
                    quarter_qoq_backfill.setdefault(period, {}).update(values)
            elif self._is_parent_net_profit_row(key) and current_value is not None:
                growth = self._calc_growth(current_value, prev_value)
                if growth is not None:
                    result["net_profit_qoq_growth"] = growth
                quarter_values = {
                    quarter: self._safe_matrix_value(row, idx)
                    for quarter, idx in self._quarter_header_index_map(headers).items()
                }
                period_map = self._build_quarter_qoq_map_from_values(quarter_values, "net_profit_qoq_growth")
                for period, values in period_map.items():
                    quarter_qoq_backfill.setdefault(period, {}).update(values)
            elif self._is_actual_net_profit_row(key) and current_value is not None and result.get("net_profit_qoq_growth") is None:
                growth = self._calc_growth(current_value, prev_value)
                if growth is not None:
                    result["net_profit_qoq_growth"] = growth
                quarter_values = {
                    quarter: self._safe_matrix_value(row, idx)
                    for quarter, idx in self._quarter_header_index_map(headers).items()
                }
                period_map = self._build_quarter_qoq_map_from_values(quarter_values, "net_profit_qoq_growth")
                for period, values in period_map.items():
                    quarter_qoq_backfill.setdefault(period, {}).update(values)
        if quarter_qoq_backfill:
            result["_quarter_qoq_backfill"] = quarter_qoq_backfill
        return result

    def _safe_matrix_value(self, row: List[str], idx: Optional[int]) -> Optional[float]:
        if idx is None or idx >= len(row):
            return None
        return self._to_number(row[idx])

    def _extract_balance_table(self, text: str) -> Dict[str, Any]:
        row_map = {
            "货币资金": "asset_cash_and_cash_equivalents",
            "应收账款": "asset_accounts_receivable",
            "存货": "asset_inventory",
            "交易性金融资产": "asset_trading_financial_assets",
            "在建工程": "asset_construction_in_progress",
            "资产总计": "asset_total_assets",
            "总资产": "asset_total_assets",
            "应付账款": "liability_accounts_payable",
            "预收款项": "liability_advance_from_customers",
            "预收账款": "liability_advance_from_customers",
            "合同负债": "liability_contract_liabilities",
            "短期借款": "liability_short_term_loans",
            "负债合计": "liability_total_liabilities",
            "总负债": "liability_total_liabilities",
            "未分配利润": "equity_unappropriated_profit",
            "所有者权益合计": "equity_total_equity",
            "股东权益合计": "equity_total_equity",
            "归属于上市公司股东的所有者权益": "equity_parent_attributable",
            "归属于上市公司股东的净资产": "equity_parent_attributable",
            "归属于母公司所有者权益合计": "equity_parent_attributable",
            "归属于母公司股东权益合计": "equity_parent_attributable",
            "归属于母公司所有者权益": "equity_parent_attributable",
            "归属于母公司股东权益": "equity_parent_attributable",
            "少数股东权益": "equity_minority_interest",
            "实收资本（或股本）": "share_capital",
            "实收资本(或股本)": "share_capital",
            "实收资本": "share_capital",
            "股本": "share_capital",
            "普通股股本": "share_capital",
        }
        return self._extract_statement_table_rows(text, row_map)

    def _extract_income_table(self, text: str) -> Dict[str, Any]:
        row_map = {
            "营业总收入": "total_operating_revenue",
            "营业收入": "total_operating_revenue",
            "营业成本": "operating_expense_cost_of_sales",
            "销售费用": "operating_expense_selling_expenses",
            "管理费用": "operating_expense_administrative_expenses",
            "财务费用": "operating_expense_financial_expenses",
            "研发费用": "operating_expense_rnd_expenses",
            "税金及附加": "operating_expense_taxes_and_surcharges",
            "营业总成本": "total_operating_expenses",
            "营业总支出": "total_operating_expenses",
            "营业利润": "operating_profit",
            "利润总额": "total_profit",
            "净利润": "net_profit",
            "归属于上市公司股东的净利润": "parent_net_profit",
            "归属于母公司所有者的净利润": "parent_net_profit",
            "归属于母公司股东的净利润": "parent_net_profit",
            "其他收益": "other_income",
            "资产减值损失": "asset_impairment_loss",
            "信用减值损失": "credit_impairment_loss",
        }
        return self._extract_statement_table_rows(text, row_map)

    def _extract_cashflow_table(self, text: str, allow_split_alias_fallback: bool = False) -> Dict[str, Any]:
        row_map = {
            "经营活动产生的现金流量净额": "operating_cf_net_amount",
            "销售商品、提供劳务收到的现金": "operating_cf_cash_from_sales",
            "销售商品和提供劳务收到的现金": "operating_cf_cash_from_sales",
            "销售商品、提供劳务收到现金": "operating_cf_cash_from_sales",
            "销售商品和提供劳务收到现金": "operating_cf_cash_from_sales",
            "投资活动产生的现金流量净额": "investing_cf_net_amount",
            "投资支付的现金": "investing_cf_cash_for_investments",
            "收回投资收到的现金": "investing_cf_cash_from_investment_recovery",
            "筹资活动产生的现金流量净额": "financing_cf_net_amount",
            "取得借款收到的现金": "financing_cf_cash_from_borrowing",
            "取得借款收到现金": "financing_cf_cash_from_borrowing",
            "偿还债务支付的现金": "financing_cf_cash_for_debt_repayment",
            "现金及现金等价物净增加额": "net_cash_flow",
            "净现金流": "net_cash_flow",
        }
        result = self._extract_statement_table_rows(text, row_map)
        if result.get("operating_cf_cash_from_sales") is None:
            direct_value = self._extract_value_by_alias_window(
                text,
                [
                    "销售商品、提供劳务收到的现金",
                    "销售商品和提供劳务收到的现金",
                    "销售商品、提供劳务收到现金",
                    "销售商品和提供劳务收到现金",
                ],
                allow_followup_number=allow_split_alias_fallback,
            )
            if direct_value is not None:
                result["operating_cf_cash_from_sales"] = direct_value
        if result.get("financing_cf_cash_from_borrowing") is None:
            direct_value = self._extract_value_by_alias_window(
                text,
                [
                    "取得借款收到的现金",
                    "取得借款收到现金",
                ],
                allow_followup_number=allow_split_alias_fallback,
            )
            if direct_value is not None:
                result["financing_cf_cash_from_borrowing"] = direct_value
        if result.get("investing_cf_cash_for_investments") is None:
            direct_value = self._extract_value_by_alias_window(
                text,
                [
                    "投资支付的现金",
                    "投资支付现金",
                ],
                allow_followup_number=allow_split_alias_fallback,
            )
            if direct_value is not None:
                result["investing_cf_cash_for_investments"] = direct_value
        if result.get("investing_cf_cash_from_investment_recovery") is None:
            direct_value = self._extract_value_by_alias_window(
                text,
                [
                    "收回投资收到的现金",
                    "收回投资收到现金",
                ],
                allow_followup_number=allow_split_alias_fallback,
            )
            if direct_value is not None:
                result["investing_cf_cash_from_investment_recovery"] = direct_value
        if result.get("financing_cf_cash_for_debt_repayment") is None:
            direct_value = self._extract_value_by_alias_window(
                text,
                [
                    "偿还债务支付的现金",
                    "偿还债务支付现金",
                ],
                allow_followup_number=allow_split_alias_fallback,
            )
            if direct_value is not None:
                result["financing_cf_cash_for_debt_repayment"] = direct_value
        self._populate_cashflow_ratio_fields(result)
        return result

    def _populate_cashflow_ratio_fields(self, data: Dict[str, Any]) -> None:
        net_cash_flow = self._to_number(data.get("net_cash_flow"))
        if net_cash_flow in (None, 0):
            return
        ratio_specs = [
            ("operating_cf_net_amount", "operating_cf_ratio_of_net_cf"),
            ("investing_cf_net_amount", "investing_cf_ratio_of_net_cf"),
            ("financing_cf_net_amount", "financing_cf_ratio_of_net_cf"),
        ]
        for amount_field, ratio_field in ratio_specs:
            if data.get(ratio_field) is not None:
                continue
            amount_value = self._to_number(data.get(amount_field))
            if amount_value is None:
                continue
            ratio_value = self._round_intermediate(amount_value / net_cash_flow * 100)
            if ratio_value is not None:
                data[ratio_field] = ratio_value

    def _normalize_label_for_match(self, text: Any) -> str:
        value = str(text or "").strip()
        value = re.sub(r"\s+", "", value)
        value = value.replace("：", "").replace(":", "")
        value = value.replace("（", "(").replace("）", ")")
        value = value.replace("、", "").replace("，", "").replace(",", "")
        return value

    def _allow_substring_alias_match(self, normalized_row: str, normalized_alias: str, field: str) -> bool:
        """Prevent summary aliases from matching subtotal/detail rows."""
        blocked_row_aliases = {
            ("asset_total_assets", "资产总计"): {"流动资产合计", "非流动资产合计"},
            ("asset_total_assets", "总资产"): {"流动资产总额", "非流动资产总额"},
            ("liability_total_liabilities", "负债合计"): {"流动负债合计", "非流动负债合计"},
            ("liability_total_liabilities", "总负债"): {"流动负债总额", "非流动负债总额"},
            ("equity_total_equity", "所有者权益合计"): {
                "归属于母公司所有者权益合计",
                "归属于上市公司股东的所有者权益",
            },
            ("equity_total_equity", "股东权益合计"): {
                "归属于母公司股东权益合计",
                "归属于上市公司股东权益合计",
            },
        }
        blocked_rows = blocked_row_aliases.get((field, normalized_alias), set())
        if any(blocked in normalized_row for blocked in blocked_rows):
            return False
        return True

    def _match_row_alias(self, row_name: str, row_map: Dict[str, str]) -> Optional[str]:
        normalized_row = self._normalize_label_for_match(row_name)

        # First pass: prefer exact matches across all aliases so that
        # "归属于母公司股东权益合计" is not prematurely captured by "股东权益合计".
        for alias, field in row_map.items():
            normalized_alias = self._normalize_label_for_match(alias)
            if not normalized_alias:
                continue
            if normalized_row == normalized_alias:
                return field

        exact_match = None
        # Second pass: allow fuzzy matches, but prefer longer aliases and
        # guard summary rows from matching subtotals like "流动负债合计".
        alias_items = sorted(
            row_map.items(),
            key=lambda item: len(self._normalize_label_for_match(item[0])),
            reverse=True,
        )
        for alias, field in alias_items:
            normalized_alias = self._normalize_label_for_match(alias)
            if not normalized_alias:
                continue
            if normalized_alias == "净利润":
                if normalized_alias not in normalized_row:
                    continue
                if self._is_parent_net_profit_row(normalized_row):
                    continue
                if any(marker in normalized_row for marker in ["持续经营净利润", "终止经营净利润", "少数股东损益"]):
                    continue
                exact_match = field
                continue
            if (
                normalized_alias
                and normalized_alias in normalized_row
                and self._allow_substring_alias_match(normalized_row, normalized_alias, field)
            ):
                return field
        return exact_match

    def _is_parent_net_profit_row(self, row_name: Any) -> bool:
        normalized_row = self._normalize_label_for_match(row_name)
        return any(
            marker in normalized_row
            for marker in [
                "归属于上市公司股东的净利润",
                "归属于母公司所有者的净利润",
                "归属于母公司股东的净利润",
            ]
        )

    def _is_actual_net_profit_row(self, row_name: Any) -> bool:
        normalized_row = self._normalize_label_for_match(row_name)
        if "净利润" not in normalized_row:
            return False
        if self._is_parent_net_profit_row(normalized_row):
            return False
        return not any(marker in normalized_row for marker in ["持续经营净利润", "终止经营净利润", "少数股东损益"])

    def _pick_previous_value_col(self, headers: List[str], current_col_idx: Optional[int]) -> Optional[int]:
        if current_col_idx is None:
            return None
        roles = self._build_column_roles(headers)
        for idx, role in roles.items():
            if idx == 0 or idx == current_col_idx:
                continue
            if role == "previous":
                return idx

        year_cols = []
        for idx in range(1, len(headers)):
            if idx == current_col_idx:
                continue
            header = str(headers[idx] or "")
            years = re.findall(r'20\d{2}', header)
            if years:
                year_cols.append((idx, int(years[0]), header))
        
        if year_cols:
            year_cols.sort(key=lambda x: x[1], reverse=True)
            for idx, year, header in year_cols:
                if idx != current_col_idx:
                    return idx

        for idx in range(current_col_idx + 1, len(headers)):
            if roles.get(idx) in {"note", "yoy", "quarter"}:
                continue
            return idx
        return None

    def _is_not_applicable_text(self, value: Any) -> bool:
        text = str(value or "").strip()
        return text in {"不适用", "—", "-", "--", "N/A", "n/a"}

    def _growth_field_for_statement(self, target_field: str) -> Optional[str]:
        return {
            "total_operating_revenue": "operating_revenue_yoy_growth",
            "net_profit": "net_profit_yoy_growth",
            "asset_total_assets": "asset_total_assets_yoy_growth",
            "liability_total_liabilities": "liability_total_liabilities_yoy_growth",
            "net_cash_flow": "net_cash_flow_yoy_growth",
        }.get(target_field)

    def _statement_previous_value_key(self, target_field: str) -> str:
        return f"_{target_field}_previous_value"

    def _assign_growth_from_row(
        self,
        result: Dict[str, Any],
        target_field: str,
        current_value: Optional[float],
        yoy_raw_value: Any = None,
        previous_value: Optional[float] = None,
    ) -> None:
        growth_field = self._growth_field_for_statement(target_field)
        if previous_value is not None and self._statement_previous_value_key(target_field) not in result:
            result[self._statement_previous_value_key(target_field)] = previous_value
        if not growth_field or result.get(growth_field) is not None:
            return
        yoy_value = self._to_number(yoy_raw_value)
        growth_limits = {
            "operating_revenue_yoy_growth": 1000.0,
            "net_profit_yoy_growth": 1000.0,
            "asset_total_assets_yoy_growth": 1000.0,
            "liability_total_liabilities_yoy_growth": 1000.0,
            "net_cash_flow_yoy_growth": float("inf"),
        }
        limit = growth_limits.get(growth_field, 1000.0)
        if yoy_value is not None and abs(yoy_value) <= limit:
            result[growth_field] = yoy_value
            return
        if current_value is not None and previous_value is not None:
            calc_growth = self._calc_growth(current_value, previous_value)
            if calc_growth is not None and abs(calc_growth) <= limit:
                result[growth_field] = calc_growth
                return
        return

    def _record_extracted_field(
        self,
        result: Dict[str, Any],
        target_field: str,
        current_value: Optional[float],
        yoy_raw_value: Any = None,
        previous_value: Optional[float] = None,
    ) -> None:
        if target_field == "liability_advance_from_customers" and current_value is None:
            current_value = 0.0

        if target_field == "parent_net_profit":
            if current_value is not None:
                result["parent_net_profit"] = current_value
                result["net_profit"] = current_value
            prev_key = self._statement_previous_value_key("net_profit")
            result.pop(prev_key, None)
            result.pop("net_profit_yoy_growth", None)
            self._assign_growth_from_row(result, "net_profit", current_value, yoy_raw_value, previous_value)
            return

        if target_field == "net_profit":
            if result.get("parent_net_profit") is not None:
                return
            if current_value is not None and result.get("net_profit") is None:
                result["net_profit"] = current_value
            self._assign_growth_from_row(result, "net_profit", current_value, yoy_raw_value, previous_value)
            return

        if current_value is not None and result.get(target_field) is None:
            result[target_field] = current_value
        self._assign_growth_from_row(result, target_field, current_value, yoy_raw_value, previous_value)

    def _backfill_statement_growth_from_original_row(
        self,
        data: Dict[str, Any],
        target_field: str,
        current_value: Optional[float],
    ) -> None:
        growth_field = self._growth_field_for_statement(target_field)
        if not growth_field or data.get(growth_field) is not None:
            return
        previous_value = self._to_number(data.get(self._statement_previous_value_key(target_field)))
        if current_value is None or previous_value is None:
            return
        calc_growth = self._calc_growth(current_value, previous_value)
        growth_limits = {
            "operating_revenue_yoy_growth": 1000.0,
            "net_profit_yoy_growth": 1000.0,
            "asset_total_assets_yoy_growth": 1000.0,
            "liability_total_liabilities_yoy_growth": 1000.0,
            "net_cash_flow_yoy_growth": float("inf"),
        }
        limit = growth_limits.get(growth_field, 1000.0)
        if calc_growth is not None and abs(calc_growth) <= limit:
            data[growth_field] = calc_growth

    def _extract_statement_rows_from_plain_text(self, text: str, row_map: Dict[str, str]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        raw_text = str(text)
        has_html_table = "<table" in raw_text.lower()
        if has_html_table:
            html_like = re.sub(r"</t[dh]>", "\t", raw_text, flags=re.IGNORECASE)
            html_like = re.sub(r"</tr>", "\n", html_like, flags=re.IGNORECASE)
            html_like = re.sub(r"<br\s*/?>", "\n", html_like, flags=re.IGNORECASE)
            raw_text = BeautifulSoup(html_like, "html.parser").get_text("\n")
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        pending_label = ""
        number_pattern = re.compile(r"-?\d[\d,]*\.\d+")
        multi_value_statement = any(len(number_pattern.findall(line)) >= 2 for line in lines)
        # If an HTML table degrades into one-cell-per-line text, column semantics are gone.
        # In that shape, plain-text fallback can easily treat a previous-period value as current.
        if has_html_table and not multi_value_statement:
            return result

        for line in lines:
            numbers = number_pattern.findall(line)
            if not numbers:
                if len(line) <= 80:
                    pending_label = f"{pending_label}{line}".strip()
                else:
                    pending_label = line
                continue

            label_part = line.split(numbers[0], 1)[0].strip()
            candidate_label = f"{pending_label}{label_part}".strip() if pending_label else label_part
            if not candidate_label:
                pending_label = ""
                continue

            target_field = self._match_row_alias(candidate_label, row_map)
            pending_label = ""
            if not target_field:
                continue

            current_value = self._to_number(numbers[0]) if len(numbers) >= 1 else None
            if len(numbers) == 1 and multi_value_statement:
                current_value = None
            previous_value = self._to_number(numbers[1]) if len(numbers) >= 2 else None
            yoy_value = self._to_number(numbers[2]) if len(numbers) >= 3 else None

            self._record_extracted_field(result, target_field, current_value, yoy_value, previous_value)

        return result

    def _extract_value_by_alias_window(
        self,
        text: str,
        aliases: List[str],
        allow_followup_number: bool = False,
    ) -> Optional[float]:
        raw_text = str(text)
        if "<table" in raw_text.lower():
            raw_text = re.sub(r"</t[dh]>", "\t", raw_text, flags=re.IGNORECASE)
            raw_text = re.sub(r"</tr>", "\n", raw_text, flags=re.IGNORECASE)
            raw_text = re.sub(r"<br\s*/?>", "\n", raw_text, flags=re.IGNORECASE)
            raw_text = BeautifulSoup(raw_text, "html.parser").get_text("\n")
        compact = re.sub(r"[ \t\u3000]+", " ", raw_text)
        compact = compact.replace("\r", "\n")
        number_pattern = re.compile(r"-?\d[\d,]*\.\d+")
        normalized_aliases = [self._normalize_label_for_match(alias) for alias in aliases]
        lines = compact.splitlines()

        normalized_number = lambda value: self._normalize_label_for_match(value)

        for idx, line in enumerate(lines):
            normalized_line = self._normalize_label_for_match(line)
            if not normalized_line:
                continue
            if any(alias in normalized_line for alias in normalized_aliases):
                numbers = number_pattern.findall(line)
                if numbers:
                    return self._to_number(numbers[0])
                if not allow_followup_number:
                    continue
                for next_line in lines[idx + 1:]:
                    stripped = str(next_line).strip()
                    if not stripped:
                        continue
                    next_numbers = number_pattern.findall(stripped)
                    normalized_next = self._normalize_label_for_match(stripped)
                    if next_numbers and normalized_next == normalized_number(next_numbers[0]):
                        return self._to_number(next_numbers[0])
                    break
        return None

    def _extract_statement_table_rows(self, text: str, row_map: Dict[str, str]) -> Dict[str, Any]:
        if any(marker in str(text) for marker in NON_PRIMARY_FINANCIAL_TABLE_MARKERS):
            return {}
        result: Dict[str, Any] = {}
        for rows in self._extract_html_tables_basic(text):
            if len(rows) < 2:
                continue
            headers, data_rows = self._pick_matrix_headers_and_rows(rows)
            if len(headers) < 2 or not data_rows:
                continue
            row_data = self._extract_statement_rows_from_matrix(data_rows, headers, row_map)
            result.update({k: v for k, v in row_data.items() if result.get(k) is None and v is not None})

        try:
            tables = pd.read_html(StringIO(text))
        except Exception:
            tables = []

        for df in tables:
            if df.empty or df.shape[1] < 2:
                continue
            df = df.fillna("")
            headers = [self._flatten_header(c) for c in df.columns]
            current_col_idx = self._pick_current_value_col(headers)
            current_col_idx = self._refine_statement_current_col(df, headers, current_col_idx)
            yoy_col_idx = self._pick_yoy_col(headers)
            previous_col_idx = self._pick_previous_value_col(headers, current_col_idx)

            if current_col_idx is None:
                continue

            for _, row in df.iterrows():
                row_name = str(row.iloc[0]).strip()
                if not row_name:
                    continue

                target_field = self._match_row_alias(row_name, row_map)
                if not target_field:
                    continue

                current_value = self._to_number(row.iloc[current_col_idx]) if current_col_idx < len(row) else None
                yoy_raw_value = row.iloc[yoy_col_idx] if yoy_col_idx is not None and yoy_col_idx < len(row) else None
                previous_value = self._to_number(row.iloc[previous_col_idx]) if previous_col_idx is not None and previous_col_idx < len(row) else None
                self._record_extracted_field(result, target_field, current_value, yoy_raw_value, previous_value)

        text_data = self._extract_statement_rows_from_plain_text(text, row_map)
        result.update({k: v for k, v in text_data.items() if result.get(k) is None and v is not None})

        return result

    def _extract_statement_rows_from_matrix(
        self,
        rows: List[List[str]],
        headers: List[str],
        row_map: Dict[str, str],
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        current_col_idx = self._pick_current_value_col(headers)
        yoy_col_idx = self._pick_yoy_col(headers)
        previous_col_idx = self._pick_previous_value_col(headers, current_col_idx)
        if current_col_idx is None:
            return result

        is_income_statement = "营业利润" in row_map and "利润总额" in row_map

        for row_idx, row in enumerate(rows):
            if not row:
                continue
            aligned_row = self._realign_row_for_missing_note_column(row, headers)
            inferred_field = None
            if is_income_statement:
                inferred_field = self._infer_broken_income_field(rows, row_idx)
            row_name = aligned_row[0].strip()
            if not row_name:
                continue

            target_field = self._match_row_alias(row_name, row_map)
            if not target_field and inferred_field:
                target_field = inferred_field
            if not target_field:
                continue

            if inferred_field == "total_profit":
                current_value = self._to_number(aligned_row[0]) if len(aligned_row) >= 1 else None
                previous_value = self._to_number(aligned_row[1]) if len(aligned_row) >= 2 else None
                yoy_raw_value = None
            else:
                current_value = self._safe_matrix_value(aligned_row, current_col_idx)
                yoy_raw_value = aligned_row[yoy_col_idx] if yoy_col_idx is not None and yoy_col_idx < len(aligned_row) else None
                previous_value = self._safe_matrix_value(aligned_row, previous_col_idx)
            self._record_extracted_field(result, target_field, current_value, yoy_raw_value, previous_value)

        return result

    def _realign_row_for_missing_note_column(self, row: List[str], headers: List[str]) -> List[str]:
        if len(row) != len(headers) or len(row) < 4:
            return row
        roles = self._build_column_roles(headers)
        note_cols = [idx for idx in range(1, len(headers)) if roles.get(idx) == "note"]
        if len(note_cols) != 1:
            return row
        note_idx = note_cols[0]
        if note_idx != 1:
            return row
        if not self._looks_like_amount_text(row[1]) or not self._looks_like_amount_text(row[2]):
            return row
        # 常见于“附注”列整列缺失：把金额列整体右移一格，对齐到表头。
        return [row[0], ""] + row[1:-1]

    def _infer_broken_income_field(self, rows: List[List[str]], row_idx: int) -> Optional[str]:
        row = rows[row_idx] if 0 <= row_idx < len(rows) else []
        if not row or not self._looks_like_amount_text(row[0]):
            return None
        prev_row = rows[row_idx - 1] if row_idx > 0 else []
        next_row = rows[row_idx + 1] if row_idx + 1 < len(rows) else []
        prev_label = self._normalize_label_for_match(prev_row[0]) if prev_row else ""
        next_label = self._normalize_label_for_match(next_row[0]) if next_row else ""
        trailing_text = "".join(str(cell or "") for cell in row[2:])
        normalized_trailing = self._normalize_label_for_match(trailing_text)
        if (
            "营业外支出" in prev_label
            and "所得税费用" in next_label
            and "填列" in normalized_trailing
            and len(row) >= 2
            and self._looks_like_amount_text(row[1])
        ):
            return "total_profit"
        return None

    def _flatten_header(self, col: Any) -> str:
        if isinstance(col, tuple):
            return " ".join(str(x) for x in col if str(x).strip()).strip()
        return str(col).strip()

    def _build_column_roles(self, headers: List[str]) -> Dict[int, str]:
        return {idx: self._classify_header_role(header) for idx, header in enumerate(headers)}

    def _classify_header_role(self, header: str) -> str:
        normalized = re.sub(r"\s+", "", str(header or ""))
        if not normalized:
            return "unknown"
        if any(keyword in normalized.lower() for keyword in ["note", "附注", "注释"]):
            return "note"
        if any(keyword in normalized for keyword in ["同比", "增减", "增长率", "本年比上年增减"]):
            return "yoy"
        current_keywords = [
            "本报告期", "本期", "本年", "期末", "期末余额", "本期发生额", "本年累计", "本报告期末",
        ]
        previous_keywords = [
            "上年同期", "上期", "上年", "上期期末", "上年末", "年初", "期初", "期初余额", "上年同期金额",
        ]
        if any(keyword in normalized for keyword in current_keywords):
            return "current"
        if any(keyword in normalized for keyword in previous_keywords):
            return "previous"
        year_hits = [int(year) for year in re.findall(r"20\d{2}", normalized)]
        if year_hits:
            if len(year_hits) >= 2:
                return "value"
            if any(keyword in normalized for keyword in ["期末", "本期", "本年", "本报告期"]):
                return "current"
            if any(keyword in normalized for keyword in ["上年", "上期", "同期", "期初", "年初"]):
                return "previous"
            return "value"
        if re.search(r"(第?[一二三四1-4][季度季]|q[1-4])", normalized, re.IGNORECASE):
            return "quarter"
        return "value"

    def _pick_current_value_col(self, headers: List[str]) -> Optional[int]:
        if len(headers) <= 1:
            return None
        roles = self._build_column_roles(headers)
        for idx in range(1, len(headers)):
            if roles.get(idx) == "current":
                return idx

        year_cols = []
        for idx in range(1, len(headers)):
            header = str(headers[idx] or "")
            years = re.findall(r'20\d{2}', header)
            if years:
                year_cols.append((idx, int(years[0]), header))
        
        if year_cols:
            year_cols.sort(key=lambda x: x[1], reverse=True)
            return year_cols[0][0]

        for idx in range(1, len(headers)):
            if roles.get(idx) in {"note", "yoy", "quarter", "previous"}:
                continue
            return idx
        return 1

    def _looks_like_amount_text(self, value: Any) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        if re.search(r"-?\d[\d,]*\.\d+", text):
            return True
        cleaned = re.sub(r"[^\d\-]", "", text)
        if not cleaned or cleaned in {"-", ""}:
            return False
        if len(cleaned.replace("-", "")) >= 6:
            return True
        return False

    def _refine_statement_current_col(self, df: pd.DataFrame, headers: List[str], current_col_idx: Optional[int]) -> Optional[int]:
        if current_col_idx is None or len(headers) <= 1:
            return current_col_idx
        header = headers[current_col_idx] if current_col_idx < len(headers) else ""
        if self._classify_header_role(header) == "current":
            return current_col_idx

        roles = self._build_column_roles(headers)
        has_semantic_headers = any(
            roles.get(idx) in {"current", "previous", "note", "yoy"}
            for idx in range(1, len(headers))
        )
        if not has_semantic_headers:
            return current_col_idx

        best_idx = current_col_idx
        best_score = -1
        for idx in range(1, len(headers)):
            col_values = df.iloc[:, idx].astype(str).tolist()
            score = sum(1 for value in col_values[:20] if self._looks_like_amount_text(value))
            role = roles.get(idx)
            if role == "current":
                score += 20
            elif role == "previous":
                score -= 10
            elif role in {"note", "yoy", "quarter"}:
                score -= 100
            if score > best_score:
                best_score = score
                best_idx = idx
        return best_idx

    def _pick_yoy_col(self, headers: List[str]) -> Optional[int]:
        roles = self._build_column_roles(headers)
        for idx in range(1, len(headers)):
            if roles.get(idx) == "yoy":
                return idx
        return None

    # ── 单位换算（代码层）────────────────────────────────────────────────────

    def _round_intermediate(self, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        return round(float(value), INTERMEDIATE_DECIMALS)

    def _apply_storage_precision(self, data: Dict[str, Any]) -> Dict[str, Any]:
        rounded: Dict[str, Any] = {}
        bounded_storage_fields = {
            "eps": 999999.9999,
            "roe": 999999.9999,
            "roe_weighted_excl_non_recurring": 999999.9999,
            "gross_profit_margin": 999999.9999,
            "net_profit_margin": 999999.9999,
            "operating_revenue_yoy_growth": 999999.9999,
            "operating_revenue_qoq_growth": 999999.9999,
            "net_profit_yoy_growth": 999999.9999,
            "net_profit_qoq_growth": 999999.9999,
            "net_profit_excl_non_recurring_yoy": 999999.9999,
            "asset_total_assets_yoy_growth": 999999.9999,
            "liability_total_liabilities_yoy_growth": 999999.9999,
            "net_cash_flow_yoy_growth": 999999.9999,
            "operating_cf_ratio_of_net_cf": 999999.9999,
            "investing_cf_ratio_of_net_cf": 999999.9999,
            "financing_cf_ratio_of_net_cf": 999999.9999,
            "net_asset_per_share": 999999.9999,
            "operating_cf_per_share": 999999.9999,
        }
        for key, value in data.items():
            if key.startswith("_") or isinstance(value, bool) or not isinstance(value, (int, float)):
                rounded[key] = value
                continue
            bound = bounded_storage_fields.get(key)
            if bound is not None and abs(float(value)) > bound:
                rounded[key] = None
                continue
            precision = STORAGE_PRECISION_FIELDS.get(key, 2)
            rounded[key] = round(float(value), precision)
        return rounded

    def _apply_unit_conversion(self, data: Dict[str, Any], unit_multiplier: float, context_text: str = "") -> Dict[str, Any]:
        """将 LLM 返回的原始数值（按文档单位）统一换算为万元。"""
        result: Dict[str, Any] = {}
        detected_from_chunk = self._detect_unit_in_text(context_text[:1200]) or unit_multiplier
        for k, v in data.items():
            if isinstance(v, (int, float)) and k not in RATIO_FIELDS:
                numeric = float(v)
                effective_multiplier = detected_from_chunk
                if effective_multiplier == 1.0 and abs(numeric) >= 1e7:
                    effective_multiplier = 0.0001
                result[k] = self._round_intermediate(numeric * effective_multiplier)
            else:
                result[k] = v
        return result

    def _normalize_field_aliases(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """处理 LLM 可能返回的字段别名，映射到数据库实际字段名。"""
        alias_map = {
            "total_assets": "asset_total_assets",
            "total_liabilities": "liability_total_liabilities",
            "total_equity": "equity_total_equity",
            "net_profit_excl": "net_profit_excl_non_recurring",
            "cash_and_cash_equivalents": "asset_cash_and_cash_equivalents",
            "accounts_receivable": "asset_accounts_receivable",
            "inventory": "asset_inventory",
            "trading_financial_assets": "asset_trading_financial_assets",
            "construction_in_progress": "asset_construction_in_progress",
            "accounts_payable": "liability_accounts_payable",
            "advance_from_customers": "liability_advance_from_customers",
            "contract_liabilities": "liability_contract_liabilities",
            "short_term_loans": "liability_short_term_loans",
            "unappropriated_profit": "equity_unappropriated_profit",
            "paid_in_capital": "share_capital",
            "capital_stock": "share_capital",
            "share_capital_total": "share_capital",
            "cost_of_sales": "operating_expense_cost_of_sales",
            "selling_expenses": "operating_expense_selling_expenses",
            "administrative_expenses": "operating_expense_administrative_expenses",
            "financial_expenses": "operating_expense_financial_expenses",
            "rnd_expenses": "operating_expense_rnd_expenses",
            "taxes_and_surcharges": "operating_expense_taxes_and_surcharges",
            "cash_from_sales": "operating_cf_cash_from_sales",
            "cash_for_investments": "investing_cf_cash_for_investments",
            "cash_from_investment_recovery": "investing_cf_cash_from_investment_recovery",
            "cash_from_borrowing": "financing_cf_cash_from_borrowing",
            "cash_for_debt_repayment": "financing_cf_cash_for_debt_repayment",
        }
        normalized: Dict[str, Any] = {}
        for k, v in data.items():
            key = alias_map.get(k, k)
            normalized[key] = self._to_number(v)

        # net_profit 同步写入 net_profit_10k_yuan（核心业绩指标表字段）
        if "net_profit" in normalized and "net_profit_10k_yuan" not in normalized:
            normalized["net_profit_10k_yuan"] = normalized["net_profit"]

        return {k: v for k, v in normalized.items() if v is not None}

    def _to_number(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        if not text or text.lower() in {"nan", "none", "null", "-", "—"}:
            return None
        text = text.replace(",", "").replace("，", "")
        text = re.sub(r"[^\d.\-]", "", text)
        if text in {"", "-", ".", "-."}:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _is_zero_like_missing(self, field: str, value: Any) -> bool:
        numeric = self._to_number(value)
        if numeric is None:
            return False
        return field in ZERO_AS_MISSING_FIELDS and abs(numeric) < 1e-8

    def _should_prefer_incoming_value(self, field: str, existing: Any, incoming: Any) -> bool:
        existing_num = self._to_number(existing)
        incoming_num = self._to_number(incoming)
        if incoming_num is None:
            return False
        if existing_num is None:
            return True
        if self._is_zero_like_missing(field, existing) and abs(incoming_num) > 1e-8:
            return True
        lower_bound = SUSPICIOUS_SMALL_FIELDS.get(field)
        if lower_bound is not None and abs(existing_num) < lower_bound <= abs(incoming_num):
            return True
        return False

    def _is_plausible_field_value(self, field: str, value: Any) -> bool:
        numeric = self._to_number(value)
        if numeric is None:
            return False
        if field in SUSPICIOUS_SMALL_FIELDS and abs(numeric) < SUSPICIOUS_SMALL_FIELDS[field]:
            return False
        if field == "asset_liability_ratio" and not (0 <= numeric <= 100):
            return False
        bounded_fields = {
            "eps": 20.0,
            "roe": 100.0,
            "roe_weighted_excl_non_recurring": 100.0,
            "gross_profit_margin": 100.0,
            "net_profit_margin": 100.0,
            "operating_revenue_yoy_growth": 1000.0,
            "net_profit_yoy_growth": 1000.0,
            "net_profit_excl_non_recurring_yoy": 1000.0,
            "net_asset_per_share": 1000.0,
            "operating_cf_per_share": 1000.0,
        }
        bound = bounded_fields.get(field)
        if bound is not None and abs(numeric) > bound:
            return False
        return True

    def _allowed_fields_for_table_type(self, table_type: str) -> Optional[set]:
        if table_type == "primary_balance":
            return set(BALANCE_SOURCE_FIELDS)
        if table_type == "primary_income":
            return set(INCOME_SOURCE_FIELDS)
        if table_type == "primary_cashflow":
            return set(CASHFLOW_SOURCE_FIELDS)
        if table_type == "core_metrics":
            return set(CORE_SOURCE_FIELDS)
        if table_type == "quarter_summary":
            return set(QUARTER_SOURCE_FIELDS)
        if table_type in {"financial_other", "supporting_financial"}:
            return set().union(
                BALANCE_SOURCE_FIELDS,
                INCOME_SOURCE_FIELDS,
                CASHFLOW_SOURCE_FIELDS,
                CORE_SOURCE_FIELDS,
                QUARTER_SOURCE_FIELDS,
            )
        return None

    def _filter_fields_by_table_type(self, data: Dict[str, Any], table_type: str) -> Dict[str, Any]:
        allowed_fields = self._allowed_fields_for_table_type(table_type)
        if allowed_fields is None:
            return dict(data)
        if not allowed_fields:
            return {}
        return {
            key: value for key, value in data.items()
            if key in allowed_fields or key.startswith("_")
        }

    def _coalesce_preferred(self, data: Dict[str, Any], field: str, *candidates: Any) -> None:
        current = data.get(field)
        if self._is_plausible_field_value(field, current):
            return
        for candidate in candidates:
            if self._is_plausible_field_value(field, candidate):
                data[field] = candidate
                return

    def _field_preferred_source_types(self, field: str) -> List[str]:
        if field in PRIMARY_STATEMENT_PRIORITY_FIELDS:
            if field in BALANCE_SOURCE_FIELDS:
                return ["primary_balance"]
            if field in INCOME_SOURCE_FIELDS:
                return ["primary_income"]
            if field in CASHFLOW_SOURCE_FIELDS:
                return ["primary_cashflow"]
        if field in CORE_METRIC_PRIORITY_FIELDS:
            return ["core_metrics"]
        if field in QUARTER_SOURCE_FIELDS:
            return ["quarter_summary"]
        return []

    def _field_expected_table_type(self, field: str) -> Optional[str]:
        preferred_types = self._field_preferred_source_types(field)
        if preferred_types:
            return preferred_types[0]
        if field in {"net_profit", "total_operating_revenue", "operating_profit", "total_profit"}:
            return "primary_income"
        if field in {
            "asset_total_assets",
            "asset_cash_and_cash_equivalents",
            "asset_accounts_receivable",
            "asset_inventory",
            "liability_total_liabilities",
            "liability_accounts_payable",
            "liability_contract_liabilities",
            "liability_short_term_loans",
            "equity_total_equity",
            "equity_parent_attributable",
            "equity_minority_interest",
            "share_capital",
        }:
            return "primary_balance"
        if field in {"net_cash_flow"}:
            return "primary_cashflow"
        if field in {"net_profit_10k_yuan", "eps", "roe", "net_asset_per_share", "operating_cf_per_share"}:
            return "core_metrics"
        return None

    def _build_field_source_meta(
        self,
        field: str,
        value: Any,
        chunk: Dict[str, Any],
        chunk_idx: int,
        extraction_mode: str,
    ) -> Dict[str, Any]:
        table_type = str(chunk.get("table_type") or "")
        text = str(chunk.get("text", ""))
        expected_type = self._field_expected_table_type(field)
        preferred_types = self._field_preferred_source_types(field)

        score = 0
        mode_score = {
            "rules": 40,
            "rules_retry": 35,
            "rules+llm": 25,
            "llm": 5,
            "rejected": -100,
        }
        score += mode_score.get(extraction_mode, 0)

        table_score = {
            "primary_income": 80,
            "primary_balance": 80,
            "primary_cashflow": 80,
            "core_metrics": 45,
            "quarter_summary": 20,
            "financial_other": 10,
            "supporting_financial": 12,
        }
        score += table_score.get(table_type, 0)

        if chunk.get("is_combined") is True:
            score += 20
        elif chunk.get("is_combined") is False:
            score -= 20

        if expected_type and table_type == expected_type:
            score += 50
        elif preferred_types:
            if table_type in preferred_types:
                score += 35
            elif table_type in PRIMARY_TABLE_TYPES or table_type in {"core_metrics", "quarter_summary"}:
                score -= 15

        if field in {"net_profit", "total_operating_revenue", "operating_profit", "total_profit"}:
            if "合并利润表" in text:
                score += 40
            elif "利润表" in text:
                score += 20
            if "未分配利润" in text or "补充资料" in text:
                score -= 20
        elif field in {
            "asset_total_assets",
            "asset_cash_and_cash_equivalents",
            "asset_accounts_receivable",
            "asset_inventory",
            "liability_total_liabilities",
            "liability_accounts_payable",
            "liability_contract_liabilities",
            "liability_short_term_loans",
            "equity_total_equity",
            "equity_parent_attributable",
            "equity_minority_interest",
        }:
            if "合并资产负债表" in text:
                score += 40
            elif "资产负债表" in text:
                score += 20
            if "主要会计数据" in text or "主要财务指标" in text:
                score -= 20
        elif field == "share_capital":
            if "实收资本" in text or "股本" in text:
                score += 60
            if "所有者权益" in text or "资产负债表" in text:
                score += 20
        elif field in {"operating_cf_net_amount", "investing_cf_net_amount", "financing_cf_net_amount", "net_cash_flow"}:
            if "合并现金流量表" in text:
                score += 40
            elif "现金流量表" in text:
                score += 20
            if "补充资料" in text:
                score -= 15
            if table_type == "supporting_financial":
                score -= 35
            if extraction_mode == "llm":
                score -= 20
        elif field in {"eps", "roe", "net_asset_per_share", "operating_cf_per_share", "net_profit_10k_yuan"}:
            if "主要会计数据" in text or "主要财务指标" in text:
                score += 35
            if "净资产收益率及每股收益" in text:
                score += 50
            if "合并利润表" in text or "合并资产负债表" in text or "合并现金流量表" in text:
                score -= 20
            if extraction_mode == "llm":
                score -= 15
        elif field == "roe_weighted_excl_non_recurring":
            if "主要会计数据" in text or "主要财务指标" in text:
                score += 35
            if "净资产收益率及每股收益" in text:
                score += 55
            if extraction_mode == "llm":
                score -= 15
        elif field == "net_profit_excl_non_recurring":
            if "主要会计数据" in text or "主要财务指标" in text:
                score += 40
            if "合并利润表" in text:
                score += 15
            if "变动原因" in text or "变动幅度" in text:
                score -= 15
            if extraction_mode == "llm":
                score -= 15
        elif field in {"operating_revenue_yoy_growth", "net_profit_yoy_growth", "net_profit_excl_non_recurring_yoy"}:
            if "主要会计数据" in text or "主要财务指标" in text:
                score += 45
            if "合并利润表" in text:
                score -= 10
        elif field in {"asset_total_assets_yoy_growth", "liability_total_liabilities_yoy_growth"}:
            if "主要会计数据" in text or "主要财务指标" in text:
                score += 40
            if "合并资产负债表" in text:
                score -= 5

        if not self._is_plausible_field_value(field, value):
            score -= 80

        return {
            "score": score,
            "table_type": table_type,
            "preferred_types": preferred_types,
            "extraction_mode": extraction_mode,
            "is_combined": chunk.get("is_combined"),
            "chunk_idx": chunk_idx,
        }

    def _annotate_field_sources(
        self,
        data: Dict[str, Any],
        chunk: Dict[str, Any],
        chunk_idx: int,
        extraction_mode: str,
    ) -> Dict[str, Any]:
        annotated = dict(data)
        field_sources: Dict[str, Any] = {}
        for field, value in data.items():
            if field.startswith("_"):
                continue
            field_sources[field] = self._build_field_source_meta(field, value, chunk, chunk_idx, extraction_mode)
        if field_sources:
            annotated["_field_sources"] = field_sources
        return annotated

    def _should_use_incoming_candidate(
        self,
        field: str,
        existing_value: Any,
        incoming_value: Any,
        existing_meta: Optional[Dict[str, Any]],
        incoming_meta: Optional[Dict[str, Any]],
    ) -> bool:
        llm_sensitive_fields = {
            "net_profit_excl_non_recurring",
            "operating_cf_net_amount",
            "investing_cf_net_amount",
            "financing_cf_net_amount",
            "share_capital",
            "net_asset_per_share",
            "operating_cf_per_share",
            "roe_weighted_excl_non_recurring",
        }
        existing_mode = str((existing_meta or {}).get("extraction_mode") or "")
        incoming_mode = str((incoming_meta or {}).get("extraction_mode") or "")
        if field in llm_sensitive_fields:
            if incoming_mode == "llm" and existing_mode != "llm":
                return False
            if existing_mode == "llm" and incoming_mode != "llm":
                return True

        existing_plausible = self._is_plausible_field_value(field, existing_value)
        incoming_plausible = self._is_plausible_field_value(field, incoming_value)

        if incoming_plausible and not existing_plausible:
            return True
        if existing_plausible and not incoming_plausible:
            return False

        existing_score = (existing_meta or {}).get("score", 0)
        incoming_score = (incoming_meta or {}).get("score", 0)
        if incoming_score != existing_score:
            return incoming_score > existing_score

        return self._should_prefer_incoming_value(field, existing_value, incoming_value)

    def _sanitize_extracted_values(self, data: Dict[str, Any]) -> Dict[str, Any]:
        sanitized = dict(data)
        for field in ZERO_AS_MISSING_FIELDS:
            if field in sanitized and self._is_zero_like_missing(field, sanitized.get(field)):
                sanitized[field] = None

        bounded_fields = {
            "eps": 20.0,
            "roe": 100.0,
            "roe_weighted_excl_non_recurring": 100.0,
            "gross_profit_margin": 100.0,
            "net_profit_margin": 100.0,
            "operating_revenue_yoy_growth": 1000.0,
            "net_profit_yoy_growth": 1000.0,
            "net_profit_excl_non_recurring_yoy": 1000.0,
            "net_asset_per_share": 1000.0,
            "operating_cf_per_share": 1000.0,
        }
        for field, bound in bounded_fields.items():
            value = self._to_number(sanitized.get(field))
            if value is not None and abs(value) > bound:
                sanitized[field] = None

        for field in SUSPICIOUS_SMALL_FIELDS:
            value = self._to_number(sanitized.get(field))
            if value is not None and abs(value) < SUSPICIOUS_SMALL_FIELDS[field]:
                sanitized[field] = None

        if not self._is_plausible_field_value("asset_liability_ratio", sanitized.get("asset_liability_ratio")):
            sanitized["asset_liability_ratio"] = None

        assets = self._to_number(sanitized.get("asset_total_assets"))
        liabilities = self._to_number(sanitized.get("liability_total_liabilities"))
        equity = self._to_number(sanitized.get("equity_total_equity"))

        asset_components = [
            self._to_number(sanitized.get("asset_cash_and_cash_equivalents")),
            self._to_number(sanitized.get("asset_accounts_receivable")),
            self._to_number(sanitized.get("asset_inventory")),
            self._to_number(sanitized.get("asset_trading_financial_assets")),
            self._to_number(sanitized.get("asset_construction_in_progress")),
        ]
        liability_components = [
            self._to_number(sanitized.get("liability_accounts_payable")),
            self._to_number(sanitized.get("liability_contract_liabilities")),
            self._to_number(sanitized.get("liability_short_term_loans")),
        ]

        max_asset_component = max((v for v in asset_components if v is not None), default=None)
        max_liability_component = max((v for v in liability_components if v is not None), default=None)

        if assets is not None and max_asset_component is not None and assets < max_asset_component:
            sanitized["asset_total_assets"] = None
            assets = None

        if liabilities is not None and max_liability_component is not None and liabilities < max_liability_component:
            sanitized["liability_total_liabilities"] = None
            liabilities = None

        # 对大体量公司的高风险资产明细做额外清洗，避免摘要表/错列小值污染主表。
        large_scale_assets = assets is not None and abs(assets) >= 500000
        for field in ["asset_cash_and_cash_equivalents", "asset_accounts_receivable", "asset_inventory"]:
            value = self._to_number(sanitized.get(field))
            if value is None or not large_scale_assets:
                continue
            if 0 < abs(value) < 30000 and abs(value) < abs(assets) * 0.02:
                sanitized[field] = None

        ratio = self._to_number(sanitized.get("asset_liability_ratio"))
        if ratio is None and assets not in (None, 0) and liabilities is not None:
            calc_ratio = self._round_intermediate(liabilities / abs(assets) * 100)
            sanitized["asset_liability_ratio"] = calc_ratio

        if assets is not None and liabilities is not None and equity is not None:
            implied_equity = self._round_intermediate(assets - liabilities)
            if abs(equity - implied_equity) > max(abs(assets) * 0.05, 1000):
                sanitized["equity_total_equity"] = implied_equity

        placeholder_fields = [
            "asset_cash_and_cash_equivalents", "asset_accounts_receivable", "asset_inventory",
            "liability_total_liabilities", "net_cash_flow", "net_profit", "net_profit_10k_yuan",
        ]
        rounded_hits = 0
        for field in placeholder_fields:
            value = self._to_number(sanitized.get(field))
            if value is None:
                continue
            if abs(value) >= 10000 and abs(value) % 10000 == 0:
                rounded_hits += 1
        if bool(sanitized.get("source_is_summary")) and rounded_hits >= 3:
            sanitized["_summary_placeholder_like"] = 1

        return sanitized

    # ── 合并策略（last-wins，合并报表优先由调用方保证）────────────────────────

    def _merge_last_wins(self, base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(base)
        merged_sources = dict(base.get("_field_sources") or {})
        incoming_sources = dict(incoming.get("_field_sources") or {})
        for k, v in incoming.items():
            if k == "_field_sources":
                continue
            if v is not None:
                if self._is_zero_like_missing(k, v):
                    continue
                if merged.get(k) is not None and (k in FIRST_WINS_FIELDS or k in SOURCE_SCORED_FIELDS):
                    if not self._should_use_incoming_candidate(
                        k,
                        merged.get(k),
                        v,
                        merged_sources.get(k),
                        incoming_sources.get(k),
                    ):
                        continue
                merged[k] = v
                if k in incoming_sources:
                    merged_sources[k] = incoming_sources[k]
        if merged_sources:
            merged["_field_sources"] = merged_sources
        return merged

    def _is_traceable_business_field(self, key: str) -> bool:
        if not key or key.startswith("_"):
            return False
        if key in {
            "stock_code", "stock_abbr", "report_year", "report_period",
            "source_is_summary", "has_anomaly", "anomaly_flags",
            "status", "message", "created_at", "updated_at", "serial_number",
        }:
            return False
        return True

    def _run_validation_pipeline(self, data: Dict[str, Any]) -> Dict[str, Any]:
        accepted = self._sanitize_extracted_values(data)
        accepted = self._invalidate_formula_outliers(accepted)
        accepted = self._enforce_qoq_field_scope(accepted)
        rejected_fields = sorted(
            key
            for key in data.keys()
            if self._is_traceable_business_field(key)
            and data.get(key) is not None
            and accepted.get(key) is None
        )

        derived = self._derive_metrics(accepted)
        derived_fields = sorted(
            key
            for key in derived.keys()
            if self._is_traceable_business_field(key)
            and derived.get(key) is not None
            and accepted.get(key) != derived.get(key)
        )
        accepted_fields = sorted(
            key
            for key in accepted.keys()
            if self._is_traceable_business_field(key)
            and accepted.get(key) is not None
            and key not in derived_fields
        )

        final = self._annotate_financial_anomalies(derived)
        final["_accepted_fields"] = accepted_fields
        final["_derived_fields"] = derived_fields
        final["_rejected_fields"] = rejected_fields
        return final

    def _build_pre_save_review(self, data: Dict[str, Any]) -> Dict[str, Any]:
        blockers: List[str] = []
        warnings: List[str] = []

        completeness_flags = list(data.get("_completeness_flags") or [])
        anomaly_flags = list(data.get("anomaly_flags") or [])
        rejected_fields = list(data.get("_rejected_fields") or [])

        if bool(data.get("_summary_placeholder_like")):
            warnings.append("summary_placeholder_like")

        critical_completeness = [flag for flag in completeness_flags if "critical_missing" in flag]
        incomplete_completeness = [flag for flag in completeness_flags if "incomplete" in flag]
        if critical_completeness:
            warnings.extend(critical_completeness)
        if incomplete_completeness:
            warnings.extend(incomplete_completeness)

        severe_anomaly_markers = [
            "balance_sheet_broken",
            "cashflow_reconciliation_broken",
        ]
        severe_anomalies = [
            flag for flag in anomaly_flags
            if any(marker in flag for marker in severe_anomaly_markers)
        ]
        mild_anomalies = [flag for flag in anomaly_flags if flag not in severe_anomalies]
        if severe_anomalies:
            warnings.extend(severe_anomalies)
        if mild_anomalies:
            warnings.extend(mild_anomalies)

        core_fields = [
            "total_operating_revenue",
            "net_profit",
            "operating_cf_net_amount",
            "asset_total_assets",
            "liability_total_liabilities",
        ]
        core_present = [
            field for field in core_fields
            if self._to_number(data.get(field)) is not None
        ]
        if not core_present:
            blockers.append("no_core_financial_fields")

        if len(rejected_fields) >= 8:
            warnings.append(f"many_rejected_fields({len(rejected_fields)})")

        review_status = "block" if blockers else ("warn" if warnings else "pass")
        return {
            "status": review_status,
            "blockers": blockers,
            "warnings": warnings,
            "core_present_fields": core_present,
            "rejected_field_count": len(rejected_fields),
        }

    def _attach_pre_save_review(self, data: Dict[str, Any]) -> Dict[str, Any]:
        reviewed = dict(data)
        review = self._build_pre_save_review(reviewed)
        reviewed["_pre_save_review"] = review
        reviewed["_pre_save_review_status"] = review["status"]
        reviewed["_pre_save_review_blockers"] = list(review.get("blockers") or [])
        reviewed["_pre_save_review_warnings"] = list(review.get("warnings") or [])
        reviewed["_requires_manual_review"] = 1 if review["status"] in {"warn", "block"} else 0
        return reviewed

    def _invalidate_formula_outliers(self, data: Dict[str, Any]) -> Dict[str, Any]:
        cleaned = dict(data)

        revenue = self._to_number(cleaned.get("total_operating_revenue"))
        cost = self._to_number(cleaned.get("operating_expense_cost_of_sales"))
        if revenue not in (None, 0) and cost is not None:
            calc_gpm = self._round_intermediate((revenue - cost) / revenue * 100)
            current_gpm = self._to_number(cleaned.get("gross_profit_margin"))
            if current_gpm is not None and abs(current_gpm - calc_gpm) > 5.0:
                cleaned["gross_profit_margin"] = None

        net_cash_flow = self._to_number(cleaned.get("net_cash_flow"))
        operating_cf = self._to_number(cleaned.get("operating_cf_net_amount"))
        investing_cf = self._to_number(cleaned.get("investing_cf_net_amount"))
        financing_cf = self._to_number(cleaned.get("financing_cf_net_amount"))
        if net_cash_flow not in (None, 0):
            for field_name, component in [
                ("operating_cf_ratio_of_net_cf", operating_cf),
                ("investing_cf_ratio_of_net_cf", investing_cf),
                ("financing_cf_ratio_of_net_cf", financing_cf),
            ]:
                current = self._to_number(cleaned.get(field_name))
                if current is None or component is None:
                    continue
                calc_ratio = self._round_intermediate(component / net_cash_flow * 100)
                if abs(current - calc_ratio) > 1.0:
                    cleaned[field_name] = None

        share_capital = self._to_number(cleaned.get("share_capital"))
        parent_equity = self._to_number(cleaned.get("equity_parent_attributable"))
        net_asset_per_share = self._to_number(cleaned.get("net_asset_per_share"))
        operating_cf_per_share = self._to_number(cleaned.get("operating_cf_per_share"))

        if share_capital not in (None, 0) and parent_equity is not None and net_asset_per_share not in (None, 0):
            calc_nav_per_share = self._round_intermediate(parent_equity * 10000 / share_capital)
            if abs(net_asset_per_share - calc_nav_per_share) > max(abs(calc_nav_per_share) * 0.5, 1.0):
                cleaned["net_asset_per_share"] = None

        if share_capital not in (None, 0) and operating_cf is not None and operating_cf_per_share not in (None, 0):
            calc_ocf_per_share = self._round_intermediate(operating_cf * 10000 / share_capital)
            if abs(operating_cf_per_share - calc_ocf_per_share) > max(abs(calc_ocf_per_share) * 0.5, 0.5):
                cleaned["operating_cf_per_share"] = None

        return cleaned

    def _enforce_qoq_field_scope(self, data: Dict[str, Any]) -> Dict[str, Any]:
        scoped = dict(data)
        report_period = str(scoped.get("report_period") or "")
        quarter_fields = ["operating_revenue_qoq_growth", "net_profit_qoq_growth"]

        # Q1/HY/Q3 报告通常不直接披露可用的“单季度 vs 上一季度”环比结果；
        # 这三类口径统一留给跨文件回填阶段处理。
        if report_period in {"Q1", "HY", "Q3"}:
            for field in quarter_fields:
                scoped[field] = None
            return scoped

        # FY 仅允许来自“分季度主要财务数据”抽取的季度环比，禁止累计口径/其他表块兜底。
        if report_period == "FY":
            field_sources = dict(scoped.get("_field_sources") or {})
            for field in quarter_fields:
                meta = field_sources.get(field) or {}
                if meta.get("table_type") != "quarter_summary":
                    scoped[field] = None
            return scoped

        return scoped

    # ── 后处理与入库 ───────────────────────────────────────────────────────────

    def _post_process(self, data: Dict[str, Any], meta: Dict[str, Any], save_to_db: bool = True) -> Dict[str, Any]:
        final = meta.copy()
        final.update(data)
        final = self._run_validation_pipeline(final)
        final = self._attach_pre_save_review(final)
        validation = self._validate_before_save(final)
        if not validation["ok"]:
            logger.error(f"[ETL] Validation failed: {validation['message']}")
            return {"status": "error", "message": validation["message"], "data": final}
        if save_to_db:
            try:
                self._save_to_db(final)
            except Exception as e:
                logger.error(f"[ETL] DB write failed: {e}")
                return {"status": "error", "message": f"DB write failed: {e}", "data": final}
        return {"status": "success", "data": final}

    def _derive_metrics(self, data: Dict[str, Any]) -> Dict[str, Any]:
        derived = dict(data)

        revenue = self._to_number(derived.get("total_operating_revenue"))
        net_profit = self._to_number(derived.get("net_profit"))
        parent_net_profit = self._to_number(derived.get("parent_net_profit"))
        cost = self._to_number(derived.get("operating_expense_cost_of_sales"))
        assets = self._to_number(derived.get("asset_total_assets"))
        liabilities = self._to_number(derived.get("liability_total_liabilities"))
        equity = self._to_number(derived.get("equity_total_equity"))
        parent_equity = self._to_number(derived.get("equity_parent_attributable"))
        minority_interest = self._to_number(derived.get("equity_minority_interest"))
        unappropriated_profit = self._to_number(derived.get("equity_unappropriated_profit"))
        share_capital = self._to_number(derived.get("share_capital"))
        operating_cf = self._to_number(derived.get("operating_cf_net_amount"))
        investing_cf = self._to_number(derived.get("investing_cf_net_amount"))
        financing_cf = self._to_number(derived.get("financing_cf_net_amount"))
        net_cash_flow = self._to_number(derived.get("net_cash_flow"))
        eps = self._to_number(derived.get("eps"))
        source_is_summary = bool(derived.get("source_is_summary"))
        operating_profit = self._to_number(derived.get("operating_profit"))
        total_profit = self._to_number(derived.get("total_profit"))
        roe = self._to_number(derived.get("roe"))
        net_profit_margin = self._to_number(derived.get("net_profit_margin"))
        report_period = str(derived.get("report_period") or "")

        if parent_net_profit is not None:
            derived["net_profit"] = parent_net_profit
            net_profit = parent_net_profit

        self._backfill_statement_growth_from_original_row(derived, "asset_total_assets", assets)
        self._backfill_statement_growth_from_original_row(derived, "liability_total_liabilities", liabilities)
        self._backfill_statement_growth_from_original_row(derived, "net_cash_flow", net_cash_flow)
        self._backfill_statement_growth_from_original_row(derived, "total_operating_revenue", revenue)
        self._backfill_statement_growth_from_original_row(derived, "net_profit", net_profit)

        if derived.get("net_profit_margin") is None:
            if revenue not in (None, 0) and net_profit is not None:
                derived["net_profit_margin"] = self._round_intermediate(net_profit / revenue * 100)

        if derived.get("gross_profit_margin") is None:
            if revenue not in (None, 0) and cost is not None:
                derived["gross_profit_margin"] = self._round_intermediate((revenue - cost) / revenue * 100)

        liability_components = [
            self._to_number(derived.get("liability_accounts_payable")),
            self._to_number(derived.get("liability_contract_liabilities")),
            self._to_number(derived.get("liability_short_term_loans")),
        ]
        max_liability_component = max((v for v in liability_components if v is not None), default=None)
        ratio = self._to_number(derived.get("asset_liability_ratio"))
        implied_liabilities = None
        if assets is not None and equity is not None:
            implied_liabilities = self._round_intermediate(assets - equity)
        ratio_liabilities = None
        if assets not in (None, 0) and ratio is not None and 0 <= ratio <= 100:
            ratio_liabilities = self._round_intermediate(assets * ratio / 100)

        liabilities_invalid = False
        if liabilities is None:
            liabilities_invalid = True
        elif max_liability_component is not None and liabilities < max_liability_component:
            liabilities_invalid = True
        elif assets not in (None, 0) and liabilities > assets * 1.05:
            liabilities_invalid = True

        if liabilities_invalid:
            replacement = None
            for candidate in [implied_liabilities, ratio_liabilities]:
                if candidate is None:
                    continue
                if candidate < 0:
                    continue
                if max_liability_component is not None and candidate < max_liability_component:
                    continue
                replacement = candidate
                break
            if replacement is not None:
                derived["liability_total_liabilities"] = replacement
                liabilities = replacement

        if assets is None and liabilities is not None and equity is not None:
            derived["asset_total_assets"] = self._round_intermediate(liabilities + equity)
            assets = derived["asset_total_assets"]

        if derived.get("asset_liability_ratio") is None and assets not in (None, 0) and liabilities is not None:
            derived["asset_liability_ratio"] = self._round_intermediate(liabilities / abs(assets) * 100)

        if assets is not None and liabilities is not None:
            implied_equity = self._round_intermediate(assets - liabilities)
            equity_invalid = (
                equity is None
                or (unappropriated_profit is not None and equity < unappropriated_profit)
                or abs((equity or 0) - implied_equity) > max(abs(assets) * 0.05, 1000)
            )
            if equity_invalid:
                derived["equity_total_equity"] = implied_equity
                equity = implied_equity

        if parent_equity is None and equity is not None and minority_interest is not None:
            derived["equity_parent_attributable"] = self._round_intermediate(equity - minority_interest)
            parent_equity = self._to_number(derived.get("equity_parent_attributable"))

        if total_profit is None and operating_profit is not None:
            derived["total_profit"] = operating_profit
            total_profit = operating_profit
        elif total_profit is not None and operating_profit is not None:
            if abs(total_profit) < max(abs(operating_profit) * 0.1, 1000) and abs(operating_profit) > 1000:
                derived["total_profit"] = operating_profit
                total_profit = operating_profit
        if operating_profit is None and total_profit is not None:
            derived["operating_profit"] = total_profit

        field_sources = dict(derived.get("_field_sources") or {})
        if parent_net_profit is not None:
            parent_meta = field_sources.get("parent_net_profit")
            if parent_meta:
                field_sources["net_profit"] = dict(parent_meta)
                derived["_field_sources"] = field_sources
        net_profit_meta = field_sources.get("net_profit") or {}
        net_profit_10k = self._to_number(derived.get("net_profit_10k_yuan"))
        if net_profit is not None and net_profit_10k is not None:
            mismatch_limit = max(min(abs(net_profit), abs(net_profit_10k)) * 0.2, 1000)
            if abs(net_profit - net_profit_10k) > mismatch_limit:
                derived["net_profit_10k_yuan"] = net_profit
                net_profit_10k = net_profit
                if net_profit_meta:
                    field_sources["net_profit_10k_yuan"] = dict(net_profit_meta)
                if field_sources:
                    derived["_field_sources"] = field_sources

        if net_cash_flow is None and all(v is not None for v in (operating_cf, investing_cf, financing_cf)):
            derived["net_cash_flow"] = self._round_intermediate(operating_cf + investing_cf + financing_cf)
            net_cash_flow = derived["net_cash_flow"]

        if net_cash_flow not in (None, 0):
            calc_operating_ratio = self._round_intermediate(operating_cf / net_cash_flow * 100) if operating_cf is not None else None
            calc_investing_ratio = self._round_intermediate(investing_cf / net_cash_flow * 100) if investing_cf is not None else None
            calc_financing_ratio = self._round_intermediate(financing_cf / net_cash_flow * 100) if financing_cf is not None else None

            for calc_val, field_name in [
                (calc_operating_ratio, "operating_cf_ratio_of_net_cf"),
                (calc_investing_ratio, "investing_cf_ratio_of_net_cf"),
                (calc_financing_ratio, "financing_cf_ratio_of_net_cf"),
            ]:
                if calc_val is None:
                    continue
                current = self._to_number(derived.get(field_name))
                if current is None:
                    derived[field_name] = calc_val

        if derived.get("net_profit_10k_yuan") is None and net_profit is not None:
            derived["net_profit_10k_yuan"] = net_profit

        if net_profit is None:
            derived["net_profit_10k_yuan"] = None
            derived["net_profit_yoy_growth"] = None
            derived["net_profit_qoq_growth"] = None

        if revenue is None:
            derived["operating_revenue_yoy_growth"] = None
            derived["operating_revenue_qoq_growth"] = None

        self._coalesce_preferred(derived, "total_operating_revenue", derived.get("total_operating_revenue"))

        if (revenue is None or net_profit is None) and derived.get("net_profit_margin") is None:
            derived["net_profit_margin"] = None

        repeated_values = {}
        for field in ["operating_revenue_qoq_growth", "net_profit_qoq_growth", "net_asset_per_share", "operating_cf_per_share"]:
            value = self._to_number(derived.get(field))
            if value is not None:
                repeated_values.setdefault(round(value, 4), []).append(field)
        for value, fields in repeated_values.items():
            if len(fields) >= 3:
                for field in fields:
                    derived[field] = None

        if derived.get("operating_cf_per_share") is None and share_capital not in (None, 0) and operating_cf is not None:
            derived["operating_cf_per_share"] = self._round_intermediate(operating_cf * 10000 / share_capital)

        if derived.get("net_asset_per_share") is None and share_capital not in (None, 0) and parent_equity is not None:
            derived["net_asset_per_share"] = self._round_intermediate(parent_equity * 10000 / share_capital)

        if derived.get("net_profit_excl_non_recurring_yoy") is None:
            pass

        ncf = self._to_number(derived.get("net_cash_flow"))
        if ncf is not None:
            derived["net_cash_flow"] = self._round_intermediate(ncf * 10000)

        return derived

    def _estimate_share_count(self, net_profit: Optional[float], eps: Optional[float]) -> Optional[float]:
        if net_profit is None or eps is None:
            return None
        if abs(net_profit) < 1e-8 or abs(eps) < 1e-8:
            return None
        if abs(eps) > 20:
            return None
        estimated_shares = abs(net_profit * 10000 / eps)
        if estimated_shares < 1e7 or estimated_shares > 2e10:
            return None
        return estimated_shares

    def _annotate_financial_anomalies(self, data: Dict[str, Any]) -> Dict[str, Any]:
        annotated = dict(data)
        anomaly_flags: List[str] = list(annotated.get("anomaly_flags") or [])
        revenue = self._to_number(annotated.get("total_operating_revenue"))
        assets = self._to_number(annotated.get("asset_total_assets"))
        liabilities = self._to_number(annotated.get("liability_total_liabilities"))

        bounded_fields = {
            "eps": 20.0,
            "roe": 100.0,
            "roe_weighted_excl_non_recurring": 100.0,
            "gross_profit_margin": 100.0,
            "net_profit_margin": 100.0,
            "operating_revenue_yoy_growth": 1000.0,
            "net_profit_yoy_growth": 1000.0,
            "net_profit_excl_non_recurring_yoy": 1000.0,
            "net_asset_per_share": 1000.0,
            "operating_cf_per_share": 1000.0,
        }
        for field, bound in bounded_fields.items():
            value = self._to_number(annotated.get(field))
            if value is not None and abs(value) > bound:
                anomaly_flags.append(f"{field}: out_of_bound({value} > {bound})")

        for field in ["net_profit", "net_profit_excl_non_recurring", "operating_cf_net_amount"]:
            value = self._to_number(annotated.get(field))
            if value is not None and revenue not in (None, 0) and abs(value) > abs(revenue) * 5:
                anomaly_flags.append(f"{field}: too_large_vs_revenue({value} vs {revenue})")

        net_profit = self._to_number(annotated.get("net_profit"))
        net_profit_10k = self._to_number(annotated.get("net_profit_10k_yuan"))
        if net_profit is not None and net_profit_10k is not None:
            if abs(net_profit - net_profit_10k) > max(abs(net_profit) * 0.2, 1000):
                anomaly_flags.append(
                    f"net_profit_mismatch: net_profit({net_profit}) vs net_profit_10k_yuan({net_profit_10k})"
                )

        total_profit = self._to_number(annotated.get("total_profit"))
        if net_profit is not None and total_profit is not None:
            if total_profit < 0 < net_profit and min(abs(total_profit), abs(net_profit)) > 100:
                anomaly_flags.append(
                    f"net_profit_sign_conflict_with_total_profit: net_profit({net_profit}) vs total_profit({total_profit})"
                )

        operating_cf = self._to_number(annotated.get("operating_cf_net_amount"))
        investing_cf = self._to_number(annotated.get("investing_cf_net_amount"))
        financing_cf = self._to_number(annotated.get("financing_cf_net_amount"))
        net_cash_flow = self._to_number(annotated.get("net_cash_flow"))
        if all(v is not None for v in (operating_cf, investing_cf, financing_cf, net_cash_flow)):
            ncf_10k = net_cash_flow / 10000.0
            cf_sum = operating_cf + investing_cf + financing_cf
            if abs(ncf_10k - cf_sum) > max(abs(cf_sum) * 0.05, 100):
                anomaly_flags.append(
                    f"cashflow_reconciliation_broken: net_cash_flow_10k({round(ncf_10k, 2)}) vs components_sum({round(cf_sum, 2)})"
                )

        if assets is not None and revenue not in (None, 0):
            if abs(assets) < abs(revenue) * 0.1:
                anomaly_flags.append(f"asset_total_assets: too_small_vs_revenue({assets} vs {revenue})")

        if assets is not None and abs(assets) >= 500000:
            for field in ["asset_cash_and_cash_equivalents", "asset_accounts_receivable", "asset_inventory"]:
                value = self._to_number(annotated.get(field))
                if value is not None and 0 < abs(value) < 30000 and abs(value) < abs(assets) * 0.02:
                    anomaly_flags.append(f"{field}: suspiciously_small_vs_assets({value} vs {assets})")

        if assets not in (None, 0) and liabilities is not None and liabilities > assets * 1.05:
            anomaly_flags.append(f"balance_sheet_broken: liabilities({liabilities}) > assets({assets})")
            if revenue not in (None, 0) and abs(liabilities) > abs(revenue) * 5:
                anomaly_flags.append(f"liability_total_liabilities: too_large_vs_revenue({liabilities} vs {revenue})")

        liability_components = [
            self._to_number(annotated.get("liability_accounts_payable")),
            self._to_number(annotated.get("liability_contract_liabilities")),
            self._to_number(annotated.get("liability_short_term_loans")),
        ]
        max_liability_component = max((v for v in liability_components if v is not None), default=None)
        if liabilities is not None and max_liability_component is not None and liabilities < max_liability_component:
            anomaly_flags.append(
                f"liability_total_liabilities: smaller_than_components({liabilities} < {max_liability_component})"
            )

        equity = self._to_number(annotated.get("equity_total_equity"))
        parent_equity = self._to_number(annotated.get("equity_parent_attributable"))
        minority_interest = self._to_number(annotated.get("equity_minority_interest"))
        unappropriated_profit = self._to_number(annotated.get("equity_unappropriated_profit"))
        share_capital = self._to_number(annotated.get("share_capital"))
        if equity is not None and unappropriated_profit is not None and equity < unappropriated_profit:
            anomaly_flags.append(
                f"equity_total_equity: smaller_than_unappropriated_profit({equity} < {unappropriated_profit})"
            )
        if equity is not None and parent_equity is not None and minority_interest is not None:
            implied_total_equity = parent_equity + minority_interest
            if abs(equity - implied_total_equity) > max(abs(equity) * 0.05, 1000):
                anomaly_flags.append(
                    f"equity_parent_mismatch: total({equity}) vs parent+minority({implied_total_equity})"
                )

        net_asset_per_share = self._to_number(annotated.get("net_asset_per_share"))
        operating_cf_per_share = self._to_number(annotated.get("operating_cf_per_share"))
        implied_shares_from_nav = None
        if parent_equity is not None and net_asset_per_share not in (None, 0):
            implied_shares_from_nav = abs(parent_equity * 10000 / net_asset_per_share)
        implied_shares_from_ocf = None
        if operating_cf is not None and operating_cf_per_share not in (None, 0):
            implied_shares_from_ocf = abs(operating_cf * 10000 / operating_cf_per_share)

        share_count_references = [
            ("share_capital", share_capital),
            ("net_asset_per_share", implied_shares_from_nav),
            ("operating_cf_per_share", implied_shares_from_ocf),
        ]
        valid_share_refs = [(name, value) for name, value in share_count_references if value is not None]
        if len(valid_share_refs) >= 2:
            reference_values = [value for _, value in valid_share_refs]
            max_share = max(reference_values)
            min_share = min(reference_values)
            if min_share > 0 and max_share / min_share > 2.5:
                joined = ", ".join(f"{name}={round(value)}" for name, value in valid_share_refs)
                anomaly_flags.append(f"share_count_inconsistent: {joined}")

        if anomaly_flags:
            deduped = []
            seen = set()
            for item in anomaly_flags:
                if item not in seen:
                    deduped.append(item)
                    seen.add(item)
            annotated["anomaly_flags"] = deduped
            annotated["has_anomaly"] = 1
        else:
            annotated["anomaly_flags"] = []
            annotated["has_anomaly"] = 0

        return annotated

    def _validate_before_save(self, data: Dict[str, Any]) -> Dict[str, Any]:
        if not data.get("stock_code"):
            return {"ok": False, "message": "missing stock_code"}
        if data.get("report_period") not in {"Q1", "HY", "Q3", "FY"}:
            return {"ok": False, "message": f"invalid report_period: {data.get('report_period')}"}
        if not isinstance(data.get("report_year"), int):
            return {"ok": False, "message": f"invalid report_year: {data.get('report_year')}"}

        meta_keys = {'stock_code', 'stock_abbr', 'report_year', 'report_period'}
        numeric_fields = [k for k, v in data.items() if isinstance(v, (int, float)) and k not in meta_keys]
        if not numeric_fields:
            return {"ok": False, "message": "no numeric financial fields extracted"}

        review_status = str(data.get("_pre_save_review_status") or "")
        if review_status == "block":
            blockers = ", ".join(data.get("_pre_save_review_blockers") or [])
            return {"ok": False, "message": f"pre_save_review_blocked: {blockers}"}

        # 资产负债表恒等式异常改为“告警可入库”，不再直接阻断主报告落库。
        assets = data.get("asset_total_assets")
        liabilities = data.get("liability_total_liabilities")
        if isinstance(assets, float) and isinstance(liabilities, float):
            if liabilities > assets * 1.05:
                return {"ok": True, "message": "ok_with_anomaly"}

        return {"ok": True, "message": "ok"}

    def _cross_file_growth_specs(self) -> List[Tuple[str, str, float]]:
        return [
            ("operating_revenue_yoy_growth", "total_operating_revenue", 1000.0),
            ("net_profit_yoy_growth", "net_profit", 1000.0),
            ("asset_total_assets_yoy_growth", "asset_total_assets", 1000.0),
            ("liability_total_liabilities_yoy_growth", "liability_total_liabilities", 1000.0),
            ("net_cash_flow_yoy_growth", "net_cash_flow", float("inf")),
        ]

    def _cross_file_candidate_score(self, data: Dict[str, Any], field: str) -> float:
        score = 0.0
        if not bool(data.get("source_is_summary")):
            score += 100.0
        if field in set(data.get("_accepted_fields") or []):
            score += 40.0
        if field in set(data.get("_derived_fields") or []):
            score += 10.0
        field_source = dict(data.get("_field_sources") or {}).get(field) or {}
        score += float(field_source.get("score", 0))
        if bool(data.get("has_anomaly")):
            score -= 5.0
        return score

    def _best_cross_file_previous_values(self, records: List[Dict[str, Any]]) -> Dict[Tuple[str, int, str, str], float]:
        best_values: Dict[Tuple[str, int, str, str], Tuple[float, float]] = {}
        for record in records:
            if record.get("status") != "success":
                continue
            data = record.get("data") or {}
            stock_code = data.get("stock_code")
            report_year = data.get("report_year")
            report_period = data.get("report_period")
            if not stock_code or not isinstance(report_year, int) or not report_period:
                continue
            for _, base_field, _ in self._cross_file_growth_specs():
                base_value = self._to_number(data.get(base_field))
                if base_value is None:
                    continue
                score = self._cross_file_candidate_score(data, base_field)
                key = (str(stock_code), int(report_year), str(report_period), base_field)
                current = best_values.get(key)
                if current is None or score > current[0]:
                    best_values[key] = (score, base_value)
        return {key: value for key, (_, value) in best_values.items()}

    def _annual_qoq_backfill_candidates(self, records: List[Dict[str, Any]]) -> Dict[Tuple[str, int, str, str], Tuple[float, float]]:
        candidates: Dict[Tuple[str, int, str, str], Tuple[float, float]] = {}
        for record in records:
            if record.get("status") != "success":
                continue
            data = record.get("data") or {}
            stock_code = data.get("stock_code")
            report_year = data.get("report_year")
            report_period = data.get("report_period")
            quarter_qoq_backfill = data.get("_quarter_qoq_backfill") or {}
            if not stock_code or not isinstance(report_year, int) or report_period != "FY" or not isinstance(quarter_qoq_backfill, dict):
                continue
            base_score = 200.0 + self._cross_file_candidate_score(data, "total_operating_revenue")
            for target_period, period_values in quarter_qoq_backfill.items():
                if target_period not in {"HY", "Q3", "FY"} or not isinstance(period_values, dict):
                    continue
                for field, value in period_values.items():
                    numeric = self._to_number(value)
                    if numeric is None:
                        continue
                    key = (str(stock_code), int(report_year), str(target_period), str(field))
                    current = candidates.get(key)
                    if current is None or base_score > current[0]:
                        candidates[key] = (base_score, numeric)
        return candidates

    def _apply_annual_qoq_backfill(self, records: List[Dict[str, Any]]) -> None:
        candidates = self._annual_qoq_backfill_candidates(records)
        if not candidates:
            return
        for record in records:
            if record.get("status") != "success":
                continue
            data = record.get("data") or {}
            stock_code = data.get("stock_code")
            report_year = data.get("report_year")
            report_period = data.get("report_period")
            if not stock_code or not isinstance(report_year, int) or report_period not in {"HY", "Q3", "FY"}:
                continue
            derived_fields = list(data.get("_derived_fields") or [])
            field_sources = dict(data.get("_field_sources") or {})
            for field in ["operating_revenue_qoq_growth", "net_profit_qoq_growth"]:
                if data.get(field) is not None:
                    continue
                candidate = candidates.get((str(stock_code), int(report_year), str(report_period), field))
                if candidate is None:
                    continue
                _, value = candidate
                data[field] = value
                field_sources[field] = {
                    "score": 260,
                    "table_type": "quarter_summary",
                    "preferred_types": ["quarter_summary"],
                    "extraction_mode": "annual_quarter_backfill",
                    "is_combined": True,
                    "chunk_idx": -1,
                }
                if field not in derived_fields:
                    derived_fields.append(field)
                data.setdefault("_annual_quarter_backfilled_fields", [])
                if field not in data["_annual_quarter_backfilled_fields"]:
                    data["_annual_quarter_backfilled_fields"].append(field)
            if field_sources:
                data["_field_sources"] = field_sources
            data["_derived_fields"] = derived_fields

    def backfill_cross_file_growths(self, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        self._apply_annual_qoq_backfill(records)
        previous_values = self._best_cross_file_previous_values(records)
        for record in records:
            if record.get("status") != "success":
                continue
            data = record.get("data") or {}
            stock_code = data.get("stock_code")
            report_year = data.get("report_year")
            report_period = data.get("report_period")
            if not stock_code or not isinstance(report_year, int) or not report_period:
                continue
            derived_fields = list(data.get("_derived_fields") or [])
            for growth_field, base_field, limit in self._cross_file_growth_specs():
                if data.get(growth_field) is not None:
                    continue
                current_value = self._to_number(data.get(base_field))
                if current_value is None:
                    continue
                previous_value = previous_values.get((str(stock_code), int(report_year) - 1, str(report_period), base_field))
                if previous_value is None:
                    continue
                calc_growth = self._calc_growth(current_value, previous_value)
                if calc_growth is None or abs(calc_growth) > limit:
                    continue
                data[growth_field] = calc_growth
                if growth_field not in derived_fields:
                    derived_fields.append(growth_field)
                data.setdefault("_cross_file_backfilled_fields", [])
                if growth_field not in data["_cross_file_backfilled_fields"]:
                    data["_cross_file_backfilled_fields"].append(growth_field)
            data["_derived_fields"] = derived_fields
        return records

    def _save_to_db(self, final_data: Dict[str, Any]):
        stock_code = self._normalize_stock_code(final_data.get("stock_code"))
        if not self._is_registry_stock_code(stock_code):
            logger.warning(
                f"[ETL] Skip non-registry record: "
                f"{final_data.get('stock_code')} {final_data.get('report_year')} {final_data.get('report_period')}"
            )
            return
        final_data["stock_code"] = stock_code
        final_data["stock_abbr"] = resolve_stock_abbr(stock_code)
        if str(final_data.get("_pre_save_review_status") or "") == "block":
            logger.warning(
                f"[ETL] Skip blocked record by pre-save review: "
                f"{final_data.get('stock_code')} {final_data.get('report_year')} {final_data.get('report_period')} "
                f"{final_data.get('_pre_save_review_blockers')}"
            )
            return
        if bool(final_data.get("source_is_summary")) and bool(final_data.get("_summary_placeholder_like")):
            logger.warning(
                f"[ETL] Skip summary placeholder-like record: "
                f"{final_data.get('stock_code')} {final_data.get('report_year')} {final_data.get('report_period')}"
            )
            return
        if self._db_engine is None:
            config = get_db_config()
            self._db_engine = create_engine(
                config.connection_string,
                pool_size=2,
                pool_recycle=1800,
            )
        storage_ready = self._apply_storage_precision(dict(final_data))
        tables_data = self._split_data_to_tables(storage_ready)
        fill_only = bool(storage_ready.get("source_is_summary"))
        table_ops = [
            ('income', IncomeSheet, 'income_sheet'),
            ('balance', BalanceSheet, 'balance_sheet'),
            ('cash', CashFlowSheet, 'cash_flow_sheet'),
            ('indicators', CorePerformanceIndicatorsSheet, 'core_performance_indicators_sheet'),
        ]
        for table_key, model_class, table_name in table_ops:
            row = tables_data[table_key]
            if not row:
                continue
            try:
                import_sheet(
                    self._db_engine,
                    pd.DataFrame([row]),
                    model_class,
                    table_name,
                    fill_only=fill_only,
                    replace_existing=not fill_only,
                )
            except Exception as e:
                logger.error(
                    f"[ETL] DB write failed but continue: {table_name} "
                    f"{storage_ready.get('stock_code')} {storage_ready.get('report_year')} {storage_ready.get('report_period')} | {e}"
                )
        logger.info(f"[ETL] DB write OK: {storage_ready.get('stock_code')} {storage_ready.get('report_year')} {storage_ready.get('report_period')}")

    def save_records_to_db(self, records: List[Dict[str, Any]]) -> None:
        valid_records: List[Dict[str, Any]] = []
        for record in records:
            if record.get("status") != "success":
                continue
            data = record.get("data") or {}
            if bool(data.get("source_is_summary")) and bool(data.get("_summary_placeholder_like")):
                logger.warning(
                    f"[ETL] Skip summary placeholder-like record: "
                    f"{data.get('stock_code')} {data.get('report_year')} {data.get('report_period')}"
                )
                continue
            valid_records.append(data)

        if not valid_records:
            return

        success_count = 0
        error_count = 0
        for data in valid_records:
            try:
                self._save_to_db(data)
                success_count += 1
            except Exception as e:
                error_count += 1
                logger.error(
                    f"[ETL] Skip failed record during batch DB write: "
                    f"{data.get('stock_code')} {data.get('report_year')} {data.get('report_period')} | {e}"
                )
        logger.info(f"[ETL] Batch DB write finished: success={success_count}, error={error_count}")

    def _split_data_to_tables(self, data: Dict[str, Any]) -> Dict[str, Dict]:
        income_fields = [
            'net_profit', 'net_profit_yoy_growth', 'other_income',
            'total_operating_revenue', 'operating_revenue_yoy_growth',
            'operating_expense_cost_of_sales', 'operating_expense_selling_expenses',
            'operating_expense_administrative_expenses', 'operating_expense_financial_expenses',
            'operating_expense_rnd_expenses', 'operating_expense_taxes_and_surcharges',
            'total_operating_expenses', 'operating_profit', 'total_profit',
            'asset_impairment_loss', 'credit_impairment_loss',
        ]
        balance_fields = [
            'asset_cash_and_cash_equivalents', 'asset_accounts_receivable', 'asset_inventory',
            'asset_trading_financial_assets', 'asset_construction_in_progress', 'asset_total_assets',
            'asset_total_assets_yoy_growth', 'liability_accounts_payable', 'liability_advance_from_customers',
            'liability_total_liabilities', 'liability_total_liabilities_yoy_growth',
            'liability_contract_liabilities', 'liability_short_term_loans', 'asset_liability_ratio',
            'equity_unappropriated_profit', 'equity_total_equity',
        ]
        cash_fields = [
            'net_cash_flow', 'net_cash_flow_yoy_growth', 'operating_cf_net_amount',
            'operating_cf_ratio_of_net_cf', 'operating_cf_cash_from_sales',
            'investing_cf_net_amount', 'investing_cf_ratio_of_net_cf',
            'investing_cf_cash_for_investments', 'investing_cf_cash_from_investment_recovery',
            'financing_cf_cash_from_borrowing', 'financing_cf_cash_for_debt_repayment',
            'financing_cf_net_amount', 'financing_cf_ratio_of_net_cf',
        ]
        indicator_fields = [
            'eps', 'total_operating_revenue', 'operating_revenue_yoy_growth', 'operating_revenue_qoq_growth',
            'net_profit_10k_yuan', 'net_profit_yoy_growth', 'net_profit_qoq_growth',
            'net_asset_per_share', 'roe', 'operating_cf_per_share', 'net_profit_excl_non_recurring',
            'net_profit_excl_non_recurring_yoy', 'gross_profit_margin', 'net_profit_margin',
            'roe_weighted_excl_non_recurring',
        ]

        base = {k: v for k, v in data.items() if k in ('stock_code', 'stock_abbr', 'report_year', 'report_period')}

        def populate(fields: List[str]) -> Dict:
            result: Dict[str, Any] = {}
            for f in fields:
                if f in data:
                    result[f] = data[f]
            if result:
                result.update(base)
            return result

        return {
            'income': populate(income_fields),
            'balance': populate(balance_fields),
            'cash': populate(cash_fields),
            'indicators': populate(indicator_fields),
        }
