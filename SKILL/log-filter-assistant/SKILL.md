---
name: log-filter-assistant
description: 日志筛选技能。用于按时间范围、日志类型、业务级别、关键词快速缩减日志规模，并返回统计与证据预览，作为后续分析输入。
---

# 日志筛选技能

## 职责边界

1. 仅负责筛选与预览，不做根因判定。
2. 统一调用 `filter_logs`。
3. 输出保留 `total/matched/returned/dropped` 统计。

## 输入要求

- 必填：`log_path`
- 可选：`start_ts_ms/end_ts_ms/log_type/level/keywords/max_output_lines`

## 输出要求

1. 全中文输出。
2. 优先表格化展示筛选条件与结果。
3. 预览片段需脱敏，不输出完整敏感字段。

## 推荐输出结构

- 筛选条件
- 统计结果
- 证据预览（前 N 条）
- 建议下一步（是否进入源码关联分析）
