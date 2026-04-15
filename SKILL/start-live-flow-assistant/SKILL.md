---
name: start-live-flow-assistant
description: startLive 开播链路分析技能。固定筛选开播事件日志，按 flowId 聚合阶段推进，联合源码关联结果输出 CRISP-L 报告。
---

# startLive 开播链路技能

## 职责边界

1. 默认调用 `analyze_start_live_flow_and_generate_crisp_l_report` 生成报告并落盘。
2. 固定筛选条件默认值：`c_startswith=1`、`keywords=CSP_BIZ_WATCHCAR_STARTLIVE,flowId`。
3. 中间结果必须保留 flow 聚合字段：`flowId/first_ts_ms/last_process/last_stage/stage_path/extra`。
4. 最终输出统一走 CRISP-L 报告结构，包含 `S. Source Correlation` 段落。

## 输入建议

- 必填：`log_path`
- 建议：`source_root`、`rule_path`
- 可选：`start_ts_ms/end_ts_ms/max_flows/exclude_last_stage/output_dir/title`

## 输出要求

1. 输出中文。
2. 结论必须附证据（flowId、stage、时间窗、源码定位）。
3. 对敏感字段执行脱敏。
4. 默认落盘：`output/[log文件名].md` 与 `output/[log文件名].json`。
