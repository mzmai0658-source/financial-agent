from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.data_paths import find_report_dirs
from src.utils.ocr_json_parser import find_json_cache_for_pdf
from src.utils.report_file_selector import select_preferred_report_files


def _find_szse_dir() -> Path:
    for path in find_report_dirs():
        p = Path(path)
        if p.name == "reports-深交所":
            return p
    raise FileNotFoundError("未找到 reports-深交所 目录")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    report_dir = _find_szse_dir()
    all_pdfs = list(report_dir.rglob("*.pdf"))
    selected_pdfs, dropped = select_preferred_report_files(all_pdfs)

    missing = []
    existing = 0
    for pdf_path in selected_pdfs:
        if find_json_cache_for_pdf(str(pdf_path)):
            existing += 1
        else:
            missing.append(pdf_path)

    print(f"REPORT_DIR={report_dir}")
    print(f"PDF_TOTAL={len(all_pdfs)}")
    print(f"PDF_SELECTED={len(selected_pdfs)}")
    print(
        "PDF_DROPPED="
        f"{len(dropped['english']) + len(dropped['pre_update']) + len(dropped['superseded'])}"
    )
    print(f"JSON_EXISTING={existing}")
    print(f"JSON_MISSING={len(missing)}")

    if missing:
        print("MISSING_FILES_BEGIN")
        for path in missing:
            print(path)
        print("MISSING_FILES_END")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
