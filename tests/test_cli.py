import csv
import json
import threading
import time
import tomllib

import pytest
from typer.testing import CliRunner

from magnet_search import cli
from magnet_search.config import ConfigError
from magnet_search.download import DOWNLOAD_RECORD_FILENAME, DownloadError
from magnet_search.qbittorrent import QbittorrentDownloadStatus
from magnet_search.storage import UploadConfigError
from magnet_search.models import AllProvidersFailed, ProviderWarning, SearchResult


runner = CliRunner()


class FakeService:
    def search(self, query: str, limit: int):
        return (
            [
                SearchResult(
                    query=query,
                    title="Sample Result",
                    magnet="magnet:?xt=urn:btih:sample",
                    source="test",
                    score=1.25,
                    url="https://example.invalid/sample",
                )
            ],
            [],
        )


class WarningService:
    def search(self, query: str, limit: int):
        return (
            [
                SearchResult(
                    query=query,
                    title="Sample Result",
                    magnet="magnet:?xt=urn:btih:sample",
                    source="test",
                )
            ],
            [ProviderWarning(provider="test-provider", message="temporary issue")],
        )


class FailingService:
    def search(self, query: str, limit: int):
        raise AllProvidersFailed("all providers failed")


class FakeDownloader:
    def __init__(self):
        self.calls = []

    def download(self, magnet, output_dir):
        self.calls.append((magnet, output_dir))
        return cli.DownloadResult(magnet=magnet.strip(), files=[output_dir / "movie.mp4"])


class FakeUploader:
    def __init__(self):
        self.calls = []

    def upload_files(self, files, base_dir):
        self.calls.append((files, base_dir))
        return ["s3://my-bucket/movie.mp4"]


class TrackingUploader:
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.calls = []
        self.lock = threading.Lock()

    def upload_files(self, files, base_dir):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append((files, base_dir))
        try:
            time.sleep(0.01)
            return [f"s3://my-bucket/{path.name}" for path in files]
        finally:
            with self.lock:
                self.active -= 1


def test_search_command_renders_table(monkeypatch):
    monkeypatch.setattr(cli, "build_search_service", lambda: FakeService())

    result = runner.invoke(cli.app, ["search", "sample movie", "--limit", "3"])

    assert result.exit_code == 0
    assert "Sample Result" in result.stdout
    assert "magnet:?xt=urn:btih:sample" in result.stdout


def test_search_command_renders_json(monkeypatch):
    monkeypatch.setattr(cli, "build_search_service", lambda: FakeService())

    result = runner.invoke(cli.app, ["search", "sample movie", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["query"] == "sample movie"
    assert payload[0]["title"] == "Sample Result"


def test_search_command_verbose_logs_to_stderr_without_breaking_json(monkeypatch):
    monkeypatch.setattr(cli, "build_search_service", lambda: FakeService())

    result = runner.invoke(cli.app, ["search", "sample movie", "--json", "--verbose"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["title"] == "Sample Result"
    assert "verbose search mode=single query=sample movie limit=3" in result.stderr
    assert "verbose search results=1 warnings=0" in result.stderr


def test_search_command_prints_warnings_to_stderr_with_parseable_json(monkeypatch):
    monkeypatch.setattr(cli, "build_search_service", lambda: WarningService())

    result = runner.invoke(cli.app, ["search", "sample movie", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload[0]["title"] == "Sample Result"
    assert "test-provider" in result.stderr
    assert "temporary issue" in result.stderr


def test_search_command_all_providers_failed_exits_1(monkeypatch):
    monkeypatch.setattr(cli, "build_search_service", lambda: FailingService())

    result = runner.invoke(cli.app, ["search", "sample movie"])

    assert result.exit_code == 1
    assert "all providers failed" in result.stderr
    assert "Traceback" not in result.output
    assert "Traceback" not in result.stderr


def test_search_command_writes_batch_csv_with_default_query_column(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "build_search_service", lambda: FakeService())
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "search-meta.csv"
    input_path.write_text("query\nsample movie\n", encoding="utf-8")

    result = runner.invoke(cli.app, ["search", str(input_path), "--search_meta", str(output_path)])

    assert result.exit_code == 0
    rows = list(csv.DictReader(output_path.open(encoding="utf-8")))
    assert rows == [
        {
            "keyword": "sample movie",
            "origin": "test",
            "result": "magnet:?xt=urn:btih:sample",
            "status": "success",
            "err": "",
        }
    ]
    assert "wrote" in result.stdout


def test_search_command_writes_batch_csv_with_custom_column(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "build_search_service", lambda: FakeService())
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"
    input_path.write_text("title\nsample movie\n", encoding="utf-8")

    result = runner.invoke(
        cli.app,
        ["search", str(input_path), "--column", "title", "--search-meta", str(output_path)],
    )

    assert result.exit_code == 0
    rows = list(csv.DictReader(output_path.open(encoding="utf-8")))
    assert rows[0]["keyword"] == "sample movie"
    assert rows[0]["origin"] == "test"


def test_search_command_verbose_logs_batch_routing(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "build_search_service", lambda: FakeService())
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"
    input_path.write_text("query\nsample movie\n", encoding="utf-8")

    result = runner.invoke(
        cli.app,
        ["search", str(input_path), "--search-meta", str(output_path), "--verbose"],
    )

    assert result.exit_code == 0
    assert "verbose search mode=batch" in result.stderr
    assert f"input={input_path}" in result.stderr
    assert f"search_meta={output_path}" in result.stderr


def test_search_command_batch_mode_writes_default_search_meta(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "build_search_service", lambda: FakeService())
    input_path = tmp_path / "input.csv"
    input_path.write_text("query\nsample movie\n", encoding="utf-8")

    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(cli.app, ["search", str(input_path)])
        rows = list(csv.DictReader(open(".search_record.csv", encoding="utf-8")))

    assert result.exit_code == 0
    assert rows[0]["keyword"] == "sample movie"


@pytest.mark.parametrize(
    "error",
    [
        ConfigError("bad config"),
        tomllib.TOMLDecodeError("bad config", "", 0),
        OSError("bad config"),
        RuntimeError("bad config"),
    ],
)
def test_search_command_build_failure_exits_1_without_traceback(monkeypatch, error):
    def raise_error():
        raise error

    monkeypatch.setattr(cli, "build_search_service", raise_error)

    result = runner.invoke(cli.app, ["search", "sample movie"])

    assert result.exit_code == 1
    assert "bad config" in result.stderr
    assert "Traceback" not in result.output
    assert "Traceback" not in result.stderr


def test_batch_command_writes_output_csv(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "build_search_service", lambda: FakeService())
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"
    input_path.write_text("title\nsample movie\n", encoding="utf-8")

    result = runner.invoke(
        cli.app,
        ["batch", str(input_path), "--column", "title", "--output", str(output_path)],
    )

    assert result.exit_code == 0
    rows = list(csv.DictReader(output_path.open(encoding="utf-8")))
    assert rows[0]["keyword"] == "sample movie"
    assert rows[0]["result"] == "magnet:?xt=urn:btih:sample"


def test_batch_command_records_provider_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "build_search_service", lambda: FailingService())
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"
    input_path.write_text("title\nsample movie\n", encoding="utf-8")

    result = runner.invoke(
        cli.app,
        ["batch", str(input_path), "--column", "title", "--output", str(output_path)],
    )

    assert result.exit_code == 0
    rows = list(csv.DictReader(output_path.open(encoding="utf-8")))
    assert rows[0]["status"] == "failed"
    assert rows[0]["err"] == "all providers failed"


def test_batch_command_deduplicates_repeated_warnings(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "build_search_service", lambda: WarningService())
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"
    input_path.write_text("title\nfirst\nsecond\n", encoding="utf-8")

    result = runner.invoke(
        cli.app,
        ["batch", str(input_path), "--column", "title", "--output", str(output_path)],
    )

    assert result.exit_code == 0
    assert result.stderr.count("test-provider") == 1
    assert result.stderr.count("temporary issue") == 1


def test_download_command_downloads_single_magnet(monkeypatch, tmp_path):
    downloader = FakeDownloader()
    monkeypatch.setattr(cli, "build_downloader", lambda verbose=False: downloader)

    result = runner.invoke(
        cli.app,
        ["download", "magnet:?xt=urn:btih:sample", "--storage", str(tmp_path / "downloads")],
    )

    assert result.exit_code == 0
    assert downloader.calls == [("magnet:?xt=urn:btih:sample", tmp_path / "downloads")]
    assert "downloaded 1 file(s)" in result.stdout


def test_download_command_rejects_output_for_single_download(tmp_path):
    result = runner.invoke(
        cli.app,
        ["download", "magnet:?xt=urn:btih:sample", "--output", str(tmp_path / "result.csv")],
    )

    assert result.exit_code == 1
    assert "--download-meta is only supported for CSV batch downloads" in result.stderr


def test_download_command_downloads_csv_batch_with_default_column(monkeypatch, tmp_path):
    downloader = FakeDownloader()
    monkeypatch.setattr(cli, "build_downloader", lambda verbose=False: downloader)
    input_path = tmp_path / "input.csv"
    input_path.write_text("magnet\nmagnet:?xt=urn:btih:first\nmagnet:?xt=urn:btih:second\n", encoding="utf-8")

    result = runner.invoke(cli.app, ["download", str(input_path), "--storage", str(tmp_path / "downloads")])

    assert result.exit_code == 0
    assert [call[0] for call in downloader.calls] == [
        "magnet:?xt=urn:btih:first",
        "magnet:?xt=urn:btih:second",
    ]
    assert "downloaded 2 item(s), 2 file(s)" in result.stdout


def test_download_command_writes_batch_result_csv(monkeypatch, tmp_path):
    downloader = FakeDownloader()
    monkeypatch.setattr(cli, "build_downloader", lambda verbose=False: downloader)
    input_path = tmp_path / "input.csv"
    result_path = tmp_path / "results" / "download-meta.csv"
    storage_path = tmp_path / "downloads"
    input_path.write_text("magnet\nmagnet:?xt=urn:btih:first\n", encoding="utf-8")

    result = runner.invoke(
        cli.app,
        [
            "download",
            str(input_path),
            "--storage",
            str(storage_path),
            "--download_meta",
            str(result_path),
        ],
    )

    assert result.exit_code == 0
    with result_path.open(newline="", encoding="utf-8") as result_file:
        rows = list(csv.DictReader(result_file))
    assert rows == [
        {
            "keyword": "",
            "origin": "",
            "input": "magnet:?xt=urn:btih:first",
            "item": "movie.mp4",
            "path": str(storage_path / "movie.mp4"),
            "status": "success",
            "err": "",
        }
    ]


def test_download_command_propagates_search_meta_to_download_meta(monkeypatch, tmp_path):
    downloader = FakeDownloader()
    monkeypatch.setattr(cli, "build_downloader", lambda verbose=False: downloader)
    input_path = tmp_path / "search-meta.csv"
    storage_path = tmp_path / "downloads"
    download_meta = tmp_path / "download-meta.csv"
    input_path.write_text(
        "keyword,origin,result,status,err\n"
        "sample movie,archive,magnet:?xt=urn:btih:first,success,\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        cli.app,
        [
            "download",
            str(input_path),
            "--storage",
            str(storage_path),
            "--download-meta",
            str(download_meta),
        ],
    )

    assert result.exit_code == 0
    assert downloader.calls == [("magnet:?xt=urn:btih:first", storage_path)]
    rows = list(csv.DictReader(download_meta.open(encoding="utf-8")))
    assert rows[0] == {
        "keyword": "sample movie",
        "origin": "archive",
        "input": "magnet:?xt=urn:btih:first",
        "item": "movie.mp4",
        "path": str(storage_path / "movie.mp4"),
        "status": "success",
        "err": "",
    }


def test_download_command_writes_default_download_meta(monkeypatch, tmp_path):
    downloader = FakeDownloader()
    monkeypatch.setattr(cli, "build_downloader", lambda verbose=False: downloader)
    input_path = tmp_path / "input.csv"
    storage_path = tmp_path / "downloads"
    input_path.write_text("magnet\nmagnet:?xt=urn:btih:first\n", encoding="utf-8")

    result = runner.invoke(cli.app, ["download", str(input_path), "--storage", str(storage_path)])

    assert result.exit_code == 0
    assert (storage_path / ".download_meta.csv").exists()


def test_download_command_downloads_csv_batch_with_custom_column(monkeypatch, tmp_path):
    downloader = FakeDownloader()
    monkeypatch.setattr(cli, "build_downloader", lambda verbose=False: downloader)
    input_path = tmp_path / "input.csv"
    input_path.write_text("link\nmagnet:?xt=urn:btih:first\n", encoding="utf-8")

    result = runner.invoke(
        cli.app,
        ["download", str(input_path), "--column", "link", "--storage", str(tmp_path / "downloads")],
    )

    assert result.exit_code == 0
    assert [call[0] for call in downloader.calls] == ["magnet:?xt=urn:btih:first"]


def test_download_command_passes_download_concurrency_to_batch(monkeypatch, tmp_path):
    captured = {}
    input_path = tmp_path / "input.csv"
    input_path.write_text("magnet\nmagnet:?xt=urn:btih:first\n", encoding="utf-8")
    monkeypatch.setattr(cli, "build_downloader", lambda verbose=False: FakeDownloader())

    def fake_run_download_batch(input_path, column, output_dir, downloader, download_concurrency, on_result=None, **kwargs):
        captured["download_concurrency"] = download_concurrency
        return [], []

    monkeypatch.setattr(cli, "run_download_batch", fake_run_download_batch)

    result = runner.invoke(
        cli.app,
        [
            "download",
            str(input_path),
            "--storage",
            str(tmp_path / "downloads"),
            "--download-concurrency",
            "3",
        ],
    )

    assert result.exit_code == 0
    assert captured["download_concurrency"] == 3


def test_download_command_skips_active_qbittorrent_sources(monkeypatch, tmp_path):
    captured = {}
    input_path = tmp_path / "input.csv"
    input_path.write_text("magnet\nactive\nfresh\n", encoding="utf-8")

    class ActiveDownloader(FakeDownloader):
        def active_download_sources(self):
            return {"active"}

        def download_sources_by_seed_priority(self, sources, output_dir, max_active):
            captured["sources"] = [source.input for source in sources]
            return [], []

    monkeypatch.setattr(cli, "QbittorrentDownloader", lambda **kwargs: ActiveDownloader())

    result = runner.invoke(
        cli.app,
        [
            "download",
            str(input_path),
            "--storage",
            str(tmp_path / "downloads"),
            "--engine",
            "qbittorrent",
        ],
    )

    assert result.exit_code == 0
    assert captured["sources"] == ["fresh"]


def test_download_command_records_qbittorrent_startup_results(monkeypatch, tmp_path):
    captured = {}
    input_path = tmp_path / "input.csv"
    storage_path = tmp_path / "downloads"
    file_path = storage_path / "recovered.mp4"
    input_path.write_text("magnet\nmagnet:?xt=urn:btih:recovered\nfresh\n", encoding="utf-8")
    file_path.parent.mkdir()
    file_path.write_text("video content", encoding="utf-8")

    class RecoveringDownloader(FakeDownloader):
        def startup_download_results(self, output_dir):
            return [
                cli.DownloadResult(
                    magnet="magnet:?xt=urn:btih:recovered",
                    files=[file_path],
                    input="magnet:?xt=urn:btih:recovered",
                )
            ]

        def download_sources_by_seed_priority(self, sources, output_dir, max_active):
            captured["sources"] = [source.input for source in sources]
            return [], []

    monkeypatch.setattr(cli, "QbittorrentDownloader", lambda **kwargs: RecoveringDownloader())

    result = runner.invoke(
        cli.app,
        [
            "download",
            str(input_path),
            "--storage",
            str(storage_path),
            "--engine",
            "qbittorrent",
        ],
    )

    assert result.exit_code == 0
    assert captured["sources"] == ["fresh"]
    rows = list(csv.DictReader((storage_path / DOWNLOAD_RECORD_FILENAME).open(encoding="utf-8")))
    assert rows == [
        {
            "keyword": "",
            "origin": "",
            "input": "magnet:?xt=urn:btih:recovered",
            "item": "recovered.mp4",
            "path": str(file_path),
            "status": "success",
            "err": "",
        }
    ]


def test_download_command_routes_qbittorrent_batch_to_seed_priority_scheduler(monkeypatch, tmp_path):
    input_path = tmp_path / "input.csv"
    input_path.write_text("magnet\nfirst\nsecond\nthird\n", encoding="utf-8")
    storage_path = tmp_path / "downloads"
    captured = {}

    class SeedPriorityDownloader:
        def __init__(self, **kwargs):
            pass

        def startup_download_results(self, storage):
            return []

        def active_download_sources(self):
            return set()

        def download_sources_by_seed_priority(self, sources, output_dir, max_active):
            captured["sources"] = sources
            captured["output_dir"] = output_dir
            captured["max_active"] = max_active
            return [
                cli.DownloadResult(magnet=source.source, files=[output_dir / f"{source.input}.bin"], input=source.input)
                for source in sources
            ], []

    monkeypatch.setattr(cli, "QbittorrentDownloader", SeedPriorityDownloader)
    monkeypatch.setattr(
        cli,
        "run_download_batch",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("generic batch should not run")),
    )

    result = runner.invoke(
        cli.app,
        [
            "download",
            str(input_path),
            "--storage",
            str(storage_path),
            "--engine",
            "qbittorrent",
            "--download-concurrency",
            "2",
        ],
    )

    assert result.exit_code == 0
    assert [source.input for source in captured["sources"]] == ["first", "second", "third"]
    assert captured["output_dir"] == storage_path
    assert captured["max_active"] == 2
    assert "downloaded 3 item(s), 3 file(s)" in result.stdout


def test_download_command_uploads_qbittorrent_seed_priority_batch_results(monkeypatch, tmp_path):
    input_path = tmp_path / "input.csv"
    input_path.write_text("magnet\nfirst\nsecond\n", encoding="utf-8")
    storage_path = tmp_path / "downloads"
    first_file = storage_path / "first.mp4"
    second_file = storage_path / "second.mp4"
    upload_path = tmp_path / "s3-upload.toml"
    upload_path.write_text('bucket = "my-bucket"\n', encoding="utf-8")
    uploader = FakeUploader()

    class SeedPriorityDownloader:
        def __init__(self, **kwargs):
            pass

        def startup_download_results(self, storage):
            return []

        def active_download_sources(self):
            return set()

        def download_sources_by_seed_priority(self, sources, output_dir, max_active):
            first_file.parent.mkdir(parents=True, exist_ok=True)
            first_file.write_text("payload", encoding="utf-8")
            second_file.write_text("payload", encoding="utf-8")
            return [
                cli.DownloadResult(magnet="first", files=[first_file], input="first"),
                cli.DownloadResult(magnet="second", files=[second_file], input="second"),
            ], []

    monkeypatch.setattr(cli, "QbittorrentDownloader", SeedPriorityDownloader)
    monkeypatch.setattr(cli, "build_s3_uploader", lambda path, key_gen="hash": uploader)

    result = runner.invoke(
        cli.app,
        [
            "download",
            str(input_path),
            "--storage",
            str(storage_path),
            "--engine",
            "qbittorrent",
            "--upload",
            str(upload_path),
        ],
    )

    assert result.exit_code == 0
    assert uploader.calls == [([first_file], storage_path), ([second_file], storage_path)]
    assert "downloaded 2 item(s), 2 file(s)" in result.stdout
    assert "uploaded 2 file(s)" in result.stdout


def test_qbittorrent_monitor_renders_current_downloads_once(monkeypatch):
    class MonitoringDownloader:
        def __init__(self, **kwargs):
            pass

        def list_downloads(self):
            return [
                QbittorrentDownloadStatus(
                    name="Ubuntu ISO",
                    state="downloading",
                    progress=0.5,
                    size=2_000_000,
                    downloaded=1_000_000,
                    download_speed=500_000,
                    upload_speed=10_000,
                    eta=60,
                    seeds=7,
                    peers=3,
                    save_path="/downloads",
                )
            ]

    monkeypatch.setattr(cli, "QbittorrentDownloader", MonitoringDownloader)

    result = runner.invoke(cli.app, ["qbittorrent-monitor", "--once"])

    assert result.exit_code == 0
    assert "Ubuntu ISO" in result.stdout
    assert "downloading" in result.stdout
    assert "50.0%" in result.stdout
    assert "500.0 KB/s" in result.stdout


def test_qbittorrent_monitor_renders_all_current_downloads_once(monkeypatch):
    class MonitoringDownloader:
        def __init__(self, **kwargs):
            pass

        def list_downloads(self):
            return [
                QbittorrentDownloadStatus(
                    name="Ubuntu ISO",
                    state="downloading",
                    progress=0.5,
                    size=2_000_000,
                    downloaded=1_000_000,
                    download_speed=500_000,
                    upload_speed=10_000,
                    eta=60,
                    seeds=7,
                    peers=3,
                    save_path="/downloads/linux",
                ),
                QbittorrentDownloadStatus(
                    name="Archive Pack",
                    state="stalledDL",
                    progress=0.25,
                    size=4_000_000,
                    downloaded=1_000_000,
                    download_speed=0,
                    upload_speed=0,
                    eta=0,
                    seeds=0,
                    peers=1,
                    save_path="/downloads/archive",
                ),
                QbittorrentDownloadStatus(
                    name="Finished Dataset",
                    state="uploading",
                    progress=1.0,
                    size=8_000_000,
                    downloaded=8_000_000,
                    download_speed=0,
                    upload_speed=20_000,
                    eta=0,
                    seeds=12,
                    peers=0,
                    save_path="/downloads/data",
                ),
            ]

    monkeypatch.setattr(cli, "QbittorrentDownloader", MonitoringDownloader)

    result = runner.invoke(cli.app, ["qbittorrent-monitor", "--once"])

    assert result.exit_code == 0
    assert "Ubuntu ISO" in result.stdout
    assert "downloading" in result.stdout
    assert "50.0%" in result.stdout
    assert "Archive Pack" in result.stdout
    assert "stalledDL" in result.stdout
    assert "25.0%" in result.stdout
    assert "Finished Dataset" in result.stdout
    assert "uploading" in result.stdout
    assert "100.0%" in result.stdout


def test_download_command_uses_upload_concurrency_for_batch_uploads(monkeypatch, tmp_path):
    input_path = tmp_path / "input.csv"
    input_path.write_text("magnet\nfirst\nsecond\nthird\nfourth\n", encoding="utf-8")
    upload_path = tmp_path / "s3-upload.toml"
    upload_path.write_text('bucket = "my-bucket"\n', encoding="utf-8")
    uploader = TrackingUploader()
    monkeypatch.setattr(cli, "build_downloader", lambda verbose=False: FakeDownloader())
    monkeypatch.setattr(cli, "build_s3_uploader", lambda path, key_gen="hash": uploader)

    def fake_run_download_batch(input_path, column, output_dir, downloader, download_concurrency, on_result=None, **kwargs):
        results = []
        for index in range(4):
            result = cli.DownloadResult(magnet=f"source-{index}", files=[output_dir / f"movie-{index}.mp4"])
            results.append(result)
            if on_result is not None:
                on_result(result)
        return results, []

    monkeypatch.setattr(cli, "run_download_batch", fake_run_download_batch)

    result = runner.invoke(
        cli.app,
        [
            "download",
            str(input_path),
            "--storage",
            str(tmp_path / "downloads"),
            "--upload",
            str(upload_path),
            "--upload-concurrency",
            "2",
        ],
    )

    assert result.exit_code == 0
    assert len(uploader.calls) == 4
    assert uploader.max_active == 2
    assert "uploaded 4 file(s)" in result.stdout


def test_download_command_passes_key_gen_to_s3_uploader(monkeypatch, tmp_path):
    input_path = tmp_path / "input.csv"
    input_path.write_text("magnet\nfirst\n", encoding="utf-8")
    upload_path = tmp_path / "s3-upload.toml"
    upload_path.write_text('bucket = "my-bucket"\n', encoding="utf-8")
    captured = {}
    monkeypatch.setattr(cli, "build_downloader", lambda verbose=False: FakeDownloader())

    def fake_build_s3_uploader(path, key_gen="hash"):
        captured["path"] = path
        captured["key_gen"] = key_gen
        return FakeUploader()

    monkeypatch.setattr(cli, "build_s3_uploader", fake_build_s3_uploader)

    result = runner.invoke(
        cli.app,
        [
            "download",
            str(input_path),
            "--storage",
            str(tmp_path / "downloads"),
            "--upload",
            str(upload_path),
            "--key-gen",
            "path",
        ],
    )

    assert result.exit_code == 0
    assert captured == {"path": upload_path, "key_gen": "path"}


def test_download_command_rejects_unknown_key_gen(tmp_path):
    upload_path = tmp_path / "s3-upload.toml"
    upload_path.write_text('bucket = "my-bucket"\n', encoding="utf-8")

    result = runner.invoke(
        cli.app,
        [
            "download",
            "magnet:?xt=urn:btih:sample",
            "--storage",
            str(tmp_path / "downloads"),
            "--upload",
            str(upload_path),
            "--key-gen",
            "bad",
        ],
    )

    assert result.exit_code == 1
    assert "--key-gen must be hash or path" in result.stderr


def test_download_command_rejects_transfer_cache_storage_without_upload(tmp_path):
    result = runner.invoke(
        cli.app,
        [
            "download",
            "magnet:?xt=urn:btih:sample",
            "--storage",
            str(tmp_path / "downloads"),
            "--transfer-cache-storage",
            "1GB",
        ],
    )

    assert result.exit_code == 1
    assert "--transfer-cache-storage requires --upload" in result.stderr


def test_download_command_passes_transfer_cache_gate_to_batch(monkeypatch, tmp_path):
    captured = {}
    input_path = tmp_path / "input.csv"
    input_path.write_text("magnet\nfirst\n", encoding="utf-8")
    upload_path = tmp_path / "s3-upload.toml"
    upload_path.write_text('bucket = "my-bucket"\n', encoding="utf-8")
    monkeypatch.setattr(cli, "build_downloader", lambda verbose=False: FakeDownloader())
    monkeypatch.setattr(cli, "build_s3_uploader", lambda path, key_gen="hash": FakeUploader())

    def fake_run_download_batch(
        input_path,
        column,
        output_dir,
        downloader,
        download_concurrency,
        on_result=None,
        before_download=None,
        raise_on_failure=None,
        **kwargs,
    ):
        captured["before_download"] = before_download
        return [], []

    monkeypatch.setattr(cli, "run_download_batch", fake_run_download_batch)

    result = runner.invoke(
        cli.app,
        [
            "download",
            str(input_path),
            "--storage",
            str(tmp_path / "downloads"),
            "--upload",
            str(upload_path),
            "--transfer-cache-storage",
            "1MB",
        ],
    )

    assert result.exit_code == 0
    assert callable(captured["before_download"])


def test_download_command_cleans_uploaded_files_when_transfer_cache_is_enabled(monkeypatch, tmp_path):
    input_path = tmp_path / "input.csv"
    input_path.write_text("magnet\nfirst\n", encoding="utf-8")
    storage_path = tmp_path / "downloads"
    upload_path = tmp_path / "s3-upload.toml"
    upload_path.write_text('bucket = "my-bucket"\n', encoding="utf-8")
    uploaded_file = storage_path / "movie.mp4"
    uploader = FakeUploader()
    monkeypatch.setattr(cli, "build_downloader", lambda verbose=False: FakeDownloader())
    monkeypatch.setattr(cli, "build_s3_uploader", lambda path, key_gen="hash": uploader)

    def fake_run_download_batch(
        input_path,
        column,
        output_dir,
        downloader,
        download_concurrency,
        on_result=None,
        before_download=None,
        raise_on_failure=None,
        **kwargs,
    ):
        if before_download is not None:
            before_download()
        uploaded_file.parent.mkdir(parents=True, exist_ok=True)
        uploaded_file.write_text("payload", encoding="utf-8")
        result = cli.DownloadResult(magnet="first", files=[uploaded_file])
        if on_result is not None:
            on_result(result)
        return [result], []

    monkeypatch.setattr(cli, "run_download_batch", fake_run_download_batch)

    result = runner.invoke(
        cli.app,
        [
            "download",
            str(input_path),
            "--storage",
            str(storage_path),
            "--upload",
            str(upload_path),
            "--transfer-cache-storage",
            "1MB",
        ],
    )

    assert result.exit_code == 0
    assert uploader.calls == [([uploaded_file], storage_path)]
    assert not uploaded_file.exists()
    assert "uploaded 1 file(s)" in result.stdout


def test_download_command_resumes_unuploaded_cache_before_new_batch_downloads(monkeypatch, tmp_path):
    input_path = tmp_path / "input.csv"
    input_path.write_text("magnet\ncached\nfresh\n", encoding="utf-8")
    storage_path = tmp_path / "downloads"
    cached_file = storage_path / "cached.mp4"
    cached_file.parent.mkdir()
    cached_file.write_text("payload", encoding="utf-8")
    record_path = storage_path / DOWNLOAD_RECORD_FILENAME
    record_path.write_text(
        "keyword,origin,input,item,path,status,err\n"
        f"cached keyword,archive,cached,cached.mp4,{cached_file},success,\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "upload-meta.csv"
    upload_path = tmp_path / "s3-upload.toml"
    upload_path.write_text('bucket = "my-bucket"\n', encoding="utf-8")
    downloader = FakeDownloader()
    uploader = FakeUploader()
    monkeypatch.setattr(cli, "build_downloader", lambda verbose=False: downloader)
    monkeypatch.setattr(cli, "build_s3_uploader", lambda path, key_gen="hash": uploader)

    result = runner.invoke(
        cli.app,
        [
            "download",
            str(input_path),
            "--storage",
            str(storage_path),
            "--upload_meta",
            str(output_path),
            "--upload",
            str(upload_path),
        ],
    )

    assert result.exit_code == 0
    assert downloader.calls == [("fresh", storage_path)]
    assert uploader.calls[0] == ([cached_file], storage_path)
    with output_path.open(newline="", encoding="utf-8") as result_file:
        rows = list(csv.DictReader(result_file))
    assert rows[0] == {
        "keyword": "cached keyword",
        "origin": "archive",
        "input": "cached",
        "item": "cached.mp4",
        "path": "cached.mp4",
        "s3_key": "movie.mp4",
        "status": "success",
        "err": "",
    }


def test_download_command_cleans_already_uploaded_cache_on_start_when_cleanup_enabled(monkeypatch, tmp_path):
    input_path = tmp_path / "input.csv"
    input_path.write_text("magnet\ncached\n", encoding="utf-8")
    storage_path = tmp_path / "downloads"
    cached_file = storage_path / "cached.mp4"
    cached_file.parent.mkdir()
    cached_file.write_text("payload", encoding="utf-8")
    (storage_path / DOWNLOAD_RECORD_FILENAME).write_text(
        "keyword,origin,input,item,path,status,err\n"
        f"cached keyword,archive,cached,cached.mp4,{cached_file},success,\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "upload-results.csv"
    output_path.write_text(
        "keyword,origin,input,item,path,s3_key,status,err\n"
        "cached keyword,archive,cached,cached.mp4,cached.mp4,s3://my-bucket/cached.mp4,success,\n",
        encoding="utf-8",
    )
    upload_path = tmp_path / "s3-upload.toml"
    upload_path.write_text('bucket = "my-bucket"\n', encoding="utf-8")
    uploader = FakeUploader()
    monkeypatch.setattr(cli, "build_downloader", lambda verbose=False: FakeDownloader())
    monkeypatch.setattr(cli, "build_s3_uploader", lambda path, key_gen="hash": uploader)

    result = runner.invoke(
        cli.app,
        [
            "download",
            str(input_path),
            "--storage",
            str(storage_path),
            "--upload_meta",
            str(output_path),
            "--upload",
            str(upload_path),
            "--transfer-cache-storage",
            "1MB",
        ],
    )

    assert result.exit_code == 0
    assert not cached_file.exists()
    assert uploader.calls == []
    assert (storage_path / DOWNLOAD_RECORD_FILENAME).exists()


def test_download_command_cleans_uploaded_cache_from_upload_meta_without_download_record(monkeypatch, tmp_path):
    input_path = tmp_path / "input.csv"
    input_path.write_text("magnet\nfresh\n", encoding="utf-8")
    storage_path = tmp_path / "downloads"
    uploaded_file = storage_path / "orphan.mp4"
    uploaded_file.parent.mkdir()
    uploaded_file.write_text("payload", encoding="utf-8")
    upload_meta = storage_path / ".upload_meta.csv"
    upload_meta.write_text(
        "keyword,origin,input,item,path,s3_key,status,err\n"
        "sample,archive,old,orphan.mp4,orphan.mp4,key,success,\n",
        encoding="utf-8",
    )
    upload_path = tmp_path / "s3-upload.toml"
    upload_path.write_text('bucket = "my-bucket"\n', encoding="utf-8")
    uploader = FakeUploader()
    monkeypatch.setattr(cli, "build_downloader", lambda verbose=False: FakeDownloader())
    monkeypatch.setattr(cli, "build_s3_uploader", lambda path, key_gen="hash": uploader)

    result = runner.invoke(
        cli.app,
        [
            "download",
            str(input_path),
            "--storage",
            str(storage_path),
            "--upload",
            str(upload_path),
        ],
    )

    assert result.exit_code == 0
    assert not uploaded_file.exists()


def test_download_command_writes_default_upload_meta_from_download_meta(monkeypatch, tmp_path):
    input_path = tmp_path / "download-meta.csv"
    storage_path = tmp_path / "downloads"
    downloaded_file = storage_path / "movie.mp4"
    downloaded_file.parent.mkdir()
    downloaded_file.write_text("payload", encoding="utf-8")
    input_path.write_text(
        "keyword,origin,input,item,path,status,err\n"
        f"sample movie,archive,magnet:?xt=urn:btih:first,movie.mp4,{downloaded_file},success,\n",
        encoding="utf-8",
    )
    upload_path = tmp_path / "s3-upload.toml"
    upload_path.write_text('bucket = "my-bucket"\n', encoding="utf-8")
    uploader = FakeUploader()
    monkeypatch.setattr(cli, "build_downloader", lambda verbose=False: FakeDownloader())
    monkeypatch.setattr(cli, "build_s3_uploader", lambda path, key_gen="hash": uploader)

    result = runner.invoke(
        cli.app,
        [
            "download",
            str(input_path),
            "--storage",
            str(storage_path),
            "--upload",
            str(upload_path),
        ],
    )

    assert result.exit_code == 0
    rows = list(csv.DictReader((storage_path / ".upload_meta.csv").open(encoding="utf-8")))
    assert rows[0] == {
        "keyword": "sample movie",
        "origin": "archive",
        "input": "magnet:?xt=urn:btih:first",
        "item": "movie.mp4",
        "path": "movie.mp4",
        "s3_key": "movie.mp4",
        "status": "success",
        "err": "",
    }


def test_download_command_uploads_when_upload_config_is_provided(monkeypatch, tmp_path):
    downloader = FakeDownloader()
    uploader = FakeUploader()
    upload_path = tmp_path / "s3-upload.toml"
    upload_path.write_text('bucket = "my-bucket"\n', encoding="utf-8")
    monkeypatch.setattr(cli, "build_downloader", lambda verbose=False: downloader)
    monkeypatch.setattr(cli, "build_s3_uploader", lambda path, key_gen="hash": uploader)

    result = runner.invoke(
        cli.app,
        [
            "download",
            "magnet:?xt=urn:btih:sample",
            "--storage",
            str(tmp_path / "downloads"),
            "--upload",
            str(upload_path),
        ],
    )

    assert result.exit_code == 0
    assert uploader.calls == [([tmp_path / "downloads" / "movie.mp4"], tmp_path / "downloads")]
    assert "uploaded 1 file(s)" in result.stdout


def test_download_command_verbose_logs_download_and_upload(monkeypatch, tmp_path):
    downloader = FakeDownloader()
    uploader = FakeUploader()
    upload_path = tmp_path / "s3-upload.toml"
    storage_path = tmp_path / "downloads"
    upload_path.write_text('bucket = "my-bucket"\n', encoding="utf-8")
    monkeypatch.setattr(cli, "build_downloader", lambda verbose=False: downloader)
    monkeypatch.setattr(cli, "build_s3_uploader", lambda path, key_gen="hash": uploader)

    result = runner.invoke(
        cli.app,
        [
            "download",
            "magnet:?xt=urn:btih:sample",
            "--storage",
            str(storage_path),
            "--upload",
            str(upload_path),
            "--verbose",
        ],
    )

    assert result.exit_code == 0
    assert "verbose download mode=single source=magnet:?xt=urn:btih:sample" in result.stderr
    assert f"storage={storage_path}" in result.stderr
    assert f"upload_config={upload_path}" in result.stderr
    assert "verbose download completed files=1" in result.stderr
    assert "verbose upload completed files=1" in result.stderr


def test_download_command_passes_verbose_to_downloader_factory(monkeypatch, tmp_path):
    captured = {}
    downloader = FakeDownloader()

    def fake_build_downloader(verbose=False):
        captured["verbose"] = verbose
        return downloader

    monkeypatch.setattr(cli, "build_downloader", fake_build_downloader)

    result = runner.invoke(
        cli.app,
        ["download", "magnet:?xt=urn:btih:sample", "--storage", str(tmp_path / "downloads"), "--verbose"],
    )

    assert result.exit_code == 0
    assert captured["verbose"] is True


def test_download_command_exits_cleanly_on_download_error(monkeypatch, tmp_path):
    class FailingDownloader:
        def download(self, magnet, output_dir):
            raise DownloadError("download failed")

    monkeypatch.setattr(cli, "build_downloader", lambda verbose=False: FailingDownloader())

    result = runner.invoke(
        cli.app,
        ["download", "magnet:?xt=urn:btih:sample", "--storage", str(tmp_path / "downloads")],
    )

    assert result.exit_code == 1
    assert "download failed" in result.stderr
    assert "Traceback" not in result.output
    assert "Traceback" not in result.stderr


def test_download_command_exits_cleanly_on_upload_config_error(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "build_downloader", lambda verbose=False: FakeDownloader())

    def fail_upload(path, key_gen="hash"):
        raise UploadConfigError("bad upload config")

    monkeypatch.setattr(cli, "build_s3_uploader", fail_upload)

    result = runner.invoke(
        cli.app,
        [
            "download",
            "magnet:?xt=urn:btih:sample",
            "--storage",
            str(tmp_path / "downloads"),
            "--upload",
            str(tmp_path / "missing.toml"),
        ],
    )

    assert result.exit_code == 1
    assert "bad upload config" in result.stderr
