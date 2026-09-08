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
README = b"README\n"
CHANGELOG = b"CHANGELOG\n"
LICENSE = b"test license\n"
PYPROJECT = b'[project]\nname = "aiopnsense"\ndescription = "Test package"\ndependencies = []\n'


def metadata(version: str = VERSION) -> bytes:
    """Build core metadata for the aiopnsense fixture.

    Args:
        version (str): Normalized PEP 440 version.

    Returns:
        bytes: Metadata bytes with the expected distribution name.
    """
    return (
        f"Metadata-Version: 2.4\nName: {PROJECT}\nVersion: {version}\n"
        "Summary: Test package\nRequires-Python: >=3.14\n"
        "Description-Content-Type: text/markdown\n\nREADME\n\nCHANGELOG\n"
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
        f"{dist_info}/licenses/LICENSE": LICENSE,
        f"{dist_info}/top_level.txt": b"aiopnsense\n",
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
        for path in (
            root,
            f"{root}/{PROJECT}",
            f"{root}/{PROJECT}.egg-info",
            f"{root}/docs",
            f"{root}/docs/source",
        ):
            directory_info = tarfile.TarInfo(path)
            directory_info.type = tarfile.DIRTYPE
            archive.addfile(directory_info)
        sources = [
            "LICENSE",
            "README.md",
            "pyproject.toml",
            f"{PROJECT}/__init__.py",
        ]
        if include_sdist_const:
            sources.append(f"{PROJECT}/const.py")
        sources.extend(
            [
                f"{PROJECT}.egg-info/PKG-INFO",
                f"{PROJECT}.egg-info/SOURCES.txt",
                f"{PROJECT}.egg-info/dependency_links.txt",
                f"{PROJECT}.egg-info/requires.txt",
                f"{PROJECT}.egg-info/top_level.txt",
                "docs/source/changelog.md",
            ]
        )
        source_members = {
            f"{root}/PKG-INFO": metadata(version),
            f"{root}/LICENSE": LICENSE,
            f"{root}/README.md": README,
            f"{root}/pyproject.toml": PYPROJECT,
            f"{root}/setup.cfg": b"[egg_info]\ntag_build = \ntag_date = 0\n\n",
            f"{root}/{PROJECT}/__init__.py": b"",
            f"{root}/{PROJECT}.egg-info/PKG-INFO": metadata(version),
            f"{root}/{PROJECT}.egg-info/SOURCES.txt": "\n".join(sources).encode(),
            f"{root}/{PROJECT}.egg-info/dependency_links.txt": b"\n",
            f"{root}/{PROJECT}.egg-info/requires.txt": b"\n",
            f"{root}/{PROJECT}.egg-info/top_level.txt": b"aiopnsense\n",
            f"{root}/docs/source/changelog.md": CHANGELOG,
        }
        if include_sdist_const:
            source_members[f"{root}/{PROJECT}/const.py"] = (
                f'VERSION = "{declared_sdist_tag}"\n'.encode()
            )
        for path, contents in source_members.items():
            info = tarfile.TarInfo(path)
            info.size = len(contents)
            archive.addfile(info, BytesIO(contents))


def write_source_root(source_root: Path, tag: str = TAG) -> None:
    """Write the trusted source inputs used for exact artifact comparison.

    Args:
        source_root (Path): Destination source tree.
        tag (str): Literal package release tag.
    """
    package = source_root / PROJECT
    package.mkdir(parents=True)
    (package / "__init__.py").write_bytes(b"")
    (package / "const.py").write_text(f'VERSION = "{tag}"\n', encoding="utf-8")
    (source_root / "README.md").write_bytes(README)
    (source_root / "LICENSE").write_bytes(LICENSE)
    changelog = source_root / "docs" / "source" / "changelog.md"
    changelog.parent.mkdir(parents=True)
    changelog.write_bytes(CHANGELOG)
    (source_root / "pyproject.toml").write_bytes(PYPROJECT)


def rewrite_sdist(path: Path, name: str, contents: bytes) -> None:
    """Replace or inject one regular sdist member while retaining valid tar structure.

    Args:
        path (Path): Source distribution archive.
        name (str): Archive member to replace or inject.
        contents (bytes): Replacement member bytes.
    """
    with tarfile.open(path, "r:gz") as archive:
        members = []
        for member in archive.getmembers():
            data = archive.extractfile(member).read() if member.isfile() else None
            members.append((member, data))
    found = False
    for member, _data in members:
        if member.name == name:
            member.size = len(contents)
            found = True
    if not found:
        member = tarfile.TarInfo(name)
        member.size = len(contents)
        members.append((member, contents))
    with tarfile.open(path, "w:gz") as archive:
        for member, data in members:
            if member.name == name:
                data = contents
            archive.addfile(member, BytesIO(data) if data is not None else None)


def add_wheel_member(path: Path, name: str, contents: bytes) -> None:
    """Inject a RECORD-covered wheel member to isolate provenance enforcement.

    Args:
        path (Path): Wheel archive.
        name (str): Archive member to inject.
        contents (bytes): Injected member bytes.
    """
    with zipfile.ZipFile(path) as archive:
        members = {
            member: archive.read(member)
            for member in archive.namelist()
            if not member.endswith("/RECORD")
        }
        record_path = next(member for member in archive.namelist() if member.endswith("/RECORD"))
    members[name] = contents
    rows = [record_entry(member, data) for member, data in members.items()]
    rows.append([record_path, "", ""])
    record = StringIO()
    csv.writer(record, lineterminator="\n").writerows(rows)
    with zipfile.ZipFile(path, "w") as archive:
        for member, data in members.items():
            archive.writestr(member, data)
        archive.writestr(record_path, record.getvalue())


@pytest.mark.parametrize(
    ("tag", "version"),
    [
        ("v1.2.3", "1.2.3"),
        ("v1.2.3-beta.4", "1.2.3b4"),
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


def test_source_root_proof_rejects_payload_drift_after_generic_validation(tmp_path: Path) -> None:
    """Trusted static verification rejects a package tree that differs from either archive.

    Args:
        tmp_path (Path): Temporary test directory.
    """
    dist_dir = tmp_path / "dist"
    write_distributions(dist_dir)
    source_root = tmp_path / "candidate"
    write_source_root(source_root)

    verify.verify_aiopnsense_distributions(TAG, dist_dir, source_root)

    (source_root / PROJECT / "const.py").write_text('VERSION = "v9.9.9"\n', encoding="utf-8")
    with pytest.raises(verify.AiopnsenseDistributionError, match="payload"):
        verify.verify_aiopnsense_distributions(TAG, dist_dir, source_root)


@pytest.mark.parametrize("member", ["unexpected.pth", "aiopnsense-1.2.3.data/scripts/tool"])
def test_source_root_proof_rejects_record_covered_wheel_installer_payload(
    tmp_path: Path, member: str
) -> None:
    """Reject undeclared wheel import hooks and installed scripts despite a valid RECORD.

    Args:
        tmp_path (Path): Temporary test directory.
        member (str): Undeclared installer path to add.
    """
    dist_dir = tmp_path / "dist"
    write_distributions(dist_dir)
    source_root = tmp_path / "candidate"
    write_source_root(source_root)
    add_wheel_member(dist_dir / f"{PROJECT}-{VERSION}-py3-none-any.whl", member, b"payload\n")

    with pytest.raises(verify.AiopnsenseDistributionError, match="installer payload"):
        verify.verify_aiopnsense_distributions(TAG, dist_dir, source_root)


@pytest.mark.parametrize(
    ("member", "contents"),
    [
        (f"{PROJECT}-{VERSION}/setup.py", b"raise SystemExit\n"),
        (
            f"{PROJECT}-{VERSION}/pyproject.toml",
            b'[build-system]\nrequires=["attacker"]\nbuild-backend="attacker.build"\n',
        ),
    ],
)
def test_source_root_proof_rejects_sdist_build_input_substitution(
    tmp_path: Path, member: str, contents: bytes
) -> None:
    """Reject executable or altered sdist build inputs with otherwise valid metadata.

    Args:
        tmp_path (Path): Temporary test directory.
        member (str): Sdist build-input member to replace or add.
        contents (bytes): Malicious replacement bytes.
    """
    dist_dir = tmp_path / "dist"
    write_distributions(dist_dir)
    source_root = tmp_path / "candidate"
    write_source_root(source_root)
    rewrite_sdist(dist_dir / f"{PROJECT}-{VERSION}.tar.gz", member, contents)

    with pytest.raises(
        verify.AiopnsenseDistributionError, match="installer payload|trusted source"
    ):
        verify.verify_aiopnsense_distributions(TAG, dist_dir, source_root)


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
        "v1.2.3-foo.1",
    ],
)
def test_normalized_version_rejects_invalid_component_shapes(tag: str) -> None:
    """Reject leading-zero and unsupported release component shapes.

    Args:
        tag (str): Invalid repository release tag.
    """
    with pytest.raises(verify.AiopnsenseDistributionError, match="Unsupported"):
        verify.normalized_version(tag)
