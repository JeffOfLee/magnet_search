from __future__ import annotations

import csv
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class DownloadError(RuntimeError):
    """Raised when a magnet download or download batch cannot complete."""

    def __init__(self, message: str, failures: list[tuple[str, Exception]] | None = None):
        super().__init__(message)
        self.failures = failures or []


@dataclass(frozen=True)
class DownloadResult:
    magnet: str
    files: list[Path]
    keyword: str = ""
    origin: str = ""
    input: str = ""


@dataclass(frozen=True)
class DownloadRecord:
    keyword: str
    origin: str
    input: str
    item: str
    path: Path
    status: str
    err: str = ""

    @property
    def source(self) -> str:
        return self.input

    @property
    def file(self) -> Path:
        return self.path


@dataclass(frozen=True)
class DownloadSource:
    keyword: str
    origin: str
    input: str
    source: str


Runner = Callable[..., subprocess.CompletedProcess[str]]
ResultCallback = Callable[[DownloadResult], None]
BeforeDownloadCallback = Callable[[], None]
RESULT_FIELDS = ["keyword", "origin", "input", "item", "path", "status", "err"]
UPLOAD_RESULT_FIELDS = ["keyword", "origin", "input", "item", "path", "s3_key", "status", "err"]
DOWNLOAD_RECORD_FILENAME = ".download_meta.csv"
DOWNLOAD_RECORD_FIELDS = RESULT_FIELDS
_download_record_lock = threading.Lock()


def write_upload_results_csv(
    result_path: Path,
    results: list[DownloadResult],
    failures: list[tuple[str, Exception]],
    upload_map: dict[str, list[str]],
) -> None:
    result_path.parent.mkdir(parents=True, exist_ok=True)
    with result_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=UPLOAD_RESULT_FIELDS)
        writer.writeheader()

        for result in results:
            s3_keys = upload_map.get(result.magnet, [])
            if result.files:
                for i, file_path in enumerate(result.files):
                    s3_key = s3_keys[i] if i < len(s3_keys) else ""
                    writer.writerow({
                        "keyword": result.keyword,
                        "origin": result.origin,
                        "input": _result_input(result),
                        "item": _item_for_path(file_path, base_dir=result_path.parent),
                        "path": str(file_path),
                        "s3_key": s3_key,
                        "status": "success",
                        "err": "",
                    })
            else:
                writer.writerow({
                    "keyword": result.keyword,
                    "origin": result.origin,
                    "input": _result_input(result),
                    "item": "",
                    "path": "",
                    "s3_key": "",
                    "status": "success",
                    "err": "",
                })

        for source, error in failures:
            writer.writerow({
                "keyword": "",
                "origin": "",
                "input": source,
                "item": "",
                "path": "",
                "s3_key": "",
                "status": "failed",
                "err": str(error),
            })
        f.flush()


def _download_meta_path(target: Path) -> Path:
    if target.suffix.lower() == ".csv":
        return target
    return target / DOWNLOAD_RECORD_FILENAME


def _cleanup_record_paths(records: list[DownloadRecord], base_dir: Path, status: str) -> None:
    for record in records:
        if record.status != status or not str(record.path):
            continue
        cleanup_download_result(DownloadResult(record.input, [record.path]), base_dir)


def _successful_record_inputs(records: list[DownloadRecord]) -> set[str]:
    return {
        record.input
        for record in records
        if record.status == "success" and record.input
    }


def _source_is_skipped(source: str, skipped: set[str]) -> bool:
    if source in skipped:
        return True
    folded = source.casefold()
    return any(token and token.casefold() in folded for token in skipped)


def _result_input(result: DownloadResult) -> str:
    return result.input or result.magnet


def _item_for_path(file_path: Path, base_dir: Path) -> str:
    try:
        return str(file_path.relative_to(base_dir))
    except ValueError:
        return file_path.name


def _download_rows(
    base_dir: Path,
    result: DownloadResult | None = None,
    failure: tuple[str, Exception] | None = None,
    failure_keyword: str = "",
    failure_origin: str = "",
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []

    if result is not None:
        rows.extend(
            {
                "keyword": result.keyword,
                "origin": result.origin,
                "input": _result_input(result),
                "item": _item_for_path(file_path, base_dir),
                "path": str(file_path),
                "status": "success",
                "err": "",
            }
            for file_path in result.files
        )
        if not result.files:
            rows.append(
                {
                    "keyword": result.keyword,
                    "origin": result.origin,
                    "input": _result_input(result),
                    "item": "",
                    "path": "",
                    "status": "success",
                    "err": "",
                }
            )

    if failure is not None:
        source, error = failure
        rows.append(
            {
                "keyword": failure_keyword,
                "origin": failure_origin,
                "input": source,
                "item": "",
                "path": "",
                "status": "failed",
                "err": str(error),
            }
        )
    return rows


def append_download_record(
    output_dir: Path,
    result: DownloadResult | None = None,
    failure: tuple[str, Exception] | None = None,
    failure_keyword: str = "",
    failure_origin: str = "",
) -> None:
    record_path = _download_meta_path(output_dir)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    base_dir = output_dir if output_dir.suffix.lower() != ".csv" else record_path.parent
    rows = _download_rows(base_dir, result, failure, failure_keyword, failure_origin)

    if not rows:
        return

    with _download_record_lock:
        write_header = not record_path.exists() or record_path.stat().st_size == 0
        with record_path.open("a", newline="", encoding="utf-8") as record_file:
            writer = csv.DictWriter(record_file, fieldnames=DOWNLOAD_RECORD_FIELDS)
            if write_header:
                writer.writeheader()
            writer.writerows(rows)
            record_file.flush()


def load_download_records(output_dir: Path) -> list[DownloadRecord]:
    record_path = _download_meta_path(output_dir)
    if not record_path.exists():
        return []

    records: list[DownloadRecord] = []
    with record_path.open(newline="", encoding="utf-8") as record_file:
        reader = csv.DictReader(record_file)
        for row in reader:
            records.append(
                DownloadRecord(
                    keyword=row.get("keyword", ""),
                    origin=row.get("origin", ""),
                    input=row.get("input", row.get("source", "")),
                    item=row.get("item", ""),
                    path=Path(row.get("path", row.get("file", ""))),
                    status=row.get("status", ""),
                    err=row.get("err", row.get("error", "")),
                )
            )
    return records


_STORAGE_SIZE_PATTERN = re.compile(r"^\s*(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>[a-zA-Z]*)\s*$")
_STORAGE_SIZE_UNITS = {
    "": 1,
    "B": 1,
    "K": 1_000,
    "KB": 1_000,
    "M": 1_000_000,
    "MB": 1_000_000,
    "G": 1_000_000_000,
    "GB": 1_000_000_000,
    "T": 1_000_000_000_000,
    "TB": 1_000_000_000_000,
    "KIB": 1024,
    "MIB": 1024**2,
    "GIB": 1024**3,
    "TIB": 1024**4,
}


def parse_storage_size(raw: str) -> int:
    match = _STORAGE_SIZE_PATTERN.match(raw)
    if match is None:
        raise DownloadError(f"invalid storage size: {raw}")

    unit = match.group("unit").upper()
    multiplier = _STORAGE_SIZE_UNITS.get(unit)
    if multiplier is None:
        raise DownloadError(f"invalid storage size unit: {match.group('unit')}")

    size = int(float(match.group("number")) * multiplier)
    if size < 1:
        raise DownloadError("storage size must be greater than 0")
    return size


class TransferCacheStorage:
    def __init__(self, limit_bytes: int):
        if limit_bytes < 1:
            raise DownloadError("transfer cache storage limit must be greater than 0")
        self.limit_bytes = limit_bytes
        self._condition = threading.Condition()
        self._cached_bytes = 0
        self._tracked_files: dict[Path, int] = {}
        self._failure: Exception | None = None

    @property
    def current_bytes(self) -> int:
        with self._condition:
            return self._cached_bytes

    def wait_for_space(self) -> None:
        with self._condition:
            if self._failure is not None:
                raise DownloadError(f"transfer cache storage stopped: {self._failure}") from self._failure
            while self._cached_bytes > self.limit_bytes:
                self._condition.wait()
                if self._failure is not None:
                    raise DownloadError(f"transfer cache storage stopped: {self._failure}") from self._failure

    def track_result(self, result: DownloadResult) -> None:
        with self._condition:
            for file_path in result.files:
                if not file_path.is_file():
                    continue
                size = file_path.stat().st_size
                if file_path in self._tracked_files:
                    self._cached_bytes -= self._tracked_files[file_path]
                self._tracked_files[file_path] = size
                self._cached_bytes += size
            self._condition.notify_all()

    def release_result(self, result: DownloadResult) -> None:
        with self._condition:
            for file_path in result.files:
                self._cached_bytes -= self._tracked_files.pop(file_path, 0)
            self._condition.notify_all()

    def abort(self, error: Exception) -> None:
        with self._condition:
            self._failure = error
            self._condition.notify_all()


def cleanup_download_result(result: DownloadResult, base_dir: Path) -> None:
    base_dir = base_dir.resolve()
    for file_path in result.files:
        try:
            resolved_file = file_path.resolve()
            resolved_file.relative_to(base_dir)
        except ValueError:
            continue

        resolved_file.unlink(missing_ok=True)
        _prune_empty_parents(resolved_file.parent, base_dir)


def _prune_empty_parents(path: Path, base_dir: Path) -> None:
    current = path
    while current != base_dir and base_dir in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _snapshot_files(directory: Path) -> dict[Path, tuple[int, int]]:
    if not directory.exists():
        return {}
    return {
        path: (path.stat().st_mtime_ns, path.stat().st_size)
        for path in directory.rglob("*")
        if path.is_file()
    }


def _changed_files(before: dict[Path, tuple[int, int]], after: dict[Path, tuple[int, int]]) -> list[Path]:
    return sorted(
        [path for path, marker in after.items() if before.get(path) != marker],
        key=lambda path: str(path),
    )


def _resolve_download_source(source: str, base_dir: Path | None = None) -> str:
    source = source.strip()
    path = Path(source)
    if path.suffix.lower() == ".torrent" and not path.is_absolute() and base_dir is not None:
        candidate = base_dir / path
        if candidate.exists():
            return str(candidate)
    return source


def _source_from_row(row: dict[str, str], column: str, base_dir: Path) -> DownloadSource | None:
    raw_input = row.get(column, "").strip()
    if not raw_input:
        return None
    resolved = _resolve_download_source(raw_input, base_dir)
    return DownloadSource(
        keyword=row.get("keyword", ""),
        origin=row.get("origin", ""),
        input=resolved,
        source=resolved,
    )


def _select_download_column(fieldnames: list[str] | None, column: str) -> str:
    if fieldnames is None:
        raise DownloadError(f"missing column: {column}")
    if column in fieldnames:
        return column
    if column == "magnet":
        for candidate in ("result", "input"):
            if candidate in fieldnames:
                return candidate
    raise DownloadError(f"missing column: {column}")


class Aria2cDownloader:
    def __init__(self, runner: Runner | None = None, verbose: bool = False):
        self.runner = runner or subprocess.run
        self.verbose = verbose

    def download(self, magnet: str, output_dir: Path) -> DownloadResult:
        magnet = magnet.strip()
        if not magnet:
            raise DownloadError("magnet link must be non-empty")

        output_dir.mkdir(parents=True, exist_ok=True)
        before = _snapshot_files(output_dir)
        command = [
            "aria2c",
            "--dir",
            str(output_dir),
            "--seed-time=0",
            magnet,
        ]
        if not self.verbose:
            command.insert(-1, "--summary-interval=0")
        try:
            completed = self.runner(command, capture_output=not self.verbose, text=True, check=False)
        except FileNotFoundError as error:
            raise DownloadError("aria2c executable not found") from error

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            message = f"aria2c failed with exit code {completed.returncode}"
            if detail:
                message = f"{message}: {detail}"
            raise DownloadError(message)

        after = _snapshot_files(output_dir)
        return DownloadResult(magnet=magnet, files=_changed_files(before, after))


def _validate_headers(fieldnames: list[str] | None, column: str) -> None:
    if fieldnames is None or column not in fieldnames:
        raise DownloadError(f"missing column: {column}")
    seen: set[str] = set()
    for fieldname in fieldnames:
        if fieldname in seen:
            raise DownloadError(f"duplicate CSV header: {fieldname}")
        seen.add(fieldname)


def _failure_message(failures: list[tuple[str, Exception]]) -> str:
    details = "; ".join(f"{source}: {error}" for source, error in failures)
    return f"{len(failures)} download(s) failed: {details}"


class _ResultRecorder:
    def __init__(self, result_path: Path, base_dir: Path):
        self.result_path = result_path
        self.base_dir = base_dir
        self.result_path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not self.result_path.exists() or self.result_path.stat().st_size == 0
        self._file = self.result_path.open("a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=RESULT_FIELDS)
        if write_header:
            self._writer.writeheader()
        self._file.flush()

    def __enter__(self) -> _ResultRecorder:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self._file.close()

    def record_success(self, result: DownloadResult) -> None:
        for row in _download_rows(self.base_dir, result=result):
            self._write(row)

    def record_failure(self, source: DownloadSource, error: Exception) -> None:
        for row in _download_rows(
            self.base_dir,
            failure=(source.input, error),
            failure_keyword=source.keyword,
            failure_origin=source.origin,
        ):
            self._write(row)

    def _write(self, row: dict[str, str]) -> None:
        self._writer.writerow(row)
        self._file.flush()


def run_download_batch(
    input_path: Path,
    column: str,
    output_dir: Path,
    downloader: Any,
    download_concurrency: int = 1,
    on_result: ResultCallback | None = None,
    before_download: BeforeDownloadCallback | None = None,
    result_path: Path | None = None,
    raise_on_failure: bool = True,
    skip_sources: set[str] | None = None,
) -> tuple[list[DownloadResult], list[tuple[str, Exception]]]:
    if download_concurrency < 1:
        raise DownloadError("download_concurrency must be at least 1")

    with input_path.open(newline="", encoding="utf-8-sig") as input_file:
        reader = csv.DictReader(input_file)
        selected_column = _select_download_column(reader.fieldnames, column)
        _validate_headers(reader.fieldnames, selected_column)

        skipped = skip_sources or set()
        download_meta_path = result_path or output_dir / DOWNLOAD_RECORD_FILENAME
        existing_records = load_download_records(download_meta_path)
        _cleanup_record_paths(existing_records, output_dir, "failed")
        skipped = skipped | _successful_record_inputs(existing_records)
        sources: list[DownloadSource] = []
        for row in reader:
            if row.get("status") == "failed":
                continue
            source = _source_from_row(row, selected_column, input_path.parent)
            if source is not None and not _source_is_skipped(source.input, skipped):
                sources.append(source)

        results: list[DownloadResult] = []
        failures: list[tuple[str, Exception]] = []

        recorder_context = _ResultRecorder(download_meta_path, output_dir)
        if recorder_context is None:
            results, failures = _run_downloads(
                sources,
                output_dir,
                downloader,
                download_concurrency,
                on_result,
                before_download,
                failures,
                results,
            )
        else:
            with recorder_context as recorder:
                results, failures = _run_downloads(
                    sources,
                    output_dir,
                    downloader,
                    download_concurrency,
                    on_result,
                    before_download,
                    failures,
                    results,
                    recorder,
                )

    if failures and raise_on_failure:
        raise DownloadError(_failure_message(failures), failures=failures)
    return results, failures


def _run_downloads(
    sources: list[DownloadSource],
    output_dir: Path,
    downloader: Any,
    download_concurrency: int,
    on_result: ResultCallback | None,
    before_download: BeforeDownloadCallback | None,
    failures: list[tuple[str, Exception]],
    results: list[DownloadResult],
    recorder: _ResultRecorder | None = None,
) -> tuple[list[DownloadResult], list[tuple[str, Exception]]]:
    if download_concurrency == 1:
        for source in sources:
            try:
                if before_download is not None:
                    before_download()
                result = downloader.download(source.source, output_dir)
                result = DownloadResult(
                    magnet=result.magnet,
                    files=result.files,
                    keyword=source.keyword,
                    origin=source.origin,
                    input=source.input,
                )
            except Exception as error:
                failures.append((source.input, error))
                if recorder is not None:
                    recorder.record_failure(source, error)
                continue
            results.append(result)
            if recorder is not None:
                recorder.record_success(result)
            if on_result is not None:
                on_result(result)
    else:

        def download_source(source: DownloadSource) -> DownloadResult:
            if before_download is not None:
                before_download()
            result = downloader.download(source.source, output_dir)
            return DownloadResult(
                magnet=result.magnet,
                files=result.files,
                keyword=source.keyword,
                origin=source.origin,
                input=source.input,
            )

        with ThreadPoolExecutor(max_workers=download_concurrency) as executor:
            futures = {executor.submit(download_source, source): (index, source) for index, source in enumerate(sources)}
            indexed_results: list[tuple[int, DownloadResult]] = []
            for future in as_completed(futures):
                index, source = futures[future]
                try:
                    result = future.result()
                except Exception as error:
                    failures.append((source.input, error))
                    if recorder is not None:
                        recorder.record_failure(source, error)
                    continue
                indexed_results.append((index, result))
                if recorder is not None:
                    recorder.record_success(result)
                if on_result is not None:
                    on_result(result)
            results = [result for _, result in sorted(indexed_results, key=lambda item: item[0])]

    return results, failures
