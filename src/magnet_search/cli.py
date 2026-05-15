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
from magnet_search.download import Aria2cDownloader, DownloadError, DownloadResult, run_download_batch
from magnet_search.models import AllProvidersFailed, ProviderWarning, SearchResult
from magnet_search.providers.configurable import JsonHttpProvider
from magnet_search.providers.internet_archive import InternetArchiveProvider
from magnet_search.providers.manager import SearchService
from magnet_search.storage import S3Uploader, UploadConfigError, UploadError, load_s3_upload_config


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


def build_downloader() -> Aria2cDownloader:
    return Aria2cDownloader()


def build_s3_uploader(upload_config_path: Path) -> S3Uploader:
    return S3Uploader(load_s3_upload_config(upload_config_path))


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


def _is_csv_batch_source(source: str) -> bool:
    path = Path(source)
    return path.exists() and path.suffix.lower() == ".csv"


def _upload_download_results(
    uploader: S3Uploader,
    results: list[DownloadResult],
    output_dir: Path,
) -> list[str]:
    uploaded: list[str] = []
    for result in results:
        uploaded.extend(uploader.upload_files(result.files, output_dir))
    return uploaded


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


@app.command()
def download(
    source: str,
    output: Path = typer.Option(Path("downloads"), "--output", "-o", help="Directory for downloaded files."),
    column: str = typer.Option("magnet", help="CSV column containing magnet links."),
    upload: Path | None = typer.Option(None, "--upload", help="S3 upload TOML config path."),
) -> None:
    try:
        uploader = build_s3_uploader(upload) if upload is not None else None
        downloader = build_downloader()

        if _is_csv_batch_source(source):
            results = run_download_batch(Path(source), column=column, output_dir=output, downloader=downloader)
            file_count = sum(len(result.files) for result in results)
            typer.echo(f"downloaded {len(results)} item(s), {file_count} file(s)")
        else:
            result = downloader.download(source, output)
            results = [result]
            typer.echo(f"downloaded {len(result.files)} file(s)")

        if uploader is not None:
            uploaded = _upload_download_results(uploader, results, output)
            typer.echo(f"uploaded {len(uploaded)} file(s)")
    except (DownloadError, UploadConfigError, UploadError, tomllib.TOMLDecodeError, OSError) as error:
        _print_error(error)
        raise typer.Exit(1) from error


if __name__ == "__main__":
    app()
