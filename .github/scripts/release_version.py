"""Normalize supported aiopnsense repository tags for package metadata."""

from __future__ import annotations

import re

_NUMERIC_COMPONENT = r"(?:0|[1-9][0-9]*)"
_RELEASE = rf"{_NUMERIC_COMPONENT}(?:\.{_NUMERIC_COMPONENT}){{1,3}}"
_STABLE_TAG = re.compile(rf"^v(?P<release>{_RELEASE})$")
_HYPHEN_PRE_RELEASE = re.compile(
    rf"^v(?P<release>{_RELEASE})-(?P<label>a|alpha|b|beta|rc)\.(?P<serial>[0-9]+)$"
)
_COMPACT_PRE_RELEASE = re.compile(rf"^v(?P<release>{_RELEASE})(?P<label>a|b|rc)(?P<serial>[0-9]+)$")
_DEV_RELEASE = re.compile(rf"^v(?P<release>{_RELEASE})(?:-dev\.?|dev)(?P<serial>[0-9]+)$")
_POST_RELEASE = re.compile(rf"^v(?P<release>{_RELEASE})(?:-post\.?|post)(?P<serial>[0-9]+)$")
_PRE_RELEASE_LABELS = {"a": "a", "alpha": "a", "b": "b", "beta": "b", "rc": "rc"}


class ReleaseTagError(ValueError):
    """Raised when a repository tag cannot be represented as a package version."""


def canonical_serial(serial: str) -> str:
    """Return a PEP 440 canonical decimal serial from an accepted ASCII serial.

    Args:
        serial: Decimal serial already validated against the fixed ASCII grammar.

    Returns:
        Decimal serial without leading zeroes, except for zero itself.
    """
    return str(int(serial))


def normalized_version(release_tag: str) -> str:
    """Return the normalized PEP 440 version represented by one repository tag.

    Args:
        release_tag: ``v``-prefixed stable, prerelease, development, or postrelease tag.

    Returns:
        Normalized PEP 440 package version without the repository tag prefix.

    Raises:
        ReleaseTagError: If the tag is outside the supported fixed ASCII grammar.
    """
    stable = _STABLE_TAG.fullmatch(release_tag)
    if stable is not None:
        return stable["release"]
    prerelease = _HYPHEN_PRE_RELEASE.fullmatch(release_tag)
    if prerelease is not None:
        return (
            f"{prerelease['release']}{_PRE_RELEASE_LABELS[prerelease['label']]}"
            f"{canonical_serial(prerelease['serial'])}"
        )
    prerelease = _COMPACT_PRE_RELEASE.fullmatch(release_tag)
    if prerelease is not None:
        return (
            f"{prerelease['release']}{prerelease['label']}{canonical_serial(prerelease['serial'])}"
        )
    development = _DEV_RELEASE.fullmatch(release_tag)
    if development is not None:
        return f"{development['release']}.dev{canonical_serial(development['serial'])}"
    postrelease = _POST_RELEASE.fullmatch(release_tag)
    if postrelease is not None:
        return f"{postrelease['release']}.post{canonical_serial(postrelease['serial'])}"
    raise ReleaseTagError(f"Unsupported package release tag: {release_tag!r}.")


def is_prerelease_tag(release_tag: str) -> bool:
    """Return whether a validated repository tag is a prerelease.

    Args:
        release_tag: Repository tag to validate and classify.

    Returns:
        True when the tag is not a stable release.

    Raises:
        ReleaseTagError: If the tag is outside the supported fixed ASCII grammar.
    """
    normalized_version(release_tag)
    return _STABLE_TAG.fullmatch(release_tag) is None
