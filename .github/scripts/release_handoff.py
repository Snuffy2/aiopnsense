"""Create and verify the bounded aiopnsense release-candidate handoff."""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import importlib.util
from io import BytesIO
import json
from pathlib import Path
import re
import sys
import tarfile
from typing import Any

MANIFEST_VERSION = 1
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
MAX_FILE_BYTES = {
    "candidate-const.py": 64 * 1024,
    "changelog.md": 2 * 1024 * 1024,
}
MAX_DISTRIBUTION_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 128
MAX_ARCHIVE_CONTENT_BYTES = 128 * 1024 * 1024


class HandoffError(RuntimeError):
    """Raised when release handoff data violates its fixed contract."""


def _digest(path: Path) -> str:
    """Return the SHA-256 digest of one regular file."""
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _normalized_version(release_tag: str) -> str:
    """Normalize a tag with the adjacent trusted repository policy."""
    policy_path = Path(__file__).with_name("release_version.py")
    spec = importlib.util.spec_from_file_location("release_version", policy_path)
    if spec is None or spec.loader is None:
        raise HandoffError("Could not load release-version policy.")
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    try:
        return policy.normalized_version(release_tag)
    except policy.ReleaseTagError as error:
        raise HandoffError(str(error)) from error


def _regular_file(path: Path, maximum: int) -> None:
    """Require a bounded regular file without following a symbolic link."""
    if path.is_symlink() or not path.is_file():
        raise HandoffError(f"Handoff member is not a regular file: {path.name}")
    if path.stat().st_size > maximum:
        raise HandoffError(f"Handoff member is too large: {path.name}")


def _identity_fields(values: argparse.Namespace) -> dict[str, Any]:
    """Return validated release identity fields from command arguments."""
    for name in ("source_sha", "candidate_sha", "target_sha", "tag_oid"):
        value = getattr(values, name)
        if not isinstance(value, str) or SHA_PATTERN.fullmatch(value) is None:
            raise HandoffError(f"Invalid {name.replace('_', ' ')}.")
    return {
        "release_tag": values.release_tag,
        "release_id": str(values.release_id),
        "prerelease": values.prerelease == "true",
        "source_sha": values.source_sha,
        "candidate_sha": values.candidate_sha,
        "target_sha": values.target_sha,
        "tag_oid": values.tag_oid,
    }


def canonicalize_sdist(path: Path, epoch: int) -> None:
    """Rewrite a bounded setuptools sdist with deterministic tar and gzip metadata."""
    _regular_file(path, MAX_DISTRIBUTION_BYTES)
    members: list[tuple[tarfile.TarInfo, bytes | None]] = []
    total = 0
    try:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive:
                if len(members) >= MAX_ARCHIVE_MEMBERS:
                    raise HandoffError("Source distribution contains too many members.")
                candidate = Path(member.name)
                if candidate.is_absolute() or ".." in candidate.parts:
                    raise HandoffError("Source distribution contains an unsafe path.")
                if not (member.isfile() or member.isdir()) or member.issym() or member.islnk():
                    raise HandoffError("Source distribution contains an unsafe member type.")
                contents = None
                if member.isfile():
                    total += member.size
                    if total > MAX_ARCHIVE_CONTENT_BYTES:
                        raise HandoffError("Source distribution is too large when expanded.")
                    source = archive.extractfile(member)
                    if source is None:
                        raise HandoffError("Could not read source distribution member.")
                    contents = source.read()
                members.append((member, contents))
    except (OSError, tarfile.TarError) as error:
        raise HandoffError("Could not read source distribution.") from error
    temporary = path.with_name(f".{path.name}.canonical")
    try:
        with (
            temporary.open("wb") as raw,
            gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as compressed,
            tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as output,
        ):
            for original, contents in sorted(members, key=lambda item: item[0].name):
                member = copy.copy(original)
                member.mtime = epoch
                member.uid = 0
                member.gid = 0
                member.uname = ""
                member.gname = ""
                member.pax_headers = {}
                if contents is None:
                    output.addfile(member)
                else:
                    output.addfile(member, BytesIO(contents))
        temporary.replace(path)
    except OSError as error:
        raise HandoffError("Could not write deterministic source distribution.") from error


def create(values: argparse.Namespace) -> None:
    """Write a canonical manifest for four explicitly named payload files."""
    root = values.handoff.resolve()
    version = _normalized_version(values.release_tag)
    names = (
        "candidate-const.py",
        "changelog.md",
        f"dist/aiopnsense-{version}-py3-none-any.whl",
        f"dist/aiopnsense-{version}.tar.gz",
    )
    files: dict[str, dict[str, Any]] = {}
    total = 0
    for name in names:
        path = root / name
        maximum = MAX_FILE_BYTES.get(name, MAX_DISTRIBUTION_BYTES)
        _regular_file(path, maximum)
        size = path.stat().st_size
        total += size
        files[name] = {"sha256": _digest(path), "size": size}
    if total > MAX_TOTAL_BYTES:
        raise HandoffError("Handoff payload is too large.")
    manifest = {"version": MANIFEST_VERSION, **_identity_fields(values), "files": files}
    (root / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _verified_payload(values: argparse.Namespace) -> dict[str, Any]:
    """Verify exact membership, canonical JSON, file types, sizes, and digests."""
    root = values.handoff.resolve()
    unresolved_root = values.handoff.absolute()
    if unresolved_root.is_symlink() or not unresolved_root.is_dir():
        raise HandoffError("Handoff root must be a real directory.")
    manifest_path = root / "manifest.json"
    _regular_file(manifest_path, 32 * 1024)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HandoffError("Invalid handoff manifest JSON.") from error
    if not isinstance(manifest, dict):
        raise HandoffError("Handoff manifest must be an object.")
    identity_names = {
        "release_tag",
        "release_id",
        "prerelease",
        "source_sha",
        "candidate_sha",
        "target_sha",
        "tag_oid",
    }
    expected_keys = {"version", *identity_names, "files"}
    if set(manifest) != expected_keys or manifest["version"] != MANIFEST_VERSION:
        raise HandoffError("Handoff manifest schema does not match.")
    files = manifest["files"]
    if not isinstance(files, dict) or not files:
        raise HandoffError("Handoff file manifest is invalid.")
    version = _normalized_version(values.release_tag)
    expected_files = {
        "candidate-const.py",
        "changelog.md",
        f"dist/aiopnsense-{version}-py3-none-any.whl",
        f"dist/aiopnsense-{version}.tar.gz",
    }
    if set(files) != expected_files:
        raise HandoffError("Handoff manifest names do not match the release tag.")
    expected_paths = {"manifest.json", "dist", *expected_files}
    actual_paths: set[str] = set()
    for path in root.rglob("*"):
        name = path.relative_to(root).as_posix()
        actual_paths.add(name)
        if path.is_symlink():
            raise HandoffError(f"Handoff contains a symbolic link: {name}")
        if name == "dist":
            if not path.is_dir():
                raise HandoffError("Handoff dist path is not a directory.")
        elif name not in expected_files | {"manifest.json"} or not path.is_file():
            raise HandoffError(f"Handoff contains an unexpected path: {name}")
    if actual_paths != expected_paths:
        raise HandoffError("Handoff contains missing or unexpected paths.")
    total = 0
    for name, record in files.items():
        if name.startswith("/") or ".." in Path(name).parts:
            raise HandoffError("Unsafe handoff path.")
        maximum = MAX_FILE_BYTES.get(name, MAX_DISTRIBUTION_BYTES)
        path = root / name
        _regular_file(path, maximum)
        if not isinstance(record, dict) or set(record) != {"sha256", "size"}:
            raise HandoffError(f"Invalid manifest record: {name}")
        size = path.stat().st_size
        total += size
        if record["size"] != size or record["sha256"] != _digest(path):
            raise HandoffError(f"Handoff digest mismatch: {name}")
    if total > MAX_TOTAL_BYTES:
        raise HandoffError("Handoff payload is too large.")
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    if manifest_path.read_text(encoding="utf-8") != canonical:
        raise HandoffError("Handoff manifest is not canonical JSON.")
    return manifest


def verify(values: argparse.Namespace) -> dict[str, Any]:
    """Verify payload integrity and match identity to trusted release state."""
    manifest = _verified_payload(values)
    expected_identity = _identity_fields(values)
    if any(manifest[name] != value for name, value in expected_identity.items()):
        raise HandoffError("Handoff identity does not match trusted release state.")
    return manifest


def _parser() -> argparse.ArgumentParser:
    """Build the release-handoff command parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("canonicalize-sdist", "create", "preflight", "verify"))
    parser.add_argument("--handoff", type=Path)
    parser.add_argument("--release-tag")
    parser.add_argument("--sdist", type=Path)
    parser.add_argument("--epoch", type=int)
    parser.add_argument("--release-id")
    parser.add_argument("--prerelease", choices=("true", "false"))
    parser.add_argument("--source-sha")
    parser.add_argument("--candidate-sha")
    parser.add_argument("--target-sha")
    parser.add_argument("--tag-oid")
    parser.add_argument("--github-output", type=Path)
    return parser


def main() -> int:
    """Run manifest creation or verification."""
    parser = _parser()
    values = parser.parse_args()
    try:
        if values.mode == "canonicalize-sdist":
            if values.sdist is None or values.epoch is None or values.epoch < 0:
                raise HandoffError("Canonicalization requires a source distribution and epoch.")
            canonicalize_sdist(values.sdist, values.epoch)
        elif values.handoff is None or values.release_tag is None:
            raise HandoffError("Handoff operations require a directory and release tag.")
        elif values.mode == "create":
            create(values)
        elif values.mode == "preflight":
            _verified_payload(values)
        else:
            manifest = verify(values)
            if values.github_output is not None:
                with values.github_output.open("a", encoding="utf-8") as output:
                    for name in ("source_sha", "candidate_sha", "target_sha", "tag_oid"):
                        output.write(f"{name.replace('_', '-')}={manifest[name]}\n")
    except (HandoffError, OSError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    sys.exit(main())
