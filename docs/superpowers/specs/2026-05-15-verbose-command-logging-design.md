# Verbose Command Logging Design

## Goal

Add a `--verbose` option to CLI commands so users can inspect progress and routing decisions without changing normal stdout output.

## User Interface

```bash
magnet-search search "resource name" --verbose
magnet-search search input.csv --output results.csv --verbose
magnet-search batch input.csv --column title --output results.csv --verbose
magnet-search download input.csv --output downloads/ --verbose
```

## Behavior

- Verbose logs are written to stderr.
- Normal stdout remains unchanged, including JSON output from `search --json`.
- Default behavior without `--verbose` stays quiet.
- Search verbose logs include mode, query or CSV path, limit, output path, result count, and warning count.
- Download verbose logs include source, output directory, batch detection, download concurrency, upload config, upload concurrency, download completion, and upload completion.

## Architecture

Keep verbose logging in `src/magnet_search/cli.py` because it describes command orchestration, not provider or storage internals. Add a small `_verbose()` helper and pass a `verbose` flag through existing CLI helper functions.

## Testing

Tests cover:

- `search --json --verbose` keeps stdout parseable and emits stderr logs.
- Batch search emits CSV routing logs.
- Download with upload emits download and upload progress logs.
- Commands without `--verbose` keep existing output shape.
