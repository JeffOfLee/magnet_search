import csv
import json
import tomllib

import pytest
from typer.testing import CliRunner

from magnet_search import cli
from magnet_search.config import ConfigError
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
