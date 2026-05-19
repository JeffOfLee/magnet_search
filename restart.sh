#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# activate venv if present
if [[ -f "$SCRIPT_DIR/.venv/bin/activate" ]]; then
  source "$SCRIPT_DIR/.venv/bin/activate"
fi

QB_URL="http://localhost:8090"
QB_USERNAME="admin"
QB_PASSWORD="admin123"

echo "=== restart.sh ==="

# Stop any running transfer.sh processes
echo "stopping running processes..."
ps aux | grep -E "transfer.sh|magnet-search download" | grep -v grep | awk '{print $2}' | xargs kill 2>/dev/null || true
ps aux | grep "qbittorrent-monitor" | grep -v grep | awk '{print $2}' | xargs kill 2>/dev/null || true
sleep 2

# Clear qBittorrent torrents (keep files on disk)
echo "clearing qBittorrent torrents..."
COOKIE=$(mktemp)
curl -s -c "$COOKIE" -X POST "$QB_URL/api/v2/auth/login" \
  -d "username=$QB_USERNAME&password=$QB_PASSWORD" > /dev/null
HASHES=$(curl -s -b "$COOKIE" "$QB_URL/api/v2/torrents/info" | python3 -c "import json,sys; print('|'.join(t['hash'] for t in json.load(sys.stdin)))")
COUNT=$(echo "$HASHES" | tr '|' '\n' | grep -c . 2>/dev/null || echo 0)
if [ -n "$HASHES" ] && [ "$COUNT" -gt 0 ]; then
  curl -s -b "$COOKIE" -X POST "$QB_URL/api/v2/torrents/delete" \
    -d "hashes=$HASHES&deleteFiles=false" > /dev/null
  echo "  cleared $COUNT torrent(s)"
fi
rm -f "$COOKIE"

# Reset ephemeral metadata (keep download_meta/upload_meta for reconcile)
echo "resetting ephemeral metadata..."
rm -f "$SCRIPT_DIR/downloads/cache/.download_meta.csv"
rm -f "$SCRIPT_DIR/upload_result.csv"
rm -f "$SCRIPT_DIR/monitor.log"
rm -f "$SCRIPT_DIR/transfer.log"
rm -f "$SCRIPT_DIR/transfer_metrics.db"

# Show cache state
CACHE_COUNT=$(find "$SCRIPT_DIR/downloads/cache" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')
CACHE_SIZE=$(du -sh "$SCRIPT_DIR/downloads/cache" 2>/dev/null | cut -f1)
echo "cache preserved: $CACHE_COUNT dir(s), $CACHE_SIZE"

# Start transfer.sh in background
echo "starting transfer.sh in background..."
nohup bash "$SCRIPT_DIR/transfer.sh" > /dev/null 2>&1 &
echo "  pid=$!"
echo "=== done ==="
