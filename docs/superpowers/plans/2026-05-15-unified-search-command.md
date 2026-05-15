# Unified Search Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `magnet-search search` handle single-query and CSV batch search while preserving the existing `batch` command.

**Architecture:** Reuse the existing `run_batch` function and CLI warning printer. Add a small CSV source detector and a shared batch execution helper in `cli.py`, so the new `search` batch path and legacy `batch` command behave consistently.

**Tech Stack:** Python 3.11, Typer, pytest

---

### Task 1: Add CLI tests for unified search

**Files:**
- Modify: `tests/test_cli.py`

- [ ] Add a test for `search input.csv --output output.csv` using the default `query` column.
- [ ] Add a test for `search input.csv --column title --output output.csv`.
- [ ] Add a test that `search input.csv` exits cleanly when `--output` is missing.
- [ ] Run `./.venv/bin/python -m pytest tests/test_cli.py -q` and confirm the new tests fail.

### Task 2: Implement search routing

**Files:**
- Modify: `src/magnet_search/cli.py`

- [ ] Add or reuse a CSV source helper.
- [ ] Add a shared batch execution helper.
- [ ] Extend `search` with optional `--column` defaulting to `query` and optional `--output`.
- [ ] Route existing single-query behavior unchanged when the first argument is not an existing CSV file.
- [ ] Run `./.venv/bin/python -m pytest tests/test_cli.py -q` and confirm it passes.

### Task 3: Document the unified command

**Files:**
- Modify: `README.md`

- [ ] Update usage examples to prefer `search input.csv --output results.csv`.
- [ ] Mention that the legacy `batch` command remains available.
- [ ] Run `./.venv/bin/python -m pytest -q`.
