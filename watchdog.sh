#!/usr/bin/env sh
# Ensure PYTHONPATH points to project root (script's parent directory)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# If this script lives in a `scripts/` subdir, project root is the parent.
# Otherwise assume the script directory itself is the project root.
if [ "$(basename "$SCRIPT_DIR")" = "scripts" ]; then
	PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
else
	PROJECT_ROOT="$SCRIPT_DIR"
fi
export PYTHONPATH="$PROJECT_ROOT"

# Ensure logs directory exists to avoid "Directory nonexistent" errors
mkdir -p "$PROJECT_ROOT/logs"

nohup poetry run python "$PROJECT_ROOT/scripts/watchdog.py" >> "$PROJECT_ROOT/logs/kiwoom_nohup.log" 2>&1 &
