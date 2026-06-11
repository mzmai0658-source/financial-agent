from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


ENGLISH_MARKERS = ("英文版",)
PRE_UPDATE_MARKERS = ("更新前", "更正前")
POST_UPDATE_MARKERS = ("更新后", "更正后")
SUMMARY_MARKERS = ("摘要",)

PERIOD_PATTERNS: Tuple[Tuple[str, str], ...] = (
    (r"20\d{2}年(?:第)?一季度报告", "Q1"),
    (r"20\d{2}年半年度报告", "HY"),
    (r"20\d{2}年(?:第)?三季度报告", "Q3"),
    (r"20\d{2}年年度报告", "FY"),
)


def _dedupe_paths(paths: Iterable[Path]) -> List[Path]:
    results: List[Path] = []
    seen = set()
    for path in paths:
        try:
            key = str(path.resolve())
        except Exception:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        results.append(path)
    return results


def _contains_any(text: str, markers: Tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def is_english_report(path: Path) -> bool:
    return _contains_any(path.stem, ENGLISH_MARKERS)


def is_pre_update_report(path: Path) -> bool:
    return _contains_any(path.stem, PRE_UPDATE_MARKERS)


def is_post_update_report(path: Path) -> bool:
    return _contains_any(path.stem, POST_UPDATE_MARKERS)


def _extract_company_token(stem: str) -> Optional[str]:
    if "：" not in stem:
        return None
    company = stem.split("：", 1)[0]
    company = re.sub(r"\s+", "", company).strip()
    return company or None


def _extract_report_period(stem: str) -> Optional[str]:
    for pattern, report_period in PERIOD_PATTERNS:
        if re.search(pattern, stem):
            return report_period
    return None


def _extract_report_year(stem: str) -> Optional[int]:
    match = re.search(r"(20\d{2})年", stem)
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _build_report_identity(path: Path) -> Optional[Tuple[str, int, str, str]]:
    stem = path.stem
    company = _extract_company_token(stem)
    year = _extract_report_year(stem)
    report_period = _extract_report_period(stem)
    if not company or year is None or not report_period:
        return None
    doc_kind = "summary" if _contains_any(stem, SUMMARY_MARKERS) else "full"
    return company, year, report_period, doc_kind


def _preferred_variant_sort_key(path: Path) -> Tuple[int, int, str]:
    stem = path.stem
    if is_post_update_report(path):
        priority = 0
    elif is_pre_update_report(path):
        priority = 2
    else:
        priority = 1
    marker_penalty = 1 if _contains_any(stem, PRE_UPDATE_MARKERS + POST_UPDATE_MARKERS) else 0
    return priority, marker_penalty, str(path)


def select_preferred_report_files(paths: Iterable[Path]) -> Tuple[List[Path], Dict[str, List[Path]]]:
    """
    统一财报筛选逻辑：
    - 跳过英文版
    - 跳过“更新前/更正前”
    - 对同公司同年份同报告期的中文文件，若存在“更新后/更正后”，优先保留它
    """
    candidates = _dedupe_paths(paths)
    dropped: Dict[str, List[Path]] = {
        "english": [],
        "pre_update": [],
        "superseded": [],
    }

    eligible: List[Path] = []
    for path in candidates:
        if is_english_report(path):
            dropped["english"].append(path)
            continue
        if is_pre_update_report(path):
            dropped["pre_update"].append(path)
            continue
        eligible.append(path)

    passthrough: List[Path] = []
    grouped: Dict[Tuple[str, int, str, str], List[Path]] = {}
    for path in eligible:
        identity = _build_report_identity(path)
        if identity is None:
            passthrough.append(path)
            continue
        grouped.setdefault(identity, []).append(path)

    selected: List[Path] = list(passthrough)
    for identity in sorted(grouped.keys()):
        group = grouped[identity]
        ranked = sorted(group, key=_preferred_variant_sort_key)
        best = ranked[0]
        selected.append(best)
        for other in ranked[1:]:
            dropped["superseded"].append(other)

    selected = sorted(_dedupe_paths(selected), key=lambda p: str(p))
    return selected, dropped
