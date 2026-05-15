import csv
import json
import tomllib

import pytest
from typer.testing import CliRunner

from magnet_search import cli
from magnet_search.config import ConfigError
from magnet_search.download import DownloadError
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
    output_path = tmp_path / "output.csv"
    input_path.write_text("query\nsample movie\n", encoding="utf-8")

    result = runner.invoke(cli.app, ["search", str(input_path), "--output", str(output_path)])

    assert result.exit_code == 0
    rows = list(csv.DictReader(output_path.open(encoding="utf-8")))
    assert rows[0]["query"] == "sample movie"
    assert rows[0]["title"] == "Sample Result"
    assert "wrote" in result.stdout


def test_search_command_writes_batch_csv_with_custom_column(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "build_search_service", lambda: FakeService())
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"
    input_path.write_text("title\nsample movie\n", encoding="utf-8")

    result = runner.invoke(
        cli.app,
        ["search", str(input_path), "--column", "title", "--output", str(output_path)],
    )

    assert result.exit_code == 0
    rows = list(csv.DictReader(output_path.open(encoding="utf-8")))
    assert rows[0]["query"] == "sample movie"
    assert rows[0]["title"] == "Sample Result"


def test_search_command_batch_mode_requires_output(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "build_search_service", lambda: FakeService())
    input_path = tmp_path / "input.csv"
    input_path.write_text("query\nsample movie\n", encoding="utf-8")

    result = runner.invoke(cli.app, ["search", str(input_path)])

    assert result.exit_code == 1
    assert "batch search requires --output" in result.stderr
    assert "Traceback" not in result.output
    assert "Traceback" not in result.stderr


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
    assert rows[0]["title"] == "Sample Result"


def test_batch_command_all_providers_failed_exits_1_without_output(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "build_search_service", lambda: FailingService())
    input_path = tmp_path / "input.csv"
    output_path = tmp_path / "output.csv"
    input_path.write_text("title\nsample movie\n", encoding="utf-8")

    result = runner.invoke(
        cli.app,
        ["batch", str(input_path), "--column", "title", "--output", str(output_path)],
    )

    assert result.exit_code == 1
    assert "all providers failed" in result.stderr
    assert "wrote" not in result.stdout
    assert not output_path.exists()


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
    monkeypatch.setattr(cli, "build_downloader", lambda: downloader)

    result = runner.invoke(
        cli.app,
        ["download", "magnet:?xt=urn:btih:sample", "--output", str(tmp_path / "downloads")],
    )

    assert result.exit_code == 0
    assert downloader.calls == [("magnet:?xt=urn:btih:sample", tmp_path / "downloads")]
    assert "downloaded 1 file(s)" in result.stdout


def test_download_command_downloads_csv_batch_with_default_column(monkeypatch, tmp_path):
    downloader = FakeDownloader()
    monkeypatch.setattr(cli, "build_downloader", lambda: downloader)
    input_path = tmp_path / "input.csv"
    input_path.write_text("magnet\nmagnet:?xt=urn:btih:first\nmagnet:?xt=urn:btih:second\n", encoding="utf-8")

    result = runner.invoke(cli.app, ["download", str(input_path), "--output", str(tmp_path / "downloads")])

    assert result.exit_code == 0
    assert [call[0] for call in downloader.calls] == [
        "magnet:?xt=urn:btih:first",
        "magnet:?xt=urn:btih:second",
    ]
    assert "downloaded 2 item(s), 2 file(s)" in result.stdout


def test_download_command_downloads_csv_batch_with_custom_column(monkeypatch, tmp_path):
    downloader = FakeDownloader()
    monkeypatch.setattr(cli, "build_downloader", lambda: downloader)
    input_path = tmp_path / "input.csv"
    input_path.write_text("link\nmagnet:?xt=urn:btih:first\n", encoding="utf-8")

    result = runner.invoke(
        cli.app,
        ["download", str(input_path), "--column", "link", "--output", str(tmp_path / "downloads")],
    )

    assert result.exit_code == 0
    assert [call[0] for call in downloader.calls] == ["magnet:?xt=urn:btih:first"]


def test_download_command_uploads_when_upload_config_is_provided(monkeypatch, tmp_path):
    downloader = FakeDownloader()
    uploader = FakeUploader()
    upload_path = tmp_path / "s3-upload.toml"
    upload_path.write_text('bucket = "my-bucket"\n', encoding="utf-8")
    monkeypatch.setattr(cli, "build_downloader", lambda: downloader)
    monkeypatch.setattr(cli, "build_s3_uploader", lambda path: uploader)

    result = runner.invoke(
        cli.app,
        [
            "download",
            "magnet:?xt=urn:btih:sample",
            "--output",
            str(tmp_path / "downloads"),
            "--upload",
            str(upload_path),
        ],
    )

    assert result.exit_code == 0
    assert uploader.calls == [([tmp_path / "downloads" / "movie.mp4"], tmp_path / "downloads")]
    assert "uploaded 1 file(s)" in result.stdout


def test_download_command_exits_cleanly_on_download_error(monkeypatch, tmp_path):
    class FailingDownloader:
        def download(self, magnet, output_dir):
            raise DownloadError("download failed")

    monkeypatch.setattr(cli, "build_downloader", lambda: FailingDownloader())

    result = runner.invoke(
        cli.app,
        ["download", "magnet:?xt=urn:btih:sample", "--output", str(tmp_path / "downloads")],
    )

    assert result.exit_code == 1
    assert "download failed" in result.stderr
    assert "Traceback" not in result.output
    assert "Traceback" not in result.stderr


def test_download_command_exits_cleanly_on_upload_config_error(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "build_downloader", lambda: FakeDownloader())

    def fail_upload(path):
        raise UploadConfigError("bad upload config")

    monkeypatch.setattr(cli, "build_s3_uploader", fail_upload)

    result = runner.invoke(
        cli.app,
        [
            "download",
            "magnet:?xt=urn:btih:sample",
            "--output",
            str(tmp_path / "downloads"),
            "--upload",
            str(tmp_path / "missing.toml"),
        ],
    )

    assert result.exit_code == 1
    assert "bad upload config" in result.stderr
