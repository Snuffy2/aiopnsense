"""Prepend one generated release entry while retaining historical changelog bytes."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
import importlib.util
from pathlib import Path
import sys
from typing import Any


def release_version_module() -> Any:
    """Load the adjacent shared release-tag policy module.

    Returns:
        Loaded release-version policy module.

    Raises:
        ValueError: If the adjacent policy module cannot be loaded.
    """
    policy_path = Path(__file__).with_name("release_version.py")
    specification = importlib.util.spec_from_file_location("release_version", policy_path)
    if specification is None or specification.loader is None:
        raise ValueError("Could not load the release version policy.")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def previous_stable_release_tag(tags: Iterable[str], release_tag: str) -> str | None:
    """Select the newest stable reachable tag before a stable release.

    Args:
        tags: Repository tags reachable from the release source commit.
        release_tag: Stable release tag whose predecessor is requested.

    Returns:
        The highest stable tag older than ``release_tag``, or None for a first stable release.

    Raises:
        ValueError: If the requested tag is not stable.
    """
    policy = release_version_module()
    try:
        if policy.is_prerelease_tag(release_tag):
            raise ValueError(f"Stable release tag required: {release_tag!r}.")
        target = _version_key(policy.normalized_version(release_tag))
    except policy.ReleaseTagError as error:
        raise ValueError(f"Stable release tag required: {release_tag!r}.") from error
    candidates = []
    for tag in tags:
        try:
            if policy.is_prerelease_tag(tag):
                continue
            version = _version_key(policy.normalized_version(tag))
        except policy.ReleaseTagError:
            continue
        if version < target:
            candidates.append((tag, version))
    return max(candidates, key=lambda candidate: candidate[1])[0] if candidates else None


def _version_key(version: str) -> tuple[int, int, int, int]:
    """Normalize a shared-policy stable version into a fixed-width comparison key.

    Args:
        version: Valid stable version without its repository tag prefix.

    Returns:
        Four numeric version components, padded with zeroes.
    """
    components = [int(component) for component in version.split(".")]
    padded = (components + [0, 0, 0, 0])[:4]
    return padded[0], padded[1], padded[2], padded[3]


def prepend_release_changelog(
    repository: Path,
    fragment: Path,
    release_tag: str,
    previous_tag: str,
    release_date: str,
    github_repository: str,
) -> None:
    """Write a stable release heading, generated entries, and untouched history.

    Args:
        repository: Repository root containing the changelog.
        fragment: Regular action-created file containing generated release entries.
        release_tag: Stable tag being released.
        previous_tag: Earlier stable tag used as the changelog baseline.
        release_date: Immutable source commit date in ISO ``YYYY-MM-DD`` form.
        github_repository: ``owner/repository`` name used for public links.

    Raises:
        ValueError: If the fragment is missing or is a symbolic link.
    """
    if fragment.is_symlink() or not fragment.is_file():
        raise ValueError(f"Generated changelog fragment must be a regular file: {fragment}")

    changelog = repository / "docs" / "source" / "changelog.md"
    history = changelog.read_bytes()
    preamble = b"# Changelog\n\n"
    if not history.startswith(preamble):
        raise ValueError(f"Changelog does not start with the expected preamble: {changelog}")
    generated = fragment.read_bytes().strip()
    heading = (
        f"## [{release_tag}](https://github.com/{github_repository}/tree/{release_tag}) "
        f"({release_date})\n\n"
        f"[Full Changelog](https://github.com/{github_repository}/compare/"
        f"{previous_tag}...{release_tag})\n"
    ).encode()
    entry = heading
    if generated:
        entry += b"\n" + generated + b"\n"
    changelog.write_bytes(preamble + entry + b"\n" + history[len(preamble) :])


def main() -> int:
    """Prepend one action-created changelog fragment.

    Returns:
        Zero when the changelog is updated.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--fragment", type=Path)
    parser.add_argument("--release-tag")
    parser.add_argument("--previous-tag")
    parser.add_argument("--release-date")
    parser.add_argument("--github-repository")
    parser.add_argument("--previous-stable-tag", metavar="RELEASE_TAG")
    args = parser.parse_args()

    try:
        if args.previous_stable_tag is not None:
            if any(
                value is not None
                for value in (
                    args.fragment,
                    args.release_tag,
                    args.previous_tag,
                    args.release_date,
                    args.github_repository,
                )
            ):
                raise ValueError(
                    "Changelog prepending options cannot select a previous stable tag."
                )
            previous_tag = previous_stable_release_tag(
                sys.stdin.read().splitlines(), args.previous_stable_tag
            )
            if previous_tag is not None:
                sys.stdout.write(f"{previous_tag}\n")
        elif None in (
            args.fragment,
            args.release_tag,
            args.previous_tag,
            args.release_date,
            args.github_repository,
        ):
            raise ValueError("Changelog prepending requires every changelog option.")
        else:
            prepend_release_changelog(
                args.repository,
                args.fragment,
                args.release_tag,
                args.previous_tag,
                args.release_date,
                args.github_repository,
            )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
