from __future__ import annotations

from typing import Protocol

from magnet_search.models import SearchResult


class Provider(Protocol):
    name: str

    def search(self, query: str, limit: int) -> list[SearchResult]:
        ...
