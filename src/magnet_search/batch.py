from __future__ import annotations

import csv
import tempfile
from pathlib import Path
from typing import Callable

from magnet_search.models import SearchResult


OUTPUT_FIELDS = ["query", "title", "magnet", "source", "size", "published_at", "score", "url"]


class BatchError(ValueError):
    pass


def _row_from_result(query: str, result: SearchResult) -> dict[str, str]:
    return {
        "query": query,
        "title": result.title,
        "magnet": result.magnet,
        "source": result.source,
        "size": result.size,
        "published_at": result.published_at,
        "score": str(result.score),
        "url": result.url,
    }


def _empty_row(query: str) -> dict[str, str]:
    return {field: "" for field in OUTPUT_FIELDS} | {"query": query}


def _validate_headers(fieldnames: list[str] | None, column: str) -> None:
    if fieldnames is None or column not in fieldnames:
        raise BatchError(f"missing column: {column}")
    seen: set[str] = set()
    for fieldname in fieldnames:
        if fieldname in seen:
            raise BatchError(f"duplicate CSV header: {fieldname}")
        seen.add(fieldname)


def _validate_results(results: object) -> list[SearchResult]:
    if not isinstance(results, list):
        raise BatchError("search_func must return list[SearchResult]")
    for index, result in enumerate(results):
        if not isinstance(result, SearchResult):
            raise BatchError(f"search_func result item {index} must be SearchResult, got {type(result).__name__}")
    return results


def run_batch(
    input_path: Path,
    column: str,
    output_path: Path,
    limit: int,
    search_func: Callable[[str, int], list[SearchResult]],
) -> None:
    if limit < 0:
        raise BatchError("limit must be non-negative")

    with input_path.open(newline="", encoding="utf-8-sig") as input_file:
        reader = csv.DictReader(input_file)
        _validate_headers(reader.fieldnames, column)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            temp_file = tempfile.NamedTemporaryFile(
                "w",
                newline="",
                encoding="utf-8",
                dir=output_path.parent,
                prefix=f".{output_path.name}.",
                suffix=".tmp",
                delete=False,
            )
            temp_path = Path(temp_file.name)
            with temp_file as output_file:
                _write_output(reader, column, output_file, limit, search_func)
            temp_path.replace(output_path)
        except Exception:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise


def _write_output(
    reader: csv.DictReader[str],
    column: str,
    output_file,
    limit: int,
    search_func: Callable[[str, int], list[SearchResult]],
) -> None:
    writer = csv.DictWriter(output_file, fieldnames=OUTPUT_FIELDS)
    writer.writeheader()
    for row in reader:
        query = row.get(column, "")
        results = _validate_results(search_func(query, limit)) if query.strip() else []
        if not results:
            writer.writerow(_empty_row(query))
            continue
        for result in results:
            writer.writerow(_row_from_result(query, result))
