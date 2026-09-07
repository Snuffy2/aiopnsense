"""Tests for generic Python distribution integrity checks."""

import base64
import csv
import hashlib
import importlib.util
from io import BytesIO, StringIO
from pathlib import Path
import tarfile
import zipfile

import pytest

SCRIPT_PATH = Path(__file__).parents[1] / ".github" / "scripts" / "verify_python_distributions.py"
SCRIPT_SPEC = importlib.util.spec_from_file_location("verify_python_distributions", SCRIPT_PATH)
assert SCRIPT_SPEC is not None
assert SCRIPT_SPEC.loader is not None
verify = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(verify)

STEM = "sample_package"
NAME = "sample-package"
VERSION = "1.2.3"
REQUIRES_PYTHON = ">=3.14"


def metadata(version: str = VERSION, requires_python: str = REQUIRES_PYTHON) -> bytes:
    """Create minimal valid distribution metadata.

    Args:
        version (str): PEP 440 version written into the metadata.
        requires_python (str): Requires-Python value written into the metadata.

    Returns:
        bytes: Core metadata bytes for the generic sample package.
    """
    return (
        f"Metadata-Version: 2.4\nName: {NAME}\nVersion: {version}\n"
        f"Requires-Python: {requires_python}\n\n"
    ).encode()


def record_entry(path: str, contents: bytes) -> list[str]:
    """Build one SHA-256 wheel RECORD row.

    Args:
        path (str): Wheel-relative member path.
        contents (bytes): Member bytes to hash.

    Returns:
        list[str]: A valid three-column RECORD row.
    """
    digest = base64.urlsafe_b64encode(hashlib.sha256(contents).digest()).rstrip(b"=").decode()
    return [path, f"sha256={digest}", str(len(contents))]


def write_distributions(
    directory: Path,
    *,
    version: str = VERSION,
    metadata_version: str | None = None,
    metadata_requires_python: str | None = None,
    wheel_metadata_version: str | None = None,
    sdist_metadata_version: str | None = None,
    wheel_requires_python: str | None = None,
    sdist_requires_python: str | None = None,
) -> None:
    """Write a minimal wheel and source distribution pair.

    Args:
        directory (Path): Destination directory for fixture distributions.
        version (str): Expected normalized release version.
        metadata_version (str | None): Optional replacement metadata version.
        metadata_requires_python (str | None): Optional replacement Requires-Python metadata value.
        wheel_metadata_version (str | None): Optional wheel-only replacement metadata version.
        sdist_metadata_version (str | None): Optional sdist-only replacement metadata version.
        wheel_requires_python (str | None): Optional wheel-only replacement Requires-Python value.
        sdist_requires_python (str | None): Optional sdist-only replacement Requires-Python value.
    """
    directory.mkdir()
    default_version = version if metadata_version is None else metadata_version
    default_requires_python = (
        REQUIRES_PYTHON if metadata_requires_python is None else metadata_requires_python
    )
    declared_wheel_version = (
        default_version if wheel_metadata_version is None else wheel_metadata_version
    )
    declared_sdist_version = (
        default_version if sdist_metadata_version is None else sdist_metadata_version
    )
    declared_wheel_requires_python = (
        default_requires_python if wheel_requires_python is None else wheel_requires_python
    )
    declared_sdist_requires_python = (
        default_requires_python if sdist_requires_python is None else sdist_requires_python
    )
    wheel_path = directory / f"{STEM}-{version}-py3-none-any.whl"
    dist_info = f"{STEM}-{version}.dist-info"
    wheel_members = {
        f"{STEM}/__init__.py": b"",
        f"{dist_info}/METADATA": metadata(declared_wheel_version, declared_wheel_requires_python),
        f"{dist_info}/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n",
    }
    record_path = f"{dist_info}/RECORD"
    rows = [record_entry(path, contents) for path, contents in wheel_members.items()]
    rows.append([record_path, "", ""])
    record = StringIO()
    csv.writer(record, lineterminator="\n").writerows(rows)
    with zipfile.ZipFile(wheel_path, "w") as archive:
        for path, contents in wheel_members.items():
            archive.writestr(path, contents)
        archive.writestr(record_path, record.getvalue())

    root = f"{STEM}-{version}"
    with tarfile.open(directory / f"{STEM}-{version}.tar.gz", "w:gz") as archive:
        info = tarfile.TarInfo(f"{root}/PKG-INFO")
        contents = metadata(declared_sdist_version, declared_sdist_requires_python)
        info.size = len(contents)
        archive.addfile(info, BytesIO(contents))


def verify_pair(directory: Path, version: str = VERSION) -> None:
    """Run the core verifier for the generic fixture package.

    Args:
        directory (Path): Fixture distribution directory.
        version (str): Expected normalized release version.
    """
    verify.verify_python_distributions(
        directory,
        distribution_stem=STEM,
        metadata_name=NAME,
        version=version,
        requires_python=REQUIRES_PYTHON,
    )


def rebuild_wheel(path: Path, members: dict[str, bytes]) -> None:
    """Replace a fixture wheel with the requested members.

    Args:
        path (Path): Wheel archive to replace.
        members (dict[str, bytes]): Wheel-member contents keyed by member path.
    """
    rebuilt = path.with_name("rebuilt.whl")
    with zipfile.ZipFile(rebuilt, "w") as archive:
        for member, contents in members.items():
            archive.writestr(member, contents)
    rebuilt.replace(path)


@pytest.mark.parametrize("version", ["1.2", "1.2.3", "1.2.3.4"])
def test_verify_python_distributions_accepts_fixed_component_versions(
    tmp_path: Path, version: str
) -> None:
    """Accept a matching pure wheel and sdist for each shared stable version shape.

    Args:
        tmp_path (Path): Temporary fixture root.
        version (str): Stable PEP 440 version to package.
    """
    dist_dir = tmp_path / "dist"
    write_distributions(dist_dir, version=version)

    verify_pair(dist_dir, version)


def test_verify_python_distributions_rejects_extra_or_missing_paths(tmp_path: Path) -> None:
    """Reject a distribution directory that is not the exact expected pair.

    Args:
        tmp_path (Path): Temporary fixture root.
    """
    dist_dir = tmp_path / "dist"
    write_distributions(dist_dir)
    (dist_dir / "unexpected.txt").write_text("unexpected", encoding="utf-8")

    with pytest.raises(verify.DistributionVerificationError, match="filenames"):
        verify_pair(dist_dir)


def test_verify_python_distributions_rejects_non_regular_distribution_path(tmp_path: Path) -> None:
    """Reject a directory in place of a required wheel file.

    Args:
        tmp_path (Path): Temporary fixture root.
    """
    dist_dir = tmp_path / "dist"
    write_distributions(dist_dir)
    wheel = dist_dir / f"{STEM}-{VERSION}-py3-none-any.whl"
    wheel.unlink()
    wheel.mkdir()

    with pytest.raises(verify.DistributionVerificationError, match="filenames"):
        verify_pair(dist_dir)


@pytest.mark.parametrize("target", ["wheel", "sdist"])
def test_verify_python_distributions_rejects_mismatched_metadata(
    tmp_path: Path, target: str
) -> None:
    """Reject a version mismatch from either distribution form.

    Args:
        tmp_path (Path): Temporary fixture root.
        target (str): Distribution form whose metadata is altered.
    """
    dist_dir = tmp_path / "dist"
    write_distributions(dist_dir, **{f"{target}_metadata_version": "9.9.9"})

    with pytest.raises(verify.DistributionVerificationError, match="does not identify"):
        verify_pair(dist_dir)


@pytest.mark.parametrize("target", ["wheel", "sdist"])
def test_verify_python_distributions_rejects_mismatched_requires_python(
    tmp_path: Path, target: str
) -> None:
    """Reject a Python-floor mismatch from either distribution form.

    Args:
        tmp_path (Path): Temporary fixture root.
        target (str): Distribution form whose Python requirement is altered.
    """
    dist_dir = tmp_path / "dist"
    write_distributions(dist_dir, **{f"{target}_requires_python": ">=3.13"})

    with pytest.raises(verify.DistributionVerificationError, match="does not identify"):
        verify_pair(dist_dir)


def test_verify_python_distributions_rejects_duplicate_or_unsafe_wheel_members(
    tmp_path: Path,
) -> None:
    """Reject duplicate wheel members and traversal paths before RECORD verification.

    Args:
        tmp_path (Path): Temporary fixture root.
    """
    dist_dir = tmp_path / "dist"
    write_distributions(dist_dir)
    wheel = dist_dir / f"{STEM}-{VERSION}-py3-none-any.whl"
    with pytest.warns(UserWarning, match="Duplicate name"), zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr(f"{STEM}/__init__.py", b"duplicate")

    with pytest.raises(verify.DistributionVerificationError, match="duplicate"):
        verify_pair(dist_dir)

    write_distributions(dist_dir := tmp_path / "unsafe")
    wheel = dist_dir / f"{STEM}-{VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.writestr("../outside.py", b"unsafe")

    with pytest.raises(verify.DistributionVerificationError, match="unsafe path"):
        verify_pair(dist_dir)


def test_verify_python_distributions_rejects_duplicate_sdist_members(tmp_path: Path) -> None:
    """Reject duplicate sdist member paths while its wheel remains valid.

    Args:
        tmp_path (Path): Temporary fixture root.
    """
    dist_dir = tmp_path / "dist"
    write_distributions(dist_dir)
    sdist = dist_dir / f"{STEM}-{VERSION}.tar.gz"
    sdist.unlink()
    with tarfile.open(sdist, "w:gz") as archive:
        contents = metadata()
        for _ in range(2):
            package_info = tarfile.TarInfo(f"{STEM}-{VERSION}/PKG-INFO")
            package_info.size = len(contents)
            archive.addfile(package_info, BytesIO(contents))

    with pytest.raises(verify.DistributionVerificationError, match="duplicate"):
        verify_pair(dist_dir)


def test_verify_python_distributions_rejects_missing_or_incompatible_wheel_metadata(
    tmp_path: Path,
) -> None:
    """Reject missing WHEEL and a wheel with unsupported compatibility metadata.

    Args:
        tmp_path (Path): Temporary fixture root.
    """
    dist_dir = tmp_path / "missing"
    write_distributions(dist_dir)
    wheel = dist_dir / f"{STEM}-{VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel) as archive:
        members = {
            member.filename: archive.read(member.filename)
            for member in archive.infolist()
            if not member.filename.endswith("/WHEEL")
        }
    rebuild_wheel(wheel, members)

    with pytest.raises(verify.DistributionVerificationError, match="required dist-info"):
        verify_pair(dist_dir)

    write_distributions(dist_dir := tmp_path / "compatibility")
    wheel = dist_dir / f"{STEM}-{VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(wheel) as archive:
        members = {member.filename: archive.read(member.filename) for member in archive.infolist()}
    members[f"{STEM}-{VERSION}.dist-info/WHEEL"] = (
        b"Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: py3-none-any\n\n"
    )
    rebuild_wheel(wheel, members)

    with pytest.raises(verify.DistributionVerificationError, match="compatibility"):
        verify_pair(dist_dir)


def test_verify_python_distributions_rejects_missing_sdist_metadata(tmp_path: Path) -> None:
    """Reject an sdist without PKG-INFO while its wheel remains valid.

    Args:
        tmp_path (Path): Temporary fixture root.
    """
    dist_dir = tmp_path / "dist"
    write_distributions(dist_dir)
    sdist = dist_dir / f"{STEM}-{VERSION}.tar.gz"
    sdist.unlink()
    with tarfile.open(sdist, "w:gz"):
        pass

    with pytest.raises(verify.DistributionVerificationError, match="PKG-INFO"):
        verify_pair(dist_dir)


def test_verify_python_distributions_bounds_all_sdist_headers(tmp_path: Path) -> None:
    """Reject an sdist with too many directory headers before metadata extraction.

    Args:
        tmp_path (Path): Temporary fixture root.
    """
    dist_dir = tmp_path / "dist"
    write_distributions(dist_dir)
    sdist = dist_dir / f"{STEM}-{VERSION}.tar.gz"
    sdist.unlink()
    root = f"{STEM}-{VERSION}"
    with tarfile.open(sdist, "w:gz") as archive:
        metadata_contents = metadata()
        package_info = tarfile.TarInfo(f"{root}/PKG-INFO")
        package_info.size = len(metadata_contents)
        archive.addfile(package_info, BytesIO(metadata_contents))
        for index in range(verify.MAX_ARCHIVE_MEMBERS):
            directory = tarfile.TarInfo(f"{root}/directory-{index}")
            directory.type = tarfile.DIRTYPE
            archive.addfile(directory)

    with pytest.raises(verify.DistributionVerificationError, match="too many members"):
        verify_pair(dist_dir)


def test_verify_python_distributions_rejects_unsafe_sdist_member_type(tmp_path: Path) -> None:
    """Reject a symbolic-link sdist member through the public verifier.

    Args:
        tmp_path (Path): Temporary fixture root.
    """
    dist_dir = tmp_path / "type"
    write_distributions(dist_dir)
    sdist = dist_dir / f"{STEM}-{VERSION}.tar.gz"
    sdist.unlink()
    with tarfile.open(sdist, "w:gz") as archive:
        metadata_contents = metadata()
        package_info = tarfile.TarInfo(f"{STEM}-{VERSION}/PKG-INFO")
        package_info.size = len(metadata_contents)
        archive.addfile(package_info, BytesIO(metadata_contents))
        link = tarfile.TarInfo(f"{STEM}-{VERSION}/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "target"
        archive.addfile(link)

    with pytest.raises(verify.DistributionVerificationError, match="unsafe member type"):
        verify_pair(dist_dir)


def test_verify_python_distributions_rejects_wheel_and_sdist_size_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise each archive verifier when its declared member sizes exceed the bound.

    Args:
        tmp_path (Path): Temporary fixture root.
        monkeypatch (pytest.MonkeyPatch): Fixture for reducing the member-size limit.
    """
    dist_dir = tmp_path / "wheel"
    write_distributions(dist_dir)
    monkeypatch.setattr(verify, "MAX_ARCHIVE_MEMBER_BYTES", 0)

    with pytest.raises(verify.DistributionVerificationError, match="oversized"):
        verify._verify_wheel(
            dist_dir / f"{STEM}-{VERSION}-py3-none-any.whl",
            STEM,
            NAME,
            VERSION,
            REQUIRES_PYTHON,
        )

    dist_dir = tmp_path / "sdist"
    write_distributions(dist_dir)

    with pytest.raises(verify.DistributionVerificationError, match="oversized"):
        verify._verify_sdist(
            dist_dir / f"{STEM}-{VERSION}.tar.gz",
            STEM,
            NAME,
            VERSION,
            REQUIRES_PYTHON,
        )


def test_verify_python_distributions_rejects_bad_wheel_record(tmp_path: Path) -> None:
    """Reject a wheel whose RECORD digest cannot verify the archived payload.

    Args:
        tmp_path (Path): Temporary fixture root.
    """
    dist_dir = tmp_path / "dist"
    write_distributions(dist_dir)
    wheel = dist_dir / f"{STEM}-{VERSION}-py3-none-any.whl"
    rebuilt = dist_dir / "rebuilt.whl"
    with zipfile.ZipFile(wheel) as source, zipfile.ZipFile(rebuilt, "w") as output:
        for member in source.infolist():
            contents = source.read(member.filename)
            if member.filename == f"{STEM}/__init__.py":
                contents = b"changed"
            output.writestr(member.filename, contents)
    rebuilt.replace(wheel)

    with pytest.raises(verify.DistributionVerificationError, match="RECORD"):
        verify_pair(dist_dir)


def test_verify_python_distributions_rejects_unsafe_sdist_member(tmp_path: Path) -> None:
    """Reject an sdist member that escapes the declared archive root.

    Args:
        tmp_path (Path): Temporary fixture root.
    """
    dist_dir = tmp_path / "dist"
    write_distributions(dist_dir)
    sdist = dist_dir / f"{STEM}-{VERSION}.tar.gz"
    sdist.unlink()
    with tarfile.open(sdist, "w:gz") as archive:
        root = f"{STEM}-{VERSION}"
        metadata_info = tarfile.TarInfo(f"{root}/PKG-INFO")
        metadata_contents = metadata()
        metadata_info.size = len(metadata_contents)
        archive.addfile(metadata_info, BytesIO(metadata_contents))
        info = tarfile.TarInfo("outside.txt")
        info.size = 1
        archive.addfile(info, BytesIO(b"x"))

    with pytest.raises(verify.DistributionVerificationError, match="outside"):
        verify_pair(dist_dir)


@pytest.mark.parametrize(
    ("path", "message"),
    [
        (f"{STEM}-{VERSION}-py3-none-any.whl", "Could not read wheel"),
        (f"{STEM}-{VERSION}.tar.gz", "Could not read source distribution"),
    ],
)
def test_verify_python_distributions_rejects_corrupt_archive(
    tmp_path: Path, path: str, message: str
) -> None:
    """Reject each distribution archive when it cannot be decoded.

    Args:
        tmp_path (Path): Temporary fixture root.
        path (str): Distribution filename replaced with invalid bytes.
        message (str): Expected verifier error text.
    """
    dist_dir = tmp_path / "dist"
    write_distributions(dist_dir)
    (dist_dir / path).write_bytes(b"not-an-archive")

    with pytest.raises(verify.DistributionVerificationError, match=message):
        verify_pair(dist_dir)
