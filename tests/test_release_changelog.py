"""Behavioral tests for stable release changelog prepending."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def load_release_changelog() -> ModuleType:
    """Load the workflow helper without packaging GitHub workflow scripts.

    Returns:
        ModuleType: The release changelog helper module.
    """
    path = Path(__file__).parents[1] / ".github" / "scripts" / "release_changelog.py"
    specification = importlib.util.spec_from_file_location("release_changelog", path)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


release_changelog = load_release_changelog()


def test_previous_stable_release_tag_uses_shared_policy() -> None:
    """Select a stable 2-to-4 component predecessor and skip prerelease tags."""
    tags = ["v1.9", "v2.0.0-beta.2", "v2.0.0.1", "v2.0.1rc1", "invalid"]
    assert release_changelog.previous_stable_release_tag(tags, "v2.1") == "v2.0.0.1"


def test_previous_stable_release_tag_rejects_prerelease_targets() -> None:
    """Reject prerelease targets that do not own stable changelog generation."""
    with pytest.raises(ValueError, match="Stable release tag required"):
        release_changelog.previous_stable_release_tag(["v1.0.0"], "v1.0.0-rc.1")


def test_previous_stable_release_tag_returns_none_for_the_first_stable_release() -> None:
    """Let the workflow use its source root when no stable tag is reachable."""
    assert release_changelog.previous_stable_release_tag(["v1.0.0-rc.1"], "v1.0.0") is None


def _repository_with_history(tmp_path: Path) -> tuple[Path, Path]:
    """Create a changelog fixture with history and an action-output file.

    Args:
        tmp_path (Path): Temporary test directory.

    Returns:
        tuple[Path, Path]: Repository root and writable generated-fragment path.
    """
    changelog = tmp_path / "docs" / "source" / "changelog.md"
    changelog.parent.mkdir(parents=True)
    changelog.write_bytes(b"# Changelog\n\n## [v1.0.0](history)\nlegacy\n")
    return tmp_path, tmp_path / ".release-changelog.md"


def test_prepend_release_changelog_preserves_history_and_treats_fragment_as_data(
    tmp_path: Path,
) -> None:
    """Prepend action output verbatim without interpreting title-like shell text.

    Args:
        tmp_path (Path): Temporary repository fixture root.
    """
    repository, fragment = _repository_with_history(tmp_path)
    fragment.write_bytes(b"**Bug Fixes**\n\n- $(not-a-command) `quoted` #1\n")
    original_history = (repository / "docs" / "source" / "changelog.md").read_bytes()

    release_changelog.prepend_release_changelog(
        repository,
        fragment,
        "v1.1.0",
        "v1.0.0",
        "2026-09-07",
        "Snuffy2/aiopnsense",
    )

    content = (repository / "docs" / "source" / "changelog.md").read_bytes()
    assert content.startswith(
        b"# Changelog\n\n"
        b"## [v1.1.0](https://github.com/Snuffy2/aiopnsense/tree/v1.1.0) (2026-09-07)\n\n"
        b"[Full Changelog](https://github.com/Snuffy2/aiopnsense/compare/v1.0.0...v1.1.0)\n\n"
    )
    assert content.endswith(original_history.removeprefix(b"# Changelog\n\n"))
    assert b"$(not-a-command) `quoted` #1" in content


def test_prepend_release_changelog_keeps_a_valid_empty_release_entry(tmp_path: Path) -> None:
    """Keep the exact heading and history when the action finds no pull requests.

    Args:
        tmp_path (Path): Temporary repository fixture root.
    """
    repository, fragment = _repository_with_history(tmp_path)
    fragment.write_bytes(b"\n")
    original_history = (repository / "docs" / "source" / "changelog.md").read_bytes()

    release_changelog.prepend_release_changelog(
        repository,
        fragment,
        "v1.1.0",
        "v1.0.0",
        "2026-09-07",
        "Snuffy2/aiopnsense",
    )

    content = (repository / "docs" / "source" / "changelog.md").read_bytes()
    assert content.startswith(b"# Changelog\n\n## [v1.1.0](")
    assert content.endswith(original_history.removeprefix(b"# Changelog\n\n"))
    assert b"**Other Changes**" not in content
    assert content.count(b"## [v1.1.0](") == 1


def test_prepend_release_changelog_rejects_a_symbolic_link_fragment(tmp_path: Path) -> None:
    """Reject a link before reading generated changelog data.

    Args:
        tmp_path (Path): Temporary repository fixture root.
    """
    repository, fragment = _repository_with_history(tmp_path)
    target = tmp_path / "fragment.txt"
    target.write_text("entries\n", encoding="utf-8")
    fragment.symlink_to(target)

    with pytest.raises(ValueError, match="regular file"):
        release_changelog.prepend_release_changelog(
            repository,
            fragment,
            "v1.1.0",
            "v1.0.0",
            "2026-09-07",
            "Snuffy2/aiopnsense",
        )
