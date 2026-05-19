from pathlib import Path
from unittest.mock import MagicMock

import pytest

from magnet_search.download import DownloadError
from magnet_search.qbittorrent import QbittorrentDownloader


class FakeResponse:
    def __init__(self, text="Ok.", status_code=200, json_data=None):
        self._text = text
        self.status_code = status_code
        self._json = json_data or []

    @property
    def text(self):
        return self._text

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            from httpx import HTTPStatusError
            raise HTTPStatusError("", request=MagicMock(), response=self)


def _make_torrent(info_hash, state="downloading", progress=0.0, name="test", **kwargs):
    torrent = {"hash": info_hash, "state": state, "progress": progress, "name": name}
    torrent.update(kwargs)
    return torrent


def test_qbittorrent_login_success():
    mock_client = MagicMock()
    mock_client.post.return_value = FakeResponse("Ok.")
    downloader = QbittorrentDownloader()
    downloader._session = mock_client

    downloader._login()

    mock_client.post.assert_called_once_with(
        "http://localhost:8080/api/v2/auth/login",
        data={"username": "admin", "password": ""},
    )


def test_qbittorrent_login_failure():
    mock_client = MagicMock()
    mock_client.post.return_value = FakeResponse("Fails.")
    downloader = QbittorrentDownloader()
    downloader._session = mock_client

    with pytest.raises(DownloadError, match="qBittorrent login failed"):
        downloader._login()


def test_qbittorrent_download_torrent_file(tmp_path: Path):
    torrent_path = tmp_path / "movie.torrent"
    torrent_path.write_text("torrent content", encoding="utf-8")
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()

    mock_client = MagicMock()
    mock_client.post.return_value = FakeResponse("Ok.")

    get_calls = [0]

    def mock_get(endpoint, params=None):
        get_calls[0] += 1
        if params and params.get("hashes"):
            return FakeResponse(json_data=[_make_torrent(params["hashes"], "pausedUP", 1.0)])
        if get_calls[0] == 1:
            return FakeResponse(json_data=[_make_torrent("oldhash")])
        return FakeResponse(json_data=[
            _make_torrent("oldhash"),
            _make_torrent("newhash"),
        ])

    mock_client.get.side_effect = mock_get

    downloader = QbittorrentDownloader(poll_interval=0.01)
    downloader._session = mock_client

    result = downloader.download(str(torrent_path), output_dir)

    add_call = mock_client.post.call_args_list[0]
    assert add_call[0][0] == "http://localhost:8080/api/v2/torrents/add"
    assert add_call[1]["data"]["savepath"] == str(output_dir)
    assert result.magnet == str(torrent_path)


def test_qbittorrent_download_magnet(tmp_path: Path):
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()

    mock_client = MagicMock()
    mock_client.post.return_value = FakeResponse("Ok.")

    get_calls = [0]

    def mock_get(endpoint, params=None):
        get_calls[0] += 1
        if params and params.get("hashes"):
            return FakeResponse(json_data=[_make_torrent(params["hashes"], "pausedUP", 1.0)])
        if get_calls[0] == 1:
            return FakeResponse(json_data=[])
        return FakeResponse(json_data=[_make_torrent("abc123")])

    mock_client.get.side_effect = mock_get

    downloader = QbittorrentDownloader(poll_interval=0.01)
    downloader._session = mock_client

    result = downloader.download("magnet:?xt=urn:btih:sample", output_dir)

    add_call = mock_client.post.call_args_list[0]
    assert add_call[1]["data"]["urls"] == "magnet:?xt=urn:btih:sample"
    assert result.magnet == "magnet:?xt=urn:btih:sample"


def test_qbittorrent_rejects_empty_source(tmp_path: Path):
    downloader = QbittorrentDownloader()

    with pytest.raises(DownloadError, match="download source must be non-empty"):
        downloader.download("   ", tmp_path / "downloads")


def test_qbittorrent_reports_add_failure(tmp_path: Path):
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()
    mock_client = MagicMock()
    mock_client.post.return_value = FakeResponse("Error: duplicate torrent")
    mock_client.get.return_value = FakeResponse(json_data=[])
    downloader = QbittorrentDownloader()
    downloader._session = mock_client

    with pytest.raises(DownloadError, match="qBittorrent failed to add torrent"):
        downloader.download("magnet:?xt=urn:btih:sample", output_dir)


def test_qbittorrent_reports_missing_torrent_after_add(tmp_path: Path):
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()
    mock_client = MagicMock()
    mock_client.post.return_value = FakeResponse("Ok.")
    mock_client.get.return_value = FakeResponse(json_data=[])

    downloader = QbittorrentDownloader(poll_interval=0.01)
    downloader._session = mock_client

    with pytest.raises(DownloadError, match="could not find added torrent"):
        downloader.download("magnet:?xt=urn:btih:sample", output_dir)


def test_qbittorrent_deletes_torrent_after_download(tmp_path: Path):
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()

    mock_client = MagicMock()
    mock_client.post.return_value = FakeResponse("Ok.")

    get_calls = [0]

    def mock_get(endpoint, params=None):
        get_calls[0] += 1
        if params and params.get("hashes"):
            return FakeResponse(json_data=[_make_torrent(params["hashes"], "pausedUP", 1.0)])
        if get_calls[0] == 1:
            return FakeResponse(json_data=[])
        return FakeResponse(json_data=[_make_torrent("newhash")])

    mock_client.get.side_effect = mock_get

    downloader = QbittorrentDownloader(poll_interval=0.01)
    downloader._session = mock_client

    downloader.download("magnet:?xt=urn:btih:sample", output_dir)

    delete_call = mock_client.post.call_args_list[-1]
    assert "/torrents/delete" in delete_call[0][0]
    assert delete_call[1]["data"]["hashes"] == "newhash"
    assert delete_call[1]["data"]["deleteFiles"] == "false"


def test_qbittorrent_discovers_downloaded_files(tmp_path: Path):
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()

    mock_client = MagicMock()
    mock_client.post.return_value = FakeResponse("Ok.")

    get_calls = [0]

    def mock_get(endpoint, params=None):
        get_calls[0] += 1
        if params and params.get("hashes"):
            return FakeResponse(json_data=[_make_torrent(params["hashes"], "pausedUP", 1.0)])
        if get_calls[0] == 1:
            return FakeResponse(json_data=[])
        (output_dir / "movie.mkv").write_text("video content", encoding="utf-8")
        return FakeResponse(json_data=[_make_torrent("newhash")])

    mock_client.get.side_effect = mock_get

    downloader = QbittorrentDownloader(poll_interval=0.01)
    downloader._session = mock_client

    result = downloader.download("magnet:?xt=urn:btih:sample", output_dir)

    assert len(result.files) == 1
    assert result.files[0].name == "movie.mkv"


def test_qbittorrent_reports_error_state(tmp_path: Path):
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()

    mock_client = MagicMock()
    mock_client.post.return_value = FakeResponse("Ok.")

    get_calls = [0]

    def mock_get(endpoint, params=None):
        get_calls[0] += 1
        if params and params.get("hashes"):
            return FakeResponse(json_data=[_make_torrent(params["hashes"], "error", 0.0)])
        if get_calls[0] == 1:
            return FakeResponse(json_data=[])
        return FakeResponse(json_data=[_make_torrent("newhash")])

    mock_client.get.side_effect = mock_get

    downloader = QbittorrentDownloader(poll_interval=0.01)
    downloader._session = mock_client

    with pytest.raises(DownloadError, match="torrent error state"):
        downloader.download("magnet:?xt=urn:btih:sample", output_dir)


def test_qbittorrent_reports_missing_files_state(tmp_path: Path):
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()

    mock_client = MagicMock()
    mock_client.post.return_value = FakeResponse("Ok.")

    get_calls = [0]

    def mock_get(endpoint, params=None):
        get_calls[0] += 1
        if params and params.get("hashes"):
            return FakeResponse(json_data=[_make_torrent(params["hashes"], "missingFiles", 0.0)])
        if get_calls[0] == 1:
            return FakeResponse(json_data=[])
        return FakeResponse(json_data=[_make_torrent("newhash")])

    mock_client.get.side_effect = mock_get

    downloader = QbittorrentDownloader(poll_interval=0.01)
    downloader._session = mock_client

    with pytest.raises(DownloadError, match="torrent error state"):
        downloader.download("magnet:?xt=urn:btih:sample", output_dir)


def test_qbittorrent_removes_torrent_when_no_active_seeds(tmp_path: Path):
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()

    mock_client = MagicMock()
    mock_client.post.return_value = FakeResponse("Ok.")

    get_calls = [0]

    def mock_get(endpoint, params=None):
        get_calls[0] += 1
        if params and params.get("hashes"):
            torrent = _make_torrent(params["hashes"], "downloading", 0.25)
            torrent["num_seeds"] = 0
            return FakeResponse(json_data=[torrent])
        if get_calls[0] == 1:
            return FakeResponse(json_data=[])
        return FakeResponse(json_data=[_make_torrent("newhash")])

    mock_client.get.side_effect = mock_get

    downloader = QbittorrentDownloader(poll_interval=0.01, no_seed_checks=2)
    downloader._session = mock_client

    with pytest.raises(DownloadError, match="no active seeds"):
        downloader.download("magnet:?xt=urn:btih:sample", output_dir)

    delete_calls = [
        call for call in mock_client.post.call_args_list if "/torrents/delete" in call[0][0]
    ]
    assert delete_calls
    assert delete_calls[-1][1]["data"] == {"hashes": "newhash", "deleteFiles": "false"}


def test_qbittorrent_startup_results_wait_for_downloading_torrent(tmp_path: Path):
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()
    file_path = output_dir / "movie.mkv"
    file_path.write_text("video content", encoding="utf-8")

    mock_client = MagicMock()
    mock_client.post.return_value = FakeResponse("Ok.")
    torrent = _make_torrent(
        "activehash",
        "downloading",
        0.4,
        magnet_uri="magnet:?xt=urn:btih:active",
        content_path=str(file_path),
    )

    def mock_get(endpoint, params=None):
        if params and params.get("hashes") == "activehash":
            return FakeResponse(json_data=[_make_torrent("activehash", "pausedUP", 1.0)])
        return FakeResponse(json_data=[torrent])

    mock_client.get.side_effect = mock_get
    downloader = QbittorrentDownloader(poll_interval=0.01)
    downloader._session = mock_client

    results = downloader.startup_download_results(output_dir)

    assert len(results) == 1
    assert results[0].magnet == "magnet:?xt=urn:btih:active"
    assert results[0].input == "magnet:?xt=urn:btih:active"
    assert results[0].files == [file_path]


def test_qbittorrent_startup_results_record_stalled_torrent_without_waiting(tmp_path: Path):
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()
    file_path = output_dir / "stalled.mkv"
    file_path.write_text("video content", encoding="utf-8")

    mock_client = MagicMock()
    mock_client.post.return_value = FakeResponse("Ok.")
    mock_client.get.return_value = FakeResponse(json_data=[
        _make_torrent(
            "stalledhash",
            "stalledDL",
            0.3,
            magnet_uri="magnet:?xt=urn:btih:stalled",
            content_path=str(file_path),
        )
    ])
    downloader = QbittorrentDownloader(poll_interval=0.01)
    downloader._session = mock_client

    results = downloader.startup_download_results(output_dir)

    assert results[0].magnet == "magnet:?xt=urn:btih:stalled"
    assert results[0].input == "magnet:?xt=urn:btih:stalled"
    assert results[0].files == [file_path]
    hash_wait_calls = [call for call in mock_client.get.call_args_list if call.kwargs.get("params")]
    assert hash_wait_calls == []
