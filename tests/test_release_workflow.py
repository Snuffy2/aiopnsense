"""Behavioral contracts for the protected aiopnsense release workflow."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess

import pytest

WORKFLOW_ROOT = Path(__file__).parents[1] / ".github" / "workflows"


def _git(repository: Path, *arguments: str) -> str:
    """Run one successful Git command in a temporary release repository.

    Args:
        repository (Path): Fixture repository root.
        *arguments (str): Arguments following the Git executable.

    Returns:
        str: Standard output with trailing whitespace removed.
    """
    return subprocess.run(
        ["git", *arguments], check=True, cwd=repository, text=True, capture_output=True
    ).stdout.strip()


def _run_workflow_shell(
    repository: Path, shell: str, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Execute one extracted workflow shell block in a fixture repository.

    Args:
        repository (Path): Fixture repository root.
        shell (str): Shell source extracted from the workflow step.
        environment (dict[str, str]): Step-specific environment variables.

    Returns:
        subprocess.CompletedProcess[str]: Completed process, including an expected nonzero status.
    """
    return subprocess.run(
        ["bash", "-c", shell],
        check=False,
        cwd=repository,
        env={**os.environ, **environment},
        text=True,
        capture_output=True,
    )


def _release_fixture(tmp_path: Path, tag: str) -> Path:
    """Create a default branch, annotated tag, and bare origin for a lease test.

    Args:
        tmp_path (Path): Temporary pytest directory.
        tag (str): Release tag initially referring to the default branch.

    Returns:
        Path: Checkout configured with a pushed ``origin`` remote.
    """
    remote = tmp_path / "origin.git"
    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    _git(tmp_path, "init", "-b", "main", str(repository))
    _git(repository, "config", "user.name", "Release Test")
    _git(repository, "config", "user.email", "release-test@example.invalid")
    (repository / "source.txt").write_text("source\n", encoding="utf-8")
    _git(repository, "add", "source.txt")
    _git(repository, "commit", "-m", "Initial source")
    _git(repository, "remote", "add", "origin", str(remote))
    _git(repository, "push", "-u", "origin", "main")
    _git(repository, "tag", "-a", tag, "-m", tag)
    _git(repository, "push", "origin", tag)
    return repository


def _workflow_text() -> str:
    """Return the release workflow source.

    Returns:
        str: UTF-8 release workflow source.
    """
    return (WORKFLOW_ROOT / "release.yml").read_text(encoding="utf-8")


def _job_block(workflow: str, job: str) -> str:
    """Return one top-level release job block from workflow source.

    Args:
        workflow (str): Release workflow source.
        job (str): Exact job identifier.

    Returns:
        str: The job source through the next top-level job or end of file.
    """
    match = re.search(rf"(?ms)^  {re.escape(job)}:\n.*?(?=^  [a-z_]+:\n|\Z)", workflow)
    assert match is not None
    return match.group()


def _step_containing(job: str, token: str) -> str:
    """Return the unique step in one job that contains a semantic token.

    Args:
        job (str): One workflow job block.
        token (str): Token that identifies exactly one step in the job.

    Returns:
        str: Matching step source.
    """
    steps = re.split(r"(?m)(?=^      - )", job)
    matches = [step for step in steps if token in step]
    assert len(matches) == 1
    return matches[0]


def test_release_workflow_separates_read_only_candidate_from_promotion() -> None:
    """Require distinct least-privileged candidate and promotion job boundaries."""
    workflow = _workflow_text()
    candidate = _job_block(workflow, "candidate")
    promote = _job_block(workflow, "promote")

    assert re.search(r"(?ms)^on:\s+release:\s+types:\s*\[published\]", workflow)
    assert "queue: max" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "timeout-minutes: 30" in candidate
    assert re.search(
        r"(?m)^    permissions:\s+      contents: read\s+      pull-requests: read\s*$",
        candidate,
    )
    assert "contents: write" not in candidate
    assert "actions: write" not in candidate
    assert "git push " not in candidate
    assert "gh auth setup-git" not in candidate
    assert "needs: candidate" in promote
    assert "timeout-minutes: 90" in promote
    assert all(
        permission in promote
        for permission in ("actions: write", "contents: write", "statuses: write")
    )


def test_candidate_uses_trusted_locked_tools_then_hands_off_bounded_artifacts() -> None:
    """Require candidate construction to use trusted tools before detached source execution."""
    candidate = _job_block(_workflow_text(), "candidate")
    snapshot = _step_containing(candidate, "Snapshot trusted policy and locked tools")
    source = _step_containing(candidate, "Determine candidate source and changelog range")
    construct = _step_containing(
        candidate, "Construct and build candidate without write credentials"
    )
    upload = _step_containing(candidate, "Upload bounded candidate handoff")

    assert "ref: ${{ github.event.repository.default_branch }}" in candidate
    assert "persist-credentials: false" in candidate
    assert snapshot.index('trusted_sha="$(git rev-parse HEAD)"') < snapshot.index(
        'UV_PROJECT_ENVIRONMENT="$RUNNER_TEMP/release-tools" uv sync --locked --only-group dev'
    )
    assert (
        "cp .github/scripts/{prepare_release.py,release_version.py,release_handoff.py,release_changelog.py}"
        in snapshot
    )
    assert 'git checkout --detach "$EVENT_SHA"' in source
    assert candidate.index(snapshot) < candidate.index(source) < candidate.index(construct)
    assert '"$RUNNER_TEMP/release-tools/bin/python" -m build --no-isolation' in construct
    assert '"$RUNNER_TEMP/release-tools/bin/twine" check "$handoff"/dist/*' in construct
    assert 'release_handoff.py" create' in construct
    for artifact in (
        "manifest.json",
        "candidate-const.py",
        "changelog.md",
        "dist/*.whl",
        "dist/*.tar.gz",
    ):
        assert artifact in upload
    assert "if-no-files-found: error" in upload


def test_fresh_stable_changelog_uses_explicit_range_and_safe_file_handoff() -> None:
    """Require only fresh stable releases to prepend action output to existing history."""
    candidate = _job_block(_workflow_text(), "candidate")
    source = _step_containing(candidate, "Determine candidate source and changelog range")
    changelog = _step_containing(candidate, "Build fresh stable changelog")
    construct = _step_containing(
        candidate, "Construct and build candidate without write credentials"
    )

    assert "ruby/setup-ruby" not in candidate
    assert "bundler-cache" not in candidate
    assert "Gemfile" not in candidate
    assert "github_changelog_generator" not in candidate
    assert 'git tag --merged "$source_sha" | python3 "$HELPERS/release_changelog.py"' in source
    assert '--previous-stable-tag "$RELEASE_TAG"' in source
    assert 'git rev-list --max-parents=0 "$source_sha"' in source
    assert "generate_changelog=true" in source
    assert 'elif [[ "$(git show -s --format=%s "$EVENT_SHA")"' in source
    assert "if: steps.source.outputs.generate-changelog == 'true'" in changelog
    assert "mikepenz/release-changelog-builder-action@v6" in changelog
    assert "fromTag: ${{ steps.source.outputs.previous-tag }}" in changelog
    assert "toTag: ${{ steps.source.outputs.source-sha }}" in changelog
    assert "outputFile: .release-changelog.md" in changelog
    assert "failOnError: true" in changelog
    assert "configurationJson:" in changelog
    assert "**Bug Fixes**" in changelog
    assert "**Other Changes**" in changelog
    assert "GITHUB_TOKEN: ${{ github.token }}" in changelog
    configuration = changelog.split("configurationJson: |\n", maxsplit=1)[1].split(
        "        env:", maxsplit=1
    )[0]
    categories = json.loads(
        "\n".join(line.removeprefix("            ") for line in configuration.splitlines())
    )["categories"]
    assert [category["title"] for category in categories] == [
        "**Breaking Changes**",
        "**New Features**",
        "**Enhancements**",
        "**Bug Fixes**",
        "**Documentation**",
        "**Code Quality**",
        "**Maintenance**",
        "**Other Changes**",
    ]
    assert all(category["consume"] is True for category in categories)
    assert "outputs.changelog" not in candidate
    assert 'python3 "$HELPERS/release_changelog.py"' in construct
    assert '--fragment "$fragment" --release-tag "$RELEASE_TAG"' in construct
    assert '--previous-tag "$PREVIOUS_TAG" --release-date "$release_date"' in construct
    assert '--github-repository "$GITHUB_REPOSITORY"' in construct
    assert 'rm "$fragment"' in construct
    assert '[[ "$(grep -Fc "## [$RELEASE_TAG](" docs/source/changelog.md)" == 1 ]]' in construct


def test_promote_reconstructs_and_verifies_handoff_with_trusted_helpers() -> None:
    """Require promotion to derive identity independently before using candidate artifacts."""
    promote = _job_block(_workflow_text(), "promote")
    snapshot = _step_containing(promote, "Snapshot trusted helpers")
    reconstruct = _step_containing(promote, "Independently prove refs and reconstruct candidate")

    assert "persist-credentials: false" in promote
    for helper in (
        "prepare_release.py",
        "release_version.py",
        "release_handoff.py",
        "verify_aiopnsense_distributions.py",
        "verify_python_distributions.py",
        "verify_release_checks.py",
        "upload_release_asset.py",
    ):
        assert helper in snapshot
    assert reconstruct.index('release_handoff.py" preflight') < reconstruct.index(
        'release_handoff.py" verify'
    )
    assert 'python3 "$HELPERS/prepare_release.py" --repository "$expected_version"' in reconstruct
    assert 'python3 "$HELPERS/prepare_release.py" --repository "$reconstructed"' in reconstruct
    assert 'GIT_AUTHOR_DATE="@$commit_epoch +0000"' in reconstruct
    assert (
        '[[ "$(git rev-list --parents -n 1 "$EVENT_SHA" | wc -w | tr -d \' \')" == 2 ]]'
        in reconstruct
    )
    assert "aiopnsense/const.py\\ndocs/source/changelog.md" in reconstruct
    assert '[[ "$tag_sha" == "$EVENT_SHA" ]]' in reconstruct
    assert 'grep -Fx "VERSION = \\"$RELEASE_TAG\\""' in reconstruct
    assert 'python3 "$HANDOFF/' not in reconstruct
    assert "uv run" not in reconstruct


def test_promote_dispatches_all_gates_from_the_trusted_controller_revision() -> None:
    """Require both release channels to verify the same four candidate-SHA gates."""
    promote = _job_block(_workflow_text(), "promote")
    validation = _step_containing(promote, "Publish candidate to an isolated validation branch")
    dispatch = _step_containing(promote, "Dispatch and verify immutable release gates")

    assert promote.index(validation) < promote.index(dispatch)
    assert "if: github.event.release.prerelease == false" in validation
    assert 'git push origin "$CANDIDATE_SHA:refs/heads/$temp_ref"' in validation
    assert 'python3 "$HELPERS/verify_release_checks.py"' in dispatch
    assert (
        '--workflow-ref "$DEFAULT_BRANCH" --workflow-sha "$TRUSTED_SHA" --sha "$CANDIDATE_SHA"'
        in dispatch
    )
    assert "--timeout-seconds" not in dispatch
    assert set(re.findall(r"--required-check\s+'([^']+)'", dispatch)) == {
        "pytest_check.yml::pytest check and post coverage",
        "docs.yml::build-docs",
        "uv-lock-check.yml::Validate uv lock consistency",
        "prek-autofix-review.yml::review",
    }


def test_promotion_uses_atomic_leases_identity_safe_uploads_and_success_cleanup() -> None:
    """Require protected ref updates, release assets, and cleanup to retain exact identity."""
    promote = _job_block(_workflow_text(), "promote")
    advance = _step_containing(promote, "git push --atomic")
    upload = _step_containing(promote, "Verify final identity and upload release assets")
    cleanup = _step_containing(
        promote, "Delete successful validation branch with its candidate lease"
    )

    assert '--force-with-lease="refs/heads/$DEFAULT_BRANCH:$TARGET_SHA"' in advance
    assert '--force-with-lease="refs/tags/$RELEASE_TAG:$ORIGINAL_TAG_OID"' in advance
    assert "git push --atomic" in advance
    assert '"$HELPERS/upload_release_asset.py"' in upload
    assert '--expected-tag "$RELEASE_TAG"' in upload
    assert '--expected-prerelease "$PRERELEASE"' in upload
    assert "if: ${{ success() && steps.validation_ref.outcome == 'success' }}" in cleanup
    assert '--force-with-lease="refs/heads/$TEMP_REF:$CANDIDATE_SHA"' in cleanup


def test_publish_job_preserves_dynamic_repository_channels_and_oidc_boundary() -> None:
    """Require verified artifacts to publish through the dynamic TestPyPI or PyPI channel."""
    publish = _job_block(_workflow_text(), "publish")

    assert "needs: promote" in publish
    assert "timeout-minutes: 15" in publish
    assert re.search(r"(?m)^    permissions:\s+      id-token: write\s*$", publish)
    assert "actions/checkout@" not in publish
    assert "actions/download-artifact@v8" in publish
    assert "name: python-distributions" in publish
    assert "&& 'testpypi' || 'pypi'" in publish
    assert "https://test.pypi.org/project/aiopnsense/" in publish
    assert "https://pypi.org/project/aiopnsense/" in publish
    assert "repository-url: https://test.pypi.org/legacy/" in publish
    assert publish.count("pypa/gh-action-pypi-publish@release/v1") == 2


@pytest.mark.parametrize("moved_ref", ["branch", "tag"])
def test_promotion_shell_rejects_moved_branch_or_tag_lease(tmp_path: Path, moved_ref: str) -> None:
    """Execute promotion lease checks against independently moved branch and tag refs.

    Args:
        tmp_path (Path): Temporary release-fixture directory.
        moved_ref (str): Protected reference moved after the candidate was constructed.
    """
    tag = "v1.2.3"
    repository = _release_fixture(tmp_path, tag)
    target_sha = _git(repository, "rev-parse", "HEAD")
    original_tag_oid = _git(repository, "rev-parse", f"refs/tags/{tag}")
    (repository / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    _git(repository, "add", "candidate.txt")
    _git(repository, "commit", "-m", f"Release {tag}")
    candidate_sha = _git(repository, "rev-parse", "HEAD")

    if moved_ref == "branch":
        _git(repository, "switch", "-c", "concurrent", target_sha)
        (repository / "concurrent.txt").write_text("moved\n", encoding="utf-8")
        _git(repository, "add", "concurrent.txt")
        _git(repository, "commit", "-m", "Concurrent main change")
        _git(repository, "push", "origin", "HEAD:refs/heads/main")
        _git(repository, "checkout", "--detach", candidate_sha)
    else:
        _git(repository, "tag", "-fa", tag, "-m", tag, candidate_sha)
        _git(repository, "push", "--force", "origin", f"refs/tags/{tag}")

    advance = _step_containing(_job_block(_workflow_text(), "promote"), "git push --atomic").split(
        "run: |", maxsplit=1
    )[-1]
    result = _run_workflow_shell(
        repository,
        advance,
        {
            "CANDIDATE_SHA": candidate_sha,
            "DEFAULT_BRANCH": "main",
            "GH_TOKEN": "fixture-token",
            "ORIGINAL_TAG_OID": original_tag_oid,
            "RELEASE_TAG": tag,
            "TARGET_SHA": target_sha,
        },
    )

    assert result.returncode != 0
