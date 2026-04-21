import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .db_constants import LOG_FILE_SQL_CONFIG, LOG_TASK_SEARCH_URL

APM_LOG_SQL_TEMPLATE = (
    "select app_id, app_version, device_id, log_file_name, log_date "
    "from logan_task "
    "where 1 = 1 "
    "and app_id = {app_id} "
    "and device_id = '{device_id}' "
    "and app_version = '{app_version}' "
    "and log_date >= {begin_time} "
    "and log_date < {end_time} "
    "limit 1"
)

# 统一按 UTC+08:00 自然日边界将 dt 转为 Unix 毫秒时间戳，避免受运行机器和数据库时区影响。
_QUERY_TZ = timezone(timedelta(hours=8))


def _load_log_file_sql_config() -> dict[str, Any]:
    jdbc_url = (os.getenv("APM_LOG_DB_URL") or LOG_FILE_SQL_CONFIG.get("url") or "").strip()
    parsed_from_url: dict[str, Any] = {}
    if jdbc_url:
        match = re.match(
            r"^jdbc:mysql://(?P<host>[^/:?#]+)(?::(?P<port>\d+))?/(?P<database>[^?;]+)",
            jdbc_url,
        )
        if match:
            parsed_from_url = {
                "host": (match.group("host") or "").strip(),
                "port": int(match.group("port") or 0),
                "database": (match.group("database") or "").strip(),
            }

    raw_port = (
        os.getenv("APM_LOG_DB_PORT")
        or LOG_FILE_SQL_CONFIG.get("port")
        or parsed_from_url.get("port")
        or 0
    )
    try:
        parsed_port = int(raw_port)
    except Exception:
        parsed_port = 0
    return {
        "url": jdbc_url,
        "host": (
            os.getenv("APM_LOG_DB_HOST")
            or LOG_FILE_SQL_CONFIG.get("host")
            or parsed_from_url.get("host")
            or ""
        ).strip(),
        "port": parsed_port,
        "user": (os.getenv("APM_LOG_DB_USER") or LOG_FILE_SQL_CONFIG.get("user") or "").strip(),
        "password": (
            os.getenv("APM_LOG_DB_PASSWORD") or LOG_FILE_SQL_CONFIG.get("password") or ""
        ).strip(),
        "database": (
            os.getenv("APM_LOG_DB_DATABASE")
            or LOG_FILE_SQL_CONFIG.get("database")
            or parsed_from_url.get("database")
            or ""
        ).strip(),
    }


def _validate_dt(dt: str) -> str:
    value = str(dt or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        raise ValueError("dt 格式不合法，必须为 YYYY-MM-DD。")
    datetime.strptime(value, "%Y-%m-%d")
    return value


def _validate_app_id(app_id: int | str) -> int:
    try:
        value = int(app_id)
    except Exception as exc:
        raise ValueError("app_id 必须是整数，且仅支持 20(iOS) 或 21(Android)。") from exc
    if value not in (20, 21):
        raise ValueError("app_id 仅支持 20(iOS) 或 21(Android)。")
    return value


def _validate_device_id(device_id: str) -> str:
    value = str(device_id or "").strip()
    if not value:
        raise ValueError("device_id 不能为空。")
    return value


def _validate_app_version(app_version: str) -> str:
    value = str(app_version or "").strip()
    if not value:
        raise ValueError("app_version 不能为空。")
    return value


def _sql_literal(value: str) -> str:
    return str(value).replace("'", "''")


def _mask_device_id(device_id: str) -> str:
    raw = str(device_id or "")
    if len(raw) <= 6:
        return "***" if raw else ""
    return f"{raw[:4]}***{raw[-2:]}"


def _row_get(row: dict[str, Any], key: str, default: Any = "") -> Any:
    normalized_key = re.sub(r"[^a-z0-9]", "", str(key).strip().lower())
    for k, v in row.items():
        normalized_item_key = re.sub(r"[^a-z0-9]", "", str(k).strip().lower())
        if normalized_item_key == normalized_key:
            return v
    return default


def _camel_to_snake(name: str) -> str:
    first_pass = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", str(name))
    second_pass = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", first_pass)
    return second_pass.replace("-", "_").strip().lower()


def _normalize_log_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for k, v in row.items():
        key = str(k).strip()
        if not key:
            continue
        normalized[key] = v
        normalized[_camel_to_snake(key)] = v
    return normalized


def _to_log_record(row: dict[str, Any]) -> dict[str, Any]:
    app_id_raw = _row_get(row, "app_id", 0)
    log_date_raw = _row_get(row, "log_date", 0)
    device_id_value = str(_row_get(row, "device_id", ""))
    try:
        app_id_value = int(app_id_raw or 0)
    except Exception:
        app_id_value = 0
    try:
        log_date_value = int(log_date_raw or 0)
    except Exception:
        log_date_value = 0
    return {
        "app_id": app_id_value,
        "app_version": str(_row_get(row, "app_version", "")),
        "device_id": device_id_value,
        "device_id_masked": _mask_device_id(device_id_value),
        "log_file_name": str(_row_get(row, "log_file_name", "")),
        "log_date": log_date_value,
    }


def _build_final_sql(
    *,
    app_id: int,
    device_id: str,
    app_version: str,
    begin_time: int,
    end_time: int,
) -> str:
    return APM_LOG_SQL_TEMPLATE.format(
        app_id=int(app_id),
        device_id=_sql_literal(device_id),
        app_version=_sql_literal(app_version),
        begin_time=int(begin_time),
        end_time=int(end_time),
    )


def _dt_to_begin_end_ms(dt: str) -> tuple[int, int]:
    begin_ms = int(datetime.strptime(dt, "%Y-%m-%d").replace(tzinfo=_QUERY_TZ).timestamp() * 1000)
    end_ms = begin_ms + 86400 * 1000
    return begin_ms, end_ms


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _query_log_task_search(
    *,
    device_id: str,
    begin_time: int,
    end_time: int,
    timeout_seconds: int,
    app_id: int,
    app_version: str,
    platform: int = 0,
) -> dict[str, Any]:
    query_string = urlencode(
        {
            "deviceId": str(device_id).strip(),
            "platform": int(platform),
            "beginTime": int(begin_time),
            "endTime": int(end_time),
        }
    )
    request_url = f"{LOG_TASK_SEARCH_URL}?{query_string}"
    request = Request(
        request_url,
        headers={
            "accept": "application/json, text/plain, */*",
        },
        method="GET",
    )
    timeout_value = max(1, int(timeout_seconds))
    try:
        with urlopen(request, timeout=timeout_value) as response:
            response_text = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        return {
            "ok": False,
            "error_code": "TASK_SEARCH_HTTP_ERROR",
            "message": str(exc),
            "request_url": request_url,
            "status_code": int(exc.code or 0),
        }
    except URLError as exc:
        return {
            "ok": False,
            "error_code": "TASK_SEARCH_NETWORK_ERROR",
            "message": str(exc.reason or exc),
            "request_url": request_url,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error_code": "TASK_SEARCH_REQUEST_FAILED",
            "message": str(exc),
            "request_url": request_url,
        }

    try:
        payload = json.loads(response_text or "{}")
    except Exception as exc:
        return {
            "ok": False,
            "error_code": "TASK_SEARCH_RESPONSE_INVALID",
            "message": f"日志查询接口响应不是有效 JSON: {exc}",
            "request_url": request_url,
        }

    code_value = _to_int(payload.get("code"), 0)
    if code_value != 200:
        return {
            "ok": False,
            "error_code": "TASK_SEARCH_API_ERROR",
            "message": str(payload.get("msg") or "日志查询接口返回非成功状态。"),
            "request_url": request_url,
            "api_code": code_value,
        }

    data_list = payload.get("data")
    if not isinstance(data_list, list):
        data_list = []

    same_day_rows: list[dict[str, Any]] = []
    for row in data_list:
        if not isinstance(row, dict):
            continue
        normalized_row = _normalize_log_row(row)
        if _to_int(_row_get(normalized_row, "log_date", 0), 0) == int(begin_time):
            same_day_rows.append(normalized_row)

    matched_rows = [
        item
        for item in same_day_rows
        if _to_int(_row_get(item, "app_id", 0), 0) == int(app_id)
        and str(_row_get(item, "app_version", "")).strip() == str(app_version).strip()
    ]
    if not matched_rows:
        matched_rows = same_day_rows

    if not matched_rows:
        return {
            "ok": False,
            "error_code": "TASK_SEARCH_NOT_FOUND",
            "message": "日志查询接口未返回 logDate=beginTime 的记录。",
            "request_url": request_url,
            "record_count": len(data_list),
        }

    matched_rows.sort(key=lambda item: _to_int(_row_get(item, "add_time", 0), 0), reverse=True)
    selected_row = matched_rows[0]
    return {
        "ok": True,
        "request_url": request_url,
        "record_count": len(data_list),
        "matched_count": len(matched_rows),
        "log_record": _to_log_record(selected_row),
    }


def apm_log_sql_assistant(
    dt: str,
    app_id: int | str,
    device_id: str,
    app_version: str,
    timeout_seconds: int = 360,
) -> dict[str, Any]:
    """按 dt + app_id + device_id + app_version 查询日志文件记录。"""
    try:
        normalized_dt = _validate_dt(dt)
        normalized_app_id = _validate_app_id(app_id)
        normalized_device_id = _validate_device_id(device_id)
        normalized_app_version = _validate_app_version(app_version)
    except Exception as exc:
        return {
            "ok": False,
            "error_code": "INVALID_ARGUMENT",
            "message": str(exc),
            "params": {
                "dt": str(dt or ""),
                "app_id": app_id,
                "device_id": str(device_id or ""),
                "app_version": str(app_version or ""),
            },
        }

    db_cfg = _load_log_file_sql_config()
    begin_time, end_time = _dt_to_begin_end_ms(normalized_dt)
    final_sql = _build_final_sql(
        app_id=normalized_app_id,
        device_id=normalized_device_id,
        app_version=normalized_app_version,
        begin_time=begin_time,
        end_time=end_time,
    )
    params = {
        "dt": normalized_dt,
        "app_id": normalized_app_id,
        "device_id": normalized_device_id,
        "app_version": normalized_app_version,
        "platform": 0,
        "begin_time": begin_time,
        "end_time": end_time,
    }

    connector_name = ""
    conn = None
    cursor = None
    row: dict[str, Any] | None = None
    db_error: dict[str, Any] | None = None

    if (
        not db_cfg.get("host")
        or not db_cfg.get("user")
        or not db_cfg.get("database")
        or int(db_cfg.get("port") or 0) <= 0
    ):
        db_error = {
            "error_code": "DB_CONFIG_MISSING",
            "message": "日志数据库配置缺失，请检查 LOG_FILE_SQL_CONFIG 或 APM_LOG_DB_* 环境变量。",
        }
    else:
        try:
            try:
                import pymysql  # type: ignore

                connector_name = "pymysql"
                conn = pymysql.connect(
                    host=str(db_cfg["host"]),
                    port=int(db_cfg["port"]),
                    user=str(db_cfg["user"]),
                    password=str(db_cfg["password"]),
                    database=str(db_cfg["database"]),
                    charset="utf8mb4",
                    connect_timeout=max(1, int(timeout_seconds)),
                    read_timeout=max(1, int(timeout_seconds)),
                    write_timeout=max(1, int(timeout_seconds)),
                    cursorclass=pymysql.cursors.DictCursor,
                )
                cursor = conn.cursor()
                # 对齐 Java Statement.executeQuery(finalSQL) 风格
                cursor.execute(final_sql)
                row = cursor.fetchone()
            except ModuleNotFoundError:
                import mysql.connector  # type: ignore

                connector_name = "mysql.connector"
                base_conn_kwargs = {
                    "host": str(db_cfg["host"]),
                    "port": int(db_cfg["port"]),
                    "user": str(db_cfg["user"]),
                    "password": str(db_cfg["password"]),
                    "database": str(db_cfg["database"]),
                    "connection_timeout": max(1, int(timeout_seconds)),
                    "autocommit": True,
                }
                clear_password_kwargs = dict(base_conn_kwargs)
                clear_password_kwargs.update(
                    {
                        "auth_plugin": "mysql_clear_password",
                    }
                )
                try:
                    conn = mysql.connector.connect(**clear_password_kwargs)
                except Exception:
                    conn = mysql.connector.connect(**base_conn_kwargs)
                cursor = conn.cursor(dictionary=True)
                # 对齐 Java Statement.executeQuery(finalSQL) 风格
                cursor.execute(final_sql)
                row = cursor.fetchone()
        except ModuleNotFoundError:
            db_error = {
                "error_code": "DB_DRIVER_MISSING",
                "message": "未安装数据库驱动，请安装 pymysql 或 mysql-connector-python。",
            }
        except Exception as exc:
            db_error = {
                "error_code": "DB_QUERY_FAILED",
                "message": str(exc),
            }
        finally:
            try:
                if cursor is not None:
                    cursor.close()
            except Exception:
                pass
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass

    if row:
        return {
            "ok": True,
            "source": "mysql_direct",
            "connector": connector_name,
            "params": params,
            "sql_preview": final_sql,
            "log_record": _to_log_record(row),
        }

    if not db_error:
        db_error = {
            "error_code": "LOG_RECORD_NOT_FOUND",
            "message": "未查询到匹配的日志文件记录，请核对 dt/app_id/device_id/app_version。",
        }

    task_search_result = _query_log_task_search(
        device_id=normalized_device_id,
        begin_time=begin_time,
        end_time=end_time,
        timeout_seconds=timeout_seconds,
        app_id=normalized_app_id,
        app_version=normalized_app_version,
        platform=0,
    )
    if task_search_result.get("ok"):
        return {
            "ok": True,
            "source": "task_search_api",
            "connector": connector_name,
            "fallback_from": db_error.get("error_code"),
            "params": params,
            "sql_preview": final_sql,
            "task_search_request_url": task_search_result.get("request_url", ""),
            "task_search_record_count": _to_int(task_search_result.get("record_count"), 0),
            "task_search_matched_count": _to_int(task_search_result.get("matched_count"), 0),
            "log_record": task_search_result.get("log_record"),
        }

    return {
        "ok": False,
        "error_code": str(db_error.get("error_code") or "LOG_RECORD_NOT_FOUND"),
        "message": str(db_error.get("message") or "未查询到匹配的日志文件记录。"),
        "connector": connector_name,
        "params": params,
        "sql_preview": final_sql,
        "task_search_error": {
            "error_code": task_search_result.get("error_code", "TASK_SEARCH_FAILED"),
            "message": task_search_result.get("message", ""),
            "request_url": task_search_result.get("request_url", ""),
        },
    }
