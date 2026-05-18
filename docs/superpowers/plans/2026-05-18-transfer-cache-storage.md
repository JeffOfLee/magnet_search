# Transfer Cache Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--transfer-cache-storage` to pause new downloads when current-run local transfer cache exceeds the configured size, then release space after successful upload cleanup.

**Architecture:** Add reusable cache tracking and cleanup primitives in `download.py`, thread them through `run_download_batch()` with a `before_download` hook, and orchestrate upload cleanup from the CLI. Keep existing behavior unchanged unless the new option is provided.

**Tech Stack:** Python, Typer, pytest, ThreadPoolExecutor.

---

### Task 1: Add cache primitive tests

**Files:**
- Modify: `tests/test_download.py`
- Modify: `src/magnet_search/download.py`

- [ ] Write failing tests for `parse_storage_size()`, `TransferCacheStorage.wait_for_space()`, `track_result()`, and `release_result()`.
- [ ] Run `./.venv/bin/python -m pytest tests/test_download.py -q` and confirm the new tests fail because the primitives do not exist.
- [ ] Implement the minimal parser and thread-safe tracker.
- [ ] Re-run `./.venv/bin/python -m pytest tests/test_download.py -q`.

### Task 2: Add batch backpressure tests

**Files:**
- Modify: `tests/test_download.py`
- Modify: `src/magnet_search/download.py`

- [ ] Write a failing test proving `run_download_batch(..., before_download=gate.wait_for_space)` blocks a later download until `release_result()` is called.
- [ ] Run that test and confirm it fails.
- [ ] Add the optional `before_download` callback to both sequential and concurrent batch paths.
- [ ] Re-run `./.venv/bin/python -m pytest tests/test_download.py -q`.

### Task 3: Add CLI wiring tests

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/magnet_search/cli.py`

- [ ] Write failing tests for `--transfer-cache-storage` requiring `--upload`, forwarding a `before_download` hook to batch execution, and deleting local files after successful upload.
- [ ] Run `./.venv/bin/python -m pytest tests/test_cli.py -q` and confirm the new tests fail.
- [ ] Add the Typer option, parser call, validation, cache tracking, and upload cleanup wiring.
- [ ] Re-run `./.venv/bin/python -m pytest tests/test_cli.py -q`.

### Task 4: Update docs and verify

**Files:**
- Modify: `README.md`

- [ ] Add an example and concise behavior note for `--transfer-cache-storage`.
- [ ] Run `./.venv/bin/python -m pytest -q`.
- [ ] Review `git diff` for scope and accidental unrelated changes.
