from __future__ import annotations

import json
import tomllib
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from magnet_search.batch import BatchError, run_batch
from magnet_search.config import ConfigError, load_config
from magnet_search.models import AllProvidersFailed, ProviderWarning, SearchResult
from magnet_search.providers.configurable import JsonHttpProvider
from magnet_search.providers.internet_archive import InternetArchiveProvider
from magnet_search.providers.manager import SearchService


app = typer.Typer(help="Search legal/public and user-configured magnet resources.")
console = Console(width=240)
error_console = Console(stderr=True)
BUILD_ERRORS = (ConfigError, tomllib.TOMLDecodeError, OSError, RuntimeError)


def build_search_service() -> SearchService:
    config = load_config()
    providers = [InternetArchiveProvider()]
    providers.extend(
        JsonHttpProvider(provider_config)
        for provider_config in config.http_providers
        if provider_config.enabled
    )
    return SearchService(providers)


def _print_error(error: Exception) -> None:
    error_console.print(f"[red]error[/red] {error}")


def _build_search_service_or_exit() -> SearchService:
    try:
        return build_search_service()
    except BUILD_ERRORS as error:
        _print_error(error)
        raise typer.Exit(1) from error


def _print_warnings(warnings: list[ProviderWarning]) -> None:
    for warning in warnings:
        error_console.print(f"[yellow]warning[/yellow] provider {warning.provider}: {warning.message}")


class BatchWarningPrinter:
    def __init__(self) -> None:
        self.warning_counts: Counter[tuple[str, str]] = Counter()

    def print_once(self, warnings: list[ProviderWarning]) -> None:
        for warning in warnings:
            key = (warning.provider, warning.message)
            self.warning_counts[key] += 1
            if self.warning_counts[key] == 1:
                error_console.print(f"[yellow]warning[/yellow] provider {warning.provider}: {warning.message}")

    def print_repeat_summary(self) -> None:
        repeat_count = sum(count - 1 for count in self.warning_counts.values())
        if repeat_count:
            error_console.print(f"[yellow]warning[/yellow] suppressed {repeat_count} repeated provider warning(s)")


def _render_table(results: list[SearchResult]) -> None:
    table = Table("Title", "Magnet", "Source", "Size", "Date", "Score", "URL")
    for result in results:
        table.add_row(
            result.title,
            result.magnet,
            result.source,
            result.size,
            result.published_at,
            str(result.score),
            result.url,
        )
    console.print(table)


@app.command()
def search(
    query: str,
    limit: int = typer.Option(3, min=1, help="Maximum number of results."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON instead of a table."),
) -> None:
    service = _build_search_service_or_exit()
    try:
        results, warnings = service.search(query, limit)
    except AllProvidersFailed as error:
        _print_error(error)
        raise typer.Exit(1) from error

    _print_warnings(warnings)
    if json_output:
        typer.echo(json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2))
    else:
        _render_table(results)


@app.command()
def batch(
    input_csv: Path,
    column: str = typer.Option(..., help="CSV column containing resource names."),
    output: Path = typer.Option(..., "--output", "-o", help="Output CSV path."),
    limit: int = typer.Option(3, min=1, help="Maximum results per resource."),
) -> None:
    service = _build_search_service_or_exit()
    warning_printer = BatchWarningPrinter()

    def search_func(query: str, per_query_limit: int) -> list[SearchResult]:
        results, warnings = service.search(query, per_query_limit)
        warning_printer.print_once(warnings)
        return results

    try:
        run_batch(input_csv, column=column, output_path=output, limit=limit, search_func=search_func)
    except AllProvidersFailed as error:
        _print_error(error)
        raise typer.Exit(1) from error
    except BatchError as error:
        _print_error(error)
        raise typer.Exit(1) from error
    except OSError as error:
        _print_error(error)
        raise typer.Exit(1) from error

    warning_printer.print_repeat_summary()
    typer.echo(f"wrote {output}")


if __name__ == "__main__":
    app()
