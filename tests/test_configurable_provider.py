from pathlib import Path

import httpx
import pytest

from magnet_search import config as config_module
from magnet_search.config import load_config
from magnet_search.models import ProviderError
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


def provider_config(**overrides):
    entry = {
        "name": "example",
        "enabled": True,
        "search_url": "https://example.invalid/search?q={query}",
        "result_path": "results",
        "title_path": "title",
        "magnet_path": "magnet",
    }
    entry.update(overrides)
    return {"providers": {"http": [entry]}}


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


def test_config_error_is_named_value_error():
    assert issubclass(config_module.ConfigError, ValueError)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"providers": []}, "providers must be a table"),
        ({"providers": {"http": {}}}, "providers.http must be a list"),
        ({"providers": {"http": ["bad"]}}, "provider 0 must be a table"),
        (provider_config(name=""), "provider 0 required field name must be a non-empty string"),
        (provider_config(search_url="https://example.invalid/search"), "example search_url must contain {query}"),
        (provider_config(enabled="true"), "example enabled must be a bool"),
    ],
)
def test_load_config_rejects_invalid_config_shapes_and_types(data, message):
    with pytest.raises(config_module.ConfigError, match=message):
        load_config(data=data)


def test_load_config_omits_disabled_providers():
    config = load_config(data=provider_config(enabled=False))

    assert config.http_providers == []


def test_json_http_provider_maps_nested_paths():
    config = load_config(
        data=provider_config(
            result_path="data.items",
            title_path="attributes.title",
            magnet_path="links.magnet",
            url_path="links.page",
            size_path="attributes.size",
            published_at_path="attributes.published_at",
        )
    )

    class NestedClient:
        def get(self, url, timeout):
            return FakeResponse(
                {
                    "data": {
                        "items": [
                            {
                                "attributes": {
                                    "title": "Nested Result",
                                    "size": "1.2 GB",
                                    "published_at": "2024-05-01",
                                },
                                "links": {
                                    "magnet": "magnet:?xt=urn:btih:nested",
                                    "page": "https://example.invalid/nested",
                                },
                            }
                        ]
                    }
                }
            )

    provider = JsonHttpProvider(config.http_providers[0], client=NestedClient())

    results = provider.search("nested movie", limit=5)

    assert results[0].title == "Nested Result"
    assert results[0].magnet == "magnet:?xt=urn:btih:nested"
    assert results[0].url == "https://example.invalid/nested"
    assert results[0].size == "1.2 GB"
    assert results[0].published_at == "2024-05-01"


def test_json_http_provider_rejects_non_list_result_path():
    config = load_config(data=provider_config(result_path="meta"))

    class NonListClient:
        def get(self, url, timeout):
            return FakeResponse({"meta": {"title": "not a list"}})

    provider = JsonHttpProvider(config.http_providers[0], client=NonListClient())

    with pytest.raises(ProviderError, match="example result_path did not resolve to a list"):
        provider.search("query", limit=5)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"results": ["bad"]}, "example result 0 must be a table"),
        ({"results": [{"title": "", "magnet": "magnet:?xt=urn:btih:valid"}]}, "example result 0 title must be a non-empty string"),
        ({"results": [{"title": "Title", "magnet": ""}]}, "example result 0 magnet must be a non-empty string"),
    ],
)
def test_json_http_provider_rejects_invalid_result_entries(payload, message):
    config = load_config(data=provider_config())

    class InvalidResultClient:
        def get(self, url, timeout):
            return FakeResponse(payload)

    provider = JsonHttpProvider(config.http_providers[0], client=InvalidResultClient())

    with pytest.raises(ProviderError, match=message):
        provider.search("query", limit=5)


def test_json_http_provider_rejects_negative_limit():
    config = load_config(data=provider_config())
    provider = JsonHttpProvider(config.http_providers[0], client=FakeClient())

    with pytest.raises(ValueError, match="limit must be non-negative"):
        provider.search("query", limit=-1)


def test_json_http_provider_returns_empty_for_zero_limit_without_request():
    config = load_config(data=provider_config())

    class ExplodingClient:
        def get(self, url, timeout):
            raise AssertionError("client should not be called")

    provider = JsonHttpProvider(config.http_providers[0], client=ExplodingClient())

    assert provider.search("query", limit=0) == []


def test_json_http_provider_wraps_network_exceptions_with_provider_name():
    config = load_config(data=provider_config())

    class FailingClient:
        def get(self, url, timeout):
            raise httpx.ConnectError("connection failed")

    provider = JsonHttpProvider(config.http_providers[0], client=FailingClient())

    with pytest.raises(ProviderError, match="example search failed: connection failed"):
        provider.search("query", limit=1)
