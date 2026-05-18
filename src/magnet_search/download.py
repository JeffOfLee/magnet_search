from __future__ import annotations

import csv
import json
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class DownloadError(RuntimeError):
    """Raised when a magnet download or download batch cannot complete."""


@dataclass(frozen=True)
class DownloadResult:
    magnet: str
    files: list[Path]


Runner = Callable[..., subprocess.CompletedProcess[str]]
ResultCallback = Callable[[DownloadResult], None]
BeforeDownloadCallback = Callable[[], None]
RESULT_FIELDS = ["source", "status", "files", "error"]


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
    def __init__(self, result_path: Path):
        self.result_path = result_path
        self.result_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.result_path.open("w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=RESULT_FIELDS)
        self._writer.writeheader()
        self._file.flush()

    def __enter__(self) -> _ResultRecorder:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self._file.close()

    def record_success(self, result: DownloadResult) -> None:
        self._write(
            {
                "source": result.magnet,
                "status": "success",
                "files": json.dumps([str(path) for path in result.files]),
                "error": "",
            }
        )

    def record_failure(self, source: str, error: Exception) -> None:
        self._write(
            {
                "source": source,
                "status": "failed",
                "files": "[]",
                "error": str(error),
            }
        )

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
) -> list[DownloadResult]:
    if download_concurrency < 1:
        raise DownloadError("download_concurrency must be at least 1")

    with input_path.open(newline="", encoding="utf-8-sig") as input_file:
        reader = csv.DictReader(input_file)
        _validate_headers(reader.fieldnames, column)

        sources = [
            _resolve_download_source(source, input_path.parent)
            for row in reader
            if (source := row.get(column, "")).strip()
        ]

        results: list[DownloadResult] = []
        failures: list[tuple[str, Exception]] = []

        recorder_context = _ResultRecorder(result_path) if result_path is not None else None
        if recorder_context is None:
            return _run_downloads(
                sources,
                output_dir,
                downloader,
                download_concurrency,
                on_result,
                before_download,
                failures,
                results,
            )

        with recorder_context as recorder:
            return _run_downloads(
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


def _run_downloads(
    sources: list[str],
    output_dir: Path,
    downloader: Any,
    download_concurrency: int,
    on_result: ResultCallback | None,
    before_download: BeforeDownloadCallback | None,
    failures: list[tuple[str, Exception]],
    results: list[DownloadResult],
    recorder: _ResultRecorder | None = None,
) -> list[DownloadResult]:
    if download_concurrency == 1:
        for source in sources:
            try:
                if before_download is not None:
                    before_download()
                result = downloader.download(source, output_dir)
            except Exception as error:
                failures.append((source, error))
                if recorder is not None:
                    recorder.record_failure(source, error)
                continue
            results.append(result)
            if recorder is not None:
                recorder.record_success(result)
            if on_result is not None:
                on_result(result)
    else:

        def download_source(source: str) -> DownloadResult:
            if before_download is not None:
                before_download()
            return downloader.download(source, output_dir)

        with ThreadPoolExecutor(max_workers=download_concurrency) as executor:
            futures = {executor.submit(download_source, source): (index, source) for index, source in enumerate(sources)}
            indexed_results: list[tuple[int, DownloadResult]] = []
            for future in as_completed(futures):
                index, source = futures[future]
                try:
                    result = future.result()
                except Exception as error:
                    failures.append((source, error))
                    if recorder is not None:
                        recorder.record_failure(source, error)
                    continue
                indexed_results.append((index, result))
                if recorder is not None:
                    recorder.record_success(result)
                if on_result is not None:
                    on_result(result)
            results = [result for _, result in sorted(indexed_results, key=lambda item: item[0])]

    if failures:
        raise DownloadError(_failure_message(failures))
    return results
