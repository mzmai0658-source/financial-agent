"""
公司信息注册表 —— 从附件1 CSV 自动加载，替代硬编码 COMPANY_MAP
"""

import os
import re
from typing import Dict, Optional, Tuple
from pathlib import Path
from loguru import logger

import pandas as pd
from src.utils.data_paths import find_company_registry_path

_CODE_TO_NAME: Dict[str, str] = {}   # "600080" -> "金花股份"
_NAME_TO_CODE: Dict[str, str] = {}   # "金花股份" -> "600080"
_CODE_TO_EXCHANGE: Dict[str, str] = {}  # "600080" -> "上交所"
_LOADED = False

MANUAL_COMPANY_OVERRIDES = {
    "002424": {"abbr": "贵州百灵", "exchange": "深交所"},
    "002898": {"abbr": "赛隆药业", "exchange": "深交所"},
    "300147": {"abbr": "香雪制药", "exchange": "深交所"},
    "300391": {"abbr": "长药控股", "exchange": "深交所"},
}

# Public repository fallback: keep the code/test/demo path usable even when the
# local Excel company registry is not included in the repository.
FALLBACK_COMPANY_REGISTRY = {
    "002821": {"abbr": "凯莱英", "exchange": "深交所"},
    "300244": {"abbr": "迪安诊断", "exchange": "深交所"},
    "300347": {"abbr": "泰格医药", "exchange": "深交所"},
    "301033": {"abbr": "迈普医学", "exchange": "深交所"},
    "301080": {"abbr": "百普赛斯", "exchange": "深交所"},
    "301096": {"abbr": "百诚医药", "exchange": "深交所"},
    "603127": {"abbr": "昭衍新药", "exchange": "上交所"},
    "603259": {"abbr": "药明康德", "exchange": "上交所"},
    "688222": {"abbr": "成都先导", "exchange": "上交所"},
    "688276": {"abbr": "百克生物", "exchange": "上交所"},
}


def _load_fallback_company_registry() -> None:
    for code, payload in {**FALLBACK_COMPANY_REGISTRY, **MANUAL_COMPANY_OVERRIDES}.items():
        abbr = str(payload.get("abbr") or "").strip()
        exchange = str(payload.get("exchange") or "").strip()
        if not code or not abbr:
            continue
        _CODE_TO_NAME[code] = abbr
        _NAME_TO_CODE[abbr] = code
        if exchange:
            _CODE_TO_EXCHANGE[code] = exchange


def _detect_csv_path() -> Optional[str]:
    detected = find_company_registry_path()
    return str(detected) if detected else None


def load_company_registry(csv_path: Optional[str] = None) -> None:
    global _CODE_TO_NAME, _NAME_TO_CODE, _CODE_TO_EXCHANGE, _LOADED

    if csv_path is None:
        csv_path = os.environ.get("COMPANY_CSV_PATH") or _detect_csv_path()

    if not csv_path or not os.path.exists(csv_path):
        _load_fallback_company_registry()
        logger.warning(f"公司信息文件未找到({csv_path})，使用内置最小公司注册表")
        _LOADED = True
        return

    try:
        df = pd.read_excel(csv_path)

        code_col = next((c for c in df.columns if "股票代码" in c), None)
        abbr_col = next((c for c in df.columns if "A股简称" in c), None)
        exchange_col = next((c for c in df.columns if "交易所" in c), None)
        full_name_col = next((c for c in df.columns if c in ("公司名称", "公司全称")), None)

        if not code_col or not abbr_col:
            logger.error(f"CSV 缺少 '股票代码' 或 'A股简称' 列: {df.columns.tolist()}")
            _LOADED = True
            return

        for _, row in df.iterrows():
            raw_code = str(row[code_col]).strip()
            code = re.sub(r"\D", "", raw_code).zfill(6)
            if len(code) != 6:
                continue
            abbr = str(row[abbr_col]).strip()
            _CODE_TO_NAME[code] = abbr
            _NAME_TO_CODE[abbr] = code

            if full_name_col and pd.notna(row.get(full_name_col)):
                full_name = str(row[full_name_col]).strip()
                _NAME_TO_CODE[full_name] = code

            if exchange_col and pd.notna(row.get(exchange_col)):
                ex = str(row[exchange_col]).strip()
                if "上海" in ex or "上交" in ex:
                    _CODE_TO_EXCHANGE[code] = "上交所"
                elif "深圳" in ex or "深交" in ex:
                    _CODE_TO_EXCHANGE[code] = "深交所"

        for code, payload in MANUAL_COMPANY_OVERRIDES.items():
            abbr = str(payload.get("abbr") or "").strip()
            exchange = str(payload.get("exchange") or "").strip()
            if not code or not abbr:
                continue
            _CODE_TO_NAME[code] = abbr
            _NAME_TO_CODE[abbr] = code
            if exchange:
                _CODE_TO_EXCHANGE[code] = exchange

        logger.info(f"公司注册表已加载: {len(_CODE_TO_NAME)} 家公司 from {csv_path}")
        _LOADED = True

    except Exception as e:
        logger.error(f"加载公司注册表失败: {e}")
        _load_fallback_company_registry()
        _LOADED = True


def _ensure_loaded():
    if not _LOADED:
        load_company_registry()


def get_code_to_name() -> Dict[str, str]:
    _ensure_loaded()
    return dict(_CODE_TO_NAME)


def get_name_to_code() -> Dict[str, str]:
    _ensure_loaded()
    return dict(_NAME_TO_CODE)


def get_code_to_exchange() -> Dict[str, str]:
    _ensure_loaded()
    return dict(_CODE_TO_EXCHANGE)


def resolve_stock_code(text: str) -> Optional[str]:
    _ensure_loaded()
    for name, code in sorted(_NAME_TO_CODE.items(), key=lambda x: -len(x[0])):
        if name in text:
            return code
    return None


def resolve_stock_abbr(code: str) -> str:
    _ensure_loaded()
    return _CODE_TO_NAME.get(code, code)


def build_company_list_for_prompt() -> str:
    _ensure_loaded()
    if not _CODE_TO_NAME:
        return "（公司列表未加载）"
    lines = [f"{name}={code}" for code, name in sorted(_CODE_TO_NAME.items())]
    return "公司代码映射：" + ", ".join(lines)
