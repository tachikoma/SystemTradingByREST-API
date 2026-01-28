#!/usr/bin/env sh
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Stop existing watcher if present
sh "$PROJECT_ROOT/scripts/stop_watchdog.sh"

# Start watcher (watchdog.sh ensures PYTHONPATH)
sh "$PROJECT_ROOT/watchdog.sh"
