import csv
from pathlib import Path

import pytest

from magnet_search.batch import BatchError, run_batch
from magnet_search.models import SearchResult


def make_result(query: str = "Sample Movie", score: float = 1.23) -> SearchResult:
    return SearchResult(
        query=query,
        title="Sample Movie Result",
        magnet="magnet:?xt=urn:btih:sample",
        source="test",
        score=score,
    )


def test_run_batch_writes_one_row_per_result(tmp_path: Path):
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"
    input_path.write_text("title\nSample Movie\n", encoding="utf-8")

    def fake_search(query: str, limit: int):
        return [make_result(query=query)]

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


def test_run_batch_preserves_original_query_when_result_query_differs(tmp_path: Path):
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"
    input_path.write_text("title\nOriginal Movie\n", encoding="utf-8")

    run_batch(
        input_path,
        column="title",
        output_path=output_path,
        limit=3,
        search_func=lambda query, limit: [make_result(query="Changed Movie")],
    )

    rows = list(csv.DictReader(output_path.open(encoding="utf-8")))
    assert rows[0]["query"] == "Original Movie"


def test_run_batch_handles_utf8_bom_header(tmp_path: Path):
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"
    input_path.write_text("title\nSample Movie\n", encoding="utf-8-sig")

    run_batch(input_path, column="title", output_path=output_path, limit=3, search_func=lambda query, limit: [])

    rows = list(csv.DictReader(output_path.open(encoding="utf-8")))
    assert rows[0]["query"] == "Sample Movie"


def test_run_batch_rejects_duplicate_headers(tmp_path: Path):
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"
    input_path.write_text("title,title\nSample Movie,Duplicate\n", encoding="utf-8")

    with pytest.raises(BatchError, match="duplicate CSV header: title"):
        run_batch(input_path, column="title", output_path=output_path, limit=3, search_func=lambda query, limit: [])


def test_run_batch_treats_whitespace_query_as_empty_without_searching(tmp_path: Path):
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"
    input_path.write_text("title\n   \n", encoding="utf-8")

    def fail_search(query: str, limit: int):
        raise AssertionError("search should not be called")

    run_batch(input_path, column="title", output_path=output_path, limit=3, search_func=fail_search)

    rows = list(csv.DictReader(output_path.open(encoding="utf-8")))
    assert rows[0]["query"] == "   "
    assert rows[0]["title"] == ""


def test_run_batch_serializes_zero_score(tmp_path: Path):
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"
    input_path.write_text("title\nSample Movie\n", encoding="utf-8")

    run_batch(
        input_path,
        column="title",
        output_path=output_path,
        limit=3,
        search_func=lambda query, limit: [make_result(query=query, score=0.0)],
    )

    rows = list(csv.DictReader(output_path.open(encoding="utf-8")))
    assert rows[0]["score"] == "0.0"


def test_run_batch_creates_output_parent_directory(tmp_path: Path):
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "missing" / "parent" / "output.csv"
    input_path.write_text("title\nSample Movie\n", encoding="utf-8")

    run_batch(input_path, column="title", output_path=output_path, limit=3, search_func=lambda query, limit: [])

    assert output_path.exists()


def test_run_batch_rejects_negative_limit_before_search(tmp_path: Path):
    input_path = tmp_path / "missing.csv"
    output_path = tmp_path / "output.csv"

    def fail_search(query: str, limit: int):
        raise AssertionError("search should not be called")

    with pytest.raises(BatchError, match="^limit must be non-negative$"):
        run_batch(input_path, column="title", output_path=output_path, limit=-1, search_func=fail_search)

    assert not output_path.exists()


def test_run_batch_rejects_non_list_search_results_atomically(tmp_path: Path):
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"
    input_path.write_text("title\nSample Movie\n", encoding="utf-8")

    with pytest.raises(BatchError, match="search_func must return list\\[SearchResult\\]"):
        run_batch(
            input_path,
            column="title",
            output_path=output_path,
            limit=3,
            search_func=lambda query, limit: "not a list",
        )

    assert not output_path.exists()


def test_run_batch_rejects_non_search_result_items_atomically(tmp_path: Path):
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"
    original_output = "existing output\n"
    input_path.write_text("title\nSample Movie\n", encoding="utf-8")
    output_path.write_text(original_output, encoding="utf-8")

    with pytest.raises(BatchError, match="search_func result item 0 must be SearchResult"):
        run_batch(
            input_path,
            column="title",
            output_path=output_path,
            limit=3,
            search_func=lambda query, limit: ["not a SearchResult"],
        )

    assert output_path.read_text(encoding="utf-8") == original_output


def test_run_batch_leaves_existing_output_unchanged_when_search_raises(tmp_path: Path):
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"
    original_output = "existing output\n"
    input_path.write_text("title\nSample Movie\n", encoding="utf-8")
    output_path.write_text(original_output, encoding="utf-8")

    def fail_search(query: str, limit: int):
        raise RuntimeError("search failed")

    with pytest.raises(RuntimeError, match="search failed"):
        run_batch(input_path, column="title", output_path=output_path, limit=3, search_func=fail_search)

    assert output_path.read_text(encoding="utf-8") == original_output
