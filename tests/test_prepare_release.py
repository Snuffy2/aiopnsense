"""Tests for release version preparation."""

import importlib.util
import io
from pathlib import Path
import sys

import pytest

SCRIPT_PATH = Path(__file__).parents[1] / ".github" / "scripts" / "prepare_release.py"
SCRIPT_SPEC = importlib.util.spec_from_file_location("prepare_release", SCRIPT_PATH)
assert SCRIPT_SPEC is not None
assert SCRIPT_SPEC.loader is not None
prepare_release = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(prepare_release)


@pytest.mark.parametrize(
    "tag",
    ["v1.1.8", "v1.2.0-beta.1", "v1.1.7.1", "v1.2.0b1"],
)
def test_validate_release_tag_accepts_supported_formats(tag: str) -> None:
    """Accept version formats already used by the release workflow.

    Args:
        tag (str): Supported release tag under test.
    """
    prepare_release.validate_release_tag(tag)


@pytest.mark.parametrize(
    "tag",
    ["", "1.1.8", "v1", "v1.1.8 beta", "v1.1.8;echo-bad"],
)
def test_validate_release_tag_rejects_unsupported_formats(tag: str) -> None:
    """Reject malformed tags before they reach Git or GitHub commands.

    Args:
        tag (str): Unsupported release tag under test.
    """
    with pytest.raises(ValueError, match="Invalid release tag"):
        prepare_release.validate_release_tag(tag)


@pytest.mark.parametrize(
    ("bump_type", "expected_tag"),
    [
        ("patch", "v1.1.8"),
        ("minor", "v1.2.0"),
        ("major", "v2.0.0"),
    ],
)
def test_next_stable_release_tag_uses_highest_stable_version(
    bump_type: str, expected_tag: str
) -> None:
    """Ignore non-stable tags while incrementing the highest stable release.

    Args:
        bump_type (str): Requested version increment.
        expected_tag (str): Expected next stable release tag.
    """
    tags = [
        "v1.1.6",
        "v1.1.7-beta.1",
        "v1.1.7.1",
        "v1.1.7b1",
        "invalid",
        "v0.99.99",
        "v1.1.7-rc.1",
        "v1.1.7",
    ]

    assert prepare_release.next_stable_release_tag(tags, bump_type) == expected_tag


@pytest.mark.parametrize(
    ("tags", "bump_type", "message"),
    [
        (["v1.1.7-beta.1", "v1.1.7.1"], "patch", "No stable released tag"),
        (["v1.1.7"], "feature", "Unsupported bump type"),
    ],
)
def test_next_stable_release_tag_rejects_invalid_requests(
    tags: list[str], bump_type: str, message: str
) -> None:
    """Reject requests without a supported stable release increment.

    Args:
        tags (list[str]): Candidate release tag names.
        bump_type (str): Requested version increment.
        message (str): Expected failure message.
    """
    with pytest.raises(ValueError, match=message):
        prepare_release.next_stable_release_tag(tags, bump_type)


def test_next_tag_cli_reads_tags_from_standard_input(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Print the next stable tag without writing the package version file.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing CLI inputs.
        capsys (pytest.CaptureFixture[str]): Fixture for capturing CLI output.
    """
    monkeypatch.setattr(sys, "argv", [str(SCRIPT_PATH), "--next-tag", "minor"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("v1.0.9\nv1.1.0-beta.1\nv1.0.10\n"))

    assert prepare_release.main() == 0
    assert capsys.readouterr().out == "v1.1.0\n"


def test_check_only_cli_preserves_positional_tag_contract(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Validate an explicit positional tag without writing files.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing CLI arguments.
        capsys (pytest.CaptureFixture[str]): Fixture for capturing CLI output.
    """
    monkeypatch.setattr(sys, "argv", [str(SCRIPT_PATH), "--check-only", "v1.1.8"])

    assert prepare_release.main() == 0
    assert capsys.readouterr().out == ""


def _write_version_file(repository: Path, content: str | None = None) -> Path:
    """Create a representative package version file.

    Args:
        repository (Path): Temporary repository root.
        content (str | None): Optional const.py content override.

    Returns:
        Path: Path to the created const.py.
    """
    package = repository / "aiopnsense"
    package.mkdir(parents=True)
    const_path = package / "const.py"
    const_path.write_text(
        content or 'VERSION = "v1.1.7"\nOTHER_VERSION = "v1.0.0"\n',
        encoding="utf-8",
    )
    return const_path


def test_update_release_version_updates_only_release_declaration(tmp_path: Path) -> None:
    """Update the package version without changing unrelated versions.

    Args:
        tmp_path (Path): Temporary repository root.
    """
    const_path = _write_version_file(tmp_path)

    prepare_release.update_release_version(tmp_path, "v1.1.8")

    assert const_path.read_text(encoding="utf-8") == (
        'VERSION = "v1.1.8"\nOTHER_VERSION = "v1.0.0"\n'
    )


def test_update_release_version_rejects_missing_declaration(tmp_path: Path) -> None:
    """Leave the version file unchanged when its declaration is missing.

    Args:
        tmp_path (Path): Temporary repository root.
    """
    const_path = _write_version_file(tmp_path, 'DOMAIN = "aiopnsense"\n')
    original = const_path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="Expected one version declaration"):
        prepare_release.update_release_version(tmp_path, "v1.1.8")

    assert const_path.read_text(encoding="utf-8") == original
