# Download and Upload Concurrency Design

## Goal

Add independent concurrency controls for batch downloads and S3 uploads.

## User Interface

```bash
magnet-search download input.csv --output downloads/ \
  --download-concurrency 4 \
  --upload s3-upload.toml \
  --upload-concurrency 8
```

## Behavior

- `--download-concurrency` controls how many batch rows can run through `aria2c` at the same time.
- `--upload-concurrency` controls how many S3 upload tasks can run at the same time.
- Both default to `1`.
- Single-source download accepts the options for interface consistency, but only has one download task.
- Download and upload are decoupled: each completed download can enqueue an upload while other downloads are still running.
- Download failures are collected after started downloads finish, then reported as a command failure.
- Upload failures are collected after started uploads finish, then reported as a command failure.

## Architecture

`src/magnet_search/download.py` extends `run_download_batch()` with:

- `download_concurrency`
- `on_result`

The callback runs when each `DownloadResult` completes, letting CLI code submit upload work to a separate `ThreadPoolExecutor`.

`src/magnet_search/cli.py` owns the upload executor because upload is optional and tied to the `--upload` flag.

## Testing

Tests cover:

- Batch download respects the `download_concurrency` cap.
- Download failures are aggregated.
- CLI passes the download concurrency option into batch execution.
- CLI uses upload concurrency for batch uploads.
- Existing default serial behavior remains valid.
