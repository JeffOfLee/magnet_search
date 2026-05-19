from __future__ import annotations

import csv
from pathlib import Path
from typing import Callable

from magnet_search.models import SearchResult


OUTPUT_FIELDS = ["keyword", "origin", "result", "status", "err"]


class BatchError(ValueError):
    pass


def _row_from_result(query: str, result: SearchResult) -> dict[str, str]:
    return {
        "keyword": query,
        "origin": result.source,
        "result": result.magnet,
        "status": "success",
        "err": "",
    }


def _failed_row(query: str, error: str) -> dict[str, str]:
    return {
        "keyword": query,
        "origin": "",
        "result": "",
        "status": "failed",
        "err": error,
    }


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
        successful_keywords = _successful_keywords(output_path)
        write_header = not output_path.exists() or output_path.stat().st_size == 0
        try:
            with output_path.open("a", newline="", encoding="utf-8") as output_file:
                _write_output(reader, column, output_file, limit, search_func, successful_keywords, write_header)
        except BatchError:
            if write_header:
                output_path.unlink(missing_ok=True)
            raise


def _successful_keywords(output_path: Path) -> set[str]:
    if not output_path.exists():
        return set()
    with output_path.open(newline="", encoding="utf-8") as output_file:
        reader = csv.DictReader(output_file)
        return {
            row.get("keyword", "")
            for row in reader
            if row.get("status") == "success" and row.get("keyword", "")
        }


def _write_output(
    reader: csv.DictReader[str],
    column: str,
    output_file,
    limit: int,
    search_func: Callable[[str, int], list[SearchResult]],
    successful_keywords: set[str] | None = None,
    write_header: bool = True,
) -> None:
    writer = csv.DictWriter(output_file, fieldnames=OUTPUT_FIELDS)
    if write_header:
        writer.writeheader()
    successful_keywords = successful_keywords or set()
    for row in reader:
        query = row.get(column, "")
        if query in successful_keywords:
            continue
        if not query.strip():
            writer.writerow(_failed_row(query, "empty keyword"))
            continue
        try:
            raw_results = search_func(query, limit)
        except Exception as error:
            writer.writerow(_failed_row(query, str(error)))
            continue

        results = _validate_results(raw_results)
        if not results:
            writer.writerow(_failed_row(query, "no results"))
            continue
        for result in results:
            writer.writerow(_row_from_result(query, result))
