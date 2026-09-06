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


def _run_authorizer(
    tmp_path: Path,
    *,
    actor: str,
    changed_files: list[str],
    commits: list[dict[str, object]],
    event: dict[str, object],
) -> subprocess.CompletedProcess[str]:
    """Run the workflow authorizer with representative API fixture files.

    Args:
        tmp_path (Path): Isolated trusted-base directory and input-file location.
        actor (str): GitHub actor that triggered the event.
        changed_files (list[str]): Changed-file API results.
        commits (list[dict[str, object]]): Pull-request commit API results.
        event (dict[str, object]): Pull-request webhook event.

    Returns:
        subprocess.CompletedProcess[str]: Completed Node process result.
    """
    event_path = tmp_path / "event.json"
    changed_files_path = tmp_path / "changed-files"
    commits_path = tmp_path / "commits.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    changed_files_path.write_text("\n".join(changed_files), encoding="utf-8")
    commits_path.write_text(json.dumps([commits]), encoding="utf-8")
    environment = {**os.environ, "GITHUB_ACTOR": actor}
    return subprocess.run(
        ["node", str(AUTHORIZER), str(event_path), str(changed_files_path), str(commits_path)],
        check=False,
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
    )


def test_authorizer_accepts_verified_uv_lockfile_update(tmp_path: Path) -> None:
    """Authorize a verified, direct lockfile-only Dependabot update.

    Args:
        tmp_path (Path): Isolated trusted-base directory.
    """
    result = _run_authorizer(
        tmp_path,
        actor=BOT,
        changed_files=["uv.lock"],
        commits=[_dependabot_commit()],
        event=_event(action="opened", ref="dependabot/uv/dependency-update"),
    )

    assert result.returncode == 0


def test_authorizer_rejects_uv_update_outside_lockfile_scope(tmp_path: Path) -> None:
    """Reject a dependency update that changes more than its lockfile.

    Args:
        tmp_path (Path): Isolated trusted-base directory.
    """
    result = _run_authorizer(
        tmp_path,
        actor=BOT,
        changed_files=["uv.lock", "pyproject.toml"],
        commits=[_dependabot_commit()],
        event=_event(action="opened", ref="dependabot/uv/dependency-update"),
    )

    assert result.returncode != 0


@pytest.mark.parametrize(
    ("path", "present", "authorized"),
    [
        (".github/workflows/ci.yml", True, True),
        ("action.yml", True, True),
        (".github/workflows/new.yml", False, False),
        (".github/actions/check/action.yml", True, False),
    ],
)
def test_authorizer_trusts_only_existing_top_level_actions_files(
    tmp_path: Path,
    path: str,
    present: bool,
    authorized: bool,
) -> None:
    """Authorize only an existing top-level Actions dependency target.

    Args:
        tmp_path (Path): Isolated trusted-base directory.
        path (str): Dependency update file reported by GitHub.
        present (bool): Whether the trusted base contains that file.
        authorized (bool): Expected authorization outcome.
    """
    if present:
        trusted_file = tmp_path / path
        trusted_file.parent.mkdir(parents=True, exist_ok=True)
        trusted_file.write_text("fixture\n", encoding="utf-8")
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
    original_sha = "c" * 40
    update_sha = "d" * 40
    update_commit = {
        "author": {"login": "maintainer"},
        "commit": {"verification": {"verified": True}},
        "committer": {"login": "web-flow"},
        "parents": [{"sha": original_sha}, {"sha": BASE_SHA}],
        "sha": update_sha,
    }
    event = _event(action="synchronize", ref="dependabot/uv/dependency-update")
    event["pull_request"]["head"]["sha"] = update_sha  # type: ignore[index]
    result = _run_authorizer(
        tmp_path,
        actor="maintainer",
        changed_files=["uv.lock"],
        commits=[_dependabot_commit(original_sha), update_commit],
        event=event,
    )

    assert result.returncode == 0
