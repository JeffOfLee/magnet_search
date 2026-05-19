from __future__ import annotations

import csv
import json
import time
import tomllib
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import typer
from rich.console import Console
from rich.live import Live
from rich.table import Table

from magnet_search.batch import BatchError, run_batch
from magnet_search.config import ConfigError, load_config
from magnet_search.download import (
    Aria2cDownloader,
    DOWNLOAD_RECORD_FILENAME,
    DownloadError,
    DownloadResult,
    TransferCacheStorage,
    append_download_record,
    collect_download_sources,
    cleanup_download_result,
    load_download_records,
    parse_storage_size,
    run_download_batch,
)
from magnet_search.models import AllProvidersFailed, ProviderWarning, SearchResult
from magnet_search.providers.configurable import JsonHttpProvider
from magnet_search.providers.internet_archive import InternetArchiveProvider
from magnet_search.providers.manager import SearchService
from magnet_search.qbittorrent import QbittorrentDownloader, QbittorrentDownloadStatus
from magnet_search.storage import S3Uploader, UploadConfigError, UploadError, load_s3_upload_config


app = typer.Typer(help="Search legal/public and user-configured magnet resources.")
console = Console(width=240)
error_console = Console(stderr=True, width=1000)
BUILD_ERRORS = (ConfigError, tomllib.TOMLDecodeError, OSError, RuntimeError)
UPLOAD_META_FILENAME = ".upload_meta.csv"
UPLOAD_META_FIELDS = ["keyword", "origin", "input", "item", "path", "s3_key", "status", "err"]


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


def build_s3_uploader(upload_config_path: Path, key_gen: str = "hash") -> S3Uploader:
    return S3Uploader(load_s3_upload_config(upload_config_path), key_gen=key_gen)


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


def _format_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(value)
    for unit in units:
        if amount < 1000 or unit == units[-1]:
            if unit == "B":
                return f"{int(amount)} {unit}"
            return f"{amount:.1f} {unit}"
        amount /= 1000
    return f"{amount:.1f} TB"


def _format_speed(value: int) -> str:
    return f"{_format_bytes(value)}/s"


def _format_eta(seconds: int) -> str:
    if seconds <= 0:
        return ""
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


def _render_qbittorrent_monitor_table(downloads: list[QbittorrentDownloadStatus]) -> Table:
    table = Table(title="qBittorrent Downloads")
    table.add_column("Name")
    table.add_column("State")
    table.add_column("Progress", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Downloaded", justify="right")
    table.add_column("Down", justify="right")
    table.add_column("Up", justify="right")
    table.add_column("ETA", justify="right")
    table.add_column("Seeds", justify="right")
    table.add_column("Peers", justify="right")
    table.add_column("Save Path")

    if not downloads:
        table.add_row("No downloads", "", "", "", "", "", "", "", "", "", "")
        return table

    for download in downloads:
        table.add_row(
            download.name,
            download.state,
            f"{download.progress * 100:.1f}%",
            _format_bytes(download.size),
            _format_bytes(download.downloaded),
            _format_speed(download.download_speed),
            _format_speed(download.upload_speed),
            _format_eta(download.eta),
            str(download.seeds),
            str(download.peers),
            download.save_path,
        )
    return table


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


def _load_uploaded_rows(output: Path | None) -> tuple[set[str], set[str]]:
    if output is None or not output.exists():
        return set(), set()

    uploaded_sources: set[str] = set()
    uploaded_files: set[str] = set()
    with output.open(newline="", encoding="utf-8") as result_file:
        reader = csv.DictReader(result_file)
        for row in reader:
            if row.get("status") not in ("success", "uploaded"):
                continue
            source = row.get("input", row.get("source", ""))
            file = row.get("path", row.get("file", ""))
            if source:
                uploaded_sources.add(source)
            if file:
                uploaded_files.add(file)
    return uploaded_sources, uploaded_files


def _cleanup_uploaded_cache_from_upload_meta(storage: Path, upload_meta: Path | None) -> None:
    if upload_meta is None or not upload_meta.exists():
        return
    with upload_meta.open(newline="", encoding="utf-8") as result_file:
        reader = csv.DictReader(result_file)
        for row in reader:
            if row.get("status") not in ("success", "uploaded"):
                continue
            raw_path = row.get("path", row.get("file", ""))
            if not raw_path:
                continue
            file_path = Path(raw_path)
            if not file_path.is_absolute():
                file_path = storage / file_path
            cleanup_download_result(DownloadResult(row.get("input", ""), [file_path]), storage)


def _cached_download_results_for_upload(
    storage: Path,
    output: Path | None,
    transfer_cache: TransferCacheStorage | None,
    download_meta: Path | None = None,
) -> tuple[list[DownloadResult], set[str]]:
    _cleanup_uploaded_cache_from_upload_meta(storage, output)
    uploaded_sources, uploaded_files = _load_uploaded_rows(output)
    records = load_download_records(download_meta or storage)
    pending_files_by_source: dict[tuple[str, str, str], list[Path]] = {}
    skip_sources = set(uploaded_sources)

    for record in records:
        if record.status != "success" or not str(record.path):
            continue
        skip_sources.add(record.input)
        file_path = record.path
        try:
            upload_path = str(file_path.relative_to(storage))
        except ValueError:
            upload_path = file_path.name
        if upload_path in uploaded_files or str(file_path) in uploaded_files:
            if file_path.exists():
                cleanup_download_result(DownloadResult(record.input, [file_path]), storage)
            continue
        if file_path.exists():
            key = (record.keyword, record.origin, record.input)
            pending_files_by_source.setdefault(key, []).append(file_path)

    cached_results = [
        DownloadResult(magnet=input_value, files=files, keyword=keyword, origin=origin, input=input_value)
        for (keyword, origin, input_value), files in pending_files_by_source.items()
    ]
    return cached_results, skip_sources


def _open_upload_result_writer(output: Path | None) -> tuple[object | None, csv.DictWriter[str] | None]:
    if output is None:
        return None, None

    output.parent.mkdir(parents=True, exist_ok=True)
    write_header = not output.exists() or output.stat().st_size == 0
    csv_file = output.open("a", newline="", encoding="utf-8")
    csv_writer = csv.DictWriter(csv_file, fieldnames=UPLOAD_META_FIELDS)
    if write_header:
        csv_writer.writeheader()
        csv_file.flush()
    return csv_file, csv_writer


def _relative_storage_path(path: Path, storage: Path) -> str:
    try:
        return str(path.relative_to(storage))
    except ValueError:
        return path.name


def _s3_key_from_uploaded(value: str) -> str:
    if value.startswith("s3://"):
        parts = value.split("/", 3)
        if len(parts) == 4:
            return parts[3]
    return value


def _default_download_meta(storage: Path, download_meta: Path | None) -> Path:
    return download_meta or storage / DOWNLOAD_RECORD_FILENAME


def _default_upload_meta(storage: Path, upload_meta: Path | None) -> Path:
    return upload_meta or storage / UPLOAD_META_FILENAME


def _is_download_meta_source(path: Path) -> bool:
    if not path.exists() or path.suffix.lower() != ".csv":
        return False
    with path.open(newline="", encoding="utf-8-sig") as input_file:
        reader = csv.DictReader(input_file)
        fields = set(reader.fieldnames or [])
    return {"input", "path", "status"}.issubset(fields)


def _active_download_sources(downloader: object) -> set[str]:
    getter = getattr(downloader, "active_download_sources", None)
    if getter is None:
        return set()
    try:
        return set(getter())
    except Exception:
        return set()


def _download_result_input(result: DownloadResult) -> str:
    return result.input or result.magnet


def _startup_download_results(downloader: object, storage: Path) -> list[DownloadResult]:
    getter = getattr(downloader, "startup_download_results", None)
    if getter is None:
        return []
    return list(getter(storage))


def _record_startup_download_results(
    results: list[DownloadResult],
    storage: Path,
    download_meta: Path,
) -> list[DownloadResult]:
    existing_inputs = {
        record.input
        for record in load_download_records(download_meta)
        if record.status == "success" and record.input
    }
    recorded: list[DownloadResult] = []
    for result in results:
        result_input = _download_result_input(result)
        if result_input in existing_inputs:
            continue
        append_download_record(download_meta, result, base_dir=storage)
        existing_inputs.add(result_input)
        recorded.append(result)
    return recorded


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


def _run_qbittorrent_seed_priority_batch(
    input_path: Path,
    column: str,
    storage: Path,
    downloader: QbittorrentDownloader,
    download_concurrency: int,
    download_meta: Path,
    on_result: ResultCallback | None = None,
    raise_on_failure: bool = True,
    skip_sources: set[str] | None = None,
) -> tuple[list[DownloadResult], list[tuple[str, Exception]]]:
    sources = collect_download_sources(
        input_path,
        column,
        storage,
        result_path=download_meta,
        skip_sources=skip_sources,
    )
    results, failures = downloader.download_sources_by_seed_priority(
        sources,
        storage,
        max_active=download_concurrency,
    )
    source_by_input = {source.input: source for source in sources}
    for result in results:
        append_download_record(download_meta, result, base_dir=storage)
        if on_result is not None:
            on_result(result)
    for source_input, error in failures:
        source = source_by_input.get(source_input)
        append_download_record(
            download_meta,
            failure=(source_input, error),
            failure_keyword=source.keyword if source is not None else "",
            failure_origin=source.origin if source is not None else "",
            base_dir=storage,
        )
    if failures and raise_on_failure:
        details = "; ".join(f"{source}: {error}" for source, error in failures)
        raise DownloadError(f"{len(failures)} download(s) failed: {details}", failures=failures)
    return results, failures


def _run_search_batch_or_exit(
    input_csv: Path,
    column: str,
    search_meta: Path,
    limit: int,
    service: SearchService,
    verbose: bool = False,
) -> None:
    warning_printer = BatchWarningPrinter()
    _verbose(verbose, f"batch input={input_csv} column={column} search_meta={search_meta} limit={limit}")

    def search_func(query: str, per_query_limit: int) -> list[SearchResult]:
        _verbose(verbose, f"batch query={query} limit={per_query_limit}")
        results, warnings = service.search(query, per_query_limit)
        warning_printer.print_once(warnings)
        _verbose(verbose, f"batch query={query} results={len(results)} warnings={len(warnings)}")
        return results

    try:
        run_batch(input_csv, column=column, output_path=search_meta, limit=limit, search_func=search_func)
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
    _verbose(verbose, f"batch wrote={search_meta}")
    typer.echo(f"wrote {search_meta}")


@app.command()
def search(
    query: str,
    limit: int = typer.Option(3, min=1, help="Maximum number of results."),
    json_output: bool = typer.Option(False, "--json", help="Print JSON instead of a table."),
    column: str = typer.Option("query", help="CSV column containing resource names."),
    search_meta: Path = typer.Option(
        Path(".search_record.csv"),
        "--search-meta",
        "--search_meta",
        "--output",
        "-o",
        help="Search metadata CSV path.",
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Print detailed process logs to stderr."),
) -> None:
    service = _build_search_service_or_exit()
    if _is_csv_batch_source(query):
        _verbose(verbose, f"search mode=batch input={Path(query)} column={column} search_meta={search_meta} limit={limit}")
        _run_search_batch_or_exit(
            Path(query),
            column=column,
            search_meta=search_meta,
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
    _run_search_batch_or_exit(input_csv, column=column, search_meta=output, limit=limit, service=service, verbose=verbose)


@app.command("qbittorrent-monitor")
def qbittorrent_monitor(
    qbittorrent_url: str = typer.Option("http://localhost:8080", help="qBittorrent Web API URL."),
    qbittorrent_username: str = typer.Option("admin", help="qBittorrent Web API username."),
    qbittorrent_password: str = typer.Option("", help="qBittorrent Web API password."),
    interval: float = typer.Option(1.0, "--interval", min=0.1, help="Refresh interval in seconds."),
    once: bool = typer.Option(False, "--once", hidden=True, help="Render once and exit."),
) -> None:
    downloader = QbittorrentDownloader(
        url=qbittorrent_url,
        username=qbittorrent_username,
        password=qbittorrent_password,
    )

    try:
        if once:
            console.print(_render_qbittorrent_monitor_table(downloader.list_downloads()))
            return

        with Live(console=console, refresh_per_second=4, transient=False) as live:
            while True:
                live.update(_render_qbittorrent_monitor_table(downloader.list_downloads()))
                time.sleep(interval)
    except KeyboardInterrupt:
        return
    except Exception as error:
        _print_error(error)
        raise typer.Exit(1) from error


@app.command()
def download(
    source: str,
    storage: Path = typer.Option(Path("downloads"), "--storage", help="Directory for downloaded files and cache."),
    download_meta: Path | None = typer.Option(
        None,
        "--download-meta",
        "--download_meta",
        "--output",
        "-o",
        help="Download metadata CSV path.",
    ),
    upload_meta: Path | None = typer.Option(
        None,
        "--upload-meta",
        "--upload_meta",
        help="Upload metadata CSV path.",
    ),
    column: str = typer.Option("magnet", help="CSV column containing magnet links."),
    upload: Path | None = typer.Option(None, "--upload", help="S3 upload TOML config path."),
    download_concurrency: int = typer.Option(1, min=1, help="Concurrent downloads for CSV batch input."),
    upload_concurrency: int = typer.Option(1, min=1, help="Concurrent S3 uploads when --upload is provided."),
    key_gen: str = typer.Option("hash", "--key-gen", help="S3 object key generation: hash or path."),
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
        if key_gen not in ("hash", "path"):
            _print_error(ValueError("--key-gen must be hash or path"))
            raise typer.Exit(1)

        uploader = build_s3_uploader(upload, key_gen=key_gen) if upload is not None else None
        is_batch = _is_csv_batch_source(source)
        is_download_meta = _is_download_meta_source(Path(source)) if is_batch else False
        mode = "batch" if is_batch else "single"
        if download_meta is not None and not is_batch:
            _print_error(ValueError("--download-meta is only supported for CSV batch downloads"))
            raise typer.Exit(1)
        resolved_download_meta = _default_download_meta(storage, download_meta)
        resolved_upload_meta = _default_upload_meta(storage, upload_meta) if uploader is not None else upload_meta

        if engine == "qbittorrent":
            downloader = QbittorrentDownloader(
                url=qbittorrent_url,
                username=qbittorrent_username,
                password=qbittorrent_password,
                verbose=verbose,
            )
        else:
            downloader = build_downloader(verbose=verbose)

        _verbose(
            verbose,
            (
                f"download mode={mode} source={source} storage={storage} download_meta={resolved_download_meta} "
                f"download_concurrency={download_concurrency} upload_config={upload} "
                f"upload_concurrency={upload_concurrency} key_gen={key_gen} upload_meta={resolved_upload_meta} "
                f"transfer_cache_storage={transfer_cache_storage} engine={engine}"
            ),
        )

        startup_results: list[DownloadResult] = []
        startup_sources: set[str] = set()
        if is_batch and not is_download_meta:
            startup_results = _record_startup_download_results(
                _startup_download_results(downloader, storage),
                storage,
                resolved_download_meta,
            )
            startup_sources = {_download_result_input(result) for result in startup_results}
            if startup_results:
                _verbose(verbose, f"download recovered startup items={len(startup_results)}")

        if uploader is None:
            if is_batch:
                active_sources = _active_download_sources(downloader) | startup_sources
                if engine == "qbittorrent":
                    results, _ = _run_qbittorrent_seed_priority_batch(
                        Path(source),
                        column,
                        storage,
                        downloader,
                        download_concurrency,
                        resolved_download_meta,
                        skip_sources=active_sources or None,
                    )
                else:
                    batch_kwargs = {
                        "input_path": Path(source),
                        "column": column,
                        "output_dir": storage,
                        "downloader": downloader,
                        "download_concurrency": download_concurrency,
                        "result_path": resolved_download_meta,
                    }
                    if active_sources:
                        batch_kwargs["skip_sources"] = active_sources
                    results, _ = run_download_batch(**batch_kwargs)
                all_results = startup_results + results
                file_count = sum(len(result.files) for result in all_results)
                _verbose(verbose, f"download completed items={len(all_results)} files={file_count}")
                typer.echo(f"downloaded {len(all_results)} item(s), {file_count} file(s)")
            else:
                result = downloader.download(source, storage)
                append_download_record(storage, DownloadResult(result.magnet, result.files, input=source))
                _verbose(verbose, f"download completed files={len(result.files)}")
                typer.echo(f"downloaded {len(result.files)} file(s)")
            return

        upload_futures: dict[Future[list[str]], DownloadResult] = {}
        download_results: list[DownloadResult] = []
        download_failures: list[tuple[str, Exception]] = []

        csv_file, csv_writer = _open_upload_result_writer(resolved_upload_meta)
        source_download_meta = Path(source) if is_download_meta else resolved_download_meta
        cached_upload_results, skip_sources = _cached_download_results_for_upload(
            storage,
            resolved_upload_meta,
            transfer_cache,
            source_download_meta,
        )

        with ThreadPoolExecutor(max_workers=upload_concurrency) as upload_executor:

            def enqueue_upload(result: DownloadResult) -> Future[list[str]]:
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
                return future

            uploaded = 0
            upload_errors: list[Exception] = []

            def record_completed_upload(future: Future[list[str]]) -> None:
                nonlocal uploaded
                result = upload_futures[future]
                try:
                    s3_keys = future.result()
                    status = "success"
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
                            s3_key = _s3_key_from_uploaded(s3_keys[i]) if i < len(s3_keys) else ""
                            upload_path = _relative_storage_path(file_path, storage)
                            csv_writer.writerow({
                                "keyword": result.keyword,
                                "origin": result.origin,
                                "input": result.input or result.magnet,
                                "item": upload_path,
                                "path": upload_path,
                                "s3_key": s3_key,
                                "status": status,
                                "err": error_str,
                            })
                    else:
                        csv_writer.writerow({
                            "keyword": result.keyword,
                            "origin": result.origin,
                            "input": result.input or result.magnet,
                            "item": "",
                            "path": "",
                            "s3_key": "",
                            "status": status,
                            "err": error_str,
                        })
                    csv_file.flush()

            cached_futures = [enqueue_upload(cached_result) for cached_result in cached_upload_results]
            for future in as_completed(cached_futures):
                record_completed_upload(future)
                upload_futures.pop(future, None)

            if is_batch and not is_download_meta:
                batch_kwargs = {
                    "input_path": Path(source),
                    "column": column,
                    "output_dir": storage,
                    "downloader": downloader,
                    "download_concurrency": download_concurrency,
                    "on_result": enqueue_upload,
                    "raise_on_failure": False,
                    "result_path": resolved_download_meta,
                }
                if skip_sources:
                    batch_kwargs["skip_sources"] = skip_sources
                active_sources = _active_download_sources(downloader)
                if active_sources:
                    batch_kwargs["skip_sources"] = set(batch_kwargs.get("skip_sources", set())) | active_sources
                if startup_sources:
                    batch_kwargs["skip_sources"] = set(batch_kwargs.get("skip_sources", set())) | startup_sources
                if engine == "qbittorrent":
                    download_results, download_failures = _run_qbittorrent_seed_priority_batch(
                        Path(source),
                        column,
                        storage,
                        downloader,
                        download_concurrency,
                        resolved_download_meta,
                        on_result=enqueue_upload,
                        raise_on_failure=False,
                        skip_sources=batch_kwargs.get("skip_sources"),
                    )
                else:
                    if transfer_cache is not None:
                        batch_kwargs["before_download"] = transfer_cache.wait_for_space
                    download_results, download_failures = run_download_batch(**batch_kwargs)
                file_count = sum(len(result.files) for result in download_results)
                _verbose(verbose, f"download completed items={len(download_results)} files={file_count}")
                typer.echo(f"downloaded {len(download_results)} item(s), {file_count} file(s)")
            else:
                if not is_download_meta:
                    result = downloader.download(source, storage)
                    result = DownloadResult(result.magnet, result.files, input=source)
                    append_download_record(storage, result)
                    download_results.append(result)
                    enqueue_upload(result)
                    _verbose(verbose, f"download completed files={len(result.files)}")
                    typer.echo(f"downloaded {len(result.files)} file(s)")

            for future in as_completed(upload_futures):
                record_completed_upload(future)

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
