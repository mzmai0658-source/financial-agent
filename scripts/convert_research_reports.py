"""
研报 PDF 转 JSON（OCR，4 线程并发，带锁与缓存）。

用法（项目根目录执行）：
    .venv\\Scripts\\python.exe scripts\\convert_research_reports.py [--force] [--workers 4] [--clear-locks]
"""

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.utils.data_paths import find_research_dirs
from src.utils.ocr_async_json import (
    OCR_MODEL,
    _convert_single_pdf_task,
    clear_lock_files,
    has_json_cache_file,
)


def convert_research_reports(max_workers: int = 4, force: bool = False):
    """转换研报 PDF 为 JSON。"""
    research_dirs = find_research_dirs()

    if not research_dirs:
        print("未找到研报目录")
        return

    print(f"研报目录: {research_dirs}")

    all_results = {}
    for research_dir in research_dirs:
        print(f"\n处理目录: {research_dir}")

        pdf_files = list(Path(research_dir).glob("*.pdf"))
        print(f"发现 {len(pdf_files)} 个 PDF")

        if not pdf_files:
            continue

        results = {}
        cached_paths = []
        pending_paths = []

        if force:
            pending_paths = [str(p) for p in pdf_files]
        else:
            for pdf_path in pdf_files:
                pdf_str = str(pdf_path)
                if has_json_cache_file(pdf_str, model=OCR_MODEL):
                    cached_paths.append(pdf_str)
                    results[pdf_str] = "cached"
                else:
                    pending_paths.append(pdf_str)

        print(f"已缓存: {len(cached_paths)}, 待处理: {len(pending_paths)}")

        if pending_paths:
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ocr") as executor:
                future_to_pdf = {
                    executor.submit(
                        _convert_single_pdf_task,
                        pdf_path,
                        force=force,
                        save=True,
                        model=OCR_MODEL,
                        optional_payload=None,
                    ): pdf_path
                    for pdf_path in pending_paths
                }

                completed = 0
                for future in as_completed(future_to_pdf):
                    pdf_path = future_to_pdf[future]
                    completed += 1
                    try:
                        result_pdf_path, status = future.result()
                    except Exception as e:
                        print(f"处理失败 {Path(pdf_path).name}: {e}")
                        result_pdf_path, status = pdf_path, "failed"
                    results[result_pdf_path] = status
                    print(f"进度 {completed}/{len(pending_paths)} | {status.upper()} | {Path(result_pdf_path).name}")

        all_results.update(results)

    saved = sum(1 for s in all_results.values() if s == "saved")
    cached = sum(1 for s in all_results.values() if s == "cached")
    failed = sum(1 for s in all_results.values() if s == "failed")
    print(f"\n完成: saved={saved}, cached={cached}, failed={failed}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="研报 PDF 转 JSON")
    parser.add_argument("--force", action="store_true", help="强制重新处理")
    parser.add_argument("--workers", type=int, default=4, help="并发线程数")
    parser.add_argument("--clear-locks", action="store_true", help="清理锁文件")
    args = parser.parse_args()

    if args.clear_locks:
        clear_lock_files()

    convert_research_reports(max_workers=args.workers, force=args.force)
