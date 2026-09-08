"""Verify aiopnsense release distributions with the shared integrity core."""

from __future__ import annotations

import argparse
from email.parser import BytesParser
from email.policy import default
import importlib.util
from pathlib import Path
import re
import stat
import tarfile
import tomllib
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


def _source_payload(source_root: Path) -> dict[str, bytes]:
    """Read the exact regular package files from a reconstructed candidate tree.

    Args:
        source_root: Candidate tree reconstructed from trusted Git refs and bounded data.

    Returns:
        Package paths relative to ``aiopnsense`` and their bytes.

    Raises:
        AiopnsenseDistributionError: If the candidate tree is missing or unsafe.
    """
    package_root = source_root / PROJECT_NAME
    if not package_root.is_dir():
        raise AiopnsenseDistributionError("Reconstructed candidate package is missing.")
    payload: dict[str, bytes] = {}
    for path in sorted(package_root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            if path.is_symlink():
                raise AiopnsenseDistributionError("Candidate package contains a symbolic link.")
            continue
        payload[path.relative_to(package_root).as_posix()] = path.read_bytes()
    if not payload:
        raise AiopnsenseDistributionError("Reconstructed candidate package is empty.")
    return payload


def verify_package_payloads(dist_dir: Path, source_root: Path, version: str) -> None:
    """Prove wheel and sdist package bytes came from the reconstructed candidate.

    Args:
        dist_dir: Directory containing the generic-verified distribution pair.
        source_root: Candidate tree reconstructed from the bounded Git bundle.
        version: Normalized PEP 440 distribution version.

    Raises:
        AiopnsenseDistributionError: If an archive payload differs from the candidate tree.
    """
    expected = _source_payload(source_root)
    wheel_path = dist_dir / f"{PROJECT_NAME}-{version}-py3-none-any.whl"
    sdist_path = dist_dir / f"{PROJECT_NAME}-{version}.tar.gz"
    try:
        with zipfile.ZipFile(wheel_path) as archive:
            names = archive.namelist()
            dist_info = f"{PROJECT_NAME}-{version}.dist-info"
            expected_names = {f"{PROJECT_NAME}/{name}" for name in expected}
            expected_names.update(
                {
                    f"{dist_info}/METADATA",
                    f"{dist_info}/WHEEL",
                    f"{dist_info}/RECORD",
                    f"{dist_info}/licenses/LICENSE",
                    f"{dist_info}/top_level.txt",
                }
            )
            if set(names) != expected_names or len(names) != len(expected_names):
                raise AiopnsenseDistributionError(
                    "Wheel contains unexpected or missing installer payload."
                )
            if any((member.external_attr >> 16) & 0o111 for member in archive.infolist()):
                raise AiopnsenseDistributionError("Wheel contains an executable member.")
            wheel = {
                name.removeprefix(f"{PROJECT_NAME}/"): archive.read(name)
                for name in names
                if name.startswith(f"{PROJECT_NAME}/")
            }
            if (
                archive.read(f"{dist_info}/licenses/LICENSE")
                != (source_root / "LICENSE").read_bytes()
            ):
                raise AiopnsenseDistributionError("Wheel license differs from trusted source.")
            if archive.read(f"{dist_info}/top_level.txt") != f"{PROJECT_NAME}\n".encode():
                raise AiopnsenseDistributionError("Wheel top-level metadata is invalid.")
    except (OSError, zipfile.BadZipFile) as error:
        raise AiopnsenseDistributionError("Could not read aiopnsense wheel payload.") from error
    root = f"{PROJECT_NAME}-{version}/{PROJECT_NAME}/"
    try:
        with tarfile.open(sdist_path, "r:gz") as archive:
            package_names = {f"{root}{name}" for name in expected}
            trusted_tests = {
                f"{PROJECT_NAME}-{version}/{path.relative_to(source_root).as_posix()}": path.read_bytes()
                for path in sorted((source_root / "tests").glob("test_*.py"))
            }
            egg_root = f"{PROJECT_NAME}-{version}/{PROJECT_NAME}.egg-info"
            generated_names = {
                f"{egg_root}/PKG-INFO",
                f"{egg_root}/SOURCES.txt",
                f"{egg_root}/dependency_links.txt",
                f"{egg_root}/requires.txt",
                f"{egg_root}/top_level.txt",
            }
            source_root_name = f"{PROJECT_NAME}-{version}"
            fixed_names = {
                f"{source_root_name}/LICENSE",
                f"{source_root_name}/PKG-INFO",
                f"{source_root_name}/README.md",
                f"{source_root_name}/pyproject.toml",
                f"{source_root_name}/setup.cfg",
                f"{source_root_name}/docs/source/changelog.md",
            }
            expected_names = package_names | generated_names | fixed_names | set(trusted_tests)
            expected_directories = {
                source_root_name,
                f"{source_root_name}/{PROJECT_NAME}",
                egg_root,
                f"{source_root_name}/docs",
                f"{source_root_name}/docs/source",
            }
            if trusted_tests:
                expected_directories.add(f"{source_root_name}/tests")
            members = archive.getmembers()
            actual_names = {member.name for member in members if member.isfile()}
            actual_directories = {member.name for member in members if member.isdir()}
            if actual_names != expected_names or actual_directories != expected_directories:
                raise AiopnsenseDistributionError(
                    "Source distribution contains unexpected or missing installer payload."
                )
            if any(
                not (member.isfile() or member.isdir())
                or member.issym()
                or member.islnk()
                or (member.isfile() and member.mode & stat.S_IXUSR)
                for member in members
            ):
                raise AiopnsenseDistributionError("Source distribution contains an unsafe member.")
            contents = {}
            for member in members:
                if member.isfile():
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise AiopnsenseDistributionError(
                            "Could not read aiopnsense sdist payload."
                        )
                    contents[member.name] = extracted.read()
            sdist = {
                name.removeprefix(root): data
                for name, data in contents.items()
                if name.startswith(root)
            }
            for name in ("LICENSE", "README.md", "pyproject.toml"):
                if contents[f"{source_root_name}/{name}"] != (source_root / name).read_bytes():
                    raise AiopnsenseDistributionError(
                        f"Source distribution {name} differs from trusted source."
                    )
            if (
                contents[f"{source_root_name}/docs/source/changelog.md"]
                != (source_root / "docs/source/changelog.md").read_bytes()
            ):
                raise AiopnsenseDistributionError(
                    "Source distribution changelog differs from trusted source."
                )
            if contents[f"{source_root_name}/setup.cfg"] != (
                b"[egg_info]\ntag_build = \ntag_date = 0\n\n"
            ):
                raise AiopnsenseDistributionError(
                    "Source distribution setup configuration is invalid."
                )
            if any(contents[name] != data for name, data in trusted_tests.items()):
                raise AiopnsenseDistributionError(
                    "Source distribution tests differ from trusted source."
                )
    except (OSError, tarfile.TarError) as error:
        raise AiopnsenseDistributionError("Could not read aiopnsense sdist payload.") from error
    if wheel != expected or sdist != expected:
        raise AiopnsenseDistributionError(
            "Distribution package payload does not match the reconstructed candidate."
        )
    _verify_generated_sdist_metadata(contents, source_root, version)


def _verify_generated_sdist_metadata(
    contents: dict[str, bytes], source_root: Path, version: str
) -> None:
    """Require exact setuptools generated installer files and source manifest."""
    project = tomllib.loads((source_root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    root = f"{PROJECT_NAME}-{version}"
    egg_root = f"{root}/{PROJECT_NAME}.egg-info"
    requirements = [_normalized_requirement(item) for item in project["dependencies"]]
    requires_text = "\n".join(requirements) + "\n"
    for extra, values in project.get("optional-dependencies", {}).items():
        requires_text += f"\n[{extra}]\n"
        requires_text += "\n".join(_normalized_requirement(item) for item in values) + "\n"
    if contents[f"{egg_root}/requires.txt"] != requires_text.encode():
        raise AiopnsenseDistributionError("Source distribution requirements are invalid.")
    if contents[f"{egg_root}/dependency_links.txt"] != b"\n":
        raise AiopnsenseDistributionError("Source distribution dependency links are invalid.")
    if contents[f"{egg_root}/top_level.txt"] != f"{PROJECT_NAME}\n".encode():
        raise AiopnsenseDistributionError("Source distribution top-level metadata is invalid.")
    if contents[f"{egg_root}/PKG-INFO"] != contents[f"{root}/PKG-INFO"]:
        raise AiopnsenseDistributionError("Source distribution metadata copies differ.")
    source_names = ["LICENSE", "README.md", "pyproject.toml"]
    source_names.extend(f"{PROJECT_NAME}/{name}" for name in _source_payload(source_root))
    source_names.extend(
        f"{PROJECT_NAME}.egg-info/{name}"
        for name in (
            "PKG-INFO",
            "SOURCES.txt",
            "dependency_links.txt",
            "requires.txt",
            "top_level.txt",
        )
    )
    source_names.append("docs/source/changelog.md")
    source_names.extend(
        path.relative_to(source_root).as_posix()
        for path in sorted((source_root / "tests").glob("test_*.py"))
    )
    if contents[f"{egg_root}/SOURCES.txt"] != "\n".join(source_names).encode():
        raise AiopnsenseDistributionError(
            "Source distribution manifest differs from trusted source."
        )


def _normalized_requirement(requirement: str) -> str:
    """Normalize the simple project requirement grammar used by this repository."""
    dependency, separator, marker = requirement.partition(";")
    match = re.fullmatch(r"([A-Za-z0-9_.-]+)(.*)", dependency.strip())
    if match is None:
        raise AiopnsenseDistributionError("Project contains an unsupported requirement.")
    name, specifiers = match.groups()
    normalized = name.lower().replace("_", "-")
    if specifiers:
        normalized += ",".join(sorted(part.strip() for part in specifiers.split(",")))
    if separator:
        normalized += ";" + "".join(marker.split())
    return normalized


def verify_static_metadata(dist_dir: Path, source_root: Path, version: str) -> None:
    """Match distribution dependencies and long description to trusted source data."""
    try:
        project = tomllib.loads((source_root / "pyproject.toml").read_text(encoding="utf-8"))[
            "project"
        ]
        expected_requirements = list(project["dependencies"])
        for extra, requirements in project.get("optional-dependencies", {}).items():
            expected_requirements.extend(
                f'{requirement}; extra == "{extra}"' for requirement in requirements
            )
        expected_description = (
            (source_root / "README.md").read_text(encoding="utf-8")
            + "\n"
            + (source_root / "docs/source/changelog.md").read_text(encoding="utf-8")
        )
        wheel_path = dist_dir / f"{PROJECT_NAME}-{version}-py3-none-any.whl"
        with zipfile.ZipFile(wheel_path) as wheel:
            wheel_metadata = wheel.read(f"{PROJECT_NAME}-{version}.dist-info/METADATA")
        sdist_path = dist_dir / f"{PROJECT_NAME}-{version}.tar.gz"
        with tarfile.open(sdist_path, "r:gz") as sdist:
            member = sdist.extractfile(f"{PROJECT_NAME}-{version}/PKG-INFO")
            if member is None:
                raise AiopnsenseDistributionError("Source distribution metadata is missing.")
            sdist_metadata = member.read()
    except (
        KeyError,
        OSError,
        UnicodeDecodeError,
        ValueError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as error:
        raise AiopnsenseDistributionError("Could not derive trusted package metadata.") from error
    expected = {_normalized_requirement(item) for item in expected_requirements}
    for label, contents in (("Wheel", wheel_metadata), ("Source distribution", sdist_metadata)):
        message = BytesParser(policy=default).parsebytes(contents)
        actual = {_normalized_requirement(item) for item in message.get_all("Requires-Dist", [])}
        if (
            message.defects
            or message.get_all("Summary") != [project["description"]]
            or message.get_all("Description-Content-Type") != ["text/markdown"]
            or actual != expected
            or message.get_payload() != expected_description
        ):
            raise AiopnsenseDistributionError(
                f"{label} metadata does not match the reconstructed candidate."
            )


def verify_aiopnsense_distributions(
    release_tag: str, dist_dir: Path, source_root: Path | None = None
) -> None:
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
    if source_root is not None:
        verify_package_payloads(dist_dir, source_root, version)
        verify_static_metadata(dist_dir, source_root, version)


def main() -> int:
    """Verify aiopnsense distributions requested from GitHub Actions.

    Returns:
        Zero when verification succeeds, otherwise the argparse failure exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release_tag")
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    arguments = parser.parse_args()
    try:
        verify_aiopnsense_distributions(
            arguments.release_tag, arguments.dist_dir, arguments.source_root
        )
    except AiopnsenseDistributionError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
