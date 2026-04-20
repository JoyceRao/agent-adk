import os
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def _resolve_download_dir() -> Path:
    # 按需求固定到 `${pwd}/source/resource`
    return (Path.cwd() / "source" / "resource").resolve()


def _build_filename_from_url(url: str) -> str:
    parsed = urlparse(url)
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
    """下载 URL 文件到 `${pwd}/source/resource` 目录。"""
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
    try:
        with urlopen(req, timeout=max(1, int(timeout_seconds))) as resp:
            content = resp.read()
            http_status = int(resp.getcode() or 0)
    except HTTPError as exc:
        return {
            "ok": False,
            "error_code": "HTTP_ERROR",
            "message": str(exc),
            "url": raw_url,
            "http_status": int(getattr(exc, "code", 0) or 0),
            "download_dir": str(download_dir),
        }
    except URLError as exc:
        return {
            "ok": False,
            "error_code": "URL_ERROR",
            "message": str(exc),
            "url": raw_url,
            "download_dir": str(download_dir),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error_code": "DOWNLOAD_FAILED",
            "message": str(exc),
            "url": raw_url,
            "download_dir": str(download_dir),
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
    }
