#!/usr/bin/env sh
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE="$PROJECT_ROOT/run/watchdog.pid"

if [ -f "$PIDFILE" ]; then
  pid=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ -n "$pid" ] && kill -0 "$pid" >/dev/null 2>&1; then
    echo "Stopping watchdog $pid"
    kill "$pid" || true
    # wait up to 5s
    i=0
    while kill -0 "$pid" >/dev/null 2>&1; do
      sleep 1
      i=$((i+1))
      if [ "$i" -ge 5 ]; then
        echo "Killing watchdog $pid"
        kill -9 "$pid" || true
        break
      fi
    done
  else
    echo "PID file exists but process not running: $pid"
  fi
  rm -f "$PIDFILE"
else
  echo "No PID file, using pgrep fallback"
  pids=$(pgrep -f "$(printf '%s' "$PROJECT_ROOT/scripts/watchdog.py")" || true)
  if [ -n "$pids" ]; then
    echo "Stopping by pids: $pids"
    echo "$pids" | xargs kill || true
  else
    echo "No watchdog process found"
  fi
fi
