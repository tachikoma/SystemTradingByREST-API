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

# Load .env if present and export variables so Python watcher can see them
if [ -f "$PROJECT_ROOT/.env" ]; then
	# export all variables defined in .env
	set -a
	# shellcheck disable=SC1090
	. "$PROJECT_ROOT/.env"
	set +a
fi

# Ensure logs directory exists to avoid "Directory nonexistent" errors
mkdir -p "$PROJECT_ROOT/logs"

# Run from project root so Poetry can always find pyproject.toml
cd "$PROJECT_ROOT" || exit 1

# cron often has a minimal PATH, so resolve Poetry explicitly.
if command -v poetry >/dev/null 2>&1; then
	POETRY_BIN="$(command -v poetry)"
elif [ -x "$HOME/.local/bin/poetry" ]; then
	POETRY_BIN="$HOME/.local/bin/poetry"
elif [ -x "$HOME/.poetry/bin/poetry" ]; then
	POETRY_BIN="$HOME/.poetry/bin/poetry"
else
	echo "poetry executable not found (PATH=$PATH)" >&2
	exit 1
fi

nohup "$POETRY_BIN" run python "$PROJECT_ROOT/scripts/watchdog.py" >> "$PROJECT_ROOT/logs/kiwoom_nohup.log" 2>&1 &
