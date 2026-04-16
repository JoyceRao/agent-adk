import json
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


@dataclass
class ParsedEntry:
    line_no: int
    timestamp_ms: int
    log_type: int
    thread_name: str
    thread_id: str
    is_main_thread: bool
    content: str
    business_level: str

def _abs_path(path: str) -> str:
    return str(Path(path).expanduser().resolve())


def _resolve_log_path(log_path: str) -> str:
    """解析日志路径。

    规则：
    1) 绝对路径：直接使用；
    2) 相对路径（如 source/resource/xxx.log）：优先按当前工作目录解析，
       若不存在则按项目根目录解析；
    3) 仅文件名（如 xxx.log）：等价映射到 source/resource/xxx.log（项目根目录下）。
    """
    raw = str(log_path or "").strip()
    if not raw:
        raise ValueError("log_path 不能为空")

    path_obj = Path(raw).expanduser()
    if path_obj.is_absolute():
        return str(path_obj.resolve())

    project_root = Path(__file__).resolve().parent.parent
    default_log_dir = project_root / "source" / "resource"

    # 仅文件名：强制映射到 source/resource/<filename>
    if path_obj.parent == Path("."):
        return str((default_log_dir / path_obj.name).resolve())

    cwd_candidate = (Path.cwd() / path_obj).resolve()
    if cwd_candidate.exists():
        return str(cwd_candidate)

    return str((project_root / path_obj).resolve())


def _safe_json_loads(raw: str) -> Optional[dict[str, Any]]:
    try:
        return json.loads(raw)
    except Exception:
        return None


def _extract_business_level(content: str) -> str:
    # 优先提取 c 为字符串化 JSON 的 level 字段。
    parsed = _safe_json_loads(content)
    if isinstance(parsed, dict):
        level = str(parsed.get("level", "")).strip().upper()
        if level:
            return level

    # 次级提取管道日志中的 |I|/|W|/|E| 等。
    m = re.search(r"\|([DIWE])\|", content)
    if m:
        return {
            "D": "DEBUG",
            "I": "INFO",
            "W": "WARN",
            "E": "ERROR",
        }.get(m.group(1), m.group(1))
    return ""


def _normalize_level(level: Optional[str]) -> str:
    lv = (level or "").strip().upper()
    if not lv:
        return ""
    return {
        "D": "DEBUG",
        "I": "INFO",
        "W": "WARN",
        "E": "ERROR",
        "DEBUG": "DEBUG",
        "INFO": "INFO",
        "WARN": "WARN",
        "WARNING": "WARN",
        "ERROR": "ERROR",
    }.get(lv, lv)


def _normalize_c_startswith(c_startswith: Optional[str]) -> str:
    raw = str(c_startswith or "").strip()
    if not raw:
        return ""
    # 兼容用户传 1 时，筛选前缀应为 "-:1"。
    if raw.startswith("-:"):
        return raw
    return f"-:{raw}"


def _mask_sensitive_text(text: str) -> str:
    if not text:
        return text

    out = text
    replacements = [
        (r"(CHDSSO=)([^;,\s\"\\]+)", r"\1***"),
        (r"(?i)(pai-token[\"'\s:=]+)([A-Za-z0-9._\-/+=]{8,})", r"\1***"),
        (r"(?i)(im-token[\"'\s:=]+)([A-Za-z0-9._\-/+=]{8,})", r"\1***"),
        (r"(?i)(jwtToken[\"'\s:=]+)([A-Za-z0-9._\-/+=]{8,})", r"\1***"),
        (r"(?i)(authorization[\"'\s:=]+)([A-Za-z0-9._\-/+=]{8,})", r"\1***"),
        (r"(?i)(cookie[\"'\s:=]+)([^\"\\]{8,})", r"\1***"),
    ]
    for pattern, repl in replacements:
        out = re.sub(pattern, repl, out)

    # 设备标识与广告标识默认脱敏：保留前 6 后 4。
    def _mask_uuid(m: re.Match[str]) -> str:
        s = m.group(0)
        if len(s) <= 10:
            return "***"
        return f"{s[:6]}***{s[-4:]}"

    out = re.sub(
        r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\b",
        _mask_uuid,
        out,
    )
    return out


def _parse_log_file(log_path: str) -> list[ParsedEntry]:
    entries: list[ParsedEntry] = []
    abs_log_path = _resolve_log_path(log_path)
    with open(abs_log_path, "r", encoding="utf-8", errors="ignore") as f:
        for idx, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line:
                continue
            obj = _safe_json_loads(line)
            if not isinstance(obj, dict):
                continue

            content = str(obj.get("c", ""))
            timestamp_ms = int(obj.get("l", 0) or 0)
            log_type = int(obj.get("f", 0) or 0)
            thread_name = str(obj.get("n", ""))
            thread_id = str(obj.get("i", ""))
            is_main_thread = bool(obj.get("m", False))
            level = _extract_business_level(content)

            entries.append(
                ParsedEntry(
                    line_no=idx,
                    timestamp_ms=timestamp_ms,
                    log_type=log_type,
                    thread_name=thread_name,
                    thread_id=thread_id,
                    is_main_thread=is_main_thread,
                    content=content,
                    business_level=level,
                )
            )
    return entries


def _apply_filters(
    entries: list[ParsedEntry],
    start_ts_ms: Optional[int] = None,
    end_ts_ms: Optional[int] = None,
    log_type: Optional[int] = None,
    level: Optional[str] = None,
    keywords: Optional[str] = None,
    c_startswith: Optional[str] = None,
    keyword_match: str = "OR",
) -> list[ParsedEntry]:
    normalized_level = _normalize_level(level)
    keyword_items = [k.strip() for k in (keywords or "").split(",") if k.strip()]
    normalized_c_prefix = _normalize_c_startswith(c_startswith)
    use_and_for_keywords = str(keyword_match or "OR").strip().upper() == "AND"

    out: list[ParsedEntry] = []
    for e in entries:
        if start_ts_ms is not None and e.timestamp_ms < start_ts_ms:
            continue
        if end_ts_ms is not None and e.timestamp_ms > end_ts_ms:
            continue
        if log_type is not None and e.log_type != log_type:
            continue
        if normalized_level and e.business_level != normalized_level:
            continue
        if normalized_c_prefix and not e.content.startswith(normalized_c_prefix):
            continue
        if keyword_items:
            if use_and_for_keywords and not all(k in e.content for k in keyword_items):
                continue
            if (not use_and_for_keywords) and not any(k in e.content for k in keyword_items):
                continue
        out.append(e)
    return out


def _ts_ms_to_text(ts_ms: int) -> str:
    if ts_ms <= 0:
        return ""
    try:
        return datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    except Exception:
        return str(ts_ms)


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _wilson_interval(success: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """返回比例的 Wilson 置信区间（95% 默认）。"""
    if total <= 0:
        return (0.0, 0.0)
    p_hat = success / total
    z2 = z * z
    denom = 1.0 + z2 / total
    center = (p_hat + z2 / (2.0 * total)) / denom
    margin = z * math.sqrt((p_hat * (1.0 - p_hat) + z2 / (4.0 * total)) / total) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def _severity_label(score: float) -> str:
    if score >= 0.6:
        return "P0"
    if score >= 0.3:
        return "P1"
    return "P2"


def _confidence_label(sample_size: int, ratio: float) -> str:
    # 简化置信度策略：先看样本量，再看信号强度。
    if sample_size >= 80 and ratio >= 0.08:
        return "高"
    if sample_size >= 30 and ratio >= 0.03:
        return "中"
    return "低"


def _default_pattern_map() -> dict[str, str]:
    return {
        "rn_net_request": "[RN_NET]OldSign req",
        "rn_net_resp": "[RN_NET]Resp",
        "rn_net_finish": "[RN_NET]Finish req",
        "task_orphaned": "Task orphaned for request",
        "cfnetwork_310": "kCFErrorDomainCFNetwork错误310",
        "reactnative_exception": "reactnative_exception",
        "app_terminate": "applicationWillTerminate",
    }


def _build_pattern_map(
    pattern_keywords: Optional[str] = None, include_default: bool = True
) -> dict[str, str]:
    out = _default_pattern_map() if include_default else {}
    extras = [x.strip() for x in (pattern_keywords or "").split(",") if x.strip()]
    for idx, keyword in enumerate(extras, start=1):
        if keyword in out.values():
            continue
        candidate = re.sub(r"[^0-9A-Za-z_]+", "_", keyword).strip("_").lower()
        candidate = candidate or f"custom_kw_{idx}"
        key = candidate
        serial = 2
        while key in out:
            key = f"{candidate}_{serial}"
            serial += 1
        out[key] = keyword
    return out


def _entry_to_preview_dict(entry: ParsedEntry, content_limit: int = 500) -> dict[str, Any]:
    return {
        "line_no": entry.line_no,
        "timestamp_ms": entry.timestamp_ms,
        "timestamp_text": _ts_ms_to_text(entry.timestamp_ms),
        "log_type": entry.log_type,
        "level": entry.business_level,
        "content": _mask_sensitive_text(entry.content[:content_limit]),
    }


def _build_filter_result(
    log_path: str,
    entries: list[ParsedEntry],
    filtered: list[ParsedEntry],
    start_ts_ms: Optional[int],
    end_ts_ms: Optional[int],
    log_type: Optional[int],
    level: Optional[str],
    keywords: Optional[str],
    c_startswith: Optional[str],
    max_output_lines: int,
    keyword_match: str = "OR",
) -> dict[str, Any]:
    clipped = filtered[: max(1, max_output_lines)]
    dropped = max(0, len(filtered) - len(clipped))
    type_counter = Counter(e.log_type for e in filtered)
    preview = [_entry_to_preview_dict(e) for e in clipped]

    return {
        "log_path": _resolve_log_path(log_path),
        "total_entries": len(entries),
        "matched_entries": len(filtered),
        "returned_entries": len(clipped),
        "dropped_entries": dropped,
        "filter": {
            "start_ts_ms": start_ts_ms,
            "end_ts_ms": end_ts_ms,
            "log_type": log_type,
            "level": level,
            "keywords": keywords,
            "keyword_match": str(keyword_match or "OR").strip().upper(),
            "c_startswith": _normalize_c_startswith(c_startswith) or None,
            "max_output_lines": max_output_lines,
        },
        "matched_type_distribution": dict(sorted(type_counter.items(), key=lambda x: x[0])),
        "entries": preview,
    }


def _pick_evidence_by_keyword(
    entries: list[dict[str, Any]], keyword: str, max_items: int = 3
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in entries:
        content = str(e.get("content", ""))
        if keyword not in content:
            continue
        out.append(
            {
                "line_no": e.get("line_no", ""),
                "timestamp_ms": e.get("timestamp_ms", ""),
                "keyword": keyword,
                "excerpt": _mask_sensitive_text(content[:180]).replace("\n", " "),
            }
        )
        if len(out) >= max_items:
            break
    return out


def _pick_evidence_by_keyword_parsed(
    entries: list[ParsedEntry], keyword: str, max_items: int = 3
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in entries:
        if keyword not in e.content:
            continue
        out.append(
            {
                "line_no": e.line_no,
                "timestamp_ms": e.timestamp_ms,
                "keyword": keyword,
                "excerpt": _mask_sensitive_text(e.content[:180]).replace("\n", " "),
            }
        )
        if len(out) >= max_items:
            break
    return out


def _evidence_text(evidence_items: list[dict[str, Any]]) -> str:
    if not evidence_items:
        return "无直接证据命中"
    head = evidence_items[0]
    return (
        f"L{head.get('line_no', '')}@{head.get('timestamp_ms', '')}, "
        f"kw={head.get('keyword', '')}"
    )

START_LIVE_STAGE_ORDER: list[tuple[str, str, int]] = [
    # 1) IM触发实时呼叫（Entry A）
    ("im_event_received", "IM触发实时呼叫（Entry A）", 110),
    ("im_event_route_realtime_call", "IM触发实时呼叫（Entry A）", 120),
    ("realtime_call_handler_enter", "IM触发实时呼叫（Entry A）", 130),
    ("realtime_call_background_notify", "IM触发实时呼叫（Entry A）", 140),
    ("realtime_call_foreground_present", "IM触发实时呼叫（Entry A）", 150),
    ("realtime_popup_will_present", "IM触发实时呼叫（Entry A）", 160),
    ("realtime_popup_skip_priority", "IM触发实时呼叫（Entry A）", 170),
    ("realtime_popup_skip_small_window", "IM触发实时呼叫（Entry A）", 180),
    ("realtime_small_window_presented", "IM触发实时呼叫（Entry A）", 190),
    ("realtime_popup_present", "IM触发实时呼叫（Entry A）", 200),
    ("realtime_popup_presented", "IM触发实时呼叫（Entry A）", 210),
    ("realtime_popup_view_did_load", "IM触发实时呼叫（Entry A）", 220),
    ("realtime_popup_load_data_result", "IM触发实时呼叫（Entry A）", 230),
    # 2) 恢复呼叫数据（Entry B）
    ("recover_check_start", "恢复呼叫数据（Entry B）", 310),
    ("recover_api_failure", "恢复呼叫数据（Entry B）", 320),
    ("recover_handle_result", "恢复呼叫数据（Entry B）", 330),
    ("recover_go_realtime_popup", "恢复呼叫数据（Entry B）", 340),
    # 3) 实时弹窗关闭路径
    ("realtime_popup_dismiss_cancel", "实时弹窗关闭路径", 410),
    ("realtime_popup_dismiss_remindStart", "实时弹窗关闭路径", 420),
    ("realtime_popup_dismiss", "实时弹窗关闭路径", 430),
    # 4) 弹窗点击开播（预创建）
    ("realtime_click_enter_room_denied_auth", "弹窗点击开播（预创建）", 510),
    ("realtime_click_enter_room", "弹窗点击开播（预创建）", 520),
    ("precreate_start", "弹窗点击开播（预创建）", 530),
    ("realtime_click_enter_room_result", "弹窗点击开播（预创建）", 540),
    ("precreate_success", "弹窗点击开播（预创建）", 550),
    ("precreate_sign_data_nil", "弹窗点击开播（预创建）", 560),
    ("precreate_disabled", "弹窗点击开播（预创建）", 570),
    ("precreate_failure", "弹窗点击开播（预创建）", 580),
    # 5) 跳转直播页并加载直播数据
    ("jump_recover_enter", "跳转直播页并加载直播数据", 610),
    ("jump_recover_im_not_login", "跳转直播页并加载直播数据", 620),
    ("jump_recover_im_login_success_retry", "跳转直播页并加载直播数据", 630),
    ("jump_recover_im_login_failed", "跳转直播页并加载直播数据", 640),
    ("jump_recover_present_livevc", "跳转直播页并加载直播数据", 650),
    ("jump_recover_livevc_presented", "跳转直播页并加载直播数据", 660),
    ("livevc_view_did_load", "跳转直播页并加载直播数据", 670),
    ("livevc_enter_room_success", "跳转直播页并加载直播数据", 680),
    ("livevc_enter_room_fail", "跳转直播页并加载直播数据", 690),
    ("livevc_update_room_status_resume_direct_load", "跳转直播页并加载直播数据", 700),
    ("livevc_update_room_status_success", "跳转直播页并加载直播数据", 710),
    ("livevc_update_room_status_failure", "跳转直播页并加载直播数据", 720),
    ("livevc_load_room_info_failure", "跳转直播页并加载直播数据", 730),
    ("livevc_load_live_room_data_finish", "跳转直播页并加载直播数据", 740),
]

START_LIVE_STAGE_MAP: dict[str, dict[str, Any]] = {
    stage: {"process": process, "order": order}
    for stage, process, order in START_LIVE_STAGE_ORDER
}

START_LIVE_FAILURE_STAGES: set[str] = {
    "recover_api_failure",
    "realtime_popup_dismiss_cancel",
    "realtime_popup_dismiss_remindStart",
    "precreate_sign_data_nil",
    "precreate_disabled",
    "precreate_failure",
    "jump_recover_im_login_failed",
    "livevc_enter_room_fail",
    "livevc_update_room_status_failure",
    "livevc_load_room_info_failure",
}

START_LIVE_SUCCESS_TERMINAL_STAGES: set[str] = {
    "livevc_load_live_room_data_finish",
}

def _extract_embedded_json_from_content(content: str) -> Optional[dict[str, Any]]:
    if not content:
        return None

    candidates: list[str] = []
    pipe_idx = content.rfind("|")
    if pipe_idx >= 0 and pipe_idx < len(content) - 1:
        candidates.append(content[pipe_idx + 1 :].strip())

    matched = re.search(r"(\{.*\})\s*$", content)
    if matched:
        candidates.append(matched.group(1).strip())

    for raw in candidates:
        parsed = _safe_json_loads(raw)
        if isinstance(parsed, dict):
            return parsed
    return None


def _is_empty_like(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return text == "" or text.lower() in {"(null)", "null", "none", "<null>"}


def _normalize_extra_value(value: Any) -> str:
    if _is_empty_like(value):
        return ""
    return str(value).strip()


def _aggregate_extra_values(values: list[str]) -> dict[str, Any]:
    non_empty = [v for v in values if v]
    first_non_empty = non_empty[0] if non_empty else ""
    last_non_empty = non_empty[-1] if non_empty else ""

    uniq_values: list[str] = []
    seen: set[str] = set()
    for value in non_empty:
        if value in seen:
            continue
        seen.add(value)
        uniq_values.append(value)

    return {
        "first_non_empty": first_non_empty,
        "last_non_empty": last_non_empty,
        "all_values": uniq_values,
    }

def _extract_source_locations(content: str) -> list[tuple[str, int]]:
    # 例: CSPRNApiRequestHandler.m:121
    matches = re.findall(r"([A-Za-z0-9_\-+]+\.(?:m|mm|h|swift|py|js|ts|tsx|java|kt)):(\d+)", content)
    out: list[tuple[str, int]] = []
    for name, line_no in matches:
        try:
            out.append((name, int(line_no)))
        except Exception:
            continue
    return out


def _index_source_files(source_root: str) -> dict[str, list[str]]:
    index: dict[str, list[str]] = defaultdict(list)
    root = Path(_abs_path(source_root))
    if not root.exists():
        return index

    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if ".git" in p.parts:
            continue
        index[p.name].append(str(p))
    return index


def _read_code_snippet(path: str, target_line: int, context: int = 2) -> str:
    if target_line <= 0:
        target_line = 1
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    start = max(1, target_line - context)
    end = min(len(lines), target_line + context)
    parts: list[str] = []
    for i in range(start, end + 1):
        marker = ">>" if i == target_line else "  "
        parts.append(f"{marker} {i:5d} | {lines[i - 1].rstrip()}")
    return "\n".join(parts)
