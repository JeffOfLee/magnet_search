# Custom Provider Development Guide

This guide explains how to add a new code-backed provider to `magnet-search` and when you should avoid that work by using the existing `JsonHttpProvider` instead.

## Choose the simplest extension path

Prefer `JsonHttpProvider` when all of the following are true:

- The upstream search API returns JSON.
- One request is enough to fetch results.
- Result fields can be mapped with stable dot-path lookups.
- The API already returns a usable magnet link.

In that case, a user can add a provider in `~/.config/magnet-search/config.toml` without changing project code.

Write a custom Python provider when you need any of the following:

- Authentication, signing, or custom headers.
- Pagination or multi-step fetches.
- Non-JSON parsing.
- Field normalization beyond simple dot-path extraction.
- Magnet generation from upstream metadata or torrent files.
- Provider-specific filtering, retries, or fallback behavior.

## Provider contract

`SearchService` works against the protocol defined in [`src/magnet_search/providers/base.py`](/Users/fujiao.li/source/magnet_search/src/magnet_search/providers/base.py):

```python
class Provider(Protocol):
    name: str

    def search(self, query: str, limit: int) -> list[SearchResult]:
        ...
```

Every provider must expose:

- `name`: a stable provider identifier used in output and warnings.
- `search(query, limit)`: returns a `list[SearchResult]`.

The `SearchResult` model lives in [`src/magnet_search/models.py`](/Users/fujiao.li/source/magnet_search/src/magnet_search/models.py) and requires:

- `query`
- `title`
- `magnet`
- `source`

Optional fields are:

- `size`
- `published_at`
- `url`
- `score`

`SearchService` assumes providers are isolated units. If one provider fails, it records a warning and keeps results from the others. If every provider fails, it raises `AllProvidersFailed`.

## Implementation pattern

The built-in [`InternetArchiveProvider`](/Users/fujiao.li/source/magnet_search/src/magnet_search/providers/internet_archive.py) is the best reference for a real provider.

Use this structure for a new provider:

1. Create a provider module under `src/magnet_search/providers/`.
2. Give the class a stable `name`.
3. Accept an injectable HTTP client in `__init__` so tests can stub network calls.
4. Validate `limit` before doing any network work.
5. Convert upstream data into `SearchResult` instances.
6. Raise `ProviderError` when the provider cannot complete the search.

Minimal example:

```python
from __future__ import annotations

from typing import Any

import httpx

from magnet_search.models import ProviderError, SearchResult


class ExampleProvider:
    name = "example"
    search_url = "https://example.invalid/search"

    def __init__(self, client: Any | None = None, timeout: float = 15.0):
        self.client = client or httpx.Client()
        self.timeout = timeout

    def search(self, query: str, limit: int) -> list[SearchResult]:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        if limit == 0:
            return []

        try:
            response = self.client.get(
                self.search_url,
                params={"q": query, "limit": limit},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as error:
            raise ProviderError(f"{self.name} search failed: {error}") from error

        raw_results = payload.get("results", [])
        if not isinstance(raw_results, list):
            raise ProviderError(f"{self.name} search failed: results must be a list")

        results: list[SearchResult] = []
        for raw in raw_results[:limit]:
            if not isinstance(raw, dict):
                continue

            title = raw.get("title")
            magnet = raw.get("magnet")
            if not isinstance(title, str) or not title:
                continue
            if not isinstance(magnet, str) or not magnet:
                continue

            results.append(
                SearchResult(
                    query=query,
                    title=title,
                    magnet=magnet,
                    source=self.name,
                    url=str(raw.get("url", "")),
                    size=str(raw.get("size", "")),
                    published_at=str(raw.get("published_at", "")),
                )
            )

        return results
```

## Error handling rules

Follow the error boundary used by [`InternetArchiveProvider`](/Users/fujiao.li/source/magnet_search/src/magnet_search/providers/internet_archive.py):

- Raise `ValueError` for invalid local caller input such as a negative `limit`.
- Return `[]` for `limit == 0`.
- Wrap request, status, parsing, and provider-specific failures in `ProviderError`.
- Skip malformed individual items when the overall response is still usable.

That split matters because `SearchService` treats provider exceptions as warnings:

- It adds a `ProviderWarning` entry with the provider name and exception class.
- It keeps results from other providers.
- It raises `AllProvidersFailed` only when nothing usable was collected.

## Wiring the provider into search

`SearchService` itself does not discover providers. It only receives `list[Provider]`:

```python
service = SearchService([InternetArchiveProvider(), ExampleProvider()])
results, warnings = service.search("sample", limit=3)
```

To make a new provider available in the CLI, wire it in where the provider list is assembled. In the current codebase that happens in [`build_search_service()` in `src/magnet_search/cli.py`](/Users/fujiao.li/source/magnet_search/src/magnet_search/cli.py):

- Import the provider class from `src/magnet_search/providers/`.
- Add it to the `providers` list before constructing `SearchService`.
- Keep built-in and configurable providers as separate concerns so `load_config()` stays focused on user-supplied TOML providers.

If the provider needs user configuration, add a config dataclass and loader validation in [`src/magnet_search/config.py`](/Users/fujiao.li/source/magnet_search/src/magnet_search/config.py) instead of hard-coding runtime values inside the provider.

## Testing expectations

Follow the testing style already used in:

- [`tests/test_internet_archive_provider.py`](/Users/fujiao.li/source/magnet_search/tests/test_internet_archive_provider.py)
- [`tests/test_provider_manager.py`](/Users/fujiao.li/source/magnet_search/tests/test_provider_manager.py)

Add provider tests for:

- Successful result mapping.
- `limit == 0` short-circuit behavior.
- Negative `limit` validation.
- Upstream status and parse failures wrapped as `ProviderError`.
- Malformed items being skipped if possible.
- Provider-specific URL, magnet, or metadata normalization.

Use a fake client instead of real HTTP requests. The current tests inject small stub classes with predictable `get()` behavior, which keeps tests fast and deterministic.

If you also change provider wiring, add or update a `SearchService`-level test to prove the new provider integrates cleanly with warning handling.

## Contributor checklist

Before opening a PR for a new provider:

1. Check whether `JsonHttpProvider` would solve the problem with config only.
2. Keep the provider module focused on one upstream source.
3. Ensure `search()` always returns `list[SearchResult]`.
4. Make `name` stable and user-facing.
5. Wrap provider failures in `ProviderError`.
6. Cover success and failure paths with tests.
7. Update `README.md` if the provider changes the supported extension story.
