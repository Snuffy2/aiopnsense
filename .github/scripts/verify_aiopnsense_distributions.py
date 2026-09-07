"""Verify aiopnsense release distributions with the shared integrity core."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import re
import tarfile
from typing import Any
import zipfile

PROJECT_NAME = "aiopnsense"
VERSION_PATTERN = re.compile(r'^VERSION = "([^"]+)"$', re.MULTILINE)


class AiopnsenseDistributionError(RuntimeError):
    """Raised when aiopnsense-specific release assertions fail."""


def shared_core() -> Any:
    """Load the adjacent shared verifier without packaging workflow scripts.

    Returns:
        Shared generic distribution-verifier module.

    Raises:
        AiopnsenseDistributionError: If the shared verifier cannot be loaded.
    """
    core_path = Path(__file__).with_name("verify_python_distributions.py")
    spec = importlib.util.spec_from_file_location("verify_python_distributions", core_path)
    if spec is None or spec.loader is None:
        raise AiopnsenseDistributionError("Could not load the shared distribution verifier.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def release_version_module() -> Any:
    """Load the adjacent tag-policy module without packaging workflow scripts.

    Returns:
        Loaded release-version policy module.

    Raises:
        AiopnsenseDistributionError: If the release version policy cannot be loaded.
    """
    policy_path = Path(__file__).with_name("release_version.py")
    spec = importlib.util.spec_from_file_location("release_version", policy_path)
    if spec is None or spec.loader is None:
        raise AiopnsenseDistributionError("Could not load the release version policy.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalized_version(release_tag: str) -> str:
    """Normalize one allowed aiopnsense release tag to PEP 440.

    Args:
        release_tag: Repository release tag beginning with ``v``.

    Returns:
        Normalized distribution version without the repository tag prefix.

    Raises:
        AiopnsenseDistributionError: If the tag is outside the supported grammar.
    """
    policy = release_version_module()
    try:
        return policy.normalized_version(release_tag)
    except policy.ReleaseTagError as error:
        raise AiopnsenseDistributionError(str(error)) from error


def _require_literal_version(contents: bytes, release_tag: str, source: str) -> None:
    """Require one literal version declaration in a package source member.

    Args:
        contents: Source-member bytes.
        release_tag: Expected literal repository release tag.
        source: Member label included in error messages.

    Raises:
        AiopnsenseDistributionError: If the source does not have the expected declaration.
    """
    try:
        versions = VERSION_PATTERN.findall(contents.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise AiopnsenseDistributionError(f"Could not decode {source}.") from error
    if versions != [release_tag]:
        raise AiopnsenseDistributionError(f"{source} does not match release tag.")


def verify_package_versions(dist_dir: Path, release_tag: str, version: str) -> None:
    """Require both distribution forms to carry the literal aiopnsense version.

    Args:
        dist_dir: Directory containing the already generic-verified distributions.
        release_tag: Expected literal repository version tag.
        version: Normalized distribution version.

    Raises:
        AiopnsenseDistributionError: If a packaged source version is absent or mismatched.
    """
    wheel_path = dist_dir / f"{PROJECT_NAME}-{version}-py3-none-any.whl"
    sdist_path = dist_dir / f"{PROJECT_NAME}-{version}.tar.gz"
    wheel_const_path = f"{PROJECT_NAME}/const.py"
    sdist_const_path = f"{PROJECT_NAME}-{version}/{PROJECT_NAME}/const.py"
    try:
        with zipfile.ZipFile(wheel_path) as archive:
            _require_literal_version(
                archive.read(wheel_const_path),
                release_tag,
                f"Wheel {wheel_const_path}",
            )
    except (KeyError, OSError, zipfile.BadZipFile) as error:
        raise AiopnsenseDistributionError(
            f"Could not read aiopnsense wheel version: {error}"
        ) from error
    try:
        with tarfile.open(sdist_path, "r:gz") as archive:
            source = archive.extractfile(sdist_const_path)
            if source is None:
                raise AiopnsenseDistributionError(
                    f"Source distribution {sdist_const_path} is missing."
                )
            _require_literal_version(
                source.read(),
                release_tag,
                f"Source distribution {sdist_const_path}",
            )
    except (KeyError, OSError, tarfile.TarError) as error:
        raise AiopnsenseDistributionError(
            f"Could not read aiopnsense source version: {error}"
        ) from error


def verify_aiopnsense_distributions(release_tag: str, dist_dir: Path) -> None:
    """Apply shared integrity and aiopnsense source-version verification.

    Args:
        release_tag: Requested repository release tag.
        dist_dir: Directory containing the generated distribution pair.

    Raises:
        AiopnsenseDistributionError: If generic or source-specific verification fails.
    """
    version = normalized_version(release_tag)
    core = shared_core()
    try:
        core.verify_python_distributions(
            dist_dir,
            distribution_stem=PROJECT_NAME,
            metadata_name=PROJECT_NAME,
            version=version,
            requires_python=">=3.14",
        )
    except core.DistributionVerificationError as error:
        raise AiopnsenseDistributionError(str(error)) from error
    verify_package_versions(dist_dir, release_tag, version)


def main() -> int:
    """Verify aiopnsense distributions requested from GitHub Actions.

    Returns:
        Zero when verification succeeds, otherwise the argparse failure exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release_tag")
    parser.add_argument("--dist-dir", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        verify_aiopnsense_distributions(arguments.release_tag, arguments.dist_dir)
    except AiopnsenseDistributionError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
