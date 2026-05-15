from __future__ import annotations

import csv
import subprocess
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


def run_download_batch(
    input_path: Path,
    column: str,
    output_dir: Path,
    downloader: Any,
) -> list[DownloadResult]:
    with input_path.open(newline="", encoding="utf-8-sig") as input_file:
        reader = csv.DictReader(input_file)
        _validate_headers(reader.fieldnames, column)

        results: list[DownloadResult] = []
        for row in reader:
            source = row.get(column, "")
            if not source.strip():
                continue
            results.append(downloader.download(_resolve_download_source(source, input_path.parent), output_dir))
        return results
