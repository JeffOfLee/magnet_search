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


class TrackingProvider:
    name = "tracking"

    def __init__(self):
        self.calls = 0

    def search(self, query: str, limit: int) -> list[SearchResult]:
        self.calls += 1
        return []


class NonListProvider:
    name = "non_list"

    def search(self, query: str, limit: int) -> list[SearchResult]:
        return "not a list"


class NonSearchResultProvider:
    name = "non_search_result"

    def search(self, query: str, limit: int) -> list[SearchResult]:
        return ["not a SearchResult"]


class EmptyMessageFailingProvider:
    name = "empty_message"

    def search(self, query: str, limit: int) -> list[SearchResult]:
        raise RuntimeError()


def test_search_service_rejects_negative_limit_before_calling_provider():
    provider = TrackingProvider()
    service = SearchService([provider])

    try:
        service.search("anything", limit=-1)
    except ValueError as error:
        assert str(error) == "limit must be non-negative"
    else:
        raise AssertionError("expected ValueError")

    assert provider.calls == 0


def test_search_service_raises_when_no_providers_configured():
    service = SearchService([])

    try:
        service.search("anything", limit=3)
    except AllProvidersFailed as error:
        assert "no providers configured" in str(error)
    else:
        raise AssertionError("expected AllProvidersFailed")


def test_search_service_warns_for_malformed_provider_output_and_keeps_working_results():
    service = SearchService([NonListProvider(), NonSearchResultProvider(), WorkingProvider()])

    results, warnings = service.search("working", limit=3)

    assert [result.title for result in results] == ["Working Result"]
    assert [warning.provider for warning in warnings] == ["non_list", "non_search_result"]
    assert "TypeError:" in warnings[0].message
    assert "TypeError:" in warnings[1].message


def test_search_service_warning_messages_include_exception_class_name():
    service = SearchService([FailingProvider(), EmptyMessageFailingProvider(), WorkingProvider()])

    results, warnings = service.search("working", limit=3)

    assert [result.title for result in results] == ["Working Result"]
    assert warnings[0].message == "ProviderError: network timeout"
    assert warnings[1].message == "RuntimeError: no error message"


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
