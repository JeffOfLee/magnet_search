# Unified Search Command Design

## Goal

Make `magnet-search search` handle both single-query search and CSV batch search based on the first argument, matching the behavior recently added to `download`.

## User Interface

```bash
magnet-search search "resource name" --limit 3
magnet-search search "resource name" --limit 3 --json
magnet-search search input.csv --output results.csv
magnet-search search input.csv --column title --output results.csv --limit 3
```

When the first argument points to an existing `.csv` file, `search` runs batch mode. Otherwise it runs single-query mode.

## Behavior

- Batch mode defaults `--column` to `query`.
- Batch mode requires `--output`.
- Single-query mode ignores `--column` and `--output`.
- Single-query mode keeps existing table and `--json` behavior.
- Existing `batch` command remains available for backward compatibility and continues using its current required `--column` and `--output` options.

## Architecture

Keep all behavior in `src/magnet_search/cli.py` because the lower-level batch implementation already exists in `src/magnet_search/batch.py`. Extract small helper functions so `search` and `batch` can share the batch execution path and warning handling.

## Testing

Add CLI tests for:

- `search input.csv --output results.csv` using default `query` column.
- `search input.csv --column title --output results.csv`.
- `search input.csv` failing cleanly when `--output` is omitted.
- Existing single-query `search` behavior continuing to work.
