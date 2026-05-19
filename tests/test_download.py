import subprocess
import threading
import time
import csv
from pathlib import Path

import pytest

from magnet_search.download import (
    Aria2cDownloader,
    DOWNLOAD_RECORD_FILENAME,
    DownloadError,
    DownloadResult,
    TransferCacheStorage,
    append_download_record,
    load_download_records,
    parse_storage_size,
    run_download_batch,
)


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
    assert runner.calls[0]["capture_output"] is True
    assert result.magnet == "magnet:?xt=urn:btih:sample"
    assert [path.name for path in result.files] == ["download-1.bin"]


def test_aria2c_downloader_verbose_streams_aria2c_output(tmp_path: Path):
    runner = FakeRunner(tmp_path)
    downloader = Aria2cDownloader(runner=runner, verbose=True)

    downloader.download("magnet:?xt=urn:btih:sample", tmp_path)

    assert runner.calls[0]["capture_output"] is False
    assert "--summary-interval=0" not in runner.calls[0]["command"]


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

    results, _ = run_download_batch(input_path, column="magnet", output_dir=output_dir, downloader=downloader)

    assert [result.magnet for result in results] == [
        "magnet:?xt=urn:btih:first",
        "magnet:?xt=urn:btih:second",
    ]


def test_run_download_batch_uses_custom_column_and_skips_blank_rows(tmp_path: Path):
    input_path = tmp_path / "input.csv"
    output_dir = tmp_path / "downloads"
    input_path.write_text("link\nmagnet:?xt=urn:btih:first\n   \nmagnet:?xt=urn:btih:second\n", encoding="utf-8")
    downloader = Aria2cDownloader(runner=FakeRunner(output_dir))

    results, _ = run_download_batch(input_path, column="link", output_dir=output_dir, downloader=downloader)

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

    results, _ = run_download_batch(input_path, column="source", output_dir=output_dir, downloader=downloader)

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

    results, _ = run_download_batch(
        input_path,
        column="magnet",
        output_dir=output_dir,
        downloader=downloader,
        download_concurrency=2,
    )

    assert len(results) == 4
    assert downloader.max_active == 2


def test_run_download_batch_records_results_as_each_item_finishes(tmp_path: Path):
    input_path = tmp_path / "input.csv"
    output_dir = tmp_path / "downloads"
    result_path = tmp_path / "results" / "download-results.csv"
    input_path.write_text("magnet\nfirst\nsecond\nbad\n", encoding="utf-8")
    observed_rows: list[list[dict[str, str]]] = []

    class ObservingDownloader:
        def download(self, source: str, output_dir: Path):
            if source == "bad":
                raise DownloadError("bad failed")
            file_path = output_dir / f"{source}.bin"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text("payload", encoding="utf-8")
            if result_path.exists():
                with result_path.open(newline="", encoding="utf-8") as result_file:
                    observed_rows.append(list(csv.DictReader(result_file)))
            return DownloadResult(magnet=source, files=[file_path])

    with pytest.raises(DownloadError, match="bad failed"):
        run_download_batch(
            input_path,
            column="magnet",
            output_dir=output_dir,
            downloader=ObservingDownloader(),
            result_path=result_path,
        )

    assert result_path.exists()
    assert observed_rows == [
        [],
        [
            {
                "keyword": "",
                "origin": "",
                "input": "first",
                "item": "first.bin",
                "path": str(output_dir / "first.bin"),
                "status": "success",
                "err": "",
            }
        ],
    ]

    with result_path.open(newline="", encoding="utf-8") as result_file:
        rows = list(csv.DictReader(result_file))

    assert rows == [
        {
            "keyword": "",
            "origin": "",
            "input": "first",
            "item": "first.bin",
            "path": str(output_dir / "first.bin"),
            "status": "success",
            "err": "",
        },
        {
            "keyword": "",
            "origin": "",
            "input": "second",
            "item": "second.bin",
            "path": str(output_dir / "second.bin"),
            "status": "success",
            "err": "",
        },
        {"keyword": "", "origin": "", "input": "bad", "item": "", "path": "", "status": "failed", "err": "bad failed"},
    ]


def test_append_download_record_persists_download_meta_row(tmp_path: Path):
    output_dir = tmp_path / "downloads"
    file_path = output_dir / "movie.mp4"
    file_path.parent.mkdir()
    file_path.write_text("payload", encoding="utf-8")

    append_download_record(output_dir, DownloadResult(magnet="first", files=[file_path]))

    record_path = output_dir / DOWNLOAD_RECORD_FILENAME
    with record_path.open(newline="", encoding="utf-8") as record_file:
        rows = list(csv.DictReader(record_file))

    assert rows == [
        {
            "keyword": "",
            "origin": "",
            "input": "first",
            "item": "movie.mp4",
            "path": str(file_path),
            "status": "success",
            "err": "",
        }
    ]
    assert load_download_records(output_dir)[0].input == "first"


def test_run_download_batch_propagates_search_meta_columns(tmp_path: Path):
    input_path = tmp_path / "search.csv"
    output_dir = tmp_path / "downloads"
    input_path.write_text(
        "keyword,origin,result,status,err\n"
        "Sample Movie,archive,magnet:?xt=urn:btih:first,success,\n",
        encoding="utf-8",
    )
    downloader = Aria2cDownloader(runner=FakeRunner(output_dir))

    run_download_batch(input_path, column="result", output_dir=output_dir, downloader=downloader)

    records = load_download_records(output_dir)
    assert records[0].keyword == "Sample Movie"
    assert records[0].origin == "archive"
    assert records[0].input == "magnet:?xt=urn:btih:first"


def test_run_download_batch_writes_download_cache_record(tmp_path: Path):
    input_path = tmp_path / "input.csv"
    output_dir = tmp_path / "downloads"
    input_path.write_text("magnet\nfirst\n", encoding="utf-8")
    downloader = Aria2cDownloader(runner=FakeRunner(output_dir))

    run_download_batch(input_path, column="magnet", output_dir=output_dir, downloader=downloader)

    records = load_download_records(output_dir)
    assert len(records) == 1
    assert records[0].input == "first"
    assert records[0].path.name == "download-1.bin"


def test_run_download_batch_skips_successful_records_and_cleans_failed_residue(tmp_path: Path):
    input_path = tmp_path / "input.csv"
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()
    result_path = output_dir / DOWNLOAD_RECORD_FILENAME
    stale_file = output_dir / "partial.bin"
    stale_file.write_text("partial", encoding="utf-8")
    input_path.write_text("magnet\nfirst\nbad\nsecond\n", encoding="utf-8")
    result_path.write_text(
        "keyword,origin,input,item,path,status,err\n"
        f",,first,first.bin,{output_dir / 'first.bin'},success,\n"
        f",,bad,partial.bin,{stale_file},failed,previous failure\n",
        encoding="utf-8",
    )
    downloader = TrackingDownloader()

    results, _ = run_download_batch(input_path, column="magnet", output_dir=output_dir, downloader=downloader)

    assert downloader.calls == ["bad", "second"]
    assert [result.magnet for result in results] == ["bad", "second"]
    assert not stale_file.exists()
    rows = list(csv.DictReader(result_path.open(encoding="utf-8")))
    assert [row["input"] for row in rows] == ["first", "bad", "bad", "second"]


def test_run_download_batch_skips_sources_already_handled_from_cache(tmp_path: Path):
    input_path = tmp_path / "input.csv"
    output_dir = tmp_path / "downloads"
    input_path.write_text("magnet\nfirst\nsecond\n", encoding="utf-8")
    downloader = TrackingDownloader()

    results, _ = run_download_batch(
        input_path,
        column="magnet",
        output_dir=output_dir,
        downloader=downloader,
        skip_sources={"first"},
    )

    assert downloader.calls == ["second"]
    assert [result.magnet for result in results] == ["second"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1024", 1024),
        ("500MB", 500_000_000),
        ("2GB", 2_000_000_000),
        ("1.5GiB", 1_610_612_736),
    ],
)
def test_parse_storage_size_accepts_bytes_and_units(raw: str, expected: int):
    assert parse_storage_size(raw) == expected


@pytest.mark.parametrize("raw", ["", "0", "-1MB", "tenGB", "1XB"])
def test_parse_storage_size_rejects_invalid_values(raw: str):
    with pytest.raises(DownloadError):
        parse_storage_size(raw)


def test_transfer_cache_storage_waits_until_tracked_result_is_released(tmp_path: Path):
    file_path = tmp_path / "movie.bin"
    file_path.write_text("payload", encoding="utf-8")
    result = DownloadResult(magnet="first", files=[file_path])
    cache = TransferCacheStorage(limit_bytes=3)
    cache.track_result(result)
    released: list[bool] = []

    thread = threading.Thread(target=lambda: (cache.wait_for_space(), released.append(True)))
    thread.start()
    time.sleep(0.05)

    assert released == []

    cache.release_result(result)
    thread.join(timeout=1)

    assert released == [True]


def test_run_download_batch_waits_for_transfer_cache_space_before_next_download(tmp_path: Path):
    input_path = tmp_path / "input.csv"
    output_dir = tmp_path / "downloads"
    input_path.write_text("magnet\nfirst\nsecond\n", encoding="utf-8")
    cache = TransferCacheStorage(limit_bytes=3)
    results_by_source: dict[str, DownloadResult] = {}
    errors: list[Exception] = []

    class WritingDownloader:
        def __init__(self):
            self.calls: list[str] = []
            self.lock = threading.Lock()

        def download(self, source: str, output_dir: Path):
            with self.lock:
                self.calls.append(source)
            file_path = output_dir / f"{source}.bin"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text("payload", encoding="utf-8")
            return DownloadResult(magnet=source, files=[file_path])

    downloader = WritingDownloader()

    def on_result(result: DownloadResult) -> None:
        results_by_source[result.magnet] = result
        cache.track_result(result)

    def run_batch() -> None:
        try:
            run_download_batch(
                input_path,
                column="magnet",
                output_dir=output_dir,
                downloader=downloader,
                download_concurrency=1,
                on_result=on_result,
                before_download=cache.wait_for_space,
            )
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=run_batch)
    thread.start()
    for _ in range(50):
        if "first" in results_by_source:
            break
        time.sleep(0.01)

    time.sleep(0.05)
    assert downloader.calls == ["first"]

    cache.release_result(results_by_source["first"])
    thread.join(timeout=1)

    assert errors == []
    assert downloader.calls == ["first", "second"]


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
