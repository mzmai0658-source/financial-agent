"""PaddleOCR 异步任务版 JSON 提取工具。

独立于现有 `ocr.py`，用于：
1. 提交 PDF 到异步 OCR 接口
2. 轮询任务状态
3. 下载完整 JSON 结果
4. 保存为 `<pdf>_by_PaddleOCR-VL-1.5.json`

当前按官方 PaddleOCR-VL-1.5 接口示例整理，但任务一默认直接复用
本地已有 JSON 缓存，不在 ETL 中主动触发在线 OCR。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from loguru import logger


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.data_paths import find_report_dirs
from src.utils.report_file_selector import select_preferred_report_files

DEFAULT_REPORT_DIRS = find_report_dirs()

JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
OCR_TOKEN = os.getenv("PADDLEOCR_API_TOKEN") or os.getenv("OCR_API_TOKEN", "")
OCR_MODEL = "PaddleOCR-VL-1.5"
OCR_POLL_INTERVAL = 5.0
OCR_SUBMIT_TIMEOUT = 120
OCR_POLL_TIMEOUT = 3600
DEFAULT_MAX_WORKERS = max(1, min(4, os.cpu_count() or 4))
LOCK_STALE_SECONDS = 12 * 60 * 60
PENDING_HEARTBEAT_SECONDS = 30
JSON_DOWNLOAD_RETRY_DELAYS = (2, 4, 8, 12, 20)

MODEL_JSON_SUFFIXES: Dict[str, str] = {
    "PaddleOCR-VL-1.5": "_by_PaddleOCR-VL-1.5.json",
}


DEFAULT_OPTIONAL_PAYLOAD: Dict[str, Any] = {
    "useDocOrientationClassify": False,
    "useDocUnwarping": False,
    "useChartRecognition": False,
}


def get_default_json_cache_path(pdf_path: str, model: str = OCR_MODEL) -> str:
    suffix = MODEL_JSON_SUFFIXES.get(model, f"_by_{model}.json")
    return pdf_path + suffix


def read_json_cache(pdf_path: str, model: str = OCR_MODEL) -> Optional[Any]:
    cache_path = get_default_json_cache_path(pdf_path, model=model)
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[OCR ASYNC] 读取 JSON 缓存失败: {e}")
        return None


def has_json_cache_file(pdf_path: str, model: str = OCR_MODEL) -> bool:
    cache_path = get_default_json_cache_path(pdf_path, model=model)
    return os.path.exists(cache_path) and os.path.getsize(cache_path) > 0


def get_json_lock_path(pdf_path: str, model: str = OCR_MODEL) -> str:
    return get_default_json_cache_path(pdf_path, model=model) + ".lock"


def _remove_stale_lock_if_needed(lock_path: str) -> bool:
    try:
        if not os.path.exists(lock_path):
            return False
        lock_age = time.time() - os.path.getmtime(lock_path)
        if lock_age <= LOCK_STALE_SECONDS:
            return False
        os.remove(lock_path)
        logger.warning(f"[OCR ASYNC] 移除过期锁文件: {lock_path}")
        return True
    except FileNotFoundError:
        return False
    except Exception as e:
        logger.warning(f"[OCR ASYNC] 清理过期锁文件失败: {lock_path} -> {e}")
        return False


def try_acquire_pdf_lock(pdf_path: str, model: str = OCR_MODEL) -> Optional[str]:
    lock_path = get_json_lock_path(pdf_path, model=model)
    _remove_stale_lock_if_needed(lock_path)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "pid": os.getpid(),
                "pdf_path": pdf_path,
                "created_at": time.time(),
            }, ensure_ascii=False))
        return lock_path
    except FileExistsError:
        return None


def release_pdf_lock(lock_path: Optional[str]) -> None:
    if not lock_path:
        return
    try:
        if os.path.exists(lock_path):
            os.remove(lock_path)
    except FileNotFoundError:
        return
    except Exception as e:
        logger.warning(f"[OCR ASYNC] 删除锁文件失败: {lock_path} -> {e}")


def clear_lock_files(report_dirs: Optional[list[Path]] = None) -> int:
    removed = 0
    seen = set()
    for report_dir in report_dirs or DEFAULT_REPORT_DIRS:
        if not report_dir.exists():
            continue
        for lock_path in report_dir.rglob("*.lock"):
            lock_key = str(lock_path.resolve())
            if lock_key in seen:
                continue
            seen.add(lock_key)
            try:
                lock_path.unlink(missing_ok=True)
                removed += 1
            except Exception as e:
                logger.warning(f"[OCR ASYNC] 删除锁文件失败: {lock_path} -> {e}")
    logger.info(f"[OCR ASYNC] 已清理锁文件: {removed}")
    return removed


def save_json_cache(pdf_path: str, payload: Any, model: str = OCR_MODEL) -> str:
    cache_path = get_default_json_cache_path(pdf_path, model=model)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    logger.info(f"[OCR ASYNC] JSON 已保存: {cache_path}")
    return cache_path


def submit_async_ocr_job(
    pdf_path: str,
    *,
    model: str = OCR_MODEL,
    optional_payload: Optional[Dict[str, Any]] = None,
    timeout: int = OCR_SUBMIT_TIMEOUT,
) -> str:
    """提交本地 PDF 或远端 URL 到异步 OCR 接口，返回 job_id。"""
    is_remote_file = pdf_path.startswith("http://") or pdf_path.startswith("https://")
    if not is_remote_file and not os.path.exists(pdf_path):
        raise FileNotFoundError(pdf_path)

    headers = {
        "Authorization": f"bearer {OCR_TOKEN}",
    }
    effective_optional_payload = optional_payload or DEFAULT_OPTIONAL_PAYLOAD

    logger.info(f"[OCR ASYNC] 提交任务: {os.path.basename(pdf_path)}")
    if is_remote_file:
        headers["Content-Type"] = "application/json"
        payload = {
            "fileUrl": pdf_path,
            "model": model,
            "optionalPayload": effective_optional_payload,
        }
        resp = requests.post(
            JOB_URL,
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    else:
        data = {
            "model": model,
            "optionalPayload": json.dumps(effective_optional_payload),
        }
        with open(pdf_path, "rb") as f:
            files = {"file": f}
            resp = requests.post(
                JOB_URL,
                headers=headers,
                data=data,
                files=files,
                timeout=timeout,
            )

    if resp.status_code != 200:
        raise RuntimeError(f"提交任务失败: HTTP {resp.status_code} {resp.text[:500]}")

    body = resp.json()
    job_id = body.get("data", {}).get("jobId")
    if not job_id:
        raise RuntimeError(f"响应缺少 jobId: {body}")

    logger.info(f"[OCR ASYNC] 任务已提交: job_id={job_id}")
    return str(job_id)


def poll_async_ocr_job(
    job_id: str,
    *,
    poll_interval: float = OCR_POLL_INTERVAL,
    timeout: int = OCR_POLL_TIMEOUT,
) -> Dict[str, Any]:
    """轮询异步 OCR 任务直到完成，返回任务详情。"""
    headers = {
        "Authorization": f"bearer {OCR_TOKEN}",
    }

    started = time.time()
    last_state = None
    last_pending_log_at = 0.0

    while True:
        if time.time() - started > timeout:
            raise TimeoutError(f"OCR 任务轮询超时: job_id={job_id}")

        resp = requests.get(f"{JOB_URL}/{job_id}", headers=headers, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(f"查询任务失败: HTTP {resp.status_code} {resp.text[:500]}")

        body = resp.json()
        data = body.get("data", {})
        state = data.get("state")

        if state != last_state:
            logger.info(f"[OCR ASYNC] job_id={job_id} state={state}")
            last_state = state

        if state == "pending":
            now = time.time()
            if now - last_pending_log_at >= PENDING_HEARTBEAT_SECONDS:
                waited_seconds = int(now - started)
                logger.info(f"[OCR ASYNC] job_id={job_id} 仍在排队，已等待 {waited_seconds}s")
                last_pending_log_at = now
        elif state == "running":
            progress = data.get("extractProgress", {})
            total_pages = progress.get("totalPages")
            extracted_pages = progress.get("extractedPages")
            if total_pages is not None and extracted_pages is not None:
                logger.info(
                    f"[OCR ASYNC] job_id={job_id} 进度: {extracted_pages}/{total_pages}"
                )
        elif state == "done":
            return data
        elif state == "failed":
            raise RuntimeError(f"OCR 任务失败: {data.get('errorMsg')}")

        time.sleep(poll_interval)


def download_async_ocr_json(job_result: Dict[str, Any]) -> Any:
    """根据任务完成结果下载 JSON 文件并解析。"""
    result_url = job_result.get("resultUrl", {}).get("jsonUrl")
    if not result_url:
        raise RuntimeError(f"任务结果中缺少 jsonUrl: {job_result}")

    logger.info("[OCR ASYNC] 下载 JSON 结果")
    resp = None
    text = ""
    retry_delays = (0, *JSON_DOWNLOAD_RETRY_DELAYS)
    last_error = ""
    for attempt, delay in enumerate(retry_delays, start=1):
        if delay:
            time.sleep(delay)
        try:
            resp = requests.get(result_url, timeout=300)
            if resp.status_code == 404 and attempt < len(retry_delays):
                last_error = f"HTTP 404 on attempt {attempt}"
                logger.warning(f"[OCR ASYNC] JSON 结果尚未就绪，准备重试 {attempt}/{len(retry_delays)-1}")
                continue
            resp.raise_for_status()
            text = resp.text.strip()
            if text:
                break
            last_error = "empty response body"
        except Exception as e:
            last_error = str(e)
            if attempt < len(retry_delays):
                logger.warning(f"[OCR ASYNC] 下载 JSON 失败，准备重试 {attempt}/{len(retry_delays)-1}: {e}")
                continue
            raise

    if not text:
        raise RuntimeError(f"下载到的 JSON 结果为空: {last_error}")

    # 官方返回有时是 JSON 数组，有时可能是 JSONL；统一兼容
    if text.startswith("["):
        return json.loads(text)

    records = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return records


def convert_pdf_to_async_json(
    pdf_path: str,
    *,
    force: bool = False,
    save: bool = True,
    model: str = OCR_MODEL,
    optional_payload: Optional[Dict[str, Any]] = None,
) -> Any:
    """将 PDF 异步解析为 JSON 结构。

    - 默认优先读取本地缓存
    - `force=True` 时强制重新提交异步任务
    """
    if not force:
        cached = read_json_cache(pdf_path, model=model)
        if cached is not None:
            logger.info(f"[OCR ASYNC] 命中 JSON 缓存: {os.path.basename(pdf_path)}")
            return cached

    job_id = submit_async_ocr_job(
        pdf_path,
        model=model,
        optional_payload=optional_payload,
    )
    job_result = poll_async_ocr_job(job_id)
    payload = download_async_ocr_json(job_result)

    if save:
        save_json_cache(pdf_path, payload, model=model)
    return payload


def list_report_pdfs(report_dirs: Optional[list[Path]] = None) -> list[str]:
    """扫描默认测试目录中的 PDF 文件路径。"""
    pdf_candidates: list[Path] = []
    for report_dir in report_dirs or DEFAULT_REPORT_DIRS:
        if not report_dir.exists():
            logger.warning(f"[OCR ASYNC] 目录不存在，跳过: {report_dir}")
            continue
        for path in sorted(report_dir.glob("*.pdf")):
            pdf_candidates.append(path)

    selected_paths, dropped = select_preferred_report_files(pdf_candidates)
    if dropped["english"]:
        logger.info(f"[OCR ASYNC] 跳过英文版财报: {len(dropped['english'])}")
    if dropped["pre_update"]:
        logger.info(f"[OCR ASYNC] 跳过更新前/更正前财报: {len(dropped['pre_update'])}")
    if dropped["superseded"]:
        logger.info(f"[OCR ASYNC] 跳过被更新后/更正后覆盖的财报: {len(dropped['superseded'])}")
    return [str(path.resolve()) for path in selected_paths]


def _convert_single_pdf_task(
    pdf_path: str,
    *,
    force: bool,
    save: bool,
    model: str,
    optional_payload: Optional[Dict[str, Any]],
) -> tuple[str, str]:
    basename = os.path.basename(pdf_path)
    lock_path: Optional[str] = None
    try:
        if not force and has_json_cache_file(pdf_path, model=model):
            logger.info(f"[OCR ASYNC] 跳过已有缓存: {basename}")
            return pdf_path, "cached"

        lock_path = try_acquire_pdf_lock(pdf_path, model=model)
        if lock_path is None:
            if has_json_cache_file(pdf_path, model=model):
                logger.info(f"[OCR ASYNC] 跳过已有缓存: {basename}")
                return pdf_path, "cached"
            logger.info(f"[OCR ASYNC] 跳过处理中任务: {basename}")
            return pdf_path, "locked"

        convert_pdf_to_async_json(
            pdf_path,
            force=force,
            save=save,
            model=model,
            optional_payload=optional_payload,
        )
        return pdf_path, "saved"
    except Exception as e:
        logger.error(f"[OCR ASYNC] 处理失败 {basename}: {e}")
        return pdf_path, "failed"
    finally:
        release_pdf_lock(lock_path)


def convert_pdfs_in_default_dirs(
    *,
    force: bool = False,
    save: bool = True,
    model: str = OCR_MODEL,
    optional_payload: Optional[Dict[str, Any]] = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> Dict[str, str]:
    """批量处理默认目录中的全部 PDF。

    返回:
        {pdf_path: "saved" | "cached" | "locked" | "failed"}
    """
    pdf_paths = list_report_pdfs()
    if not pdf_paths:
        logger.warning("[OCR ASYNC] 默认目录下未找到 PDF")
        return {}

    results: Dict[str, str] = {}
    total = len(pdf_paths)
    max_workers = max(1, int(max_workers or 1))

    cached_paths: list[str] = []
    pending_paths: list[str] = []
    if force:
        pending_paths = pdf_paths
    else:
        for pdf_path in pdf_paths:
            if has_json_cache_file(pdf_path, model=model):
                cached_paths.append(pdf_path)
                results[pdf_path] = "cached"
            else:
                pending_paths.append(pdf_path)

    logger.info(
        f"[OCR ASYNC] 开始批量处理默认目录，共 {total} 个 PDF | "
        f"已缓存跳过={len(cached_paths)} | 待提交={len(pending_paths)} | workers={max_workers}"
    )

    for pdf_path in cached_paths:
        logger.info(f"[OCR ASYNC] 跳过已有缓存: {os.path.basename(pdf_path)}")

    if pending_paths:
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ocr") as executor:
            future_to_pdf = {
                executor.submit(
                    _convert_single_pdf_task,
                    pdf_path,
                    force=force,
                    save=save,
                    model=model,
                    optional_payload=optional_payload,
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
                    logger.error(f"[OCR ASYNC] 处理失败 {os.path.basename(pdf_path)}: {e}")
                    result_pdf_path, status = pdf_path, "failed"
                results[result_pdf_path] = status
                logger.info(
                    f"[OCR ASYNC] 进度 {completed}/{len(pending_paths)} | "
                    f"{status.upper()} | {os.path.basename(result_pdf_path)}"
                )

    saved = sum(1 for status in results.values() if status == "saved")
    cached = sum(1 for status in results.values() if status == "cached")
    locked = sum(1 for status in results.values() if status == "locked")
    failed = sum(1 for status in results.values() if status == "failed")
    logger.info(
        f"[OCR ASYNC] 批量处理完成: saved={saved}, cached={cached}, locked={locked}, failed={failed}"
    )
    return results


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="PaddleOCR 异步版 JSON 提取脚本")
    parser.add_argument("pdf_path", nargs="?", help="待解析 PDF 路径")
    parser.add_argument("--force", action="store_true", help="忽略本地缓存，强制重新解析")
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="只返回结果，不保存 JSON 文件",
    )
    parser.add_argument(
        "--list-default-pdfs",
        action="store_true",
        help="列出默认测试目录中的 PDF，不执行解析",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        help=f"批量处理并发数，默认 {DEFAULT_MAX_WORKERS}",
    )
    parser.add_argument(
        "--clear-locks",
        action="store_true",
        help="先清理默认目录下遗留的 .lock 文件，再继续执行",
    )
    args = parser.parse_args()

    if args.clear_locks:
        clear_lock_files()

    if args.list_default_pdfs or not args.pdf_path:
        if args.list_default_pdfs:
            pdfs = list_report_pdfs()
            print("默认测试目录中的 PDF：")
            for path in pdfs:
                print(path)
            print()
            print("用法示例：")
            if pdfs:
                print(f'python "{Path(__file__).resolve()}" "{pdfs[0]}"')
                print(f'python "{Path(__file__).resolve()}" "{pdfs[0]}" --force')
            else:
                print("python ocr_async_json.py <pdf_path>")
            return

        # 直接运行且未传参：默认批量处理两个测试目录
        results = convert_pdfs_in_default_dirs(
            force=args.force,
            save=not args.no_save,
            max_workers=args.workers,
        )
        print("批量处理结果：")
        for pdf_path, status in results.items():
            print(f"[{status}] {pdf_path}")
        if not results:
            print("默认目录下未找到 PDF")
        else:
            print("已处理默认目录中的 PDF，并将 JSON 保存到原 PDF 路径旁。")
        return

    pdf_path = str(Path(args.pdf_path).resolve())
    payload = convert_pdf_to_async_json(
        pdf_path,
        force=args.force,
        save=not args.no_save,
    )

    if isinstance(payload, list):
        logger.info(f"[OCR ASYNC] 完成，共返回 {len(payload)} 条页面记录")
    else:
        logger.info("[OCR ASYNC] 完成")


if __name__ == "__main__":
    main()
