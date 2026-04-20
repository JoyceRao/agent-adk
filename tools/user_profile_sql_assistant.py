import os
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from .db_constants import DB_CONFIG
from .user_profile_sql_api_assistant import user_profile_sql_api_assistant

USER_PROFILE_SQL_TEMPLATE = (
    "select dt, app_id, platform, device_id, user_id, model, app_version, os_version "
    "from gzlc_real.fact_wuxian_api_event_v2 "
    "where dt = '{dt}' "
    "and user_id = '{user_id}' "
    "and app_id = {app_id} "
    "and LENGTH(device_id) > 0 "
    "limit 1"
)


def _load_db_config() -> dict[str, str]:
    return {
        "url": (os.getenv("USER_PROFILE_DB_URL") or DB_CONFIG.get("url") or "").strip(),
        "username": (
            os.getenv("USER_PROFILE_DB_USERNAME") or DB_CONFIG.get("username") or ""
        ).strip(),
        "password": (
            os.getenv("USER_PROFILE_DB_PASSWORD") or DB_CONFIG.get("password") or ""
        ).strip(),
        "group": (os.getenv("USER_PROFILE_DB_GROUP") or DB_CONFIG.get("group") or "").strip(),
    }


def _validate_dt(dt: str) -> str:
    value = str(dt or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        raise ValueError("dt 格式不合法，必须为 YYYY-MM-DD。")
    datetime.strptime(value, "%Y-%m-%d")
    return value


def _validate_user_id(user_id: str) -> str:
    value = str(user_id or "").strip()
    if not value:
        raise ValueError("user_id 不能为空。")
    return value


def _validate_app_id(app_id: int | str) -> int:
    try:
        value = int(app_id)
    except Exception as exc:
        raise ValueError("app_id 必须是整数，且仅支持 20(iOS) 或 21(Android)。") from exc
    if value not in (20, 21):
        raise ValueError("app_id 仅支持 20(iOS) 或 21(Android)。")
    return value


def _parse_mysql_jdbc_url(jdbc_url: str) -> tuple[str, int, str]:
    raw = str(jdbc_url or "").strip()
    if not raw.startswith("jdbc:mysql://"):
        raise ValueError("仅支持 jdbc:mysql:// 开头的 URL。")
    parsed = urlparse(raw.replace("jdbc:", "", 1))
    host = parsed.hostname or ""
    port = int(parsed.port or 3306)
    database = parsed.path.lstrip("/")
    if not host or not database:
        raise ValueError("数据库 URL 缺少 host 或 database。")
    return host, port, database


def _mask_device_id(device_id: str) -> str:
    raw = str(device_id or "")
    if len(raw) <= 6:
        return "***" if raw else ""
    return f"{raw[:4]}***{raw[-2:]}"


def _row_get(row: dict[str, Any], key: str, default: Any = "") -> Any:
    lower_key = key.lower()
    for k, v in row.items():
        if str(k).strip().lower() == lower_key:
            return v
    return default


def _to_profile(row: dict[str, Any]) -> dict[str, Any]:
    app_id_raw = _row_get(row, "app_id", 0)
    try:
        app_id_value = int(app_id_raw or 0)
    except Exception:
        app_id_value = 0
    device_id_value = str(_row_get(row, "device_id", ""))
    return {
        "dt": str(_row_get(row, "dt", "")),
        "app_id": app_id_value,
        "platform": str(_row_get(row, "platform", "")),
        "device_id": device_id_value,
        "device_id_masked": _mask_device_id(device_id_value),
        "user_id": str(_row_get(row, "user_id", "")),
        "model": str(_row_get(row, "model", "")),
        "app_version": str(_row_get(row, "app_version", "")),
        "os_version": str(_row_get(row, "os_version", "")),
    }


def _sql_literal(value: str) -> str:
    return str(value).replace("'", "''")


def _build_final_sql(dt: str, user_id: str, app_id: int) -> str:
    return USER_PROFILE_SQL_TEMPLATE.format(
        dt=_sql_literal(dt),
        user_id=_sql_literal(user_id),
        app_id=int(app_id),
    )


def query_user_profile_by_sql(
    dt: str,
    user_id: str,
    app_id: int | str,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """按 dt + user_id + app_id 查询用户画像信息（Python 版本）。

    说明：
    1) 连接配置默认读取 tools/db_constants.py，也支持环境变量覆盖：
       USER_PROFILE_DB_URL / USER_PROFILE_DB_USERNAME / USER_PROFILE_DB_PASSWORD / USER_PROFILE_DB_GROUP
    2) 查询顺序：优先 MySQL 直连（pymysql 或 mysql-connector-python），失败后回退 SQL API。
    3) SQL 执行对齐 Java Statement.executeQuery(finalSQL) 风格：
       先做入参校验和字面量转义，再执行完整 SQL 字符串。
    """
    try:
        normalized_dt = _validate_dt(dt)
        normalized_user_id = _validate_user_id(user_id)
        normalized_app_id = _validate_app_id(app_id)
    except Exception as exc:
        return {
            "ok": False,
            "error_code": "INVALID_ARGUMENT",
            "message": str(exc),
            "params": {
                "dt": str(dt or ""),
                "user_id": str(user_id or ""),
                "app_id": app_id,
            },
        }
    db_cfg = _load_db_config()

    final_sql = _build_final_sql(normalized_dt, normalized_user_id, normalized_app_id)
    params = {
        "dt": normalized_dt,
        "user_id": normalized_user_id,
        "app_id": normalized_app_id,
    }

    mysql_attempt_reason: dict[str, Any] = {
        "ok": False,
        "error_code": "",
        "message": "",
    }

    connector_name = ""
    conn = None
    cursor = None
    row: dict[str, Any] | None = None
    try:
        if not db_cfg.get("url"):
            mysql_attempt_reason = {
                "ok": False,
                "error_code": "DB_CONFIG_MISSING",
                "message": "数据库 URL 未配置。",
            }
        else:
            try:
                host, port, database = _parse_mysql_jdbc_url(db_cfg["url"])
            except Exception as exc:
                mysql_attempt_reason = {
                    "ok": False,
                    "error_code": "DB_URL_PARSE_ERROR",
                    "message": str(exc),
                    "db_url": db_cfg.get("url", ""),
                }
            else:
                try:
                    try:
                        import pymysql  # type: ignore

                        connector_name = "pymysql"
                        conn = pymysql.connect(
                            host=host,
                            port=port,
                            user=db_cfg["username"],
                            password=db_cfg["password"],
                            database=database,
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
                            "host": host,
                            "port": port,
                            "user": db_cfg["username"],
                            "password": db_cfg["password"],
                            "database": database,
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
                    mysql_attempt_reason = {
                        "ok": False,
                        "error_code": "DB_DRIVER_MISSING",
                        "message": "未安装数据库驱动，请安装 pymysql 或 mysql-connector-python。",
                    }
                except Exception as exc:
                    mysql_attempt_reason = {
                        "ok": False,
                        "error_code": "DB_QUERY_FAILED",
                        "message": str(exc),
                        "connector": connector_name,
                    }
                else:
                    if row:
                        profile = _to_profile(row)
                        return {
                            "ok": True,
                            "source": "mysql_direct",
                            "connector": connector_name,
                            "db_group": db_cfg.get("group", ""),
                            "params": params,
                            "sql_preview": final_sql,
                            "api_attempt": {
                                "ok": False,
                                "error_code": "SKIPPED",
                                "message": "MySQL 直连成功，未调用 SQL API。",
                            },
                            "user_profile": profile,
                        }
                    mysql_attempt_reason = {
                        "ok": False,
                        "error_code": "USER_PROFILE_NOT_FOUND",
                        "message": "MySQL 未查询到用户信息，请核对 dt/user_id/app_id。",
                        "connector": connector_name,
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

    api_attempt = user_profile_sql_api_assistant(
        sql=final_sql,
        timeout_seconds=timeout_seconds,
    )
    if api_attempt.get("ok"):
        api_row = api_attempt.get("row", {}) or {}
        return {
            "ok": True,
            "source": "sql_api_assistant",
            "connector": "http",
            "db_group": db_cfg.get("group", ""),
            "params": params,
            "sql_preview": final_sql,
            "api_http_status": api_attempt.get("http_status", 0),
            "api_url": api_attempt.get("api_url", ""),
            "mysql_attempt": mysql_attempt_reason,
            "user_profile": _to_profile(api_row),
        }

    api_failure = {
        "ok": False,
        "error_code": str(api_attempt.get("error_code", "")),
        "message": str(api_attempt.get("message", "")),
        "http_status": api_attempt.get("http_status", 0),
        "api_url": api_attempt.get("api_url", ""),
    }

    result = {
        "ok": False,
        "error_code": str(mysql_attempt_reason.get("error_code", "USER_PROFILE_QUERY_FAILED")),
        "message": str(mysql_attempt_reason.get("message", "用户画像查询失败。")),
        "sql_preview": final_sql,
        "api_attempt": api_failure,
        "params": params,
    }
    if "db_url" in mysql_attempt_reason:
        result["db_url"] = mysql_attempt_reason.get("db_url", "")
    if "connector" in mysql_attempt_reason:
        result["connector"] = mysql_attempt_reason.get("connector", "")
    return result
