"""Tests for immutable release-check verification."""

from collections.abc import Sequence
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest

SCRIPT_PATH = Path(__file__).parents[1] / ".github" / "scripts" / "verify_release_checks.py"
SCRIPT_SPEC = importlib.util.spec_from_file_location("verify_release_checks", SCRIPT_PATH)
assert SCRIPT_SPEC is not None
assert SCRIPT_SPEC.loader is not None
verify = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(verify)

REPOSITORY = "owner/repository"
WORKFLOW_REF = "main"
WORKFLOW_SHA = "b" * 40
SHA = "a" * 40


def _successful_run() -> dict[str, Any]:
    """Return a completed workflow run for the immutable test candidate.

    Returns:
        dict[str, Any]: GitHub workflow-run response fixture.
    """
    return {
        "id": 42,
        "workflow_id": 7,
        "event": "workflow_dispatch",
        "head_branch": WORKFLOW_REF,
        "head_sha": WORKFLOW_SHA,
        "status": "completed",
        "conclusion": "success",
        "check_suite_id": 99,
    }


def test_dispatch_workflow_uses_expected_ref_sha_and_authoritative_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Send the candidate identity and accept only GitHub's returned run ID.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing the API helper.
    """
    calls: list[tuple[list[str], int | None]] = []

    def fake_api(arguments: Sequence[str], expected_status: int | None = None) -> dict[str, Any]:
        """Record one API request and return a dispatched workflow run.

        Args:
            arguments (Sequence[str]): GitHub CLI API arguments.
            expected_status (int | None): Required HTTP response status.

        Returns:
            dict[str, Any]: Dispatch response fixture.
        """
        calls.append((list(arguments), expected_status))
        return {"workflow_run_id": 42}

    monkeypatch.setattr(verify, "github_api", fake_api)

    assert verify.dispatch_workflow(REPOSITORY, "validation.yml", WORKFLOW_REF, SHA) == 42
    arguments, status = calls[0]
    assert status == 200
    assert "--include" in arguments
    assert arguments[arguments.index("--method") + 1] == "POST"
    assert "Accept: application/vnd.github+json" in arguments
    assert "X-GitHub-Api-Version: 2026-03-10" in arguments
    assert f"repos/{REPOSITORY}/actions/workflows/validation.yml/dispatches" in arguments
    assert "return_run_details=true" not in arguments
    assert f"ref={WORKFLOW_REF}" in arguments
    assert f"inputs[expected_sha]={SHA}" in arguments


@pytest.mark.parametrize("run_id", [None, 0, -1, True, "42"])
def test_dispatch_workflow_rejects_missing_or_invalid_run_id(
    monkeypatch: pytest.MonkeyPatch, run_id: object
) -> None:
    """Fail closed when the dispatch response is not an authoritative run ID.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing the API helper.
        run_id (object): Invalid response value.
    """
    monkeypatch.setattr(
        verify,
        "github_api",
        lambda _arguments, expected_status=None: {"workflow_run_id": run_id},
    )

    with pytest.raises(verify.GitHubCommandError):
        verify.dispatch_workflow(REPOSITORY, "validation.yml", WORKFLOW_REF, SHA)


def test_parse_required_checks_derives_workflows_in_first_seen_order() -> None:
    """Group required jobs while preserving workflow dispatch order."""
    checks = verify.parse_required_checks(
        [
            "validation.yml::Test and build",
            "uv-lock-check.yml::Validate uv lock consistency",
            "validation.yml::Test and build",
        ]
    )

    assert list(checks) == ["validation.yml", "uv-lock-check.yml"]
    assert checks == {
        "validation.yml": {"Test and build"},
        "uv-lock-check.yml": {"Validate uv lock consistency"},
    }


@pytest.mark.parametrize("value", ["workflow", "::job", "workflow::"])
def test_parse_required_checks_rejects_invalid_required_check(value: str) -> None:
    """Reject malformed workflow and required-job mappings.

    Args:
        value (str): Invalid required-check value.
    """
    with pytest.raises(ValueError):
        verify.parse_required_checks([value])


def test_wait_for_workflow_requires_exact_identity_actions_suite_and_successful_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the dispatched run, Actions suite, and exact required job.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing API and time helpers.
    """

    def fake_api(arguments: Sequence[str]) -> dict[str, Any]:
        """Return endpoint-specific GitHub responses for the valid run.

        Args:
            arguments (Sequence[str]): GitHub CLI API arguments.

        Returns:
            dict[str, Any]: Response fixture for the requested endpoint.

        Raises:
            AssertionError: If the verifier requests an unexpected endpoint.
        """
        endpoint = arguments[0]
        if endpoint.endswith("/actions/workflows/validation.yml"):
            return {"id": 7}
        if endpoint.endswith("/actions/runs/42"):
            return _successful_run()
        if endpoint.endswith("/check-suites/99"):
            return {"head_sha": WORKFLOW_SHA, "app": {"slug": "github-actions"}}
        if endpoint.endswith("/actions/runs/42/jobs?per_page=100"):
            return {
                "total_count": 1,
                "jobs": [{"name": "Test and build", "conclusion": "success"}],
            }
        raise AssertionError(f"Unexpected GitHub API endpoint: {endpoint}")

    monkeypatch.setattr(verify, "github_api", fake_api)
    monkeypatch.setattr(verify.time, "monotonic", lambda: 0.0)

    assert (
        verify.wait_for_workflow(
            REPOSITORY,
            "validation.yml",
            WORKFLOW_REF,
            WORKFLOW_SHA,
            SHA,
            {"Test and build"},
            deadline=1.0,
            expected_run_id=42,
        )
        == 42
    )


def test_wait_for_workflow_completes_after_pending_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Poll an authoritative run until its completion before checking results.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing API and time helpers.
    """
    pending_run = _successful_run()
    pending_run["status"] = "in_progress"
    pending_run.pop("conclusion")
    responses = iter([pending_run, _successful_run()])
    sleeps: list[float] = []

    def fake_api(arguments: Sequence[str]) -> dict[str, Any]:
        """Return one pending response before the completed run fixture.

        Args:
            arguments (Sequence[str]): GitHub CLI API arguments.

        Returns:
            dict[str, Any]: Response fixture for the requested endpoint.

        Raises:
            AssertionError: If the verifier requests an unexpected endpoint.
        """
        endpoint = arguments[0]
        if endpoint.endswith("/actions/workflows/validation.yml"):
            return {"id": 7}
        if endpoint.endswith("/actions/runs/42"):
            return next(responses)
        if endpoint.endswith("/check-suites/99"):
            return {"head_sha": WORKFLOW_SHA, "app": {"slug": "github-actions"}}
        if endpoint.endswith("/actions/runs/42/jobs?per_page=100"):
            return {
                "total_count": 1,
                "jobs": [{"name": "Test and build", "conclusion": "success"}],
            }
        raise AssertionError(f"Unexpected GitHub API endpoint: {endpoint}")

    monkeypatch.setattr(verify, "github_api", fake_api)
    monkeypatch.setattr(verify.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(verify.time, "sleep", sleeps.append)

    assert (
        verify.wait_for_workflow(
            REPOSITORY,
            "validation.yml",
            WORKFLOW_REF,
            WORKFLOW_SHA,
            SHA,
            {"Test and build"},
            deadline=1.0,
            expected_run_id=42,
        )
        == 42
    )
    assert sleeps == [10]


@pytest.mark.parametrize("conclusion", ["failure", "cancelled"])
def test_wait_for_workflow_rejects_unsuccessful_conclusion(
    monkeypatch: pytest.MonkeyPatch, conclusion: str
) -> None:
    """Reject a completed run that did not conclude successfully.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing API and time helpers.
        conclusion (str): Completed workflow conclusion to reject.
    """
    run = _successful_run()
    run["conclusion"] = conclusion
    monkeypatch.setattr(
        verify,
        "github_api",
        lambda arguments: {"id": 7} if "/workflows/" in arguments[0] else run,
    )
    monkeypatch.setattr(verify.time, "monotonic", lambda: 0.0)

    with pytest.raises(verify.GitHubCommandError, match=conclusion):
        verify.wait_for_workflow(
            REPOSITORY,
            "validation.yml",
            WORKFLOW_REF,
            WORKFLOW_SHA,
            SHA,
            set(),
            deadline=1.0,
            expected_run_id=42,
        )


def test_wait_for_workflow_times_out_after_bounded_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop retrying a transient run lookup when the shared deadline expires.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing API and time helpers.
    """
    clock = iter([0.0, 1.0])
    sleeps: list[float] = []

    def fake_api(arguments: Sequence[str]) -> dict[str, Any]:
        """Return metadata and report that the run has not propagated yet.

        Args:
            arguments (Sequence[str]): GitHub CLI API arguments.

        Returns:
            dict[str, Any]: Workflow metadata.

        Raises:
            verify.GitHubCommandError: When the run has not propagated yet.
        """
        if "/workflows/" in arguments[0]:
            return {"id": 7}
        raise verify.GitHubCommandError("HTTP 404 Not Found")

    monkeypatch.setattr(verify, "github_api", fake_api)
    monkeypatch.setattr(verify.time, "monotonic", lambda: next(clock, 1.0))
    monkeypatch.setattr(verify.time, "sleep", sleeps.append)

    with pytest.raises(verify.GitHubCommandError, match="Timed out"):
        verify.wait_for_workflow(
            REPOSITORY,
            "validation.yml",
            WORKFLOW_REF,
            WORKFLOW_SHA,
            SHA,
            set(),
            deadline=1.0,
            expected_run_id=42,
        )
    assert sleeps == [5]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("id", 41),
        ("workflow_id", 8),
        ("event", "push"),
        ("head_branch", "other"),
        ("head_sha", "c" * 40),
    ],
)
def test_wait_for_workflow_rejects_non_authoritative_identity(
    monkeypatch: pytest.MonkeyPatch, key: str, value: object
) -> None:
    """Reject every mismatched run-identity field.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing API and time helpers.
        key (str): Workflow-run field to replace.
        value (object): Mismatched field value.
    """
    run = _successful_run()
    run[key] = value
    monkeypatch.setattr(
        verify,
        "github_api",
        lambda arguments: {"id": 7} if "/workflows/" in arguments[0] else run,
    )
    monkeypatch.setattr(verify.time, "monotonic", lambda: 0.0)

    with pytest.raises(verify.GitHubCommandError):
        verify.wait_for_workflow(
            REPOSITORY,
            "validation.yml",
            WORKFLOW_REF,
            WORKFLOW_SHA,
            SHA,
            set(),
            deadline=1.0,
            expected_run_id=42,
        )


@pytest.mark.parametrize(
    "error_message",
    [
        "HTTP 404 Not Found",
        "HTTP 429 Too Many Requests",
        "HTTP/2 503 Service Unavailable",
    ],
)
def test_wait_for_workflow_retries_explicit_transient_run_lookup_errors(
    monkeypatch: pytest.MonkeyPatch, error_message: str
) -> None:
    """Retry explicit transient run lookup failures before verifying completed checks.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing API and time helpers.
        error_message (str): Explicit transient HTTP diagnostic returned by the CLI.
    """
    run_attempts = 0
    sleeps: list[int] = []

    def fake_api(arguments: Sequence[str]) -> dict[str, Any]:
        """Return a transient run error followed by valid endpoint responses.

        Args:
            arguments (Sequence[str]): GitHub CLI API arguments.

        Returns:
            dict[str, Any]: Response fixture for the requested endpoint.

        Raises:
            verify.GitHubCommandError: For the transient workflow-run lookup.
            AssertionError: If the verifier requests an unexpected endpoint.
        """
        nonlocal run_attempts
        endpoint = arguments[0]
        if endpoint.endswith("/actions/workflows/validation.yml"):
            return {"id": 7}
        if endpoint.endswith("/actions/runs/42"):
            run_attempts += 1
            if run_attempts == 1:
                raise verify.GitHubCommandError(error_message)
            return _successful_run()
        if endpoint.endswith("/check-suites/99"):
            return {"head_sha": WORKFLOW_SHA, "app": {"slug": "github-actions"}}
        if endpoint.endswith("/actions/runs/42/jobs?per_page=100"):
            return {
                "total_count": 1,
                "jobs": [{"name": "Test and build", "conclusion": "success"}],
            }
        raise AssertionError(f"Unexpected GitHub API endpoint: {endpoint}")

    monkeypatch.setattr(verify, "github_api", fake_api)
    monkeypatch.setattr(verify.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(verify.time, "sleep", sleeps.append)

    assert (
        verify.wait_for_workflow(
            REPOSITORY,
            "validation.yml",
            WORKFLOW_REF,
            WORKFLOW_SHA,
            SHA,
            {"Test and build"},
            deadline=1.0,
            expected_run_id=42,
        )
        == 42
    )
    assert any(delay > 0 for delay in sleeps)


@pytest.mark.parametrize(
    "error_message",
    [
        "HTTP 401 Unauthorized",
        "HTTP 403 Forbidden",
        "HTTP 418 I'm a teapot",
        "GitHub CLI exited after 503 retries",
    ],
)
def test_wait_for_workflow_rejects_nonretryable_run_lookup_errors(
    monkeypatch: pytest.MonkeyPatch, error_message: str
) -> None:
    """Fail immediately for non-transient or non-HTTP run lookup failures.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing API and time helpers.
        error_message (str): GitHub CLI diagnostic that must not be retried.
    """
    run_attempts = 0
    sleeps: list[int] = []

    def fake_api(arguments: Sequence[str]) -> dict[str, Any]:
        """Return workflow metadata and fail the exact dispatched-run lookup.

        Args:
            arguments (Sequence[str]): GitHub API arguments.

        Returns:
            dict[str, Any]: Workflow metadata for the expected endpoint.

        Raises:
            verify.GitHubCommandError: For the exact workflow-run lookup.
            AssertionError: If the verifier requests an unexpected endpoint.
        """
        nonlocal run_attempts
        endpoint = arguments[0]
        if endpoint.endswith("/actions/workflows/validation.yml"):
            return {"id": 7}
        if endpoint.endswith("/actions/runs/42"):
            run_attempts += 1
            raise verify.GitHubCommandError(error_message)
        raise AssertionError(f"Unexpected GitHub API endpoint: {endpoint}")

    monkeypatch.setattr(verify, "github_api", fake_api)
    monkeypatch.setattr(verify.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(verify.time, "sleep", sleeps.append)

    with pytest.raises(verify.GitHubCommandError) as raised:
        verify.wait_for_workflow(
            REPOSITORY,
            "validation.yml",
            WORKFLOW_REF,
            WORKFLOW_SHA,
            SHA,
            set(),
            deadline=1.0,
            expected_run_id=42,
        )

    assert str(raised.value) == error_message
    assert run_attempts == 1
    assert not sleeps


@pytest.mark.parametrize(
    "payload",
    [
        {"total_count": 2, "jobs": [{"name": "required", "conclusion": "success"}]},
        {
            "total_count": 4,
            "jobs": [
                {"name": "required", "conclusion": "success"},
                {"name": "duplicate", "conclusion": "success"},
                {"name": "duplicate", "conclusion": "success"},
                {"name": "failed", "conclusion": "failure"},
            ],
        },
    ],
)
def test_verify_jobs_rejects_truncated_or_invalid_required_results(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> None:
    """Reject truncated, missing, duplicate, or unsuccessful required jobs.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing the API helper.
        payload (dict[str, Any]): Jobs API response fixture.
    """
    monkeypatch.setattr(verify, "github_api", lambda _arguments: payload)

    with pytest.raises(verify.GitHubCommandError):
        verify.verify_jobs(REPOSITORY, 42, {"required", "duplicate", "failed", "missing"})


@pytest.mark.parametrize(
    "suite",
    [
        {"head_sha": "c" * 40, "app": {"slug": "github-actions"}},
        {"head_sha": WORKFLOW_SHA, "app": {"slug": "another-app"}},
    ],
)
def test_verify_check_suite_rejects_mismatched_candidate_or_non_actions_suite(
    monkeypatch: pytest.MonkeyPatch, suite: dict[str, Any]
) -> None:
    """Reject check suites that do not prove the candidate's Actions execution.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing the API helper.
        suite (dict[str, Any]): Invalid check-suite response fixture.
    """
    monkeypatch.setattr(verify, "github_api", lambda _arguments: suite)

    with pytest.raises(verify.GitHubCommandError):
        verify.verify_check_suite(REPOSITORY, {"check_suite_id": 99}, WORKFLOW_SHA)


def test_github_api_rejects_malformed_or_non_authoritative_http_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed for absent CLI, malformed JSON, and wrong dispatch status.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing CLI discovery and execution.
    """
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(verify.GitHubCommandError):
        verify.github_api([])

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="API failed"),
    )
    with pytest.raises(verify.GitHubCommandError, match="API failed"):
        verify.github_api([])

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout="HTTP/2 204 No Content\n\n", stderr=""
        ),
    )
    with pytest.raises(verify.GitHubCommandError):
        verify.github_api([], expected_status=200)

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="{", stderr=""),
    )
    with pytest.raises(json.JSONDecodeError):
        verify.github_api([])


def test_github_api_converts_timeout_to_github_command_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Convert a timed-out GitHub CLI request into the script error type.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing CLI discovery and execution.
    """
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/gh")

    def raise_timeout(*_args: Any, **_kwargs: Any) -> None:
        """Raise the subprocess timeout reported by a stalled CLI request.

        Args:
            _args (Any): Positional subprocess arguments.
            _kwargs (Any): Keyword subprocess arguments.

        Raises:
            subprocess.TimeoutExpired: Always, to emulate a stalled command.
        """
        raise subprocess.TimeoutExpired("gh api", 30)

    monkeypatch.setattr(subprocess, "run", raise_timeout)

    with pytest.raises(verify.GitHubCommandError, match="timed out"):
        verify.github_api([])


def test_github_api_uses_bounded_request_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bound each GitHub request so polling cannot wait forever.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing CLI discovery and execution.
    """
    calls: list[dict[str, Any]] = []

    def fake_run(*_args: object, **kwargs: Any) -> SimpleNamespace:
        """Record the subprocess options and return an empty JSON object.

        Args:
            _args (object): Positional subprocess arguments.
            kwargs (Any): Keyword subprocess arguments.

        Returns:
            SimpleNamespace: Successful subprocess result fixture.
        """
        calls.append(kwargs)
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert verify.github_api(["repos/example/repository"]) == {}
    assert calls[0]["timeout"] == 30


def test_github_api_rejects_valid_json_non_object_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject valid JSON responses that cannot provide API object fields.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing CLI discovery and execution.
    """
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/gh")

    def return_scalar(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        """Return a successful CLI response containing a JSON scalar.

        Args:
            _args (Any): Positional subprocess arguments.
            _kwargs (Any): Keyword subprocess arguments.

        Returns:
            SimpleNamespace: Successful subprocess result fixture.
        """
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(subprocess, "run", return_scalar)

    with pytest.raises(verify.GitHubCommandError, match="not an object"):
        verify.github_api([])


def test_publish_verified_status_attests_exact_check_and_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publish a successful commit status only for a verified check and run.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing the API helper.
    """
    calls: list[list[str]] = []

    def fake_api(arguments: Sequence[str]) -> dict[str, Any]:
        """Record a status request and return GitHub's confirmation.

        Args:
            arguments (Sequence[str]): GitHub CLI API arguments.

        Returns:
            dict[str, Any]: Confirmed successful status context.
        """
        calls.append(list(arguments))
        return {"context": "required", "state": "success"}

    monkeypatch.setattr(verify, "github_api", fake_api)

    verify.publish_verified_status(REPOSITORY, SHA, "required", 42)

    arguments = calls[0]
    assert f"repos/{REPOSITORY}/statuses/{SHA}" in arguments
    assert "state=success" in arguments
    assert "context=required" in arguments
    assert f"target_url=https://github.com/{REPOSITORY}/actions/runs/42" in arguments


def test_main_publishes_statuses_only_after_every_workflow_is_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wait for all gate verifications before publishing any required status.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing orchestration helpers.
    """
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_release_checks.py",
            "--repository",
            REPOSITORY,
            "--workflow-ref",
            WORKFLOW_REF,
            "--workflow-sha",
            WORKFLOW_SHA,
            "--sha",
            SHA,
            "--required-check",
            "first.yml::first",
            "--required-check",
            "second.yml::second",
        ],
    )
    monkeypatch.setattr(
        verify,
        "dispatch_workflow",
        lambda _repository, workflow, _ref, _sha: 41 if workflow == "first.yml" else 42,
    )

    def fake_wait(
        _repository: str,
        workflow: str,
        _ref: str,
        _workflow_sha: str,
        _sha: str,
        _required_checks: set[str],
        _deadline: float,
        run_id: int,
    ) -> int:
        """Record a completed verification and return its authoritative run ID.

        Args:
            _repository (str): Unused GitHub repository name.
            workflow (str): Workflow filename being verified.
            _ref (str): Unused validation branch name.
            _workflow_sha (str): Unused trusted workflow commit SHA.
            _sha (str): Unused candidate commit SHA.
            _required_checks (set[str]): Unused required job names.
            _deadline (float): Unused verification deadline.
            run_id (int): Authoritative dispatched workflow run ID.

        Returns:
            int: The authoritative workflow run ID.
        """
        events.append(("verify", workflow))
        return run_id

    monkeypatch.setattr(
        verify,
        "wait_for_workflow",
        fake_wait,
    )
    monkeypatch.setattr(
        verify,
        "publish_verified_status",
        lambda _repository, _sha, check, _run_id: events.append(("publish", check)),
    )

    assert verify.main() == 0
    assert len(events) == 4
    assert set(events) == {
        ("verify", "first.yml"),
        ("verify", "second.yml"),
        ("publish", "first"),
        ("publish", "second"),
    }
    verification_indices = [index for index, event in enumerate(events) if event[0] == "verify"]
    publication_indices = [index for index, event in enumerate(events) if event[0] == "publish"]
    assert max(verification_indices) < min(publication_indices)


def test_main_uses_a_4200_second_default_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the release-gate default deadline while retaining bounded API calls.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing orchestration helpers.
    """
    deadlines: list[float] = []
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_release_checks.py",
            "--repository",
            REPOSITORY,
            "--workflow-ref",
            WORKFLOW_REF,
            "--workflow-sha",
            WORKFLOW_SHA,
            "--sha",
            SHA,
            "--required-check",
            "validation.yml::Test and build",
        ],
    )
    monkeypatch.setattr(verify, "dispatch_workflow", lambda *_args: 42)
    monkeypatch.setattr(verify.time, "monotonic", lambda: 100.0)

    def fake_wait(
        _repository: str,
        _workflow: str,
        _workflow_ref: str,
        _workflow_sha: str,
        _sha: str,
        _required_checks: set[str],
        deadline: float,
        expected_run_id: int,
    ) -> int:
        """Record the release-gate deadline and return the verified run.

        Args:
            _repository (str): Unused GitHub repository name.
            _workflow (str): Unused workflow filename.
            _workflow_ref (str): Unused trusted workflow branch.
            _workflow_sha (str): Unused trusted workflow SHA.
            _sha (str): Unused candidate SHA.
            _required_checks (set[str]): Unused exact required job names.
            deadline (float): Monotonic deadline passed to the workflow verifier.
            expected_run_id (int): Authoritative workflow run ID.

        Returns:
            int: The verified authoritative workflow run ID.
        """
        deadlines.append(deadline)
        return expected_run_id

    monkeypatch.setattr(verify, "wait_for_workflow", fake_wait)
    monkeypatch.setattr(verify, "publish_verified_status", lambda *_args: None)

    assert verify.main() == 0
    assert deadlines == [4300.0]


def test_main_withholds_all_statuses_when_a_later_workflow_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Withhold every status when any required release gate fails.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing orchestration helpers.
    """
    published: list[tuple[str, str, str, int]] = []
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_release_checks.py",
            "--repository",
            REPOSITORY,
            "--workflow-ref",
            WORKFLOW_REF,
            "--workflow-sha",
            WORKFLOW_SHA,
            "--sha",
            SHA,
            "--required-check",
            "first.yml::first",
            "--required-check",
            "second.yml::second",
        ],
    )
    monkeypatch.setattr(
        verify,
        "dispatch_workflow",
        lambda _repository, workflow, _ref, _sha: 41 if workflow == "first.yml" else 42,
    )

    def fake_wait(
        _repository: str,
        workflow: str,
        _ref: str,
        _workflow_sha: str,
        _sha: str,
        _required_checks: set[str],
        _deadline: float,
        expected_run_id: int,
    ) -> int:
        """Return the first run ID and fail the second workflow verification.

        Args:
            _repository (str): Unused GitHub repository name.
            workflow (str): Workflow filename being verified.
            _ref (str): Unused validation branch name.
            _workflow_sha (str): Unused trusted workflow commit SHA.
            _sha (str): Unused candidate commit SHA.
            _required_checks (set[str]): Unused required job names.
            _deadline (float): Unused verification deadline.
            expected_run_id (int): Authoritative dispatched workflow run ID.

        Returns:
            int: The authoritative run ID for the first workflow.

        Raises:
            verify.GitHubCommandError: When verifying the simulated second workflow.
        """
        if workflow == "second.yml":
            raise verify.GitHubCommandError("second gate failed")
        return expected_run_id

    monkeypatch.setattr(verify, "wait_for_workflow", fake_wait)
    monkeypatch.setattr(
        verify,
        "publish_verified_status",
        lambda repository, sha, check, run_id: published.append((repository, sha, check, run_id)),
    )

    assert verify.main() == 1
    assert published == []
