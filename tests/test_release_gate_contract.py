"""Shared semantic contracts for every release-dispatched workflow gate."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
from typing import Final

import pytest

from tests.release_gate_contract_adapter import GATES

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT: Final = REPO_ROOT / ".github" / "workflows"
TIMEOUT_MINUTES: Final = {
    "pytest": "30",
    "docs": "15",
    "lock": "15",
    "prek": "15",
    "firmware": "60",
}
PRODUCT_COMMANDS: Final = {
    "pytest": ("uv run --locked --group pytest pytest",),
    "docs": (
        "uv run --locked --extra docs sphinx-build -b html",
        "uv run --locked --extra docs sphinx-build -b doctest",
    ),
    "lock": ("uv lock --check",),
    "prek": ("UV_LOCKED=1 uv run --locked --group dev prek run --all-files",),
    "firmware": ("uv sync --locked --group dev", "./scripts/build"),
}
CONCURRENCY_TOKENS: Final = (
    "github.workflow",
    "github.event.pull_request.head.repo.full_name || github.repository",
    "github.event.pull_request.number",
    "github.event.pull_request.head.ref",
    "inputs.expected_sha",
    "github.ref",
)


def workflow_text(name: str) -> str:
    """Read one workflow artifact.

    Args:
        name (str): Workflow filename.

    Returns:
        str: Complete workflow YAML text.
    """
    return (WORKFLOW_ROOT / name).read_text(encoding="utf-8")


def job_block(workflow: str, job: str) -> str:
    """Extract one top-level GitHub Actions job.

    Args:
        workflow (str): Complete workflow YAML text.
        job (str): Required job identifier.

    Returns:
        str: YAML text for the selected job.
    """
    match = re.search(rf"(?ms)^  {re.escape(job)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)", workflow)
    assert match is not None
    return match.group(0)


def steps(job: str) -> list[str]:
    """Split a job into its workflow steps.

    Args:
        job (str): YAML text for one job.

    Returns:
        list[str]: Ordered step YAML blocks.
    """
    values = re.split(r"(?m)(?=^      - )", job)
    return [value for value in values if value.startswith("      - ")]


def step_with(job: str, *tokens: str) -> str:
    """Find exactly one step containing every required semantic token.

    Args:
        job (str): YAML text for one job.
        *tokens (str): Tokens that identify the required step.

    Returns:
        str: Matching step YAML block.
    """
    matches = [step for step in steps(job) if all(token in step for token in tokens)]
    assert len(matches) == 1
    return matches[0]


def dispatch_input(workflow: str) -> None:
    """Assert that workflow dispatch accepts a typed immutable candidate SHA.

    Args:
        workflow (str): Complete workflow YAML text.
    """
    assert re.search(
        r"(?ms)^  workflow_dispatch:\n.*?^      expected_sha:\n.*?^        required: true\n.*?^        type: string$",
        workflow,
    )


def gate_policy(workflow: str, gate: dict[str, str]) -> None:
    """Assert the common release-gate execution policy.

    Args:
        workflow (str): Complete workflow YAML text.
        gate (dict[str, str]): Repository-local identity and product data for the gate.
    """
    dispatch_input(workflow)
    job = job_block(workflow, gate["job"])
    assert f"    timeout-minutes: {TIMEOUT_MINUTES[gate['product']]}" in job
    permissions = re.search(r"(?ms)^    permissions:\n(.*?)(?=^    [A-Za-z_-]+:|^      - |\Z)", job)
    if permissions is None:
        permissions = re.search(r"(?ms)^permissions:\n(.*?)(?=^\S|\Z)", workflow)
    assert permissions is not None
    permission_text = permissions.group(0)
    assert "contents: read" in permission_text
    assert not re.search(r": (?:write|admin)\b", permission_text)
    assert not re.search(
        r"^\s{2,}(?!contents:|pull-requests:)[A-Za-z-]+: read$",
        permission_text,
        re.MULTILINE,
    )
    concurrency = re.search(r"(?ms)^concurrency:\n(.*?)(?=^\S|\Z)", workflow)
    assert concurrency is not None
    assert all(token in concurrency.group(0) for token in CONCURRENCY_TOKENS)
    assert "cancel-in-progress: true" in concurrency.group(0)
    guard = step_with(job, "EXPECTED_SHA", "[0-9a-f]{40}")
    assert "set -euo pipefail" in guard
    assert '[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]]' in guard
    checkout = step_with(job, "actions/checkout@", "inputs.expected_sha")
    assert "persist-credentials: false" in checkout
    head = step_with(job, "git rev-parse HEAD", "EXPECTED_SHA")
    assert '"$(git rev-parse HEAD)"' in head
    assert '"$EXPECTED_SHA"' in head
    assert all(command in job for command in PRODUCT_COMMANDS[gate["product"]])
    setup_uv = step_with(job, "astral-sh/setup-uv@v10.0.1")
    assert re.search(r"python-version: ['\"]3\.14['\"]", setup_uv)
    for option in (
        "enable-cache: true",
        "ignore-nothing-to-cache: true",
        "cache-dependency-glob: uv.lock",
    ):
        assert option in setup_uv
    if gate["product"] == "prek":
        assert 'test -z "$(git status --porcelain)"' in job


def declared_release_checks() -> set[str]:
    """Extract the complete release-controller required-check manifest.

    Returns:
        set[str]: Declared workflow and job identities.
    """
    release = workflow_text("release.yml")
    checks = {
        check
        for check in re.findall(r"--required-check ['\"]([^'\"]+)['\"]", release)
        if ".yml::" in check
    }
    required = re.search(r"(?ms)^  REQUIRED_CHECKS: \|\n(.*?)(?=^\S|\Z)", release)
    if required is not None:
        checks.update(line.strip() for line in required.group(1).splitlines() if line.strip())
    return checks


def shell(cwd: Path, script: str, **environment: str) -> subprocess.CompletedProcess[str]:
    """Run an extracted workflow shell fragment.

    Args:
        cwd (Path): Temporary repository where the fragment runs.
        script (str): Shell fragment extracted from a production workflow.
        **environment (str): Environment variables for the fragment.

    Returns:
        subprocess.CompletedProcess[str]: Completed shell process without automatic error raising.
    """
    return subprocess.run(
        ["bash", "-c", script],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, **environment},
    )


def git(cwd: Path, *arguments: str) -> str:
    """Run a successful Git command in a temporary repository.

    Args:
        cwd (Path): Temporary repository where Git runs.
        *arguments (str): Arguments after the Git executable.

    Returns:
        str: Stripped standard output.
    """
    result = subprocess.run(
        ["git", *arguments], cwd=cwd, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def commits(tmp_path: Path) -> tuple[Path, str, str]:
    """Create distinct immutable candidate and trusted-controller commits.

    Args:
        tmp_path (Path): Test-provided temporary directory.

    Returns:
        tuple[Path, str, str]: Repository path, candidate SHA, and newer controller SHA.
    """
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.name", "Release Test")
    git(repository, "config", "user.email", "release@example.invalid")
    (repository / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    git(repository, "add", "candidate.txt")
    git(repository, "commit", "-m", "Candidate")
    candidate = git(repository, "rev-parse", "HEAD")
    (repository / "controller.txt").write_text("controller\n", encoding="utf-8")
    git(repository, "add", "controller.txt")
    git(repository, "commit", "-m", "Controller")
    return repository, candidate, git(repository, "rev-parse", "HEAD")


def test_release_manifest_matches_complete_adapter_inventory() -> None:
    """Require local adapter data to cover every release-dispatched gate."""
    assert declared_release_checks() == {gate["required_check"] for gate in GATES}


@pytest.mark.parametrize("gate", GATES, ids=lambda gate: gate["workflow"])
def test_gate_enforces_shared_release_contract(gate: dict[str, str]) -> None:
    """Require every release callee to satisfy the shared policy.

    Args:
        gate (dict[str, str]): Repository-local identity and product data for the gate.
    """
    gate_policy(workflow_text(gate["workflow"]), gate)


@pytest.mark.parametrize("gate", GATES, ids=lambda gate: gate["workflow"])
def test_gate_shells_distinguish_trusted_controller_from_candidate(
    tmp_path: Path, gate: dict[str, str]
) -> None:
    """Prove guards distinguish trusted controller and candidate commits.

    Args:
        tmp_path (Path): Test-provided temporary directory.
        gate (dict[str, str]): Repository-local identity and product data for the gate.
    """
    repository, candidate, controller = commits(tmp_path)
    job = job_block(workflow_text(gate["workflow"]), gate["job"])
    guard = step_with(job, "EXPECTED_SHA", "[0-9a-f]{40}").split("run: |", maxsplit=1)[1]
    head = step_with(job, "git rev-parse HEAD", "EXPECTED_SHA").split("run: |", maxsplit=1)[1]
    assert shell(repository, guard, EXPECTED_SHA=candidate).returncode == 0
    assert shell(repository, guard, EXPECTED_SHA="bad").returncode != 0
    git(repository, "checkout", "--detach", candidate)
    assert shell(repository, head, EXPECTED_SHA=candidate).returncode == 0
    git(repository, "checkout", "--detach", controller)
    assert shell(repository, head, EXPECTED_SHA=candidate).returncode != 0


def test_prek_porcelain_cleanliness_rejects_all_change_classes(tmp_path: Path) -> None:
    """Prove the dispatched porcelain check rejects every local change class.

    Args:
        tmp_path (Path): Test-provided temporary directory.
    """
    gate = next(gate for gate in GATES if gate["product"] == "prek")
    job = job_block(workflow_text(gate["workflow"]), gate["job"])
    match = re.search(r'^\s*(test -z "\$\(git status --porcelain\)".*)$', job, re.MULTILINE)
    assert match is not None
    repository, _candidate, _controller = commits(tmp_path)
    assert shell(repository, match.group(1)).returncode == 0
    (repository / "candidate.txt").write_text("changed\n", encoding="utf-8")
    assert shell(repository, match.group(1)).returncode != 0
    git(repository, "add", "candidate.txt")
    assert shell(repository, match.group(1)).returncode != 0
    git(repository, "reset", "--hard", "HEAD")
    (repository / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    assert shell(repository, match.group(1)).returncode != 0


def test_common_policy_rejects_known_workflow_fault_mutations() -> None:
    """Require shared policy checks to reject known workflow regression mutations."""
    gate = next(gate for gate in GATES if gate["product"] == "prek")
    original = workflow_text(gate["workflow"])
    mutations = (
        lambda text: text.replace("UV_LOCKED=1 ", "", 1),
        lambda text: text.replace("inputs.expected_sha", "github.sha", 1),
        lambda text: re.sub(r"(?m)^  group:.*$", "  group: ${{ github.ref }}", text, count=1),
        lambda text: text.replace("git rev-parse HEAD", "git rev-parse NOT_HEAD", 1),
        lambda text: re.sub(r"(?m)^    timeout-minutes: 15\n", "", text, count=1),
        lambda text: text.replace("git status --porcelain", "git diff --exit-code", 1),
    )
    for mutate in mutations:
        with pytest.raises(AssertionError):
            gate_policy(mutate(original), gate)
