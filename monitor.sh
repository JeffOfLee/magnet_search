#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ -f "$SCRIPT_DIR/.venv/bin/activate" ]]; then
  source "$SCRIPT_DIR/.venv/bin/activate"
fi

QB_URL="${QB_URL:-http://localhost:8090}"
QB_USERNAME="${QB_USERNAME:-admin}"
QB_PASSWORD="${QB_PASSWORD:-admin123}"
INTERVAL="${INTERVAL:-5}"
ONCE="${ONCE:-}"
STATE_FILTER="${STATE_FILTER:-}"

ARGS=(
  --qbittorrent-url "$QB_URL"
  --qbittorrent-username "$QB_USERNAME"
  --qbittorrent-password "$QB_PASSWORD"
)

if [[ -n "${ONCE:-}" ]]; then
  ARGS+=(--once)
  echo "single snapshot (Ctrl-C to stop)..."
else
  ARGS+=(--interval "$INTERVAL")
  echo "watching qBittorrent every ${INTERVAL}s (Ctrl-C to stop)..."
fi

if [[ -n "${STATE_FILTER:-}" ]]; then
  for s in $STATE_FILTER; do
    ARGS+=(--state "$s")
  done
fi

magnet-search qbittorrent-monitor "${ARGS[@]}"
