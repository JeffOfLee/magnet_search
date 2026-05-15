# Download and S3 Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one `download` CLI command for single magnet downloads, CSV batch downloads, and optional S3 upload via a TOML upload config file.

**Architecture:** Add focused download and storage modules, then keep the CLI command as a thin orchestration layer. The downloader shells out to `aria2c` with test injection, and the S3 uploader lazily constructs a boto3 client only when upload is requested.

**Tech Stack:** Python 3.11, Typer, aria2c, boto3, pytest

---

### Task 1: Test and implement download execution

**Files:**
- Create: `tests/test_download.py`
- Create: `src/magnet_search/download.py`

- [ ] Write failing tests for aria2c command construction, downloaded file detection, and failure handling.
- [ ] Implement `Aria2cDownloader`, `DownloadResult`, and `DownloadError`.
- [ ] Run `./.venv/bin/python -m pytest tests/test_download.py -q`.

### Task 2: Test and implement CSV batch download

**Files:**
- Modify: `tests/test_download.py`
- Modify: `src/magnet_search/download.py`

- [ ] Write failing tests for default `magnet` column, custom column, blank rows, and missing column errors.
- [ ] Implement `run_download_batch`.
- [ ] Run `./.venv/bin/python -m pytest tests/test_download.py -q`.

### Task 3: Test and implement S3 upload config and uploader

**Files:**
- Create: `tests/test_storage.py`
- Create: `src/magnet_search/storage.py`
- Modify: `pyproject.toml`

- [ ] Write failing tests for TOML parsing, required bucket validation, optional fields, and upload key construction.
- [ ] Implement `S3UploadConfig`, `load_s3_upload_config`, `S3Uploader`, `UploadConfigError`, and `UploadError`.
- [ ] Add `boto3` to project dependencies.
- [ ] Run `./.venv/bin/python -m pytest tests/test_storage.py -q`.

### Task 4: Test and implement CLI command

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `src/magnet_search/cli.py`

- [ ] Write failing tests for single download routing, CSV routing, upload config routing, and clean error exits.
- [ ] Implement `magnet-search download`.
- [ ] Run `./.venv/bin/python -m pytest tests/test_cli.py -q`.

### Task 5: Document and verify

**Files:**
- Modify: `README.md`

- [ ] Add usage examples and upload config documentation.
- [ ] Run `./.venv/bin/python -m pytest -q`.
- [ ] Review `git diff` for scope and accuracy.
