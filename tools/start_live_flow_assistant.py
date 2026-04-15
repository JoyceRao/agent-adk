import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

from .crisp_l_report_assistant import generate_markdown_report
from .source_correlation_assistant import analyze_log_with_source
from .shared import (
    START_LIVE_FAILURE_STAGES,
    START_LIVE_STAGE_MAP,
    START_LIVE_SUCCESS_TERMINAL_STAGES,
    _abs_path,
    _aggregate_extra_values,
    _apply_filters,
    _extract_embedded_json_from_content,
    _normalize_c_startswith,
    _normalize_extra_value,
    _parse_log_file,
    _resolve_log_path,
    _ts_ms_to_text,
)

DEFAULT_START_LIVE_REPORT_FILENAME = "start_live_flow_report.md"
DEFAULT_START_LIVE_JSON_FILENAME = "start_live_flow_report.json"


def resolve_start_live_output_filenames(
    log_path: str,
    report_filename: Optional[str] = None,
    json_filename: Optional[str] = None,
) -> tuple[str, str]:
    """解析 start-live 报告输出文件名，默认按日志文件名生成。"""
    log_filename = Path(log_path).name or "report.log"
    log_stem = Path(log_filename).stem or "report"

    normalized_report_filename = str(report_filename or "").strip()
    if (
        not normalized_report_filename
        or normalized_report_filename == DEFAULT_START_LIVE_REPORT_FILENAME
    ):
        normalized_report_filename = f"{log_stem}.md"

    normalized_json_filename = str(json_filename or "").strip()
    if (
        not normalized_json_filename
        or normalized_json_filename == DEFAULT_START_LIVE_JSON_FILENAME
    ):
        normalized_json_filename = f"{log_stem}.json"

    return normalized_report_filename, normalized_json_filename


def analyze_start_live_flow(
    log_path: str,
    start_ts_ms: Optional[int] = None,
    end_ts_ms: Optional[int] = None,
    c_startswith: str = "1",
    keywords: str = "CSP_BIZ_WATCHCAR_STARTLIVE,flowId",
    max_flows: int = 1000,
    include_stage_path: bool = True,
    exclude_last_stage: str = "recover_check_start",
) -> dict[str, Any]:
    """按 flowId 分组分析开播链路，输出最后流程与 stage。"""
    all_entries = _parse_log_file(log_path)
    filtered_any_keyword = _apply_filters(
        entries=all_entries,
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
        c_startswith=c_startswith,
        keywords=keywords,
    )

    required_keywords = [k.strip() for k in (keywords or "").split(",") if k.strip()]
    filtered_all_keywords = [
        e for e in filtered_any_keyword if all(k in e.content for k in required_keywords)
    ]

    flow_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stage_counter: Counter[str] = Counter()

    for e in filtered_all_keywords:
        payload = _extract_embedded_json_from_content(e.content)
        if not isinstance(payload, dict):
            continue

        if str(payload.get("logEventid", "")).strip() != "CSP_BIZ_WATCHCAR_STARTLIVE":
            continue

        flow_id = str(payload.get("flowId", "")).strip()
        if not flow_id:
            continue

        stage = str(payload.get("stage", "")).strip()
        if stage:
            stage_counter[stage] += 1

        flow_events[flow_id].append(
            {
                "line_no": e.line_no,
                "timestamp_ms": e.timestamp_ms,
                "stage": stage,
                "payload": payload,
            }
        )

    output_flows: list[dict[str, Any]] = []
    extra_keys = ["reserveId", "sceneid", "dealer_id", "opl_user_id"]

    for flow_id, events in flow_events.items():
        sorted_events = sorted(events, key=lambda x: (int(x["timestamp_ms"]), int(x["line_no"])))
        first_event = sorted_events[0]
        last_event = sorted_events[-1]

        mapped_stage_hits: list[tuple[int, int, str, str]] = []
        for idx, ev in enumerate(sorted_events):
            stage = str(ev.get("stage", "")).strip()
            stage_info = START_LIVE_STAGE_MAP.get(stage)
            if not stage_info:
                continue
            mapped_stage_hits.append(
                (
                    int(stage_info.get("order", 0)),
                    idx,
                    stage,
                    str(stage_info.get("process", "未知流程")),
                )
            )

        last_stage_by_time = str(last_event.get("stage", "")).strip()
        last_process = "未知流程"
        last_stage = last_stage_by_time

        if mapped_stage_hits:
            _, _, best_stage, best_process = max(mapped_stage_hits, key=lambda x: (x[0], x[1]))
            last_stage = best_stage
            last_process = best_process

        if (
            last_stage_by_time in START_LIVE_FAILURE_STAGES
            or last_stage in START_LIVE_FAILURE_STAGES
        ):
            status = "failure_end"
        elif (
            last_stage_by_time in START_LIVE_SUCCESS_TERMINAL_STAGES
            or last_stage in START_LIVE_SUCCESS_TERMINAL_STAGES
        ):
            status = "success_end"
        elif last_process == "未知流程":
            status = "unknown"
        else:
            status = "in_progress"

        stage_path: list[str] = []
        if include_stage_path:
            seen_stage: set[str] = set()
            for ev in sorted_events:
                stage = str(ev.get("stage", "")).strip()
                if not stage or stage in seen_stage:
                    continue
                seen_stage.add(stage)
                stage_path.append(stage)

        extras_raw: dict[str, list[str]] = {k: [] for k in extra_keys}
        for ev in sorted_events:
            payload = ev.get("payload", {})
            for key in extra_keys:
                extras_raw[key].append(_normalize_extra_value(payload.get(key)))
        extras = {k: _aggregate_extra_values(v) for k, v in extras_raw.items()}

        output_flows.append(
            {
                "flowId": flow_id,
                "event_count": len(sorted_events),
                "first_line_no": int(first_event.get("line_no", 0)),
                "last_line_no": int(last_event.get("line_no", 0)),
                "first_ts_ms": int(first_event.get("timestamp_ms", 0)),
                "first_ts_text": _ts_ms_to_text(int(first_event.get("timestamp_ms", 0))),
                "last_ts_ms": int(last_event.get("timestamp_ms", 0)),
                "last_ts_text": _ts_ms_to_text(int(last_event.get("timestamp_ms", 0))),
                "last_process": last_process,
                "last_stage": last_stage,
                "last_stage_by_time": last_stage_by_time,
                "status": status,
                "extra": extras,
                "stage_path": stage_path if include_stage_path else [],
            }
        )

    normalized_exclude_stage = str(exclude_last_stage or "").strip()
    excluded_flow_ids: list[str] = []
    effective_flows = output_flows
    if normalized_exclude_stage:
        effective_flows = []
        for flow in output_flows:
            last_stage = str(flow.get("last_stage", "")).strip()
            last_stage_by_time = str(flow.get("last_stage_by_time", "")).strip()
            if (
                last_stage == normalized_exclude_stage
                or last_stage_by_time == normalized_exclude_stage
            ):
                excluded_flow_ids.append(str(flow.get("flowId", "")).strip())
                continue
            effective_flows.append(flow)

    effective_flows.sort(
        key=lambda x: (int(x.get("first_ts_ms", 0)), str(x.get("flowId", "")))
    )
    safe_max_flows = max(1, int(max_flows or 1000))
    clipped_flows = effective_flows[:safe_max_flows]
    dropped_flows = max(0, len(effective_flows) - len(clipped_flows))

    status_distribution = Counter(x.get("status", "unknown") for x in effective_flows)
    stage_distribution = dict(sorted(stage_counter.items(), key=lambda x: (-x[1], x[0])))

    return {
        "meta": {
            "log_path": _resolve_log_path(log_path),
            "analysis_type": "start_live_flow",
        },
        "summary": {
            "total_entries": len(all_entries),
            "matched_entries": sum(len(v) for v in flow_events.values()),
            "flow_count": len(effective_flows),
            "returned_flow_count": len(clipped_flows),
            "dropped_flow_count": dropped_flows,
            "excluded_flow_count": len(excluded_flow_ids),
            "excluded_flow_ids": excluded_flow_ids,
            "start_ts_ms": start_ts_ms,
            "end_ts_ms": end_ts_ms,
            "filter": {
                "c_startswith": _normalize_c_startswith(c_startswith) or None,
                "keywords": required_keywords,
                "keyword_match": "AND",
                "exclude_last_stage": normalized_exclude_stage or None,
            },
            "status_distribution": dict(sorted(status_distribution.items(), key=lambda x: x[0])),
            "stage_distribution": stage_distribution,
        },
        "flows": clipped_flows,
    }


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _duration_stats_ms(flows: list[dict[str, Any]]) -> dict[str, Any]:
    durations: list[int] = []
    for flow in flows:
        first_ts = int(flow.get("first_ts_ms", 0) or 0)
        last_ts = int(flow.get("last_ts_ms", 0) or 0)
        if first_ts > 0 and last_ts >= first_ts:
            durations.append(last_ts - first_ts)

    if not durations:
        return {
            "sample_size": 0,
            "avg_ms": 0,
            "p50_ms": 0,
            "p95_ms": 0,
            "max_ms": 0,
        }

    sorted_values = sorted(durations)
    n = len(sorted_values)
    p50_idx = int((n - 1) * 0.50)
    p95_idx = int((n - 1) * 0.95)

    return {
        "sample_size": n,
        "avg_ms": int(sum(sorted_values) / n),
        "p50_ms": int(sorted_values[p50_idx]),
        "p95_ms": int(sorted_values[p95_idx]),
        "max_ms": int(sorted_values[-1]),
    }


def _build_flow_evidence_lines(
    flows: list[dict[str, Any]],
    status: Optional[str] = None,
    limit: int = 3,
) -> list[str]:
    lines: list[str] = []
    for flow in flows:
        if status and str(flow.get("status", "")) != status:
            continue
        stage = str(flow.get("last_stage", "")).strip() or str(
            flow.get("last_stage_by_time", "")
        ).strip()
        lines.append(
            "flowId={flow_id}, stage={stage}, window={start}->{end}".format(
                flow_id=flow.get("flowId", ""),
                stage=stage,
                start=flow.get("first_ts_text", ""),
                end=flow.get("last_ts_text", ""),
            )
        )
        if len(lines) >= limit:
            break
    return lines


def _merge_start_live_and_source(
    start_live_analysis: dict[str, Any],
    source_analysis: dict[str, Any],
    source_root: str,
    rule_path: str,
) -> dict[str, Any]:
    meta = start_live_analysis.get("meta", {}) or {}
    summary = start_live_analysis.get("summary", {}) or {}
    flows = start_live_analysis.get("flows", []) or []

    source_filter_summary = source_analysis.get("filter_summary", {}) or {}
    source_pattern_counts = source_analysis.get("pattern_counts", {}) or {}
    source_correlations = source_analysis.get("source_correlations", []) or []
    source_preview = source_analysis.get("evidence_preview", []) or []
    source_rule_excerpt = source_analysis.get("rule_excerpt", "")

    status_distribution = summary.get("status_distribution", {}) or {}
    flow_count = int(summary.get("flow_count", len(flows)) or len(flows))
    success_count = int(status_distribution.get("success_end", 0) or 0)
    failure_count = int(status_distribution.get("failure_end", 0) or 0)
    in_progress_count = int(status_distribution.get("in_progress", 0) or 0)
    unknown_count = int(status_distribution.get("unknown", 0) or 0)

    success_ratio = _safe_ratio(success_count, flow_count)
    failure_ratio = _safe_ratio(failure_count, flow_count)
    in_progress_ratio = _safe_ratio(in_progress_count, flow_count)

    duration_stats = _duration_stats_ms(flows)

    last_stage_counter: Counter[str] = Counter(
        str(x.get("last_stage", "")).strip() or str(x.get("last_stage_by_time", "")).strip()
        for x in flows
    )
    top_last_stages = [
        {
            "stage": stage,
            "count": count,
            "ratio": _safe_ratio(count, max(1, flow_count)),
        }
        for stage, count in last_stage_counter.most_common(5)
        if stage
    ]

    stage_distribution = summary.get("stage_distribution", {}) or {}
    stage_distribution_items = sorted(stage_distribution.items(), key=lambda x: (-x[1], x[0]))

    source_anomalies = source_analysis.get("anomalies", []) or []
    raw_problems: list[dict[str, Any]] = []

    if flow_count <= 0:
        raw_problems.append(
            {
                "conclusion": "start-live 固定筛选后未命中有效 flowId，当前窗口无法给出链路结论",
                "impact": "无法评估开播成功率与卡点阶段，需先补齐样本。",
                "severity": "P2",
                "confidence": "低",
                "trigger_condition": "过滤条件为 c_startswith=1 且关键词同时包含 CSP_BIZ_WATCHCAR_STARTLIVE、flowId",
                "evidence_summary": "flow_count=0",
                "suggestion": "扩大时间窗口或补充日志源后重跑分析。",
                "stage": "筛选阶段",
            }
        )

    if failure_count > 0:
        failure_stage_counter = Counter(
            str(flow.get("last_stage", "")).strip() for flow in flows if flow.get("status") == "failure_end"
        )
        top_failure_stage, top_failure_count = ("failure_end", failure_count)
        if failure_stage_counter:
            top_failure_stage, top_failure_count = failure_stage_counter.most_common(1)[0]
        evidence_lines = _build_flow_evidence_lines(flows, status="failure_end", limit=3)
        raw_problems.append(
            {
                "conclusion": f"开播链路存在失败收敛，主要停留在 `{top_failure_stage}`",
                "impact": f"failure_end={failure_count}/{flow_count}（{_pct(failure_ratio)}），影响用户开播成功。",
                "severity": "P0" if failure_ratio >= 0.2 else "P1",
                "confidence": "高" if failure_count >= 5 else "中",
                "trigger_condition": "flow 最终状态命中 failure_end",
                "evidence_summary": "; ".join(evidence_lines) if evidence_lines else "failure_end flow 存在",
                "suggestion": (
                    "优先按失败 stage 回放请求参数、接口返回与分支条件，补充埋点验证失败原因。"
                ),
                "stage": top_failure_stage,
                "top_failure_count": top_failure_count,
            }
        )

    if in_progress_count > 0:
        in_progress_stage_counter = Counter(
            str(flow.get("last_stage", "")).strip()
            for flow in flows
            if flow.get("status") == "in_progress"
        )
        top_in_progress_stage, _ = ("in_progress", in_progress_count)
        if in_progress_stage_counter:
            top_in_progress_stage, _ = in_progress_stage_counter.most_common(1)[0]
        evidence_lines = _build_flow_evidence_lines(flows, status="in_progress", limit=3)
        raw_problems.append(
            {
                "conclusion": f"链路存在未收敛 flow，主要卡在 `{top_in_progress_stage}`",
                "impact": f"in_progress={in_progress_count}/{flow_count}（{_pct(in_progress_ratio)}），可能造成开播卡顿或停留。",
                "severity": "P1" if in_progress_ratio >= 0.3 else "P2",
                "confidence": "高" if in_progress_count >= 10 else "中",
                "trigger_condition": "flow 未进入 success_end / failure_end 收敛阶段",
                "evidence_summary": "; ".join(evidence_lines) if evidence_lines else "存在 in_progress flow",
                "suggestion": "按 stage_path 回放卡点前后 3~5 条日志，核对分支条件与异步回调时序。",
                "stage": top_in_progress_stage,
            }
        )

    if unknown_count > 0:
        raw_problems.append(
            {
                "conclusion": "存在未映射 stage，流程语义不完整",
                "impact": f"unknown={unknown_count}/{flow_count}，会降低阶段判定准确性。",
                "severity": "P2",
                "confidence": "中",
                "trigger_condition": "last_process 为“未知流程”或 stage 未命中映射",
                "evidence_summary": f"unknown_count={unknown_count}",
                "suggestion": "对齐 startLive.puml 与线上埋点，补充 stage->process 映射。",
                "stage": "stage_map",
            }
        )

    if source_anomalies:
        top_anomaly = source_anomalies[0]
        raw_problems.append(
            {
                "conclusion": f"通用异常分析检测到风险信号：{top_anomaly.get('name', '')}",
                "impact": "日志+源码联合分析命中异常模式，可能放大开播失败或卡点问题。",
                "severity": str(top_anomaly.get("severity", "P1") or "P1"),
                "confidence": "中",
                "trigger_condition": "analyze_log_with_source 命中异常模式统计阈值",
                "evidence_summary": str(top_anomaly.get("evidence", "")),
                "suggestion": str(top_anomaly.get("advice", "")) or "按源码命中点补齐埋点并验证分支。",
                "stage": "source_correlation",
            }
        )

    if not raw_problems:
        raw_problems.append(
            {
                "conclusion": "当前窗口未发现显著失败终点，开播链路整体可达",
                "impact": "风险较低，但仍需持续关注卡点阶段分布。",
                "severity": "P2",
                "confidence": "中",
                "trigger_condition": "success_end 占比较高且无 failure_end",
                "evidence_summary": f"success_end={success_count}/{flow_count}",
                "suggestion": "持续观察成功率与耗时，若回落再放大窗口分析。",
                "stage": "steady_state",
            }
        )

    problems: list[dict[str, Any]] = []
    scenarios: list[dict[str, Any]] = []
    plan_actions: list[dict[str, Any]] = []

    owner_map = {
        "recover": "恢复链路客户端负责人",
        "precreate": "开播前置链路客户端 + 接口负责人",
        "livevc": "直播页客户端 + 服务端负责人",
        "source_correlation": "客户端负责人 + 接口负责人",
        "stage_map": "日志平台与客户端埋点负责人",
    }

    for idx, item in enumerate(raw_problems, start=1):
        problem_id = f"SL-{idx:02d}"
        problem = {
            "problem_id": problem_id,
            "conclusion": item.get("conclusion", ""),
            "impact": item.get("impact", ""),
            "severity": item.get("severity", "P2"),
            "confidence": item.get("confidence", "中"),
            "trigger_condition": item.get("trigger_condition", ""),
            "evidence_summary": item.get("evidence_summary", ""),
            "suggestion": item.get("suggestion", ""),
            "stage": item.get("stage", ""),
        }
        problems.append(problem)

        stage_text = str(problem.get("stage", ""))
        owner_hint = "客户端负责人 + 接口负责人"
        for prefix, owner in owner_map.items():
            if stage_text.startswith(prefix) or stage_text == prefix:
                owner_hint = owner
                break

        scenarios.append(
            {
                "problem_id": problem_id,
                "trigger_condition": problem.get("trigger_condition", ""),
                "repro_hint": (
                    "按问题 flowId 回放 stage_path，重点关注卡点 stage 前后 3 条日志与接口返回。"
                ),
                "key_evidence": problem.get("evidence_summary", ""),
            }
        )

        priority = {"P0": "立即", "P1": "高", "P2": "中"}.get(
            str(problem.get("severity", "P2")), "中"
        )
        plan_actions.append(
            {
                "problem_id": problem_id,
                "priority": priority,
                "stage": stage_text or "排查",
                "action": problem.get("suggestion", ""),
                "owner_hint": owner_hint,
                "acceptance_criteria": (
                    "开播 success_end 占比提升且卡点 stage 占比下降，核心链路无新增 failure_end。"
                ),
            }
        )

    indicators = [
        {
            "name": "start-live flow 数",
            "value": str(flow_count),
            "formula": "count(flowId)",
            "confidence_interval": "N/A（计数指标）",
            "sample_size": flow_count,
        },
        {
            "name": "开播成功率",
            "value": _pct(success_ratio),
            "formula": "success_end / flow_count",
            "confidence_interval": "N/A（描述性指标）",
            "sample_size": flow_count,
        },
        {
            "name": "失败终止率",
            "value": _pct(failure_ratio),
            "formula": "failure_end / flow_count",
            "confidence_interval": "N/A（描述性指标）",
            "sample_size": flow_count,
        },
        {
            "name": "未收敛占比",
            "value": _pct(in_progress_ratio),
            "formula": "in_progress / flow_count",
            "confidence_interval": "N/A（描述性指标）",
            "sample_size": flow_count,
        },
        {
            "name": "链路平均耗时(ms)",
            "value": str(duration_stats.get("avg_ms", 0)),
            "formula": "avg(last_ts_ms - first_ts_ms)",
            "confidence_interval": "N/A（描述性指标）",
            "sample_size": int(duration_stats.get("sample_size", 0)),
        },
        {
            "name": "链路P95耗时(ms)",
            "value": str(duration_stats.get("p95_ms", 0)),
            "formula": "p95(last_ts_ms - first_ts_ms)",
            "confidence_interval": "N/A（描述性指标）",
            "sample_size": int(duration_stats.get("sample_size", 0)),
        },
    ]

    significance_notes = [
        "start-live 结论按 flowId 聚合，并以 stage 映射后的流程顺序判定最后流程。",
        "固定筛选条件采用 AND：同时命中 CSP_BIZ_WATCHCAR_STARTLIVE 与 flowId。",
    ]
    if summary.get("excluded_flow_count", 0):
        significance_notes.append(
            f"已按 exclude_last_stage 过滤 {summary.get('excluded_flow_count', 0)} 个 flow。"
        )
    if summary.get("dropped_flow_count", 0):
        significance_notes.append(
            f"受 max_flows 限制，额外裁剪 {summary.get('dropped_flow_count', 0)} 个 flow。"
        )
    if top_last_stages:
        significance_notes.append(
            "当前主要末尾 stage 分布："
            + "、".join(
                [f"{x['stage']}({_pct(x['ratio'])})" for x in top_last_stages[:3]]
            )
        )

    source_limitations = (
        source_analysis.get("crisp_l", {})
        .get("indicators", {})
        .get("data_limitations", [])
        or []
    )
    data_limitations = [str(x) for x in source_limitations if str(x).strip()]
    if flow_count <= 0:
        data_limitations.append("当前窗口未命中有效 flowId，start-live 指标无法评估。")
    if flow_count < 5 and flow_count > 0:
        data_limitations.append("flow 样本量较小，建议扩大时间窗口后复核。")

    summary_text = (
        "start-live 共命中 {flow_count} 个 flow，成功 {success_count} 个（{success_rate}），"
        "失败 {failure_count} 个（{failure_rate}），未收敛 {in_progress_count} 个（{in_progress_rate}）。"
    ).format(
        flow_count=flow_count,
        success_count=success_count,
        success_rate=_pct(success_ratio),
        failure_count=failure_count,
        failure_rate=_pct(failure_ratio),
        in_progress_count=in_progress_count,
        in_progress_rate=_pct(in_progress_ratio),
    )

    loop_checkpoints = [
        {
            "window": "T+1h",
            "metric": "开播成功率",
            "target": ">= 95%",
            "current": _pct(success_ratio),
        },
        {
            "window": "T+24h",
            "metric": "失败终止率",
            "target": "<= 3%",
            "current": _pct(failure_ratio),
        },
        {
            "window": "T+24h",
            "metric": "P95链路耗时(ms)",
            "target": "<= 5000",
            "current": str(duration_stats.get("p95_ms", 0)),
        },
    ]

    loop_alerts = [
        "若开播成功率连续两个观察窗口低于 90%，触发 P0 告警。",
        "若同一失败 stage 在 1 小时内占比 > 20%，触发专项排查告警。",
    ]

    rollback_rule = "修复上线后 2 小时内 success_end 无提升且 failure_end 增长时执行回滚。"

    merged_filter_summary = {
        "total_entries": int(source_filter_summary.get("total_entries", summary.get("total_entries", 0))),
        "matched_entries": int(source_filter_summary.get("matched_entries", summary.get("matched_entries", 0))),
        "returned_entries": int(source_filter_summary.get("returned_entries", 0)),
        "dropped_entries": int(source_filter_summary.get("dropped_entries", 0)),
        "pattern_count_basis": "full_filtered_entries",
        "type_distribution": source_filter_summary.get("type_distribution", {}),
        "flow_count": flow_count,
    }

    merged_pattern_counts = dict(source_pattern_counts)
    merged_pattern_counts.update(
        {
            "start_live_flow_count": flow_count,
            "start_live_success_count": success_count,
            "start_live_failure_count": failure_count,
            "start_live_in_progress_count": in_progress_count,
        }
    )
    for stage, count in stage_distribution_items[:5]:
        merged_pattern_counts[f"start_live_stage::{stage}"] = int(count)

    anomalies = [
        {
            "name": p.get("conclusion", ""),
            "severity": p.get("severity", "P2"),
            "evidence": p.get("evidence_summary", ""),
            "advice": p.get("suggestion", ""),
        }
        for p in problems
    ]

    flow_preview: list[dict[str, Any]] = []
    for flow in flows[:10]:
        stage_path = flow.get("stage_path", []) or []
        stage_path_text = " -> ".join([str(x) for x in stage_path if str(x).strip()])
        flow_preview.append(
            {
                "line_no": flow.get("first_line_no", ""),
                "timestamp_ms": flow.get("first_ts_ms", ""),
                "log_type": 1,
                "level": str(flow.get("status", "")),
                "content": (
                    "flowId={flow_id}, last_process={process}, last_stage={stage}, stage_path={path}".format(
                        flow_id=flow.get("flowId", ""),
                        process=flow.get("last_process", ""),
                        stage=flow.get("last_stage", ""),
                        path=stage_path_text,
                    )
                ),
            }
        )

    merged_evidence_preview = (source_preview[:40] + flow_preview)[:50]

    return {
        "meta": {
            "analysis_type": "start_live_flow_crisp_l",
            "log_path": str(meta.get("log_path", "")),
            "source_root": _abs_path(source_root),
            "rule_path": _abs_path(rule_path),
        },
        "filter_summary": merged_filter_summary,
        "pattern_counts": merged_pattern_counts,
        "anomalies": anomalies,
        "evidence_preview": merged_evidence_preview,
        "source_correlations": source_correlations,
        "rule_excerpt": source_rule_excerpt,
        "crisp_l": {
            "conclusion": {
                "summary": summary_text,
                "problems": problems,
            },
            "reproduction": {
                "scenarios": scenarios,
            },
            "indicators": {
                "metrics": indicators,
                "significance_notes": significance_notes,
                "data_limitations": data_limitations,
            },
            "source_correlation": {
                "hits": source_correlations,
            },
            "plan": {
                "actions": plan_actions,
            },
            "loop_closure": {
                "checkpoints": loop_checkpoints,
                "alerts": loop_alerts,
                "rollback_rule": rollback_rule,
            },
        },
    }


def analyze_start_live_flow_with_source(
    log_path: str,
    source_root: str = "source/GZCheSuPaiApp",
    rule_path: str = "source/log_rule.md",
    start_ts_ms: Optional[int] = None,
    end_ts_ms: Optional[int] = None,
    c_startswith: str = "1",
    keywords: str = "CSP_BIZ_WATCHCAR_STARTLIVE,flowId",
    max_flows: int = 1000,
    include_stage_path: bool = True,
    exclude_last_stage: str = "recover_check_start",
    max_output_lines: int = 1000,
) -> dict[str, Any]:
    """联合输出 start-live flow 聚合 + 通用源码关联分析 + CRISP-L 融合结果。"""
    start_live_analysis = analyze_start_live_flow(
        log_path=log_path,
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
        c_startswith=c_startswith,
        keywords=keywords,
        max_flows=max_flows,
        include_stage_path=include_stage_path,
        exclude_last_stage=exclude_last_stage,
    )

    source_analysis = analyze_log_with_source(
        log_path=log_path,
        source_root=source_root,
        rule_path=rule_path,
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
        keywords=keywords,
        c_startswith=c_startswith,
        keyword_match="AND",
        max_output_lines=max_output_lines,
    )

    merged_analysis = _merge_start_live_and_source(
        start_live_analysis=start_live_analysis,
        source_analysis=source_analysis,
        source_root=source_root,
        rule_path=rule_path,
    )

    return {
        "meta": {
            "analysis_type": "start_live_flow_crisp_l",
            "log_path": _resolve_log_path(log_path),
            "source_root": _abs_path(source_root),
            "rule_path": _abs_path(rule_path),
        },
        "start_live_analysis": start_live_analysis,
        "source_analysis": source_analysis,
        "merged_analysis": merged_analysis,
    }


def generate_start_live_flow_markdown(
    analysis: dict[str, Any],
    title: str = "startLive 开播链路日志报告",
) -> str:
    """兼容旧接口：统一复用 CRISP-L 报告渲染。"""
    if "merged_analysis" in analysis:
        report_input = analysis.get("merged_analysis", {}) or {}
    elif "crisp_l" in analysis and "filter_summary" in analysis:
        report_input = analysis
    elif "summary" in analysis and "flows" in analysis:
        report_input = _merge_start_live_and_source(
            start_live_analysis=analysis,
            source_analysis={},
            source_root="source/GZCheSuPaiApp",
            rule_path="source/log_rule.md",
        )
    else:
        report_input = {
            "meta": {
                "analysis_type": "start_live_flow_crisp_l",
                "log_path": "",
                "source_root": _abs_path("source/GZCheSuPaiApp"),
                "rule_path": _abs_path("source/log_rule.md"),
            },
            "filter_summary": {
                "total_entries": 0,
                "matched_entries": 0,
                "returned_entries": 0,
                "dropped_entries": 0,
            },
            "pattern_counts": {},
            "source_correlations": [],
            "evidence_preview": [],
            "crisp_l": {
                "conclusion": {
                    "summary": "未提供可渲染的 start-live 分析结构。",
                    "problems": [],
                },
                "reproduction": {"scenarios": []},
                "indicators": {
                    "metrics": [],
                    "significance_notes": [],
                    "data_limitations": ["输入分析结构缺失，无法生成完整结论。"],
                },
                "source_correlation": {"hits": []},
                "plan": {"actions": []},
                "loop_closure": {
                    "checkpoints": [],
                    "alerts": [],
                    "rollback_rule": "",
                },
            },
        }
    return generate_markdown_report(analysis=report_input, title=title)


def analyze_start_live_flow_and_generate_crisp_l_report(
    log_path: str,
    source_root: str = "source/GZCheSuPaiApp",
    rule_path: str = "source/log_rule.md",
    start_ts_ms: Optional[int] = None,
    end_ts_ms: Optional[int] = None,
    c_startswith: str = "1",
    keywords: str = "CSP_BIZ_WATCHCAR_STARTLIVE,flowId",
    max_flows: int = 1000,
    include_stage_path: bool = True,
    exclude_last_stage: str = "recover_check_start",
    max_output_lines: int = 1000,
    title: str = "startLive 开播链路日志报告",
    output_dir: str = "output",
    report_filename: Optional[str] = None,
    json_filename: Optional[str] = None,
) -> dict[str, Any]:
    """一键执行 start-live 融合分析并输出 CRISP-L 报告。"""
    analysis = analyze_start_live_flow_with_source(
        log_path=log_path,
        source_root=source_root,
        rule_path=rule_path,
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
        c_startswith=c_startswith,
        keywords=keywords,
        max_flows=max_flows,
        include_stage_path=include_stage_path,
        exclude_last_stage=exclude_last_stage,
        max_output_lines=max_output_lines,
    )

    report_markdown = generate_markdown_report(
        analysis=analysis.get("merged_analysis", {}) or {},
        title=title,
    )

    output_path = Path(_abs_path(output_dir))
    output_path.mkdir(parents=True, exist_ok=True)
    normalized_report_filename, normalized_json_filename = resolve_start_live_output_filenames(
        log_path=log_path,
        report_filename=report_filename,
        json_filename=json_filename,
    )

    report_path = output_path / normalized_report_filename
    json_path = output_path / normalized_json_filename
    report_path.write_text(report_markdown, encoding="utf-8")

    analysis["artifacts"] = {
        "report_path": str(report_path),
        "json_path": str(json_path),
    }
    json_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "analysis": analysis,
        "report_markdown": report_markdown,
        "report_path": str(report_path),
        "json_path": str(json_path),
    }


def analyze_start_live_flow_and_generate_report(
    log_path: str,
    start_ts_ms: Optional[int] = None,
    end_ts_ms: Optional[int] = None,
    c_startswith: str = "1",
    keywords: str = "CSP_BIZ_WATCHCAR_STARTLIVE,flowId",
    max_flows: int = 1000,
    include_stage_path: bool = True,
    exclude_last_stage: str = "recover_check_start",
    title: str = "startLive 开播链路日志报告",
    output_dir: str = "output",
    report_filename: Optional[str] = None,
    json_filename: Optional[str] = None,
) -> dict[str, Any]:
    """兼容旧函数名：行为升级为 CRISP-L 融合报告链路。"""
    return analyze_start_live_flow_and_generate_crisp_l_report(
        log_path=log_path,
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
        c_startswith=c_startswith,
        keywords=keywords,
        max_flows=max_flows,
        include_stage_path=include_stage_path,
        exclude_last_stage=exclude_last_stage,
        title=title,
        output_dir=output_dir,
        report_filename=report_filename,
        json_filename=json_filename,
    )
