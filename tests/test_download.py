import subprocess
import threading
import time
from pathlib import Path

import pytest

from magnet_search.download import Aria2cDownloader, DownloadError, run_download_batch


class FakeRunner:
    def __init__(self, output_dir: Path, returncode: int = 0):
        self.output_dir = output_dir
        self.returncode = returncode
        self.calls = []

    def __call__(self, command, capture_output, text, check):
        self.calls.append(
            {
                "command": command,
                "capture_output": capture_output,
                "text": text,
                "check": check,
            }
        )
        if self.returncode == 0:
            (self.output_dir / f"download-{len(self.calls)}.bin").write_text("payload", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")
        return subprocess.CompletedProcess(command, self.returncode, stdout="partial", stderr="failed")


class TrackingDownloader:
    def __init__(self, delay: float = 0.01):
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self.calls = []
        self.lock = threading.Lock()

    def download(self, source: str, output_dir: Path):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append(source)
        try:
            time.sleep(self.delay)
            return type("Result", (), {"magnet": source, "files": [output_dir / f"{source}.bin"]})()
        finally:
            with self.lock:
                self.active -= 1


def test_aria2c_downloader_builds_command_and_returns_downloaded_files(tmp_path: Path):
    runner = FakeRunner(tmp_path)
    downloader = Aria2cDownloader(runner=runner)

    result = downloader.download("magnet:?xt=urn:btih:sample", tmp_path)

    assert runner.calls[0]["command"] == [
        "aria2c",
        "--dir",
        str(tmp_path),
        "--seed-time=0",
        "--summary-interval=0",
        "magnet:?xt=urn:btih:sample",
    ]
    assert result.magnet == "magnet:?xt=urn:btih:sample"
    assert [path.name for path in result.files] == ["download-1.bin"]


def test_aria2c_downloader_accepts_torrent_file_path(tmp_path: Path):
    torrent_path = tmp_path / "movie.torrent"
    torrent_path.write_text("torrent payload", encoding="utf-8")
    runner = FakeRunner(tmp_path)
    downloader = Aria2cDownloader(runner=runner)

    result = downloader.download(str(torrent_path), tmp_path)

    assert runner.calls[0]["command"][-1] == str(torrent_path)
    assert result.magnet == str(torrent_path)


def test_aria2c_downloader_rejects_empty_magnet_without_running(tmp_path: Path):
    runner = FakeRunner(tmp_path)
    downloader = Aria2cDownloader(runner=runner)

    with pytest.raises(DownloadError, match="magnet link must be non-empty"):
        downloader.download("   ", tmp_path)

    assert runner.calls == []


def test_aria2c_downloader_raises_when_command_fails(tmp_path: Path):
    runner = FakeRunner(tmp_path, returncode=2)
    downloader = Aria2cDownloader(runner=runner)

    with pytest.raises(DownloadError, match="aria2c failed with exit code 2"):
        downloader.download("magnet:?xt=urn:btih:sample", tmp_path)


def test_run_download_batch_uses_default_magnet_column(tmp_path: Path):
    input_path = tmp_path / "input.csv"
    output_dir = tmp_path / "downloads"
    input_path.write_text("magnet\nmagnet:?xt=urn:btih:first\nmagnet:?xt=urn:btih:second\n", encoding="utf-8")
    downloader = Aria2cDownloader(runner=FakeRunner(output_dir))

    results = run_download_batch(input_path, column="magnet", output_dir=output_dir, downloader=downloader)

    assert [result.magnet for result in results] == [
        "magnet:?xt=urn:btih:first",
        "magnet:?xt=urn:btih:second",
    ]


def test_run_download_batch_uses_custom_column_and_skips_blank_rows(tmp_path: Path):
    input_path = tmp_path / "input.csv"
    output_dir = tmp_path / "downloads"
    input_path.write_text("link\nmagnet:?xt=urn:btih:first\n   \nmagnet:?xt=urn:btih:second\n", encoding="utf-8")
    downloader = Aria2cDownloader(runner=FakeRunner(output_dir))

    results = run_download_batch(input_path, column="link", output_dir=output_dir, downloader=downloader)

    assert [result.magnet for result in results] == [
        "magnet:?xt=urn:btih:first",
        "magnet:?xt=urn:btih:second",
    ]


def test_run_download_batch_resolves_relative_torrent_paths_from_csv_directory(tmp_path: Path):
    csv_dir = tmp_path / "lists"
    csv_dir.mkdir()
    torrent_dir = csv_dir / "torrents"
    torrent_dir.mkdir()
    torrent_path = torrent_dir / "movie.torrent"
    torrent_path.write_text("torrent payload", encoding="utf-8")
    input_path = csv_dir / "input.csv"
    output_dir = tmp_path / "downloads"
    input_path.write_text("source\ntorrents/movie.torrent\n", encoding="utf-8")
    runner = FakeRunner(output_dir)
    downloader = Aria2cDownloader(runner=runner)

    results = run_download_batch(input_path, column="source", output_dir=output_dir, downloader=downloader)

    assert runner.calls[0]["command"][-1] == str(torrent_path)
    assert results[0].magnet == str(torrent_path)


def test_run_download_batch_respects_download_concurrency(tmp_path: Path):
    input_path = tmp_path / "input.csv"
    output_dir = tmp_path / "downloads"
    input_path.write_text(
        "magnet\nfirst\nsecond\nthird\nfourth\n",
        encoding="utf-8",
    )
    downloader = TrackingDownloader()

    results = run_download_batch(
        input_path,
        column="magnet",
        output_dir=output_dir,
        downloader=downloader,
        download_concurrency=2,
    )

    assert len(results) == 4
    assert downloader.max_active == 2


def test_run_download_batch_aggregates_download_failures(tmp_path: Path):
    class FailingDownloader:
        def download(self, source: str, output_dir: Path):
            if source in {"bad-one", "bad-two"}:
                raise DownloadError(f"{source} failed")
            return type("Result", (), {"magnet": source, "files": []})()

    input_path = tmp_path / "input.csv"
    input_path.write_text("magnet\ngood\nbad-one\nbad-two\n", encoding="utf-8")

    with pytest.raises(DownloadError) as error:
        run_download_batch(
            input_path,
            column="magnet",
            output_dir=tmp_path / "downloads",
            downloader=FailingDownloader(),
            download_concurrency=2,
        )

    message = str(error.value)
    assert "2 download(s) failed" in message
    assert "bad-one failed" in message
    assert "bad-two failed" in message


def test_run_download_batch_rejects_missing_column(tmp_path: Path):
    input_path = tmp_path / "input.csv"
    input_path.write_text("link\nmagnet:?xt=urn:btih:first\n", encoding="utf-8")
    downloader = Aria2cDownloader(runner=FakeRunner(tmp_path / "downloads"))

    with pytest.raises(DownloadError, match="missing column: magnet"):
        run_download_batch(input_path, column="magnet", output_dir=tmp_path / "downloads", downloader=downloader)
