# 日志分析助手（Google ADK Agent）

一个面向移动端日志排障的 Agent 项目，支持两种主路径：

1. `ADK + Skill`：通过 ADK 会话使用 `$skill` 做自然语言编排分析。
2. `ADK + Tools`：通过 ADK 点名工具，或在 Python 中直接调用工具函数。

本 README 重点提供“如何使用 Skill / tools”的统一规范。

## 1. 先选使用方式（ADK + Skill vs ADK + Tools）

| 方式 | 适合场景 | 优点 | 建议 |
|---|---|---|---|
| ADK + Skill | 在线排障、边问边查、需要解释结论 | 交互强、上手快 | 先用它收敛问题范围 |
| ADK + Tools | 批量日志、固定报表、自动任务 | 可控、可复现、便于集成 | 用于沉淀标准流程 |

推荐组合：先 Skill 快速定位，再用 tools 固化为可重复脚本。

---

## 2. 核心能力

| 能力 | 说明 |
|---|---|
| 日志筛选 | 按 `start_ts_ms/end_ts_ms/log_type/level/keywords/c_startswith` 缩减日志量 |
| 全量模式统计 | 对筛选后的全量样本统计异常模式，不受预览窗口截断影响 |
| 时间线分析 | 按时间桶聚合，输出波峰窗口与模式命中分布 |
| 源码关联 | 自动从日志提取 `文件:行号` 并回填源码片段 |
| 报告生成 | 生成 CRISP-L 结构化 Markdown 报告并落盘 |
| 安全脱敏 | 默认脱敏 token/cookie/jwt/uuid 等敏感信息 |

---

## 3. 环境准备

### 3.1 Python 环境

```bash
python -m venv .venv
source .venv/bin/activate
```

### 3.2 安装依赖

如果只调用 `tools.py`：标准库即可。  
如果运行 ADK Agent：

```bash
python -m pip install --upgrade pip
python -m pip install google-adk litellm python-dotenv
```

### 3.3 模型环境变量（运行 Agent 时）

```bash
export OPENAI_MODEL=gpt-5.3-codex
export OPENAI_API_KEY=<YOUR_API_KEY>
# export OPENAI_BASE_URL=<YOUR_OPENAI_COMPATIBLE_BASE_URL>
```

### 3.4 启动前自检（强烈建议）

```bash
scripts/preflight_check.sh \
  --log-path source/resource/20_61B17947-82FD-4113-9A8B-02EB0080E449_1775318400000_df646c18-0ca9-49ae-9f95-3fc9d3ae4c83.log \
  --route-smoke \
  --max-output-lines 50
```

---

## 4. 统一输入规范（Skill / tools 共用）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `log_path` | `str` | 是 | 待分析日志路径 |
| `source_root` | `str` | 否 | 源码目录，默认 `source/GZCheSuPaiApp` |
| `rule_path` | `str` | 否 | 规则文档，默认 `source/log_rule.md` |
| `start_ts_ms` | `int` | 否 | 起始时间戳（毫秒） |
| `end_ts_ms` | `int` | 否 | 结束时间戳（毫秒） |
| `log_type` | `int` | 否 | 日志类型（如 `1` / `99`） |
| `level` | `str` | 否 | `INFO/WARN/ERROR/DEBUG` |
| `keywords` | `str` | 否 | 关键词，逗号分隔（OR 匹配） |
| `c_startswith` | `str` | 否 | `c` 字段前缀匹配，传 `1` 等价匹配 `"-:1"` |
| `pattern_keywords` | `str` | 否 | 额外模式关键词（全量模式统计/时间线） |
| `max_output_lines` | `int` | 否 | 预览窗口条数 |
| `bucket_ms` | `int` | 否 | 时间线桶大小（毫秒） |
| `max_output_buckets` | `int` | 否 | 时间线最大返回桶数 |
| `title` | `str` | 否 | 报告标题 |
| `output_dir` | `str` | 否 | 报告输出目录，默认 `output` |

---

## 5. 使用口径规范（重要）

### 5.1 统计口径

1. 模式统计与异常占比：基于“筛选后的全量样本”。
2. 证据预览：默认基于窗口抽样（`max_output_lines`）。
3. 因此 `max_output_lines` 只影响预览，不影响统计结论。

### 5.2 推荐分析顺序

1. `filter_logs`：确认筛选命中规模。
2. `scan_patterns_full`：拿全量模式计数和证据。  
3. `build_timeline`：看波峰时间窗。
4. `analyze_log_with_source`：做根因研判 + 源码关联。
5. `analyze_and_generate_report`：产出最终报告。

### 5.3 安全规范

1. 输出必须脱敏 token/cookie/jwt/uuid。
2. 结论必须给证据（行号/时间戳/关键词/源码定位）。
3. 数据不足时必须标注“条件化结论”。

---

## 6. ADK + Skill 使用规范

### 6.1 启动 Agent

```bash
adk run .
# 或
adk web .
```

### 6.2 使用规范

1. 优先以 `$skill` 明确意图，减少路由歧义。
2. 输入尽量包含 `log_path`、时间窗、`log_type`、关键词。
3. 复杂排障建议按“筛选 -> 全量统计 -> 时间线 -> 联合分析 -> 报告”推进。
4. 结论必须附证据点（日志行号/时间戳/关键词/源码定位）。

### 6.3 自然语言调用示例（ADK 会话）

```text
请使用 $log-analysis-assistant 分析 source/resource/xxx.log，先做全量模式统计，再给时间线和根因假设。
```

```text
请使用 $log-filter-assistant 在 1775530200000~1775532000000 时间窗筛选 f=1 且包含 [RN_NET] 的日志。
```

```text
请使用 $source-correlation-assistant 分析 source/resource/xxx.log，给出源码关联证据。
```

```text
请使用 $crisp-l-report-assistant 直接输出 CRISP-L 报告并写入 output。
```

```text
请使用 $start-live-flow-assistant 分析 source/resource/xxx.log，
按 flowId 输出最后流程和 stage，结合源码证据生成 CRISP-L 开播链路报告（默认写入 output/[log文件名].md）。
```

### 6.4 运行时路由 Skill 名称（`route_by_skill`）

| `skill_name` | 作用 | 路由 |
|---|---|---|
| `log-filter-assistant` | 筛选与预览 | `filter_agent -> filter_logs` |
| `source-correlation-assistant` | 联合分析 | `analysis_agent -> analyze_log_with_source` |
| `crisp-l-report-assistant` | 一键报告 | `report_agent -> analyze_and_generate_report` |
| `start-live-flow-assistant` | 开播链路 flow 分组 + 源码关联 + CRISP-L 报告 | `report_agent -> analyze_start_live_flow_and_generate_crisp_l_report` |
| `log-orchestrator-assistant` | 串行编排 | `root_agent -> filter -> analysis -> report` |

---

## 7. ADK + Tools 使用规范

### 7.1 使用规范

1. 需要强确定性时，优先显式调用工具或 `route_by_skill`。
2. 需要高复现时，保留固定参数模板并在脚本中调用。
3. 先做 `filter_logs/scan_patterns_full`，再做 `analyze_log_with_source`，最后出报告。
4. 注意统计口径：`max_output_lines` 仅影响预览，不影响全量统计结论。

### 7.2 在 ADK 会话中直接点名工具（推荐）

你可以在 `adk run .` 或 `adk web .` 的对话中直接输入以下指令：

```text
请先调用 list_skills，然后调用 route_by_skill，参数：
skill_name="log-filter-assistant",
log_path="source/resource/20_1E14C9C4-3F59-4C44-8D44-A2D86BBFE5AB_1775491200000_d72a5f01-bc0d-4b85-b2d8-8fee76d1e5ed.log",
start_ts_ms=1775530200000,
end_ts_ms=1775532000000,
log_type=1,
keywords="[RN_NET],reactnative_exception",
max_output_lines=200
```

```text
调用 analyze_and_generate_report(
  log_path="source/resource/20_1E14C9C4-3F59-4C44-8D44-A2D86BBFE5AB_1775491200000_d72a5f01-bc0d-4b85-b2d8-8fee76d1e5ed.log",
  source_root="source/GZCheSuPaiApp",
  rule_path="source/log_rule.md",
  log_type=1,
  title="ADK工具直调报告",
  output_dir="output"
)
```

```text
调用 route_by_skill(
  skill_name="start-live-flow-assistant",
  log_path="source/resource/20_61B17947-82FD-4113-9A8B-02EB0080E449_1775318400000_df646c18-0ca9-49ae-9f95-3fc9d3ae4c83.log",
  start_ts_ms=1775530200000,
  end_ts_ms=1775532000000,
  c_startswith="1",
  keywords="CSP_BIZ_WATCHCAR_STARTLIVE,flowId",
  max_flows=500,
  include_stage_path=true,
  exclude_last_stage="recover_check_start",
  generate_start_live_report=true,
  output_dir="output",
  title="startLive 开播链路日志报告"
)
```

说明：不传 `start_live_report_filename/start_live_json_filename` 时，
默认输出为 `output/[log文件名].md` 与 `output/[log文件名].json`。

### 7.3 在 Python 中直接调用 tools（脚本化）

#### 7.3.1 一键报告（最常用）

```bash
python3 - <<'PY'
from tools import analyze_and_generate_report

report = analyze_and_generate_report(
    log_path="source/resource/20_1E14C9C4-3F59-4C44-8D44-A2D86BBFE5AB_1775491200000_d72a5f01-bc0d-4b85-b2d8-8fee76d1e5ed.log",
    log_type=1,
    source_root="source/GZCheSuPaiApp",
    rule_path="source/log_rule.md",
    title="日志分析报告",
    output_dir="output",
)
print(report[:800])
PY
```

#### 7.3.2 先筛选后分析（缩减日志量）

```bash
python3 - <<'PY'
from tools import filter_logs, analyze_log_with_source

log_path = "source/resource/20_1E14C9C4-3F59-4C44-8D44-A2D86BBFE5AB_1775491200000_d72a5f01-bc0d-4b85-b2d8-8fee76d1e5ed.log"

filtered = filter_logs(
    log_path=log_path,
    start_ts_ms=1775530200000,
    end_ts_ms=1775532000000,
    log_type=1,
    level="INFO",
    keywords="[RN_NET],reactnative_exception",
    max_output_lines=100,
)
print("matched:", filtered["matched_entries"], "returned:", filtered["returned_entries"])

analysis = analyze_log_with_source(
    log_path=log_path,
    start_ts_ms=1775530200000,
    end_ts_ms=1775532000000,
    log_type=1,
    keywords="[RN_NET],reactnative_exception",
    max_output_lines=100,
)
print("pattern_counts:", analysis["pattern_counts"])
print("count_basis:", analysis["filter_summary"].get("pattern_count_basis"))
PY
```

#### 7.3.3 全量模式统计（推荐先做）

```bash
python3 - <<'PY'
from tools import scan_patterns_full

res = scan_patterns_full(
    log_path="source/resource/20_1E14C9C4-3F59-4C44-8D44-A2D86BBFE5AB_1775491200000_d72a5f01-bc0d-4b85-b2d8-8fee76d1e5ed.log",
    log_type=1,
    keywords="[RN_NET],reactnative_exception",
)
print("count_basis:", res["meta"]["count_basis"])
print("top_patterns:", [(x["pattern_name"], x["count"]) for x in res["top_patterns"][:5]])
PY
```

#### 7.3.4 时间线分析（看波峰）

```bash
python3 - <<'PY'
from tools import build_timeline

res = build_timeline(
    log_path="source/resource/20_61B17947-82FD-4113-9A8B-02EB0080E449_1775318400000_df646c18-0ca9-49ae-9f95-3fc9d3ae4c83.log",
    log_type=1,
    bucket_ms=60000,
    max_output_buckets=30,
    keywords="[RN_NET],reactnative_exception",
)
print("matched_entries:", res["filter_summary"]["matched_entries"])
print("peak:", [(x["bucket_start_text"], x["event_count"]) for x in res["peak_buckets"][:3]])
PY
```

#### 7.3.5 确定性 Skill 路由（脚本场景）

```bash
python3 - <<'PY'
from tools import route_by_skill

res = route_by_skill(
    skill_name="crisp-l-report-assistant",
    log_path="source/resource/20_1E14C9C4-3F59-4C44-8D44-A2D86BBFE5AB_1775491200000_d72a5f01-bc0d-4b85-b2d8-8fee76d1e5ed.log",
    source_root="source/GZCheSuPaiApp",
    rule_path="source/log_rule.md",
    log_type=1,
    max_output_lines=300,
    title="路由调用报告",
    output_dir="output",
)
print("skill:", res.get("normalized_skill_name"))
print("report_path:", res.get("report_path"))
PY
```

#### 7.3.6 start_live_flow_assistant（脚本场景）

```bash
python3 - <<'PY'
from tools import route_by_skill

res = route_by_skill(
    skill_name="start-live-flow-assistant",
    log_path="source/resource/20_61B17947-82FD-4113-9A8B-02EB0080E449_1775318400000_df646c18-0ca9-49ae-9f95-3fc9d3ae4c83.log",
    c_startswith="1",
    keywords="CSP_BIZ_WATCHCAR_STARTLIVE,flowId",
    max_flows=300,
    include_stage_path=True,
    generate_start_live_report=True,
    output_dir="output",
    title="startLive 开播链路日志报告",
)
print("skill:", res.get("normalized_skill_name"))
print("report_path:", res.get("report_path"))
print("json_path:", res.get("json_path"))
PY
```

默认会写入：
- `output/[log文件名].md`
- `output/[log文件名].json`

如需自定义文件名，可额外传：
- `start_live_report_filename="custom_name.md"`
- `start_live_json_filename="custom_name.json"`



## 8. 主要工具清单

| 函数 | 作用 | 关键参数 |
|---|---|---|
| `filter_logs` | 条件筛选 + 预览 | `start_ts_ms/end_ts_ms/log_type/level/keywords/c_startswith/max_output_lines` |
| `scan_patterns_full` | 全量样本模式统计 + 证据提取 | `pattern_keywords/include_default_patterns/evidence_per_pattern` |
| `build_timeline` | 时间线桶聚合 + 波峰提取 | `bucket_ms/max_output_buckets/pattern_keywords` |
| `analyze_log_with_source` | 异常研判 + 源码关联 + 指标 | `source_root/rule_path/max_source_matches` |
| `generate_markdown_report` | 渲染 CRISP-L Markdown | `analysis/title` |
| `analyze_and_generate_report` | 一键分析并落盘 | `log_path/output_dir/title` |
| `analyze_start_live_flow` | 开播链路按 flowId 分组分析 | `log_path/max_flows/include_stage_path/exclude_last_stage` |
| `analyze_start_live_flow_with_source` | 开播链路 flow + 源码关联融合分析 | `log_path/source_root/rule_path/max_flows` |
| `generate_start_live_flow_markdown` | 兼容旧接口，统一渲染 CRISP-L 开播链路报告 | `analysis/title` |
| `analyze_start_live_flow_and_generate_crisp_l_report` | 开播链路一键 CRISP-L 报告落盘 | `log_path/output_dir/(可选)report_filename/json_filename` |
| `analyze_start_live_flow_and_generate_report` | 旧函数名兼容入口（同上） | `log_path/output_dir/(可选)report_filename/json_filename` |
| `list_skills` | 列出可路由 skill | 无 |
| `route_by_skill` | 按 skill_name 确定性路由 | `skill_name/log_path/...` |

---

## 9. 常见问题

### Q1：我已经筛选了，为什么报告里还说样本很多？
筛选统计是全量口径；预览只是窗口抽样。请看 `matched_entries` 与 `returned_entries` 的区别。

### Q2：如何确保不是抽样误判？
先调用 `scan_patterns_full`，确认全量模式计数，再看 `analyze_log_with_source` 的结论。

### Q3：如何快速缩减日志量？
优先同时设置：`start_ts_ms + end_ts_ms + log_type + keywords`，必要时再加 `level`。

---

## 10. 目录结构

```text
my_agent_project/
├── agent.py
├── prompt.py
├── tools.py
├── scripts/
│   ├── preflight_check.py
│   ├── preflight_check.sh
│   ├── run_report.py
│   └── run_report.sh
├── source/
│   ├── log_rule.md
│   ├── resource/*.log
│   └── GZCheSuPaiApp/
├── SKILL/
│   ├── README.md
│   └── log-analysis-assistant/
│       ├── SKILL.md
│       ├── agents/openai.yaml
│       └── references/log-analysis-playbook.md
└── output/
```
