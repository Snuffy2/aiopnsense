"""Tests for the ``aiopnsense_dump`` live script."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
import sys
from typing import Any
from unittest.mock import AsyncMock

import pytest


def load_dump_module() -> ModuleType:
    """Load the script as an importable module for direct unit testing."""
    module_path = Path(__file__).parents[1] / "scripts" / "aiopnsense_dump.py"
    sys.path.insert(0, str(module_path.parent))
    spec = importlib.util.spec_from_file_location("aiopnsense_dump", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeClient:
    """Small async fake matching just the script-call surface."""

    validate: AsyncMock
    async_close: AsyncMock
    get_system_info: AsyncMock
    get_firmware_update_info: AsyncMock
    stream_poll_intervals: list[int]
    stream_samples: tuple[dict[str, Any], ...]

    def __init__(
        self,
        system_info_return: dict[str, Any] | None = None,
        firmware_update_return: dict[str, Any] | None = None,
        stream_samples: tuple[dict[str, Any], ...] = (),
    ) -> None:
        """Create the async fakes used by run_endpoint tests."""
        self.validate = AsyncMock()
        self.async_close = AsyncMock()
        self.get_system_info = AsyncMock(return_value=system_info_return or {"system": "ok"})
        self.get_firmware_update_info = AsyncMock(
            return_value=firmware_update_return or {"firmware": "ok"}
        )
        self.stream_poll_intervals = []
        self.stream_samples = stream_samples

    async def stream_interface_traffic(self, poll_interval: int = 1) -> Any:
        """Return a bounded async sequence for streaming endpoint tests."""
        self.stream_poll_intervals.append(poll_interval)
        for sample in self.stream_samples:
            yield sample


def test_endpoint_registry_contains_expected_read_only_entries() -> None:
    """Registry contains only read-only endpoints requested by the task."""
    module = load_dump_module()
    entries = module.list_endpoints()
    endpoint_map = {entry["endpoint"]: (entry["method"], entry["warning"]) for entry in entries}
    expected_methods = {
        "carp": "get_carp",
        "certificates": "get_certificates",
        "device_unique_id": "get_device_unique_id",
        "system_info": "get_system_info",
        "notices": "get_notices",
        "host_firmware_version": "get_host_firmware_version",
        "dhcp_leases": "get_dhcp_leases",
        "firewall": "get_firewall",
        "smart": "get_smart",
        "speedtest": "get_speedtest",
        "unbound_blocklist": "get_unbound_blocklist",
        "vnstat": "get_vnstat",
        "openvpn": "get_openvpn",
        "wireguard": "get_wireguard",
        "telemetry": "get_telemetry",
        "interfaces": "get_interfaces",
        "gateways": "get_gateways",
        "interface_traffic": "get_interface_traffic",
        "interface_traffic_stream": "stream_interface_traffic",
        "arp_table": "get_arp_table",
        "services": "get_services",
        "upgrade_status": "upgrade_status",
        "firmware_update_info": "get_firmware_update_info",
    }

    assert set(endpoint_map) == set(expected_methods)
    assert len(endpoint_map) == len(expected_methods)
    for endpoint, method_name in expected_methods.items():
        actual_method, warning = endpoint_map[endpoint]
        assert actual_method == method_name
        if endpoint == "firmware_update_info":
            assert warning is not None
            assert "firmware check" in warning
        else:
            assert warning is None


@pytest.mark.asyncio
async def test_run_endpoint_calls_public_method() -> None:
    """Non-stream endpoints call their mapped public no-arg method."""
    module = load_dump_module()
    client = FakeClient(system_info_return={"system": "running"})

    result = await module.run_endpoint(client, "system_info", stream_seconds=30.0)

    client.get_system_info.assert_awaited_once()
    assert result["endpoint"] == "system_info"
    assert result["method"] == "get_system_info"
    assert result["warning"] is None
    assert result["data"] == {"system": "running"}


@pytest.mark.asyncio
async def test_run_endpoint_includes_warning_for_refreshing_method() -> None:
    """Endpoints with cautions include warning text in the output payload."""
    module = load_dump_module()
    client = FakeClient(firmware_update_return={"firmware": "ready"})

    result = await module.run_endpoint(client, "firmware_update_info", stream_seconds=30.0)

    assert result["warning"] is not None
    assert "firmware check" in result["warning"]
    assert result["data"] == {"firmware": "ready"}


@pytest.mark.asyncio
async def test_run_endpoint_collects_stream_for_fixed_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Streaming endpoint collects samples until the provided deadline passes."""
    module = load_dump_module()
    stream_samples = (
        {"sample": 1},
        {"sample": 2},
        {"sample": 3},
        {"sample": 4},
        {"sample": 5},
    )
    client = FakeClient(stream_samples=stream_samples)
    state = {"value": 0.0}

    def fake_monotonic() -> float:
        state["value"] += 0.4
        return state["value"]

    monkeypatch.setattr(module.time, "monotonic", fake_monotonic)

    result = await module.run_endpoint(client, "interface_traffic_stream", stream_seconds=2.0)

    assert client.stream_poll_intervals == [1]
    assert result["endpoint"] == "interface_traffic_stream"
    assert result["data"] == [
        {"sample": 1},
        {"sample": 2},
        {"sample": 3},
        {"sample": 4},
    ]


def test_choose_endpoint_from_menu_accepts_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """Menu input of a 1-based number returns the selected endpoint."""
    module = load_dump_module()
    monkeypatch.setattr("builtins.input", lambda _prompt="": "2")

    selected = module.choose_endpoint_from_menu(["system_info", "services", "smart"])

    assert selected == "services"


@pytest.mark.parametrize("selection", ["0", "4", "abc"])
def test_choose_endpoint_from_menu_rejects_invalid_number(
    selection: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any invalid menu input should exit with a clear error."""
    module = load_dump_module()
    monkeypatch.setattr("builtins.input", lambda _prompt="": selection)

    with pytest.raises(SystemExit):
        module.choose_endpoint_from_menu(["system_info", "services", "smart"])


@pytest.mark.asyncio
async def test_async_main_list_does_not_load_env(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--list prints metadata and exits before config loading."""
    module = load_dump_module()
    monkeypatch.setattr(
        module, "load_live_config", lambda *_args: (_ for _ in ()).throw(RuntimeError("nope"))
    )

    await module.async_main(["--list"])

    captured = json.loads(capsys.readouterr().out.strip())
    assert {"endpoint": "system_info", "method": "get_system_info", "warning": None} in captured
