"""Tests for the shared release-tag to PEP 440 normalization policy."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def load_release_version() -> ModuleType:
    """Load the release policy without packaging GitHub workflow scripts.

    Returns:
        ModuleType: The release tag policy module.
    """
    path = Path(__file__).parents[1] / ".github" / "scripts" / "release_version.py"
    specification = importlib.util.spec_from_file_location("release_version_policy", path)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


policy = load_release_version()


@pytest.mark.parametrize(
    ("release_tag", "expected"),
    [
        ("v1.2-alpha.0", "1.2a0"),
        ("v1.2-alpha.01", "1.2a1"),
        ("v1.2-alpha.10", "1.2a10"),
        ("v1.2a01", "1.2a1"),
        ("v1.2-beta.0", "1.2b0"),
        ("v1.2-beta.01", "1.2b1"),
        ("v1.2-beta.10", "1.2b10"),
        ("v1.2b01", "1.2b1"),
        ("v1.2-rc.0", "1.2rc0"),
        ("v1.2-rc.01", "1.2rc1"),
        ("v1.2-rc.10", "1.2rc10"),
        ("v1.2rc01", "1.2rc1"),
        ("v1.2-dev.0", "1.2.dev0"),
        ("v1.2-dev.01", "1.2.dev1"),
        ("v1.2-dev.10", "1.2.dev10"),
        ("v1.2-post.0", "1.2.post0"),
        ("v1.2-post.01", "1.2.post1"),
        ("v1.2-post.10", "1.2.post10"),
    ],
)
def test_normalized_version_canonicalizes_accepted_ascii_serials(
    release_tag: str, expected: str
) -> None:
    """Accepted PEP 440 suffix serials normalize to package artifact versions.

    Args:
        release_tag (str): Accepted repository tag with a suffix serial.
        expected (str): Canonical PEP 440 version used by package artifacts.
    """
    assert policy.normalized_version(release_tag) == expected


@pytest.mark.parametrize(
    "release_tag",
    [
        "v1.2-alpha.١",
        "v1.2-beta.١",
        "v1.2-rc.١",
        "v1.2-dev.١",
        "v1.2-post.١",
    ],
)
def test_normalized_version_rejects_unicode_suffix_serials(release_tag: str) -> None:
    """Unicode digits do not reach accepted serial canonicalization.

    Args:
        release_tag (str): Tag containing a non-ASCII suffix serial.
    """
    with pytest.raises(policy.ReleaseTagError, match="Unsupported package release tag"):
        policy.normalized_version(release_tag)
