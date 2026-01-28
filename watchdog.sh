#!/usr/bin/env sh
# Ensure PYTHONPATH points to project root (script's parent directory)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export PYTHONPATH="$PROJECT_ROOT"

nohup poetry run python "$PROJECT_ROOT/scripts/watchdog.py" >> "$PROJECT_ROOT/logs/kiwoom_nohup.log" 2>&1 &
