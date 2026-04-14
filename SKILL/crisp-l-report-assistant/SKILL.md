---
name: crisp-l-report-assistant
description: CRISP-L 报告生成技能。基于结构化分析结果输出固定格式 Markdown 报告，支持一键分析+生成+落盘。
---

# CRISP-L 报告技能

## 职责边界

1. 负责报告渲染与落盘，不重复做深度解析。
2. 优先调用 `analyze_and_generate_report` 一键输出。
3. 若已提供 `analysis` 结构体，则调用 `generate_markdown_report`。

## CRISP-L 固定结构

1. `0. 快速摘要（结论 + 修复建议）`
2. `C. Conclusion`
3. `R. Reproduction`
4. `I. Indicators`
5. `S. Source Correlation`
6. `P. Plan`
7. `L. Loop Closure`
8. `其他（局限性与证据预览）`

## 输出要求

1. 全中文。
2. 长内容优先表格化。
3. 每条核心结论都要有证据锚点。
4. 建议项必须可执行、可验收（优先级+指标阈值）。
