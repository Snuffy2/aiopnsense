"""Tests for shared live OPNsense script helpers."""

from __future__ import annotations

import importlib.util
import dataclasses
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest


def load_common_module() -> ModuleType:
    """Load the shared script helper as a module for direct unit testing."""
    module_path = Path(__file__).parents[1] / "scripts" / "_opnsense_live_common.py"
    spec = importlib.util.spec_from_file_location("_opnsense_live_common", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_default_env_file_points_to_scripts_env() -> None:
    """DEFAULT_ENV_FILE resolves to aiopnsense.env under scripts."""
    common = load_common_module()

    assert common.DEFAULT_ENV_FILE.name == "aiopnsense.env"
    assert common.DEFAULT_ENV_FILE.parent.name == "scripts"


def test_resolve_env_file_argument_maps_documented_default() -> None:
    """The documented env path resolves to the script-local runtime file."""
    common = load_common_module()

    assert (
        common.resolve_env_file_argument(Path("scripts/aiopnsense.env")) == common.DEFAULT_ENV_FILE
    )
    custom_path = Path("local.env")
    assert common.resolve_env_file_argument(custom_path) == custom_path


def test_reexec_with_repo_venv_uses_local_python(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bootstrap re-exec uses the repo venv when launched outside it."""
    common = load_common_module()
    calls: list[tuple[str, list[str]]] = []
    script_path = tmp_path / "scripts" / "aiopnsense_dump.py"
    script_path.parent.mkdir()
    script_path.write_text("", encoding="utf-8")
    repo_venv = tmp_path / ".venv"
    expected_python = repo_venv / "bin" / "python"
    expected_python.parent.mkdir(parents=True)
    expected_python.write_text("", encoding="utf-8")

    monkeypatch.delenv("AIOPNSENSE_LIVE_SCRIPT_BOOTSTRAPPED", raising=False)
    monkeypatch.setattr(common.sys, "prefix", "/usr/local")
    monkeypatch.setattr(common.sys, "argv", ["aiopnsense_dump.py", "--help"])
    monkeypatch.setattr(common.os, "execv", lambda path, args: calls.append((path, args)))

    common.reexec_with_repo_venv(script_path)

    assert common.os.environ["AIOPNSENSE_LIVE_SCRIPT_BOOTSTRAPPED"] == "1"
    assert calls == [
        (
            str(expected_python),
            [str(expected_python), str(script_path), "--help"],
        )
    ]


def test_reexec_with_repo_venv_skips_when_already_in_venv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bootstrap does nothing when the repo venv is already active."""
    common = load_common_module()
    repo_venv = Path(__file__).parents[1] / ".venv"
    execv = MagicMock()

    monkeypatch.delenv("AIOPNSENSE_LIVE_SCRIPT_BOOTSTRAPPED", raising=False)
    monkeypatch.setattr(common.sys, "prefix", str(repo_venv))
    monkeypatch.setattr(common.os, "execv", execv)

    common.reexec_with_repo_venv(Path(__file__).parents[1] / "scripts" / "aiopnsense_dump.py")

    execv.assert_not_called()


def test_load_env_file_parses_simple_shell_style_values(tmp_path: Path) -> None:
    """Env files support blank lines, comments, quoted values, and inline comments."""
    common = load_common_module()
    env_file = tmp_path / "aiopnsense.env"
    env_file.write_text(
        "\n".join(
            [
                "# local credentials",
                "AIOPNSENSE_URL=https://firewall.example.test",
                "AIOPNSENSE_API_KEY='key value'",
                'AIOPNSENSE_API_SECRET="secret value"',
                "AIOPNSENSE_VERIFY_SSL=false # local cert",
                "",
            ]
        ),
        encoding="utf-8",
    )

    assert common.load_env_file(env_file) == {
        "AIOPNSENSE_URL": "https://firewall.example.test",
        "AIOPNSENSE_API_KEY": "key value",
        "AIOPNSENSE_API_SECRET": "secret value",
        "AIOPNSENSE_VERIFY_SSL": "false",
    }


def test_strip_inline_comment_preserves_hashes_inside_values() -> None:
    """Hash characters are comments only when value-starting or whitespace-delimited."""
    common = load_common_module()

    assert common._strip_inline_comment("abc#123") == "abc#123"
    assert common._strip_inline_comment("'abc#123'") == "'abc#123'"
    assert common._strip_inline_comment('"abc#123"') == '"abc#123"'
    assert common._strip_inline_comment("# full-line comment") == ""
    assert common._strip_inline_comment("abc # comment") == "abc"
    assert common._strip_inline_comment("abc\t# comment") == "abc"


def test_load_env_file_rejects_malformed_line(tmp_path: Path) -> None:
    """Malformed env file lines raise a clear configuration error."""
    common = load_common_module()
    env_file = tmp_path / "aiopnsense.env"
    env_file.write_text("AIOPNSENSE_URL\n", encoding="utf-8")

    with pytest.raises(common.LiveConfigError, match="Malformed env file line 1"):
        common.load_env_file(env_file)


def test_load_env_file_rejects_non_file_path(tmp_path: Path) -> None:
    """Passing a directory raises a configuration error with clear path context."""
    common = load_common_module()
    env_dir = tmp_path / "not-a-file"
    env_dir.mkdir()

    with pytest.raises(common.LiveConfigError, match=f"not a file: {env_dir}") as excinfo:
        common.load_env_file(env_dir)

    assert str(env_dir) in str(excinfo.value)


def test_load_env_file_rejects_malformed_line_without_leaking_secret(tmp_path: Path) -> None:
    """Malformed env lines should not leak secret text in error messages."""
    common = load_common_module()
    env_file = tmp_path / "aiopnsense.env"
    env_file.write_text("AIOPNSENSE_API_SECRET abc123TOPSECRET\n", encoding="utf-8")

    with pytest.raises(common.LiveConfigError, match="Malformed env file line 1") as excinfo:
        common.load_env_file(env_file)

    assert "abc123TOPSECRET" not in str(excinfo.value)


def test_load_env_file_rejects_invalid_key_characters_without_leaking_secret(
    tmp_path: Path,
) -> None:
    """Malformed env keys are rejected without exposing secret values."""
    common = load_common_module()
    env_file = tmp_path / "aiopnsense.env"
    env_file.write_text("AIOPNSENSE_API_SECRET abc=123TOPSECRET\n", encoding="utf-8")

    with pytest.raises(common.LiveConfigError, match="Malformed env file line 1") as excinfo:
        common.load_env_file(env_file)

    assert "123TOPSECRET" not in str(excinfo.value)


def test_get_env_value_prefers_canonical_key() -> None:
    """Canonical AIOPNSENSE variables win over OPNSENSE fallback variables."""
    common = load_common_module()
    env = {
        "AIOPNSENSE_URL": "https://canonical.example.test",
        "OPNSENSE_URL": "https://fallback.example.test",
    }

    assert common.get_env_value(env, "URL") == "https://canonical.example.test"


def test_get_env_value_uses_fallback_key() -> None:
    """OPNSENSE variables are accepted when AIOPNSENSE variables are absent."""
    common = load_common_module()
    env = {"OPNSENSE_API_KEY": "fallback-key"}

    assert common.get_env_value(env, "API_KEY") == "fallback-key"


def test_get_env_value_prefers_canonical_key_even_when_blank() -> None:
    """Canonical key presence wins even when blank."""
    common = load_common_module()
    env = {
        "AIOPNSENSE_URL": "",
        "OPNSENSE_URL": "https://fallback.example.test",
    }

    with pytest.raises(common.LiveConfigError, match="AIOPNSENSE_URL"):
        common.get_env_value(env, "URL")


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("1", True),
        ("true", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
    ],
)
def test_parse_bool_accepts_documented_values(raw_value: str, expected: bool) -> None:
    """Documented boolean spellings parse consistently."""
    common = load_common_module()

    assert common.parse_bool(raw_value, "AIOPNSENSE_VERIFY_SSL") is expected


def test_parse_bool_rejects_unknown_value() -> None:
    """Unknown boolean text raises a clear configuration error."""
    common = load_common_module()

    with pytest.raises(common.LiveConfigError, match="AIOPNSENSE_VERIFY_SSL"):
        common.parse_bool("maybe", "AIOPNSENSE_VERIFY_SSL")


def test_load_config_requires_url_key_and_secret(tmp_path: Path) -> None:
    """All connection fields are required."""
    common = load_common_module()
    env_file = tmp_path / "aiopnsense.env"
    env_file.write_text("AIOPNSENSE_URL=https://firewall.example.test\n", encoding="utf-8")

    with pytest.raises(common.LiveConfigError, match="AIOPNSENSE_API_KEY"):
        common.load_live_config(env_file)


def test_load_config_builds_config_from_fallback_names(tmp_path: Path) -> None:
    """Fallback OPNSENSE variable names populate the live config."""
    common = load_common_module()
    env_file = tmp_path / "aiopnsense.env"
    env_file.write_text(
        "\n".join(
            [
                "OPNSENSE_URL=https://firewall.example.test",
                "OPNSENSE_API_KEY=key",
                "OPNSENSE_API_SECRET=secret",
                "OPNSENSE_VERIFY_SSL=no",
                "",
            ]
        ),
        encoding="utf-8",
    )

    config = common.load_live_config(env_file)

    assert config.url == "https://firewall.example.test"
    assert config.api_key == "key"
    assert config.api_secret == "secret"
    assert config.verify_ssl is False


def test_load_config_defaults_verify_ssl_true(tmp_path: Path) -> None:
    """Missing VERIFY_SSL defaults to True."""
    common = load_common_module()
    env_file = tmp_path / "aiopnsense.env"
    env_file.write_text(
        "\n".join(
            [
                "AIOPNSENSE_URL=https://firewall.example.test",
                "AIOPNSENSE_API_KEY=key",
                "AIOPNSENSE_API_SECRET=secret",
                "",
            ]
        ),
        encoding="utf-8",
    )

    config = common.load_live_config(env_file)

    assert config.verify_ssl is True


def test_load_config_empty_canonical_verify_ssl_is_invalid(tmp_path: Path) -> None:
    """Empty canonical verify flag must be rejected, not defaulted or treated as fallback."""
    common = load_common_module()
    env_file = tmp_path / "aiopnsense.env"
    env_file.write_text(
        "\n".join(
            [
                "AIOPNSENSE_URL=https://firewall.example.test",
                "AIOPNSENSE_API_KEY=key",
                "AIOPNSENSE_API_SECRET=secret",
                "AIOPNSENSE_VERIFY_SSL=",
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(common.LiveConfigError, match="AIOPNSENSE_VERIFY_SSL"):
        common.load_live_config(env_file)


def test_live_config_is_frozen() -> None:
    """LiveConfig is immutable and rejects attribute reassignment."""
    common = load_common_module()
    config = common.LiveConfig(
        url="https://firewall.example.test",
        api_key="key",
        api_secret="secret",
        verify_ssl=True,
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        config.url = "https://changed.example.test"


def test_create_client_uses_live_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_client passes all required constructor arguments to OPNsenseClient."""
    common = load_common_module()

    class FakeOPNsenseClient:
        """Capture constructor arguments for verification."""

        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    fake_client = FakeOPNsenseClient()
    fake_constructor = MagicMock(return_value=fake_client)

    monkeypatch.setattr(common, "_OPNSENSE_CLIENT_CLASS", fake_constructor)
    config = common.LiveConfig(
        url="https://firewall.example.test",
        api_key="my-key",
        api_secret="my-secret",
        verify_ssl=False,
    )
    fake_session = object()

    result = common.create_client(config, fake_session)

    fake_constructor.assert_called_once_with(
        url="https://firewall.example.test",
        username="my-key",
        password="my-secret",
        session=fake_session,
        opts={"verify_ssl": False},
        throw_errors=True,
    )
    assert result is fake_client


def test_format_json_sorts_keys_and_indents() -> None:
    """Script output is stable, readable JSON."""
    common = load_common_module()
    payload: dict[str, Any] = {"z": 1, "a": {"b": 2}}

    assert (
        common.format_json(payload)
        == json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
    )


def test_write_output_prints_and_writes_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Output is mirrored to stdout and an optional file."""
    common = load_common_module()
    output_file = tmp_path / "dump.json"

    common.write_output({"ok": True}, output_file)

    assert capsys.readouterr().out == '{\n  "ok": true\n}\n'
    assert output_file.read_text(encoding="utf-8") == '{\n  "ok": true\n}\n'
