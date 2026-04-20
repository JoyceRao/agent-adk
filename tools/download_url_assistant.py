import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from .shared import _project_root


HTTP_500_RETRY_WINDOW_SECONDS = 90.0
HTTP_500_MAX_RETRY_COUNT = 60


def _resolve_download_dir() -> Path:
    # 固定到项目目录下，避免 run/web 启动目录差异导致路径漂移。
    return (_project_root() / "source" / "resource").resolve()


def _build_filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    query_name_list = parse_qs(parsed.query).get("name", [])
    if query_name_list:
        query_name = str(query_name_list[0] or "").strip()
        if query_name:
            # 优先使用下载接口显式传入的文件名，避免 path 固定为 downing 导致命名错误。
            return Path(query_name).name

    raw_name = Path(parsed.path).name.strip()
    if raw_name:
        return raw_name
    return f"download_{datetime.now().strftime('%Y%m%d_%H%M%S')}.bin"


def _pick_target_path(download_dir: Path, filename: str) -> Path:
    safe_name = Path(filename).name or "download.bin"
    stem = Path(safe_name).stem
    suffix = Path(safe_name).suffix
    candidate = download_dir / safe_name
    if not candidate.exists():
        return candidate

    index = 1
    while True:
        alt_name = f"{stem}_{index}{suffix}"
        candidate = download_dir / alt_name
        if not candidate.exists():
            return candidate
        index += 1


def download_url_assistant(
    url: str,
    timeout_seconds: int = 360,
) -> dict[str, Any]:
    """下载 URL 文件到 `<project_root>/source/resource` 目录。"""
    raw_url = str(url or "").strip()
    if not raw_url:
        return {
            "ok": False,
            "error_code": "INVALID_ARGUMENT",
            "message": "url 不能为空。",
        }

    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"}:
        return {
            "ok": False,
            "error_code": "INVALID_URL_SCHEME",
            "message": "仅支持 http/https 协议。",
            "url": raw_url,
        }

    download_dir = _resolve_download_dir()
    download_dir.mkdir(parents=True, exist_ok=True)

    filename = _build_filename_from_url(raw_url)
    target_path = _pick_target_path(download_dir=download_dir, filename=filename)

    req = Request(
        raw_url,
        headers={"User-Agent": "Mozilla/5.0 (download_url_assistant)"},
        method="GET",
    )
    request_timeout = max(1, int(timeout_seconds))
    start_monotonic = time.monotonic()
    retry_500_count = 0
    attempt_count = 0
    content = b""
    http_status = 0
    while True:
        attempt_count += 1
        try:
            with urlopen(req, timeout=request_timeout) as resp:
                content = resp.read()
                http_status = int(resp.getcode() or 0)
            if http_status == 500:
                raise HTTPError(
                    url=raw_url,
                    code=500,
                    msg="HTTP Error 500: Internal Server Error",
                    hdrs=None,
                    fp=None,
                )
            break
        except HTTPError as exc:
            status_code = int(getattr(exc, "code", 0) or 0)
            if status_code == 500:
                elapsed_seconds = time.monotonic() - start_monotonic
                within_retry_window = elapsed_seconds < HTTP_500_RETRY_WINDOW_SECONDS
                has_retry_budget = retry_500_count < HTTP_500_MAX_RETRY_COUNT
                if within_retry_window and has_retry_budget:
                    retry_500_count += 1
                    interval_seconds = (
                        HTTP_500_RETRY_WINDOW_SECONDS / float(HTTP_500_MAX_RETRY_COUNT)
                    )
                    remaining_seconds = max(0.0, HTTP_500_RETRY_WINDOW_SECONDS - elapsed_seconds)
                    sleep_seconds = min(interval_seconds, remaining_seconds)
                    if sleep_seconds > 0:
                        time.sleep(sleep_seconds)
                    continue

                return {
                    "ok": False,
                    "error_code": "HTTP_ERROR",
                    "message": str(exc),
                    "url": raw_url,
                    "http_status": status_code,
                    "download_dir": str(download_dir),
                    "attempt_count": attempt_count,
                    "retry_500_count": retry_500_count,
                    "retry_window_seconds": HTTP_500_RETRY_WINDOW_SECONDS,
                    "elapsed_seconds": round(elapsed_seconds, 3),
                }

            return {
                "ok": False,
                "error_code": "HTTP_ERROR",
                "message": str(exc),
                "url": raw_url,
                "http_status": status_code,
                "download_dir": str(download_dir),
                "attempt_count": attempt_count,
                "retry_500_count": retry_500_count,
                "elapsed_seconds": round(time.monotonic() - start_monotonic, 3),
            }
        except URLError as exc:
            return {
                "ok": False,
                "error_code": "URL_ERROR",
                "message": str(exc),
                "url": raw_url,
                "download_dir": str(download_dir),
                "attempt_count": attempt_count,
                "retry_500_count": retry_500_count,
                "elapsed_seconds": round(time.monotonic() - start_monotonic, 3),
            }
        except Exception as exc:
            return {
                "ok": False,
                "error_code": "DOWNLOAD_FAILED",
                "message": str(exc),
                "url": raw_url,
                "download_dir": str(download_dir),
                "attempt_count": attempt_count,
                "retry_500_count": retry_500_count,
                "elapsed_seconds": round(time.monotonic() - start_monotonic, 3),
            }

    with open(target_path, "wb") as f:
        f.write(content)

    return {
        "ok": True,
        "url": raw_url,
        "http_status": http_status,
        "download_dir": str(download_dir),
        "filename": target_path.name,
        "saved_path": str(target_path),
        "size_bytes": int(os.path.getsize(target_path)),
        "attempt_count": attempt_count,
        "retry_500_count": retry_500_count,
        "elapsed_seconds": round(time.monotonic() - start_monotonic, 3),
    }
