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
