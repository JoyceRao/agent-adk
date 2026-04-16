from pathlib import Path
from typing import Any, Optional

from .shared import _abs_path, _mask_sensitive_text
from .source_correlation_assistant import analyze_log_with_source

def generate_markdown_report(analysis: dict[str, Any], title: str = "日志分析报告") -> str:
    """将结构化分析结果转换为 CRISP-L Markdown 报告。"""
    meta = analysis.get("meta", {})
    report_profile = str(analysis.get("report_profile", "")).strip().lower()
    is_start_live_compact = report_profile in {"start_live_compact", "start-live-compact"}
    fs = analysis.get("filter_summary", {})
    pattern_counts = analysis.get("pattern_counts", {})
    source_hits = analysis.get("source_correlations", [])
    preview = analysis.get("evidence_preview", [])
    crisp_l = analysis.get("crisp_l", {})
    legacy_crisp_l = analysis.get("legacy_crisp_l", {})

    quick_summary = crisp_l.get("quick_summary", {})
    conclusion = crisp_l.get("conclusion", {})
    reproduction = crisp_l.get("reproduction", {}) or legacy_crisp_l.get("reproduction", {})
    indicators = crisp_l.get("indicators", {})
    plan = crisp_l.get("plan", {}) or legacy_crisp_l.get("plan", {})
    loop_closure = crisp_l.get("loop_closure", {}) or legacy_crisp_l.get("loop_closure", {})
    abnormal_flows_table = analysis.get("abnormal_flows_table", []) or []

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
    summary_text = _mask_sensitive_text(
        str(quick_summary.get("summary", "") or conclusion.get("summary", "未生成摘要"))
    )
    top_action = (
        _mask_sensitive_text(str(quick_summary.get("top_action", "")))
        if str(quick_summary.get("top_action", "")).strip()
        else (
        _mask_sensitive_text(str(plan_actions[0].get("action", "")))
        if plan_actions
        else "扩大筛选窗口后复查并补充证据。"
        )
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

    if not is_start_live_compact:
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

    if not is_start_live_compact:
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

    if not is_start_live_compact:
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
    else:
        lines.append("## 其他（未收敛/失败 flow 明细）")
        lines.append("")
        status_groups = [
            ("in_progress", "未收敛（in_progress）"),
            ("failure_end", "失败（failure_end）"),
        ]
        rendered_any_group = False
        for group_status, group_title in status_groups:
            group_rows = [
                row
                for row in abnormal_flows_table
                if str(row.get("status", "")).strip() == group_status
            ]
            group_rows.sort(
                key=lambda row: (
                    int(row.get("first_ts_ms", 0) or 0),
                    str(row.get("flowId", "")),
                ),
                reverse=True,
            )

            lines.append(f"### {group_title}")
            lines.append("")
            lines.append("| flowId | 开始时间（l） | 最后流程段 | 最后 stage | reserveId | roomId | dealer_id | opl_user_id |")
            lines.append("|---|---|---|---|---|---|---|---|")
            if group_rows:
                rendered_any_group = True
                for row in group_rows:
                    lines.append(
                        f"| {_mask_sensitive_text(str(row.get('flowId', '')))} | "
                        f"{_mask_sensitive_text(str(row.get('first_ts_text', '') or row.get('first_ts_ms', '')))} | "
                        f"{_mask_sensitive_text(str(row.get('last_process', '')))} | "
                        f"{_mask_sensitive_text(str(row.get('last_stage', '')))} | "
                        f"{_mask_sensitive_text(str(row.get('reserveId', '')))} | "
                        f"{_mask_sensitive_text(str(row.get('roomId', '')))} | "
                        f"{_mask_sensitive_text(str(row.get('dealer_id', '')))} | "
                        f"{_mask_sensitive_text(str(row.get('opl_user_id', '')))} |"
                    )
            else:
                lines.append("| - | - | - | - | - | - | - | - |")
            lines.append("")

        if not rendered_any_group:
            lines.append("未命中未收敛或失败 flow。")
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
    c_startswith: Optional[str] = None,
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
        c_startswith=c_startswith,
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
