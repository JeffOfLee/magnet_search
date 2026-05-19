#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# activate venv if present
if [[ -f "$SCRIPT_DIR/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.venv/bin/activate"
fi

LOG_FILE="$SCRIPT_DIR/transfer.log"
QB_URL="http://localhost:8090"
QB_USERNAME="admin"
QB_PASSWORD="admin123"
STORAGE_DIR="$SCRIPT_DIR/downloads/cache"
SOURCE_CSV="$SCRIPT_DIR/downloads/torrents/torrent_list.csv"
DOWNLOAD_META="$STORAGE_DIR/.download_meta.csv"
UPLOAD_META="$SCRIPT_DIR/upload_result.csv"
METRICS_DB="$SCRIPT_DIR/transfer_metrics.db"
MONITOR_PID=""

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

cleanup() {
  if [[ -n "${MONITOR_PID:-}" ]]; then
    kill "$MONITOR_PID" 2>/dev/null || true
    wait "$MONITOR_PID" 2>/dev/null || true
    MONITOR_PID=""
  fi
  rm -f "${COOKIE_TMP:-}"
}

trap cleanup EXIT

log "=== transfer start ==="
log "script_dir=$SCRIPT_DIR"
log "qb_url=$QB_URL"
log "source_csv=$SOURCE_CSV"
log "storage_dir=$STORAGE_DIR"

# ---- Pre-flight checks ----
if [[ -f "$SOURCE_CSV" ]]; then
  TORRENT_COUNT=$(tail -n +2 "$SOURCE_CSV" | wc -l | tr -d ' ')
  log "torrent row count in CSV: $TORRENT_COUNT"
fi

COOKIE_TMP=$(mktemp)
curl -s -c "$COOKIE_TMP" -X POST "$QB_URL/api/v2/auth/login" \
  -d "username=$QB_USERNAME&password=$QB_PASSWORD" > /dev/null
QB_TORRENT_COUNT=$(curl -s -b "$COOKIE_TMP" "$QB_URL/api/v2/torrents/info" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))")
log "current qBittorrent torrents: $QB_TORRENT_COUNT"

if [[ -f "$DOWNLOAD_META" ]]; then
  ALREADY_DL=$(python3 -c "
import csv
with open('$DOWNLOAD_META', newline='', encoding='utf-8') as f:
    rows = list(csv.DictReader(f))
success = sum(1 for r in rows if r.get('status') == 'success')
failed = sum(1 for r in rows if r.get('status') == 'failed')
print(f'success={success} failed={failed}')
")
  log "previous download meta: $ALREADY_DL"

  # reconcile: remove records whose files no longer exist on disk
  RECONCILE=$(python3 -c "
import csv, os, shutil, tempfile
from pathlib import Path
meta_path = '$DOWNLOAD_META'
new_rows = []
removed = 0
with open(meta_path, newline='', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    for row in reader:
        path_str = row.get('path', row.get('file', ''))
        if not path_str:
            new_rows.append(row)
            continue
        file_path = Path(path_str)
        if not file_path.is_absolute():
            file_path = Path('$STORAGE_DIR') / file_path
        if file_path.exists():
            new_rows.append(row)
        else:
            removed += 1
if removed and fieldnames:
    tmp = tempfile.NamedTemporaryFile(mode='w', delete=False, newline='', encoding='utf-8', dir='$STORAGE_DIR')
    writer = csv.DictWriter(tmp, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(new_rows)
    tmp.close()
    os.replace(tmp.name, meta_path)
print(f'reconciled: removed {removed} stale record(s), kept {len(new_rows)}')
")
  log "download meta reconcile: $RECONCILE"
fi

if [[ -f "$UPLOAD_META" ]]; then
  ALREADY_UP=$(python3 -c "
import csv
with open('$UPLOAD_META', newline='', encoding='utf-8') as f:
    rows = list(csv.DictReader(f))
success = sum(1 for r in rows if r.get('status') == 'success')
failed = sum(1 for r in rows if r.get('status') == 'failed')
print(f'success={success} failed={failed}')
")
  log "previous upload meta: $ALREADY_UP"
fi

# ---- Wait for Web UI ----
log "waiting for qBittorrent Web UI..."
for i in $(seq 1 30); do
  if curl -s "$QB_URL/api/v2/app/version" > /dev/null 2>&1; then
    log "qBittorrent is ready ($(curl -s "$QB_URL/api/v2/app/version"))"
    break
  fi
  log "  attempt $i/30 ..."
  sleep 2
done

# ---- Start qBittorrent monitor in background ----
MONITOR_LOG="$SCRIPT_DIR/monitor.log"
log "starting qBittorrent monitor (log: $MONITOR_LOG)..."

start_monitor() {
  local cookie_file
  cookie_file=$(mktemp)
  curl -s -c "$cookie_file" -X POST "$QB_URL/api/v2/auth/login" \
    -d "username=$QB_USERNAME&password=$QB_PASSWORD" > /dev/null
  while true; do
    curl -s -b "$cookie_file" "$QB_URL/api/v2/torrents/info" | python3 -c "
import json, sys
from datetime import datetime
data = json.load(sys.stdin)
total_dl = total_up = active = 0
for t in data:
    state = t.get('state','')
    dlspeed = t.get('dlspeed', 0) or 0
    upspeed = t.get('upspeed', 0) or 0
    total_dl += dlspeed
    total_up += upspeed
    if state in ('downloading','forcedDL','queuedDL','metaDL','checkingDL','stalledDL'):
        active += 1
def fmt(b):
    if b >= 1048576: return f'{b/1048576:.1f} MB/s'
    if b >= 1024: return f'{b/1024:.1f} KB/s'
    return f'{b} B/s'
ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
print(f'[{ts}] {len(data)} torrents  active={active}  dl={fmt(total_dl)}  ul={fmt(total_up)}')
" >> "$MONITOR_LOG"
    sleep 5
  done
}

start_monitor &
MONITOR_PID=$!
log "monitor started (pid=$MONITOR_PID)"

# ---- Download + Upload ----
log "starting download + upload pipeline..."
magnet-search download "$SOURCE_CSV" \
  --column torrent_filename \
  --storage "$STORAGE_DIR" \
  --engine qbittorrent \
  --download-concurrency 5 \
  --upload-concurrency 5 \
  --qbittorrent-url "$QB_URL" \
  --qbittorrent-username "$QB_USERNAME" \
  --qbittorrent-password "$QB_PASSWORD" \
  --upload "$SCRIPT_DIR/s3-upload.toml" \
  --transfer-cache-storage 200GB \
  --upload-meta "$UPLOAD_META" \
  --metrics-db "$METRICS_DB" \
  --verbose

# ---- Post-run summary ----
log "pipeline complete, summarizing..."

if [[ -f "$MONITOR_LOG" ]]; then
  log "monitor log lines: $(wc -l < "$MONITOR_LOG" | tr -d ' ')"
fi
if [[ -f "$UPLOAD_META" ]]; then
  SUMMARY=$(python3 -c "
import csv
with open('$UPLOAD_META', newline='', encoding='utf-8') as f:
    rows = list(csv.DictReader(f))
success = sum(1 for r in rows if r.get('status') == 'success')
failed = sum(1 for r in rows if r.get('status') == 'failed')
items = len(set(r.get('input','') for r in rows if r.get('input')))
print(f'items={items} files_ok={success} files_failed={failed}')
")
  log "upload result: $SUMMARY"
fi

if [[ -f "$DOWNLOAD_META" ]]; then
  DL_SUMMARY=$(python3 -c "
import csv
with open('$DOWNLOAD_META', newline='', encoding='utf-8') as f:
    rows = list(csv.DictReader(f))
success = sum(1 for r in rows if r.get('status') == 'success')
failed = sum(1 for r in rows if r.get('status') == 'failed')
print(f'success={success} failed={failed}')
")
  log "download result: $DL_SUMMARY"
fi

log "=== transfer done ==="
