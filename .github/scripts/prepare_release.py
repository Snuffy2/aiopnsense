"""Validate a release tag and prepare the package version file."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
import importlib.util
from pathlib import Path
import re
import sys
from typing import Any

CONST_VERSION_PATTERN = re.compile(r'^(VERSION\s*=\s*)"[^"]*"', re.MULTILINE)


def release_version_module() -> Any:
    """Load the adjacent tag-policy module without packaging workflow scripts.

    Returns:
        Loaded release-version policy module.

    Raises:
        ValueError: If the adjacent policy module cannot be loaded.
    """
    policy_path = Path(__file__).with_name("release_version.py")
    spec = importlib.util.spec_from_file_location("release_version", policy_path)
    if spec is None or spec.loader is None:
        raise ValueError("Could not load the release version policy.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_release_tag(tag: str) -> None:
    """Validate a release tag against the repository's supported formats.

    Args:
        tag (str): Candidate release tag.

    Raises:
        ValueError: If the tag does not use a supported version format.
    """
    policy = release_version_module()
    try:
        policy.normalized_version(tag)
    except policy.ReleaseTagError as error:
        msg = f"Invalid release tag: {tag}"
        raise ValueError(msg) from error


def validate_release_request(tag: str, prerelease: bool) -> None:
    """Validate that a release tag agrees with the prerelease selection.

    Args:
        tag (str): Candidate release tag.
        prerelease (bool): Whether the release should be treated as a prerelease.

    Raises:
        ValueError: If the tag format and prerelease selection disagree.
    """
    policy = release_version_module()
    try:
        tag_is_prerelease = policy.is_prerelease_tag(tag)
    except policy.ReleaseTagError as error:
        msg = f"Invalid release tag: {tag}"
        raise ValueError(msg) from error
    if tag_is_prerelease != prerelease:
        tag_kind = "Prerelease" if tag_is_prerelease else "Stable"
        required_value = str(tag_is_prerelease).lower()
        msg = f"{tag_kind} tag {tag} requires prerelease={required_value}."
        raise ValueError(msg)


def next_stable_release_tag(tags: Iterable[str], bump_type: str) -> str:
    """Return the next stable tag after the highest released stable version.

    Args:
        tags (Iterable[str]): Candidate tag names from the release repository.
        bump_type (str): Requested stable version increment.

    Returns:
        str: The next stable release tag.

    Raises:
        ValueError: If the bump type is unsupported or no stable tag is found.
    """
    if bump_type not in {"patch", "minor", "major"}:
        msg = f"Unsupported bump type: {bump_type}"
        raise ValueError(msg)

    policy = release_version_module()
    versions = []
    for tag in tags:
        try:
            if policy.is_prerelease_tag(tag):
                continue
            version = policy.normalized_version(tag)
        except policy.ReleaseTagError:
            continue
        parts = tuple(int(component) for component in version.split("."))
        versions.append(parts + (0,) * (4 - len(parts)))
    if not versions:
        msg = "No stable released tag found."
        raise ValueError(msg)

    major, minor, patch, _build = max(versions)
    if bump_type == "patch":
        patch += 1
    elif bump_type == "minor":
        minor += 1
        patch = 0
    else:
        major += 1
        minor = 0
        patch = 0
    return f"v{major}.{minor}.{patch}"


def update_release_version(repository: Path, tag: str) -> None:
    """Update the package version to the requested release tag.

    Args:
        repository (Path): Repository root containing the package.
        tag (str): Requested release tag.

    Raises:
        ValueError: If the tag or version declaration is invalid.
    """
    validate_release_tag(tag)
    const_path = repository / "aiopnsense" / "const.py"
    content = const_path.read_text(encoding="utf-8")
    updated, replacements = CONST_VERSION_PATTERN.subn(
        lambda match: f'{match.group(1)}"{tag}"', content
    )
    if replacements != 1:
        msg = f"Expected one version declaration in {const_path}, found {replacements}."
        raise ValueError(msg)
    const_path.write_text(updated, encoding="utf-8")


def main() -> int:
    """Run the release preparation command.

    Returns:
        int: Zero when validation or version preparation succeeds.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", nargs="?", help="Release tag, such as v1.1.8")
    parser.add_argument(
        "--next-tag",
        metavar="BUMP_TYPE",
        help="Print the next stable tag for patch, minor, or major",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate the tag without updating the version file",
    )
    parser.add_argument(
        "--expected-prerelease",
        choices=("true", "false"),
        help="Require the tag to match the workflow prerelease selection",
    )
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path.cwd(),
        help="Repository root containing the package version file",
    )
    args = parser.parse_args()

    try:
        if (args.tag is None) == (args.next_tag is None):
            msg = "Provide exactly one release tag or --next-tag BUMP_TYPE."
            raise ValueError(msg)
        if args.next_tag is not None:
            if args.check_only or args.expected_prerelease is not None:
                msg = "Validation options cannot be used with --next-tag."
                raise ValueError(msg)
            sys.stdout.write(
                f"{next_stable_release_tag(sys.stdin.read().splitlines(), args.next_tag)}\n"
            )
        elif args.check_only:
            if args.expected_prerelease is None:
                validate_release_tag(args.tag)
            else:
                validate_release_request(args.tag, args.expected_prerelease == "true")
        else:
            if args.expected_prerelease is not None:
                msg = "--expected-prerelease requires --check-only."
                raise ValueError(msg)
            update_release_version(args.repository, args.tag)
    except (OSError, ValueError) as error:
        parser.error(str(error))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
