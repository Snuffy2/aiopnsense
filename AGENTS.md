# AGENTS

## Purpose

- Provide clear, repo-specific instructions for autonomous agents working in this repository.

## General Guidelines

- Be concise and explain coding steps briefly when making code changes; include code snippets and tests where relevant.
- For non-trivial edits, provide a short plan. For small, low-risk edits, implement and include a one-line summary.
- Focus on a single conceptual change at a time when public APIs or multiple modules are affected.
- If deviating from these guidelines, explicitly state which guideline is deviated from and why.
- Any changes that require changes to both hass-opnsense and aiopnsense require coordinated branches in both repositories.

## Agent permissions and venv policy

- Agents may create a repository-local venv at `./.venv`.
- The project uses `pyproject.toml` dependency groups (`lint`, `pytest`, `dev`). Installing packages from repo manifests into `./.venv` is allowed for running tests or local tooling after approval; avoid unrelated network operations without explicit consent.

## Folder structure (repo-specific)

- `aiopnsense`: integration code.
- `tests`: pytest test suite and fixtures.
- `docs/source`: Sphinx documentation. The API reference is generated from docstrings; see `CODING_STANDARDS.md` (Structure) before adding public API.
- `README.md`: primary documentation.
- `.github/workflows`: GitHub Workflows
- `.github/scripts`: scripts for GitHub Workflows

## Coding standards

- Before editing code or tests, read [CODING_STANDARDS.md](CODING_STANDARDS.md) for Python compatibility, typing, docstrings, and validation.
- When changing a public method's return or exception behavior, trace its callers in both downstreams named in the [README](README.md#origin-and-purpose), hass-opnsense and Home Assistant Core. Identify which client instances reach the method and their effective `throw_errors` modes.
  Include caller-supplied values interpolated into endpoint paths, such as the interface name hass-opnsense passes to `reload_interface`.

## Local tooling note

- For test runs without worktree artifacts, use `PYTHONDONTWRITEBYTECODE=1 pytest --no-cov -p no:cacheprovider -o addopts=""`.
- The main checkout's venv installs `aiopnsense` editable from the main checkout. `pytest` run through it from a worktree imports the worktree's code, but ad-hoc scripts and probes (`python -c`, scratch scripts) import the main checkout. Run them with `PYTHONPATH` set to the worktree root and check `aiopnsense.__file__` before trusting the result.
- After changing docs, public API docstrings or `docs/source/api`, run `prek run --all-files --hook-stage manual docs-build` (without `--all-files` prek stashes unstaged edits and builds only the staged tree). It builds the docs with warnings as errors, as Read the Docs does, and installs the `docs` extra from `uv.lock` on first use.
- `pydoclint` is not installed in the venv and runs through prek. Lint docstrings with `prek run pydoclint --all-files`.

## Error handling & logging

- Catch specific exceptions (do not catch Exception directly).
- Add robust error handling and clear debug/info logs.
- If tests fail due to missing dev dependencies, either install them into `./.venv` (if allowed) or report exact `pip install` commands.

## Testing

- Use `importlib` only in workflow script or other script/module-loading tests.

## PR & branch behavior

- Create branches or PRs only when explicitly requested. Do not open PRs autonomously.
- Every pull request title must begin with a Conventional Commit prefix, such as `fix:`,
  `feat:`, or `chore(deps):`.

## Network / install consent

- Obtain explicit consent before any network operations outside the repository not strictly needed to run local tests.
- Package installs required for running tests are allowed when user approves.

## Live OPNsense testing

- If `scripts/aiopnsense.env` exists in the main checkout, the live scripts may be used for live tests.
- The env file is gitignored, so a worktree does not have its own copy. Its absence from a worktree does not mean live access is unavailable. Pass the main checkout's file with `--env-file`, and never copy it into a worktree or print, log, or commit its contents.
- From a worktree root, run a live script like this. `PYTHONPATH` makes `aiopnsense_dump.py` import the worktree's code:

  ```sh
  MAIN_CHECKOUT="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"
  PYTHONPATH="$PWD" "$MAIN_CHECKOUT/.venv/bin/python" scripts/opnsense_api_call.py \
    --env-file "$MAIN_CHECKOUT/scripts/aiopnsense.env" \
    --method get --endpoint /api/core/firmware/status
  ```

- `opnsense_api_call.py` output includes the Router URL and response headers. Report only the non-sensitive fields you need.
- Non-mutating live script commands or API endpoints may be used without permission.
- Mutating live script commands or API endpoints, including `POST /api/core/firmware/check`, require explicit permission before use.

## CI/CD

- Use GitHub Actions for CI/CD where applicable.

## Conventions for changes and documentation

- When editing code, prefer fixing root causes over surface patches.
- Keep changes minimal and consistent with the codebase style.
- Add tests for any changed behavior and update documentation if needed.

## Agent skills

### Issue tracker

Issues and specs are tracked as local markdown files under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
