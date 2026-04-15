from typing import Any, Optional

from .shared import _apply_filters, _build_filter_result, _parse_log_file

def filter_logs(
    log_path: str,
    start_ts_ms: Optional[int] = None,
    end_ts_ms: Optional[int] = None,
    log_type: Optional[int] = None,
    level: Optional[str] = None,
    keywords: Optional[str] = None,
    c_startswith: Optional[str] = None,
    max_output_lines: int = 1000,
) -> dict[str, Any]:
    """按时间和类型筛选日志，缩减日志量。

    Args:
        log_path: 日志文件路径。
        start_ts_ms: 起始时间戳（毫秒）。
        end_ts_ms: 结束时间戳（毫秒）。
        log_type: 外层日志类型 f（如 1 或 99）。
        level: 业务级别（I/W/E/D 或 INFO/WARN/ERROR）。
        keywords: 关键词，多个用英文逗号分隔。
        c_startswith: c 字段前缀匹配。传 1 时等价于前缀 "-:1"。
        max_output_lines: 最大返回行数，避免输出过大。
    """
    entries = _parse_log_file(log_path)
    filtered = _apply_filters(
        entries=entries,
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
        log_type=log_type,
        level=level,
        keywords=keywords,
        c_startswith=c_startswith,
    )
    return _build_filter_result(
        log_path=log_path,
        entries=entries,
        filtered=filtered,
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
        log_type=log_type,
        level=level,
        keywords=keywords,
        c_startswith=c_startswith,
        max_output_lines=max_output_lines,
        keyword_match="OR",
    )
