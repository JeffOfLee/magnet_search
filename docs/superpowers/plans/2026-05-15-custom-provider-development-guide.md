# Custom Provider Development Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a contributor-facing custom provider development guide and a short README entry that links to it.

**Architecture:** Keep `README.md` as the discovery surface and place implementation detail in a dedicated docs page. Base all guidance on the current provider protocol, built-in provider implementation, configuration model, and tests so the docs describe real extension points instead of speculative ones.

**Tech Stack:** Markdown, Python project source, pytest for verification

---

### Task 1: Draft the README extension entry

**Files:**
- Modify: `README.md`

- [ ] Add a short section that explains the extension split between configurable JSON HTTP providers and custom code-backed providers.
- [ ] Link the full guide from the README section.

### Task 2: Write the full development guide

**Files:**
- Create: `docs/custom-provider-development.md`

- [ ] Document when to prefer `JsonHttpProvider`.
- [ ] Document when to implement a custom provider.
- [ ] Explain the `Provider` protocol and `SearchResult` requirements.
- [ ] Include a minimal provider example that matches current project conventions.
- [ ] Explain error handling and `SearchService` warning behavior.
- [ ] Describe where provider wiring happens conceptually and how config changes should be handled.
- [ ] Add testing expectations and a contributor checklist.

### Task 3: Verify the docs against the codebase

**Files:**
- Review: `src/magnet_search/providers/base.py`
- Review: `src/magnet_search/providers/internet_archive.py`
- Review: `src/magnet_search/providers/manager.py`
- Review: `src/magnet_search/config.py`
- Review: `tests/test_internet_archive_provider.py`
- Review: `tests/test_provider_manager.py`

- [ ] Re-read the referenced code and confirm the guide does not describe unsupported behavior.
- [ ] Correct any drift found during that pass.

### Task 4: Run verification

**Files:**
- Test: `tests/`

- [ ] Run `pytest -q`
- [ ] Confirm the suite passes before closing the task.
