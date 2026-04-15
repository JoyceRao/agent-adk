# AI日志分析

## 一、背景

**目标**：依托日志与源码，打通“日志采集 → AI智能分析 → 修复建议输出 → 自动化落地”全流程，实现日志排障的高效化、标准化，降低人工成本。

**现状**：目前日志分析、问题定位及修复工作，高度依赖人工主动进行日志回捞、手动分析排查、手动推进修复，效率低下且易出现人为误判，缺乏标准化流程支撑。

## 二、分析方案

采用 Google ADK 框架，结合框架特性实现两种核心分析模式——ADK + Tools 模式与 ADK + Extensions（Skills）模式，适配不同日志分析场景，兼顾交互灵活性与执行确定性。


### 1. 先选使用方式（ADK + Skill vs ADK + Tools）

两种模式适配不同场景，建议结合使用以实现高效排障，具体对比及建议如下：

|方式|适合场景|优点|建议|
|---|---|---|---|
|ADK + Skill|在线实时排障、边问边查、需要详细解释分析结论|自然语言交互性强、上手门槛低、能快速收敛问题范围|优先使用，快速定位问题核心，减少无效操作|
|ADK + Tools|批量日志分析、固定报表生成、自动化任务执行|执行结果可控、可复现性强、便于集成到自动化脚本|用于沉淀标准分析流程，实现重复场景的高效落地|

**推荐组合策略**：先通过 ADK + Skill 模式快速定位问题范围、研判根因方向，再用 ADK + Tools 模式将成熟的分析流程固化为可重复执行的脚本，兼顾效率与标准化。

---

### 2. 核心能力

日志分析助手具备全流程日志处理与分析能力，覆盖从日志筛选到报告生成的全环节，核心能力如下：

|能力|说明|
|---|---|
|日志筛选|支持按 start_ts_ms（起始时间戳）、end_ts_ms（结束时间戳）、log_type（日志类型）、level（日志级别）、keywords（关键词）、c_startswith（c 字段前缀）等条件筛选，大幅缩减日志分析量，聚焦核心日志。|
|全量模式统计|对筛选后的全量日志样本进行异常模式统计，不受预览窗口条数限制，确保统计结论的准确性，避免抽样误判。|
|时间线分析|按指定时间桶（bucket_ms）聚合日志数据，自动识别日志波峰窗口及模式命中分布，助力定位异常集中时段。|
|源码关联|自动从日志中提取“文件:行号”信息，并关联对应源码目录，回填源码片段，辅助根因定位。|
|报告生成|生成符合 CRISP-L 规范的结构化 Markdown 报告，并自动落盘至指定目录，便于归档、分享与复盘。|
|安全脱敏|默认对日志中的敏感信息（token、cookie、jwt、uuid 等）进行脱敏处理，保障数据安全，避免信息泄露。|

---

### 3. 统一输入规范（Skill / tools 共用）

为确保分析结果准确、避免路由歧义，ADK + Skill 与 ADK + Tools 模式共用统一输入规范，所有输入字段及要求如下（必填字段需严格填写，可选字段根据场景补充）：

|字段|类型|必填|说明|
|---|---|---|---|
|log_path|str|是|待分析日志文件的绝对路径或相对路径，必填项，若路径错误会导致分析失败。|
|source_root|str|否|源码目录路径，默认值为 source/GZCheSuPaiApp，用于源码关联功能。|
|rule_path|str|否|日志分析规则文档路径，默认值为 source/log_rule.md，用于辅助异常模式识别。|
|start_ts_ms|int|否|起始时间戳（单位：毫秒），用于筛选指定时间段内的日志，不填则不限制起始时间。|
|end_ts_ms|int|否|结束时间戳（单位：毫秒），用于筛选指定时间段内的日志，不填则不限制结束时间。|
|log_type|int|否|日志类型（如 1、99 等），用于筛选特定类型的日志，不填则匹配所有类型。|
|level|str|否|日志级别，可选值为 INFO、WARN、ERROR、DEBUG，不填则匹配所有级别。|
|keywords|str|否|筛选关键词，多个关键词用逗号分隔，采用 OR 匹配规则（满足任一关键词即命中）。|
|c_startswith|str|否|c 字段前缀匹配，传 1 时等价匹配 "-:1"。|
|pattern_keywords|str|否|额外模式关键词，仅用于全量模式统计和时间线分析，辅助识别特定异常模式。|
|max_output_lines|int|否|日志预览窗口条数，仅影响预览结果，不影响全量统计结论，默认值可根据场景调整。|
|bucket_ms|int|否|时间线分析的时间桶大小（单位：毫秒），用于日志聚合，不填则使用默认桶大小。|
|max_output_buckets|int|否|时间线分析的最大返回桶数，不填则使用默认值，用于控制时间线输出长度。|
|title|str|否|分析报告的标题，不填则使用默认标题（如“日志分析报告”）。|
|output_dir|str|否|报告输出目录，默认值为 output，若目录不存在会自动创建。|

---

### 4. 使用口径规范（重要）

为确保分析结果准确、可追溯、符合安全要求，所有使用者必须严格遵循以下口径规范，避免因操作不规范导致分析偏差或安全风险。

#### 4.1 统计口径

1. 模式统计与异常占比：所有统计结论均基于“筛选后的全量日志样本”，确保统计结果的准确性。

2. 证据预览：日志证据预览默认基于窗口抽样（由 max_output_lines 控制），仅用于快速查看日志片段。

3. 关键提醒：max_output_lines 仅影响预览结果的条数，不影响全量统计结论，请勿通过预览条数判断整体异常规模。

#### 4.2 推荐分析顺序

为提升排障效率、避免无效操作，建议按以下顺序执行分析步骤，形成标准化流程：

1. 调用 filter_logs：筛选目标日志，确认筛选命中的日志规模，判断是否需要进一步缩小筛选范围。

2. 调用 scan_patterns_full：对筛选后的全量日志进行模式统计，获取异常模式计数及相关证据，初步定位异常类型。

3. 调用 build_timeline：生成日志时间线，查看异常波峰窗口，定位异常集中时段。

4. 调用 analyze_log_with_source：结合源码关联功能，进行根因研判，获取具体的异常原因及源码定位。

5. 调用 analyze_and_generate_report：整合所有分析结果，生成 CRISP-L 结构化报告并落盘归档。

#### 4.3 安全规范

1. 敏感信息脱敏：所有输出结果（包括预览日志、分析报告）必须对 token、cookie、jwt、uuid 等敏感信息进行脱敏，严禁输出未脱敏的敏感数据。

2. 结论需附证据：所有分析结论必须附带明确证据，包括日志行号、时间戳、关键词、源码定位等，确保结论可追溯、可验证。

3. 条件化标注：当日志数据不足、筛选条件不明确或分析结果存在不确定性时，必须标注“条件化结论”，说明结论的适用前提及潜在偏差。

---

### 5. ADK + Skill 使用规范

ADK + Skill 模式适用于在线实时排障，通过自然语言交互调用 Skill 完成分析，核心规范如下，确保交互高效、路由准确。

#### 5.1 启动 Agent

启动 Agent 后，即可进入自然语言交互模式，支持两种启动方式（二选一）：

```bash
.venv/bin/adk run .
# 或
.venv/bin/adk web .
```

#### 5.2 使用规范

1. 明确意图：优先使用 $skill 前缀指定具体 Skill，减少路由歧义，确保 Agent 准确理解分析需求。

2. 完整输入：输入自然语言指令时，尽量包含 log_path、时间窗（start_ts_ms/end_ts_ms）、log_type、关键词等核心信息，避免因信息不全导致分析失败或偏差。

3. 流程推进：复杂排障场景，建议按照“筛选 → 全量统计 → 时间线 → 联合分析 → 报告”的顺序逐步推进，确保分析逻辑连贯。

4. 证据要求：所有分析结论必须附带证据点（日志行号、时间戳、关键词、源码定位等），不允许输出无证据的结论。

#### 5.3 自然语言调用示例（ADK 会话）

以下为常见场景的自然语言调用示例，可直接参考使用，根据实际需求调整参数：

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

#### 5.4 运行时路由 Skill 名称（route_by_skill）

不同 Skill 对应不同的分析功能及路由路径，具体对应关系如下，便于精准调用：

|skill_name|作用|路由|
|---|---|---|
|log-filter-assistant|日志筛选与预览，快速缩减日志量|filter_agent -> filter_logs|
|source-correlation-assistant|日志与源码联合分析，定位根因|analysis_agent -> analyze_log_with_source|
|crisp-l-report-assistant|一键生成 CRISP-L 结构化报告|report_agent -> analyze_and_generate_report|
|log-orchestrator-assistant|串行编排全分析流程，自动完成筛选到报告的全环节|root_agent -> filter -> analysis -> report|

---

### 6. ADK + Tools 使用规范

ADK + Tools 模式适用于批量分析、自动化脚本场景，通过显式调用工具函数实现分析，核心规范如下，确保执行确定性与可复现性。

#### 6.1 使用规范

1. 确定性优先：当需要强确定性、可复现的分析结果时，优先显式调用工具函数或使用 route_by_skill 进行精准路由。

2. 模板沉淀：对于重复出现的分析场景，保留固定参数模板，并在脚本中调用，提升复用效率。

3. 流程规范：遵循“筛选 → 全量统计 → 根因分析 → 报告生成”的顺序调用工具，避免逻辑混乱。

4. 口径注意：严格区分 max_output_lines 的作用，仅用于日志预览，不影响全量统计结论，避免因预览条数误判分析结果。

#### 6.2 在 ADK 会话中直接点名工具（推荐）

启动 Agent（.venv/bin/adk run . 或 .venv/bin/adk web .）后，可在对话中直接输入指令点名工具，示例如下：

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

#### 6.3 在 Python 中直接调用 tools（脚本化）

对于批量分析、自动化任务，可在 Python 脚本中直接调用工具函数，以下为常用场景的示例代码，可直接复制修改使用。

##### 6.3.1 一键报告（最常用）

直接调用 analyze_and_generate_report 函数，一键完成分析并生成报告落盘：

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

##### 6.3.2 先筛选后分析（缩减日志量）

先调用 filter_logs 筛选目标日志，再调用 analyze_log_with_source 进行根因分析，适用于日志量较大的场景：

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

##### 6.3.3 全量模式统计（推荐先做）

调用 scan_patterns_full 函数，对全量筛选日志进行模式统计，获取异常模式及计数，避免抽样误判：

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

##### 6.3.4 时间线分析（看波峰）

调用 build_timeline 函数，生成日志时间线，定位异常集中的波峰窗口：

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

##### 6.3.5 确定性 Skill 路由（脚本场景）

调用 route_by_skill 函数，按 skill_name 进行精准路由，适用于脚本化场景下的确定性分析：

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

---

### 7. 主要工具清单

以下为日志分析助手的核心工具函数，明确各函数的作用及关键参数，便于快速调用、精准使用：

|函数|作用|关键参数|
|---|---|---|
|filter_logs|按条件筛选日志，并返回预览结果，缩减分析量|start_ts_ms、end_ts_ms、log_type、level、keywords、c_startswith、max_output_lines|
|scan_patterns_full|对筛选后的全量日志进行模式统计，提取异常模式及证据|pattern_keywords、include_default_patterns、evidence_per_pattern|
|build_timeline|按时间桶聚合日志，提取波峰窗口及模式命中分布|bucket_ms、max_output_buckets、pattern_keywords|
|analyze_log_with_source|结合源码进行异常根因研判，关联源码片段及相关指标|source_root、rule_path、max_source_matches|
|generate_markdown_report|将分析结果渲染为 CRISP-L 规范的 Markdown 报告|analysis、title|
|analyze_and_generate_report|一键完成日志分析、报告生成及落盘，最常用函数|log_path、output_dir、title|
|list_skills|列出所有可路由的 Skill 名称，便于精准调用|无|
|route_by_skill|按 skill_name 进行确定性路由，实现精准分析|skill_name、log_path 及对应分析参数|

---

### 8. 常见问题

针对使用过程中常见的疑问及异常场景，整理如下解答，帮助快速解决问题，提升使用效率。

#### Q1：我已经筛选了，为什么报告里还说样本很多？

解答：日志筛选的统计口径为“全量筛选”，即报告中显示的样本量是筛选后所有符合条件的日志；而日志预览仅为窗口抽样（由 max_output_lines 控制），仅用于快速查看片段，因此会出现“预览条数少、报告样本多”的情况。可通过查看 filter_logs 返回结果中的 matched_entries（全量匹配数）与 returned_entries（预览返回数）区分。

#### Q2：如何确保不是抽样误判？

解答：优先调用 scan_patterns_full 函数，该函数基于筛选后的全量日志进行模式统计，可获取准确的异常模式计数及证据，避免因抽样导致的误判；后续再结合 analyze_log_with_source 的结论，进一步验证根因。

#### Q3：如何快速缩减日志量？

解答：建议同时设置以下筛选条件，快速缩小日志范围：start_ts_ms + end_ts_ms（限定时间窗）+ log_type（限定日志类型）+ keywords（限定核心关键词）；若日志量仍较大，可额外增加 level（如仅保留 ERROR 级别），进一步缩减分析量。

---

### 9. 目录结构

项目目录结构规范如下，便于文件管理、脚本调用及后期维护，请勿随意修改目录层级及核心文件名称：

```text
my_agent_project/
├── agent.py          # Agent 核心配置文件
├── prompt.py         # 提示词配置文件
├── tools.py          # 核心工具函数定义文件
├── scripts/          # 自动化脚本目录
│   ├── preflight_check.py  # 前置检查脚本（Python版）
│   ├── preflight_check.sh  # 前置检查脚本（Shell版）
│   ├── run_report.py       # 报告生成自动化脚本（Python版）
│   └── run_report.sh       # 报告生成自动化脚本（Shell版）
├── source/           # 源码及日志资源目录
│   ├── log_rule.md   # 日志分析规则文档
│   ├── resource/*.log# 待分析日志文件存放目录
│   └── GZCheSuPaiApp/ # 源码目录（用于源码关联）
├── SKILL/            # Skill 配置目录
│   ├── README.md     # Skill 说明文档
│   └── log-analysis-assistant/ # 日志分析核心 Skill
│       ├── SKILL.md  # Skill 详细配置
│       ├── agents/openai.yaml # Agent 模型配置
│       └── references/log-analysis-playbook.md # 分析手册
└── output/           # 报告输出目录（自动创建）
```
> （注：文档部分内容可能由 AI 生成）
