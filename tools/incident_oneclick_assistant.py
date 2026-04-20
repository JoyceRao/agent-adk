from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .apm_log_sql_assistant import apm_log_sql_assistant
from .crisp_l_report_assistant import analyze_and_generate_report
from .db_constants import LOG_DOWNLOAD_URL
from .download_url_assistant import download_url_assistant
from .log_filter_assistant import filter_logs
from .source_correlation_assistant import analyze_log_with_source
from .shared import _abs_path
from .user_profile_sql_assistant import query_user_profile_by_sql


_START_LIVE_HINTS = (
    "开播",
    "startlive",
    "start_live",
    "live flow",
    "flowid",
    "csp_biz_watchcar_startlive",
)


def _local_tzinfo():
    return datetime.now().astimezone().tzinfo


def _to_ms(dt_obj: datetime) -> int:
    return int(dt_obj.timestamp() * 1000)


def _parse_date_token(raw: str) -> date:
    normalized = raw.replace("/", "-").replace(".", "-").strip()
    return datetime.strptime(normalized, "%Y-%m-%d").date()


def _day_window_ms(d: date) -> tuple[int, int]:
    tzinfo = _local_tzinfo()
    start_dt = datetime.combine(d, time(0, 0, 0), tzinfo=tzinfo)
    end_dt = datetime.combine(d, time(23, 59, 59, 999000), tzinfo=tzinfo)
    return (_to_ms(start_dt), _to_ms(end_dt))


def _parse_hms(raw: str) -> tuple[int, int, int]:
    text = raw.strip()
    parts = text.split(":")
    if len(parts) == 2:
        hh, mm = int(parts[0]), int(parts[1])
        ss = 0
    elif len(parts) == 3:
        hh, mm, ss = int(parts[0]), int(parts[1]), int(parts[2])
    else:
        raise ValueError("时间格式不合法")
    if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
        raise ValueError("时间范围不合法")
    return (hh, mm, ss)


def _extract_user_id(text: str) -> str:
    patterns = [
        r"(?i)\buser[_\s-]?id\s*[:=：]?\s*([0-9A-Za-z_-]+)",
        r"(?i)\buid\s*[:=：]?\s*([0-9A-Za-z_-]+)",
        r"用户(?:id)?\s*[:=：]?\s*([0-9A-Za-z_-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


def _extract_app_id(text: str) -> tuple[Optional[int], str]:
    app_id_match = re.search(r"(?i)\bapp[_\s-]?id\s*[:=：]?\s*(\d+)", text)
    if app_id_match:
        value = int(app_id_match.group(1))
        if value in (20, 21):
            return (value, "ios" if value == 20 else "android")
        return (None, "")

    has_ios = bool(re.search(r"(?i)\bios\b|苹果|iphone", text))
    has_android = bool(re.search(r"(?i)\bandroid\b|安卓", text))

    if has_ios and not has_android:
        return (20, "ios")
    if has_android and not has_ios:
        return (21, "android")
    return (None, "")


def _extract_dt_and_range(text: str) -> tuple[str, Optional[int], Optional[int]]:
    tzinfo = _local_tzinfo()

    # 1) 完整日期 + 时间范围，例如：2026-04-17 10:00~12:00
    same_day_range_pattern = (
        r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})\s+"
        r"(\d{1,2}:\d{2}(?::\d{2})?)\s*(?:~|-|—|到|至)\s*"
        r"(\d{1,2}:\d{2}(?::\d{2})?)"
    )
    match = re.search(same_day_range_pattern, text)
    if match:
        d = _parse_date_token(match.group(1))
        h1, m1, s1 = _parse_hms(match.group(2))
        h2, m2, s2 = _parse_hms(match.group(3))
        start_dt = datetime.combine(d, time(h1, m1, s1), tzinfo=tzinfo)
        end_dt = datetime.combine(d, time(h2, m2, s2), tzinfo=tzinfo)
        if end_dt < start_dt:
            end_dt = end_dt + timedelta(days=1)
        return (d.strftime("%Y-%m-%d"), _to_ms(start_dt), _to_ms(end_dt))

    # 2) 完整起止时间，例如：2026-04-17 10:00 至 2026-04-17 12:00
    full_range_pattern = (
        r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)\s*"
        r"(?:~|-|—|到|至)\s*"
        r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)"
    )
    match = re.search(full_range_pattern, text)
    if match:
        dt1 = _parse_datetime_token(match.group(1))
        dt2 = _parse_datetime_token(match.group(2))
        start_dt = dt1.replace(tzinfo=tzinfo)
        end_dt = dt2.replace(tzinfo=tzinfo)
        if end_dt < start_dt:
            end_dt = end_dt + timedelta(days=1)
        return (start_dt.strftime("%Y-%m-%d"), _to_ms(start_dt), _to_ms(end_dt))

    # 3) 仅日期（默认整天）
    date_match = re.search(r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})", text)
    if date_match:
        d = _parse_date_token(date_match.group(1))
        start_ms, end_ms = _day_window_ms(d)
        return (d.strftime("%Y-%m-%d"), start_ms, end_ms)

    # 4) 相对日期（今天/昨天/前天）
    now = datetime.now().astimezone()
    base_day = now.date()
    relative_map = {
        "今天": 0,
        "今日": 0,
        "yesterday": -1,
        "昨天": -1,
        "昨日": -1,
        "前天": -2,
        "today": 0,
    }
    lowered = text.lower()
    for token, delta in relative_map.items():
        if token in lowered or token in text:
            d = base_day + timedelta(days=delta)
            start_ms, end_ms = _day_window_ms(d)
            return (d.strftime("%Y-%m-%d"), start_ms, end_ms)

    return ("", None, None)


def _parse_datetime_token(raw: str) -> datetime:
    normalized = raw.replace("/", "-").replace(".", "-").strip()
    formats = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")
    for fmt in formats:
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析时间: {raw}")


def _clean_problem_desc(text: str) -> str:
    cleaned = text
    strip_patterns = [
        r"(?i)\buser[_\s-]?id\s*[:=：]?\s*[0-9A-Za-z_-]+",
        r"(?i)\buid\s*[:=：]?\s*[0-9A-Za-z_-]+",
        r"用户(?:id)?\s*[:=：]?\s*[0-9A-Za-z_-]+",
        r"(?i)\bapp[_\s-]?id\s*[:=：]?\s*\d+",
        r"今天|今日|昨天|昨日|前天|(?i:today)|(?i:yesterday)",
        r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?",
        r"\d{1,2}:\d{2}(?::\d{2})?\s*(?:~|-|—|到|至)\s*\d{1,2}:\d{2}(?::\d{2})?",
        r"(?i)\bios\b|苹果|iphone",
        r"(?i)\bandroid\b|安卓",
    ]
    for pattern in strip_patterns:
        cleaned = re.sub(pattern, " ", cleaned)

    cleaned = re.sub(r"[，,。；;]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    marker_match = re.search(r"(?:发生|出现|遇到|存在)(.+)$", cleaned)
    if marker_match:
        tail = marker_match.group(1).strip()
        if tail:
            cleaned = tail

    cleaned = re.sub(r"^(问题|故障|异常|报错)\s*[:：]?\s*", "", cleaned).strip()
    cleaned = re.sub(r"^[：:，,\-\s]+", "", cleaned).strip()
    if not cleaned:
        cleaned = "问题待分析"
    return cleaned


def _build_keywords_hint(problem_desc: str) -> list[str]:
    normalized = problem_desc.lower()
    hints: list[str] = []

    if any(token in normalized for token in _START_LIVE_HINTS):
        hints.extend(["开播", "startLive", "flowId", "CSP_BIZ_WATCHCAR_STARTLIVE"])

    keyword_dict = {
        "闪退": ["crash", "applicationWillTerminate"],
        "崩溃": ["crash", "applicationWillTerminate"],
        "网络": ["kCFErrorDomainCFNetwork错误310", "RN_NET"],
        "超时": ["timeout", "Task orphaned"],
        "白屏": ["reactnative_exception", "RN_NET"],
    }
    for token, extra_hints in keyword_dict.items():
        if token in problem_desc:
            hints.extend(extra_hints)

    if problem_desc and problem_desc != "问题待分析":
        hints.append(problem_desc)

    seen: set[str] = set()
    output: list[str] = []
    for item in hints:
        norm = item.strip()
        if not norm:
            continue
        key = norm.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(norm)
    return output[:8]


def _is_start_live_problem(problem_desc: str, incident_text: str) -> bool:
    text = f"{problem_desc} {incident_text}".lower()
    return any(token in text for token in _START_LIVE_HINTS)


def parse_incident_text(incident_text: str) -> dict[str, Any]:
    """解析自然语言问题描述并产出 SQL 所需参数。"""
    raw = str(incident_text or "").strip()
    if not raw:
        return {
            "ok": False,
            "error_code": "INVALID_ARGUMENT",
            "message": "incident_text 不能为空。",
            "missing_fields": ["dt", "user_id", "app_id"],
            "parsed_incident": {},
        }

    dt, start_ts_ms, end_ts_ms = _extract_dt_and_range(raw)
    user_id = _extract_user_id(raw)
    app_id, app_name = _extract_app_id(raw)
    problem_desc = _clean_problem_desc(raw)
    keywords_hint = _build_keywords_hint(problem_desc)

    missing_fields: list[str] = []
    if not dt:
        missing_fields.append("dt")
    if not user_id:
        missing_fields.append("user_id")
    if app_id is None:
        missing_fields.append("app_id")

    parse_confidence = 0.15
    parse_confidence += 0.3 if dt else 0.0
    parse_confidence += 0.3 if user_id else 0.0
    parse_confidence += 0.2 if app_id is not None else 0.0
    parse_confidence += 0.05 if problem_desc and problem_desc != "问题待分析" else 0.0

    parsed = {
        "dt": dt,
        "user_id": user_id,
        "app_id": app_id,
        "app_name": app_name,
        "start_ts_ms": start_ts_ms,
        "end_ts_ms": end_ts_ms,
        "problem_desc": problem_desc,
        "keywords_hint": keywords_hint,
        "parse_confidence": round(min(parse_confidence, 1.0), 2),
    }

    if app_id is None and re.search(r"(?i)\bapp[_\s-]?id\s*[:=：]?\s*\d+", raw):
        return {
            "ok": False,
            "error_code": "INVALID_ARGUMENT",
            "message": "app_id 仅支持 20(iOS) 或 21(Android)。",
            "missing_fields": ["app_id"],
            "parsed_incident": parsed,
        }

    if missing_fields:
        return {
            "ok": False,
            "error_code": "MISSING_REQUIRED_FIELDS",
            "message": "缺少必要参数，至少需要 dt/user_id/app_id。",
            "missing_fields": missing_fields,
            "parsed_incident": parsed,
        }

    if app_id not in (20, 21):
        return {
            "ok": False,
            "error_code": "INVALID_ARGUMENT",
            "message": "app_id 仅支持 20(iOS) 或 21(Android)。",
            "missing_fields": ["app_id"],
            "parsed_incident": parsed,
        }

    return {
        "ok": True,
        "parsed_incident": parsed,
    }


def _merge_keywords(items: list[str]) -> str:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        normalized = str(item or "").strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(normalized)
    return ",".join(output)


def _build_report_path(log_path: str, output_dir: str) -> str:
    report_filename = f"{Path(log_path).stem}.md"
    return str(Path(_abs_path(output_dir)) / report_filename)


def _build_log_download_url(log_file_name: str) -> str:
    base_url = str(LOG_DOWNLOAD_URL or "").strip()
    if not base_url:
        raise ValueError("LOG_DOWNLOAD_URL 未配置。")

    normalized_log_file_name = str(log_file_name or "").strip()
    if not normalized_log_file_name:
        raise ValueError("log_file_name 不能为空。")

    split_result = urlsplit(base_url)
    if not split_result.scheme or not split_result.netloc:
        raise ValueError("LOG_DOWNLOAD_URL 格式不合法。")

    query_items = dict(parse_qsl(split_result.query, keep_blank_values=True))
    query_items["name"] = normalized_log_file_name
    return urlunsplit(
        (
            split_result.scheme,
            split_result.netloc,
            split_result.path,
            urlencode(query_items, safe="/"),
            split_result.fragment,
        )
    )


def _write_no_data_report(
    *,
    incident_text: str,
    parsed_incident: dict[str, Any],
    output_dir: str,
    log_path: str,
) -> tuple[str, str]:
    output_path = Path(_abs_path(output_dir))
    output_path.mkdir(parents=True, exist_ok=True)
    report_filename = f"{Path(log_path).stem}_no_data.md"
    report_path = output_path / report_filename

    lines = [
        "# 日志一键分析报告（无数据）",
        "",
        "## 0. 快速结论",
        "",
        "当前检索条件下未命中日志，建议先核对时间窗、用户标识与问题关键词。",
        "",
        "## 检索输入",
        "",
        f"- incident_text: {incident_text}",
        f"- dt: {parsed_incident.get('dt', '')}",
        f"- user_id: {parsed_incident.get('user_id', '')}",
        f"- app_id: {parsed_incident.get('app_id', '')}",
        f"- start_ts_ms: {parsed_incident.get('start_ts_ms', '')}",
        f"- end_ts_ms: {parsed_incident.get('end_ts_ms', '')}",
        "",
        "## 建议",
        "",
        "1. 放宽关键词（保留问题关键词，去掉设备与版本约束）后重试。",
        "2. 扩大时间窗（建议从 2 小时扩大到 24 小时）后重试。",
        "3. 核对 dt/user_id/app_id 是否与线上日志分区一致。",
        "",
    ]
    report_markdown = "\n".join(lines)
    report_path.write_text(report_markdown, encoding="utf-8")
    return (str(report_path), report_markdown)


def analyze_incident_one_click(
    incident_text: str,
    log_path: str = "",
    source_root: str = "source/GZCheSuPaiApp",
    rule_path: str = "source/log_rule.md",
    output_dir: str = "output",
    start_ts_ms: Optional[int] = None,
    end_ts_ms: Optional[int] = None,
    max_output_lines: int = 1000,
    max_flows: int = 2000,
    include_stage_path: bool = True,
    exclude_last_stage: str = "recover_check_start",
    title: str = "日志分析报告",
) -> dict[str, Any]:
    """一键编排：自然语言解析 -> SQL 用户画像 -> 日志文件 SQL -> 下载本地日志 -> 分析。"""
    parsed_result = parse_incident_text(incident_text)
    if not parsed_result.get("ok", False):
        return {
            "ok": False,
            "stage": "parse_incident_text",
            **parsed_result,
        }

    parsed_incident = parsed_result.get("parsed_incident", {}) or {}
    dt = str(parsed_incident.get("dt", ""))
    user_id = str(parsed_incident.get("user_id", ""))
    app_id = int(parsed_incident.get("app_id", 0) or 0)

    user_profile_result = query_user_profile_by_sql(dt=dt, user_id=user_id, app_id=app_id)
    if not user_profile_result.get("ok", False):
        return {
            "ok": False,
            "stage": "query_user_profile_by_sql",
            "parsed_incident": parsed_incident,
            "sql_params": {
                "dt": dt,
                "user_id": user_id,
                "app_id": app_id,
            },
            "user_profile_query": user_profile_result,
            "error_code": user_profile_result.get("error_code", "USER_PROFILE_QUERY_FAILED"),
            "message": user_profile_result.get("message", "用户画像查询失败。"),
        }

    profile_raw = user_profile_result.get("user_profile", {}) or {}
    profile_for_output = {
        "dt": profile_raw.get("dt", dt),
        "app_id": int(profile_raw.get("app_id", app_id) or app_id),
        "platform": str(profile_raw.get("platform", "")),
        "device_id": str(
            profile_raw.get("device_id_masked", "") or profile_raw.get("device_id", "")
        ),
        "user_id": str(profile_raw.get("user_id", user_id)),
        "model": str(profile_raw.get("model", "")),
        "app_version": str(profile_raw.get("app_version", "")),
        "os_version": str(profile_raw.get("os_version", "")),
    }
    profile_device_id = str(profile_raw.get("device_id", "")).strip()
    profile_app_version = str(profile_raw.get("app_version", "")).strip()

    if not profile_device_id or not profile_app_version:
        return {
            "ok": False,
            "stage": "query_user_profile_by_sql",
            "parsed_incident": parsed_incident,
            "sql_params": {
                "dt": dt,
                "user_id": user_id,
                "app_id": app_id,
            },
            "user_profile": profile_for_output,
            "error_code": "USER_PROFILE_INCOMPLETE",
            "message": "用户画像缺少 device_id 或 app_version，无法继续查询日志文件记录。",
        }

    log_record_query = apm_log_sql_assistant(
        dt=dt,
        app_id=app_id,
        device_id=profile_device_id,
        app_version=profile_app_version,
    )
    if not log_record_query.get("ok", False):
        return {
            "ok": False,
            "stage": "apm_log_sql_assistant",
            "parsed_incident": parsed_incident,
            "sql_params": {
                "dt": dt,
                "user_id": user_id,
                "app_id": app_id,
            },
            "user_profile": profile_for_output,
            "log_record_query": log_record_query,
            "error_code": log_record_query.get("error_code", "LOG_RECORD_QUERY_FAILED"),
            "message": log_record_query.get("message", "日志文件记录查询失败。"),
        }

    log_record = log_record_query.get("log_record", {}) or {}
    log_file_name = str(log_record.get("log_file_name", "")).strip()
    if not log_file_name:
        return {
            "ok": False,
            "stage": "apm_log_sql_assistant",
            "parsed_incident": parsed_incident,
            "sql_params": {
                "dt": dt,
                "user_id": user_id,
                "app_id": app_id,
            },
            "user_profile": profile_for_output,
            "log_record_query": log_record_query,
            "error_code": "LOG_FILE_NAME_MISSING",
            "message": "日志文件记录中未包含 log_file_name。",
        }

    try:
        download_url = _build_log_download_url(log_file_name)
    except Exception as exc:
        return {
            "ok": False,
            "stage": "build_download_url",
            "parsed_incident": parsed_incident,
            "sql_params": {
                "dt": dt,
                "user_id": user_id,
                "app_id": app_id,
            },
            "user_profile": profile_for_output,
            "log_record_query": log_record_query,
            "error_code": "DOWNLOAD_URL_BUILD_FAILED",
            "message": str(exc),
        }

    download_result = download_url_assistant(url=download_url)
    if not download_result.get("ok", False):
        return {
            "ok": False,
            "stage": "download_url_assistant",
            "parsed_incident": parsed_incident,
            "sql_params": {
                "dt": dt,
                "user_id": user_id,
                "app_id": app_id,
            },
            "user_profile": profile_for_output,
            "log_record_query": log_record_query,
            "download_url": download_url,
            "download_result": download_result,
            "error_code": download_result.get("error_code", "LOG_DOWNLOAD_FAILED"),
            "message": download_result.get("message", "日志下载失败。"),
        }

    local_log_path = str(download_result.get("saved_path", "")).strip()
    if not local_log_path or not Path(local_log_path).exists():
        return {
            "ok": False,
            "stage": "download_url_assistant",
            "parsed_incident": parsed_incident,
            "sql_params": {
                "dt": dt,
                "user_id": user_id,
                "app_id": app_id,
            },
            "user_profile": profile_for_output,
            "log_record_query": log_record_query,
            "download_url": download_url,
            "download_result": download_result,
            "error_code": "LOCAL_LOG_NOT_FOUND",
            "message": "日志下载完成但本地文件不存在。",
        }

    log_record_for_output = {
        "app_id": int(log_record.get("app_id", app_id) or app_id),
        "app_version": str(log_record.get("app_version", "")),
        "device_id": str(log_record.get("device_id_masked", "")),
        "log_file_name": log_file_name,
        "log_date": int(log_record.get("log_date", 0) or 0),
    }
    download_summary = {
        "download_url": download_url,
        "download_dir": str(download_result.get("download_dir", "")),
        "filename": str(download_result.get("filename", "")),
        "saved_path": local_log_path,
        "size_bytes": int(download_result.get("size_bytes", 0) or 0),
        "http_status": int(download_result.get("http_status", 0) or 0),
    }

    effective_start_ts = (
        int(start_ts_ms)
        if start_ts_ms is not None
        else parsed_incident.get("start_ts_ms")
    )
    effective_end_ts = (
        int(end_ts_ms)
        if end_ts_ms is not None
        else parsed_incident.get("end_ts_ms")
    )

    problem_desc = str(parsed_incident.get("problem_desc", "")).strip()
    keywords_hint = parsed_incident.get("keywords_hint", []) or []

    full_keywords = _merge_keywords(
        [
            *[str(x) for x in keywords_hint],
            user_id,
            profile_device_id,
            profile_app_version,
            str(profile_raw.get("os_version", "")),
        ]
    )
    relaxed_keywords = _merge_keywords([problem_desc, *[str(x) for x in keywords_hint], user_id])

    initial_filter = filter_logs(
        log_path=local_log_path,
        start_ts_ms=effective_start_ts,
        end_ts_ms=effective_end_ts,
        keywords=full_keywords,
        max_output_lines=max_output_lines,
    )

    active_keywords = full_keywords
    filter_relaxed = False
    filter_result = initial_filter

    if int(initial_filter.get("matched_entries", 0) or 0) <= 0:
        filter_relaxed = True
        relaxed_filter = filter_logs(
            log_path=local_log_path,
            start_ts_ms=effective_start_ts,
            end_ts_ms=effective_end_ts,
            keywords=relaxed_keywords,
            max_output_lines=max_output_lines,
        )
        filter_result = relaxed_filter
        active_keywords = relaxed_keywords

    filter_summary = {
        "total_entries": int(filter_result.get("total_entries", 0) or 0),
        "matched_entries": int(filter_result.get("matched_entries", 0) or 0),
        "returned_entries": int(filter_result.get("returned_entries", 0) or 0),
        "dropped_entries": int(filter_result.get("dropped_entries", 0) or 0),
        "keywords": active_keywords,
        "relaxed_once": filter_relaxed,
    }

    if filter_summary["matched_entries"] <= 0:
        report_path, report_markdown = _write_no_data_report(
            incident_text=incident_text,
            parsed_incident=parsed_incident,
            output_dir=output_dir,
            log_path=local_log_path,
        )
        return {
            "ok": True,
            "selected_skill": "no-data-report",
            "parsed_incident": parsed_incident,
            "sql_params": {
                "dt": dt,
                "user_id": user_id,
                "app_id": app_id,
            },
            "user_profile": profile_for_output,
            "log_record": log_record_for_output,
            "log_download": download_summary,
            "analyzed_log_path": local_log_path,
            "filter_summary": filter_summary,
            "analysis_summary": {
                "anomaly_count": 0,
                "source_hit_count": 0,
            },
            "report_path": report_path,
            "report_preview": report_markdown.splitlines()[:20],
            "report_markdown": report_markdown,
            "message": "日志命中为 0，已生成无数据报告。",
        }

    start_live_mode = _is_start_live_problem(problem_desc=problem_desc, incident_text=incident_text)

    if start_live_mode:
        from .skill_router import route_by_skill

        routed = route_by_skill(
            skill_name="start-live-flow-assistant",
            log_path=local_log_path,
            source_root=source_root,
            rule_path=rule_path,
            start_ts_ms=effective_start_ts,
            end_ts_ms=effective_end_ts,
            c_startswith="1",
            keywords="CSP_BIZ_WATCHCAR_STARTLIVE,flowId",
            max_output_lines=max_output_lines,
            max_flows=max_flows,
            include_stage_path=include_stage_path,
            exclude_last_stage=exclude_last_stage,
            generate_start_live_report=True,
            output_dir=output_dir,
            title="startLive 开播链路日志报告",
        )

        start_live_analysis = routed.get("result", {}) or {}
        flow_count = int(
            (
                (start_live_analysis.get("start_live_analysis", {}) or {})
                .get("summary", {})
                .get("flow_count", 0)
            )
            or 0
        )

        if routed.get("error") or flow_count <= 0:
            fallback_analysis = analyze_log_with_source(
                log_path=local_log_path,
                source_root=source_root,
                rule_path=rule_path,
                start_ts_ms=effective_start_ts,
                end_ts_ms=effective_end_ts,
                keywords=active_keywords,
                max_output_lines=max_output_lines,
            )
            fallback_report_markdown = analyze_and_generate_report(
                log_path=local_log_path,
                source_root=source_root,
                rule_path=rule_path,
                start_ts_ms=effective_start_ts,
                end_ts_ms=effective_end_ts,
                keywords=active_keywords,
                max_output_lines=max_output_lines,
                title=title,
                output_dir=output_dir,
            )
            return {
                "ok": True,
                "selected_skill": "default-log-report",
                "degraded_from": "start-live-flow-assistant",
                "degraded_reason": "START_LIVE_NO_FLOW_ID",
                "parsed_incident": parsed_incident,
                "sql_params": {
                    "dt": dt,
                    "user_id": user_id,
                    "app_id": app_id,
                },
                "user_profile": profile_for_output,
                "log_record": log_record_for_output,
                "log_download": download_summary,
                "analyzed_log_path": local_log_path,
                "filter_summary": filter_summary,
                "analysis_summary": {
                    "anomaly_count": len(fallback_analysis.get("anomalies", []) or []),
                    "source_hit_count": len(fallback_analysis.get("source_correlations", []) or []),
                },
                "report_path": _build_report_path(log_path=local_log_path, output_dir=output_dir),
                "report_preview": fallback_report_markdown.splitlines()[:20],
                "report_markdown": fallback_report_markdown,
            }

        merged_analysis = start_live_analysis.get("merged_analysis", {}) or {}
        return {
            "ok": True,
            "selected_skill": "start-live-flow-assistant",
            "parsed_incident": parsed_incident,
            "sql_params": {
                "dt": dt,
                "user_id": user_id,
                "app_id": app_id,
            },
            "user_profile": profile_for_output,
            "log_record": log_record_for_output,
            "log_download": download_summary,
            "analyzed_log_path": local_log_path,
            "filter_summary": filter_summary,
            "analysis_summary": {
                "anomaly_count": len(merged_analysis.get("anomalies", []) or []),
                "source_hit_count": len(merged_analysis.get("source_correlations", []) or []),
                "flow_count": flow_count,
            },
            "report_path": routed.get("report_path", ""),
            "json_path": routed.get("json_path", ""),
            "report_preview": routed.get("report_preview", []),
            "report_markdown": routed.get("report_markdown", ""),
            "skill_result": routed,
        }

    analysis = analyze_log_with_source(
        log_path=local_log_path,
        source_root=source_root,
        rule_path=rule_path,
        start_ts_ms=effective_start_ts,
        end_ts_ms=effective_end_ts,
        keywords=active_keywords,
        max_output_lines=max_output_lines,
    )
    report_markdown = analyze_and_generate_report(
        log_path=local_log_path,
        source_root=source_root,
        rule_path=rule_path,
        start_ts_ms=effective_start_ts,
        end_ts_ms=effective_end_ts,
        keywords=active_keywords,
        max_output_lines=max_output_lines,
        title=title,
        output_dir=output_dir,
    )

    return {
        "ok": True,
        "selected_skill": "default-log-report",
        "parsed_incident": parsed_incident,
        "sql_params": {
            "dt": dt,
            "user_id": user_id,
            "app_id": app_id,
        },
        "user_profile": profile_for_output,
        "log_record": log_record_for_output,
        "log_download": download_summary,
        "analyzed_log_path": local_log_path,
        "filter_summary": filter_summary,
        "analysis_summary": {
            "anomaly_count": len(analysis.get("anomalies", []) or []),
            "source_hit_count": len(analysis.get("source_correlations", []) or []),
        },
        "report_path": _build_report_path(log_path=local_log_path, output_dir=output_dir),
        "report_preview": report_markdown.splitlines()[:20],
        "report_markdown": report_markdown,
    }
