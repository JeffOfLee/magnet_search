# Custom Provider Development Guide Design

## Goal

Add contributor-facing documentation that explains when to use the existing configurable JSON HTTP provider and when to implement a new code-backed provider that integrates with `SearchService`.

## Scope

- Add a short extension entry point to `README.md`.
- Add a full guide under `docs/`.
- Keep the guide aligned with the current `Provider` protocol, `SearchResult` model, `InternetArchiveProvider`, and `SearchService` behavior.

## Content decisions

- The README should stay short and route contributors to the full guide.
- The full guide should prioritize external contributors who have not read the codebase yet.
- The guide should explicitly describe the decision boundary between `JsonHttpProvider` and a custom provider.
- The guide should include the provider contract, implementation pattern, wiring notes, error handling, and testing expectations.

## Risks

- Documentation can drift from code if it invents wiring that does not exist in the current project.
- Over-explaining configurable providers in the main guide could bury the custom-provider workflow.

## Validation

- Re-read `README.md`, `providers/base.py`, `providers/internet_archive.py`, `providers/manager.py`, `config.py`, and provider tests while drafting.
- Run the test suite after editing docs to confirm no incidental breakage.
