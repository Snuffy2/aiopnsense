"""Executable behavior tests for trusted Dependabot update authorization."""

import json
import os
from pathlib import Path
import subprocess

import pytest

AUTHORIZER = Path(__file__).parents[1] / ".github" / "scripts" / "dependabot-auto-merge.mjs"
BOT = "dependabot[bot]"
REPOSITORY = "example/aiopnsense"
HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40


def _event(*, action: str, ref: str) -> dict[str, object]:
    """Return a Dependabot pull-request event fixture.

    Args:
        action (str): Pull-request event action.
        ref (str): Dependabot pull-request branch.

    Returns:
        dict[str, object]: Event data consumed by the authorizer.
    """
    return {
        "action": action,
        "repository": {
            "default_branch": "main",
            "fork": False,
            "full_name": REPOSITORY,
        },
        "pull_request": {
            "base": {"ref": "main", "sha": BASE_SHA},
            "head": {
                "ref": ref,
                "repo": {"full_name": REPOSITORY},
                "sha": HEAD_SHA,
            },
            "user": {"login": BOT},
        },
    }


def _dependabot_commit(sha: str = HEAD_SHA) -> dict[str, object]:
    """Return a verified Dependabot commit fixture.

    Args:
        sha (str): Commit identifier.

    Returns:
        dict[str, object]: Commit data returned by the pull-request API.
    """
    return {
        "author": {"login": BOT},
        "commit": {"verification": {"verified": True}},
        "parents": [],
        "sha": sha,
    }


def _write_trusted_file(tmp_path: Path, path: str) -> None:
    """Create a file in the trusted-base fixture.

    Args:
        tmp_path (Path): Isolated trusted-base directory.
        path (str): Relative file path to create.
    """
    trusted_file = tmp_path / path
    trusted_file.parent.mkdir(parents=True, exist_ok=True)
    trusted_file.write_text("fixture\n", encoding="utf-8")


def _run_authorizer(
    tmp_path: Path,
    *,
    actor: str,
    ancestry_proofs: list[dict[str, object]] | None = None,
    changed_files: list[str],
    commits: list[dict[str, object]],
    event: dict[str, object],
) -> subprocess.CompletedProcess[str]:
    """Run the workflow authorizer with representative API fixture files.

    Args:
        tmp_path (Path): Isolated trusted-base directory and input-file location.
        actor (str): GitHub actor that triggered the event.
        ancestry_proofs (list[dict[str, object]] | None): Merge-parent ancestry
            evidence returned by the compare API.
        changed_files (list[str]): Changed-file API results.
        commits (list[dict[str, object]]): Pull-request commit API results.
        event (dict[str, object]): Pull-request webhook event.

    Returns:
        subprocess.CompletedProcess[str]: Completed Node process result.
    """
    event_path = tmp_path / "event.json"
    changed_files_path = tmp_path / "changed-files"
    commits_path = tmp_path / "commits.json"
    ancestry_proofs_path = tmp_path / "ancestry-proofs.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    changed_files_path.write_text("\n".join(changed_files), encoding="utf-8")
    commits_path.write_text(json.dumps([commits]), encoding="utf-8")
    ancestry_proofs_path.write_text(json.dumps(ancestry_proofs or []), encoding="utf-8")
    environment = {**os.environ, "GITHUB_ACTOR": actor}
    return subprocess.run(
        [
            "node",
            str(AUTHORIZER),
            str(event_path),
            str(changed_files_path),
            str(commits_path),
            str(ancestry_proofs_path),
        ],
        check=False,
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
    )


def test_authorizer_accepts_reopened_verified_uv_lockfile_update(tmp_path: Path) -> None:
    """Authorize a reopened, verified direct lockfile-only Dependabot update.

    Args:
        tmp_path (Path): Isolated trusted-base directory.
    """
    _write_trusted_file(tmp_path, "uv.lock")
    result = _run_authorizer(
        tmp_path,
        actor=BOT,
        changed_files=["uv.lock"],
        commits=[_dependabot_commit()],
        event=_event(action="reopened", ref="dependabot/uv/dependency-update"),
    )

    assert result.returncode == 0


def test_authorizer_rejects_uv_update_outside_lockfile_scope(tmp_path: Path) -> None:
    """Reject a dependency update that changes more than its lockfile.

    Args:
        tmp_path (Path): Isolated trusted-base directory.
    """
    _write_trusted_file(tmp_path, "uv.lock")
    result = _run_authorizer(
        tmp_path,
        actor=BOT,
        changed_files=["uv.lock", "pyproject.toml"],
        commits=[_dependabot_commit()],
        event=_event(action="opened", ref="dependabot/uv/dependency-update"),
    )

    assert result.returncode != 0


def test_authorizer_rejects_npm_update_when_trusted_base_uses_uv(tmp_path: Path) -> None:
    """Reject npm authorization when the trusted base lacks npm metadata.

    Args:
        tmp_path (Path): Isolated trusted-base directory.
    """
    _write_trusted_file(tmp_path, "uv.lock")
    result = _run_authorizer(
        tmp_path,
        actor=BOT,
        changed_files=["package-lock.json"],
        commits=[_dependabot_commit()],
        event=_event(
            action="opened",
            ref="dependabot/npm_and_yarn/dependency-update",
        ),
    )

    assert result.returncode != 0


@pytest.mark.parametrize(
    ("path", "present", "authorized"),
    [
        (".github/workflows/ci.yml", True, True),
        ("action.yml", True, True),
        (".github/workflows/new.yml", False, False),
        (".github/actions/check/action.yml", True, True),
    ],
)
def test_authorizer_trusts_only_existing_actions_files(
    tmp_path: Path,
    path: str,
    present: bool,
    authorized: bool,
) -> None:
    """Authorize only an existing trusted-base Actions dependency target.

    Args:
        tmp_path (Path): Isolated trusted-base directory.
        path (str): Dependency update file reported by GitHub.
        present (bool): Whether the trusted base contains that file.
        authorized (bool): Expected authorization outcome.
    """
    if present:
        _write_trusted_file(tmp_path, path)
    result = _run_authorizer(
        tmp_path,
        actor=BOT,
        changed_files=[path],
        commits=[_dependabot_commit()],
        event=_event(
            action="opened",
            ref="dependabot/github_actions/dependency-update",
        ),
    )

    assert (result.returncode == 0) is authorized


def test_authorizer_accepts_verified_update_branch_history(tmp_path: Path) -> None:
    """Authorize an update-branch merge that preserves trusted history.

    Args:
        tmp_path (Path): Isolated trusted-base directory.
    """
    _write_trusted_file(tmp_path, "uv.lock")
    original_sha = "c" * 40
    update_sha = "d" * 40
    update_commit = {
        "author": {"login": "maintainer"},
        "commit": {"verification": {"verified": True}},
        "committer": {"login": "web-flow"},
        "parents": [{"sha": original_sha}, {"sha": BASE_SHA}],
        "sha": update_sha,
    }
    event = _event(action="reopened", ref="dependabot/uv/dependency-update")
    event["pull_request"]["head"]["sha"] = update_sha  # type: ignore[index]
    result = _run_authorizer(
        tmp_path,
        actor="arbitrary-actor",
        ancestry_proofs=[
            {
                "ahead_by": 0,
                "base_commit": BASE_SHA,
                "base_sha": BASE_SHA,
                "behind_by": 0,
                "head_commit": BASE_SHA,
                "merge_base_commit": BASE_SHA,
                "parent_sha": BASE_SHA,
                "status": "identical",
            }
        ],
        changed_files=["uv.lock"],
        commits=[_dependabot_commit(original_sha), update_commit],
        event=event,
    )

    assert result.returncode == 0


def test_authorizer_rejects_invalid_update_branch_history(tmp_path: Path) -> None:
    """Reject an update branch whose merge commit is not trusted.

    Args:
        tmp_path (Path): Isolated trusted-base directory.
    """
    _write_trusted_file(tmp_path, "uv.lock")
    original_sha = "c" * 40
    invalid_update = {
        "author": {"login": "maintainer"},
        "commit": {"verification": {"verified": True}},
        "committer": {"login": "web-flow"},
        "parents": [{"sha": original_sha}],
        "sha": HEAD_SHA,
    }
    result = _run_authorizer(
        tmp_path,
        actor="maintainer",
        changed_files=["uv.lock"],
        commits=[_dependabot_commit(original_sha), invalid_update],
        event=_event(action="synchronize", ref="dependabot/uv/dependency-update"),
    )

    assert result.returncode != 0


@pytest.mark.parametrize(
    "ancestry_proofs",
    [
        [],
        [{}],
        [
            {
                "ahead_by": 1,
                "base_commit": BASE_SHA,
                "base_sha": BASE_SHA,
                "behind_by": 0,
                "head_commit": BASE_SHA,
                "merge_base_commit": BASE_SHA,
                "parent_sha": BASE_SHA,
                "status": "diverged",
            }
        ],
    ],
)
def test_authorizer_rejects_incomplete_or_diverged_merge_ancestry(
    tmp_path: Path, ancestry_proofs: list[dict[str, object]]
) -> None:
    """Reject unproven second parents in reopened update-branch history.

    Args:
        tmp_path (Path): Isolated trusted-base directory.
        ancestry_proofs (list[dict[str, object]]): Invalid compare API evidence.
    """
    _write_trusted_file(tmp_path, "uv.lock")
    original_sha = "c" * 40
    update_commit = {
        "author": {"login": "maintainer"},
        "commit": {"verification": {"verified": True}},
        "committer": {"login": "web-flow"},
        "parents": [{"sha": original_sha}, {"sha": BASE_SHA}],
        "sha": HEAD_SHA,
    }
    result = _run_authorizer(
        tmp_path,
        actor="arbitrary-actor",
        ancestry_proofs=ancestry_proofs,
        changed_files=["uv.lock"],
        commits=[_dependabot_commit(original_sha), update_commit],
        event=_event(action="reopened", ref="dependabot/uv/dependency-update"),
    )

    assert result.returncode != 0


def test_authorizer_rejects_update_merge_from_stale_base(tmp_path: Path) -> None:
    """Reject a latest update merge whose second parent is not the event base.

    Args:
        tmp_path (Path): Isolated trusted-base directory.
    """
    _write_trusted_file(tmp_path, "uv.lock")
    original_sha = "c" * 40
    stale_base_sha = "d" * 40
    update_commit = {
        "author": {"login": "maintainer"},
        "commit": {"verification": {"verified": True}},
        "committer": {"login": "web-flow"},
        "parents": [{"sha": original_sha}, {"sha": stale_base_sha}],
        "sha": HEAD_SHA,
    }
    result = _run_authorizer(
        tmp_path,
        actor="arbitrary-actor",
        ancestry_proofs=[
            {
                "ahead_by": 1,
                "base_commit": stale_base_sha,
                "base_sha": BASE_SHA,
                "behind_by": 0,
                "head_commit": BASE_SHA,
                "merge_base_commit": stale_base_sha,
                "parent_sha": stale_base_sha,
                "status": "ahead",
            }
        ],
        changed_files=["uv.lock"],
        commits=[_dependabot_commit(original_sha), update_commit],
        event=_event(action="reopened", ref="dependabot/uv/dependency-update"),
    )

    assert result.returncode != 0


def test_authorizer_rejects_non_web_flow_update_merge(tmp_path: Path) -> None:
    """Reject an otherwise valid update merge not created by GitHub's web flow.

    Args:
        tmp_path (Path): Isolated trusted-base directory.
    """
    _write_trusted_file(tmp_path, "uv.lock")
    original_sha = "c" * 40
    update_commit = {
        "author": {"login": "maintainer"},
        "commit": {"verification": {"verified": True}},
        "committer": {"login": "another-bot"},
        "parents": [{"sha": original_sha}, {"sha": BASE_SHA}],
        "sha": HEAD_SHA,
    }
    result = _run_authorizer(
        tmp_path,
        actor="arbitrary-actor",
        ancestry_proofs=[
            {
                "ahead_by": 0,
                "base_commit": BASE_SHA,
                "base_sha": BASE_SHA,
                "behind_by": 0,
                "head_commit": BASE_SHA,
                "merge_base_commit": BASE_SHA,
                "parent_sha": BASE_SHA,
                "status": "identical",
            }
        ],
        changed_files=["uv.lock"],
        commits=[_dependabot_commit(original_sha), update_commit],
        event=_event(action="reopened", ref="dependabot/uv/dependency-update"),
    )

    assert result.returncode != 0


def test_authorizer_rejects_unrelated_merge_parent_evidence(tmp_path: Path) -> None:
    """Reject compare evidence whose claimed parent differs from the merge parent.

    Args:
        tmp_path (Path): Isolated trusted-base directory.
    """
    _write_trusted_file(tmp_path, "uv.lock")
    original_sha = "c" * 40
    unrelated_parent_sha = "e" * 40
    update_commit = {
        "author": {"login": "maintainer"},
        "commit": {"verification": {"verified": True}},
        "committer": {"login": "web-flow"},
        "parents": [{"sha": original_sha}, {"sha": unrelated_parent_sha}],
        "sha": HEAD_SHA,
    }
    result = _run_authorizer(
        tmp_path,
        actor="arbitrary-actor",
        ancestry_proofs=[
            {
                "ahead_by": 0,
                "base_commit": BASE_SHA,
                "base_sha": BASE_SHA,
                "behind_by": 0,
                "head_commit": BASE_SHA,
                "merge_base_commit": BASE_SHA,
                "parent_sha": unrelated_parent_sha,
                "status": "identical",
            }
        ],
        changed_files=["uv.lock"],
        commits=[_dependabot_commit(original_sha), update_commit],
        event=_event(action="reopened", ref="dependabot/uv/dependency-update"),
    )

    assert result.returncode != 0


def _workflow(name: str) -> dict[str, object]:
    """Load a workflow into parsed YAML data for contract assertions.

    Args:
        name (str): Filename relative to the workflow directory.

    Returns:
        dict[str, object]: Parsed workflow mapping.
    """
    source = Path(__file__).parents[1] / ".github" / "workflows" / name
    parser = """
require \"json\"
require \"yaml\"
document = YAML.safe_load(STDIN.read, aliases: false)
document[\"on\"] = document.delete(true) if document.key?(true)
STDOUT.write(JSON.generate(document))
"""
    result = subprocess.run(
        ["ruby", "-rjson", "-ryaml", "-e", parser],
        check=True,
        input=source.read_text(encoding="utf-8"),
        text=True,
        capture_output=True,
    )
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, dict)
    return parsed


def _mapping(value: object) -> dict[str, object]:
    """Return a mapping value for workflow contract assertions.

    Args:
        value (object): Parsed YAML value.

    Returns:
        dict[str, object]: Mapping represented by the value.
    """
    assert isinstance(value, dict)
    return value


def _steps(job: dict[str, object]) -> list[dict[str, object]]:
    """Return a workflow job's steps as mappings.

    Args:
        job (dict[str, object]): Parsed workflow job.

    Returns:
        list[dict[str, object]]: Ordered workflow steps.
    """
    raw_steps = job["steps"]
    assert isinstance(raw_steps, list)
    return [_mapping(step) for step in raw_steps]


def _job_items(workflow: dict[str, object]) -> list[tuple[str, dict[str, object]]]:
    """Return named jobs from a parsed workflow.

    Args:
        workflow (dict[str, object]): Parsed workflow mapping.

    Returns:
        list[tuple[str, dict[str, object]]]: Job names and their mappings.
    """
    return [(str(name), _mapping(job)) for name, job in _mapping(workflow["jobs"]).items()]


def _authorization_step(job: dict[str, object]) -> dict[str, object]:
    """Find the step that invokes the Dependabot authorizer.

    Args:
        job (dict[str, object]): Parsed workflow job.

    Returns:
        dict[str, object]: Authorizer step.
    """
    return next(step for step in _steps(job) if "dependabot-auto-merge.mjs" in str(step.get("run")))


def _assert_trusted_authorization(
    job: dict[str, object], *, require_full_provenance: bool, require_event_condition: bool
) -> None:
    """Assert trusted checkout and compare evidence precede authorization.

    Args:
        job (dict[str, object]): Parsed workflow job.
        require_full_provenance (bool): Whether the job-level eligibility guard
            must include the complete same-repository provenance check.
        require_event_condition (bool): Whether the gate must explicitly limit
            execution to pull-request events.
    """
    authorizer = _authorization_step(job)
    eligibility = str(authorizer.get("if", job.get("if")))
    assert "pull_request.user.login == 'dependabot[bot]'" in eligibility
    if require_event_condition:
        assert "github.event_name == 'pull_request'" in eligibility
    provenance_terms = (
        "repository.fork == false",
        "pull_request.head.repo.full_name == github.repository",
        "pull_request.base.ref == github.event.repository.default_branch",
    )
    if require_full_provenance:
        for term in provenance_terms:
            assert term in eligibility
    else:
        for term in provenance_terms:
            assert term not in eligibility
    assert "continue-on-error" not in authorizer
    steps = _steps(job)
    authorizer_index = steps.index(authorizer)
    trusted_checkout = next(
        step
        for step in steps[:authorizer_index]
        if str(step.get("uses", "")).startswith("actions/checkout@")
        and _mapping(step["with"])["ref"] == "${{ github.event.pull_request.base.sha }}"
    )
    assert _mapping(trusted_checkout["with"])["persist-credentials"] is False
    if require_full_provenance:
        assert "if" not in trusted_checkout
    elif require_event_condition:
        assert str(trusted_checkout["if"]) == eligibility
    else:
        assert "if" not in trusted_checkout
    command = str(authorizer["run"])
    for token in (
        "pulls/${PR_NUMBER}/files",
        "pulls/${PR_NUMBER}/commits",
        "compare/",
        "ancestry_proofs",
    ):
        assert token in command


def test_dependabot_workflows_authorize_history_before_pr_head_checkout() -> None:
    """Keep Dependabot auto-merge and pytest authorization fail-closed.

    The contract parses workflow semantics so formatting changes do not hide a
    moved trusted checkout, missing compare proof, or skippable authorizer.
    """
    auto_merge_jobs = _job_items(_workflow("dependabot-auto-merge.yml"))
    pytest_jobs = _job_items(_workflow("pytest_check.yml"))
    auto_merge_authorization = next(
        job for _name, job in auto_merge_jobs if "dependabot-auto-merge.mjs" in str(job)
    )
    tests = next(
        job for _name, job in pytest_jobs if "uv run --locked --group pytest pytest" in str(job)
    )
    for job, require_full_provenance, require_event_condition in (
        (auto_merge_authorization, False, False),
        (tests, False, True),
    ):
        permissions = _mapping(job["permissions"])
        assert permissions["contents"] == "read"
        assert permissions["pull-requests"] == "read"
        _assert_trusted_authorization(
            job,
            require_full_provenance=require_full_provenance,
            require_event_condition=require_event_condition,
        )

    test_condition = str(tests["if"])
    assert "pull_request.user.login == 'dependabot[bot]'" in test_condition
    steps = _steps(tests)
    head_checkout_index = next(
        index
        for index, step in enumerate(steps)
        if str(step.get("uses", "")).startswith("actions/checkout@")
        and _mapping(step["with"])["ref"] == "${{ inputs.expected_sha || github.sha }}"
    )
    assert head_checkout_index > steps.index(_authorization_step(tests))


def test_dependabot_auto_merge_writes_only_after_successful_authorization() -> None:
    """Keep write-capable auto-merge jobs checkout-free and dependent.

    This limits write tokens to jobs that run only after trusted authorization.
    """
    jobs = _job_items(_workflow("dependabot-auto-merge.yml"))
    authorization_name, _authorization = next(
        (name, job) for name, job in jobs if "dependabot-auto-merge.mjs" in str(job)
    )
    write_jobs = [
        job
        for _name, job in jobs
        if _mapping(job["permissions"]).get("contents") == "write"
        and _mapping(job["permissions"]).get("pull-requests") == "write"
    ]
    enable = next(job for job in write_jobs if "gh pr merge --auto --squash" in str(job))
    assert enable["needs"] == authorization_name
    assert "if" not in enable
    cleanup = next(job for job in write_jobs if "gh pr merge --disable-auto" in str(job))
    cleanup_guard = str(cleanup["if"])
    for term in (
        "failure()",
        "!cancelled()",
        "repository.fork == false",
        "pull_request.user.login == 'dependabot[bot]'",
        "pull_request.head.repo.full_name == github.repository",
        "pull_request.base.ref == github.event.repository.default_branch",
    ):
        assert term in cleanup_guard
    for job in write_jobs:
        permissions = _mapping(job["permissions"])
        assert permissions["contents"] == "write"
        assert permissions["pull-requests"] == "write"
        assert not any(
            str(step.get("uses", "")).startswith("actions/checkout@") for step in _steps(job)
        )


def test_coverage_writes_run_after_pr_checks_without_pr_checkout() -> None:
    """Keep PR-head tests read-only and coverage publication post-run.

    The post-run workflow may write comments or default-branch coverage data,
    but its PR path never checks out untrusted pull-request code.
    """
    tests = next(
        job
        for _name, job in _job_items(_workflow("pytest_check.yml"))
        if "uv run --locked --group pytest pytest" in str(job)
    )
    permissions = _mapping(tests["permissions"])
    assert permissions == {"contents": "read", "pull-requests": "read"}
    coverage_step = next(
        step for step in _steps(tests) if "python-coverage-comment-action" in str(step.get("uses"))
    )
    assert "github.event_name == 'pull_request'" in str(coverage_step["if"])
    assert _mapping(coverage_step["with"])["MINIMUM_GREEN"] == 90
    assert _mapping(coverage_step["with"])["MINIMUM_ORANGE"] == 70
    assert any(
        _mapping(step.get("with", {})).get("name") == "python-coverage-data"
        for step in _steps(tests)
    )

    post_jobs = _job_items(_workflow("pytest_post_coverage.yml"))
    post_pr = next(
        job
        for _name, job in post_jobs
        if "post_comment" in str(job) and "GITHUB_PR_RUN_ID" in str(job)
    )
    post_permissions = _mapping(post_pr["permissions"])
    assert post_permissions["actions"] == "read"
    assert post_permissions["contents"] == "read"
    assert post_permissions["pull-requests"] == "write"
    assert not any(
        str(step.get("uses", "")).startswith("actions/checkout@") for step in _steps(post_pr)
    )
    assert "GITHUB_PR_RUN_ID" in str(post_pr)

    publish = next(job for _name, job in post_jobs if "save_coverage_data_files" in str(job))
    assert "workflow_run.event == 'push'" in str(publish["if"])
    assert "workflow_run.head_branch == github.event.repository.default_branch" in str(
        publish["if"]
    )
    assert _mapping(publish["permissions"])["contents"] == "write"
    checkout = next(
        step
        for step in _steps(publish)
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert _mapping(checkout["with"])["persist-credentials"] is False
