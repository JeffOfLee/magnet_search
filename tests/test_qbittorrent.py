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


def test_qbittorrent_adds_magnet_paused_when_requested(tmp_path: Path):
    mock_client = MagicMock()
    mock_client.post.return_value = FakeResponse("Ok.")
    mock_client.get.side_effect = [
        FakeResponse(json_data=[]),
        FakeResponse(json_data=[_make_torrent("newhash", "pausedDL", 0.0)]),
    ]
    downloader = QbittorrentDownloader(poll_interval=0.01)
    downloader._session = mock_client

    info_hash = downloader.add_paused("magnet:?xt=urn:btih:sample", tmp_path)

    assert info_hash == "newhash"
    add_call = mock_client.post.call_args_list[0]
    assert add_call[0][0].endswith("/api/v2/torrents/add")
    assert add_call[1]["data"]["paused"] == "true"
    assert add_call[1]["data"]["stopped"] == "true"
    assert add_call[1]["data"]["savepath"] == str(tmp_path)
    stop_call = mock_client.post.call_args_list[-1]
    assert stop_call[0][0].endswith("/api/v2/torrents/stop")
    assert stop_call[1]["data"] == {"hashes": "newhash"}


def test_qbittorrent_stop_and_start_hashes():
    mock_client = MagicMock()
    mock_client.post.return_value = FakeResponse("Ok.")
    downloader = QbittorrentDownloader()
    downloader._session = mock_client

    downloader.resume_hashes(["a", "b"])
    downloader.pause_hashes(["c"])
    downloader.resume_hashes([])

    assert mock_client.post.call_args_list[0][0][0].endswith("/api/v2/torrents/start")
    assert mock_client.post.call_args_list[0][1]["data"] == {"hashes": "a|b"}
    assert mock_client.post.call_args_list[1][0][0].endswith("/api/v2/torrents/stop")
    assert mock_client.post.call_args_list[1][1]["data"] == {"hashes": "c"}
    assert len(mock_client.post.call_args_list) == 2


def test_qbittorrent_start_stop_falls_back_to_legacy_pause_resume():
    mock_client = MagicMock()
    mock_client.post.side_effect = [
        FakeResponse("not found", status_code=404),
        FakeResponse("Ok."),
        FakeResponse("not found", status_code=404),
        FakeResponse("Ok."),
    ]
    downloader = QbittorrentDownloader()
    downloader._session = mock_client

    downloader.resume_hashes(["a"])
    downloader.pause_hashes(["b"])

    assert mock_client.post.call_args_list[0][0][0].endswith("/api/v2/torrents/start")
    assert mock_client.post.call_args_list[1][0][0].endswith("/api/v2/torrents/resume")
    assert mock_client.post.call_args_list[2][0][0].endswith("/api/v2/torrents/stop")
    assert mock_client.post.call_args_list[3][0][0].endswith("/api/v2/torrents/pause")


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


def test_qbittorrent_list_downloads_returns_current_status():
    mock_client = MagicMock()
    mock_client.post.return_value = FakeResponse("Ok.")
    mock_client.get.return_value = FakeResponse(json_data=[
        _make_torrent(
            "ubuntu",
            "downloading",
            0.25,
            name="Ubuntu ISO",
            size=4_000,
            downloaded=1_000,
            dlspeed=512,
            upspeed=64,
            eta=120,
            num_seeds=5,
            num_leechs=2,
            save_path="/downloads",
        )
    ])
    downloader = QbittorrentDownloader()
    downloader._session = mock_client

    downloads = downloader.list_downloads()

    assert downloads[0].name == "Ubuntu ISO"
    assert downloads[0].state == "downloading"
    assert downloads[0].progress == 0.25
    assert downloads[0].download_speed == 512
    assert downloads[0].seeds == 5


def test_qbittorrent_batch_resumes_top_n_by_active_seeds(tmp_path: Path):
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()
    for name in ("one.bin", "two.bin", "three.bin"):
        (output_dir / name).write_text("payload", encoding="utf-8")

    downloader = QbittorrentDownloader(poll_interval=0.01)
    downloader.add_paused = MagicMock(side_effect=["h1", "h2", "h3"])
    resume_calls: list[list[str]] = []
    pause_calls: list[list[str]] = []
    removed: list[str] = []
    completed_inputs: list[str] = []
    downloader.resume_hashes = lambda hashes: resume_calls.append(list(hashes))
    downloader.pause_hashes = lambda hashes: pause_calls.append(list(hashes))
    downloader._remove_torrent = lambda info_hash: removed.append(info_hash)
    snapshots = [
        [
            _make_torrent("h1", "pausedDL", 0.0, name="one", num_seeds=1),
            _make_torrent("h2", "pausedDL", 0.0, name="two", num_seeds=10),
            _make_torrent("h3", "pausedDL", 0.0, name="three", num_seeds=5),
        ],
        [
            _make_torrent("h1", "pausedDL", 0.0, name="one", num_seeds=1),
            _make_torrent(
                "h2",
                "pausedUP",
                1.0,
                name="two",
                num_seeds=10,
                content_path=str(output_dir / "two.bin"),
            ),
            _make_torrent(
                "h3",
                "pausedUP",
                1.0,
                name="three",
                num_seeds=5,
                content_path=str(output_dir / "three.bin"),
            ),
        ],
        [
            _make_torrent(
                "h1",
                "pausedUP",
                1.0,
                name="one",
                num_seeds=1,
                content_path=str(output_dir / "one.bin"),
            ),
        ],
    ]
    def mock_get(endpoint, params=None):
        if params:
            return FakeResponse(json_data=snapshots.pop(0))
        return FakeResponse(json_data=[])

    downloader._api_get = mock_get

    results, failures = downloader.download_sources_by_seed_priority(
        ["one", "two", "three"],
        output_dir,
        max_active=2,
        on_result=lambda result: completed_inputs.append(result.input),
    )

    assert failures == []
    assert downloader.add_paused.call_args_list == [
        ((source, output_dir),) for source in ["one", "two", "three"]
    ]
    assert resume_calls[0] == ["h2", "h3"]
    assert pause_calls[0] == ["h1"]
    assert resume_calls[1] == ["h1"]
    assert [result.input for result in results] == ["two", "three", "one"]
    assert completed_inputs == ["two", "three", "one"]
    assert [path.name for result in results for path in result.files] == ["two.bin", "three.bin", "one.bin"]
    assert removed == []


def test_qbittorrent_batch_reuses_completed_record_with_complete_cache(tmp_path: Path):
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()
    file_path = output_dir / "done.bin"
    file_path.write_text("payload", encoding="utf-8")
    source = "magnet:?xt=urn:btih:donehash"
    torrent = _make_torrent(
        "donehash",
        "pausedUP",
        1.0,
        name="done",
        magnet_uri=source,
        content_path=str(file_path),
        size=file_path.stat().st_size,
    )
    downloader = QbittorrentDownloader(poll_interval=0.01)
    downloader.add_paused = MagicMock(return_value="newhash")
    downloader._remove_torrent = MagicMock()
    downloader._api_get = lambda endpoint, params=None: FakeResponse(json_data=[torrent])
    completed_inputs: list[str] = []

    results, failures = downloader.download_sources_by_seed_priority(
        [source],
        output_dir,
        max_active=1,
        on_result=lambda result: completed_inputs.append(result.input),
    )

    assert failures == []
    assert [result.input for result in results] == [source]
    assert results[0].files == [file_path]
    assert completed_inputs == [source]
    downloader.add_paused.assert_not_called()
    downloader._remove_torrent.assert_not_called()


def test_qbittorrent_batch_redownloads_completed_record_with_incomplete_cache(tmp_path: Path):
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()
    old_file = output_dir / "incomplete.bin"
    old_file.write_text("short", encoding="utf-8")
    new_file = output_dir / "complete.bin"
    new_file.write_text("complete payload", encoding="utf-8")
    source = "magnet:?xt=urn:btih:oldhash"
    old_torrent = _make_torrent(
        "oldhash",
        "pausedUP",
        1.0,
        name="incomplete",
        magnet_uri=source,
        content_path=str(old_file),
        size=old_file.stat().st_size + 10,
    )
    new_torrent = _make_torrent(
        "newhash",
        "pausedUP",
        1.0,
        name="complete",
        magnet_uri=source,
        content_path=str(new_file),
        size=new_file.stat().st_size,
    )
    downloader = QbittorrentDownloader(poll_interval=0.01)
    downloader.add_paused = MagicMock(return_value="newhash")
    removed: list[str] = []
    downloader._remove_torrent = lambda info_hash: removed.append(info_hash)

    def mock_get(endpoint, params=None):
        if params and params.get("hashes") == "newhash":
            return FakeResponse(json_data=[new_torrent])
        return FakeResponse(json_data=[old_torrent])

    downloader._api_get = mock_get

    results, failures = downloader.download_sources_by_seed_priority([source], output_dir, max_active=1)

    assert failures == []
    assert [result.files for result in results] == [[new_file]]
    assert downloader.add_paused.call_args_list == [((source, output_dir),)]
    assert removed == ["oldhash"]


def test_qbittorrent_batch_attaches_existing_active_record(tmp_path: Path):
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()
    file_path = output_dir / "active.bin"
    file_path.write_text("payload", encoding="utf-8")
    source = "magnet:?xt=urn:btih:activehash"
    downloader = QbittorrentDownloader(poll_interval=0.01)
    downloader.add_paused = MagicMock(return_value="newhash")
    resume_calls: list[list[str]] = []
    pause_calls: list[list[str]] = []
    downloader.resume_hashes = lambda hashes: resume_calls.append(list(hashes))
    downloader.pause_hashes = lambda hashes: pause_calls.append(list(hashes))
    downloader._remove_torrent = MagicMock()
    snapshots = [
        [_make_torrent("activehash", "downloading", 0.5, name="active", magnet_uri=source, num_seeds=3)],
        [
            _make_torrent(
                "activehash",
                "pausedUP",
                1.0,
                name="active",
                magnet_uri=source,
                content_path=str(file_path),
                size=file_path.stat().st_size,
                num_seeds=3,
            )
        ],
    ]

    def mock_get(endpoint, params=None):
        if params and params.get("hashes") == "activehash":
            return FakeResponse(json_data=snapshots.pop(0))
        return FakeResponse(json_data=[snapshots[0][0]])

    downloader._api_get = mock_get

    results, failures = downloader.download_sources_by_seed_priority([source], output_dir, max_active=1)

    assert failures == []
    assert results[0].files == [file_path]
    downloader.add_paused.assert_not_called()
    downloader._remove_torrent.assert_not_called()
    assert resume_calls[0] == ["activehash"]
    assert pause_calls[0] == []


def test_qbittorrent_batch_fails_existing_stalled_record_without_readding(tmp_path: Path):
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()
    source = "magnet:?xt=urn:btih:stalledhash"
    torrent = _make_torrent("stalledhash", "stalledDL", 0.2, name="stalled", magnet_uri=source)
    downloader = QbittorrentDownloader(poll_interval=0.01)
    downloader.add_paused = MagicMock(return_value="newhash")
    downloader._remove_torrent = MagicMock()
    downloader._api_get = lambda endpoint, params=None: FakeResponse(json_data=[torrent])

    results, failures = downloader.download_sources_by_seed_priority([source], output_dir, max_active=1)

    assert results == []
    assert len(failures) == 1
    assert failures[0][0] == source
    assert "stalledDL" in str(failures[0][1])
    downloader.add_paused.assert_not_called()
    downloader._remove_torrent.assert_not_called()


def test_qbittorrent_seed_priority_batch_fails_torrent_with_no_active_seeds(tmp_path: Path):
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()
    downloader = QbittorrentDownloader(poll_interval=0.01, no_seed_checks=2)
    downloader.add_paused = MagicMock(return_value="h1")
    removed: list[str] = []
    downloader.resume_hashes = lambda hashes: None
    downloader.pause_hashes = lambda hashes: None
    downloader._remove_torrent = lambda info_hash: removed.append(info_hash)
    snapshots = [
        [_make_torrent("h1", "pausedDL", 0.0, name="one", num_seeds=0)],
        [_make_torrent("h1", "pausedDL", 0.0, name="one", num_seeds=0)],
    ]
    def mock_get(endpoint, params=None):
        if params:
            return FakeResponse(json_data=snapshots.pop(0))
        return FakeResponse(json_data=[])

    downloader._api_get = mock_get

    results, failures = downloader.download_sources_by_seed_priority(["one"], output_dir, max_active=1)

    assert results == []
    assert len(failures) == 1
    assert failures[0][0] == "one"
    assert "no active seeds" in str(failures[0][1])
    assert removed == ["h1"]
