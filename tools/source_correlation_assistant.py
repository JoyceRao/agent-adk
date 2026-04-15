import os
from collections import Counter
from typing import Any, Optional

from .shared import (
    _abs_path,
    _apply_filters,
    _build_filter_result,
    _build_pattern_map,
    _confidence_label,
    _default_pattern_map,
    _evidence_text,
    _extract_source_locations,
    _index_source_files,
    _mask_sensitive_text,
    _normalize_c_startswith,
    _pct,
    _pick_evidence_by_keyword_parsed,
    _parse_log_file,
    _read_code_snippet,
    _resolve_log_path,
    _safe_ratio,
    _severity_label,
    _ts_ms_to_text,
    _wilson_interval,
)

def analyze_log_with_source(
    log_path: str,
    source_root: str = "source/GZCheSuPaiApp",
    rule_path: str = "source/log_rule.md",
    start_ts_ms: Optional[int] = None,
    end_ts_ms: Optional[int] = None,
    log_type: Optional[int] = None,
    level: Optional[str] = None,
    keywords: Optional[str] = None,
    c_startswith: Optional[str] = None,
    keyword_match: str = "OR",
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
        c_startswith=c_startswith,
        keyword_match=keyword_match,
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
        c_startswith=c_startswith,
        max_output_lines=max_output_lines,
        keyword_match=keyword_match,
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
    c_startswith: Optional[str] = None,
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
        c_startswith=c_startswith,
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
        c_startswith=c_startswith,
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
    c_startswith: Optional[str] = None,
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
        c_startswith=c_startswith,
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
            "log_path": _resolve_log_path(log_path),
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
            "c_startswith": _normalize_c_startswith(c_startswith) or None,
        },
        "pattern_definitions": pattern_map,
        "timeline": clipped_rows,
        "peak_buckets": peak_buckets,
        "dropped_buckets": dropped_buckets,
    }
