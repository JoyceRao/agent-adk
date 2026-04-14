#!/usr/bin/env bash
set -euo pipefail

# One-command wrapper for generating markdown report.
# Example:
#   scripts/run_report.sh \
#     --log-path source/resource/20_xxx.log \
#     --source-root source/GZCheSuPaiApp \
#     --rule-path source/log_rule.md \
#     --log-type 1 \
#     --max-output-lines 1500 \
#     --title "日志分析报告" \
#     --output-dir output

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PY_BIN=".venv/bin/python"
if [[ ! -x "$PY_BIN" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PY_BIN="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    PY_BIN="$(command -v python)"
  else
    echo "ERROR: python interpreter not found" >&2
    exit 1
  fi
fi

"$PY_BIN" scripts/run_report.py "$@"
