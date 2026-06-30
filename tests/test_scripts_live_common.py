"""Tests for shared live OPNsense script helpers."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any

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


def test_load_env_file_rejects_malformed_line(tmp_path: Path) -> None:
    """Malformed env file lines raise a clear configuration error."""
    common = load_common_module()
    env_file = tmp_path / "aiopnsense.env"
    env_file.write_text("AIOPNSENSE_URL\n", encoding="utf-8")

    with pytest.raises(common.LiveConfigError, match="line 1"):
        common.load_env_file(env_file)


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
