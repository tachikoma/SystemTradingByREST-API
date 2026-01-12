#!/usr/bin/env sh
set -e

# Basic runtime checks before starting the app
# - warn if .env file not present (project expects .env)
# - ensure required env var KIWOOM_MODE exists (common for this project)
# - ensure the app directory is writable by current user

APP_DIR="/app"
REQUIRED_VARS="KIWOOM_MODE"

if [ ! -f "$APP_DIR/.env" ]; then
  echo "[warn] .env not found in $APP_DIR — confirm required env vars are passed at runtime or mount .env"
fi

for v in $REQUIRED_VARS; do
  if [ -z "$(printenv "$v")" ]; then
    echo "[warn] Required env var $v is not set"
  fi
done

# Check write permission
if [ ! -w "$APP_DIR" ]; then
  echo "[error] $APP_DIR is not writable by user $(id -u):$(id -g)"
  echo "Attempting to fix ownership..."
  if [ "$(id -u)" = "0" ]; then
    chown -R appuser:appuser "$APP_DIR" || true
    echo "Ownership changed to appuser"
  else
    echo "Cannot change ownership because not running as root. Ensure volume permissions are set correctly."
  fi
fi

# Exec the provided command
exec "$@"
