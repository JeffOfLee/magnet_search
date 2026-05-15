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


def test_ranking_orders_equal_score_inputs_deterministically_when_reversed():
    first = SearchResult(
        query="a",
        title="same",
        magnet="magnet:1",
        source="alpha",
    )
    second = SearchResult(
        query="a",
        title="same",
        magnet="magnet:2",
        source="beta",
    )

    forward = rank_results("a", [first, second], limit=2)
    reversed_results = rank_results("a", [second, first], limit=2)

    assert [result.magnet for result in forward] == ["magnet:1", "magnet:2"]
    assert [result.magnet for result in reversed_results] == ["magnet:1", "magnet:2"]


def test_ranking_orders_metadata_ties_deterministically_when_reversed():
    size_result = SearchResult(
        query="a",
        title="same",
        magnet="magnet:1",
        source="alpha",
        size="1 GB",
        url="https://example.test/same",
    )
    published_result = SearchResult(
        query="a",
        title="same",
        magnet="magnet:1",
        source="alpha",
        published_at="2024-01-01",
        url="https://example.test/same",
    )

    forward = rank_results("a", [size_result, published_result], limit=2)
    reversed_results = rank_results("a", [published_result, size_result], limit=2)

    assert [(result.size, result.published_at) for result in forward] == [
        ("", "2024-01-01"),
        ("1 GB", ""),
    ]
    assert [(result.size, result.published_at) for result in reversed_results] == [
        ("", "2024-01-01"),
        ("1 GB", ""),
    ]


def test_ranking_limit_zero_returns_empty_list():
    result = SearchResult(query="a", title="a", magnet="magnet:1", source="test")

    assert rank_results("a", [result], limit=0) == []


def test_ranking_negative_limit_raises_value_error():
    result = SearchResult(query="a", title="a", magnet="magnet:1", source="test")

    try:
        rank_results("a", [result], limit=-1)
    except ValueError as exc:
        assert "limit must be non-negative" in str(exc)
    else:
        raise AssertionError("Expected ValueError")
