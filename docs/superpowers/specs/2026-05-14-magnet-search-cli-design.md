# Magnet Search CLI Design

## Goal

Build a Python command-line tool that searches for magnet links by resource name and supports batch searches from CSV files.

The first version supports:

- Single resource-name search, such as a movie title.
- Batch search from a CSV file by selecting one input column.
- Default search through legal/public providers.
- Optional user-configured HTTP providers for additional sources.

The tool must not hard-code gray or piracy-oriented sources. Custom providers are user-controlled configuration.

## Command Interface

The installed command is `magnet-search`.

Single search:

```bash
magnet-search search "resource name" --limit 3
magnet-search search "resource name" --limit 3 --json
```

Batch search:

```bash
magnet-search batch input.csv --column title --output results.csv --limit 3
```

Defaults:

- `--limit` defaults to `3`.
- Batch output writes one row per result.
- If a batch query has no results, the output still includes one row for that query with empty result fields.

## Output Fields

Search results use the same normalized fields in table, JSON, and CSV output:

- `query`: original user query.
- `title`: result title.
- `magnet`: magnet URI.
- `source`: provider name.
- `size`: human-readable size when available.
- `published_at`: publication or upload date when available.
- `score`: ranking score.
- `url`: source detail page URL when available.

Batch CSV output columns are:

```text
query,title,magnet,source,size,published_at,score,url
```

## Architecture

The project is a small Python package with focused modules:

- `cli.py`: Typer command entry point, argument parsing, human-readable output, and exit codes.
- `models.py`: shared data structures such as `SearchResult` and provider errors.
- `providers/base.py`: provider protocol with `search(query, limit)`.
- `providers/internet_archive.py`: default legal/public provider.
- `providers/configurable.py`: custom HTTP provider loaded from user configuration.
- `batch.py`: CSV reading, per-row searching, and CSV writing.
- `ranking.py`: deterministic ranking and result truncation.
- `config.py`: configuration discovery and TOML loading.

Data flow:

1. CLI receives a query or CSV path.
2. Provider manager loads enabled providers.
3. Each provider returns normalized `SearchResult` objects.
4. Ranking sorts by title relevance, provider quality, and metadata completeness.
5. Results are truncated to the requested limit.
6. CLI renders table, JSON, or CSV output.

## Provider Model

All providers implement the same interface:

```python
class Provider(Protocol):
    name: str

    def search(self, query: str, limit: int) -> list[SearchResult]:
        ...
```

The default provider uses public/legal metadata sources.

Custom HTTP providers are defined in TOML under the user's config file:

```toml
[[providers.http]]
name = "example"
enabled = true
search_url = "https://example.invalid/search?q={query}"
result_path = "results"
title_path = "title"
magnet_path = "magnet"
url_path = "url"
size_path = "size"
published_at_path = "published_at"
```

The configurable provider only maps user-specified JSON responses into normalized results. It does not include any built-in gray-source endpoints.

## Error Handling

- A single provider failure does not fail the whole search. The CLI prints a warning to stderr and returns results from other providers.
- If all providers fail, the command exits non-zero.
- If a CSV file is missing the requested column, `batch` exits non-zero with a clear message.
- If a CSV row has an empty query value, output includes an empty result row with that query value.
- Invalid custom provider config fails fast before searching.
- Network timeouts are bounded and reported by provider name.

## Ranking

Ranking is deterministic and intentionally simple in the first version:

- Higher title similarity to the query ranks first.
- Results with a magnet URI rank above incomplete results.
- Results with source URL, size, or date metadata receive small boosts.
- Provider quality can add a small provider-specific boost.

The exact numeric score is internal but exposed as `score` for transparency.

## Testing Strategy

Use test-first development.

Core tests:

- CLI `search` renders table output for a mocked provider.
- CLI `search --json` returns valid JSON.
- CLI `batch` reads a CSV column and writes one row per result.
- CLI `batch` writes an empty result row when a query has no matches.
- CSV missing column returns non-zero with a clear error.
- Ranking prefers closer title matches and complete magnet results.
- Configurable provider maps JSON fields into `SearchResult`.
- Provider failure produces a warning without losing results from working providers.

Network-dependent providers should be tested with mocked HTTP responses, not live network calls.

## Non-Goals

- No built-in piracy or gray-market source list.
- No GUI or web application.
- No downloader, torrent client integration, or automatic opening of magnet links.
- No aggressive crawling.
- No account login or bypass behavior.
