"""Verify generic Python wheel and source-distribution integrity."""

from __future__ import annotations

import base64
import csv
from email.parser import BytesParser
from email.policy import default
import hashlib
from io import StringIO
from pathlib import Path, PurePosixPath
import stat
import tarfile
import zipfile

MAX_ARCHIVE_MEMBERS = 64
MAX_DISTRIBUTION_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_CONTENT_BYTES = 100 * 1024 * 1024


class DistributionVerificationError(RuntimeError):
    """Raised when distributions cannot prove their declared identity."""


def _require_safe_path(name: str, source: str) -> None:
    """Reject an unsafe archive member path.

    Args:
        name: Archive-member path.
        source: Archive label used in the error message.

    Raises:
        DistributionVerificationError: If the path is empty, absolute, or traverses upward.
    """
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts:
        raise DistributionVerificationError(f"{source} contains an unsafe path: {name!r}.")


def _require_bounded_sizes(sizes: list[int], source: str) -> None:
    """Reject archives whose declared regular-file contents exceed fixed bounds.

    Args:
        sizes: Declared uncompressed regular-file sizes.
        source: Archive label used in the error message.

    Raises:
        DistributionVerificationError: If member count or size limits are exceeded.
    """
    if len(sizes) > MAX_ARCHIVE_MEMBERS:
        raise DistributionVerificationError(f"{source} contains too many members.")
    if any(size < 0 or size > MAX_ARCHIVE_MEMBER_BYTES for size in sizes):
        raise DistributionVerificationError(f"{source} contains an oversized member.")
    if sum(sizes) > MAX_ARCHIVE_CONTENT_BYTES:
        raise DistributionVerificationError(f"{source} exceeds its expanded-size limit.")


def _require_metadata(
    metadata: bytes,
    metadata_name: str,
    version: str,
    requires_python: str,
    source: str,
) -> None:
    """Require exact normalized name, version, and Python requirement metadata.

    Args:
        metadata: Raw core metadata bytes.
        metadata_name: Expected distribution metadata name.
        version: Expected normalized PEP 440 version.
        requires_python: Expected Requires-Python core metadata value.
        source: Member label used in an error message.

    Raises:
        DistributionVerificationError: If metadata is malformed or mismatched.
    """
    message = BytesParser(policy=default).parsebytes(metadata)
    if (
        message.defects
        or message.get_all("Name") != [metadata_name]
        or message.get_all("Version") != [version]
        or message.get_all("Requires-Python") != [requires_python]
    ):
        raise DistributionVerificationError(
            f"{source} does not identify {metadata_name} {version}."
        )


def _verify_record(archive: zipfile.ZipFile, names: list[str], record_path: str) -> None:
    """Require a SHA-256 RECORD entry for every wheel member.

    Args:
        archive: Open wheel archive.
        names: Unique member paths in archive order.
        record_path: Wheel RECORD member path.

    Raises:
        DistributionVerificationError: If RECORD is malformed or does not verify bytes.
    """
    try:
        rows = list(csv.reader(StringIO(archive.read(record_path).decode("utf-8"))))
    except (UnicodeDecodeError, csv.Error) as error:
        raise DistributionVerificationError(f"Wheel RECORD is malformed: {error}") from error
    records = {row[0]: row[1:] for row in rows if len(row) == 3}
    if len(records) != len(rows) or set(records) != set(names):
        raise DistributionVerificationError(
            "Wheel RECORD does not cover every member exactly once."
        )
    for name in names:
        digest, size = records[name]
        if name == record_path:
            if digest or size:
                raise DistributionVerificationError("Wheel RECORD self-entry must be empty.")
            continue
        contents = archive.read(name)
        expected = base64.urlsafe_b64encode(hashlib.sha256(contents).digest()).rstrip(b"=").decode()
        if digest != f"sha256={expected}" or size != str(len(contents)):
            raise DistributionVerificationError(f"Wheel RECORD does not verify {name!r}.")


def _verify_wheel(
    path: Path,
    distribution_stem: str,
    metadata_name: str,
    version: str,
    requires_python: str,
) -> None:
    """Verify wheel paths, metadata, compatibility declaration, and RECORD.

    Args:
        path: Wheel archive path.
        distribution_stem: Normalized distribution filename stem.
        metadata_name: Expected core metadata name.
        version: Expected normalized PEP 440 version.
        requires_python: Expected Requires-Python core metadata value.

    Raises:
        DistributionVerificationError: If the wheel cannot prove its identity.
    """
    dist_info = f"{distribution_stem}-{version}.dist-info"
    metadata_path = f"{dist_info}/METADATA"
    wheel_path = f"{dist_info}/WHEEL"
    record_path = f"{dist_info}/RECORD"
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            names = [member.filename for member in members]
            if len(names) != len(set(names)):
                raise DistributionVerificationError("Wheel contains duplicate member paths.")
            _require_bounded_sizes([member.file_size for member in members], "Wheel")
            for member in members:
                _require_safe_path(member.filename, "Wheel")
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise DistributionVerificationError("Wheel contains a symbolic link.")
            if {metadata_path, wheel_path, record_path} - set(names):
                raise DistributionVerificationError("Wheel lacks required dist-info members.")
            _require_metadata(
                archive.read(metadata_path),
                metadata_name,
                version,
                requires_python,
                metadata_path,
            )
            wheel = BytesParser(policy=default).parsebytes(archive.read(wheel_path))
            if (
                wheel.defects
                or wheel.get_all("Wheel-Version") != ["1.0"]
                or wheel.get_all("Root-Is-Purelib") != ["true"]
                or wheel.get_all("Tag") != ["py3-none-any"]
                or wheel.get_payload(decode=True) not in (b"", None)
            ):
                raise DistributionVerificationError(
                    "Wheel has an unsupported compatibility declaration."
                )
            _verify_record(archive, names, record_path)
    except (OSError, zipfile.BadZipFile) as error:
        raise DistributionVerificationError(f"Could not read wheel {path}: {error}") from error


def _verify_sdist(
    path: Path,
    distribution_stem: str,
    metadata_name: str,
    version: str,
    requires_python: str,
) -> None:
    """Verify source-distribution paths, types, bounds, and root metadata.

    Args:
        path: Source distribution archive path.
        distribution_stem: Normalized distribution filename stem.
        metadata_name: Expected core metadata name.
        version: Expected normalized PEP 440 version.
        requires_python: Expected Requires-Python core metadata value.

    Raises:
        DistributionVerificationError: If the source distribution cannot prove its identity.
    """
    root = f"{distribution_stem}-{version}"
    metadata_path = f"{root}/PKG-INFO"
    try:
        member_names: set[str] = set()
        member_count = 0
        content_size = 0
        has_metadata = False
        with tarfile.open(path, "r|gz") as archive:
            for member in archive:
                member_count += 1
                if member_count > MAX_ARCHIVE_MEMBERS:
                    raise DistributionVerificationError(
                        "Source distribution contains too many members."
                    )
                if member.name in member_names:
                    raise DistributionVerificationError(
                        "Source distribution contains duplicate member paths."
                    )
                member_names.add(member.name)
                _require_safe_path(member.name, "Source distribution")
                if not (member.isdir() or member.isfile()) or member.issym() or member.islnk():
                    raise DistributionVerificationError(
                        "Source distribution contains an unsafe member type."
                    )
                if member.name != root and not member.name.startswith(f"{root}/"):
                    raise DistributionVerificationError(
                        "Source distribution member is outside its root."
                    )
                if member.isfile():
                    if member.size < 0 or member.size > MAX_ARCHIVE_MEMBER_BYTES:
                        raise DistributionVerificationError(
                            "Source distribution contains an oversized member."
                        )
                    content_size += member.size
                    if content_size > MAX_ARCHIVE_CONTENT_BYTES:
                        raise DistributionVerificationError(
                            "Source distribution exceeds its expanded-size limit."
                        )
                has_metadata |= member.name == metadata_path
        if not has_metadata:
            raise DistributionVerificationError("Source distribution lacks PKG-INFO.")
        with tarfile.open(path, "r|gz") as archive:
            for member in archive:
                if member.name != metadata_path:
                    continue
                metadata = archive.extractfile(member)
                if metadata is None:
                    raise DistributionVerificationError(
                        "Source distribution could not extract PKG-INFO."
                    )
                _require_metadata(
                    metadata.read(), metadata_name, version, requires_python, metadata_path
                )
                break
            else:
                raise DistributionVerificationError("Source distribution lacks PKG-INFO.")
    except (OSError, tarfile.TarError) as error:
        raise DistributionVerificationError(
            f"Could not read source distribution {path}: {error}"
        ) from error


def verify_python_distributions(
    dist_dir: Path,
    *,
    distribution_stem: str,
    metadata_name: str,
    version: str,
    requires_python: str,
) -> None:
    """Require exactly one generic wheel and source distribution for a package version.

    Args:
        dist_dir: Directory containing the generated distribution archives.
        distribution_stem: Normalized stem used in filenames and archive roots.
        metadata_name: Expected distribution metadata name.
        version: Normalized PEP 440 version without a repository tag prefix.
        requires_python: Exact Requires-Python value expected in both core metadata files.

    Raises:
        DistributionVerificationError: If the distribution set is incomplete or unsafe.
    """
    expected = {
        f"{distribution_stem}-{version}-py3-none-any.whl",
        f"{distribution_stem}-{version}.tar.gz",
    }
    if not dist_dir.is_dir() or dist_dir.is_symlink():
        raise DistributionVerificationError("Distribution directory must be a real directory.")
    paths = list(dist_dir.iterdir())
    if (
        {path.name for path in paths} != expected
        or len(paths) != len(expected)
        or any(
            path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_DISTRIBUTION_BYTES
            for path in paths
        )
    ):
        raise DistributionVerificationError(
            "Distribution filenames do not match the requested release."
        )
    _verify_wheel(
        dist_dir / f"{distribution_stem}-{version}-py3-none-any.whl",
        distribution_stem,
        metadata_name,
        version,
        requires_python,
    )
    _verify_sdist(
        dist_dir / f"{distribution_stem}-{version}.tar.gz",
        distribution_stem,
        metadata_name,
        version,
        requires_python,
    )
