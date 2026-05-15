# Torrent File Download Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the existing `download` command accept BT `.torrent` files directly and from CSV batch rows.

**Architecture:** Reuse `Aria2cDownloader` because aria2c supports torrent files. Add source normalization in `download.py` so CSV-relative torrent paths resolve against the CSV location, while preserving existing magnet behavior and CLI upload flow.

**Tech Stack:** Python 3.11, aria2c, Typer, pytest

---

### Task 1: Add failing tests

**Files:**
- Modify: `tests/test_download.py`

- [ ] Add a test that `Aria2cDownloader.download()` passes an existing `.torrent` path to aria2c.
- [ ] Add a test that `run_download_batch()` resolves relative `.torrent` paths against the CSV parent directory.
- [ ] Run `./.venv/bin/python -m pytest tests/test_download.py -q` and confirm the relative-path test fails.

### Task 2: Implement source normalization

**Files:**
- Modify: `src/magnet_search/download.py`

- [ ] Add a helper that resolves relative `.torrent` paths against an optional base directory.
- [ ] Use that helper in `run_download_batch()`.
- [ ] Preserve existing magnet link behavior.
- [ ] Run `./.venv/bin/python -m pytest tests/test_download.py -q`.

### Task 3: Update docs and labels

**Files:**
- Modify: `README.md`
- Modify: `src/magnet_search/download.py`

- [ ] Update user docs to show `download movie.torrent`.
- [ ] Clarify that CSV values can be magnet links or torrent paths.
- [ ] Keep public CLI options backward compatible.

### Task 4: Verify

**Files:**
- Test: `tests/`

- [ ] Run `./.venv/bin/python -m pytest -q`.
- [ ] Review `git diff --stat` and ensure scope matches the feature.
