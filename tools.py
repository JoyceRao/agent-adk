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


SKILL_DEFINITIONS: dict[str, dict[str, Any]] = {
    "log-filter-assistant": {
        "delegated_agent": "filter_agent",
        "tool": "filter_logs",
        "aliases": ["filter", "log-filter", "filter-skill"],
        "description": "日志筛选与预览输出。",
    },
    "source-correlation-assistant": {
        "delegated_agent": "analysis_agent",
        "tool": "analyze_log_with_source",
        "aliases": ["source", "source-correlation", "analysis", "analysis-skill"],
        "description": "日志+源码关联分析与异常研判。",
    },
    "crisp-l-report-assistant": {
        "delegated_agent": "report_agent",
        "tool": "analyze_and_generate_report",
        "aliases": ["report", "crisp-l", "report-skill"],
        "description": "CRISP-L 报告生成与落盘。",
    },
    "log-orchestrator-assistant": {
        "delegated_agent": "root_agent",
        "tool": "orchestration(filter->analysis->report)",
        "aliases": ["orchestrator", "orchestration", "root", "workflow"],
        "description": "按标准链路执行筛选、分析、报告。",
    },
}

SKILL_ALIAS_TO_CANONICAL: dict[str, str] = {}
for _canonical_name, _meta in SKILL_DEFINITIONS.items():
    SKILL_ALIAS_TO_CANONICAL[_canonical_name.lower()] = _canonical_name
    for _alias in _meta.get("aliases", []):
        SKILL_ALIAS_TO_CANONICAL[str(_alias).strip().lower()] = _canonical_name


def _abs_path(path: str) -> str:
    return str(Path(path).expanduser().resolve())


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
    abs_log_path = _abs_path(log_path)
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
) -> list[ParsedEntry]:
    normalized_level = _normalize_level(level)
    keyword_items = [k.strip() for k in (keywords or "").split(",") if k.strip()]

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
        if keyword_items and not any(k in e.content for k in keyword_items):
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
    max_output_lines: int,
) -> dict[str, Any]:
    clipped = filtered[: max(1, max_output_lines)]
    dropped = max(0, len(filtered) - len(clipped))
    type_counter = Counter(e.log_type for e in filtered)
    preview = [_entry_to_preview_dict(e) for e in clipped]

    return {
        "log_path": _abs_path(log_path),
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


def _normalize_skill_name(skill_name: str) -> str:
    raw = (skill_name or "").strip().lower()
    if raw.startswith("$"):
        raw = raw[1:]
    raw = raw.replace("_", "-")
    return SKILL_ALIAS_TO_CANONICAL.get(raw, raw)


def filter_logs(
    log_path: str,
    start_ts_ms: Optional[int] = None,
    end_ts_ms: Optional[int] = None,
    log_type: Optional[int] = None,
    level: Optional[str] = None,
    keywords: Optional[str] = None,
    max_output_lines: int = 1000,
) -> dict[str, Any]:
    """按时间和类型筛选日志，缩减日志量。

    Args:
        log_path: 日志文件路径。
        start_ts_ms: 起始时间戳（毫秒）。
        end_ts_ms: 结束时间戳（毫秒）。
        log_type: 外层日志类型 f（如 1 或 99）。
        level: 业务级别（I/W/E/D 或 INFO/WARN/ERROR）。
        keywords: 关键词，多个用英文逗号分隔。
        max_output_lines: 最大返回行数，避免输出过大。
    """
    entries = _parse_log_file(log_path)
    filtered = _apply_filters(
        entries=entries,
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
        log_type=log_type,
        level=level,
        keywords=keywords,
    )
    return _build_filter_result(
        log_path=log_path,
        entries=entries,
        filtered=filtered,
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
        log_type=log_type,
        level=level,
        keywords=keywords,
        max_output_lines=max_output_lines,
    )


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


def analyze_log_with_source(
    log_path: str,
    source_root: str = "source/GZCheSuPaiApp",
    rule_path: str = "source/log_rule.md",
    start_ts_ms: Optional[int] = None,
    end_ts_ms: Optional[int] = None,
    log_type: Optional[int] = None,
    level: Optional[str] = None,
    keywords: Optional[str] = None,
    max_output_lines: int = 1000,
    max_source_matches: int = 20,
) -> dict[str, Any]:
    """结合日志与源码进行分析，输出 CRISP-L 结构化结论。"""
    all_entries = _parse_log_file(log_path)
    filtered_all = _apply_filters(
        entries=all_entries,
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
        log_type=log_type,
        level=level,
        keywords=keywords,
    )
    filtered = _build_filter_result(
        log_path=log_path,
        entries=all_entries,
        filtered=filtered_all,
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
        log_type=log_type,
        level=level,
        keywords=keywords,
        max_output_lines=max_output_lines,
    )

    preview_entries: list[dict[str, Any]] = filtered["entries"]
    total_entries = int(filtered["total_entries"])
    matched_entries = int(filtered["matched_entries"])
    returned_entries = int(filtered["returned_entries"])

    pattern_map = _default_pattern_map()
    pattern_counts = {k: 0 for k in pattern_map}
    for parsed in filtered_all:
        content = parsed.content
        for name, kw in pattern_map.items():
            if kw in content:
                pattern_counts[name] += 1

    req_cnt = pattern_counts["rn_net_request"]
    resp_cnt = pattern_counts["rn_net_resp"]
    fin_cnt = pattern_counts["rn_net_finish"]
    cfnetwork_cnt = pattern_counts["cfnetwork_310"]
    rn_exception_cnt = pattern_counts["reactnative_exception"]
    orphan_cnt = pattern_counts["task_orphaned"]

    closure_rate = _safe_ratio(fin_cnt, req_cnt)
    resp_rate = _safe_ratio(resp_cnt, req_cnt)
    closure_ci = _wilson_interval(fin_cnt, req_cnt) if req_cnt > 0 else (0.0, 0.0)
    matched_ratio = _safe_ratio(matched_entries, total_entries)
    matched_ci = _wilson_interval(matched_entries, total_entries) if total_entries > 0 else (0.0, 0.0)
    exception_ratio = _safe_ratio(rn_exception_cnt + cfnetwork_cnt, max(1, matched_entries))

    problems: list[dict[str, Any]] = []
    problem_seq = 1

    def _append_problem(
        conclusion: str,
        impact: str,
        signal_ratio: float,
        trigger_condition: str,
        evidence_items: list[dict[str, Any]],
        suggestion: str,
    ) -> None:
        nonlocal problem_seq
        score = min(1.0, signal_ratio)
        problem_id = f"P-{problem_seq:02d}"
        problem_seq += 1
        problems.append(
            {
                "problem_id": problem_id,
                "conclusion": conclusion,
                "impact": impact,
                "severity": _severity_label(score),
                "confidence": _confidence_label(returned_entries, signal_ratio),
                "signal_ratio": signal_ratio,
                "trigger_condition": trigger_condition,
                "evidence_items": evidence_items,
                "evidence_summary": _evidence_text(evidence_items),
                "suggestion": suggestion,
            }
        )

    if req_cnt > 0 and req_cnt > fin_cnt:
        gap = req_cnt - fin_cnt
        gap_ratio = _safe_ratio(gap, req_cnt)
        chain_evidence = _pick_evidence_by_keyword_parsed(
            filtered_all, "[RN_NET]OldSign req", max_items=2
        )
        chain_evidence += _pick_evidence_by_keyword_parsed(
            filtered_all, "[RN_NET]Finish req", max_items=2
        )
        _append_problem(
            conclusion="RN 请求链路不闭合，Finish 数量显著低于 Request 数量",
            impact=f"链路闭合率 {_pct(closure_rate)}，可能导致页面数据不一致/加载失败",
            signal_ratio=min(1.0, gap_ratio + 0.15),
            trigger_condition="存在 [RN_NET]OldSign req 但缺少对应 [RN_NET]Finish req",
            evidence_items=chain_evidence,
            suggestion="优先排查请求取消/超时策略、前后台切换时机与重试幂等控制。",
        )

    if cfnetwork_cnt > 0:
        cfnetwork_ratio = _safe_ratio(cfnetwork_cnt, max(1, matched_entries))
        _append_problem(
            conclusion="检测到 CFNetwork 310 网络错误，存在基础连通性风险",
            impact=f"网络错误信号占筛选样本 {_pct(cfnetwork_ratio)}",
            signal_ratio=min(1.0, cfnetwork_ratio * 12 + 0.1),
            trigger_condition="日志出现 kCFErrorDomainCFNetwork错误310",
            evidence_items=_pick_evidence_by_keyword_parsed(
                filtered_all, "kCFErrorDomainCFNetwork错误310"
            ),
            suggestion="补充 DNS/证书链/网关健康检查，区分弱网重试与快速失败路径。",
        )

    if rn_exception_cnt > 0:
        rn_exc_ratio = _safe_ratio(rn_exception_cnt, max(1, matched_entries))
        _append_problem(
            conclusion="RN 异常监控上报命中，客户端异常处理路径被触发",
            impact=f"异常信号占筛选样本 {_pct(rn_exc_ratio)}，可能造成用户可见失败",
            signal_ratio=min(1.0, rn_exc_ratio * 10 + 0.12),
            trigger_condition="日志出现 reactnative_exception 或 request_catch 相关异常路径",
            evidence_items=_pick_evidence_by_keyword_parsed(filtered_all, "reactnative_exception"),
            suggestion="按 requestId 与 URI 建立端到端追踪，定位抛错环节并加兜底降级。",
        )

    if orphan_cnt > 0:
        orphan_ratio = _safe_ratio(orphan_cnt, max(1, matched_entries))
        _append_problem(
            conclusion="图片任务 orphan 事件出现，可能存在页面切换时资源回收抖动",
            impact=f"orphan 占筛选样本 {_pct(orphan_ratio)}，主要影响性能稳定性",
            signal_ratio=min(1.0, orphan_ratio * 6 + 0.06),
            trigger_condition="日志出现 Task orphaned for request",
            evidence_items=_pick_evidence_by_keyword_parsed(filtered_all, "Task orphaned for request"),
            suggestion="优化图片请求取消策略与缓存命中策略，减少页面切换抖动。",
        )

    if not problems:
        fallback_evidence = [
            {
                "line_no": e.line_no,
                "timestamp_ms": e.timestamp_ms,
                "keyword": "",
                "excerpt": _mask_sensitive_text(e.content[:180]).replace("\n", " "),
            }
            for e in filtered_all[:2]
        ]
        _append_problem(
            conclusion="当前筛选窗口未发现显著异常模式",
            impact="风险等级较低，但不代表全量时间窗口无异常",
            signal_ratio=0.05,
            trigger_condition="统计未触发异常阈值",
            evidence_items=fallback_evidence,
            suggestion="扩大时间窗口或增加关键词范围后复查。",
        )

    anomalies = [
        {
            "name": p["conclusion"],
            "severity": p["severity"],
            "evidence": p["evidence_summary"],
            "advice": p["suggestion"],
        }
        for p in problems
    ]

    reproduction_scenarios = []
    for p in problems:
        reproduction_scenarios.append(
            {
                "problem_id": p["problem_id"],
                "trigger_condition": p["trigger_condition"],
                "repro_hint": "建议在弱网、前后台切换、并发请求场景下复现。",
                "key_evidence": p["evidence_summary"],
            }
        )

    indicators = [
        {
            "name": "筛选命中率",
            "value": _pct(matched_ratio),
            "formula": "matched_entries / total_entries",
            "confidence_interval": f"[{_pct(matched_ci[0])}, {_pct(matched_ci[1])}]",
            "sample_size": total_entries,
        },
        {
            "name": "请求链路闭合率",
            "value": _pct(closure_rate),
            "formula": "rn_net_finish / rn_net_request",
            "confidence_interval": f"[{_pct(closure_ci[0])}, {_pct(closure_ci[1])}]",
            "sample_size": req_cnt,
        },
        {
            "name": "请求响应回包率",
            "value": _pct(resp_rate),
            "formula": "rn_net_resp / rn_net_request",
            "confidence_interval": "N/A（描述性指标）",
            "sample_size": req_cnt,
        },
        {
            "name": "异常信号占比",
            "value": _pct(exception_ratio),
            "formula": "(cfnetwork_310 + reactnative_exception) / matched_entries",
            "confidence_interval": "N/A（描述性指标）",
            "sample_size": matched_entries,
        },
    ]

    significance_notes: list[str] = []
    significance_notes.append("模式计数基于筛选命中的全量样本，证据预览默认是窗口抽样。")
    if req_cnt < 30:
        significance_notes.append("请求样本量 < 30，闭合率结论的统计稳定性有限。")
    else:
        significance_notes.append("请求样本量 >= 30，链路闭合率结论具备基础统计参考价值。")
    if req_cnt > 0 and closure_ci[1] < 0.9:
        significance_notes.append("闭合率 95% 区间上界仍低于 90%，链路不闭合问题具有显著性。")
    elif req_cnt > 0 and closure_ci[0] < 0.9 <= closure_ci[1]:
        significance_notes.append("闭合率区间跨越 90% 阈值，建议扩大窗口复核后再定级。")
    if returned_entries < matched_entries:
        significance_notes.append("报告基于抽样输出窗口，部分细节未在证据预览中展示。")

    source_index = _index_source_files(source_root)
    source_hits: list[dict[str, Any]] = []
    seen = set()
    for item in filtered_all:
        if len(source_hits) >= max_source_matches:
            break
        locs = _extract_source_locations(item.content)
        for filename, ln in locs:
            key = (filename, ln)
            if key in seen:
                continue
            seen.add(key)
            paths = source_index.get(filename, [])
            if not paths:
                continue
            source_path = paths[0]
            try:
                snippet = _read_code_snippet(source_path, ln, context=2)
            except Exception:
                snippet = ""
            source_hits.append(
                {
                    "from_log_line": item.line_no,
                    "source_file": source_path,
                    "source_line": ln,
                    "snippet": snippet,
                }
            )
            if len(source_hits) >= max_source_matches:
                break

    plan_actions: list[dict[str, Any]] = []
    for p in problems:
        priority = {"P0": "立即", "P1": "高", "P2": "中"}.get(p["severity"], "中")
        plan_actions.append(
            {
                "problem_id": p["problem_id"],
                "priority": priority,
                "stage": "止血" if p["severity"] == "P0" else "修复",
                "action": p["suggestion"],
                "owner_hint": "客户端负责人 + 接口负责人联合处理",
                "acceptance_criteria": (
                    "请求链路闭合率 >= 95%，reactnative_exception 占比下降 50% 以上。"
                ),
            }
        )

    target_closure = 0.95 if req_cnt == 0 else min(0.99, max(0.90, closure_rate + 0.10))
    loop_checkpoints = [
        {
            "window": "T+1h",
            "metric": "请求链路闭合率",
            "target": f">= {_pct(target_closure)}",
            "current": _pct(closure_rate),
        },
        {
            "window": "T+24h",
            "metric": "reactnative_exception 占比",
            "target": "<= 基线的 50%",
            "current": _pct(_safe_ratio(rn_exception_cnt, max(1, matched_entries))),
        },
        {
            "window": "T+72h",
            "metric": "CFNetwork 310 命中次数",
            "target": "连续 3 个观察窗口为 0 或接近 0",
            "current": str(cfnetwork_cnt),
        },
    ]
    loop_alerts = [
        "若链路闭合率连续两个窗口低于 85%，触发 P0 告警。",
        "若 reactnative_exception 较修复前反弹 > 20%，触发回滚评估。",
    ]
    loop_rollback = "修复后 2 小时内核心指标无改善且用户影响扩大时，执行版本回滚。"

    data_limitations: list[str] = []
    if matched_entries <= 0:
        data_limitations.append("筛选命中为 0，当前无法形成统计结论。")
    if matched_entries < 30:
        data_limitations.append("筛选样本量较小，建议扩大时间窗口和关键词集合。")
    if returned_entries < matched_entries:
        data_limitations.append("证据预览为抽样窗口，未覆盖全部命中日志。")

    rule_summary = ""
    rule_abs = _abs_path(rule_path)
    if os.path.exists(rule_abs):
        try:
            with open(rule_abs, "r", encoding="utf-8", errors="ignore") as rf:
                rule_summary = "".join(rf.readlines()[:40]).strip()
        except Exception:
            rule_summary = ""

    conclusion_summary = (
        f"本次共识别 {len(problems)} 个问题，重点风险集中在请求链路闭合率与异常信号路径。"
        if problems
        else "当前未识别到显著异常问题。"
    )

    return {
        "meta": {
            "log_path": filtered["log_path"],
            "source_root": _abs_path(source_root),
            "rule_path": rule_abs,
        },
        "filter_summary": {
            "total_entries": total_entries,
            "matched_entries": matched_entries,
            "returned_entries": returned_entries,
            "dropped_entries": int(filtered.get("dropped_entries", 0)),
            "pattern_count_basis": "full_filtered_entries",
            "type_distribution": filtered["matched_type_distribution"],
        },
        "pattern_counts": pattern_counts,
        "anomalies": anomalies,
        "evidence_preview": preview_entries[:50],
        "source_correlations": source_hits,
        "rule_excerpt": rule_summary,
        "crisp_l": {
            "conclusion": {
                "summary": conclusion_summary,
                "problems": problems,
            },
            "reproduction": {
                "scenarios": reproduction_scenarios,
            },
            "indicators": {
                "metrics": indicators,
                "significance_notes": significance_notes,
                "data_limitations": data_limitations,
            },
            "source_correlation": {
                "hits": source_hits,
            },
            "plan": {
                "actions": plan_actions,
            },
            "loop_closure": {
                "checkpoints": loop_checkpoints,
                "alerts": loop_alerts,
                "rollback_rule": loop_rollback,
            },
        },
    }


def scan_patterns_full(
    log_path: str,
    start_ts_ms: Optional[int] = None,
    end_ts_ms: Optional[int] = None,
    log_type: Optional[int] = None,
    level: Optional[str] = None,
    keywords: Optional[str] = None,
    pattern_keywords: Optional[str] = None,
    include_default_patterns: bool = True,
    evidence_per_pattern: int = 2,
) -> dict[str, Any]:
    """按全量筛选结果统计模式命中，避免抽样窗口导致的漏检。"""
    all_entries = _parse_log_file(log_path)
    filtered_all = _apply_filters(
        entries=all_entries,
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
        log_type=log_type,
        level=level,
        keywords=keywords,
    )
    filtered = _build_filter_result(
        log_path=log_path,
        entries=all_entries,
        filtered=filtered_all,
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
        log_type=log_type,
        level=level,
        keywords=keywords,
        max_output_lines=100,
    )

    pattern_map = _build_pattern_map(
        pattern_keywords=pattern_keywords,
        include_default=include_default_patterns,
    )
    pattern_counts = {k: 0 for k in pattern_map}
    for entry in filtered_all:
        for pattern_name, keyword in pattern_map.items():
            if keyword in entry.content:
                pattern_counts[pattern_name] += 1

    matched_entries = len(filtered_all)
    pattern_rates = {
        k: _pct(_safe_ratio(v, max(1, matched_entries))) for k, v in pattern_counts.items()
    }
    top_patterns = []
    for pattern_name, count in sorted(pattern_counts.items(), key=lambda x: (-x[1], x[0])):
        if count <= 0:
            continue
        keyword = pattern_map.get(pattern_name, "")
        top_patterns.append(
            {
                "pattern_name": pattern_name,
                "keyword": keyword,
                "count": count,
                "rate": _safe_ratio(count, max(1, matched_entries)),
                "rate_text": _pct(_safe_ratio(count, max(1, matched_entries))),
                "evidence": _pick_evidence_by_keyword_parsed(
                    filtered_all,
                    keyword,
                    max_items=max(1, evidence_per_pattern),
                ),
            }
        )

    return {
        "meta": {
            "log_path": filtered["log_path"],
            "count_basis": "full_filtered_entries",
        },
        "filter_summary": {
            "total_entries": len(all_entries),
            "matched_entries": matched_entries,
            "returned_entries": filtered["returned_entries"],
            "dropped_entries": filtered["dropped_entries"],
            "type_distribution": filtered["matched_type_distribution"],
        },
        "pattern_definitions": pattern_map,
        "pattern_counts": pattern_counts,
        "pattern_rates": pattern_rates,
        "top_patterns": top_patterns,
    }


def build_timeline(
    log_path: str,
    start_ts_ms: Optional[int] = None,
    end_ts_ms: Optional[int] = None,
    log_type: Optional[int] = None,
    level: Optional[str] = None,
    keywords: Optional[str] = None,
    bucket_ms: int = 60000,
    max_output_buckets: int = 240,
    pattern_keywords: Optional[str] = None,
    include_default_patterns: bool = True,
) -> dict[str, Any]:
    """构建事件时间线，输出按时间桶聚合的波峰信息。"""
    safe_bucket_ms = max(1000, int(bucket_ms or 60000))
    all_entries = _parse_log_file(log_path)
    filtered_all = _apply_filters(
        entries=all_entries,
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
        log_type=log_type,
        level=level,
        keywords=keywords,
    )
    pattern_map = _build_pattern_map(
        pattern_keywords=pattern_keywords,
        include_default=include_default_patterns,
    )

    timeline_map: dict[int, dict[str, Any]] = {}
    no_timestamp_entries = 0
    for entry in filtered_all:
        ts = int(entry.timestamp_ms)
        if ts <= 0:
            no_timestamp_entries += 1
            continue
        bucket_start = (ts // safe_bucket_ms) * safe_bucket_ms
        bucket = timeline_map.setdefault(
            bucket_start,
            {
                "event_count": 0,
                "level_distribution": Counter(),
                "type_distribution": Counter(),
                "pattern_hits": Counter(),
            },
        )
        bucket["event_count"] += 1
        bucket["level_distribution"][entry.business_level or "UNKNOWN"] += 1
        bucket["type_distribution"][entry.log_type] += 1
        for pattern_name, keyword in pattern_map.items():
            if keyword in entry.content:
                bucket["pattern_hits"][pattern_name] += 1

    timeline_rows_full: list[dict[str, Any]] = []
    for bucket_start in sorted(timeline_map.keys()):
        bucket = timeline_map[bucket_start]
        bucket_end = bucket_start + safe_bucket_ms - 1
        timeline_rows_full.append(
            {
                "bucket_start_ms": bucket_start,
                "bucket_end_ms": bucket_end,
                "bucket_start_text": _ts_ms_to_text(bucket_start),
                "bucket_end_text": _ts_ms_to_text(bucket_end),
                "event_count": bucket["event_count"],
                "level_distribution": dict(sorted(bucket["level_distribution"].items(), key=lambda x: x[0])),
                "type_distribution": dict(sorted(bucket["type_distribution"].items(), key=lambda x: x[0])),
                "pattern_hits": dict(sorted(bucket["pattern_hits"].items(), key=lambda x: x[0])),
            }
        )

    clipped_rows = timeline_rows_full[: max(1, max_output_buckets)]
    dropped_buckets = max(0, len(timeline_rows_full) - len(clipped_rows))
    peak_buckets = sorted(
        timeline_rows_full,
        key=lambda x: x.get("event_count", 0),
        reverse=True,
    )[:5]

    return {
        "meta": {
            "log_path": _abs_path(log_path),
            "bucket_ms": safe_bucket_ms,
            "count_basis": "full_filtered_entries",
        },
        "filter_summary": {
            "total_entries": len(all_entries),
            "matched_entries": len(filtered_all),
            "no_timestamp_entries": no_timestamp_entries,
            "start_ts_ms": start_ts_ms,
            "end_ts_ms": end_ts_ms,
            "log_type": log_type,
            "level": level,
            "keywords": keywords,
        },
        "pattern_definitions": pattern_map,
        "timeline": clipped_rows,
        "peak_buckets": peak_buckets,
        "dropped_buckets": dropped_buckets,
    }


def generate_markdown_report(analysis: dict[str, Any], title: str = "日志分析报告") -> str:
    """将结构化分析结果转换为 CRISP-L Markdown 报告。"""
    meta = analysis.get("meta", {})
    fs = analysis.get("filter_summary", {})
    pattern_counts = analysis.get("pattern_counts", {})
    source_hits = analysis.get("source_correlations", [])
    preview = analysis.get("evidence_preview", [])
    crisp_l = analysis.get("crisp_l", {})

    conclusion = crisp_l.get("conclusion", {})
    reproduction = crisp_l.get("reproduction", {})
    indicators = crisp_l.get("indicators", {})
    plan = crisp_l.get("plan", {})
    loop_closure = crisp_l.get("loop_closure", {})

    problems = conclusion.get("problems", []) or []
    scenarios = reproduction.get("scenarios", []) or []
    metric_rows = indicators.get("metrics", []) or []
    significance_notes = indicators.get("significance_notes", []) or []
    data_limitations = indicators.get("data_limitations", []) or []
    plan_actions = plan.get("actions", []) or []
    checkpoints = loop_closure.get("checkpoints", []) or []
    alerts = loop_closure.get("alerts", []) or []
    rollback_rule = loop_closure.get("rollback_rule", "")

    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")

    lines.append("## 0. 快速摘要（先看结论与修复建议）")
    lines.append("")
    lines.append("| 结论摘要 | 修复优先动作 |")
    lines.append("|---|---|")
    summary_text = _mask_sensitive_text(str(conclusion.get("summary", "未生成摘要")))
    top_action = (
        _mask_sensitive_text(str(plan_actions[0].get("action", "")))
        if plan_actions
        else "扩大筛选窗口后复查并补充证据。"
    )
    lines.append(f"| {summary_text} | {top_action} |")
    lines.append("")

    lines.append("## C. Conclusion（结论与影响）")
    lines.append("")
    lines.append("| 问题ID | 结论 | 严重级别 | 影响 | 置信度 | 关键证据 |")
    lines.append("|---|---|---|---|---|---|")
    if problems:
        for p in problems:
            lines.append(
                f"| {p.get('problem_id', '')} | {_mask_sensitive_text(str(p.get('conclusion', '')))} | "
                f"{p.get('severity', '')} | {_mask_sensitive_text(str(p.get('impact', '')))} | "
                f"{p.get('confidence', '')} | {_mask_sensitive_text(str(p.get('evidence_summary', '')))} |"
            )
    else:
        lines.append("| - | 未识别到显著异常问题 | P2 | 风险较低 | 低 | 无 |")
    lines.append("")

    lines.append("## R. Reproduction（复现场景与触发条件）")
    lines.append("")
    lines.append("| 问题ID | 触发条件 | 复现建议 | 关键证据 |")
    lines.append("|---|---|---|---|")
    if scenarios:
        for s in scenarios:
            lines.append(
                f"| {s.get('problem_id', '')} | {_mask_sensitive_text(str(s.get('trigger_condition', '')))} | "
                f"{_mask_sensitive_text(str(s.get('repro_hint', '')))} | "
                f"{_mask_sensitive_text(str(s.get('key_evidence', '')))} |"
            )
    else:
        lines.append("| - | 无 | 无 | 无 |")
    lines.append("")

    lines.append("## I. Indicators（关键指标与统计显著性）")
    lines.append("")
    lines.append("| 指标 | 数值 | 公式 | 置信区间/说明 | 样本量 |")
    lines.append("|---|---:|---|---|---:|")
    for item in metric_rows:
        lines.append(
            f"| {item.get('name', '')} | {item.get('value', '')} | `{item.get('formula', '')}` | "
            f"{item.get('confidence_interval', '')} | {item.get('sample_size', 0)} |"
        )
    lines.append("")
    if significance_notes:
        lines.append("统计显著性说明：")
        for note in significance_notes:
            lines.append(f"- {_mask_sensitive_text(str(note))}")
        lines.append("")

    lines.append("## S. Source Correlation（日志与源码关联证据）")
    lines.append("")
    lines.append("| 日志行号 | 源码文件 | 源码行 |")
    lines.append("|---:|---|---:|")
    if source_hits:
        for hit in source_hits[:12]:
            lines.append(
                f"| {hit.get('from_log_line', '')} | `{hit.get('source_file', '')}` | {hit.get('source_line', '')} |"
            )
    else:
        lines.append("| - | 未在源码目录中匹配到定位文件 | - |")
    lines.append("")

    if source_hits:
        lines.append("源码片段（节选）：")
        lines.append("")
        for idx, hit in enumerate(source_hits[:5], start=1):
            lines.append(f"### 片段 {idx}: `{hit.get('source_file', '')}:{hit.get('source_line', '')}`")
            lines.append("")
            lines.append("```text")
            lines.append(hit.get("snippet", "") or "")
            lines.append("```")
            lines.append("")

    lines.append("## P. Plan（修复方案与优先级）")
    lines.append("")
    lines.append("| 问题ID | 优先级 | 阶段 | 修复动作 | Owner 建议 | 验收标准 |")
    lines.append("|---|---|---|---|---|---|")
    if plan_actions:
        for action in plan_actions:
            lines.append(
                f"| {action.get('problem_id', '')} | {action.get('priority', '')} | {action.get('stage', '')} | "
                f"{_mask_sensitive_text(str(action.get('action', '')))} | "
                f"{_mask_sensitive_text(str(action.get('owner_hint', '')))} | "
                f"{_mask_sensitive_text(str(action.get('acceptance_criteria', '')))} |"
            )
    else:
        lines.append("| - | 中 | 复查 | 扩大时间窗口并补充数据后再判断 | 客户端负责人 | 明确可复现问题后再执行修复 |")
    lines.append("")

    lines.append("## L. Loop Closure（上线验证与监控闭环）")
    lines.append("")
    lines.append("| 观察窗口 | 指标 | 目标阈值 | 当前值 |")
    lines.append("|---|---|---|---|")
    for cp in checkpoints:
        lines.append(
            f"| {cp.get('window', '')} | {cp.get('metric', '')} | {cp.get('target', '')} | {cp.get('current', '')} |"
        )
    lines.append("")
    if alerts:
        lines.append("告警规则：")
        for alert in alerts:
            lines.append(f"- {_mask_sensitive_text(str(alert))}")
        lines.append("")
    if rollback_rule:
        lines.append(f"回滚规则：{_mask_sensitive_text(str(rollback_rule))}")
        lines.append("")

    lines.append("## 其他（分析范围、模式统计、证据预览、局限性）")
    lines.append("")
    lines.append("| 项目 | 内容 |")
    lines.append("|---|---|")
    lines.append(f"| 日志文件 | `{meta.get('log_path', '')}` |")
    lines.append(f"| 源码目录 | `{meta.get('source_root', '')}` |")
    lines.append(f"| 规则文件 | `{meta.get('rule_path', '')}` |")
    lines.append(f"| 原始日志条数 | {fs.get('total_entries', 0)} |")
    lines.append(f"| 筛选命中条数 | {fs.get('matched_entries', 0)} |")
    lines.append(f"| 输出分析条数 | {fs.get('returned_entries', 0)} |")
    lines.append(f"| 缩减条数 | {fs.get('dropped_entries', 0)} |")
    lines.append("")

    lines.append("| 模式 | 命中次数 |")
    lines.append("|---|---:|")
    for k, v in pattern_counts.items():
        lines.append(f"| `{k}` | {v} |")
    lines.append("")

    lines.append("| 行号 | 时间戳(ms) | 类型f | 级别 | 片段 |")
    lines.append("|---:|---:|---:|---|---|")
    for e in preview[:15]:
        content = _mask_sensitive_text(str(e.get("content", ""))).replace("\n", " ").replace("|", "\\|")
        lines.append(
            f"| {e.get('line_no', '')} | {e.get('timestamp_ms', '')} | {e.get('log_type', '')} | "
            f"{e.get('level', '')} | {content[:140]} |"
        )
    lines.append("")

    if data_limitations:
        lines.append("数据局限性：")
        for item in data_limitations:
            lines.append(f"- {_mask_sensitive_text(str(item))}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def analyze_and_generate_report(
    log_path: str,
    source_root: str = "source/GZCheSuPaiApp",
    rule_path: str = "source/log_rule.md",
    start_ts_ms: Optional[int] = None,
    end_ts_ms: Optional[int] = None,
    log_type: Optional[int] = None,
    level: Optional[str] = None,
    keywords: Optional[str] = None,
    max_output_lines: int = 1000,
    title: str = "日志分析报告",
    output_dir: str = "output",
) -> str:
    """一键执行：日志筛选 + 日志源码联合分析 + Markdown 报告输出。"""
    analysis = analyze_log_with_source(
        log_path=log_path,
        source_root=source_root,
        rule_path=rule_path,
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
        log_type=log_type,
        level=level,
        keywords=keywords,
        max_output_lines=max_output_lines,
    )
    report_markdown = generate_markdown_report(analysis=analysis, title=title)

    # 文件名规则：取 log_path 的文件名，后缀统一改为 .md。
    log_filename = Path(log_path).name or "report.log"
    report_filename = f"{Path(log_filename).stem}.md"
    output_path = Path(_abs_path(output_dir)) / report_filename
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_markdown, encoding="utf-8")

    return report_markdown


def list_skills() -> dict[str, Any]:
    """列出项目中可路由的 Skill 与对应 Agent/Tool。"""
    skills = []
    for skill_name, meta in SKILL_DEFINITIONS.items():
        skills.append(
            {
                "skill_name": skill_name,
                "delegated_agent": meta.get("delegated_agent", ""),
                "tool": meta.get("tool", ""),
                "aliases": meta.get("aliases", []),
                "description": meta.get("description", ""),
            }
        )

    return {
        "skills": skills,
        "usage_hint": (
            "调用 route_by_skill 时，skill_name 可传规范名（如 log-filter-assistant）"
            "或别名（如 filter/report/orchestrator）。"
        ),
    }


def route_by_skill(
    skill_name: str,
    log_path: Optional[str] = None,
    source_root: str = "source/GZCheSuPaiApp",
    rule_path: str = "source/log_rule.md",
    start_ts_ms: Optional[int] = None,
    end_ts_ms: Optional[int] = None,
    log_type: Optional[int] = None,
    level: Optional[str] = None,
    keywords: Optional[str] = None,
    max_output_lines: int = 1000,
    title: str = "日志分析报告",
    output_dir: str = "output",
) -> dict[str, Any]:
    """根据 Skill 名称做确定性路由，避免纯自然语言调度不稳定。"""
    normalized_skill = _normalize_skill_name(skill_name)
    meta = SKILL_DEFINITIONS.get(normalized_skill)
    if not meta:
        return {
            "error": f"未知 skill_name: {skill_name}",
            "normalized_skill_name": normalized_skill,
            "available_skills": list(SKILL_DEFINITIONS.keys()),
        }

    def _require_log_path() -> Optional[dict[str, Any]]:
        if log_path:
            return None
        return {
            "error": "该 Skill 需要 log_path 参数。",
            "normalized_skill_name": normalized_skill,
            "required_args": ["log_path"],
        }

    if normalized_skill == "log-filter-assistant":
        missing = _require_log_path()
        if missing:
            return missing
        result = filter_logs(
            log_path=log_path or "",
            start_ts_ms=start_ts_ms,
            end_ts_ms=end_ts_ms,
            log_type=log_type,
            level=level,
            keywords=keywords,
            max_output_lines=max_output_lines,
        )
        return {
            "skill_name": skill_name,
            "normalized_skill_name": normalized_skill,
            "delegated_agent": meta["delegated_agent"],
            "tool": meta["tool"],
            "result": result,
        }

    if normalized_skill == "source-correlation-assistant":
        missing = _require_log_path()
        if missing:
            return missing
        result = analyze_log_with_source(
            log_path=log_path or "",
            source_root=source_root,
            rule_path=rule_path,
            start_ts_ms=start_ts_ms,
            end_ts_ms=end_ts_ms,
            log_type=log_type,
            level=level,
            keywords=keywords,
            max_output_lines=max_output_lines,
        )
        return {
            "skill_name": skill_name,
            "normalized_skill_name": normalized_skill,
            "delegated_agent": meta["delegated_agent"],
            "tool": meta["tool"],
            "result": result,
        }

    if normalized_skill == "crisp-l-report-assistant":
        missing = _require_log_path()
        if missing:
            return missing
        report_markdown = analyze_and_generate_report(
            log_path=log_path or "",
            source_root=source_root,
            rule_path=rule_path,
            start_ts_ms=start_ts_ms,
            end_ts_ms=end_ts_ms,
            log_type=log_type,
            level=level,
            keywords=keywords,
            max_output_lines=max_output_lines,
            title=title,
            output_dir=output_dir,
        )
        report_path = str(Path(_abs_path(output_dir)) / f"{Path(log_path or '').stem}.md")
        return {
            "skill_name": skill_name,
            "normalized_skill_name": normalized_skill,
            "delegated_agent": meta["delegated_agent"],
            "tool": meta["tool"],
            "report_path": report_path,
            "report_preview": report_markdown.splitlines()[:12],
            "report_markdown": report_markdown,
        }

    # log-orchestrator-assistant：强制链路执行 filter -> analysis -> report
    missing = _require_log_path()
    if missing:
        return missing

    filtered = filter_logs(
        log_path=log_path or "",
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
        log_type=log_type,
        level=level,
        keywords=keywords,
        max_output_lines=max_output_lines,
    )
    analysis = analyze_log_with_source(
        log_path=log_path or "",
        source_root=source_root,
        rule_path=rule_path,
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
        log_type=log_type,
        level=level,
        keywords=keywords,
        max_output_lines=max_output_lines,
    )
    report_markdown = generate_markdown_report(analysis=analysis, title=title)
    report_path = Path(_abs_path(output_dir)) / f"{Path(log_path or '').stem}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_markdown, encoding="utf-8")

    return {
        "skill_name": skill_name,
        "normalized_skill_name": normalized_skill,
        "delegated_agent": meta["delegated_agent"],
        "tool": meta["tool"],
        "pipeline": ["filter_logs", "analyze_log_with_source", "generate_markdown_report"],
        "report_path": str(report_path),
        "filter_summary": {
            "total_entries": filtered.get("total_entries", 0),
            "matched_entries": filtered.get("matched_entries", 0),
            "returned_entries": filtered.get("returned_entries", 0),
            "dropped_entries": filtered.get("dropped_entries", 0),
        },
        "analysis_summary": {
            "anomaly_count": len(analysis.get("anomalies", []) or []),
            "source_hit_count": len(analysis.get("source_correlations", []) or []),
        },
        "report_preview": report_markdown.splitlines()[:12],
        "report_markdown": report_markdown,
    }
