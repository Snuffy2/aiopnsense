# AGENTS

## Purpose

- Provide clear, repo-specific instructions for autonomous agents working in this repository.

## General Guidelines

- Be concise and explain coding steps briefly when making code changes; include code snippets and tests where relevant.
- For non-trivial edits, provide a short plan. For small, low-risk edits, implement and include a one-line summary.
- Focus on a single conceptual change at a time when public APIs or multiple modules are affected.
- Maintain project style and Python 3.14+ compatibility.
- If deviating from these guidelines, explicitly state which guideline is deviated from and why.
- Any changes that require changes to both hass-opnsense and aiopnsense require coordinated branches in both repositories.

## Agent permissions and venv policy

- Agents may create and use a repository-local venv at `./.venv`. Use `./.venv/bin/python`, `./.venv/bin/pytest`, and `./.venv/bin/prek` for local commands unless using the main checkout venv for a git worktree with no dependency changes.
- The project uses `pyproject.toml` dependency groups (`lint`, `pytest`, `dev`). Installing packages from repo manifests into `./.venv` is allowed for running tests or local tooling after approval; avoid unrelated network operations without explicit consent.

## Folder structure (repo-specific)

- `aiopnsense`: integration code.
- `tests`: pytest test suite and fixtures.
- `README.md`: primary documentation.
- `.github/workflows`: GitHub Workflows
- `.github/scripts`: scripts for GitHub Workflows

## Coding standards

- Add typing annotations to all functions and classes (including return types).
- Add or update docstrings for all files, classes and methods, including private methods and nested methods. Method docstrings must follow the Google Style.
- Preserve existing comments and keep imports at the top of files.
- Do not use `assert` or `cast` in main code.
- Follow existing repository style; run `prek`.
- Python 3.14 syntax is allowed, including PEP 695 type parameters and PEP 758 grouped exception handlers.

## Local tooling note

- Use the repo's `prek` and `pytest` commands through the applicable repo venv path (`./.venv` here, or the main checkout venv for a worktree with no dependency changes). Do not use system Python.
- By default, run the full pytest suite. If running targeted tests, explain why.

## Error handling & logging

- Catch specific exceptions (do not catch Exception directly).
- Add robust error handling and clear debug/info logs.
- If tests fail due to missing dev dependencies, either install them into `./.venv` (if allowed) or report exact `pip install` commands.

## Testing

- Use `pytest`.
- Add typed, well-documented tests in `tests/` and use fixtures in `conftest.py`.
- Use `importlib` only in workflow script or other script/module-loading tests; minimize `cast` and `Any` unless the test boundary requires them.
- Parameterize tests when appropriate; avoid duplicate test functions.

## PR & branch behavior

- Create branches or PRs only when explicitly requested. Do not open PRs autonomously.

## Network / install consent

- Obtain explicit consent before any network operations outside the repository not strictly needed to run local tests.
- Package installs required for running tests are allowed when user approves.

## CI/CD

- Use GitHub Actions for CI/CD where applicable.

## Conventions for changes and documentation

- When editing code, prefer fixing root causes over surface patches.
- Keep changes minimal and consistent with the codebase style.
- Add tests for any changed behavior and update documentation if needed.
