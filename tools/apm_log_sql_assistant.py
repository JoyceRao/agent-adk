import os
import re
from datetime import datetime
from typing import Any

from .db_constants import LOG_FILE_SQL_CONFIG

APM_LOG_SQL_TEMPLATE = (
    "select app_id, app_version, device_id, log_file_name, log_date "
    "from logan_task "
    "where 1 = 1 "
    "and app_id = {app_id} "
    "and device_id = '{device_id}' "
    "and app_version = '{app_version}' "
    "and log_date >= UNIX_TIMESTAMP('{dt}') * 1000 "
    "and log_date < UNIX_TIMESTAMP('{dt}') * 1000 + 86400 * 1000 "
    "limit 1"
)


def _load_log_file_sql_config() -> dict[str, Any]:
    raw_port = os.getenv("APM_LOG_DB_PORT") or LOG_FILE_SQL_CONFIG.get("port") or 0
    try:
        parsed_port = int(raw_port)
    except Exception:
        parsed_port = 0
    return {
        "host": (os.getenv("APM_LOG_DB_HOST") or LOG_FILE_SQL_CONFIG.get("host") or "").strip(),
        "port": parsed_port,
        "user": (os.getenv("APM_LOG_DB_USER") or LOG_FILE_SQL_CONFIG.get("user") or "").strip(),
        "password": (
            os.getenv("APM_LOG_DB_PASSWORD") or LOG_FILE_SQL_CONFIG.get("password") or ""
        ).strip(),
        "database": (
            os.getenv("APM_LOG_DB_DATABASE") or LOG_FILE_SQL_CONFIG.get("database") or ""
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
    lower_key = key.lower()
    for k, v in row.items():
        if str(k).strip().lower() == lower_key:
            return v
    return default


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


def _build_final_sql(dt: str, app_id: int, device_id: str, app_version: str) -> str:
    return APM_LOG_SQL_TEMPLATE.format(
        dt=_sql_literal(dt),
        app_id=int(app_id),
        device_id=_sql_literal(device_id),
        app_version=_sql_literal(app_version),
    )


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
    final_sql = _build_final_sql(
        dt=normalized_dt,
        app_id=normalized_app_id,
        device_id=normalized_device_id,
        app_version=normalized_app_version,
    )
    params = {
        "dt": normalized_dt,
        "app_id": normalized_app_id,
        "device_id": normalized_device_id,
        "app_version": normalized_app_version,
    }

    if (
        not db_cfg.get("host")
        or not db_cfg.get("user")
        or not db_cfg.get("database")
        or int(db_cfg.get("port") or 0) <= 0
    ):
        return {
            "ok": False,
            "error_code": "DB_CONFIG_MISSING",
            "message": "日志数据库配置缺失，请检查 LOG_FILE_SQL_CONFIG 或 APM_LOG_DB_* 环境变量。",
            "params": params,
            "sql_preview": final_sql,
        }

    connector_name = ""
    conn = None
    cursor = None
    row: dict[str, Any] | None = None
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
        return {
            "ok": False,
            "error_code": "DB_DRIVER_MISSING",
            "message": "未安装数据库驱动，请安装 pymysql 或 mysql-connector-python。",
            "params": params,
            "sql_preview": final_sql,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error_code": "DB_QUERY_FAILED",
            "message": str(exc),
            "connector": connector_name,
            "params": params,
            "sql_preview": final_sql,
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

    return {
        "ok": False,
        "error_code": "LOG_RECORD_NOT_FOUND",
        "message": "未查询到匹配的日志文件记录，请核对 dt/app_id/device_id/app_version。",
        "connector": connector_name,
        "params": params,
        "sql_preview": final_sql,
    }
