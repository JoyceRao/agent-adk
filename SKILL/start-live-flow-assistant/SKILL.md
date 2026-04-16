---
name: start-live-flow-assistant
description: startLive 开播链路分析技能。固定筛选开播事件日志，按 flowId 聚合阶段推进，联合源码关联结果输出 CRISP-L 报告。
---

# startLive 开播链路技能

## 职责边界

1. 默认调用 `analyze_start_live_flow_and_generate_crisp_l_report` 生成报告并落盘。
2. 固定筛选条件默认值：`c_startswith=1`、`keywords=CSP_BIZ_WATCHCAR_STARTLIVE,flowId`。
3. 固定源码目录：`source/GZCheSuPaiApp`（start-live 分析不使用其他 `source_root`）。
4. 中间结果必须保留 flow 聚合字段：`flowId/first_ts_ms/last_process/last_stage/stage_path/extra`。
5. 最终 Markdown 仅输出精简 CRISP-L 三段：`0/C/S`，并在“其他”追加未收敛/失败 flow 明细表（未收敛优先展示）。
6. JSON 落盘仅保留 `flows` 数组（根节点为数组）。

## 输入建议

- 必填：`log_path`
- 建议：`source_root`、`rule_path`
- 可选：`start_ts_ms/end_ts_ms/max_flows/exclude_last_stage/output_dir/title`（`max_flows` 默认 `2000`）

## 输出要求

1. 输出中文。
2. 结论必须附证据（flowId、stage、时间窗、源码定位）。
3. 对敏感字段执行脱敏。
4. 默认落盘：`output/[log文件名].md` 与 `output/[log文件名].json`（JSON 为 `flows` 数组）。
