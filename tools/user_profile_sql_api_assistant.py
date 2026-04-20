import json
import os
import re
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


def _split_select_items(select_clause: str) -> list[str]:
    items: list[str] = []
    buf: list[str] = []
    depth = 0
    in_single = False
    in_double = False
    for ch in select_clause:
        if ch == "'" and not in_double:
            in_single = not in_single
            buf.append(ch)
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            buf.append(ch)
            continue
        if in_single or in_double:
            buf.append(ch)
            continue
        if ch == "(":
            depth += 1
            buf.append(ch)
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            buf.append(ch)
            continue
        if ch == "," and depth == 0:
            item = "".join(buf).strip()
            if item:
                items.append(item)
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        items.append(tail)
    return items


def _normalize_identifier(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return value
    if "." in value:
        value = value.split(".")[-1].strip()
    if (
        (value.startswith("`") and value.endswith("`"))
        or (value.startswith('"') and value.endswith('"'))
        or (value.startswith("[") and value.endswith("]"))
    ):
        value = value[1:-1].strip()
    return value


def _extract_sql_columns(sql_text: str) -> list[str]:
    sql = str(sql_text or "").strip()
    if not sql:
        return []
    matched = re.search(r"\bselect\b(.*?)\bfrom\b", sql, flags=re.IGNORECASE | re.DOTALL)
    if not matched:
        return []
    select_clause = matched.group(1)
    result: list[str] = []
    for expr in _split_select_items(select_clause):
        alias_match = re.search(r"\bas\s+([`\"\[\]\w.]+)\s*$", expr, flags=re.IGNORECASE)
        if alias_match:
            name = _normalize_identifier(alias_match.group(1))
        else:
            plain_alias_match = re.search(r"\s+([`\"\[\]\w.]+)\s*$", expr)
            if plain_alias_match and "(" in expr:
                name = _normalize_identifier(plain_alias_match.group(1))
            else:
                name = _normalize_identifier(expr)
        if name:
            result.append(name)
    return result


def _extract_payload_columns(value: Any) -> list[str]:
    if isinstance(value, list):
        if value and all(isinstance(item, str) for item in value):
            return [str(item).strip() for item in value if str(item).strip()]
        if value and all(isinstance(item, dict) for item in value):
            cols: list[str] = []
            for item in value:
                for key in ("name", "column", "field", "label"):
                    if key in item and str(item.get(key, "")).strip():
                        cols.append(str(item.get(key, "")).strip())
                        break
            if cols:
                return cols
    if isinstance(value, dict):
        for key in ("columns", "columnNames", "fields", "headers"):
            if key in value:
                cols = _extract_payload_columns(value.get(key))
                if cols:
                    return cols
        for child in value.values():
            cols = _extract_payload_columns(child)
            if cols:
                return cols
    return []


def _coerce_row_dict(row: Any, columns: list[str]) -> Optional[dict[str, Any]]:
    if isinstance(row, dict):
        return row
    if isinstance(row, (list, tuple)):
        if not columns:
            return None
        mapped: dict[str, Any] = {}
        for idx, value in enumerate(row):
            if idx >= len(columns):
                break
            mapped[str(columns[idx])] = value
        return mapped if mapped else None
    return None


def _find_first_row(value: Any, columns_hint: list[str]) -> Optional[dict[str, Any]]:
    if isinstance(value, dict):
        if _looks_like_user_profile_row(value):
            return value
        payload_columns = _extract_payload_columns(value)
        effective_columns = payload_columns or columns_hint
        preferred = ["data", "result", "rows", "list", "records", "obj", "content"]
        for key in preferred:
            if key in value:
                child = value.get(key)
                child_row = _coerce_row_dict(child, effective_columns)
                if child_row is not None and _looks_like_user_profile_row(child_row):
                    return child_row
                found = _find_first_row(child, effective_columns)
                if found is not None:
                    return found
        for child in value.values():
            found = _find_first_row(child, effective_columns)
            if found is not None:
                return found
        return None
    if isinstance(value, list):
        row_as_dict = _coerce_row_dict(value, columns_hint)
        if row_as_dict is not None and _looks_like_user_profile_row(row_as_dict):
            return row_as_dict
        for child in value:
            child_as_dict = _coerce_row_dict(child, columns_hint)
            if child_as_dict is not None and _looks_like_user_profile_row(child_as_dict):
                return child_as_dict
            found = _find_first_row(child, columns_hint)
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

    sql_columns = _extract_sql_columns(sql_text=sql_text)
    row = _find_first_row(parsed, columns_hint=sql_columns)
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
