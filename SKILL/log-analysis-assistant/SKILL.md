---
name: log-analysis-assistant
description: 面向移动端与后端运行日志的排障技能。用于分析 `.log` 文件、粘贴日志文本和多文件日志集合，输出事件时间线、异常聚类、根因假设与验证建议。适用于崩溃排查、接口报错、超时激增、鉴权失败、版本回归对比等场景。
---

# 日志分析助手（执行规则版）

## 1. 目标

将原始日志转化为可执行诊断结论，强调“证据链 + 时间线 + 可验证假设”。

核心目标：

1. 快速缩减日志量并锁定问题范围。
2. 基于全量筛选样本给出稳定统计结论。
3. 输出可复盘、可落地、可验收的行动建议。

## 2. 适用输入

- 单个日志文件（如 `source/resource/*.log`）
- 多文件日志集合（跨设备、跨版本、跨时间段）
- 粘贴的局部日志片段
- 带业务背景的问题描述

## 3. 统一输入契约

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `log_path` | `str` | 是 | 待分析日志路径 |
| `source_root` | `str` | 否 | 默认 `source/GZCheSuPaiApp` |
| `rule_path` | `str` | 否 | 默认 `source/log_rule.md` |
| `start_ts_ms` | `int` | 否 | 起始时间戳（毫秒） |
| `end_ts_ms` | `int` | 否 | 结束时间戳（毫秒） |
| `log_type` | `int` | 否 | 外层日志类型（如 `1` / `99`） |
| `level` | `str` | 否 | `INFO/WARN/ERROR/DEBUG` |
| `keywords` | `str` | 否 | 关键词，逗号分隔（OR 匹配） |
| `pattern_keywords` | `str` | 否 | 额外模式关键词 |
| `bucket_ms` | `int` | 否 | 时间线桶大小 |
| `max_output_lines` | `int` | 否 | 预览条数上限 |
| `max_output_buckets` | `int` | 否 | 时间线桶返回上限 |
| `title` | `str` | 否 | 报告标题 |
| `output_dir` | `str` | 否 | 报告输出目录，默认 `output` |

## 4. 执行流程（必须遵守）

### 4.1 标准链路

1. 定义范围：先明确时间窗、症状、日志类型、关键词。
2. 条件筛选：调用 `filter_logs` 评估 `matched_entries` 与缩减比例。
3. 全量统计：调用 `scan_patterns_full` 获取模式计数与证据。
4. 时间线：调用 `build_timeline` 定位波峰窗口与模式聚集时段。
5. 联合分析：调用 `analyze_log_with_source` 做根因研判与源码关联。
6. 报告输出：按需调用 `analyze_and_generate_report` 或 `generate_markdown_report`。

### 4.2 跳步规则

1. 用户只要“筛选结果”时，执行到第 2 步即可。
2. 用户只要“全量统计”时，执行第 3 步并补充关键证据。
3. 用户明确“直接出报告”时，可直接走第 6 步的一键工具。

### 4.3 异常兜底

1. `log_path` 缺失时，先提示补充 `log_path`。
2. 筛选命中为 0 时，不输出强结论，只输出缺失项与扩窗建议。
3. 多文件结论冲突时，必须显式指出冲突和可能原因。

## 5. 工具使用规范

1. 结论前必须有全量统计证据，优先 `scan_patterns_full`。
2. 时间相关结论必须有时间桶证据，优先 `build_timeline`。
3. 代码关联结论必须有 `source_file:line` 证据，来自 `analyze_log_with_source`。
4. 需要可追溯交付时，输出 CRISP-L 报告并落盘。

## 6. 统计与口径规范

1. 模式统计与异常占比基于“筛选后的全量样本”。
2. 证据预览允许抽样展示。
3. `max_output_lines` 只影响预览，不影响统计结论。
4. 输出中应明确标注：`全量统计` 与 `抽样证据预览`。

## 7. 输出规范

1. 输出语言统一中文。
2. 结论必须附证据点，至少包含行号或时间戳和关键词。
3. 长内容优先使用 Markdown 表格。
4. 复杂任务优先使用以下结构：

- 事件总结
- 时间线
- 关键发现
- 根因假设（含置信度）
- 下一步行动（验证与修复）

5. 若用户要求报告，优先输出 CRISP-L 结构：

- 0. 快速摘要
- C. Conclusion
- R. Reproduction
- I. Indicators
- S. Source Correlation
- P. Plan
- L. Loop Closure

## 8. 安全与质量约束

1. 不暴露完整密钥、令牌、Cookie、JWT、设备标识。
2. 明确区分“症状”和“根因”，避免偷换概念。
3. 数据不足时，先写清缺失项，再给条件化结论。
4. 修复建议必须可执行且可验收。

## 9. 快速调用示例

```text
请使用 $log-analysis-assistant 分析 source/resource/xxx.log，先筛选再做全量模式统计，并输出时间线和根因假设。
```

```text
请使用 $log-analysis-assistant 在 1775530200000~1775532000000 时间窗分析 f=1，关键词为 [RN_NET],reactnative_exception。
```

```text
请使用 $log-analysis-assistant 直接输出 CRISP-L 报告到 output 目录。
```

## 10. 参考资料

需要模式识别或排障清单时，读取 [log-analysis-playbook.md](references/log-analysis-playbook.md)。
