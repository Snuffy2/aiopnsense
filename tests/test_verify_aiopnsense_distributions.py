"""Tests for aiopnsense source-version distribution verification."""

import base64
import csv
import hashlib
import importlib.util
from io import BytesIO, StringIO
from pathlib import Path
import tarfile
import zipfile

import pytest

SCRIPT_PATH = (
    Path(__file__).parents[1] / ".github" / "scripts" / "verify_aiopnsense_distributions.py"
)
SCRIPT_SPEC = importlib.util.spec_from_file_location("verify_aiopnsense_distributions", SCRIPT_PATH)
assert SCRIPT_SPEC is not None
assert SCRIPT_SPEC.loader is not None
verify = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(verify)

TAG = "v1.2.3"
VERSION = "1.2.3"
PROJECT = "aiopnsense"


def metadata(version: str = VERSION) -> bytes:
    """Build core metadata for the aiopnsense fixture.

    Args:
        version (str): Normalized PEP 440 version.

    Returns:
        bytes: Metadata bytes with the expected distribution name.
    """
    return (
        f"Metadata-Version: 2.4\nName: {PROJECT}\nVersion: {version}\nRequires-Python: >=3.14\n\n"
    ).encode()


def record_entry(path: str, contents: bytes) -> list[str]:
    """Return a SHA-256 wheel RECORD row.

    Args:
        path (str): Wheel member path.
        contents (bytes): Member bytes to hash.

    Returns:
        list[str]: One valid three-column RECORD row.
    """
    digest = base64.urlsafe_b64encode(hashlib.sha256(contents).digest()).rstrip(b"=").decode()
    return [path, f"sha256={digest}", str(len(contents))]


def write_distributions(
    directory: Path,
    *,
    tag: str = TAG,
    wheel_const_tag: str | None = None,
    sdist_const_tag: str | None = None,
    include_wheel_const: bool = True,
    include_sdist_const: bool = True,
) -> None:
    """Create a matching distribution pair with an embedded source version.

    Args:
        directory (Path): Destination distribution directory.
        tag (str): Repository release tag normalized for archive and metadata filenames.
        wheel_const_tag (str | None): Optional replacement wheel source version written to const.py.
        sdist_const_tag (str | None): Optional replacement sdist source version written to const.py.
        include_wheel_const (bool): Whether to add the wheel source version file.
        include_sdist_const (bool): Whether to add the sdist source version file.
    """
    version = verify.normalized_version(tag)
    declared_wheel_tag = tag if wheel_const_tag is None else wheel_const_tag
    declared_sdist_tag = tag if sdist_const_tag is None else sdist_const_tag
    directory.mkdir()
    dist_info = f"{PROJECT}-{version}.dist-info"
    wheel_members = {
        f"{PROJECT}/__init__.py": b"",
        f"{dist_info}/METADATA": metadata(version),
        f"{dist_info}/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n",
    }
    if include_wheel_const:
        wheel_members[f"{PROJECT}/const.py"] = f'VERSION = "{declared_wheel_tag}"\n'.encode()
    record_path = f"{dist_info}/RECORD"
    rows = [record_entry(path, contents) for path, contents in wheel_members.items()]
    rows.append([record_path, "", ""])
    record = StringIO()
    csv.writer(record, lineterminator="\n").writerows(rows)
    with zipfile.ZipFile(directory / f"{PROJECT}-{version}-py3-none-any.whl", "w") as archive:
        for path, contents in wheel_members.items():
            archive.writestr(path, contents)
        archive.writestr(record_path, record.getvalue())

    root = f"{PROJECT}-{version}"
    with tarfile.open(directory / f"{PROJECT}-{version}.tar.gz", "w:gz") as archive:
        source_members = {f"{root}/PKG-INFO": metadata(version)}
        if include_sdist_const:
            source_members[f"{root}/{PROJECT}/const.py"] = (
                f'VERSION = "{declared_sdist_tag}"\n'.encode()
            )
        for path, contents in source_members.items():
            info = tarfile.TarInfo(path)
            info.size = len(contents)
            archive.addfile(info, BytesIO(contents))


@pytest.mark.parametrize(
    ("tag", "version"),
    [
        ("v1.2", "1.2"),
        ("v1.2.3", "1.2.3"),
        ("v1.2.3.4", "1.2.3.4"),
        ("v1.2.3-beta.4", "1.2.3b4"),
        ("v1.2.3rc4", "1.2.3rc4"),
    ],
)
def test_verify_aiopnsense_distributions_accepts_supported_tags(
    tmp_path: Path, tag: str, version: str
) -> None:
    """Verify generic integrity and the literal aiopnsense source version.

    Args:
        tmp_path (Path): Temporary fixture root.
        tag (str): Repository release tag.
        version (str): Expected normalized PEP 440 version.
    """
    dist_dir = tmp_path / "dist"
    write_distributions(dist_dir, tag=tag)

    assert verify.normalized_version(tag) == version
    verify.verify_aiopnsense_distributions(tag, dist_dir)


@pytest.mark.parametrize("member", ["wheel", "sdist"])
def test_verify_aiopnsense_distributions_rejects_mismatched_const_version(
    tmp_path: Path, member: str
) -> None:
    """Reject a generic-valid pair whose wheel or sdist source version differs.

    Args:
        tmp_path (Path): Temporary fixture root.
        member (str): Distribution form with the mismatched source declaration.
    """
    dist_dir = tmp_path / "dist"
    write_distributions(dist_dir, **{f"{member}_const_tag": "v9.9.9"})

    with pytest.raises(verify.AiopnsenseDistributionError, match="const.py"):
        verify.verify_aiopnsense_distributions(TAG, dist_dir)


@pytest.mark.parametrize("member", ["wheel", "sdist"])
def test_verify_aiopnsense_distributions_rejects_missing_const_version(
    tmp_path: Path, member: str
) -> None:
    """Reject a generic-valid pair missing const.py from either distribution form.

    Args:
        tmp_path (Path): Temporary fixture root.
        member (str): Distribution form missing the source declaration.
    """
    dist_dir = tmp_path / "dist"
    write_distributions(dist_dir, **{f"include_{member}_const": False})

    with pytest.raises(verify.AiopnsenseDistributionError, match="const.py"):
        verify.verify_aiopnsense_distributions(TAG, dist_dir)


@pytest.mark.parametrize(
    "tag",
    [
        "v01.2",
        "v1.02",
        "v1.2.03",
        "v1.2.3.04",
        "v01.2-beta.1",
        "v1.02-beta.1",
        "v1.2.03-beta.1",
        "v1.2.3.04-beta.1",
        "v1",
        "v1.2.3.4.5",
    ],
)
def test_normalized_version_rejects_invalid_component_shapes(tag: str) -> None:
    """Reject leading-zero and unsupported release component shapes.

    Args:
        tag (str): Invalid repository release tag.
    """
    with pytest.raises(verify.AiopnsenseDistributionError, match="Unsupported"):
        verify.normalized_version(tag)
