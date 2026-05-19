# qBittorrent Seed Priority Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make qBittorrent CSV batch downloads submit all tasks first, then download only the top N unfinished torrents ranked by active seeds.

**Architecture:** Add qBittorrent-specific batch orchestration in `src/magnet_search/qbittorrent.py`, while keeping generic CSV parsing and metadata recording in `src/magnet_search/download.py`. Route only `engine=qbittorrent` CSV batch downloads through the new scheduler from `src/magnet_search/cli.py`; keep `aria2c` on the existing `run_download_batch()` path.

**Tech Stack:** Python 3.11+, Typer, Rich, httpx, pytest, qBittorrent Web API.

---

## File Structure

- Modify `src/magnet_search/qbittorrent.py`: add paused submission, pause/resume helpers, source-to-hash resolution, and seed-priority batch scheduler.
- Modify `src/magnet_search/cli.py`: route qBittorrent CSV batch downloads to the scheduler and preserve upload callbacks.
- Modify `src/magnet_search/download.py`: expose a CSV source collection helper or callback shape if needed without importing qBittorrent.
- Modify `tests/test_qbittorrent.py`: unit-test scheduler behavior with fake qBittorrent API responses.
- Modify `tests/test_cli.py`: verify CLI routing for qBittorrent batch and keep the existing monitor multi-item regression.
- Modify `docs/qbittorrent-setup.md` and `README.md`: document the new qBittorrent batch scheduling meaning of `--download-concurrency`.

## Task 1: Add qBittorrent Pause/Resume API Primitives

**Files:**
- Modify: `src/magnet_search/qbittorrent.py`
- Test: `tests/test_qbittorrent.py`

- [ ] **Step 1: Write failing tests for paused add and pause/resume**

Add tests like:

```python
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
    assert add_call[1]["data"]["savepath"] == str(tmp_path)


def test_qbittorrent_pause_and_resume_hashes():
    mock_client = MagicMock()
    mock_client.post.return_value = FakeResponse("Ok.")
    downloader = QbittorrentDownloader()
    downloader._session = mock_client

    downloader.resume_hashes(["a", "b"])
    downloader.pause_hashes(["c"])

    assert mock_client.post.call_args_list[0][0][0].endswith("/api/v2/torrents/resume")
    assert mock_client.post.call_args_list[0][1]["data"] == {"hashes": "a|b"}
    assert mock_client.post.call_args_list[1][0][0].endswith("/api/v2/torrents/pause")
    assert mock_client.post.call_args_list[1][1]["data"] == {"hashes": "c"}
```

- [ ] **Step 2: Run tests and verify failure**

Run: `./.venv/bin/python -m pytest tests/test_qbittorrent.py -k "paused_when_requested or pause_and_resume" -q`

Expected: FAIL because `add_paused`, `resume_hashes`, and `pause_hashes` do not exist.

- [ ] **Step 3: Implement minimal primitives**

Add public methods to `QbittorrentDownloader`:

```python
def add_paused(self, source: str, output_dir: Path) -> str:
    return self._add_source(source, output_dir, paused=True)

def resume_hashes(self, hashes: list[str]) -> None:
    self._set_hashes_state("/api/v2/torrents/resume", hashes)

def pause_hashes(self, hashes: list[str]) -> None:
    self._set_hashes_state("/api/v2/torrents/pause", hashes)
```

Extract the existing add logic from `download()` into `_add_source(source, output_dir, paused=False)` so both single-download and batch scheduler share response validation and hash detection.

- [ ] **Step 4: Run tests and verify pass**

Run: `./.venv/bin/python -m pytest tests/test_qbittorrent.py -k "paused_when_requested or pause_and_resume" -q`

Expected: PASS.

## Task 2: Implement Seed-Priority Scheduler

**Files:**
- Modify: `src/magnet_search/qbittorrent.py`
- Test: `tests/test_qbittorrent.py`

- [ ] **Step 1: Write failing scheduler tests**

Add a test that creates three sources, returns torrent snapshots with seed counts 10, 5, and 1, and asserts only the top two hashes are resumed:

```python
def test_qbittorrent_batch_resumes_top_n_by_active_seeds(tmp_path: Path):
    output_dir = tmp_path / "downloads"
    output_dir.mkdir()
    mock_client = MagicMock()
    mock_client.post.return_value = FakeResponse("Ok.")
    snapshots = [
        [],
        [_make_torrent("h1", "pausedDL", 0.0, name="one")],
        [_make_torrent("h1", "pausedDL", 0.0, name="one"), _make_torrent("h2", "pausedDL", 0.0, name="two")],
        [_make_torrent("h1", "pausedDL", 0.0, name="one"), _make_torrent("h2", "pausedDL", 0.0, name="two"), _make_torrent("h3", "pausedDL", 0.0, name="three")],
        [
            _make_torrent("h1", "pausedDL", 0.0, name="one", num_seeds=1),
            _make_torrent("h2", "pausedDL", 0.0, name="two", num_seeds=10),
            _make_torrent("h3", "pausedDL", 0.0, name="three", num_seeds=5),
        ],
        [
            _make_torrent("h1", "pausedDL", 0.0, name="one", num_seeds=1),
            _make_torrent("h2", "pausedUP", 1.0, name="two", num_seeds=10, content_path=str(output_dir / "two.bin")),
            _make_torrent("h3", "pausedUP", 1.0, name="three", num_seeds=5, content_path=str(output_dir / "three.bin")),
        ],
        [
            _make_torrent("h1", "pausedUP", 1.0, name="one", num_seeds=1, content_path=str(output_dir / "one.bin")),
        ],
    ]
    for name in ("one.bin", "two.bin", "three.bin"):
        (output_dir / name).write_text("payload", encoding="utf-8")
    mock_client.get.side_effect = lambda endpoint, params=None: FakeResponse(json_data=snapshots.pop(0))
    downloader = QbittorrentDownloader(poll_interval=0.01)
    downloader._session = mock_client

    results, failures = downloader.download_sources_by_seed_priority(
        ["one", "two", "three"],
        output_dir,
        max_active=2,
    )

    assert failures == []
    assert [result.input for result in results] == ["two", "three", "one"]
    resume_calls = [call for call in mock_client.post.call_args_list if call[0][0].endswith("/api/v2/torrents/resume")]
    assert resume_calls[0][1]["data"] == {"hashes": "h2|h3"}
```

- [ ] **Step 2: Run scheduler test and verify failure**

Run: `./.venv/bin/python -m pytest tests/test_qbittorrent.py::test_qbittorrent_batch_resumes_top_n_by_active_seeds -q`

Expected: FAIL because `download_sources_by_seed_priority` does not exist.

- [ ] **Step 3: Implement scheduler**

Add:

```python
def download_sources_by_seed_priority(
    self,
    sources: list[str],
    output_dir: Path,
    max_active: int,
) -> tuple[list[DownloadResult], list[tuple[str, Exception]]]:
    ...
```

Implementation rules:

- Validate `max_active >= 1`.
- Add every source paused and keep `hash -> (index, source)`.
- Poll all tracked hashes through `/api/v2/torrents/info` using the `hashes` parameter.
- Use `_unfinished_torrents()` for `progress < 1.0` and non-error states.
- Sort unfinished by `(-num_seeds, original_index)`.
- Resume top N hashes and pause the remaining unfinished hashes.
- Convert completed torrents to `DownloadResult` via `_download_result_from_torrent()`.
- Remove completed and failed torrents with `_remove_torrent()`.
- Return results in completion order, with failures as `(source, exception)`.

- [ ] **Step 4: Run scheduler tests**

Run: `./.venv/bin/python -m pytest tests/test_qbittorrent.py -k "seed_priority or paused_when_requested or pause_and_resume" -q`

Expected: PASS.

## Task 3: Wire CLI qBittorrent Batch Routing

**Files:**
- Modify: `src/magnet_search/cli.py`
- Modify: `src/magnet_search/download.py` if a reusable source collection helper is needed.
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI routing test**

Add a test that invokes:

```python
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
```

Patch `cli.QbittorrentDownloader` with a fake object exposing `download_sources_by_seed_priority()`. Assert it receives all CSV sources and `max_active=2`.

- [ ] **Step 2: Run routing test and verify failure**

Run: `./.venv/bin/python -m pytest tests/test_cli.py -k "qbittorrent_batch_routes" -q`

Expected: FAIL because CLI still calls `run_download_batch()`.

- [ ] **Step 3: Implement routing**

Add a helper in `cli.py`:

```python
def _run_qbittorrent_seed_priority_batch(...):
    ...
```

It should parse CSV sources, call `downloader.download_sources_by_seed_priority(sources, storage, download_concurrency)`, record success/failure rows with `append_download_record()` or the existing result recorder path, and call `on_result` for upload integration when needed.

- [ ] **Step 4: Run CLI routing test**

Run: `./.venv/bin/python -m pytest tests/test_cli.py -k "qbittorrent_batch_routes or download_command_records_qbittorrent_startup_results" -q`

Expected: PASS.

## Task 4: Preserve Upload Integration

**Files:**
- Modify: `src/magnet_search/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing upload integration test**

Add a test with `--engine qbittorrent --upload s3-upload.toml` and fake scheduler returning two `DownloadResult` objects. Assert `FakeUploader.upload_files()` receives both results and `uploaded 2 file(s)` appears.

- [ ] **Step 2: Run upload integration test**

Run: `./.venv/bin/python -m pytest tests/test_cli.py -k "qbittorrent_batch_upload" -q`

Expected: FAIL until CLI calls `enqueue_upload` for scheduler results.

- [ ] **Step 3: Implement upload callback integration**

In the qBittorrent batch route, call the same `on_result` callback used by generic batch downloads for every successful scheduler result.

- [ ] **Step 4: Run upload integration test**

Run: `./.venv/bin/python -m pytest tests/test_cli.py -k "qbittorrent_batch_upload" -q`

Expected: PASS.

## Task 5: Documentation and Final Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/qbittorrent-setup.md`

- [ ] **Step 1: Update docs**

Document that `--download-concurrency` means top-N active qBittorrent downloads for `--engine qbittorrent`, after all CSV tasks are submitted paused.

- [ ] **Step 2: Run focused tests**

Run: `./.venv/bin/python -m pytest tests/test_qbittorrent.py tests/test_cli.py -k "qbittorrent or download_command_uses_upload_concurrency" -q`

Expected: PASS.

- [ ] **Step 3: Run full tests**

Run: `./.venv/bin/python -m pytest -q`

Expected: PASS.

- [ ] **Step 4: Review git diff**

Run: `git diff --stat`

Expected: only planned files changed, plus the previously added monitor regression test in `tests/test_cli.py`.
