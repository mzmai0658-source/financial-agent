from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional
import os


ROOT_DIR = Path(__file__).resolve().parents[2]

# Keep strings ASCII-safe with unicode escapes to avoid shell/codepage issues.
TEST_DATA_DIR_NAME = "\u6d4b\u8bd5\u6570\u636e"
OFFICIAL_DATA_DIR_NAME = "\u6b63\u5f0f\u6570\u636e"
SAMPLE_DATA_DIR_NAME = "B\u9898-\u793a\u4f8b\u6570\u636e"

ATTACHMENT_1_BASENAME = (
    "\u9644\u4ef61\uff1a\u533b\u836f\u4e0a\u5e02\u516c\u53f8\u57fa\u672c\u4fe1\u606f"
)
ATTACHMENT_2_DIRNAME = "\u9644\u4ef62\uff1a\u8d22\u52a1\u62a5\u544a"
ATTACHMENT_4_BASENAME = "\u9644\u4ef64\uff1a\u95ee\u9898\u6c47\u603b"
ATTACHMENT_5_DIRNAME = "\u9644\u4ef65\uff1a\u7814\u62a5\u6570\u636e"
ATTACHMENT_6_BASENAME = "\u9644\u4ef66\uff1a\u95ee\u9898\u6c47\u603b"

TEST_DATA_DIR = ROOT_DIR / TEST_DATA_DIR_NAME
OFFICIAL_DATA_DIR = ROOT_DIR / OFFICIAL_DATA_DIR_NAME
SAMPLE_DATA_DIRS = [
    ROOT_DIR / SAMPLE_DATA_DIR_NAME / "\u793a\u4f8b\u6570\u636e",
    ROOT_DIR / SAMPLE_DATA_DIR_NAME,
]


def _iter_candidate_data_roots() -> Iterable[Path]:
    # 1) Explicit override
    env_root = os.getenv("DATA_ROOT") or os.getenv("DATA_DIR")
    seen = set()
    if env_root:
        path = Path(env_root)
        if path.exists():
            seen.add(str(path.resolve()))
            yield path

    # 2) Auto priority: test -> official -> sample -> repo root
    for path in [TEST_DATA_DIR, OFFICIAL_DATA_DIR, *SAMPLE_DATA_DIRS, ROOT_DIR]:
        if not path.exists():
            continue
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        yield path


def _path_priority(path: Path) -> tuple[int, int, str]:
    text = str(path)
    if TEST_DATA_DIR_NAME in text:
        rank = 0
    elif OFFICIAL_DATA_DIR_NAME in text:
        rank = 1
    elif SAMPLE_DATA_DIR_NAME in text:
        rank = 2
    else:
        rank = 3
    return (rank, len(path.parts), text)


def _dedupe_paths(paths: Iterable[Path]) -> List[Path]:
    results: List[Path] = []
    seen = set()
    for path in paths:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        results.append(path)
    return results


def _pick_preferred_path(paths: Iterable[Path]) -> Optional[Path]:
    candidates = [path for path in _dedupe_paths(paths) if path.exists()]
    if not candidates:
        return None
    return sorted(candidates, key=_path_priority)[0]


def get_preferred_data_root() -> Path:
    for path in _iter_candidate_data_roots():
        return path
    return ROOT_DIR


def find_question_file(attachment_index: int) -> Optional[Path]:
    base_name = f"\u9644\u4ef6{attachment_index}\uff1a\u95ee\u9898\u6c47\u603b"
    candidates: List[Path] = []
    for root in _iter_candidate_data_roots():
        exact = root / f"{base_name}.xlsx"
        if exact.is_file():
            candidates.append(exact)
        candidates.extend(path for path in root.rglob(f"\u9644\u4ef6{attachment_index}*\u95ee\u9898\u6c47\u603b*.xlsx") if path.is_file())
        candidates.extend(path for path in root.rglob(f"\u9644\u4ef6{attachment_index}*.xlsx") if path.is_file())
    return _pick_preferred_path(candidates)


def find_company_registry_path() -> Optional[Path]:
    candidates: List[Path] = []
    for root in _iter_candidate_data_roots():
        candidates.extend(path for path in root.rglob("\u9644\u4ef61*\u57fa\u672c\u4fe1\u606f*.xlsx") if path.is_file())
        candidates.extend(path for path in root.rglob("\u9644\u4ef61*.xlsx") if path.is_file())
    return _pick_preferred_path(candidates)


def find_financial_reports_root() -> Optional[Path]:
    candidates: List[Path] = []
    for root in _iter_candidate_data_roots():
        exact = root / ATTACHMENT_2_DIRNAME
        if exact.is_dir():
            candidates.append(exact)
        candidates.extend(path for path in root.rglob(ATTACHMENT_2_DIRNAME) if path.is_dir())
    return _pick_preferred_path(candidates)


def find_research_reports_root() -> Optional[Path]:
    candidates: List[Path] = []
    for root in _iter_candidate_data_roots():
        exact = root / ATTACHMENT_5_DIRNAME
        if exact.is_dir():
            candidates.append(exact)
        candidates.extend(path for path in root.rglob(ATTACHMENT_5_DIRNAME) if path.is_dir())
    return _pick_preferred_path(candidates)


def find_report_dirs() -> List[Path]:
    report_root = find_financial_reports_root()
    if not report_root:
        return []

    candidates: List[Path] = []
    for name in ("reports-\u4e0a\u4ea4\u6240", "reports-\u6df1\u4ea4\u6240"):
        exact = report_root / name
        if exact.is_dir():
            candidates.append(exact)
        candidates.extend(path for path in report_root.rglob(name) if path.is_dir())
    return sorted(_dedupe_paths(candidates), key=_path_priority)


def find_research_dirs() -> List[Path]:
    research_root = find_research_reports_root()
    if not research_root:
        return []

    candidates: List[Path] = []
    for name in ("\u4e2a\u80a1\u7814\u62a5", "\u884c\u4e1a\u7814\u62a5"):
        exact = research_root / name
        if exact.is_dir():
            candidates.append(exact)
        candidates.extend(path for path in research_root.rglob(name) if path.is_dir())
    return sorted(_dedupe_paths(candidates), key=_path_priority)

