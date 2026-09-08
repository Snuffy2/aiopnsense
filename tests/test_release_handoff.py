"""Behavioral tests for the bounded release-candidate handoff."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tarfile

import pytest

SCRIPT = Path(__file__).parents[1] / ".github" / "scripts" / "release_handoff.py"
SPEC = importlib.util.spec_from_file_location("release_handoff", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
release_handoff = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_handoff)


def _arguments(root: Path) -> argparse.Namespace:
    """Return fixed trusted identity arguments for a handoff fixture.

    Args:
        root (Path): Handoff fixture directory.

    Returns:
        argparse.Namespace: Trusted fixture arguments.
    """
    return argparse.Namespace(
        handoff=root,
        release_tag="v1.2.3",
        release_id="1234",
        prerelease="false",
        source_sha="1" * 40,
        candidate_sha="2" * 40,
        target_sha="1" * 40,
        tag_oid="3" * 40,
    )


def _handoff(tmp_path: Path) -> tuple[Path, argparse.Namespace]:
    """Create one valid exact handoff fixture.

    Args:
        tmp_path (Path): Temporary test directory.

    Returns:
        tuple[Path, argparse.Namespace]: Handoff root and its trusted arguments.
    """
    root = tmp_path / "handoff"
    dist = root / "dist"
    dist.mkdir(parents=True)
    (root / "candidate-const.py").write_text('VERSION = "v1.2.3"\n', encoding="utf-8")
    (root / "changelog.md").write_text("## [v1.2.3](https://example.invalid)\n", encoding="utf-8")
    (dist / "aiopnsense-1.2.3-py3-none-any.whl").write_bytes(b"wheel")
    (dist / "aiopnsense-1.2.3.tar.gz").write_bytes(b"sdist")
    arguments = _arguments(root)
    release_handoff.create(arguments)
    return root, arguments


def test_manifest_round_trip_uses_exact_canonical_payload(tmp_path: Path) -> None:
    """Create and verify the only five files allowed across the privilege boundary.

    Args:
        tmp_path (Path): Temporary test directory.
    """
    root, arguments = _handoff(tmp_path)

    manifest = release_handoff.verify(arguments)

    assert set(manifest["files"]) == {
        "candidate-const.py",
        "changelog.md",
        "dist/aiopnsense-1.2.3-py3-none-any.whl",
        "dist/aiopnsense-1.2.3.tar.gz",
    }
    encoded = (root / "manifest.json").read_text(encoding="utf-8")
    assert encoded == json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"


@pytest.mark.parametrize(
    "mutation",
    [
        "forged-manifest",
        "alternate-manifest",
        "extra-file",
        "empty-directory",
        "special-file",
        "wrong-payload",
        "wrong-identity",
        "symlink",
    ],
)
def test_manifest_rejects_boundary_mutations(tmp_path: Path, mutation: str) -> None:
    """Reject forged identities, path growth, symlinks, and payload substitution.

    Args:
        tmp_path (Path): Temporary test directory.
        mutation (str): Boundary mutation to apply.
    """
    root, arguments = _handoff(tmp_path)
    if mutation == "forged-manifest":
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"]["candidate-const.py"]["sha256"] = "0" * 64
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    elif mutation == "alternate-manifest":
        extra = root / "extra.txt"
        extra.write_bytes(b"extra")
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"]["extra.txt"] = {
            "sha256": release_handoff._digest(extra),
            "size": extra.stat().st_size,
        }
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    elif mutation == "extra-file":
        (root / "execute-me.py").write_text("raise SystemExit\n", encoding="utf-8")
    elif mutation == "empty-directory":
        (root / "empty").mkdir()
    elif mutation == "special-file":
        os.mkfifo(root / "named-pipe")
    elif mutation == "wrong-payload":
        (root / "candidate-const.py").write_text('VERSION = "v9.9.9"\n', encoding="utf-8")
    elif mutation == "wrong-identity":
        arguments.candidate_sha = "4" * 40
    else:
        target = root / "candidate-const.py"
        saved = tmp_path / "saved-const.py"
        shutil.move(target, saved)
        target.symlink_to(saved)

    with pytest.raises(release_handoff.HandoffError):
        release_handoff.verify(arguments)


def test_manifest_rejects_oversized_changelog(tmp_path: Path) -> None:
    """Reject a handoff member before hashing an unbounded candidate payload.

    Args:
        tmp_path (Path): Temporary test directory.
    """
    root, arguments = _handoff(tmp_path)
    (root / "changelog.md").write_bytes(b"x" * (2 * 1024 * 1024 + 1))

    with pytest.raises(release_handoff.HandoffError, match="too large"):
        release_handoff.verify(arguments)


def test_preflight_checks_payload_without_accepting_manifest_identity(tmp_path: Path) -> None:
    """Bound candidate data before reconstructing the independently derived commit.

    Args:
        tmp_path (Path): Temporary test directory.
    """
    _root, arguments = _handoff(tmp_path)
    arguments.candidate_sha = "4" * 40

    release_handoff._verified_payload(arguments)
    with pytest.raises(release_handoff.HandoffError, match="identity"):
        release_handoff.verify(arguments)


def test_sdist_canonicalization_is_byte_reproducible(tmp_path: Path) -> None:
    """Normalize different archive timestamps to the same release artifact bytes.

    Args:
        tmp_path (Path): Temporary test directory.
    """
    outputs = []
    for index, timestamp in enumerate((100, 200)):
        source = tmp_path / f"source-{index}.txt"
        source.write_text("release payload\n", encoding="utf-8")
        archive = tmp_path / f"release-{index}.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            info = output.gettarinfo(source, arcname="aiopnsense-1.2.3/payload.txt")
            info.mtime = timestamp
            with source.open("rb") as contents:
                output.addfile(info, contents)
        release_handoff.canonicalize_sdist(archive, 1_700_000_000)
        outputs.append(archive.read_bytes())

    assert outputs[0] == outputs[1]


@pytest.mark.parametrize("member_name", ["../escape", "aiopnsense/link"])
def test_sdist_canonicalization_rejects_unsafe_members(tmp_path: Path, member_name: str) -> None:
    """Reject traversal and links before rewriting a candidate archive.

    Args:
        tmp_path (Path): Temporary test directory.
        member_name (str): Unsafe archive member to construct.
    """
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        info = tarfile.TarInfo(member_name)
        if member_name.endswith("link"):
            info.type = tarfile.SYMTYPE
            info.linkname = "target"
        output.addfile(info)

    with pytest.raises(release_handoff.HandoffError, match="unsafe"):
        release_handoff.canonicalize_sdist(archive, 1_700_000_000)


def test_manifest_rejects_symlink_handoff_root(tmp_path: Path) -> None:
    """Reject a handoff root redirected after artifact download.

    Args:
        tmp_path (Path): Temporary test directory.
    """
    root, arguments = _handoff(tmp_path)
    link = tmp_path / "handoff-link"
    link.symlink_to(root, target_is_directory=True)
    arguments.handoff = link

    with pytest.raises(release_handoff.HandoffError, match="root"):
        release_handoff.verify(arguments)
