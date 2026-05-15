# Download and Upload Concurrency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add independent batch download and S3 upload concurrency controls to the existing `download` command.

**Architecture:** Extend `run_download_batch()` with a download worker pool and a completion callback. Keep upload concurrency in the CLI with a separate executor so upload only starts when `--upload` is provided.

**Tech Stack:** Python 3.11, `concurrent.futures`, Typer, pytest

---

### Task 1: Add failing tests for download concurrency

**Files:**
- Modify: `tests/test_download.py`

- [ ] Test that `run_download_batch(..., download_concurrency=2)` never runs more than two downloader calls at once.
- [ ] Test that multiple failed downloads are reported in one `DownloadError`.
- [ ] Run `./.venv/bin/python -m pytest tests/test_download.py -q` and confirm failure.

### Task 2: Implement concurrent downloads

**Files:**
- Modify: `src/magnet_search/download.py`

- [ ] Add `download_concurrency` and `on_result` parameters.
- [ ] Validate `download_concurrency >= 1`.
- [ ] Use `ThreadPoolExecutor` for concurrent batch downloads.
- [ ] Preserve existing serial behavior when concurrency is `1`.
- [ ] Run `./.venv/bin/python -m pytest tests/test_download.py -q`.

### Task 3: Add failing CLI tests for upload concurrency

**Files:**
- Modify: `tests/test_cli.py`

- [ ] Test that `--download-concurrency` is forwarded to `run_download_batch`.
- [ ] Test that batch upload honors `--upload-concurrency`.
- [ ] Run `./.venv/bin/python -m pytest tests/test_cli.py -q` and confirm failure.

### Task 4: Implement CLI upload executor

**Files:**
- Modify: `src/magnet_search/cli.py`

- [ ] Add `--download-concurrency` and `--upload-concurrency` options.
- [ ] Create an upload executor only when `--upload` is provided.
- [ ] Submit uploads from the download completion callback.
- [ ] Aggregate upload failures and exit cleanly.
- [ ] Run `./.venv/bin/python -m pytest tests/test_cli.py -q`.

### Task 5: Document and verify

**Files:**
- Modify: `README.md`

- [ ] Add concurrent batch download/upload examples.
- [ ] Run `./.venv/bin/python -m pytest -q`.
