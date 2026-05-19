# qBittorrent Seed Priority Scheduler Design

## Goal

Change qBittorrent CSV batch downloads so every pending source is submitted to qBittorrent first, then only the top N unfinished torrents with the most active seeds are resumed for downloading.

## User Interface

Users keep the existing command shape:

```bash
magnet-search download input.csv --storage downloads --engine qbittorrent --download-concurrency 3
```

For `engine=qbittorrent`, `--download-concurrency` means the maximum number of unfinished qBittorrent torrents allowed to run at one time. For other engines, it keeps the existing thread-pool meaning.

## Behavior

- CSV batch inputs using qBittorrent submit all unprocessed sources to qBittorrent before waiting for completion.
- New qBittorrent tasks are added paused, so submitting the full batch does not start every download at once.
- The scheduler polls qBittorrent, sorts unfinished torrents by active seed count, and resumes only the top N.
- Torrents outside the top N are paused while they remain unfinished.
- Completed torrents are recorded to download metadata, removed from qBittorrent while keeping files, and no longer participate in scheduling.
- Error states are recorded as failures and removed from qBittorrent.
- Torrents that remain unfinished with no active seeds for `no_seed_checks` scheduler polls are recorded as failures and removed.

## Sorting

The scheduler ranks unfinished torrents by:

1. Active seed count from `num_seeds`, descending.
2. Existing CSV order, ascending, as a stable tie-break.

`num_seeds` is used because the current code and monitor already expose it as the active connected seed count from `/api/v2/torrents/info`.

## Architecture

Add qBittorrent-specific batch orchestration to `src/magnet_search/qbittorrent.py`.

The new method owns qBittorrent Web API operations:

- Add all sources paused.
- Resolve source-to-hash mapping.
- Poll `/api/v2/torrents/info`.
- Resume top N hashes and pause the rest.
- Emit `DownloadResult` or failure rows through callbacks compatible with the existing CLI flow.

Keep generic CSV parsing, result recording, and upload integration in `download.py` and `cli.py`. The CLI chooses this scheduler only when `engine=qbittorrent` and the source is a CSV batch. `aria2c` and non-qBittorrent engines keep using `run_download_batch()`.

## Testing

Tests cover:

- qBittorrent batch submits every CSV source before completing any item.
- New qBittorrent tasks are added paused.
- The scheduler resumes only the top N unfinished torrents by `num_seeds`.
- When one active torrent completes, the next highest-seed unfinished torrent is resumed.
- Completed torrents are recorded with discovered files and removed from qBittorrent.
- Error and no-seed torrents are recorded as failures.
- The CLI routes qBittorrent CSV batch downloads through the qBittorrent scheduler and still routes aria2c through the generic batch runner.
