import json
import os
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen


DEFAULT_SQL_API_ENDPOINT = "http://performance-service.guazi-cloud.com/statistical/executeSql"


def _safe_preview(text: str, max_len: int = 800) -> str:
    raw = str(text or "")
    if len(raw) <= max_len:
        return raw
    return raw[:max_len] + "...(truncated)"


def _looks_like_user_profile_row(value: dict[str, Any]) -> bool:
    keys = {str(k).strip().lower() for k in value.keys()}
    target = {"dt", "app_id", "platform", "device_id", "user_id", "model", "app_version", "os_version"}
    return len(keys & target) >= 3


def _find_first_row(value: Any) -> Optional[dict[str, Any]]:
    if isinstance(value, dict):
        if _looks_like_user_profile_row(value):
            return value
        preferred = ["data", "result", "rows", "list", "records", "obj", "content"]
        for key in preferred:
            if key in value:
                found = _find_first_row(value.get(key))
                if found is not None:
                    return found
        for child in value.values():
            found = _find_first_row(child)
            if found is not None:
                return found
        return None
    if isinstance(value, list):
        for child in value:
            found = _find_first_row(child)
            if found is not None:
                return found
        return None
    return None


def _build_headers(api_url: str) -> dict[str, str]:
    parsed = urlparse(api_url)
    default_host = parsed.netloc
    return {
        "Host": os.getenv("USER_PROFILE_SQL_API_HOST", default_host),
        "user-agent": os.getenv("USER_PROFILE_SQL_API_USER_AGENT", "Charles/4.6.1"),
        "content-type": "application/json",
    }


def _build_api_url(sql_text: str, api_url: Optional[str] = None) -> str:
    custom_url = (api_url or os.getenv("USER_PROFILE_SQL_API_URL") or "").strip()
    if custom_url:
        return custom_url

    output_payload = json.dumps({"sql": sql_text}, ensure_ascii=False)
    query_params = [
        ("dataSource", "doris"),
        ("business line", "1"),
        ("pid", "2"),
        ("output", output_payload),
    ]
    query_string = urlencode(query_params, quote_via=quote)
    return f"{DEFAULT_SQL_API_ENDPOINT}?{query_string}"


def user_profile_sql_api_assistant(
    sql: str,
    timeout_seconds: int = 20,
    api_url: Optional[str] = None,
) -> dict[str, Any]:
    """通过 SQL API 请求执行 sql 并返回首行结果。"""
    sql_text = str(sql or "").strip()
    if not sql_text:
        return {
            "ok": False,
            "error_code": "INVALID_SQL",
            "message": "sql 不能为空。",
        }

    final_api_url = _build_api_url(sql_text=sql_text, api_url=api_url).strip()
    if not final_api_url:
        return {
            "ok": False,
            "error_code": "API_URL_MISSING",
            "message": "SQL API URL 未配置。",
        }

    payload = json.dumps({"sql": sql_text}, ensure_ascii=False).encode("utf-8")
    headers = _build_headers(final_api_url)
    req = Request(final_api_url, data=payload, headers=headers, method="POST")

    try:
        with urlopen(req, timeout=max(1, int(timeout_seconds))) as resp:
            status_code = int(resp.getcode() or 0)
            body_text = resp.read().decode("utf-8", errors="ignore")
    except HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="ignore")
        except Exception:
            pass
        return {
            "ok": False,
            "error_code": "API_HTTP_ERROR",
            "message": str(exc),
            "http_status": int(getattr(exc, "code", 0) or 0),
            "api_url": final_api_url,
            "response_preview": _safe_preview(body),
        }
    except URLError as exc:
        return {
            "ok": False,
            "error_code": "API_REQUEST_FAILED",
            "message": str(exc),
            "api_url": final_api_url,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error_code": "API_REQUEST_FAILED",
            "message": str(exc),
            "api_url": final_api_url,
        }

    parsed: Any
    try:
        parsed = json.loads(body_text) if body_text.strip() else {}
    except Exception as exc:
        return {
            "ok": False,
            "error_code": "API_RESPONSE_PARSE_ERROR",
            "message": str(exc),
            "http_status": status_code,
            "api_url": final_api_url,
            "response_preview": _safe_preview(body_text),
        }

    row = _find_first_row(parsed)
    if row is None:
        return {
            "ok": False,
            "error_code": "API_EMPTY_RESULT",
            "message": "SQL API 调用成功但未解析到结果行。",
            "http_status": status_code,
            "api_url": final_api_url,
            "response_preview": _safe_preview(body_text),
        }

    return {
        "ok": True,
        "http_status": status_code,
        "api_url": final_api_url,
        "row": row,
        "response_preview": _safe_preview(body_text),
    }
