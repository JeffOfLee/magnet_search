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


def _required_result_string(provider_name: str, index: int, field: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ProviderError(f"{provider_name} result {index} {field} must be a non-empty string")
    return value


class JsonHttpProvider:
    def __init__(self, config: HttpProviderConfig, client: Any | None = None, timeout: float = 15.0):
        self.config = config
        self.name = config.name
        self.client = client or httpx.Client()
        self.timeout = timeout

    def search(self, query: str, limit: int) -> list[SearchResult]:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        if limit == 0:
            return []
        if not self.config.enabled:
            return []
        if "{query}" not in self.config.search_url:
            raise ProviderError(f"{self.name} search_url must contain {{query}}")
        url = self.config.search_url.replace("{query}", quote_plus(query))
        try:
            response = self.client.get(url, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except Exception as error:
            raise ProviderError(f"{self.name} search failed: {error}") from error

        raw_results = _path_value(payload, self.config.result_path)
        if not isinstance(raw_results, list):
            raise ProviderError(f"{self.name} result_path did not resolve to a list")

        results: list[SearchResult] = []
        for index, raw in enumerate(raw_results[:limit]):
            if not isinstance(raw, dict):
                raise ProviderError(f"{self.name} result {index} must be a table")
            title = _required_result_string(self.name, index, "title", _path_value(raw, self.config.title_path))
            magnet = _required_result_string(self.name, index, "magnet", _path_value(raw, self.config.magnet_path))
            results.append(
                SearchResult(
                    query=query,
                    title=title,
                    magnet=magnet,
                    source=self.name,
                    url=str(_path_value(raw, self.config.url_path)),
                    size=str(_path_value(raw, self.config.size_path)),
                    published_at=str(_path_value(raw, self.config.published_at_path)),
                )
            )
        return results
