from __future__ import annotations

import csv
import json
import tomllib
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from magnet_search.batch import BatchError, run_batch
from magnet_search.config import ConfigError, load_config
from magnet_search.download import (
    Aria2cDownloader,
    DownloadError,
    DownloadResult,
    TransferCacheStorage,
    cleanup_download_result,
    parse_storage_size,
    run_download_batch,
)
from magnet_search.models import AllProvidersFailed, ProviderWarning, SearchResult
from magnet_search.providers.configurable import JsonHttpProvider
from magnet_search.providers.internet_archive import InternetArchiveProvider
from magnet_search.providers.manager import SearchService
from magnet_search.qbittorrent import QbittorrentDownloader
from magnet_search.storage import S3Uploader, UploadConfigError, UploadError, load_s3_upload_config


app = typer.Typer(help="Search legal/public and user-configured magnet resources.")
console = Console(width=240)
error_console = Console(stderr=True, width=1000)
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


def build_downloader(verbose: bool = False) -> Aria2cDownloader:
    return Aria2cDownloader(verbose=verbose)


def build_s3_uploader(upload_config_path: Path) -> S3Uploader:
    return S3Uploader(load_s3_upload_config(upload_config_path))


def _print_error(error: Exception) -> None:
    error_console.print(f"[red]error[/red] {error}")


def _verbose(enabled: bool, message: str) -> None:
    if enabled:
        error_console.print(f"[dim]verbose[/dim] {message}")


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


def _upload_download_result(
    uploader: S3Uploader,
    result: DownloadResult,
    output_dir: Path,
    transfer_cache: TransferCacheStorage | None = None,
) -> list[str]:
    if not result.files:
        return []
    uploaded = uploader.upload_files(result.files, output_dir)
    if transfer_cache is not None:
        cleanup_download_result(result, output_dir)
        transfer_cache.release_result(result)
    return uploaded


def _collect_upload_futures(upload_futures: list[Future[list[str]]]) -> list[str]:
    uploaded: list[str] = []
    failures: list[Exception] = []
    for future in as_completed(upload_futures):
        try:
            uploaded.extend(future.result())
        except Exception as error:
            failures.append(error)
    if failures:
        details = "; ".join(str(error) for error in failures)
        raise UploadError(f"{len(failures)} upload(s) failed: {details}")
    return uploaded


def _run_search_batch_or_exit(
    input_csv: Path,
    column: str,
    output: Path,
    limit: int,
    service: SearchService,
    verbose: bool = False,
) -> None:
    warning_printer = BatchWarningPrinter()
    _verbose(verbose, f"batch input={input_csv} column={column} output={output} limit={limit}")

    def search_func(query: str, per_query_limit: int) -> list[SearchResult]:
        _verbose(verbose, f"batch query={query} limit={per_query_limit}")
        results, warnings = service.search(query, per_query_limit)
        warning_printer.print_once(warnings)
        _verbose(verbose, f"batch query={query} results={len(results)} warnings={len(warnings)}")
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
    _verbose(verbose, f"batch wrote={output}")
    typer.echo(f"wrote {output}")


@app.command()
def search(
    query: str,
    limit: int = typer.Option(3, min=1, help="Maximum number of results."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON instead of a table."),
    column: str = typer.Option("query", help="CSV column containing resource names."),
    output: Path | None = typer.Option(None, "--output", "-o", help="Batch output CSV path."),
    verbose: bool = typer.Option(False, "--verbose", help="Print detailed process logs to stderr."),
) -> None:
    service = _build_search_service_or_exit()
    if _is_csv_batch_source(query):
        if output is None:
            _print_error(ValueError("batch search requires --output"))
            raise typer.Exit(1)
        _verbose(verbose, f"search mode=batch input={Path(query)} column={column} output={output} limit={limit}")
        _run_search_batch_or_exit(
            Path(query),
            column=column,
            output=output,
            limit=limit,
            service=service,
            verbose=verbose,
        )
        return

    _verbose(verbose, f"search mode=single query={query} limit={limit}")
    try:
        results, warnings = service.search(query, limit)
    except AllProvidersFailed as error:
        _print_error(error)
        raise typer.Exit(1) from error

    _print_warnings(warnings)
    _verbose(verbose, f"search results={len(results)} warnings={len(warnings)}")
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
    verbose: bool = typer.Option(False, "--verbose", help="Print detailed process logs to stderr."),
) -> None:
    service = _build_search_service_or_exit()
    _run_search_batch_or_exit(input_csv, column=column, output=output, limit=limit, service=service, verbose=verbose)


@app.command()
def download(
    source: str,
    storage: Path = typer.Option(Path("downloads"), "--storage", help="Directory for downloaded files and cache."),
    output: Path | None = typer.Option(None, "--output", "-o", help="Batch result CSV path."),
    column: str = typer.Option("magnet", help="CSV column containing magnet links."),
    upload: Path | None = typer.Option(None, "--upload", help="S3 upload TOML config path."),
    download_concurrency: int = typer.Option(1, min=1, help="Concurrent downloads for CSV batch input."),
    upload_concurrency: int = typer.Option(1, min=1, help="Concurrent S3 uploads when --upload is provided."),
    transfer_cache_storage: str | None = typer.Option(
        None,
        "--transfer-cache-storage",
        help="Maximum current-run local cache size before pausing new downloads, e.g. 500MB or 10GB.",
    ),
    engine: str = typer.Option("aria2c", help="Download engine: aria2c or qbittorrent."),
    qbittorrent_url: str = typer.Option("http://localhost:8080", help="qBittorrent Web API URL."),
    qbittorrent_username: str = typer.Option("admin", help="qBittorrent Web API username."),
    qbittorrent_password: str = typer.Option("", help="qBittorrent Web API password."),
    verbose: bool = typer.Option(False, "--verbose", help="Print detailed process logs to stderr."),
) -> None:
    try:
        if transfer_cache_storage is not None and upload is None:
            _print_error(ValueError("--transfer-cache-storage requires --upload"))
            raise typer.Exit(1)

        transfer_cache = (
            TransferCacheStorage(parse_storage_size(transfer_cache_storage))
            if transfer_cache_storage is not None
            else None
        )
        uploader = build_s3_uploader(upload) if upload is not None else None
        is_batch = _is_csv_batch_source(source)
        mode = "batch" if is_batch else "single"
        if output is not None and not is_batch:
            _print_error(ValueError("--output is only supported for CSV batch downloads"))
            raise typer.Exit(1)

        if engine == "qbittorrent":
            downloader = QbittorrentDownloader(
                url=qbittorrent_url,
                username=qbittorrent_username,
                password=qbittorrent_password,
            )
        else:
            downloader = build_downloader(verbose=verbose)

        _verbose(
            verbose,
            (
                f"download mode={mode} source={source} storage={storage} output={output} "
                f"download_concurrency={download_concurrency} upload_config={upload} "
                f"upload_concurrency={upload_concurrency} "
                f"transfer_cache_storage={transfer_cache_storage} engine={engine}"
            ),
        )

        if uploader is None:
            if is_batch:
                batch_kwargs = {
                    "input_path": Path(source),
                    "column": column,
                    "output_dir": storage,
                    "downloader": downloader,
                    "download_concurrency": download_concurrency,
                }
                if output is not None:
                    batch_kwargs["result_path"] = output
                results, _ = run_download_batch(**batch_kwargs)
                file_count = sum(len(result.files) for result in results)
                _verbose(verbose, f"download completed items={len(results)} files={file_count}")
                typer.echo(f"downloaded {len(results)} item(s), {file_count} file(s)")
            else:
                result = downloader.download(source, storage)
                _verbose(verbose, f"download completed files={len(result.files)}")
                typer.echo(f"downloaded {len(result.files)} file(s)")
            return

        upload_futures: dict[Future[list[str]], DownloadResult] = {}
        download_results: list[DownloadResult] = []
        download_failures: list[tuple[str, Exception]] = []

        csv_file = None
        csv_writer = None
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            csv_file = output.open("w", newline="", encoding="utf-8")
            csv_writer = csv.DictWriter(csv_file, fieldnames=["source", "status", "file", "s3_key", "error"])
            csv_writer.writeheader()
            csv_file.flush()

        with ThreadPoolExecutor(max_workers=upload_concurrency) as upload_executor:

            def enqueue_upload(result: DownloadResult) -> None:
                _verbose(verbose, f"upload enqueue source={result.magnet} files={len(result.files)}")
                if transfer_cache is not None:
                    transfer_cache.track_result(result)
                future = upload_executor.submit(_upload_download_result, uploader, result, storage, transfer_cache)
                if transfer_cache is not None:
                    future.add_done_callback(
                        lambda completed: transfer_cache.abort(completed.exception())
                        if completed.exception() is not None
                        else None
                    )
                upload_futures[future] = result

            if is_batch:
                batch_kwargs = {
                    "input_path": Path(source),
                    "column": column,
                    "output_dir": storage,
                    "downloader": downloader,
                    "download_concurrency": download_concurrency,
                    "on_result": enqueue_upload,
                    "raise_on_failure": False,
                }
                if transfer_cache is not None:
                    batch_kwargs["before_download"] = transfer_cache.wait_for_space
                download_results, download_failures = run_download_batch(**batch_kwargs)
                file_count = sum(len(result.files) for result in download_results)
                _verbose(verbose, f"download completed items={len(download_results)} files={file_count}")
                typer.echo(f"downloaded {len(download_results)} item(s), {file_count} file(s)")
            else:
                result = downloader.download(source, storage)
                download_results.append(result)
                enqueue_upload(result)
                _verbose(verbose, f"download completed files={len(result.files)}")
                typer.echo(f"downloaded {len(result.files)} file(s)")

            if csv_writer is not None:
                for source, error in download_failures:
                    csv_writer.writerow({
                        "source": source,
                        "status": "failed",
                        "file": "",
                        "s3_key": "",
                        "error": str(error),
                    })
                csv_file.flush()

            uploaded = 0
            upload_errors: list[Exception] = []
            for future in as_completed(upload_futures):
                result = upload_futures[future]
                try:
                    s3_keys = future.result()
                    status = "uploaded"
                    error_str = ""
                except Exception as error:
                    upload_errors.append(error)
                    s3_keys = []
                    status = "failed"
                    error_str = str(error)

                uploaded += len(s3_keys)

                if csv_writer is not None:
                    if result.files:
                        for i, file_path in enumerate(result.files):
                            s3_key = s3_keys[i] if i < len(s3_keys) else ""
                            csv_writer.writerow({
                                "source": result.magnet,
                                "status": status,
                                "file": str(file_path),
                                "s3_key": s3_key,
                                "error": error_str,
                            })
                    else:
                        csv_writer.writerow({
                            "source": result.magnet,
                            "status": status,
                            "file": "",
                            "s3_key": "",
                            "error": error_str,
                        })
                    csv_file.flush()

            _verbose(verbose, f"upload completed files={uploaded}")
            typer.echo(f"uploaded {uploaded} file(s)")
            if upload_errors:
                details = "; ".join(str(error) for error in upload_errors)
                raise UploadError(f"{len(upload_errors)} upload(s) failed: {details}")

        if csv_file is not None:
            csv_file.close()
    except (DownloadError, UploadConfigError, UploadError, tomllib.TOMLDecodeError, OSError) as error:
        _print_error(error)
        raise typer.Exit(1) from error


if __name__ == "__main__":
    app()
