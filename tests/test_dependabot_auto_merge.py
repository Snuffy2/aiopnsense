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
        "committer": {"login": "web-flow"},
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


@pytest.mark.parametrize("committer", [None, "maintainer"])
def test_authorizer_rejects_direct_history_without_web_flow_committer(
    tmp_path: Path, committer: str | None
) -> None:
    """Reject a direct update whose verified root lacks the web-flow committer.

    Args:
        tmp_path (Path): Isolated trusted-base directory.
        committer (str | None): Missing or maintainer committer identity.
    """
    _write_trusted_file(tmp_path, "uv.lock")
    root_commit = _dependabot_commit()
    if committer is None:
        root_commit.pop("committer")
    else:
        root_commit["committer"] = {"login": committer}

    result = _run_authorizer(
        tmp_path,
        actor=BOT,
        changed_files=["uv.lock"],
        commits=[root_commit],
        event=_event(action="reopened", ref="dependabot/uv/dependency-update"),
    )

    assert result.returncode != 0


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


@pytest.mark.parametrize("committer", [None, "maintainer"])
def test_authorizer_rejects_update_root_without_web_flow_committer(
    tmp_path: Path, committer: str | None
) -> None:
    """Reject an update branch whose Dependabot root lacks web-flow provenance.

    Args:
        tmp_path (Path): Isolated trusted-base directory.
        committer (str | None): Missing or maintainer committer identity.
    """
    _write_trusted_file(tmp_path, "uv.lock")
    original_sha = "c" * 40
    update_sha = "d" * 40
    root_commit = _dependabot_commit(original_sha)
    if committer is None:
        root_commit.pop("committer")
    else:
        root_commit["committer"] = {"login": committer}
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
        commits=[root_commit, update_commit],
        event=event,
    )

    assert result.returncode != 0


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
