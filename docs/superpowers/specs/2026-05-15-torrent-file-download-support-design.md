# Torrent File Download Support Design

## Goal

Extend `magnet-search download` so it can download BT `.torrent` files as well as magnet links, including CSV batch inputs that mix both source types.

## User Interface

```bash
magnet-search download "magnet:?xt=..." --output downloads/
magnet-search download movie.torrent --output downloads/
magnet-search download input.csv --output downloads/
magnet-search download input.csv --column source --output downloads/
```

The existing `--upload s3-upload.toml` behavior remains unchanged and uploads files produced by either magnet or torrent downloads.

## Source Detection

- Existing `.csv` path: batch input.
- Existing `.torrent` path: torrent file input.
- Anything else: direct magnet link or aria2c-supported URI.

CSV rows use the configured column, defaulting to `magnet` for backward compatibility. Each row value can be a magnet link or a `.torrent` path. Relative `.torrent` paths inside CSV files are resolved relative to the CSV file's parent directory.

## Architecture

Keep using `aria2c`; it already supports magnet links and torrent files. Rename internal model fields from magnet-specific names to source-oriented names where useful, while keeping aliases or compatible behavior where tests and CLI code expect `magnet`.

`src/magnet_search/download.py` will normalize each source before invoking aria2c:

- Strip whitespace.
- For CSV batch rows, resolve relative `.torrent` paths against the CSV parent.
- Pass the resolved source string to aria2c.

## Testing

Add tests for:

- Single `.torrent` file command construction.
- CSV row with relative `.torrent` path resolving from the CSV directory.
- Existing magnet download behavior remaining unchanged.
- README examples documenting torrent support.
