from typing import List, Any, Dict, Tuple, Optional, Set
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from loguru import logger
from sqlalchemy import create_engine, text

from .base import BaseAgent
from .etl_worker import ETLWorker
from src.agent.llm_client import LLMClient
from config.db_config import get_db_config
from src.utils.company_registry import get_code_to_name, get_name_to_code
from src.utils.ocr_json_parser import find_json_cache_for_pdf, parse_ocr_json_to_content_and_chunks, read_ocr_json
from src.utils.report_file_selector import select_preferred_report_files


class BossAgent(BaseAgent):
    """
    Boss Agent：ETL 调度入口。
    并发扫描目录下所有 PDF，分配给多个独立 ETLWorker 解析入库。
    """

    # 并发 ETL 线程数（避免超过 LLM API QPS 限制）
    ETL_MAX_WORKERS = 3
    REPORT_PERIOD_PATTERNS: Tuple[Tuple[str, str], ...] = (
        (r"(20\d{2})年(?:第)?一季度报告", "Q1"),
        (r"(20\d{2})年半年度报告", "HY"),
        (r"(20\d{2})年(?:第)?三季度报告", "Q3"),
        (r"(20\d{2})年年度报告", "FY"),
    )

    def __init__(self, llm_client: LLMClient):
        super().__init__("BossAgent", llm_client)
        self._db_engine = None
        self._report_key_cache: Dict[str, Optional[Tuple[str, int, str]]] = {}
        self._meta_probe_worker: Optional[ETLWorker] = None

    def run(self, command: str, **kwargs) -> Any:
        logger.info(f"[Boss] Command: {command}")
        if command == 'etl':
            return self.run_etl(kwargs.get('directory'))
        logger.warning(f"[Boss] Unknown command: {command}")
        return None

    # ── ETL 调度（并发）────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_company_token(token: str) -> str:
        return re.sub(r"\s+", "", str(token or "")).strip()

    def _normalized_registry_name_to_code(self) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for name, code in get_name_to_code().items():
            token = self._normalize_company_token(name)
            if token:
                mapping[token] = code
        return mapping

    def _is_registry_pdf(self, pdf_path: Path) -> bool:
        registry_codes = get_code_to_name()
        stem = pdf_path.stem

        if len(stem) >= 6 and stem[:6].isdigit():
            return stem[:6] in registry_codes

        code_match = re.search(r"(\d{6})", stem)
        if code_match:
            return code_match.group(1) in registry_codes

        registry_name_map = self._normalized_registry_name_to_code()
        for sep in ("：", ":"):
            if sep in stem:
                company = self._normalize_company_token(stem.split(sep, 1)[0])
                if company:
                    return company in registry_name_map

        return False

    def _company_group_key(self, pdf_path: Path) -> str:
        stem = pdf_path.stem
        if len(stem) >= 6 and stem[:6].isdigit():
            return stem[:6]

        code_match = re.search(r"(\d{6})", stem)
        if code_match:
            return code_match.group(1)

        for sep in ("：", ":"):
            if sep in stem:
                company = self._normalize_company_token(stem.split(sep, 1)[0])
                if company:
                    return f"name:{company}"

        return f"path:{pdf_path.name}"

    def _group_pdfs_by_company(self, pdf_files: List[Path]) -> List[Tuple[str, List[Path]]]:
        buckets: Dict[str, List[Path]] = {}
        for pdf_path in sorted(pdf_files, key=lambda p: str(p)):
            buckets.setdefault(self._company_group_key(pdf_path), []).append(pdf_path)
        return sorted(buckets.items(), key=lambda item: item[0])

    def _get_db_engine(self):
        if self._db_engine is None:
            config = get_db_config()
            self._db_engine = create_engine(
                config.connection_string,
                pool_size=2,
                pool_recycle=1800,
            )
        return self._db_engine

    def _fetch_completed_report_keys(self) -> Set[Tuple[str, int, str]]:
        engine = self._get_db_engine()
        table_names = [
            "income_sheet",
            "balance_sheet",
            "cash_flow_sheet",
            "core_performance_indicators_sheet",
        ]
        table_key_sets: List[Set[Tuple[str, int, str]]] = []
        with engine.connect() as conn:
            for table_name in table_names:
                rows = conn.execute(
                    text(f"SELECT stock_code, report_year, report_period FROM {table_name}")
                ).fetchall()
                table_key_sets.append({
                    (str(row[0]), int(row[1]), str(row[2]))
                    for row in rows
                    if row[0] is not None and row[1] is not None and row[2] is not None
                })

        if not table_key_sets:
            return set()
        completed = set.intersection(*table_key_sets)
        logger.info(f"[Boss] Found {len(completed)} completed report keys in DB.")
        return completed

    def _extract_report_key(self, pdf_path: Path) -> Optional[Tuple[str, int, str]]:
        cache_key = str(pdf_path.resolve())
        if cache_key in self._report_key_cache:
            return self._report_key_cache[cache_key]

        if self._meta_probe_worker is None:
            class _SilentLLM:
                def chat(self, *args, **kwargs):
                    return ""

            self._meta_probe_worker = ETLWorker(_SilentLLM())

        report_key = None
        try:
            content, _ = self._meta_probe_worker._load_source(str(pdf_path))
            meta = self._meta_probe_worker._extract_metadata(content, str(pdf_path))
            stock_code = str(meta.get("stock_code") or "").strip()
            report_year = meta.get("report_year")
            report_period = str(meta.get("report_period") or "").strip()
            if stock_code and isinstance(report_year, int) and report_period:
                report_key = (stock_code, report_year, report_period)
        except Exception as e:
            logger.warning(f"[Boss] Failed to resolve report key via metadata probe: {pdf_path.name} | {e}")

        self._report_key_cache[cache_key] = report_key
        return report_key

    def run_etl(self, directory: str, skip_completed: bool = True) -> List[Dict[str, Any]]:
        if not directory:
            logger.error("[Boss] No directory provided for ETL.")
            return []

        all_pdf_files = list(Path(directory).rglob("*.pdf"))
        pdf_files, dropped = select_preferred_report_files(all_pdf_files)
        registry_filtered_pdf_files = [pdf for pdf in pdf_files if self._is_registry_pdf(pdf)]
        non_registry_count = len(pdf_files) - len(registry_filtered_pdf_files)
        pdf_files = registry_filtered_pdf_files
        total = len(pdf_files)
        logger.info(
            f"[Boss] Found {len(all_pdf_files)} PDF files, selected {len(registry_filtered_pdf_files)} after filtering."
        )
        if dropped["english"]:
            logger.info(f"[Boss] Skip English reports: {len(dropped['english'])}")
        if dropped["pre_update"]:
            logger.info(f"[Boss] Skip pre-update reports: {len(dropped['pre_update'])}")
        if dropped["superseded"]:
            logger.info(f"[Boss] Skip superseded reports: {len(dropped['superseded'])}")
        if non_registry_count:
            logger.info(f"[Boss] Skip non-registry reports: {non_registry_count}")

        if total == 0:
            return []

        results: List[Dict[str, Any]] = []
        coordinator = ETLWorker(self.llm)
        company_groups = self._group_pdfs_by_company(pdf_files)
        logger.info(f"[Boss] Grouped into {len(company_groups)} company batches.")
        completed_report_keys = self._fetch_completed_report_keys() if skip_completed else set()

        processed_offset = 0
        for company_index, (company_key, company_pdf_files) in enumerate(company_groups, start=1):
            company_report_keys: Set[Tuple[str, int, str]] = set()
            unresolved_report_keys = 0
            for pdf_path in company_pdf_files:
                report_key = self._extract_report_key(pdf_path)
                if report_key:
                    company_report_keys.add(report_key)
                else:
                    unresolved_report_keys += 1

            company_completed = (
                skip_completed
                and bool(company_report_keys)
                and unresolved_report_keys == 0
                and company_report_keys.issubset(completed_report_keys)
            )
            if company_completed:
                logger.info(
                    f"[Boss] Skip completed company batch: {company_key} | "
                    f"files={len(company_pdf_files)} | report_keys={len(company_report_keys)}"
                )
                processed_offset += len(company_pdf_files)
                continue

            logger.info(
                f"[Boss] Company batch {company_index}/{len(company_groups)}: "
                f"{company_key} | files={len(company_pdf_files)} | "
                f"reprocess_from_start={skip_completed and bool(company_report_keys & completed_report_keys)} | "
                f"resolved_report_keys={len(company_report_keys)} | unresolved_report_keys={unresolved_report_keys}"
            )
            company_results: List[Dict[str, Any]] = []

            # 每家公司内部并发处理，完成后立刻按公司回填并写库
            with ThreadPoolExecutor(max_workers=min(self.ETL_MAX_WORKERS, max(1, len(company_pdf_files)))) as executor:
                future_to_pdf = {
                    executor.submit(
                        self._process_single_pdf,
                        pdf,
                        processed_offset + idx + 1,
                        total,
                    ): pdf
                    for idx, pdf in enumerate(company_pdf_files)
                }
                for future in as_completed(future_to_pdf):
                    pdf = future_to_pdf[future]
                    try:
                        res = future.result()
                        company_results.append(res)
                        results.append(res)
                        status = res.get("status", "unknown")
                        logger.info(f"[Boss] {pdf.name} → {status}")
                    except Exception as e:
                        logger.error(f"[Boss] Unhandled exception for {pdf.name}: {e}")
                        error_result = {"status": "error", "file": str(pdf), "message": str(e)}
                        company_results.append(error_result)
                        results.append(error_result)

            processed_offset += len(company_pdf_files)

            stock_code_buckets: Dict[str, List[Dict[str, Any]]] = {}
            fallback_bucket: List[Dict[str, Any]] = []
            for result in company_results:
                if result.get("status") != "success":
                    continue
                data = result.get("data") or {}
                stock_code = str(data.get("stock_code") or "").strip()
                if stock_code:
                    stock_code_buckets.setdefault(stock_code, []).append(result)
                else:
                    fallback_bucket.append(result)

            for stock_code, records in sorted(stock_code_buckets.items()):
                try:
                    logger.info(
                        f"[Boss] Company write-back: {company_key} -> {stock_code} ({len(records)} files)"
                    )
                    coordinator.backfill_cross_file_growths(records)
                    coordinator.save_records_to_db(records)
                    for result in records:
                        data = result.get("data") or {}
                        stock_code_key = str(data.get("stock_code") or "").strip()
                        report_year = data.get("report_year")
                        report_period = str(data.get("report_period") or "").strip()
                        if stock_code_key and isinstance(report_year, int) and report_period:
                            completed_report_keys.add((stock_code_key, report_year, report_period))
                except Exception as e:
                    logger.error(f"[Boss] Company batch DB write failed for {stock_code}: {e}")
                    for result in records:
                        result["status"] = "error"
                        result["message"] = f"DB write failed: {e}"

            if fallback_bucket:
                try:
                    logger.info(
                        f"[Boss] Fallback write for unresolved stock_code in {company_key}: "
                        f"{len(fallback_bucket)} files"
                    )
                    coordinator.backfill_cross_file_growths(fallback_bucket)
                    coordinator.save_records_to_db(fallback_bucket)
                except Exception as e:
                    logger.error(f"[Boss] Fallback batch DB write failed for {company_key}: {e}")
                    for result in fallback_bucket:
                        result["status"] = "error"
                        result["message"] = f"DB write failed: {e}"

        success = sum(1 for r in results if r.get("status") == "success")
        logger.info(f"[Boss] ETL complete: {success}/{total} succeeded.")
        return results

    def _process_single_pdf(self, pdf_path: Path, index: int, total: int) -> Dict[str, Any]:
        """每个线程独立创建 ETLWorker，避免共享状态竞争。"""
        logger.info(f"[Boss] [{index}/{total}] Start: {pdf_path.name}")
        worker = ETLWorker(self.llm)
        return worker.run(str(pdf_path), save_to_db=False)

