"""Shared helpers for live OPNsense diagnostic scripts."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp

from aiopnsense import OPNsenseClient

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = SCRIPT_DIR / "aiopnsense.env"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
_ENV_KEY_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class LiveConfigError(ValueError):
    """Raised when live script configuration is missing or invalid."""


@dataclass(frozen=True)
class LiveConfig:
    """Connection configuration loaded from a live script env file.

    Args:
        url: Base URL of the OPNsense instance.
        api_key: OPNsense API key used as HTTP basic auth username.
        api_secret: OPNsense API secret used as HTTP basic auth password.
        verify_ssl: Whether aiohttp should verify the server TLS certificate.
    """

    url: str
    api_key: str
    api_secret: str
    verify_ssl: bool


def _strip_inline_comment(value: str) -> str:
    """Remove unquoted inline comments from an env file value.

    Args:
        value: Raw value text after the first equals sign.

    Returns:
        Value text with an unquoted ``#`` comment removed.
    """
    quote: str | None = None
    for index, char in enumerate(value):
        if char in {"'", '"'}:
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
        elif char == "#" and quote is None and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value.strip()


def _unquote(value: str) -> str:
    """Remove matching single or double quotes around a value.

    Args:
        value: Env file value after whitespace and inline comments are removed.

    Returns:
        Unquoted value when quotes match, otherwise the original value.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_env_file(path: Path = DEFAULT_ENV_FILE) -> dict[str, str]:
    """Load a simple ``KEY=value`` env file.

    Args:
        path: Env file to read.

    Returns:
        Mapping of env variable names to values.

    Raises:
        LiveConfigError: Raised when the file is missing or contains malformed lines.
    """
    if not path.exists():
        raise LiveConfigError(f"Env file not found: {path}")
    if not path.is_file():
        raise LiveConfigError(f"Env file is not a file: {path}")

    values: dict[str, str] = {}
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise LiveConfigError(f"Unable to read env file: {path}") from exc

    for line_number, raw_line in enumerate(raw_lines, 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise LiveConfigError(f"Malformed env file line {line_number}")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key or _ENV_KEY_PATTERN.fullmatch(key) is None:
            raise LiveConfigError(f"Malformed env file line {line_number}")
        value = _unquote(_strip_inline_comment(raw_value))
        values[key] = value
    return values


def get_env_value(values: Mapping[str, str], suffix: str, *, required: bool = True) -> str | None:
    """Return a canonical ``AIOPNSENSE_*`` value with ``OPNSENSE_*`` fallback.

    Args:
        values: Env values loaded from disk.
        suffix: Variable suffix, such as ``URL`` or ``API_SECRET``.
        required: Whether a missing value should raise.

    Returns:
        Configured value or ``None`` when missing and not required.

    Raises:
        LiveConfigError: Raised when a required value is missing.
    """
    canonical_key = f"AIOPNSENSE_{suffix}"
    fallback_key = f"OPNSENSE_{suffix}"
    if canonical_key in values:
        canonical_value = values[canonical_key]
        if required and not canonical_value.strip():
            raise LiveConfigError(f"Missing required env value: {canonical_key}")
        return canonical_value
    fallback_value = values.get(fallback_key)
    if fallback_value is None:
        if required:
            raise LiveConfigError(f"Missing required env value: {canonical_key}")
        return None
    if required and not fallback_value.strip():
        raise LiveConfigError(f"Missing required env value: {canonical_key}")
    return fallback_value


def parse_bool(value: str, name: str) -> bool:
    """Parse a documented boolean env value.

    Args:
        value: Raw boolean text.
        name: Env variable name used in error messages.

    Returns:
        Parsed boolean.

    Raises:
        LiveConfigError: Raised when the value is not a documented boolean spelling.
    """
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise LiveConfigError(f"{name} must be one of: 1, true, yes, on, 0, false, no, off")


def load_live_config(path: Path = DEFAULT_ENV_FILE) -> LiveConfig:
    """Load live OPNsense connection configuration.

    Args:
        path: Env file to read.

    Returns:
        LiveConfig with URL, credentials, and TLS verification setting.
    """
    values = load_env_file(path)
    verify_ssl_raw = get_env_value(values, "VERIFY_SSL", required=False)
    if verify_ssl_raw is None:
        verify_ssl_raw = "true"
    return LiveConfig(
        url=str(get_env_value(values, "URL")),
        api_key=str(get_env_value(values, "API_KEY")),
        api_secret=str(get_env_value(values, "API_SECRET")),
        verify_ssl=parse_bool(verify_ssl_raw, name="AIOPNSENSE_VERIFY_SSL"),
    )


def format_json(payload: Any) -> str:
    """Return stable pretty JSON for terminal and file output.

    Args:
        payload: JSON-serializable payload.

    Returns:
        Pretty JSON string ending with a newline.
    """
    return json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"


def write_output(payload: Any, output_path: Path | None = None) -> None:
    """Print JSON output and optionally mirror it to a file.

    Args:
        payload: JSON-serializable payload.
        output_path: Optional path to write the same JSON output.
    """
    rendered = format_json(payload)
    print(rendered, end="")
    if output_path is not None:
        output_path.write_text(rendered, encoding="utf-8")


def create_client(config: LiveConfig, session: aiohttp.ClientSession) -> OPNsenseClient:
    """Create an OPNsense client for live scripts.

    Args:
        config: Live connection configuration.
        session: aiohttp session owned by the caller.

    Returns:
        Configured OPNsenseClient.
    """
    return OPNsenseClient(
        url=config.url,
        username=config.api_key,
        password=config.api_secret,
        session=session,
        opts={"verify_ssl": config.verify_ssl},
        throw_errors=True,
    )
