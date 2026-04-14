#!/usr/bin/env python3
import argparse
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools import analyze_and_generate_report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate log analysis markdown report from tools.analyze_and_generate_report"
    )
    parser.add_argument("--log-path", required=True, help="Path to .log file")
    parser.add_argument("--source-root", default="source/GZCheSuPaiApp", help="Source code root")
    parser.add_argument("--rule-path", default="source/log_rule.md", help="Log rule markdown path")
    parser.add_argument("--start-ts-ms", type=int, default=None, help="Start timestamp in ms")
    parser.add_argument("--end-ts-ms", type=int, default=None, help="End timestamp in ms")
    parser.add_argument("--log-type", type=int, default=None, help="Log type f")
    parser.add_argument("--level", default=None, help="Business level, e.g. INFO/WARN/ERROR")
    parser.add_argument("--keywords", default=None, help="Comma-separated keywords")
    parser.add_argument("--max-output-lines", type=int, default=300, help="Max output lines for analysis")
    parser.add_argument("--title", default="日志分析报告", help="Report title")
    parser.add_argument("--output-dir", default="output", help="Output directory")

    args = parser.parse_args()

    report = analyze_and_generate_report(
        log_path=args.log_path,
        source_root=args.source_root,
        rule_path=args.rule_path,
        start_ts_ms=args.start_ts_ms,
        end_ts_ms=args.end_ts_ms,
        log_type=args.log_type,
        level=args.level,
        keywords=args.keywords,
        max_output_lines=args.max_output_lines,
        title=args.title,
        output_dir=args.output_dir,
    )

    report_path = Path(args.output_dir).resolve() / f"{Path(args.log_path).stem}.md"

    print("DONE")
    print(f"report_path={report_path}")
    print("preview:")
    for line in report.splitlines()[:12]:
        print(line)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
