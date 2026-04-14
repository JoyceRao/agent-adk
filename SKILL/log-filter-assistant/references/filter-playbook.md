# Filter Playbook

## 目标

通过最小代价缩小日志窗口，为下游分析提升信噪比。

## 策略

1. 先按 `start_ts_ms/end_ts_ms` 收敛时间窗口。
2. 再按 `log_type` 缩减日志域。
3. 再按 `level` 与 `keywords` 聚焦异常信号。
4. 控制 `max_output_lines`，避免预览过大影响分析效率。

## 质量门槛

- `matched_entries` 为 0：提示扩窗或放宽关键词。
- `returned_entries << matched_entries`：提示当前为抽样窗口，需谨慎下结论。
