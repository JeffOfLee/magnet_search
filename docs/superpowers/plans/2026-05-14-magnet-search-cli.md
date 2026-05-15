# Magnet Search CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python CLI named `magnet-search` that searches magnet links by resource name and performs batch CSV searches.

**Architecture:** The CLI is a small Python package with Typer commands, a provider protocol, deterministic ranking, CSV batch orchestration, and provider implementations for Internet Archive plus user-configured JSON HTTP providers. Network behavior is isolated behind injectable HTTP clients so tests use fake responses.

**Tech Stack:** Python 3.11+, Typer, Rich, HTTPX, pytest, standard-library `csv`, `json`, `tomllib`, `dataclasses`, and `difflib`.

---

## Workspace Note

The current workspace at `/Users/fujiao.li/source/magnet_search` is not a Git repository. Commit steps are included because the workflow expects frequent commits; if execution happens before `git init`, report the changed files instead of running the commit command.

## File Structure

- Create `pyproject.toml`: package metadata, dependencies, console script, pytest config.
- Create `README.md`: basic CLI usage and provider safety model.
- Create `src/magnet_search/__init__.py`: package version.
- Create `src/magnet_search/models.py`: `SearchResult`, `ProviderWarning`, provider exceptions.
- Create `src/magnet_search/ranking.py`: score calculation and result truncation.
- Create `src/magnet_search/torrent.py`: bencode parsing for torrent info hash and magnet URI construction.
- Create `src/magnet_search/providers/__init__.py`: provider exports.
- Create `src/magnet_search/providers/base.py`: provider protocol.
- Create `src/magnet_search/providers/manager.py`: provider orchestration and failure isolation.
- Create `src/magnet_search/providers/configurable.py`: user-configured JSON HTTP provider.
- Create `src/magnet_search/providers/internet_archive.py`: legal/public default provider.
- Create `src/magnet_search/config.py`: config file discovery and TOML parsing.
- Create `src/magnet_search/batch.py`: CSV input/output workflow.
- Create `src/magnet_search/cli.py`: Typer CLI commands and rendering.
- Create `tests/test_ranking.py`: ranking behavior.
- Create `tests/test_torrent.py`: torrent hash and magnet behavior.
- Create `tests/test_provider_manager.py`: provider failure isolation.
- Create `tests/test_configurable_provider.py`: TOML and JSON provider mapping.
- Create `tests/test_internet_archive_provider.py`: Internet Archive provider with fake HTTP.
- Create `tests/test_batch.py`: CSV batch behavior.
- Create `tests/test_cli.py`: CLI command behavior.

## Task 1: Project Scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `src/magnet_search/__init__.py`
- Create: `src/magnet_search/providers/__init__.py`

- [ ] **Step 1: Create package metadata**

Create `pyproject.toml` with this content:

```toml
[build-system]
requires = ["setuptools>=69", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "magnet-search"
version = "0.1.0"
description = "CLI tool for searching legal/public magnet resources and user-configured providers"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
  "httpx>=0.27",
  "rich>=13.7",
  "typer>=0.12",
]

[project.optional-dependencies]
test = [
  "pytest>=8.0",
]

[project.scripts]
magnet-search = "magnet_search.cli:app"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 2: Create minimal README**

Create `README.md` with this content:

```markdown
# Magnet Search

`magnet-search` is a Python CLI for finding magnet links from legal/public sources and user-configured JSON HTTP providers.

## Usage

```bash
magnet-search search "resource name" --limit 3
magnet-search search "resource name" --limit 3 --json
magnet-search batch input.csv --column title --output results.csv --limit 3
```

The built-in provider targets public/legal metadata. The tool does not include built-in piracy or gray-market sources. Additional providers can be configured by the user in TOML.
```

- [ ] **Step 3: Create package markers**

Create `src/magnet_search/__init__.py`:

```python
__version__ = "0.1.0"
```

Create `src/magnet_search/providers/__init__.py`:

```python
"""Search provider implementations."""
```

- [ ] **Step 4: Install test dependencies**

Run:

```bash
python3 -m pip install -e ".[test]"
```

Expected: package installs successfully. If network is restricted, request approval to rerun the same command with escalation.

- [ ] **Step 5: Commit scaffold**

```bash
git add pyproject.toml README.md src/magnet_search/__init__.py src/magnet_search/providers/__init__.py
git commit -m "chore: scaffold magnet search package"
```

Expected: commit succeeds in a Git repository. If not in a Git repository, report these four files as changed.

## Task 2: Models and Ranking

**Files:**
- Create: `tests/test_ranking.py`
- Create: `src/magnet_search/models.py`
- Create: `src/magnet_search/ranking.py`

- [ ] **Step 1: Write failing ranking tests**

Create `tests/test_ranking.py`:

```python
from magnet_search.models import SearchResult
from magnet_search.ranking import rank_results


def test_ranking_prefers_close_title_with_magnet():
    weak = SearchResult(
        query="night of the living dead",
        title="Unrelated public domain collection",
        magnet="magnet:?xt=urn:btih:weak",
        source="internet_archive",
    )
    strong = SearchResult(
        query="night of the living dead",
        title="Night of the Living Dead",
        magnet="magnet:?xt=urn:btih:strong",
        source="internet_archive",
        size="1.2 GB",
        published_at="1968-10-01",
        url="https://archive.org/details/night_of_the_living_dead",
    )

    ranked = rank_results("night of the living dead", [weak, strong], limit=2)

    assert [result.title for result in ranked] == [
        "Night of the Living Dead",
        "Unrelated public domain collection",
    ]
    assert ranked[0].score > ranked[1].score


def test_ranking_truncates_to_limit():
    results = [
        SearchResult(query="a", title=f"title {index}", magnet=f"magnet:{index}", source="test")
        for index in range(5)
    ]

    ranked = rank_results("a", results, limit=3)

    assert len(ranked) == 3
```

- [ ] **Step 2: Verify ranking tests fail**

Run:

```bash
pytest tests/test_ranking.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'magnet_search.models'` or missing `rank_results`.

- [ ] **Step 3: Implement models and ranking**

Create `src/magnet_search/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class SearchResult:
    query: str
    title: str
    magnet: str
    source: str
    size: str = ""
    published_at: str = ""
    score: float = 0.0
    url: str = ""

    def with_score(self, score: float) -> "SearchResult":
        return replace(self, score=round(score, 4))


@dataclass(frozen=True)
class ProviderWarning:
    provider: str
    message: str


class ProviderError(Exception):
    """Raised when one provider cannot complete a search."""


class AllProvidersFailed(RuntimeError):
    """Raised when every configured provider fails."""
```

Create `src/magnet_search/ranking.py`:

```python
from __future__ import annotations

from difflib import SequenceMatcher

from magnet_search.models import SearchResult


PROVIDER_BOOSTS = {
    "internet_archive": 0.1,
}


def _text_similarity(query: str, title: str) -> float:
    query_text = query.casefold().strip()
    title_text = title.casefold().strip()
    if not query_text or not title_text:
        return 0.0
    return SequenceMatcher(None, query_text, title_text).ratio()


def score_result(query: str, result: SearchResult) -> float:
    score = _text_similarity(query, result.title)
    if result.magnet:
        score += 0.25
    if result.url:
        score += 0.03
    if result.size:
        score += 0.03
    if result.published_at:
        score += 0.03
    score += PROVIDER_BOOSTS.get(result.source, 0.0)
    return score


def rank_results(query: str, results: list[SearchResult], limit: int) -> list[SearchResult]:
    scored = [result.with_score(score_result(query, result)) for result in results]
    scored.sort(key=lambda result: (result.score, result.title.casefold()), reverse=True)
    return scored[:limit]
```

- [ ] **Step 4: Verify ranking tests pass**

Run:

```bash
pytest tests/test_ranking.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit ranking**

```bash
git add tests/test_ranking.py src/magnet_search/models.py src/magnet_search/ranking.py
git commit -m "feat: add result model and ranking"
```

Expected: commit succeeds in a Git repository. If not in a Git repository, report these files as changed.

## Task 3: Torrent Info Hash and Magnet Builder

**Files:**
- Create: `tests/test_torrent.py`
- Create: `src/magnet_search/torrent.py`

- [ ] **Step 1: Write failing torrent tests**

Create `tests/test_torrent.py`:

```python
import hashlib

from magnet_search.torrent import build_magnet, extract_info_hash, extract_trackers


def test_extract_info_hash_hashes_raw_info_dictionary_bytes():
    info = b"d6:lengthi12345e4:name10:sample.txt12:piece lengthi16384e6:pieces0:e"
    torrent = b"d8:announce14:http://tracker4:info" + info + b"e"

    assert extract_info_hash(torrent) == hashlib.sha1(info).hexdigest()


def test_extract_trackers_reads_announce_field():
    torrent = b"d8:announce14:http://tracker4:infod4:name10:sample.txtee"

    assert extract_trackers(torrent) == ["http://tracker"]


def test_build_magnet_includes_hash_display_name_and_trackers():
    uri = build_magnet(
        info_hash="abc123",
        display_name="Sample File",
        trackers=["http://tracker"],
    )

    assert uri.startswith("magnet:?xt=urn:btih:abc123")
    assert "dn=Sample+File" in uri
    assert "tr=http%3A%2F%2Ftracker" in uri
```

- [ ] **Step 2: Verify torrent tests fail**

Run:

```bash
pytest tests/test_torrent.py -v
```

Expected: FAIL with missing `magnet_search.torrent`.

- [ ] **Step 3: Implement torrent helpers**

Create `src/magnet_search/torrent.py`:

```python
from __future__ import annotations

from hashlib import sha1
from typing import Any
from urllib.parse import urlencode


class BencodeError(ValueError):
    pass


def _parse_value(data: bytes, index: int) -> tuple[Any, int]:
    marker = data[index:index + 1]
    if marker == b"i":
        end = data.index(b"e", index)
        return int(data[index + 1:end]), end + 1
    if marker == b"l":
        values = []
        index += 1
        while data[index:index + 1] != b"e":
            value, index = _parse_value(data, index)
            values.append(value)
        return values, index + 1
    if marker == b"d":
        values = {}
        index += 1
        while data[index:index + 1] != b"e":
            key, index = _parse_value(data, index)
            value, index = _parse_value(data, index)
            values[key] = value
        return values, index + 1
    if marker.isdigit():
        colon = data.index(b":", index)
        length = int(data[index:colon])
        start = colon + 1
        end = start + length
        return data[start:end], end
    raise BencodeError(f"invalid bencode marker at byte {index}")


def _value_span(data: bytes, index: int) -> tuple[int, int]:
    start = index
    marker = data[index:index + 1]
    if marker == b"i":
        return start, data.index(b"e", index) + 1
    if marker in {b"l", b"d"}:
        index += 1
        while data[index:index + 1] != b"e":
            _, index = _value_span(data, index)
        return start, index + 1
    if marker.isdigit():
        colon = data.index(b":", index)
        length = int(data[index:colon])
        return start, colon + 1 + length
    raise BencodeError(f"invalid bencode marker at byte {index}")


def extract_info_hash(torrent_bytes: bytes) -> str:
    if torrent_bytes[:1] != b"d":
        raise BencodeError("torrent root must be a dictionary")
    index = 1
    while torrent_bytes[index:index + 1] != b"e":
        key, index = _parse_value(torrent_bytes, index)
        value_start, value_end = _value_span(torrent_bytes, index)
        if key == b"info":
            return sha1(torrent_bytes[value_start:value_end]).hexdigest()
        index = value_end
    raise BencodeError("torrent does not contain an info dictionary")


def extract_trackers(torrent_bytes: bytes) -> list[str]:
    root, _ = _parse_value(torrent_bytes, 0)
    trackers: list[str] = []
    announce = root.get(b"announce") if isinstance(root, dict) else None
    if isinstance(announce, bytes):
        trackers.append(announce.decode("utf-8", errors="replace"))
    announce_list = root.get(b"announce-list") if isinstance(root, dict) else None
    if isinstance(announce_list, list):
        for group in announce_list:
            if isinstance(group, list):
                for value in group:
                    if isinstance(value, bytes):
                        trackers.append(value.decode("utf-8", errors="replace"))
    return list(dict.fromkeys(trackers))


def build_magnet(info_hash: str, display_name: str = "", trackers: list[str] | None = None) -> str:
    params: list[tuple[str, str]] = [("xt", f"urn:btih:{info_hash}")]
    if display_name:
        params.append(("dn", display_name))
    for tracker in trackers or []:
        params.append(("tr", tracker))
    return "magnet:?" + urlencode(params)
```

- [ ] **Step 4: Verify torrent tests pass**

Run:

```bash
pytest tests/test_torrent.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit torrent helpers**

```bash
git add tests/test_torrent.py src/magnet_search/torrent.py
git commit -m "feat: build magnet links from torrent metadata"
```

Expected: commit succeeds in a Git repository. If not in a Git repository, report these files as changed.

## Task 4: Provider Protocol and Search Service

**Files:**
- Create: `tests/test_provider_manager.py`
- Create: `src/magnet_search/providers/base.py`
- Create: `src/magnet_search/providers/manager.py`

- [ ] **Step 1: Write failing provider manager tests**

Create `tests/test_provider_manager.py`:

```python
from magnet_search.models import AllProvidersFailed, ProviderError, SearchResult
from magnet_search.providers.manager import SearchService


class WorkingProvider:
    name = "working"

    def search(self, query: str, limit: int) -> list[SearchResult]:
        return [
            SearchResult(
                query=query,
                title="Working Result",
                magnet="magnet:?xt=urn:btih:working",
                source=self.name,
            )
        ]


class FailingProvider:
    name = "failing"

    def search(self, query: str, limit: int) -> list[SearchResult]:
        raise ProviderError("network timeout")


def test_search_service_keeps_results_when_one_provider_fails():
    service = SearchService([FailingProvider(), WorkingProvider()])

    results, warnings = service.search("working", limit=3)

    assert [result.title for result in results] == ["Working Result"]
    assert warnings[0].provider == "failing"
    assert "network timeout" in warnings[0].message


def test_search_service_raises_when_all_providers_fail():
    service = SearchService([FailingProvider()])

    try:
        service.search("anything", limit=3)
    except AllProvidersFailed as error:
        assert "all providers failed" in str(error)
    else:
        raise AssertionError("expected AllProvidersFailed")
```

- [ ] **Step 2: Verify provider manager tests fail**

Run:

```bash
pytest tests/test_provider_manager.py -v
```

Expected: FAIL with missing `magnet_search.providers.manager`.

- [ ] **Step 3: Implement provider protocol and search service**

Create `src/magnet_search/providers/base.py`:

```python
from __future__ import annotations

from typing import Protocol

from magnet_search.models import SearchResult


class Provider(Protocol):
    name: str

    def search(self, query: str, limit: int) -> list[SearchResult]:
        ...
```

Create `src/magnet_search/providers/manager.py`:

```python
from __future__ import annotations

from magnet_search.models import AllProvidersFailed, ProviderWarning, SearchResult
from magnet_search.providers.base import Provider
from magnet_search.ranking import rank_results


class SearchService:
    def __init__(self, providers: list[Provider]):
        self.providers = providers

    def search(self, query: str, limit: int) -> tuple[list[SearchResult], list[ProviderWarning]]:
        collected: list[SearchResult] = []
        warnings: list[ProviderWarning] = []
        for provider in self.providers:
            try:
                collected.extend(provider.search(query, limit))
            except Exception as error:
                warnings.append(ProviderWarning(provider=provider.name, message=str(error)))

        if not collected and warnings:
            raise AllProvidersFailed("all providers failed")

        return rank_results(query, collected, limit), warnings
```

- [ ] **Step 4: Verify provider manager tests pass**

Run:

```bash
pytest tests/test_provider_manager.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit provider service**

```bash
git add tests/test_provider_manager.py src/magnet_search/providers/base.py src/magnet_search/providers/manager.py
git commit -m "feat: isolate provider failures during search"
```

Expected: commit succeeds in a Git repository. If not in a Git repository, report these files as changed.

## Task 5: Config Loader and Configurable HTTP Provider

**Files:**
- Create: `tests/test_configurable_provider.py`
- Create: `src/magnet_search/config.py`
- Create: `src/magnet_search/providers/configurable.py`

- [ ] **Step 1: Write failing configurable provider tests**

Create `tests/test_configurable_provider.py`:

```python
from pathlib import Path

from magnet_search.config import load_config
from magnet_search.providers.configurable import JsonHttpProvider


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self):
        self.requested_url = ""

    def get(self, url, timeout):
        self.requested_url = url
        return FakeResponse(
            {
                "results": [
                    {
                        "title": "Example Result",
                        "magnet": "magnet:?xt=urn:btih:example",
                        "url": "https://example.invalid/item",
                        "size": "700 MB",
                        "published_at": "2020-01-01",
                    }
                ]
            }
        )


def test_load_config_reads_http_provider(tmp_path: Path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
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
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.http_providers[0].name == "example"
    assert config.http_providers[0].enabled is True


def test_json_http_provider_maps_configured_json_fields():
    config = load_config(
        data={
            "providers": {
                "http": [
                    {
                        "name": "example",
                        "enabled": True,
                        "search_url": "https://example.invalid/search?q={query}",
                        "result_path": "results",
                        "title_path": "title",
                        "magnet_path": "magnet",
                        "url_path": "url",
                        "size_path": "size",
                        "published_at_path": "published_at",
                    }
                ]
            }
        }
    )
    client = FakeClient()
    provider = JsonHttpProvider(config.http_providers[0], client=client)

    results = provider.search("example movie", limit=3)

    assert client.requested_url == "https://example.invalid/search?q=example+movie"
    assert results[0].query == "example movie"
    assert results[0].title == "Example Result"
    assert results[0].magnet == "magnet:?xt=urn:btih:example"
    assert results[0].source == "example"
```

- [ ] **Step 2: Verify configurable provider tests fail**

Run:

```bash
pytest tests/test_configurable_provider.py -v
```

Expected: FAIL with missing `magnet_search.config` or missing `JsonHttpProvider`.

- [ ] **Step 3: Implement config loader and JSON HTTP provider**

Create `src/magnet_search/config.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib


@dataclass(frozen=True)
class HttpProviderConfig:
    name: str
    enabled: bool
    search_url: str
    result_path: str
    title_path: str
    magnet_path: str
    url_path: str = ""
    size_path: str = ""
    published_at_path: str = ""


@dataclass(frozen=True)
class AppConfig:
    http_providers: list[HttpProviderConfig]


def default_config_path() -> Path:
    return Path.home() / ".config" / "magnet-search" / "config.toml"


def _load_raw(path: Path | None, data: dict[str, Any] | None) -> dict[str, Any]:
    if data is not None:
        return data
    if path is None:
        path = default_config_path()
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def load_config(path: Path | None = None, data: dict[str, Any] | None = None) -> AppConfig:
    raw = _load_raw(path, data)
    http_entries = raw.get("providers", {}).get("http", [])
    providers = [
        HttpProviderConfig(
            name=str(entry["name"]),
            enabled=bool(entry.get("enabled", True)),
            search_url=str(entry["search_url"]),
            result_path=str(entry["result_path"]),
            title_path=str(entry["title_path"]),
            magnet_path=str(entry["magnet_path"]),
            url_path=str(entry.get("url_path", "")),
            size_path=str(entry.get("size_path", "")),
            published_at_path=str(entry.get("published_at_path", "")),
        )
        for entry in http_entries
    ]
    return AppConfig(http_providers=providers)
```

Create `src/magnet_search/providers/configurable.py`:

```python
from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

import httpx

from magnet_search.config import HttpProviderConfig
from magnet_search.models import ProviderError, SearchResult


def _path_value(payload: Any, path: str) -> Any:
    value = payload
    if not path:
        return ""
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part, "")
        else:
            return ""
    return value


class JsonHttpProvider:
    def __init__(self, config: HttpProviderConfig, client: Any | None = None, timeout: float = 15.0):
        self.config = config
        self.name = config.name
        self.client = client or httpx.Client()
        self.timeout = timeout

    def search(self, query: str, limit: int) -> list[SearchResult]:
        if not self.config.enabled:
            return []
        url = self.config.search_url.format(query=quote_plus(query))
        try:
            response = self.client.get(url, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except Exception as error:
            raise ProviderError(str(error)) from error

        raw_results = _path_value(payload, self.config.result_path)
        if not isinstance(raw_results, list):
            raise ProviderError(f"{self.name} result_path did not resolve to a list")

        results: list[SearchResult] = []
        for raw in raw_results[:limit]:
            results.append(
                SearchResult(
                    query=query,
                    title=str(_path_value(raw, self.config.title_path)),
                    magnet=str(_path_value(raw, self.config.magnet_path)),
                    source=self.name,
                    url=str(_path_value(raw, self.config.url_path)),
                    size=str(_path_value(raw, self.config.size_path)),
                    published_at=str(_path_value(raw, self.config.published_at_path)),
                )
            )
        return results
```

- [ ] **Step 4: Verify configurable provider tests pass**

Run:

```bash
pytest tests/test_configurable_provider.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit configurable provider**

```bash
git add tests/test_configurable_provider.py src/magnet_search/config.py src/magnet_search/providers/configurable.py
git commit -m "feat: load configurable http providers"
```

Expected: commit succeeds in a Git repository. If not in a Git repository, report these files as changed.

## Task 6: Internet Archive Provider

**Files:**
- Create: `tests/test_internet_archive_provider.py`
- Create: `src/magnet_search/providers/internet_archive.py`

- [ ] **Step 1: Write failing Internet Archive provider test**

Create `tests/test_internet_archive_provider.py`:

```python
from magnet_search.providers.internet_archive import InternetArchiveProvider


INFO = b"d6:lengthi12345e4:name10:sample.txt12:piece lengthi16384e6:pieces0:e"
TORRENT = b"d8:announce14:http://tracker4:info" + INFO + b"e"


class FakeResponse:
    def __init__(self, payload=None, content=b""):
        self.payload = payload
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=15.0):
        self.calls.append((url, params, timeout))
        if "advancedsearch.php" in url:
            return FakeResponse(
                payload={
                    "response": {
                        "docs": [
                            {
                                "identifier": "sample_item",
                                "title": "Sample Public Movie",
                                "date": "1968-10-01",
                                "item_size": 734003200,
                            }
                        ]
                    }
                }
            )
        return FakeResponse(content=TORRENT)


def test_internet_archive_provider_builds_magnet_from_torrent_metadata():
    client = FakeClient()
    provider = InternetArchiveProvider(client=client)

    results = provider.search("sample public movie", limit=1)

    assert results[0].query == "sample public movie"
    assert results[0].title == "Sample Public Movie"
    assert results[0].source == "internet_archive"
    assert results[0].url == "https://archive.org/details/sample_item"
    assert results[0].size == "700.0 MB"
    assert results[0].magnet.startswith("magnet:?xt=urn:btih:")
    assert "dn=Sample+Public+Movie" in results[0].magnet
```

- [ ] **Step 2: Verify Internet Archive provider test fails**

Run:

```bash
pytest tests/test_internet_archive_provider.py -v
```

Expected: FAIL with missing `magnet_search.providers.internet_archive`.

- [ ] **Step 3: Implement Internet Archive provider**

Create `src/magnet_search/providers/internet_archive.py`:

```python
from __future__ import annotations

from typing import Any

import httpx

from magnet_search.models import ProviderError, SearchResult
from magnet_search.torrent import build_magnet, extract_info_hash, extract_trackers


class InternetArchiveProvider:
    name = "internet_archive"
    search_url = "https://archive.org/advancedsearch.php"

    def __init__(self, client: Any | None = None, timeout: float = 15.0):
        self.client = client or httpx.Client()
        self.timeout = timeout

    def search(self, query: str, limit: int) -> list[SearchResult]:
        try:
            response = self.client.get(
                self.search_url,
                params={
                    "q": query,
                    "fl[]": ["identifier", "title", "date", "item_size"],
                    "rows": max(limit * 3, limit),
                    "output": "json",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            docs = response.json().get("response", {}).get("docs", [])
        except Exception as error:
            raise ProviderError(str(error)) from error

        results: list[SearchResult] = []
        for doc in docs:
            if len(results) >= limit:
                break
            identifier = str(doc.get("identifier", ""))
            title = str(doc.get("title") or identifier)
            if not identifier:
                continue
            magnet = self._magnet_for_item(identifier, title)
            results.append(
                SearchResult(
                    query=query,
                    title=title,
                    magnet=magnet,
                    source=self.name,
                    size=_human_size(doc.get("item_size")),
                    published_at=str(doc.get("date", "")),
                    url=f"https://archive.org/details/{identifier}",
                )
            )
        return results

    def _magnet_for_item(self, identifier: str, title: str) -> str:
        torrent_url = f"https://archive.org/download/{identifier}/{identifier}_archive.torrent"
        try:
            response = self.client.get(torrent_url, timeout=self.timeout)
            response.raise_for_status()
            torrent_bytes = response.content
            return build_magnet(
                info_hash=extract_info_hash(torrent_bytes),
                display_name=title,
                trackers=extract_trackers(torrent_bytes),
            )
        except Exception:
            return ""


def _human_size(value: Any) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"
    return f"{size:.1f} {units[unit_index]}"
```

- [ ] **Step 4: Verify Internet Archive provider test passes**

Run:

```bash
pytest tests/test_internet_archive_provider.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit Internet Archive provider**

```bash
git add tests/test_internet_archive_provider.py src/magnet_search/providers/internet_archive.py
git commit -m "feat: add internet archive provider"
```

Expected: commit succeeds in a Git repository. If not in a Git repository, report these files as changed.

## Task 7: Batch CSV Workflow

**Files:**
- Create: `tests/test_batch.py`
- Create: `src/magnet_search/batch.py`

- [ ] **Step 1: Write failing batch tests**

Create `tests/test_batch.py`:

```python
import csv
from pathlib import Path

from magnet_search.batch import BatchError, run_batch
from magnet_search.models import SearchResult


def test_run_batch_writes_one_row_per_result(tmp_path: Path):
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"
    input_path.write_text("title\nSample Movie\n", encoding="utf-8")

    def fake_search(query: str, limit: int):
        return [
            SearchResult(
                query=query,
                title="Sample Movie Result",
                magnet="magnet:?xt=urn:btih:sample",
                source="test",
                score=1.23,
            )
        ]

    run_batch(input_path, column="title", output_path=output_path, limit=3, search_func=fake_search)

    rows = list(csv.DictReader(output_path.open(encoding="utf-8")))
    assert rows[0]["query"] == "Sample Movie"
    assert rows[0]["title"] == "Sample Movie Result"
    assert rows[0]["magnet"] == "magnet:?xt=urn:btih:sample"


def test_run_batch_writes_empty_result_row_when_no_results(tmp_path: Path):
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"
    input_path.write_text("title\nMissing Movie\n", encoding="utf-8")

    run_batch(input_path, column="title", output_path=output_path, limit=3, search_func=lambda query, limit: [])

    rows = list(csv.DictReader(output_path.open(encoding="utf-8")))
    assert rows == [
        {
            "query": "Missing Movie",
            "title": "",
            "magnet": "",
            "source": "",
            "size": "",
            "published_at": "",
            "score": "",
            "url": "",
        }
    ]


def test_run_batch_fails_when_column_is_missing(tmp_path: Path):
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"
    input_path.write_text("name\nSample Movie\n", encoding="utf-8")

    try:
        run_batch(input_path, column="title", output_path=output_path, limit=3, search_func=lambda query, limit: [])
    except BatchError as error:
        assert "missing column: title" in str(error)
    else:
        raise AssertionError("expected BatchError")
```

- [ ] **Step 2: Verify batch tests fail**

Run:

```bash
pytest tests/test_batch.py -v
```

Expected: FAIL with missing `magnet_search.batch`.

- [ ] **Step 3: Implement batch workflow**

Create `src/magnet_search/batch.py`:

```python
from __future__ import annotations

import csv
from pathlib import Path
from typing import Callable

from magnet_search.models import SearchResult


OUTPUT_FIELDS = ["query", "title", "magnet", "source", "size", "published_at", "score", "url"]


class BatchError(ValueError):
    pass


def _row_from_result(result: SearchResult) -> dict[str, str]:
    return {
        "query": result.query,
        "title": result.title,
        "magnet": result.magnet,
        "source": result.source,
        "size": result.size,
        "published_at": result.published_at,
        "score": "" if result.score == 0 else str(result.score),
        "url": result.url,
    }


def _empty_row(query: str) -> dict[str, str]:
    return {field: "" for field in OUTPUT_FIELDS} | {"query": query}


def run_batch(
    input_path: Path,
    column: str,
    output_path: Path,
    limit: int,
    search_func: Callable[[str, int], list[SearchResult]],
) -> None:
    with input_path.open(newline="", encoding="utf-8") as input_file:
        reader = csv.DictReader(input_file)
        if reader.fieldnames is None or column not in reader.fieldnames:
            raise BatchError(f"missing column: {column}")
        with output_path.open("w", newline="", encoding="utf-8") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            for row in reader:
                query = row.get(column, "")
                results = search_func(query, limit) if query else []
                if not results:
                    writer.writerow(_empty_row(query))
                    continue
                for result in results:
                    writer.writerow(_row_from_result(result))
```

- [ ] **Step 4: Verify batch tests pass**

Run:

```bash
pytest tests/test_batch.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit batch workflow**

```bash
git add tests/test_batch.py src/magnet_search/batch.py
git commit -m "feat: add csv batch search workflow"
```

Expected: commit succeeds in a Git repository. If not in a Git repository, report these files as changed.

## Task 8: CLI Commands

**Files:**
- Create: `tests/test_cli.py`
- Create: `src/magnet_search/cli.py`

- [ ] **Step 1: Write failing CLI tests**

Create `tests/test_cli.py`:

```python
import csv
import json

from typer.testing import CliRunner

from magnet_search import cli
from magnet_search.models import SearchResult


runner = CliRunner()


class FakeService:
    def search(self, query: str, limit: int):
        return (
            [
                SearchResult(
                    query=query,
                    title="Sample Result",
                    magnet="magnet:?xt=urn:btih:sample",
                    source="test",
                    score=1.25,
                    url="https://example.invalid/sample",
                )
            ],
            [],
        )


def test_search_command_renders_table(monkeypatch):
    monkeypatch.setattr(cli, "build_search_service", lambda: FakeService())

    result = runner.invoke(cli.app, ["search", "sample movie", "--limit", "3"])

    assert result.exit_code == 0
    assert "Sample Result" in result.stdout
    assert "magnet:?xt=urn:btih:sample" in result.stdout


def test_search_command_renders_json(monkeypatch):
    monkeypatch.setattr(cli, "build_search_service", lambda: FakeService())

    result = runner.invoke(cli.app, ["search", "sample movie", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["query"] == "sample movie"
    assert payload[0]["title"] == "Sample Result"


def test_batch_command_writes_output_csv(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "build_search_service", lambda: FakeService())
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"
    input_path.write_text("title\nsample movie\n", encoding="utf-8")

    result = runner.invoke(
        cli.app,
        ["batch", str(input_path), "--column", "title", "--output", str(output_path)],
    )

    assert result.exit_code == 0
    rows = list(csv.DictReader(output_path.open(encoding="utf-8")))
    assert rows[0]["title"] == "Sample Result"
```

- [ ] **Step 2: Verify CLI tests fail**

Run:

```bash
pytest tests/test_cli.py -v
```

Expected: FAIL with missing `magnet_search.cli`.

- [ ] **Step 3: Implement CLI**

Create `src/magnet_search/cli.py`:

```python
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from magnet_search.batch import BatchError, run_batch
from magnet_search.config import load_config
from magnet_search.models import AllProvidersFailed, SearchResult
from magnet_search.providers.configurable import JsonHttpProvider
from magnet_search.providers.internet_archive import InternetArchiveProvider
from magnet_search.providers.manager import SearchService


app = typer.Typer(help="Search legal/public and user-configured magnet resources.")
console = Console()
error_console = Console(stderr=True)


def build_search_service() -> SearchService:
    config = load_config()
    providers = [InternetArchiveProvider()]
    providers.extend(JsonHttpProvider(provider_config) for provider_config in config.http_providers if provider_config.enabled)
    return SearchService(providers)


def _print_warnings(warnings) -> None:
    for warning in warnings:
        error_console.print(f"[yellow]warning[/yellow] provider {warning.provider}: {warning.message}")


def _render_table(results: list[SearchResult]) -> None:
    table = Table("Title", "Magnet", "Source", "Size", "Date", "Score", "URL")
    for result in results:
        table.add_row(
            result.title,
            result.magnet,
            result.source,
            result.size,
            result.published_at,
            str(result.score),
            result.url,
        )
    console.print(table)


@app.command()
def search(
    query: str,
    limit: int = typer.Option(3, min=1, help="Maximum number of results."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON instead of a table."),
) -> None:
    service = build_search_service()
    try:
        results, warnings = service.search(query, limit)
    except AllProvidersFailed as error:
        error_console.print(f"[red]error[/red] {error}")
        raise typer.Exit(1) from error

    _print_warnings(warnings)
    if json_output:
        typer.echo(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2))
    else:
        _render_table(results)


@app.command()
def batch(
    input_csv: Path,
    column: str = typer.Option(..., help="CSV column containing resource names."),
    output: Path = typer.Option(..., "--output", "-o", help="Output CSV path."),
    limit: int = typer.Option(3, min=1, help="Maximum results per resource."),
) -> None:
    service = build_search_service()

    def search_func(query: str, per_query_limit: int) -> list[SearchResult]:
        try:
            results, warnings = service.search(query, per_query_limit)
        except AllProvidersFailed:
            return []
        _print_warnings(warnings)
        return results

    try:
        run_batch(input_csv, column=column, output_path=output, limit=limit, search_func=search_func)
    except BatchError as error:
        error_console.print(f"[red]error[/red] {error}")
        raise typer.Exit(1) from error
    except OSError as error:
        error_console.print(f"[red]error[/red] {error}")
        raise typer.Exit(1) from error

    typer.echo(f"wrote {output}")


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Verify CLI tests pass**

Run:

```bash
pytest tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit CLI**

```bash
git add tests/test_cli.py src/magnet_search/cli.py
git commit -m "feat: add magnet search cli commands"
```

Expected: commit succeeds in a Git repository. If not in a Git repository, report these files as changed.

## Task 9: Full Verification and Documentation Pass

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Expand README usage**

Update `README.md` to include:

```markdown
## Custom HTTP Providers

Create `~/.config/magnet-search/config.toml`:

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

The provider response must be JSON. Dot paths such as `data.results` are supported for nested objects.

## Output Columns

Batch output writes:

```text
query,title,magnet,source,size,published_at,score,url
```
```

- [ ] **Step 2: Run the complete test suite**

Run:

```bash
pytest -v
```

Expected: PASS for all tests.

- [ ] **Step 3: Run CLI help**

Run:

```bash
python3 -m magnet_search.cli --help
```

Expected: output includes `search` and `batch`.

- [ ] **Step 4: Run installed CLI help**

Run:

```bash
magnet-search --help
```

Expected: output includes `search` and `batch`.

- [ ] **Step 5: Commit docs and verification pass**

```bash
git add README.md
git commit -m "docs: document magnet search usage"
```

Expected: commit succeeds in a Git repository. If not in a Git repository, report `README.md` as changed.

## Self-Review

- Spec coverage: single search is covered in Task 8; batch CSV search is covered in Task 7 and Task 8; default legal/public provider is covered in Task 6; configurable providers are covered in Task 5; ranking is covered in Task 2; error handling is covered in Tasks 4, 7, and 8.
- Placeholder scan: the plan uses concrete files, commands, test code, and implementation code. It contains no deferred implementation sections.
- Type consistency: `SearchResult`, `ProviderWarning`, `ProviderError`, `AllProvidersFailed`, `SearchService.search`, `JsonHttpProvider.search`, and `run_batch` signatures are consistent across tasks.
