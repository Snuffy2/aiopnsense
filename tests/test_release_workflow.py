"""Structural contracts for the protected aiopnsense release workflow."""

import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

WORKFLOW_ROOT = Path(__file__).parents[1] / ".github" / "workflows"
SCRIPT_ROOT = Path(__file__).parents[1] / ".github" / "scripts"


def _git(repository: Path, *arguments: str) -> str:
    """Run one successful Git command in a release fixture repository.

    Args:
        repository (Path): Fixture repository root.
        *arguments (str): Arguments following the Git executable.

    Returns:
        str: Standard output with trailing whitespace removed.
    """
    return subprocess.run(
        ["git", *arguments],
        check=True,
        cwd=repository,
        text=True,
        capture_output=True,
    ).stdout.strip()


def _run_workflow_shell(
    repository: Path, run: str, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run an extracted release workflow shell block in a fixture repository.

    Args:
        repository (Path): Fixture repository root.
        run (str): Shell source from the workflow step.
        environment (dict[str, str]): Step-specific environment variables.

    Returns:
        subprocess.CompletedProcess[str]: Completed shell process without raising for a nonzero exit
            status.
    """
    return subprocess.run(
        ["bash", "-c", run],
        check=False,
        cwd=repository,
        env={**os.environ, **environment},
        text=True,
        capture_output=True,
    )


def _release_fixture(
    tmp_path: Path, tag: str, *, legacy_helpers: bool = False
) -> tuple[Path, Path]:
    """Create a pushed default branch and annotated stable-release tag fixture.

    Args:
        tmp_path (Path): Temporary pytest directory.
        tag (str): Stable release tag initially pointing at the default branch.
        legacy_helpers (bool): Whether the release source predates the staged package helpers.

    Returns:
        tuple[Path, Path]: Repository checkout and its bare origin remote.
    """
    remote = tmp_path / "origin.git"
    repository = tmp_path / "repository"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    _git(tmp_path, "init", "-b", "main", str(repository))
    _git(repository, "config", "user.name", "Release Test")
    _git(repository, "config", "user.email", "release-test@example.invalid")
    package = repository / "aiopnsense"
    package.mkdir()
    (package / "const.py").write_text('VERSION = "v1.0.0"\n', encoding="utf-8")
    changelog = repository / "docs" / "source" / "changelog.md"
    changelog.parent.mkdir(parents=True)
    changelog.write_text("# Changelog\n", encoding="utf-8")
    helper = repository / ".github" / "scripts"
    helper.mkdir(parents=True)
    shutil.copy2(SCRIPT_ROOT / "prepare_release.py", helper / "prepare_release.py")
    if not legacy_helpers:
        for name in (
            "release_version.py",
            "verify_aiopnsense_distributions.py",
            "verify_python_distributions.py",
        ):
            shutil.copy2(SCRIPT_ROOT / name, helper / name)
    shutil.copy2(SCRIPT_ROOT / "verify_release_checks.py", helper / "verify_release_checks.py")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "Initial release source")
    _git(repository, "remote", "add", "origin", str(remote))
    _git(repository, "push", "-u", "origin", "main")
    _git(repository, "tag", "-a", tag, "-m", tag)
    _git(repository, "push", "origin", tag)
    return repository, remote


def _run_base_step(
    repository: Path, tmp_path: Path, tag: str, *, prerelease: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run release metadata validation for a stable tag fixture.

    Args:
        repository (Path): Fixture repository root.
        tmp_path (Path): Temporary pytest directory.
        tag (str): Release tag under validation.
        prerelease (bool): Whether the fixture models a prerelease.

    Returns:
        subprocess.CompletedProcess[str]: Completed metadata-validation shell process.
    """
    output = tmp_path / "base-output"
    return _run_workflow_shell(
        repository,
        _step_containing(_workflow_text("release.yml"), "trusted_sha=").split("run: |", 1)[-1],
        {
            "GITHUB_OUTPUT": str(output),
            "IS_PRERELEASE": str(prerelease).lower(),
            "RELEASE_TAG": tag,
            "RELEASE_TARGET": "main",
            "RUNNER_TEMP": str(tmp_path),
        },
    )


def _publish_resume_candidate(
    repository: Path,
    tag: str,
    *,
    const_version: str | None = None,
    changelog: str | None = None,
    extra_path: bool = False,
) -> str:
    """Create and push one candidate shaped like a stable release retry.

    Args:
        repository (Path): Fixture repository root.
        tag (str): Release tag used in the candidate subject and metadata.
        const_version (str | None): Optional replacement for the exact tagged version.
        changelog (str | None): Optional release changelog content.
        extra_path (bool): Whether to add a forbidden third changed path.

    Returns:
        str: Candidate commit SHA.
    """
    (repository / "aiopnsense" / "const.py").write_text(
        f'VERSION = "{const_version or tag}"\n', encoding="utf-8"
    )
    (repository / "docs" / "source" / "changelog.md").write_text(
        changelog or f"# Changelog\n\n## [{tag}](https://example.invalid/{tag})\n",
        encoding="utf-8",
    )
    if extra_path:
        (repository / "forbidden.txt").write_text("unexpected\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", f"Release {tag}")
    candidate_sha = _git(repository, "rev-parse", "HEAD")
    _git(repository, "tag", "-fa", tag, "-m", tag)
    _git(repository, "push", "--force", "origin", "HEAD:refs/heads/main", f"refs/tags/{tag}")
    _git(repository, "fetch", "--force", "origin", "main", f"refs/tags/{tag}:refs/tags/{tag}")
    _git(repository, "checkout", "--detach", "origin/main")
    return candidate_sha


def _restore_trusted_package_helpers(repository: Path) -> str:
    """Advance the trusted default branch with package helpers absent from the release source.

    Args:
        repository (Path): Fixture repository whose release source predates the helpers.

    Returns:
        str: SHA of the default-branch commit that owns the restored helpers.
    """
    helper = repository / ".github" / "scripts"
    for name in (
        "release_version.py",
        "verify_aiopnsense_distributions.py",
        "verify_python_distributions.py",
    ):
        shutil.copy2(SCRIPT_ROOT / name, helper / name)
    _git(repository, "add", ".github/scripts")
    _git(repository, "commit", "-m", "Add trusted release package helpers")
    trusted_sha = _git(repository, "rev-parse", "HEAD")
    _git(repository, "push", "origin", "HEAD:refs/heads/main")
    _git(repository, "fetch", "origin", "main")
    _git(repository, "checkout", "--detach", "origin/main")
    return trusted_sha


def _workflow_text(name: str) -> str:
    """Return a workflow source file.

    Args:
        name (str): Workflow filename.

    Returns:
        str: UTF-8 workflow source.
    """
    return (WORKFLOW_ROOT / name).read_text(encoding="utf-8")


def _step_containing(workflow: str, token: str) -> str:
    """Return the unique workflow step containing a stable semantic token.

    Args:
        workflow (str): Workflow source text.
        token (str): Token that identifies exactly one workflow step.

    Returns:
        str: Matching workflow step source.
    """
    steps = re.split(r"(?m)(?=^      - )", workflow)
    matches = [step for step in steps if token in step]
    assert len(matches) == 1
    return matches[0]


def _normalized(value: str) -> str:
    """Collapse whitespace for shell-contract assertions.

    Args:
        value (str): Source text to normalize.

    Returns:
        str: Whitespace-collapsed text.
    """
    return " ".join(value.split())


def test_stable_candidate_shell_creates_only_version_and_changelog(tmp_path: Path) -> None:
    """Create a fresh stable candidate through the extracted workflow shell.

    Args:
        tmp_path (Path): Temporary release-fixture directory.
    """
    tag = "v1.2.3"
    repository, _remote = _release_fixture(tmp_path, tag)
    base = _run_base_step(repository, tmp_path, tag)

    assert base.returncode == 0, base.stderr
    assert "resume=false" in (tmp_path / "base-output").read_text(encoding="utf-8")
    base_outputs = dict(
        line.split("=", maxsplit=1)
        for line in (tmp_path / "base-output").read_text(encoding="utf-8").splitlines()
    )
    (repository / "docs" / "source" / "changelog.md").write_text(
        f"# Changelog\n\n## [{tag}](https://example.invalid/{tag})\n", encoding="utf-8"
    )
    output = tmp_path / "candidate-output"
    candidate = _run_workflow_shell(
        repository,
        _step_containing(
            _workflow_text("release.yml"), "Stable release candidate must contain"
        ).split("run: |", 1)[-1],
        {
            "GITHUB_OUTPUT": str(output),
            "RELEASE_TAG": tag,
            "RESUME": "false",
            "RESUME_SHA": "",
            "TRUSTED_HELPERS": base_outputs["trusted-helpers"],
        },
    )

    assert candidate.returncode == 0, candidate.stderr
    candidate_sha = output.read_text(encoding="utf-8").removeprefix("sha=").strip()
    assert _git(
        repository, "diff", "--name-only", f"{candidate_sha}^", candidate_sha
    ).splitlines() == [
        "aiopnsense/const.py",
        "docs/source/changelog.md",
    ]
    assert _git(repository, "show", f"{candidate_sha}:aiopnsense/const.py") == f'VERSION = "{tag}"'


def test_stable_resume_shell_reuses_the_exact_candidate(tmp_path: Path) -> None:
    """Resume only an already promoted candidate whose bounded metadata is valid.

    Args:
        tmp_path (Path): Temporary release-fixture directory.
    """
    tag = "v1.2.3"
    repository, _remote = _release_fixture(tmp_path, tag)
    candidate_sha = _publish_resume_candidate(repository, tag)
    base = _run_base_step(repository, tmp_path, tag)

    assert base.returncode == 0, base.stderr
    output = (tmp_path / "base-output").read_text(encoding="utf-8")
    assert "resume=true" in output
    assert f"candidate-sha={candidate_sha}" in output


def test_stable_resume_shell_stages_new_helpers_before_detaching_old_candidate(
    tmp_path: Path,
) -> None:
    """Resume an old candidate with helpers supplied only by trusted main.

    Args:
        tmp_path (Path): Temporary release-fixture directory.
    """
    tag = "v1.2.3"
    repository, _remote = _release_fixture(tmp_path, tag, legacy_helpers=True)
    candidate_sha = _publish_resume_candidate(repository, tag)
    trusted_sha = _restore_trusted_package_helpers(repository)

    base = _run_base_step(repository, tmp_path, tag)

    assert base.returncode == 0, base.stderr
    outputs = dict(
        line.split("=", maxsplit=1)
        for line in (tmp_path / "base-output").read_text(encoding="utf-8").splitlines()
    )
    assert outputs["candidate-sha"] == candidate_sha
    assert outputs["trusted-sha"] == trusted_sha
    assert not (repository / ".github" / "scripts" / "release_version.py").exists()
    trusted_helpers = Path(outputs["trusted-helpers"])
    assert {
        "prepare_release.py",
        "release_version.py",
        "verify_aiopnsense_distributions.py",
        "verify_python_distributions.py",
        "verify_release_checks.py",
    }.issubset({path.name for path in trusted_helpers.iterdir()})


@pytest.mark.parametrize(
    ("const_version", "changelog", "extra_path"),
    [
        ("v9.9.9", None, False),
        (None, "# Changelog\n\n## [unrelated](https://example.invalid/)\n", False),
        (None, None, True),
    ],
    ids=["wrong-version", "missing-release-heading", "third-path"],
)
def test_stable_resume_shell_rejects_invalid_candidate_metadata(
    tmp_path: Path,
    const_version: str | None,
    changelog: str | None,
    extra_path: bool,
) -> None:
    """Reject stable retries that do not reproduce the bounded release shape.

    Args:
        tmp_path (Path): Temporary release-fixture directory.
        const_version (str | None): Optional incorrect version declaration.
        changelog (str | None): Optional invalid release changelog.
        extra_path (bool): Whether to add a forbidden changed file.
    """
    tag = "v1.2.3"
    repository, _remote = _release_fixture(tmp_path, tag)
    _publish_resume_candidate(
        repository,
        tag,
        const_version=const_version,
        changelog=changelog,
        extra_path=extra_path,
    )
    base = _run_base_step(repository, tmp_path, tag)

    assert base.returncode != 0
    assert "invalid release contents" in base.stderr


def test_stable_resume_shell_rejects_merge_candidate(tmp_path: Path) -> None:
    """Reject a retry candidate with multiple parents even when its files look valid.

    Args:
        tmp_path (Path): Temporary release-fixture directory.
    """
    tag = "v1.2.3"
    repository, _remote = _release_fixture(tmp_path, tag)
    _publish_resume_candidate(repository, tag)
    parent = _git(repository, "rev-parse", "HEAD^")
    _git(repository, "switch", "-c", "side", parent)
    (repository / "side.txt").write_text("side\n", encoding="utf-8")
    _git(repository, "add", "side.txt")
    _git(repository, "commit", "-m", "Side change")
    _git(repository, "checkout", "--detach", "origin/main")
    _git(repository, "merge", "--no-ff", "side", "-m", f"Release {tag}")
    _git(repository, "tag", "-fa", tag, "-m", tag)
    _git(repository, "push", "--force", "origin", "HEAD:refs/heads/main", f"refs/tags/{tag}")
    _git(repository, "fetch", "--force", "origin", "main", f"refs/tags/{tag}:refs/tags/{tag}")
    _git(repository, "checkout", "--detach", "origin/main")

    base = _run_base_step(repository, tmp_path, tag)

    assert base.returncode != 0
    assert "invalid release contents" in base.stderr


def test_prerelease_metadata_shell_uses_tag_source_and_newer_trusted_workflow(
    tmp_path: Path,
) -> None:
    """Keep prerelease validation code at trusted main while testing the tagged source.

    Args:
        tmp_path (Path): Temporary release-fixture directory.
    """
    tag = "v1.2.3-beta.1"
    repository, _remote = _release_fixture(tmp_path, tag, legacy_helpers=True)
    (repository / "aiopnsense" / "const.py").write_text(f'VERSION = "{tag}"\n', encoding="utf-8")
    _git(repository, "add", "aiopnsense/const.py")
    _git(repository, "commit", "-m", "Prepare prerelease")
    source_sha = _git(repository, "rev-parse", "HEAD")
    _git(repository, "tag", "-fa", tag, "-m", tag)
    _git(repository, "push", "--force", "origin", "HEAD:refs/heads/main", f"refs/tags/{tag}")
    trusted_sha = _restore_trusted_package_helpers(repository)

    base = _run_base_step(repository, tmp_path, tag, prerelease=True)

    assert base.returncode == 0, base.stderr
    output = (tmp_path / "base-output").read_text(encoding="utf-8")
    assert f"source-sha={source_sha}" in output
    assert f"trusted-sha={trusted_sha}" in output
    assert source_sha != trusted_sha
    assert "trusted-helper=" in output
    assert "trusted-helpers=" in output
    assert _git(repository, "rev-parse", "HEAD") == source_sha
    assert not (repository / ".github" / "scripts" / "release_version.py").exists()


@pytest.mark.parametrize("moved_ref", ["branch", "tag"])
def test_promotion_shell_refuses_moved_branch_or_tag_lease(tmp_path: Path, moved_ref: str) -> None:
    """Reject promotion before GitHub authentication when either protected ref moved.

    Args:
        tmp_path (Path): Temporary release-fixture directory.
        moved_ref (str): Protected reference changed after candidate creation.
    """
    tag = "v1.2.3"
    repository, _remote = _release_fixture(tmp_path, tag)
    target_sha = _git(repository, "rev-parse", "HEAD")
    original_tag_oid = _git(repository, "rev-parse", f"refs/tags/{tag}")
    (repository / "aiopnsense" / "const.py").write_text(f'VERSION = "{tag}"\n', encoding="utf-8")
    (repository / "docs" / "source" / "changelog.md").write_text(
        f"# Changelog\n\n## [{tag}](https://example.invalid/{tag})\n", encoding="utf-8"
    )
    _git(repository, "add", ".")
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

    promotion = _run_workflow_shell(
        repository,
        _step_containing(_workflow_text("release.yml"), "git push --atomic").split("run: |", 1)[-1],
        {
            "GH_TOKEN": "fixture-token",
            "ORIGINAL_TAG_OID": original_tag_oid,
            "RELEASE_TAG": tag,
            "RELEASE_TARGET": "main",
            "TARGET_SHA": target_sha,
        },
    )

    assert promotion.returncode != 0


def test_release_workflow_uses_published_event_and_guarded_package_promotion() -> None:
    """Require published releases, exact gates, and leased package promotion."""
    workflow = _workflow_text("release.yml")

    assert re.search(r"(?ms)^on:\s+release:\s+types:\s*\[published\]", workflow)
    assert re.search(r"(?m)^      statuses:\s*write\s*$", workflow)
    dispatch = _step_containing(workflow, 'python3 "$TRUSTED_HELPER"')
    required_checks = set(re.findall(r"--required-check\s+['\"]([^'\"]+)", dispatch))
    assert required_checks == {
        "pytest_check.yml::pytest check and post coverage",
        "docs.yml::build-docs",
        "uv-lock-check.yml::Validate uv lock consistency",
        "prek-autofix-review.yml::review",
    }
    assert '--workflow-ref "$TRUSTED_REF"' in dispatch
    assert '--workflow-sha "$TRUSTED_SHA"' in dispatch
    assert (
        "CANDIDATE_SHA: ${{ github.event.release.prerelease == true && steps.base.outputs.source-sha || steps.candidate.outputs.sha }}"
        in dispatch
    )
    assert 'python3 "$TRUSTED_HELPER"' in dispatch
    assert "if: github.event.release.prerelease == false" not in dispatch

    validation_branch = _step_containing(
        workflow, "Publish candidate to an isolated validation branch"
    )
    assert "if: github.event.release.prerelease == false" in validation_branch

    promotion = _normalized(_step_containing(workflow, "git push --atomic"))
    assert '--force-with-lease="refs/heads/$RELEASE_TARGET:$TARGET_SHA"' in promotion
    assert '--force-with-lease="refs/tags/$RELEASE_TAG:$ORIGINAL_TAG_OID"' in promotion

    metadata = _normalized(_step_containing(workflow, 'tag_sha="$(git rev-parse'))
    assert 'git diff --name-only "$tag_sha^" "$tag_sha"' in metadata
    assert 'git show "$tag_sha:aiopnsense/const.py"' in metadata
    assert 'git show "$tag_sha:docs/source/changelog.md"' in metadata
    assert 'grep -F "## [$RELEASE_TAG]("' in metadata
    for helper in (
        "prepare_release.py",
        "release_version.py",
        "verify_aiopnsense_distributions.py",
        "verify_python_distributions.py",
        "verify_release_checks.py",
    ):
        assert helper in metadata
    assert 'python3 "$trusted_prepare" --repository "$resume_root" "$RELEASE_TAG"' in metadata
    assert 'echo "trusted-helper=$trusted_helper"' in metadata
    assert 'echo "trusted-helpers=$trusted_helpers"' in metadata
    assert 'git checkout --detach "$source_sha"' in metadata

    candidate = _normalized(_step_containing(workflow, "Stable release candidate must contain"))
    assert 'changed_before_version="$(git diff --name-only)"' in candidate
    assert "git diff --cached --name-only" in candidate
    assert "! git diff --quiet" in candidate

    build = _normalized(_step_containing(workflow, "Build and check distributions"))
    assert "uv build" in build
    assert "rm -f dist/.gitignore" in build
    assert "twine check dist/*" in build
    assert (
        '"$TRUSTED_HELPERS/verify_aiopnsense_distributions.py" "$RELEASE_TAG" --dist-dir dist'
        in build
    )

    cleanup = _normalized(_step_containing(workflow, "Delete validated temporary branch"))
    assert "if: github.event.release.prerelease == false && success()" in cleanup
    assert '--force-with-lease="refs/heads/$TEMP_REF:$CANDIDATE_SHA"' in cleanup


def test_publish_job_receives_only_verified_artifacts_and_oidc_identity() -> None:
    """Keep candidate repository code out of the PyPI write boundary."""
    workflow = _workflow_text("release.yml")
    publish = workflow.split("  publish:\n", maxsplit=1)[1]

    assert "id-token: write" in publish
    assert "actions/download-artifact@v8" in publish
    assert "name: python-distributions" in publish
    assert "actions/checkout@" not in publish
    assert "repository-url: https://test.pypi.org/legacy/" in publish
    assert (
        "name: ${{ needs.release.outputs.is-prerelease == 'true' && 'testpypi' || 'pypi' }}"
        in publish
    )
    assert publish.count("pypa/gh-action-pypi-publish@release/v1") == 2


@pytest.mark.parametrize(
    "workflow_name",
    ["pytest_check.yml", "docs.yml", "uv-lock-check.yml", "prek-autofix-review.yml"],
)
def test_release_gate_workflows_require_and_checkout_exact_sha(workflow_name: str) -> None:
    """Require every dispatched gate to validate and check out the candidate SHA.

    Args:
        workflow_name (str): Workflow filename under test.
    """
    workflow = _workflow_text(workflow_name)

    assert re.search(
        r"(?ms)^  workflow_dispatch:\s+inputs:\s+expected_sha:.*?"
        r"^        required:\s*true\s+^        type:\s*string\s*$",
        workflow,
    )
    guard = _normalized(_step_containing(workflow, "Require expected release commit"))
    assert '[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]]' in guard
    assert "WORKFLOW_SHA" not in guard
    checkouts = [
        _normalized(step)
        for step in re.split(r"(?m)(?=^      - )", workflow)
        if "actions/checkout@" in step
    ]
    assert any(
        re.search(r"ref:\s*\$\{\{[^}]*inputs\.expected_sha[^}]*}}", checkout)
        and "persist-credentials: false" in checkout
        for checkout in checkouts
    )
    verification = _normalized(_step_containing(workflow, "Verify checked out release commit"))
    assert 'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"' in verification


def test_release_gate_dispatch_concurrency_isolated_by_candidate_sha() -> None:
    """Dispatches isolate candidates while preserving pull-request and push grouping."""
    workflow = _workflow_text("prek-autofix-review.yml")

    assert (
        "group: prek-autofix-${{ github.event.pull_request.number || "
        "inputs.expected_sha || github.ref }}" in workflow
    )
    assert "cancel-in-progress: true" in workflow

    def group(pull_request: str = "", expected_sha: str = "", ref: str = "") -> str:
        """Resolve the ordered GitHub expression used by the asserted workflow text.

        Args:
            pull_request (str): Optional pull request number.
            expected_sha (str): Optional immutable dispatched candidate SHA.
            ref (str): Fallback branch or tag ref.

        Returns:
            str: The rendered concurrency key.
        """
        return "prek-autofix-" + (pull_request or expected_sha or ref)

    assert group(expected_sha="a" * 40) != group(expected_sha="b" * 40)
    assert group(expected_sha="a" * 40) == group(expected_sha="a" * 40)
    assert (
        group(pull_request="37", expected_sha="a" * 40, ref="refs/heads/main") == "prek-autofix-37"
    )
    assert group(ref="refs/heads/main") == "prek-autofix-refs/heads/main"


def test_release_gate_prek_dispatch_inherits_locked_uv() -> None:
    """The dispatch path locks nested uv commands and retains the clean-tree proof."""
    workflow = _workflow_text("prek-autofix-review.yml")
    dispatch = _normalized(_step_containing(workflow, "Verify prek without pull-request context"))

    assert "if: github.event_name == 'workflow_dispatch'" in dispatch
    assert "UV_LOCKED=1 uv run --locked prek run --all-files" in dispatch
    assert "git diff --exit-code" in dispatch


@pytest.mark.parametrize(
    "workflow_name",
    ["pytest_check.yml", "docs.yml", "uv-lock-check.yml", "prek-autofix-review.yml"],
)
def test_release_gate_shells_accept_trusted_controller_and_verify_candidate_checkout(
    tmp_path: Path, workflow_name: str
) -> None:
    """Allow a trusted controller SHA while requiring the checked-out candidate SHA.

    Args:
        tmp_path (Path): Temporary repository used to execute the workflow shell guards.
        workflow_name (str): Existing release-gate workflow under test.
    """
    repository = tmp_path / "repository"
    _git(tmp_path, "init", "-b", "main", str(repository))
    _git(repository, "config", "user.name", "Release Test")
    _git(repository, "config", "user.email", "release-test@example.invalid")
    (repository / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    _git(repository, "add", "candidate.txt")
    _git(repository, "commit", "-m", "Candidate")
    candidate_sha = _git(repository, "rev-parse", "HEAD")
    (repository / "controller.txt").write_text("trusted controller\n", encoding="utf-8")
    _git(repository, "add", "controller.txt")
    _git(repository, "commit", "-m", "Trusted controller")
    controller_sha = _git(repository, "rev-parse", "HEAD")
    assert controller_sha != candidate_sha

    workflow = _workflow_text(workflow_name)
    guard = _step_containing(workflow, "Require expected release commit").split("run: |", 1)[-1]
    guard_result = _run_workflow_shell(
        repository,
        guard,
        {"EXPECTED_SHA": candidate_sha, "WORKFLOW_SHA": controller_sha},
    )
    assert guard_result.returncode == 0, guard_result.stderr

    malformed_result = _run_workflow_shell(
        repository,
        guard,
        {"EXPECTED_SHA": "not-a-git-object", "WORKFLOW_SHA": controller_sha},
    )
    assert malformed_result.returncode != 0

    _git(repository, "checkout", "--detach", candidate_sha)
    verification = _step_containing(workflow, "Verify checked out release commit").split(
        "run: ", 1
    )[-1]
    verified = _run_workflow_shell(repository, verification, {"EXPECTED_SHA": candidate_sha})
    assert verified.returncode == 0, verified.stderr

    _git(repository, "checkout", "--detach", controller_sha)
    wrong_head = _run_workflow_shell(repository, verification, {"EXPECTED_SHA": candidate_sha})
    assert wrong_head.returncode != 0
