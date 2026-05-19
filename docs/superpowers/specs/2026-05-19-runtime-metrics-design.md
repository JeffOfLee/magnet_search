# Runtime Metrics Design

## Goal

Add runtime metrics for long-running `magnet-search` commands and a `magnet-search metrics` observation command. The executing command writes current status, progress, speed, and error counters into SQLite, and the observation command reads that SQLite database and refreshes a terminal view periodically.

## User Interface

```bash
magnet-search search input.csv --metrics-db .metrics.sqlite
magnet-search batch input.csv --column title --output results.csv --metrics-db .metrics.sqlite
magnet-search download input.csv --storage downloads --metrics-db downloads/.metrics.sqlite
magnet-search download input.csv --storage downloads --upload s3-upload.toml --metrics-db downloads/.metrics.sqlite

magnet-search metrics --metrics-db downloads/.metrics.sqlite --interval 1
magnet-search metrics --metrics-db downloads/.metrics.sqlite --once
```

`--metrics-db` is explicit. Commands keep their current output behavior when the option is omitted. For `download`, users can place the database under `--storage`; no hidden default is introduced because implicit metrics writes would create side effects for existing scripts.

## Metrics Model

Use a small SQLite database managed by `src/magnet_search/metrics.py`.

`runs` stores one row per command execution:

- `run_id`: generated UUID.
- `command`: `search`, `batch`, `download`, or `upload`.
- `status`: `running`, `completed`, or `failed`.
- `stage`: current high-level stage, such as `searching`, `downloading`, `uploading`, or `done`.
- `started_at`, `updated_at`, `finished_at`: Unix timestamps.
- `error`: last fatal error, if any.

`run_metrics` stores the current aggregate counters for a run:

- `run_id`
- `total_items`
- `completed_items`
- `failed_items`
- `skipped_items`
- `downloaded_files`
- `uploaded_files`
- `bytes_downloaded`
- `bytes_uploaded`
- `items_per_second`
- `bytes_per_second`
- `eta_seconds`

The schema stores a current snapshot rather than an event log. That keeps updates cheap and makes the monitor command simple. The latest run is selected by `updated_at` unless the user provides `--run-id`.

## Runtime Updates

Introduce a thread-safe `MetricsTracker` abstraction. CLI commands create a tracker only when `--metrics-db` is provided, then pass update callbacks into existing batch and transfer orchestration.

Search and legacy batch:

- Count CSV rows after header validation and write `total_items`.
- Update `completed_items` or `failed_items` after each query.
- Recalculate elapsed time, rate, and ETA on every update.

Download:

- After CSV source resolution and skip filtering, write `total_items` and `skipped_items`.
- Update completed, failed, downloaded file count, and downloaded bytes after each item.
- Single-source downloads use `total_items=1`.

Upload:

- Switch stage to `uploading` when upload futures are active.
- Update uploaded file count, uploaded bytes, failure count, speed, and ETA as futures finish.
- Cached upload work and newly downloaded upload work both update the same run.

qBittorrent:

- The existing polling loop updates stage, item progress, download speed, and ETA when a tracker is attached.
- `qbittorrent-monitor` remains available for qBittorrent-specific torrent lists.

On normal completion, mark the run `completed` and set `finished_at`. On handled command errors, mark the run `failed` with the error message before exiting.

## Observation Command

Add `magnet-search metrics` in `src/magnet_search/cli.py`.

Options:

- `--metrics-db PATH`: SQLite database path.
- `--interval FLOAT`: refresh interval in seconds, default `1.0`.
- `--once`: render one snapshot and exit.
- `--run-id TEXT`: optional run selection. Defaults to the most recently updated run.
- `--json`: optional machine-readable single snapshot output, valid with `--once`.

The terminal view uses Rich `Live` and displays:

- Run id, command, status, stage.
- Progress percentage and `completed/failed/skipped/total`.
- Downloaded files, uploaded files.
- Item speed, byte speed, ETA.
- Last update time and last error.

If the database or run is missing, the command exits cleanly with a clear error. If a running command stops updating, the view still shows the latest snapshot and last update timestamp.

## Architecture

Keep metrics persistence in `metrics.py` and command wiring in `cli.py`.

`batch.py` and `download.py` should remain mostly orchestration-focused. They can accept optional callbacks for progress events where needed, but they should not import SQLite or know about the metrics schema.

The SQLite code uses only the Python standard library `sqlite3`; no new runtime dependency is needed.

## Testing

Tests cover:

- Metrics database initialization creates the expected tables.
- `MetricsTracker` records run status, counters, rates, and ETA.
- Search/batch commands update metrics without changing stdout or existing CSV output.
- Download batch updates total, completed, failed, files, bytes, and completion status.
- Upload updates uploaded file counters for cached and newly downloaded files.
- `magnet-search metrics --once` renders a snapshot from SQLite.
- `magnet-search metrics --once --json` emits parseable JSON.
- Commands without `--metrics-db` preserve existing behavior.
