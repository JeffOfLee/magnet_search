from __future__ import annotations

import csv
import subprocess
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
    def __init__(self, runner: Runner | None = None):
        self.runner = runner or subprocess.run

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
            "--summary-interval=0",
            magnet,
        ]
        try:
            completed = self.runner(command, capture_output=True, text=True, check=False)
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


def run_download_batch(
    input_path: Path,
    column: str,
    output_dir: Path,
    downloader: Any,
    download_concurrency: int = 1,
    on_result: ResultCallback | None = None,
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

        if download_concurrency == 1:
            for source in sources:
                try:
                    result = downloader.download(source, output_dir)
                except Exception as error:
                    failures.append((source, error))
                    continue
                results.append(result)
                if on_result is not None:
                    on_result(result)
        else:
            with ThreadPoolExecutor(max_workers=download_concurrency) as executor:
                futures = {
                    executor.submit(downloader.download, source, output_dir): (index, source)
                    for index, source in enumerate(sources)
                }
                indexed_results: list[tuple[int, DownloadResult]] = []
                for future in as_completed(futures):
                    index, source = futures[future]
                    try:
                        result = future.result()
                    except Exception as error:
                        failures.append((source, error))
                        continue
                    indexed_results.append((index, result))
                    if on_result is not None:
                        on_result(result)
                results = [result for _, result in sorted(indexed_results, key=lambda item: item[0])]

        if failures:
            raise DownloadError(_failure_message(failures))
        return results
