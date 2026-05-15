from __future__ import annotations

import math
from typing import Any
from urllib.parse import quote

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
        if limit < 0:
            raise ValueError("limit must be non-negative")
        if limit == 0:
            return []

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
            payload = response.json()
            docs = self._docs_from_payload(payload)
        except Exception as error:
            raise ProviderError(f"{self.name} search failed: {error}") from error

        results: list[SearchResult] = []
        for doc in docs:
            if len(results) >= limit:
                break
            if not isinstance(doc, dict):
                continue
            identifier_value = doc.get("identifier")
            if not isinstance(identifier_value, str):
                continue
            identifier = identifier_value.strip()
            escaped_identifier = quote(identifier, safe="")
            title = str(doc.get("title") or identifier)
            if not identifier:
                continue
            magnet = self._magnet_for_item(identifier, title)
            if not magnet:
                continue
            results.append(
                SearchResult(
                    query=query,
                    title=title,
                    magnet=magnet,
                    source=self.name,
                    size=_human_size(doc.get("item_size")),
                    published_at=str(doc.get("date", "")),
                    url=f"https://archive.org/details/{escaped_identifier}",
                )
            )
        return results

    def _docs_from_payload(self, payload: Any) -> list[Any]:
        if not isinstance(payload, dict):
            raise ValueError("malformed search response: payload must be a dict")
        response = payload.get("response")
        if not isinstance(response, dict):
            raise ValueError("malformed search response: response must be a dict")
        docs = response.get("docs")
        if not isinstance(docs, list):
            raise ValueError("malformed search response: docs must be a list")
        return docs

    def _magnet_for_item(self, identifier: str, title: str) -> str:
        escaped_identifier = quote(identifier, safe="")
        escaped_torrent_name = quote(f"{identifier}_archive.torrent", safe="")
        torrent_url = f"https://archive.org/download/{escaped_identifier}/{escaped_torrent_name}"
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
    if not math.isfinite(size) or size < 0:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"
    return f"{size:.1f} {units[unit_index]}"
