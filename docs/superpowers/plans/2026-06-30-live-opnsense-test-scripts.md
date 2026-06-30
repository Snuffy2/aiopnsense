# Live OPNsense Test Scripts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add live diagnostic scripts that can dump supported `aiopnsense` library outputs and arbitrary raw OPNsense API responses using credentials from a local env file.

**Architecture:** Put all credential loading, JSON output, SSL option parsing, and client/session setup in one small helper module under `scripts/`. Build two separate CLIs on top: one safe-ish `aiopnsense` public-method dumper with a menu, and one explicit raw OPNsense API caller for arbitrary GET/POST paths.

**Tech Stack:** Python 3.14, stdlib `argparse`/`json`/`asyncio`, `aiohttp`, existing `aiopnsense.OPNsenseClient`, pytest, prek/ruff/mypy.

---

## File Structure

- Create `scripts/_opnsense_live_common.py`
  - Owns `.env` parsing, canonical/fallback env key lookup, boolean parsing, output file mirroring, JSON serialization, `aiohttp.ClientSession` creation, and `OPNsenseClient` construction.
  - Does not know about supported aiopnsense endpoints or raw CLI argument policy.
- Create `scripts/aiopnsense_dump.py`
  - Owns the supported library-method registry, menu, endpoint selection, stream timeout, warning labels, and calling public `OPNsenseClient` methods.
  - Uses only public library methods for live data collection.
- Create `scripts/opnsense_api_call.py`
  - Owns arbitrary raw OPNsense API calls, method selection, POST payload parsing, and response metadata capture.
  - Uses `aiohttp` directly, not private `OPNsenseClient` transport helpers.
- Create `scripts/aiopnsense.env.example`
  - Documents canonical `AIOPNSENSE_*` variables and optional fallback `OPNSENSE_*` variable names.
- Modify `.gitignore`
  - Ignore only `scripts/aiopnsense.env`, leaving `scripts/aiopnsense.env.example` tracked.
- Create tests:
  - `tests/test_scripts_live_common.py`
  - `tests/test_scripts_aiopnsense_dump.py`
  - `tests/test_scripts_opnsense_api_call.py`

## Task 1: Shared Live Script Helper

**Files:**
- Create: `scripts/_opnsense_live_common.py`
- Test: `tests/test_scripts_live_common.py`

- [ ] **Step 1: Write failing tests for env loading, fallback lookup, boolean parsing, output mirroring, and JSON formatting**

Create `tests/test_scripts_live_common.py`:

```python
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

    assert common.parse_bool(raw_value, name="AIOPNSENSE_VERIFY_SSL") is expected


def test_parse_bool_rejects_unknown_value() -> None:
    """Unknown boolean text raises a clear configuration error."""
    common = load_common_module()

    with pytest.raises(common.LiveConfigError, match="AIOPNSENSE_VERIFY_SSL"):
        common.parse_bool("maybe", name="AIOPNSENSE_VERIFY_SSL")


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

    assert common.format_json(payload) == json.dumps(payload, indent=2, sort_keys=True) + "\n"


def test_write_output_prints_and_writes_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Output is mirrored to stdout and an optional file."""
    common = load_common_module()
    output_file = tmp_path / "dump.json"

    common.write_output({"ok": True}, output_file)

    assert capsys.readouterr().out == '{\n  "ok": true\n}\n'
    assert output_file.read_text(encoding="utf-8") == '{\n  "ok": true\n}\n'
```

- [ ] **Step 2: Run tests to verify they fail because the helper does not exist**

Run:

```bash
./.venv/bin/python -m pytest tests/test_scripts_live_common.py -q
```

Expected: FAIL with `FileNotFoundError` for `scripts/_opnsense_live_common.py`.

- [ ] **Step 3: Implement the shared helper**

Create `scripts/_opnsense_live_common.py`:

```python
"""Shared helpers for live OPNsense diagnostic scripts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import aiohttp

from aiopnsense import OPNsenseClient

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = SCRIPT_DIR / "aiopnsense.env"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


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
        elif char == "#" and quote is None:
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

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise LiveConfigError(f"Malformed env file line {line_number}: {raw_line}")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise LiveConfigError(f"Malformed env file line {line_number}: {raw_line}")
        values[key] = _unquote(_strip_inline_comment(raw_value))
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
    value = values.get(canonical_key) or values.get(fallback_key)
    if value:
        return value
    if required:
        raise LiveConfigError(f"Missing required env value: {canonical_key}")
    return None


def parse_bool(value: str, *, name: str) -> bool:
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
    raise LiveConfigError(
        f"{name} must be one of: 1, true, yes, on, 0, false, no, off"
    )


def load_live_config(path: Path = DEFAULT_ENV_FILE) -> LiveConfig:
    """Load live OPNsense connection configuration.

    Args:
        path: Env file to read.

    Returns:
        LiveConfig with URL, credentials, and TLS verification setting.
    """
    values = load_env_file(path)
    verify_ssl_raw = get_env_value(values, "VERIFY_SSL", required=False) or "true"
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
```

- [ ] **Step 4: Run helper tests to verify they pass**

Run:

```bash
./.venv/bin/python -m pytest tests/test_scripts_live_common.py -q
```

Expected: PASS for all tests in `tests/test_scripts_live_common.py`.

- [ ] **Step 5: Commit the shared helper**

Run:

```bash
git add scripts/_opnsense_live_common.py tests/test_scripts_live_common.py
git commit -m "test: add shared live opnsense script helpers"
```

Expected: commit succeeds.

## Task 2: aiopnsense Public Method Dumper

**Files:**
- Create: `scripts/aiopnsense_dump.py`
- Test: `tests/test_scripts_aiopnsense_dump.py`

- [ ] **Step 1: Write failing tests for endpoint registry, menu selection, stream timeout, and output shape**

Create `tests/test_scripts_aiopnsense_dump.py`:

```python
"""Tests for the aiopnsense public-method dump script."""

from __future__ import annotations

from collections.abc import AsyncIterator
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock

import pytest


def load_dump_module() -> ModuleType:
    """Load the dump script as a module for direct unit testing."""
    scripts_dir = Path(__file__).parents[1] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    module_path = scripts_dir / "aiopnsense_dump.py"
    spec = importlib.util.spec_from_file_location("aiopnsense_dump", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeClient:
    """Fake async client with representative public aiopnsense methods."""

    def __init__(self) -> None:
        """Initialize call tracking for the fake client."""
        self.validate = AsyncMock(return_value=None)
        self.async_close = AsyncMock(return_value=None)
        self.get_system_info = AsyncMock(return_value={"name": "opnsense"})
        self.get_firmware_update_info = AsyncMock(return_value={"status": "ok"})

    async def stream_interface_traffic(self, poll_interval: int = 1) -> AsyncIterator[dict[str, Any]]:
        """Yield representative traffic samples indefinitely."""
        index = 0
        while True:
            yield {"sample": index, "poll_interval": poll_interval}
            index += 1


def test_endpoint_registry_contains_expected_read_only_entries() -> None:
    """The registry exposes known read-only method names and warning metadata."""
    module = load_dump_module()

    assert "system_info" in module.ENDPOINTS
    assert module.ENDPOINTS["system_info"].method_name == "get_system_info"
    assert "firmware_update_info" in module.ENDPOINTS
    assert module.ENDPOINTS["firmware_update_info"].warning is not None
    assert "system_reboot" not in module.ENDPOINTS
    assert "restart_service" not in module.ENDPOINTS


@pytest.mark.asyncio
async def test_run_endpoint_calls_public_method() -> None:
    """Running a normal endpoint calls the configured public method."""
    module = load_dump_module()
    client = FakeClient()

    result = await module.run_endpoint(client, "system_info", stream_seconds=30)

    assert result == {
        "endpoint": "system_info",
        "method": "get_system_info",
        "warning": None,
        "data": {"name": "opnsense"},
    }
    client.get_system_info.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_run_endpoint_includes_warning_for_refreshing_method() -> None:
    """Warning labels are included for methods that may refresh backend state."""
    module = load_dump_module()
    client = FakeClient()

    result = await module.run_endpoint(client, "firmware_update_info", stream_seconds=30)

    assert result["endpoint"] == "firmware_update_info"
    assert result["method"] == "get_firmware_update_info"
    assert "firmware check" in result["warning"]
    assert result["data"] == {"status": "ok"}


@pytest.mark.asyncio
async def test_run_endpoint_collects_stream_for_fixed_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stream endpoint collection stops when the monotonic deadline is reached."""
    module = load_dump_module()
    client = FakeClient()
    times = iter([0.0, 0.0, 1.0, 31.0])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(times))

    result = await module.run_endpoint(client, "interface_traffic_stream", stream_seconds=30)

    assert result == {
        "endpoint": "interface_traffic_stream",
        "method": "stream_interface_traffic",
        "warning": None,
        "data": [
            {"sample": 0, "poll_interval": 1},
            {"sample": 1, "poll_interval": 1},
        ],
    }


def test_choose_endpoint_from_menu_accepts_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """Menu selection accepts a one-based endpoint number."""
    module = load_dump_module()
    monkeypatch.setattr("builtins.input", lambda _prompt: "1")

    selected = module.choose_endpoint_from_menu(["system_info", "telemetry"])

    assert selected == "system_info"


def test_choose_endpoint_from_menu_rejects_invalid_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid menu selections raise a clear parser error."""
    module = load_dump_module()
    monkeypatch.setattr("builtins.input", lambda _prompt: "9")

    with pytest.raises(SystemExit):
        module.choose_endpoint_from_menu(["system_info", "telemetry"])
```

- [ ] **Step 2: Run tests to verify they fail because the script does not exist**

Run:

```bash
./.venv/bin/python -m pytest tests/test_scripts_aiopnsense_dump.py -q
```

Expected: FAIL with `FileNotFoundError` for `scripts/aiopnsense_dump.py`.

- [ ] **Step 3: Implement the aiopnsense dump script**

Create `scripts/aiopnsense_dump.py`:

```python
#!/usr/bin/env python3
"""Dump supported aiopnsense public method outputs from a live OPNsense instance."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any

import aiohttp

from _opnsense_live_common import (
    DEFAULT_ENV_FILE,
    LiveConfigError,
    create_client,
    load_live_config,
    write_output,
)


@dataclass(frozen=True)
class EndpointSpec:
    """Supported aiopnsense public method entry.

    Args:
        method_name: Public OPNsenseClient method name.
        warning: Operator warning included in output for methods with notable side effects.
    """

    method_name: str
    warning: str | None = None


ENDPOINTS: dict[str, EndpointSpec] = {
    "host_firmware_version": EndpointSpec("get_host_firmware_version"),
    "device_unique_id": EndpointSpec("get_device_unique_id"),
    "system_info": EndpointSpec("get_system_info"),
    "carp": EndpointSpec("get_carp"),
    "notices": EndpointSpec("get_notices"),
    "certificates": EndpointSpec("get_certificates"),
    "telemetry": EndpointSpec("get_telemetry"),
    "interfaces": EndpointSpec("get_interfaces"),
    "gateways": EndpointSpec("get_gateways"),
    "interface_traffic": EndpointSpec("get_interface_traffic"),
    "interface_traffic_stream": EndpointSpec("stream_interface_traffic"),
    "arp_table": EndpointSpec("get_arp_table"),
    "dhcp_leases": EndpointSpec("get_dhcp_leases"),
    "firewall": EndpointSpec("get_firewall"),
    "services": EndpointSpec("get_services"),
    "smart": EndpointSpec("get_smart"),
    "speedtest": EndpointSpec("get_speedtest"),
    "unbound_blocklist": EndpointSpec("get_unbound_blocklist"),
    "vnstat": EndpointSpec("get_vnstat"),
    "openvpn": EndpointSpec("get_openvpn"),
    "wireguard": EndpointSpec("get_wireguard"),
    "upgrade_status": EndpointSpec("upgrade_status"),
    "firmware_update_info": EndpointSpec(
        "get_firmware_update_info",
        warning="May trigger a firmware check if OPNsense cached firmware status is stale.",
    ),
}


def build_parser() -> argparse.ArgumentParser:
    """Build the command line parser.

    Returns:
        Configured argument parser.
    """
    parser = argparse.ArgumentParser(
        description="Dump JSON returned by supported aiopnsense public methods."
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help=f"Path to aiopnsense env file. Default: {DEFAULT_ENV_FILE}",
    )
    parser.add_argument(
        "--endpoint",
        choices=sorted(ENDPOINTS),
        help="Endpoint name to run. If omitted, an interactive menu is shown.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List supported endpoint names and exit.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path to write the same JSON printed to stdout.",
    )
    parser.add_argument(
        "--stream-seconds",
        type=float,
        default=30.0,
        help="Seconds to collect interface traffic stream samples. Default: 30.",
    )
    return parser


def list_endpoints() -> list[dict[str, str | None]]:
    """Return endpoint registry rows for CLI listing.

    Returns:
        List of endpoint metadata rows.
    """
    return [
        {
            "endpoint": name,
            "method": spec.method_name,
            "warning": spec.warning,
        }
        for name, spec in sorted(ENDPOINTS.items())
    ]


def choose_endpoint_from_menu(endpoint_names: list[str]) -> str:
    """Prompt for an endpoint selection.

    Args:
        endpoint_names: Ordered endpoint names to display.

    Returns:
        Selected endpoint name.

    Raises:
        SystemExit: Raised when the user enters an invalid selection.
    """
    for index, name in enumerate(endpoint_names, 1):
        spec = ENDPOINTS[name]
        warning = f" [{spec.warning}]" if spec.warning else ""
        print(f"{index:2d}. {name} -> {spec.method_name}{warning}")

    raw_selection = input("Select endpoint number: ").strip()
    try:
        selected_index = int(raw_selection)
    except ValueError as err:
        raise SystemExit(f"Invalid endpoint selection: {raw_selection}") from err
    if selected_index < 1 or selected_index > len(endpoint_names):
        raise SystemExit(f"Invalid endpoint selection: {raw_selection}")
    return endpoint_names[selected_index - 1]


async def _collect_stream(client: Any, stream_seconds: float) -> list[dict[str, Any]]:
    """Collect interface traffic stream samples for a fixed duration.

    Args:
        client: OPNsenseClient-like object.
        stream_seconds: Number of seconds to collect stream samples.

    Returns:
        Collected stream samples.
    """
    samples: list[dict[str, Any]] = []
    deadline = time.monotonic() + max(stream_seconds, 0.0)
    async for sample in client.stream_interface_traffic(poll_interval=1):
        samples.append(sample)
        if time.monotonic() >= deadline:
            break
    return samples


async def run_endpoint(client: Any, endpoint_name: str, *, stream_seconds: float) -> dict[str, Any]:
    """Run one supported aiopnsense endpoint and return a JSON-ready payload.

    Args:
        client: OPNsenseClient-like object.
        endpoint_name: Supported endpoint registry key.
        stream_seconds: Duration for stream endpoints.

    Returns:
        JSON-ready result with endpoint metadata and data.
    """
    spec = ENDPOINTS[endpoint_name]
    if spec.method_name == "stream_interface_traffic":
        data = await _collect_stream(client, stream_seconds)
    else:
        method: Callable[[], Awaitable[Any]] = getattr(client, spec.method_name)
        data = await method()
    return {
        "endpoint": endpoint_name,
        "method": spec.method_name,
        "warning": spec.warning,
        "data": data,
    }


async def async_main(argv: list[str] | None = None) -> int:
    """Run the script.

    Args:
        argv: Optional argument vector for tests.

    Returns:
        Process exit code.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list:
        write_output(list_endpoints(), args.output)
        return 0

    endpoint_name = args.endpoint or choose_endpoint_from_menu(sorted(ENDPOINTS))
    config = load_live_config(args.env_file)

    async with aiohttp.ClientSession() as session:
        client = create_client(config, session)
        try:
            await client.validate()
            result = await run_endpoint(
                client,
                endpoint_name,
                stream_seconds=float(args.stream_seconds),
            )
        finally:
            await client.async_close()

    write_output(result, args.output)
    return 0


def main() -> int:
    """Run the async CLI entrypoint and translate config failures to parser exits.

    Returns:
        Process exit code.
    """
    try:
        return asyncio.run(async_main())
    except LiveConfigError as err:
        raise SystemExit(str(err)) from err


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run aiopnsense dump tests to verify they pass**

Run:

```bash
./.venv/bin/python -m pytest tests/test_scripts_aiopnsense_dump.py -q
```

Expected: PASS for all tests in `tests/test_scripts_aiopnsense_dump.py`.

- [ ] **Step 5: Commit the aiopnsense dump script**

Run:

```bash
git add scripts/aiopnsense_dump.py tests/test_scripts_aiopnsense_dump.py
git commit -m "feat: add aiopnsense live dump script"
```

Expected: commit succeeds.

## Task 3: Raw OPNsense API Caller

**Files:**
- Create: `scripts/opnsense_api_call.py`
- Test: `tests/test_scripts_opnsense_api_call.py`

- [ ] **Step 1: Write failing tests for endpoint normalization, payload parsing, and raw response output**

Create `tests/test_scripts_opnsense_api_call.py`:

```python
"""Tests for the raw OPNsense API call script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def load_raw_module() -> ModuleType:
    """Load the raw API script as a module for direct unit testing."""
    scripts_dir = Path(__file__).parents[1] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    module_path = scripts_dir / "opnsense_api_call.py"
    spec = importlib.util.spec_from_file_location("opnsense_api_call", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeHeaders(dict[str, str]):
    """Headers mapping with aiohttp-compatible item iteration."""


class FakeResponse:
    """Small async context manager for fake aiohttp responses."""

    def __init__(
        self,
        *,
        status: int = 200,
        reason: str = "OK",
        headers: dict[str, str] | None = None,
        json_payload: Any = None,
        text_payload: str = "",
        json_error: BaseException | None = None,
    ) -> None:
        """Initialize fake response state."""
        self.status = status
        self.reason = reason
        self.headers = FakeHeaders(headers or {"content-type": "application/json"})
        self._json_payload = json_payload
        self._text_payload = text_payload
        self._json_error = json_error

    async def __aenter__(self) -> "FakeResponse":
        """Enter the fake response context."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        """Exit the fake response context."""

    async def json(self, content_type: object = None) -> Any:
        """Return fake JSON payload or raise a configured parse error."""
        del content_type
        if self._json_error is not None:
            raise self._json_error
        return self._json_payload

    async def text(self) -> str:
        """Return fake text payload."""
        return self._text_payload


class FakeSession:
    """Fake aiohttp session recording GET and POST calls."""

    def __init__(self, response: FakeResponse) -> None:
        """Initialize fake session call tracking."""
        self.response = response
        self.calls: list[tuple[str, str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        """Record a GET call and return the fake response."""
        self.calls.append(("get", url, kwargs))
        return self.response

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        """Record a POST call and return the fake response."""
        self.calls.append(("post", url, kwargs))
        return self.response


def test_normalize_endpoint_requires_leading_slash() -> None:
    """Endpoint normalization accepts both slash and non-slash paths."""
    module = load_raw_module()

    assert module.normalize_endpoint("api/core/system/status") == "/api/core/system/status"
    assert module.normalize_endpoint("/api/core/system/status") == "/api/core/system/status"


def test_normalize_endpoint_rejects_blank_value() -> None:
    """Blank endpoint values are rejected."""
    module = load_raw_module()

    with pytest.raises(ValueError, match="endpoint"):
        module.normalize_endpoint(" ")


def test_load_payload_from_json_string() -> None:
    """POST payloads can be provided as inline JSON objects."""
    module = load_raw_module()

    assert module.load_payload('{"foo": "bar"}', None) == {"foo": "bar"}


def test_load_payload_from_file(tmp_path: Path) -> None:
    """POST payloads can be loaded from a JSON file."""
    module = load_raw_module()
    payload_file = tmp_path / "payload.json"
    payload_file.write_text('{"foo": "bar"}', encoding="utf-8")

    assert module.load_payload(None, payload_file) == {"foo": "bar"}


def test_load_payload_rejects_non_object_json() -> None:
    """The raw script sends JSON objects as POST payloads."""
    module = load_raw_module()

    with pytest.raises(ValueError, match="JSON object"):
        module.load_payload("[1, 2]", None)


@pytest.mark.asyncio
async def test_call_api_get_returns_response_metadata() -> None:
    """GET calls return status, headers, parsed JSON, and no text body."""
    module = load_raw_module()
    config = module.LiveConfig(
        url="https://firewall.example.test",
        api_key="key",
        api_secret="secret",
        verify_ssl=False,
    )
    session = FakeSession(
        FakeResponse(
            status=200,
            reason="OK",
            headers={"x-test": "yes"},
            json_payload={"ok": True},
        )
    )

    result = await module.call_api(
        session,
        config,
        endpoint="/api/test",
        method="get",
        payload=None,
    )

    assert result == {
        "method": "GET",
        "endpoint": "/api/test",
        "url": "https://firewall.example.test/api/test",
        "status": 200,
        "reason": "OK",
        "headers": {"x-test": "yes"},
        "json": {"ok": True},
        "text": None,
    }
    assert session.calls[0][0] == "get"
    assert session.calls[0][1] == "https://firewall.example.test/api/test"
    assert session.calls[0][2]["ssl"] is False


@pytest.mark.asyncio
async def test_call_api_post_sends_payload() -> None:
    """POST calls send the JSON object payload."""
    module = load_raw_module()
    config = module.LiveConfig(
        url="https://firewall.example.test/",
        api_key="key",
        api_secret="secret",
        verify_ssl=True,
    )
    session = FakeSession(FakeResponse(json_payload={"saved": True}))

    result = await module.call_api(
        session,
        config,
        endpoint="/api/test",
        method="post",
        payload={"enabled": "1"},
    )

    assert result["method"] == "POST"
    assert result["url"] == "https://firewall.example.test/api/test"
    assert session.calls[0][0] == "post"
    assert session.calls[0][2]["json"] == {"enabled": "1"}
    assert session.calls[0][2]["ssl"] is True


@pytest.mark.asyncio
async def test_call_api_falls_back_to_text_for_non_json_response() -> None:
    """Non-JSON responses preserve the raw text body."""
    module = load_raw_module()
    config = module.LiveConfig(
        url="https://firewall.example.test",
        api_key="key",
        api_secret="secret",
        verify_ssl=True,
    )
    session = FakeSession(
        FakeResponse(
            status=404,
            reason="Not Found",
            headers={"content-type": "text/plain"},
            text_payload="missing",
            json_error=ValueError("not json"),
        )
    )

    result = await module.call_api(
        session,
        config,
        endpoint="/api/missing",
        method="get",
        payload=None,
    )

    assert result["status"] == 404
    assert result["json"] is None
    assert result["text"] == "missing"
```

- [ ] **Step 2: Run tests to verify they fail because the script does not exist**

Run:

```bash
./.venv/bin/python -m pytest tests/test_scripts_opnsense_api_call.py -q
```

Expected: FAIL with `FileNotFoundError` for `scripts/opnsense_api_call.py`.

- [ ] **Step 3: Implement the raw API call script**

Create `scripts/opnsense_api_call.py`:

```python
#!/usr/bin/env python3
"""Call an arbitrary raw OPNsense API endpoint from a local env file."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

import aiohttp

from _opnsense_live_common import (
    DEFAULT_ENV_FILE,
    LiveConfig,
    LiveConfigError,
    load_live_config,
    write_output,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the command line parser.

    Returns:
        Configured parser.
    """
    parser = argparse.ArgumentParser(
        description="Call an arbitrary raw OPNsense API endpoint."
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help=f"Path to aiopnsense env file. Default: {DEFAULT_ENV_FILE}",
    )
    parser.add_argument(
        "--endpoint",
        required=True,
        help="OPNsense API endpoint path, for example /api/core/firmware/status.",
    )
    parser.add_argument(
        "--method",
        required=True,
        choices=("get", "post"),
        help="HTTP method to use.",
    )
    parser.add_argument(
        "--payload",
        help="Inline JSON object payload for POST.",
    )
    parser.add_argument(
        "--payload-file",
        type=Path,
        help="Path to JSON object payload for POST.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path to write the same JSON printed to stdout.",
    )
    return parser


def normalize_endpoint(endpoint: str) -> str:
    """Return an endpoint path with a leading slash.

    Args:
        endpoint: Raw endpoint argument.

    Returns:
        Normalized endpoint path.

    Raises:
        ValueError: Raised when the endpoint is blank.
    """
    cleaned = endpoint.strip()
    if not cleaned:
        raise ValueError("endpoint must not be blank")
    return cleaned if cleaned.startswith("/") else f"/{cleaned}"


def _load_json_object(raw: str) -> dict[str, Any]:
    """Parse a JSON object.

    Args:
        raw: JSON text.

    Returns:
        Parsed JSON object.

    Raises:
        ValueError: Raised when the text is invalid JSON or not an object.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as err:
        raise ValueError(f"payload must be valid JSON: {err}") from err
    if not isinstance(parsed, dict):
        raise ValueError("payload must be a JSON object")
    return parsed


def load_payload(payload: str | None, payload_file: Path | None) -> dict[str, Any] | None:
    """Load an optional POST payload.

    Args:
        payload: Inline JSON object text.
        payload_file: JSON object file path.

    Returns:
        Parsed payload object or ``None``.

    Raises:
        ValueError: Raised when both payload sources are provided or parsing fails.
    """
    if payload and payload_file:
        raise ValueError("use either --payload or --payload-file, not both")
    if payload_file is not None:
        return _load_json_object(payload_file.read_text(encoding="utf-8"))
    if payload:
        return _load_json_object(payload)
    return None


async def call_api(
    session: Any,
    config: LiveConfig,
    *,
    endpoint: str,
    method: str,
    payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Call a raw OPNsense API endpoint.

    Args:
        session: aiohttp-like session.
        config: Live OPNsense configuration.
        endpoint: Normalized endpoint path.
        method: Lowercase HTTP method.
        payload: Optional POST payload.

    Returns:
        Response metadata plus parsed JSON or text body.
    """
    normalized_method = method.lower()
    normalized_endpoint = normalize_endpoint(endpoint)
    url = f"{config.url.rstrip('/')}{normalized_endpoint}"
    request_kwargs: dict[str, Any] = {
        "auth": aiohttp.BasicAuth(config.api_key, config.api_secret),
        "timeout": aiohttp.ClientTimeout(total=60),
        "ssl": config.verify_ssl,
    }
    if normalized_method == "post":
        request_kwargs["json"] = dict(payload or {})
        request = session.post
    else:
        request = session.get

    async with request(url, **request_kwargs) as response:
        parsed_json: Any | None
        text: str | None
        try:
            parsed_json = await response.json(content_type=None)
            text = None
        except (aiohttp.ContentTypeError, json.JSONDecodeError, ValueError):
            parsed_json = None
            text = await response.text()

        return {
            "method": normalized_method.upper(),
            "endpoint": normalized_endpoint,
            "url": url,
            "status": response.status,
            "reason": response.reason,
            "headers": dict(response.headers),
            "json": parsed_json,
            "text": text,
        }


async def async_main(argv: list[str] | None = None) -> int:
    """Run the script.

    Args:
        argv: Optional argument vector for tests.

    Returns:
        Process exit code.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        endpoint = normalize_endpoint(args.endpoint)
        payload = load_payload(args.payload, args.payload_file)
    except ValueError as err:
        parser.error(str(err))

    if args.method == "get" and payload is not None:
        parser.error("--payload and --payload-file are only valid with --method post")

    config = load_live_config(args.env_file)
    async with aiohttp.ClientSession() as session:
        result = await call_api(
            session,
            config,
            endpoint=endpoint,
            method=args.method,
            payload=payload,
        )
    write_output(result, args.output)
    return 0


def main() -> int:
    """Run the async CLI entrypoint and translate config failures to parser exits.

    Returns:
        Process exit code.
    """
    try:
        return asyncio.run(async_main())
    except LiveConfigError as err:
        raise SystemExit(str(err)) from err


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run raw API caller tests to verify they pass**

Run:

```bash
./.venv/bin/python -m pytest tests/test_scripts_opnsense_api_call.py -q
```

Expected: PASS for all tests in `tests/test_scripts_opnsense_api_call.py`.

- [ ] **Step 5: Commit the raw API caller**

Run:

```bash
git add scripts/opnsense_api_call.py tests/test_scripts_opnsense_api_call.py
git commit -m "feat: add raw opnsense api call script"
```

Expected: commit succeeds.

## Task 4: Env Example, Git Ignore, and Executability

**Files:**
- Create: `scripts/aiopnsense.env.example`
- Modify: `.gitignore`
- Modify file mode: `scripts/aiopnsense_dump.py`, `scripts/opnsense_api_call.py`

- [ ] **Step 1: Add the env example file**

Create `scripts/aiopnsense.env.example`:

```dotenv
# Copy this file to scripts/aiopnsense.env and fill in local credentials.
# The real aiopnsense.env file contains an OPNsense API key and secret and must not be committed.

AIOPNSENSE_URL=https://opnsense.example.com
AIOPNSENSE_API_KEY=replace-with-opnsense-api-key
AIOPNSENSE_API_SECRET=replace-with-opnsense-api-secret

# Set to false only for local/self-signed certificates when you accept that TLS risk.
AIOPNSENSE_VERIFY_SSL=true

# Fallback variable names are also accepted when AIOPNSENSE_* values are absent:
# OPNSENSE_URL=https://opnsense.example.com
# OPNSENSE_API_KEY=replace-with-opnsense-api-key
# OPNSENSE_API_SECRET=replace-with-opnsense-api-secret
# OPNSENSE_VERIFY_SSL=true
```

- [ ] **Step 2: Add the real env file to `.gitignore`**

Modify `.gitignore` by appending this line:

```gitignore
scripts/aiopnsense.env
```

- [ ] **Step 3: Make both scripts executable**

Run:

```bash
chmod +x scripts/aiopnsense_dump.py scripts/opnsense_api_call.py
```

Expected: command exits 0.

- [ ] **Step 4: Verify ignore behavior and executable bits**

Run:

```bash
git check-ignore scripts/aiopnsense.env
git ls-files --stage scripts/aiopnsense_dump.py scripts/opnsense_api_call.py
```

Expected:
- `git check-ignore` prints `scripts/aiopnsense.env`.
- `git ls-files --stage` shows mode `100755` for both scripts after they are added.

- [ ] **Step 5: Commit the env documentation and file modes**

Run:

```bash
git add .gitignore scripts/aiopnsense.env.example scripts/aiopnsense_dump.py scripts/opnsense_api_call.py
git commit -m "chore: document live script environment"
```

Expected: commit succeeds.

## Task 5: Full Validation and Final Cleanup

**Files:**
- Validate all files changed by Tasks 1-4.

- [ ] **Step 1: Run the focused script tests**

Run:

```bash
./.venv/bin/python -m pytest \
  tests/test_scripts_live_common.py \
  tests/test_scripts_aiopnsense_dump.py \
  tests/test_scripts_opnsense_api_call.py \
  -q
```

Expected: PASS for all focused script tests.

- [ ] **Step 2: Run the full pytest suite**

Run:

```bash
./.venv/bin/python -m pytest
```

Expected: full suite passes.

- [ ] **Step 3: Run prek**

Run:

```bash
./.venv/bin/python -m prek run -a
```

Expected: all hooks pass. If prek rewrites files, inspect the diff, rerun the focused tests and full pytest suite, then amend the relevant last commit.

- [ ] **Step 4: Run whitespace validation**

Run:

```bash
git diff --check
```

Expected: no output and exit code 0.

- [ ] **Step 5: Smoke-test CLI help without live credentials**

Run:

```bash
./scripts/aiopnsense_dump.py --help
./scripts/opnsense_api_call.py --help
```

Expected: both commands print argparse help and exit 0 without reading `scripts/aiopnsense.env`.

- [ ] **Step 6: Smoke-test endpoint listing without live credentials**

Run:

```bash
./scripts/aiopnsense_dump.py --list
```

Expected: JSON list of supported endpoint names and public method names prints to stdout and exits 0 without reading `scripts/aiopnsense.env`.

- [ ] **Step 7: Inspect final diff**

Run:

```bash
git status --short --branch
git diff --stat HEAD~4..HEAD
git diff --check HEAD~4..HEAD
```

Expected:
- Current branch remains `Add-live-test-scripts`.
- Only script, script-test, env example, and `.gitignore` changes are present.
- No whitespace errors.

## Self-Review

- Spec coverage:
  - Two scripts under `scripts/`: Tasks 2 and 3.
  - Shared same-folder env file: Task 1 helper and Task 4 env example.
  - `aiopnsense.env` ignored: Task 4.
  - Env contains OPNsense API key and secret: Task 4 example and Task 1 config loader.
  - Optional output file in addition to screen: Task 1 `write_output`, Tasks 2 and 3 CLI args.
  - aiopnsense public endpoint dump with menu or endpoint option: Task 2.
  - Stream endpoint fixed duration default 30 seconds: Task 2.
  - Arbitrary raw OPNsense endpoint with method and POST payload: Task 3.
  - No live OPNsense calls in tests: all tests use pure helpers or fakes.
- Placeholder scan:
  - The plan contains no deferred implementation markers and every code-changing step includes concrete code or exact file content.
- Type consistency:
  - `LiveConfig`, `LiveConfigError`, `load_live_config`, `write_output`, and `create_client` are defined in Task 1 before Tasks 2 and 3 import them.
  - `EndpointSpec`, `ENDPOINTS`, `run_endpoint`, and `choose_endpoint_from_menu` are defined in Task 2 before tests assert them.
  - `normalize_endpoint`, `load_payload`, and `call_api` are defined in Task 3 before tests assert them.
