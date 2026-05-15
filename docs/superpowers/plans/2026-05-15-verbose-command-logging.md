# Verbose Command Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--verbose` to search, batch, and download commands while preserving stdout compatibility.

**Architecture:** Implement a CLI-level `_verbose()` helper that writes to stderr only when enabled. Thread the verbose flag through existing batch and download orchestration helpers so lower-level modules remain focused.

**Tech Stack:** Python 3.11, Typer, pytest

---

### Task 1: Add failing CLI tests

**Files:**
- Modify: `tests/test_cli.py`

- [ ] Test that `search --json --verbose` writes valid JSON to stdout and logs to stderr.
- [ ] Test that `search input.csv --output output.csv --verbose` logs batch routing.
- [ ] Test that `download ... --upload ... --verbose` logs download and upload progress.
- [ ] Run `./.venv/bin/python -m pytest tests/test_cli.py -q` and confirm failures.

### Task 2: Implement verbose logging

**Files:**
- Modify: `src/magnet_search/cli.py`

- [ ] Add `_verbose(enabled, message)`.
- [ ] Add `--verbose` options to `search`, `batch`, and `download`.
- [ ] Log command routing, counts, and completion events to stderr.
- [ ] Run `./.venv/bin/python -m pytest tests/test_cli.py -q`.

### Task 3: Document and verify

**Files:**
- Modify: `README.md`

- [ ] Add `--verbose` examples.
- [ ] Run `./.venv/bin/python -m pytest -q`.
