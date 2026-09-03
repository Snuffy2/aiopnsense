"""Tests for immutable release-check verification and workflow contracts."""

from collections.abc import Sequence
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
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
REF = "release-validation/v1.0.6-123-1"
SHA = "a" * 40


def test_dispatch_workflow_uses_expected_ref_sha_and_authoritative_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Send the candidate identity and accept only GitHub's returned run ID.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing the API helper.
    """
    calls: list[tuple[list[str], int | None]] = []

    def fake_api(arguments: Sequence[str], expected_status: int | None = None) -> dict[str, Any]:
        calls.append((list(arguments), expected_status))
        return {"workflow_run_id": 42}

    monkeypatch.setattr(verify, "github_api", fake_api)

    assert verify.dispatch_workflow(REPOSITORY, "validate.yml", REF, SHA) == 42
    arguments, status = calls[0]
    assert status == 200
    assert f"repos/{REPOSITORY}/actions/workflows/validate.yml/dispatches" in arguments
    assert f"ref={REF}" in arguments
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
        verify.dispatch_workflow(REPOSITORY, "validate.yml", REF, SHA)


def test_parse_required_checks_derives_workflows_in_first_seen_order() -> None:
    """Group required jobs while preserving workflow dispatch order."""
    checks = verify.parse_required_checks(
        [
            "pytest_check.yml::pytest and coverage report",
            "validate.yml::HACS Validation",
            "validate.yml::Hassfest Validation",
            "pytest_check.yml::pytest and coverage report",
        ]
    )

    assert list(checks) == ["pytest_check.yml", "validate.yml"]
    assert checks == {
        "pytest_check.yml": {"pytest and coverage report"},
        "validate.yml": {"HACS Validation", "Hassfest Validation"},
    }


def test_wait_for_workflow_requires_exact_identity_and_successful_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the exact dispatched run, Actions suite, and exact required job.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing API and time helpers.
    """
    responses = iter(
        [
            {"id": 7},
            {
                "id": 42,
                "workflow_id": 7,
                "event": "workflow_dispatch",
                "head_branch": REF,
                "head_sha": SHA,
                "status": "completed",
                "conclusion": "success",
                "check_suite_id": 99,
            },
            {"head_sha": SHA, "app": {"slug": "github-actions"}},
            {
                "total_count": 1,
                "jobs": [{"name": "HACS Validation", "conclusion": "success"}],
            },
        ]
    )
    monkeypatch.setattr(verify, "github_api", lambda _arguments: next(responses))
    monkeypatch.setattr(verify.time, "monotonic", lambda: 0.0)

    assert (
        verify.wait_for_workflow(
            REPOSITORY,
            "validate.yml",
            REF,
            SHA,
            {"HACS Validation"},
            deadline=1.0,
            expected_run_id=42,
        )
        == 42
    )


def test_wait_for_workflow_retries_a_transient_run_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry a transient run lookup failure before verifying its completed checks.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing API and time helpers.
    """
    responses: list[dict[str, Any] | RuntimeError] = [
        {"id": 7},
        verify.GitHubCommandError("HTTP 404 Not Found"),
        {
            "id": 42,
            "workflow_id": 7,
            "event": "workflow_dispatch",
            "head_branch": REF,
            "head_sha": SHA,
            "status": "completed",
            "conclusion": "success",
            "check_suite_id": 99,
        },
        {"head_sha": SHA, "app": {"slug": "github-actions"}},
        {
            "total_count": 1,
            "jobs": [{"name": "HACS Validation", "conclusion": "success"}],
        },
    ]
    sleeps: list[int] = []

    def fake_api(_arguments: Sequence[str]) -> dict[str, Any]:
        """Return the next scripted API response.

        Args:
            _arguments (Sequence[str]): API request arguments, unused by this scripted response.

        Returns:
            dict[str, Any]: The scripted API response.

        Raises:
            verify.GitHubCommandError: When simulating a transient run lookup failure.
        """
        response = responses.pop(0)
        if isinstance(response, RuntimeError):
            raise verify.GitHubCommandError(str(response))
        return response

    monkeypatch.setattr(verify, "github_api", fake_api)
    monkeypatch.setattr(verify.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(verify.time, "sleep", sleeps.append)

    assert (
        verify.wait_for_workflow(
            REPOSITORY,
            "validate.yml",
            REF,
            SHA,
            {"HACS Validation"},
            deadline=1.0,
            expected_run_id=42,
        )
        == 42
    )
    assert any(delay > 0 for delay in sleeps)


@pytest.mark.parametrize(
    "run",
    [
        {"id": 41},
        {
            "id": 42,
            "workflow_id": 7,
            "event": "push",
            "head_branch": REF,
            "head_sha": SHA,
        },
    ],
)
def test_wait_for_workflow_rejects_non_authoritative_identity(
    monkeypatch: pytest.MonkeyPatch, run: dict[str, Any]
) -> None:
    """Reject a mismatched run ID or workflow identity.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing API and time helpers.
        run (dict[str, Any]): Invalid workflow run response.
    """
    monkeypatch.setattr(
        verify,
        "github_api",
        lambda _arguments: {"id": 7} if "/workflows/" in _arguments[0] else run,
    )
    monkeypatch.setattr(verify.time, "monotonic", lambda: 0.0)

    with pytest.raises(verify.GitHubCommandError):
        verify.wait_for_workflow(
            REPOSITORY, "validate.yml", REF, SHA, set(), deadline=1.0, expected_run_id=42
        )


def test_verify_jobs_rejects_incomplete_required_check_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject required checks unless every requested job has one successful result.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing the API helper.
    """
    monkeypatch.setattr(
        verify,
        "github_api",
        lambda _arguments: {
            "total_count": 4,
            "jobs": [
                {"name": "required", "conclusion": "success"},
                {"name": "duplicate", "conclusion": "success"},
                {"name": "duplicate", "conclusion": "success"},
                {"name": "failed", "conclusion": "failure"},
            ],
        },
    )

    with pytest.raises(verify.GitHubCommandError):
        verify.verify_jobs(REPOSITORY, 42, {"required", "duplicate", "failed", "missing"})


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


def test_verify_check_suite_rejects_mismatched_candidate_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a check suite that is tied to a different candidate commit.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing the API helper.
    """
    monkeypatch.setattr(
        verify,
        "github_api",
        lambda _arguments: {"head_sha": "b" * 40, "app": {"slug": "github-actions"}},
    )

    with pytest.raises(verify.GitHubCommandError):
        verify.verify_check_suite(REPOSITORY, {"check_suite_id": 99}, SHA)


def test_release_workflow_uses_published_event_and_guarded_package_promotion() -> None:
    """Require published releases, exact gates, and lease-guarded package promotion."""
    workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    assert "release:\n    types: [published]" in workflow
    assert "inputs.tag" not in workflow
    assert "python3 .github/scripts/verify_release_checks.py" in workflow
    for check in (
        "pytest_check.yml::pytest check and post coverage",
        "docs.yml::build-docs",
        "uv-lock-check.yml::Validate uv lock consistency",
        "prek-autofix-review.yml::review",
    ):
        assert check in workflow
    assert "git push --atomic" in workflow
    assert '--force-with-lease="refs/heads/$RELEASE_TARGET:$TARGET_SHA"' in workflow
    assert '--force-with-lease="refs/tags/$RELEASE_TAG:$ORIGINAL_TAG_OID"' in workflow
    assert "git add aiopnsense/const.py docs/source/changelog.md" in workflow
    assert 'gh release upload "$RELEASE_TAG" dist/* --clobber' in workflow
    assert "pypa/gh-action-pypi-publish@release/v1" in workflow


@pytest.mark.parametrize(
    "workflow_name",
    ["pytest_check.yml", "docs.yml", "uv-lock-check.yml", "prek-autofix-review.yml"],
)
def test_release_gate_workflows_require_and_checkout_exact_sha(
    workflow_name: str,
) -> None:
    """Require every dispatched gate to validate and check out the candidate SHA.

    Args:
        workflow_name (str): Workflow filename under test.
    """
    workflow = (Path(__file__).parents[1] / ".github" / "workflows" / workflow_name).read_text(
        encoding="utf-8"
    )

    assert "expected_sha:" in workflow
    assert "required: true" in workflow
    assert '[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]]' in workflow
    assert 'test "$WORKFLOW_SHA" = "$EXPECTED_SHA"' in workflow
    assert "ref:" in workflow
    assert "inputs.expected_sha" in workflow
    assert "persist-credentials: false" in workflow
