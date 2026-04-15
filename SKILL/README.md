# Skills 使用规范（ADK + Skill / ADK + Tools）

本文件聚焦 `SKILL/` 目录下的能力编排规范，和根目录 [README.md](../README.md) 配套使用：

1. 根 `README.md` 负责项目总览与端到端用法。
2. 本文档负责 Skill 路由、输入契约、编排顺序与技能层示例。

---

## 1. Skill 映射关系

| Skill 名称 | 作用 | 对齐 Agent | 主要工具 |
|---|---|---|---|
| `log-filter-assistant` | 日志筛选与预览输出 | `filter_agent` | `filter_logs` |
| `source-correlation-assistant` | 日志 + 源码关联分析 | `analysis_agent` | `scan_patterns_full` / `build_timeline` / `analyze_log_with_source` |
| `crisp-l-report-assistant` | CRISP-L 报告渲染与落盘 | `report_agent` | `analyze_and_generate_report` / `generate_markdown_report` |
| `start-live-flow-assistant` | 开播链路 flow 聚合 + 源码关联 + CRISP-L 报告 | `report_agent` | `analyze_start_live_flow_with_source` / `analyze_start_live_flow_and_generate_crisp_l_report` |
| `log-orchestrator-assistant` | 多技能编排与流程守卫 | `root_agent` | `route_by_skill`（编排链路） |

---

## 2. 统一输入契约（Skill / Tools 共用）

建议所有 Skill 与 Tools 共用同一组输入字段，避免串联时重复适配。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `log_path` | `str` | 是 | 待分析日志路径 |
| `source_root` | `str` | 否 | 源码根目录，默认 `source/GZCheSuPaiApp` |
| `rule_path` | `str` | 否 | 日志规则文档，默认 `source/log_rule.md` |
| `start_ts_ms` | `int` | 否 | 起始时间戳（毫秒） |
| `end_ts_ms` | `int` | 否 | 结束时间戳（毫秒） |
| `log_type` | `int` | 否 | 外层日志类型（如 `1` / `99`） |
| `level` | `str` | 否 | `INFO/WARN/ERROR/DEBUG` |
| `keywords` | `str` | 否 | 关键词，逗号分隔（OR 匹配） |
| `c_startswith` | `str` | 否 | `c` 字段前缀匹配，传 `1` 等价匹配 `"-:1"` |
| `pattern_keywords` | `str` | 否 | 额外模式关键词（全量模式统计/时间线） |
| `bucket_ms` | `int` | 否 | 时间线聚合桶大小（毫秒） |
| `max_output_lines` | `int` | 否 | 预览窗口条数 |
| `max_output_buckets` | `int` | 否 | 时间线最大返回桶数量 |
| `title` | `str` | 否 | 报告标题 |
| `output_dir` | `str` | 否 | 报告输出目录，默认 `output` |

---

## 3. 编排规范

### 3.1 推荐链路

`log-filter-assistant` -> `source-correlation-assistant` -> `crisp-l-report-assistant`

### 3.2 直接出报告链路

当用户明确要求“直接出报告”时，推荐使用：

`log-orchestrator-assistant` -> `analyze_and_generate_report`

### 3.3 统计口径规范（必须遵守）

1. 统计结论（模式计数、占比）必须基于筛选后的全量样本。
2. 证据预览允许抽样展示。
3. `max_output_lines` 只影响预览，不影响统计结论。

### 3.4 扩展同步规则（新增）

后续拓展 Skill 或 Tool，必须同步更新 ADK 工程，避免“能力实现了但路由/导出没接入”：

1. 新 Skill：同步 `SKILL/*/SKILL.md`、`tools/skill_router.py`、`prompt.py`。
2. 新 Tool：同步 `tools/__init__.py` 与根目录 `tools.py` 的 `__all__` 导出。
3. 需要对话可调度时：同步 `agent.py` 的 tools/sub_agents 暴露。
4. 提交前执行 `scripts/preflight_check.sh`，通过“扩展同步检查”后再交付。

---

## 4. ADK + Skill 使用示例

在 `.venv/bin/adk run .` 或 `.venv/bin/adk web .` 对话里，可直接这样输入。

### 4.1 分步骤排障

```text
请使用 $log-filter-assistant 在 1775530200000~1775532000000 时间窗筛选 f=1 且包含 [RN_NET] 的日志，输出筛选统计表。
```

```text
请使用 $source-correlation-assistant 对同一日志做全量模式统计和时间线分析，再给出根因假设与源码关联证据。
```

```text
请使用 $crisp-l-report-assistant 输出 CRISP-L 报告并写入 output 目录。
```

### 4.2 一次性编排

```text
请使用 $log-orchestrator-assistant 执行完整流程：筛选 -> 分析 -> 报告。
日志：source/resource/20_1E14C9C4-3F59-4C44-8D44-A2D86BBFE5AB_1775491200000_d72a5f01-bc0d-4b85-b2d8-8fee76d1e5ed.log
筛选：log_type=1, keywords="[RN_NET],reactnative_exception"
输出目录：output
```

---

## 5. ADK + Tools 使用示例

### 5.1 在 ADK 对话里显式路由（强约束）

```text
请先调用 list_skills，然后调用 route_by_skill，参数：
skill_name="source-correlation-assistant",
log_path="source/resource/20_1E14C9C4-3F59-4C44-8D44-A2D86BBFE5AB_1775491200000_d72a5f01-bc0d-4b85-b2d8-8fee76d1e5ed.log",
log_type=1,
keywords="[RN_NET],reactnative_exception",
max_output_lines=200
```

### 5.2 在 Python 中脚本化调用

```bash
python3 - <<'PY'
from tools import scan_patterns_full, build_timeline, analyze_and_generate_report

log_path = "source/resource/20_1E14C9C4-3F59-4C44-8D44-A2D86BBFE5AB_1775491200000_d72a5f01-bc0d-4b85-b2d8-8fee76d1e5ed.log"

stats = scan_patterns_full(
    log_path=log_path,
    log_type=1,
    keywords="[RN_NET],reactnative_exception",
)
print("top_patterns:", [(x["pattern_name"], x["count"]) for x in stats["top_patterns"][:3]])

timeline = build_timeline(
    log_path=log_path,
    log_type=1,
    bucket_ms=60000,
    max_output_buckets=20,
)
print("peak:", [(x["bucket_start_text"], x["event_count"]) for x in timeline["peak_buckets"][:3]])

report = analyze_and_generate_report(
    log_path=log_path,
    source_root="source/GZCheSuPaiApp",
    rule_path="source/log_rule.md",
    log_type=1,
    title="技能层脚本化报告",
    output_dir="output",
)
print(report[:600])
PY
```

---

## 6. 常见落地建议

1. 在线排障优先 `ADK + Skill`，先拿结论方向。
2. 稳定复盘优先 `ADK + Tools`，沉淀可复现脚本。
3. 报警处置建议固定模板：`filter -> scan_patterns_full -> build_timeline -> analyze_and_generate_report`。
4. 团队协作时，统一使用本文件的输入契约字段，避免参数命名漂移。
