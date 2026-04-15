from pathlib import Path
from typing import Any, Optional

from .crisp_l_report_assistant import analyze_and_generate_report, generate_markdown_report
from .log_filter_assistant import filter_logs
from .source_correlation_assistant import analyze_log_with_source
from .start_live_flow_assistant import (
    analyze_start_live_flow_and_generate_crisp_l_report,
    analyze_start_live_flow_with_source,
)
from .shared import _abs_path

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
    "start-live-flow-assistant": {
        "delegated_agent": "report_agent",
        "tool": "analyze_start_live_flow_and_generate_crisp_l_report",
        "aliases": ["start-live", "startlive", "live-flow", "start-live-flow"],
        "description": "开播链路 flow 聚合 + 源码关联，输出 CRISP-L 报告。",
    },
}

SKILL_ALIAS_TO_CANONICAL: dict[str, str] = {}
for _canonical_name, _meta in SKILL_DEFINITIONS.items():
    SKILL_ALIAS_TO_CANONICAL[_canonical_name.lower()] = _canonical_name
    for _alias in _meta.get("aliases", []):
        SKILL_ALIAS_TO_CANONICAL[str(_alias).strip().lower()] = _canonical_name

def _normalize_skill_name(skill_name: str) -> str:
    raw = (skill_name or "").strip().lower()
    if raw.startswith("$"):
        raw = raw[1:]
    raw = raw.replace("_", "-")
    return SKILL_ALIAS_TO_CANONICAL.get(raw, raw)

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
            "或别名（如 filter/report/orchestrator/start-live）。"
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
    c_startswith: Optional[str] = None,
    max_output_lines: int = 1000,
    max_flows: int = 1000,
    include_stage_path: bool = True,
    exclude_last_stage: str = "recover_check_start",
    generate_start_live_report: bool = True,
    start_live_report_filename: str = "start_live_flow_report.md",
    start_live_json_filename: str = "start_live_flow_report.json",
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
            c_startswith=c_startswith,
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
            c_startswith=c_startswith,
            max_output_lines=max_output_lines,
        )
        return {
            "skill_name": skill_name,
            "normalized_skill_name": normalized_skill,
            "delegated_agent": meta["delegated_agent"],
            "tool": meta["tool"],
            "result": result,
        }

    if normalized_skill == "start-live-flow-assistant":
        missing = _require_log_path()
        if missing:
            return missing
        effective_title = (
            title.strip()
            if title and title.strip() and title.strip() != "日志分析报告"
            else "startLive 开播链路日志报告"
        )

        start_live_result: dict[str, Any]
        response = {
            "skill_name": skill_name,
            "normalized_skill_name": normalized_skill,
            "delegated_agent": meta["delegated_agent"],
            "tool": meta["tool"],
            "result": {},
        }
        if generate_start_live_report:
            generated = analyze_start_live_flow_and_generate_crisp_l_report(
                log_path=log_path or "",
                source_root=source_root,
                rule_path=rule_path,
                start_ts_ms=start_ts_ms,
                end_ts_ms=end_ts_ms,
                c_startswith=c_startswith or "1",
                keywords=keywords or "CSP_BIZ_WATCHCAR_STARTLIVE,flowId",
                max_flows=max_flows,
                include_stage_path=include_stage_path,
                exclude_last_stage=exclude_last_stage,
                max_output_lines=max_output_lines,
                title=effective_title,
                output_dir=output_dir,
                report_filename=start_live_report_filename,
                json_filename=start_live_json_filename,
            )
            start_live_result = generated.get("analysis", {}) or {}
            response.update(
                {
                    "result": start_live_result,
                    "report_path": generated.get("report_path", ""),
                    "json_path": generated.get("json_path", ""),
                    "report_preview": (generated.get("report_markdown", "") or "").splitlines()[:20],
                    "report_markdown": generated.get("report_markdown", ""),
                }
            )
        else:
            start_live_result = analyze_start_live_flow_with_source(
                log_path=log_path or "",
                source_root=source_root,
                rule_path=rule_path,
                start_ts_ms=start_ts_ms,
                end_ts_ms=end_ts_ms,
                c_startswith=c_startswith or "1",
                keywords=keywords or "CSP_BIZ_WATCHCAR_STARTLIVE,flowId",
                max_flows=max_flows,
                include_stage_path=include_stage_path,
                exclude_last_stage=exclude_last_stage,
                max_output_lines=max_output_lines,
            )
            response["result"] = start_live_result
        return response

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
            c_startswith=c_startswith,
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
        c_startswith=c_startswith,
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
        c_startswith=c_startswith,
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
