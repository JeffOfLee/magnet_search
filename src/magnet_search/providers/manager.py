from __future__ import annotations

from magnet_search.models import AllProvidersFailed, ProviderWarning, SearchResult
from magnet_search.providers.base import Provider
from magnet_search.ranking import rank_results


def _warning_message(error: Exception) -> str:
    message = str(error) or "no error message"
    return f"{error.__class__.__name__}: {message}"


class SearchService:
    def __init__(self, providers: list[Provider]):
        self.providers = providers

    def search(self, query: str, limit: int) -> tuple[list[SearchResult], list[ProviderWarning]]:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        if not self.providers:
            raise AllProvidersFailed("no providers configured")

        collected: list[SearchResult] = []
        warnings: list[ProviderWarning] = []
        for provider in self.providers:
            try:
                results = provider.search(query, limit)
                if not isinstance(results, list):
                    raise TypeError("provider search returned non-list result")
                if not all(isinstance(result, SearchResult) for result in results):
                    raise TypeError("provider search returned non-SearchResult item")
                collected.extend(results)
            except Exception as error:
                warnings.append(ProviderWarning(provider=provider.name, message=_warning_message(error)))

        if not collected and warnings:
            raise AllProvidersFailed("all providers failed")

        return rank_results(query, collected, limit), warnings
