import math

import pytest

from magnet_search.models import ProviderError
from magnet_search.providers.internet_archive import InternetArchiveProvider, _human_size


INFO = b"d6:lengthi12345e4:name10:sample.txt12:piece lengthi16384e6:pieces0:e"
TORRENT = b"d8:announce14:http://tracker4:info" + INFO + b"e"


class FakeResponse:
    def __init__(self, payload=None, content=b"", json_error=None, status_error=None):
        self.payload = payload
        self.content = content
        self.json_error = json_error
        self.status_error = status_error

    def raise_for_status(self):
        if self.status_error:
            raise self.status_error
        return None

    def json(self):
        if self.json_error:
            raise self.json_error
        return self.payload


class FakeClient:
    def __init__(self, search_response=None, torrent_response=None):
        self.calls = []
        self.search_response = search_response
        self.torrent_response = torrent_response

    def get(self, url, params=None, timeout=15.0):
        self.calls.append((url, params, timeout))
        if "advancedsearch.php" in url:
            return self.search_response or FakeResponse(
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
        return self.torrent_response or FakeResponse(content=TORRENT)


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
    assert "dn=Sample%20Public%20Movie" in results[0].magnet


def test_limit_zero_returns_empty_without_http_calls():
    client = FakeClient()
    provider = InternetArchiveProvider(client=client)

    assert provider.search("sample public movie", limit=0) == []
    assert client.calls == []


def test_negative_limit_raises_without_http_calls():
    client = FakeClient()
    provider = InternetArchiveProvider(client=client)

    with pytest.raises(ValueError, match="limit must be non-negative"):
        provider.search("sample public movie", limit=-1)

    assert client.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"response": []},
        {"response": {"docs": {}}},
    ],
)
def test_malformed_search_payload_raises_provider_error_with_context(payload):
    client = FakeClient(search_response=FakeResponse(payload=payload))
    provider = InternetArchiveProvider(client=client)

    with pytest.raises(ProviderError) as error:
        provider.search("sample public movie", limit=1)

    message = str(error.value)
    assert "internet_archive search failed" in message
    assert "malformed" in message


def test_malformed_docs_and_invalid_identifiers_are_skipped():
    client = FakeClient(
        search_response=FakeResponse(
            payload={
                "response": {
                    "docs": [
                        [],
                        {"identifier": ""},
                        {"identifier": "   "},
                        {"identifier": None},
                        {
                            "identifier": " valid_item ",
                            "title": "Valid Item",
                            "date": "1970-01-01",
                            "item_size": 1024,
                        },
                    ]
                }
            }
        )
    )
    provider = InternetArchiveProvider(client=client)

    results = provider.search("sample public movie", limit=3)

    assert len(results) == 1
    assert results[0].title == "Valid Item"
    assert results[0].url == "https://archive.org/details/valid_item"
    assert client.calls[1][0] == "https://archive.org/download/valid_item/valid_item_archive.torrent"


def test_identifier_is_escaped_in_details_and_torrent_urls():
    identifier = "space #?%/segment"
    client = FakeClient(
        search_response=FakeResponse(
            payload={
                "response": {
                    "docs": [
                        {
                            "identifier": identifier,
                            "title": "Escaped Item",
                        }
                    ]
                }
            }
        )
    )
    provider = InternetArchiveProvider(client=client)

    results = provider.search("sample public movie", limit=1)

    escaped = "space%20%23%3F%25%2Fsegment"
    assert results[0].url == f"https://archive.org/details/{escaped}"
    assert client.calls[1][0] == f"https://archive.org/download/{escaped}/{escaped}_archive.torrent"


def test_torrent_failure_skips_item_without_empty_magnet_result():
    client = FakeClient(torrent_response=FakeResponse(content=b"not a torrent"))
    provider = InternetArchiveProvider(client=client)

    assert provider.search("sample public movie", limit=1) == []


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(status_error=RuntimeError("status failed")),
        FakeResponse(json_error=ValueError("invalid json")),
    ],
)
def test_search_http_and_json_failures_raise_provider_error_with_context(response):
    client = FakeClient(search_response=response)
    provider = InternetArchiveProvider(client=client)

    with pytest.raises(ProviderError) as error:
        provider.search("sample public movie", limit=1)

    assert str(error.value).startswith("internet_archive search failed: ")


def test_human_size_rejects_invalid_values_and_formats_expected_sizes():
    assert _human_size(-1) == ""
    assert _human_size(math.nan) == ""
    assert _human_size(math.inf) == ""
    assert _human_size(None) == ""
    assert _human_size(0) == "0 B"
    assert _human_size(42) == "42 B"
    assert _human_size(1048576) == "1.0 MB"
