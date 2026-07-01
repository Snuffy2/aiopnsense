"""Tests for the ``aiopnsense_dump`` live script."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
from pathlib import Path
from types import ModuleType
import argparse
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

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
    stream_interface_traffic_calls: int
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
        self.stream_interface_traffic_calls = 0
        self.stream_poll_intervals = []
        self.stream_samples = stream_samples

    def stream_interface_traffic(self, poll_interval: int = 1) -> Any:
        """Return a bounded async sequence for streaming endpoint tests."""
        self.stream_interface_traffic_calls += 1
        self.stream_poll_intervals.append(poll_interval)

        async def _stream() -> Any:
            for sample in self.stream_samples:
                yield sample

        return _stream()


class FakeClientSession:
    """Async context manager session stub used by async_main tests."""

    def __init__(self) -> None:
        """Create awaitable enter/exit methods."""
        self.enter = AsyncMock(return_value=self)
        self.exit = AsyncMock(return_value=None)

    async def __aenter__(self) -> "FakeClientSession":
        return await self.enter()

    async def __aexit__(self, *_args: Any) -> None:
        await self.exit()


def test_endpoint_registry_contains_expected_read_only_entries() -> None:
    """Registry matches the exact read-only contract required by the task."""
    module = load_dump_module()
    entries = module.list_endpoints()
    endpoint_map = {entry["endpoint"]: (entry["method"], entry["warning"]) for entry in entries}
    expected_entries = {
        "arp_table": ("get_arp_table", None),
        "carp": ("get_carp", None),
        "certificates": ("get_certificates", None),
        "device_unique_id": ("get_device_unique_id", None),
        "dhcp_leases": ("get_dhcp_leases", None),
        "firewall": ("get_firewall", None),
        "firmware_update_info": (
            "get_firmware_update_info",
            "May trigger a firmware check if OPNsense cached firmware status is stale.",
        ),
        "gateways": ("get_gateways", None),
        "host_firmware_version": ("get_host_firmware_version", None),
        "interfaces": ("get_interfaces", None),
        "interface_traffic": ("get_interface_traffic", None),
        "interface_traffic_stream": ("stream_interface_traffic", None),
        "notices": ("get_notices", None),
        "openvpn": ("get_openvpn", None),
        "services": ("get_services", None),
        "smart": ("get_smart", None),
        "speedtest": ("get_speedtest", None),
        "system_info": ("get_system_info", None),
        "telemetry": ("get_telemetry", None),
        "unbound_blocklist": ("get_unbound_blocklist", None),
        "upgrade_status": ("upgrade_status", None),
        "vnstat": ("get_vnstat", None),
        "wireguard": ("get_wireguard", None),
    }

    assert endpoint_map == expected_entries


def test_build_parser_returns_argparse_parser_for_docs() -> None:
    """The dump script exposes a zero-argument parser factory for Sphinx docs."""
    module = load_dump_module()

    parser = module.build_parser()

    assert isinstance(parser, argparse.ArgumentParser)
    parsed = parser.parse_args(["--endpoint", "system_info"])
    assert parsed.endpoint == "system_info"


def test_build_parser_documents_endpoint_choices_in_option_help() -> None:
    """Endpoint choices render in option help instead of bloating usage."""
    module = load_dump_module()
    parser = module.build_parser()

    help_text = " ".join(parser.format_help().split())

    assert "--endpoint ENDPOINT" in help_text
    assert "--endpoint {arp_table" not in help_text
    assert "Available endpoints: arp_table, carp" in help_text
    assert "wireguard." in help_text


def test_parse_args_resolves_documented_default_env_file() -> None:
    """Runtime parsing resolves the documented env file path to the script directory."""
    module = load_dump_module()

    parsed = module.parse_args(["--endpoint", "system_info"])

    assert parsed.env_file == module.DEFAULT_ENV_FILE


def test_build_parser_documents_relative_default_env_file() -> None:
    """Sphinx parser docs should show the user-facing default env path."""
    module = load_dump_module()
    parser = module.build_parser()

    parsed = parser.parse_args(["--endpoint", "system_info"])

    assert parsed.env_file == Path("scripts/aiopnsense.env")


def test_choose_endpoint_from_menu_prints_method_and_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Menu rows include endpoint-to-method mapping and warning annotation."""
    module = load_dump_module()
    monkeypatch.setattr("builtins.input", lambda _prompt="": "3")

    selected = module.choose_endpoint_from_menu(["system_info", "services", "firmware_update_info"])

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "1. system_info -> get_system_info"
    assert (
        lines[2]
        == "3. firmware_update_info -> get_firmware_update_info [May trigger a firmware check if OPNsense cached firmware status is stale.]"
    )
    assert selected == "firmware_update_info"


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


@pytest.mark.parametrize("prompt_error", [EOFError, KeyboardInterrupt])
def test_choose_endpoint_from_menu_rejects_prompt_cancellation(
    prompt_error: type[BaseException], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prompt cancellation exits through the same controlled invalid-selection path."""
    module = load_dump_module()

    def raise_prompt_error(_prompt: str = "") -> None:
        """Raise the configured prompt cancellation error."""
        raise prompt_error

    monkeypatch.setattr("builtins.input", raise_prompt_error)

    with pytest.raises(SystemExit, match="Invalid endpoint selection"):
        module.choose_endpoint_from_menu(["system_info", "services", "smart"])


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
    """Firmware check warnings appear in the dump payload."""
    module = load_dump_module()
    client = FakeClient(firmware_update_return={"firmware": "ready"})

    result = await module.run_endpoint(client, "firmware_update_info", stream_seconds=30.0)

    assert (
        result["warning"]
        == "May trigger a firmware check if OPNsense cached firmware status is stale."
    )
    assert result["data"] == {"firmware": "ready"}


@pytest.mark.asyncio
async def test_run_endpoint_collects_available_stream_samples_quickly() -> None:
    """Streaming endpoint collects all immediately-available samples quickly."""
    module = load_dump_module()
    stream_samples = (
        {"sample": 1},
        {"sample": 2},
        {"sample": 3},
        {"sample": 4},
        {"sample": 5},
    )
    client = FakeClient(stream_samples=stream_samples)

    result = await module.run_endpoint(client, "interface_traffic_stream", stream_seconds=2.0)

    assert client.stream_poll_intervals == [1]
    assert result["endpoint"] == "interface_traffic_stream"
    assert result["data"] == [
        {"sample": 1},
        {"sample": 2},
        {"sample": 3},
        {"sample": 4},
        {"sample": 5},
    ]


@pytest.mark.asyncio
async def test_run_endpoint_returns_empty_when_stream_seconds_is_zero() -> None:
    """Non-positive stream duration should return an empty sample list without polling."""
    module = load_dump_module()
    stream_samples = ({"sample": 1}, {"sample": 2})
    client = FakeClient(stream_samples=stream_samples)

    result = await module.run_endpoint(client, "interface_traffic_stream", stream_seconds=0.0)

    assert client.stream_interface_traffic_calls == 0
    assert result["data"] == []
    assert result["warning"] is None


@pytest.mark.asyncio
async def test_run_endpoint_collects_empty_stream_with_short_timeout() -> None:
    """A stalled iterator respects timeout and closes cleanly."""
    module = load_dump_module()
    client = FakeClient(stream_samples=())

    class StalledStream:
        """Slow iterator that supports explicit close."""

        def __init__(self) -> None:
            """Initialize the stalled stream state."""
            self.closed = False
            self.opened = False

        def __aiter__(self) -> "StalledStream":
            """Return the stream iterator."""
            return self

        async def __anext__(self) -> dict[str, Any]:
            """Wait briefly and then end iteration."""
            await module.asyncio.sleep(1.0)
            raise StopAsyncIteration

        async def aclose(self) -> None:
            """Mark the stream as closed."""
            self.closed = True

    stream = StalledStream()

    def fake_stream(poll_interval: int = 1) -> StalledStream:
        """Expose the stalled stream with capture for the polling interval."""
        del poll_interval
        client.stream_interface_traffic_calls += 1
        stream.opened = True
        return stream

    original_stream = client.stream_interface_traffic
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(client, "stream_interface_traffic", fake_stream)

    start = module.time.monotonic()
    result = await module.run_endpoint(client, "interface_traffic_stream", stream_seconds=0.1)
    elapsed = module.time.monotonic() - start

    assert client.stream_interface_traffic_calls == 1
    assert stream.opened
    assert stream.closed
    assert elapsed < 0.5
    assert result["data"] == []
    monkeypatch.setattr(client, "stream_interface_traffic", original_stream)


@pytest.mark.asyncio
async def test_run_endpoint_propagates_stream_timeout_error_and_closes_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Iterator TimeoutError must propagate and the stream must still close."""
    module = load_dump_module()

    class TimeoutStream:
        """Slow iterator that fails with stream-level TimeoutError."""

        def __init__(self) -> None:
            """Initialize the timeout stream state."""
            self.closed = False
            self.opened = False

        def __aiter__(self) -> TimeoutStream:
            """Return the stream iterator."""
            return self

        async def __anext__(self) -> dict[str, Any]:
            """Raise the simulated stream timeout."""
            raise TimeoutError("stream-level timeout")

        async def aclose(self) -> None:
            """Mark the stream as closed."""
            self.closed = True

    stream = TimeoutStream()

    client = FakeClient(stream_samples=())
    original_stream = client.stream_interface_traffic

    def fake_stream(poll_interval: int = 1) -> TimeoutStream:
        """Expose the timeout stream with capture for the polling interval."""
        del poll_interval
        client.stream_interface_traffic_calls += 1
        stream.opened = True
        return stream

    monkeypatch.setattr(client, "stream_interface_traffic", fake_stream)

    with pytest.raises(TimeoutError, match="stream-level timeout"):
        await module.run_endpoint(client, "interface_traffic_stream", stream_seconds=5.0)

    assert client.stream_interface_traffic_calls == 1
    assert stream.opened
    assert stream.closed
    monkeypatch.setattr(client, "stream_interface_traffic", original_stream)


@pytest.mark.asyncio
async def test_run_endpoint_stream_error_beats_close_error(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Primary stream errors preserve when stream close fails too."""
    module = load_dump_module()

    class TimeoutStream:
        """Slow iterator that errors and cannot be closed."""

        def __init__(self) -> None:
            """Initialize the timeout stream state."""
            self.closed = False
            self.opened = False

        def __aiter__(self) -> TimeoutStream:
            """Return the stream iterator."""
            return self

        async def __anext__(self) -> dict[str, Any]:
            """Raise the simulated stream timeout."""
            raise TimeoutError("stream-level timeout")

        async def aclose(self) -> None:
            """Raise when the stream cannot be closed cleanly."""
            self.closed = True
            raise RuntimeError("close failed")

    stream = TimeoutStream()

    client = FakeClient(stream_samples=())

    def fake_stream(poll_interval: int = 1) -> TimeoutStream:
        """Expose the failing-close stream for polling capture."""
        del poll_interval
        client.stream_interface_traffic_calls += 1
        stream.opened = True
        return stream

    monkeypatch.setattr(client, "stream_interface_traffic", fake_stream)

    with (
        caplog.at_level(logging.DEBUG, logger=module._LOGGER.name),
        pytest.raises(TimeoutError, match="stream-level timeout"),
    ):
        await module.run_endpoint(client, "interface_traffic_stream", stream_seconds=5.0)

    assert any(
        "Failed to close stream after primary stream error" in record.message
        for record in caplog.records
    )
    assert client.stream_interface_traffic_calls == 1
    assert stream.opened
    assert stream.closed


@pytest.mark.asyncio
async def test_async_main_list_uses_write_output_and_skips_env_load(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--list uses write_output and does not load live config."""
    module = load_dump_module()
    load_live_config = MagicMock(
        side_effect=AssertionError("load_live_config should not be called")
    )
    write_calls: list[tuple[Any, Path | None]] = []
    original_write_output = module.write_output

    monkeypatch.setattr(module, "load_live_config", load_live_config)

    def spy_write_output(payload: Any, output: Path | None) -> None:
        """Capture payload and output path while preserving write behavior."""
        write_calls.append((payload, output))
        original_write_output(payload, output)

    monkeypatch.setattr(module, "write_output", spy_write_output)
    output_path = tmp_path / "endpoints.json"

    exit_code = await module.async_main(["--list", "--output", str(output_path)])

    assert load_live_config.call_count == 0
    assert exit_code == 0
    assert write_calls == [(module.list_endpoints(), output_path)]
    assert output_path.exists()

    rendered = json.loads(output_path.read_text(encoding="utf-8"))
    assert rendered == module.list_endpoints()

    captured = json.loads(capsys.readouterr().out.strip())
    assert captured == module.list_endpoints()


@pytest.mark.asyncio
async def test_async_main_returns_zero_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normal execution path returns zero from async_main."""
    module = load_dump_module()
    config = object()
    session = FakeClientSession()
    client = FakeClient()

    monkeypatch.setattr(module, "load_live_config", MagicMock(return_value=config))
    monkeypatch.setattr(module.aiohttp, "ClientSession", lambda: session)
    monkeypatch.setattr(module, "create_client", lambda _config, _session: client)
    monkeypatch.setattr(module, "run_endpoint", AsyncMock(return_value={"endpoint": "system_info"}))
    monkeypatch.setattr(module, "write_output", MagicMock())

    exit_code = await module.async_main(["--endpoint", "system_info"])

    assert exit_code == 0
    session.enter.assert_awaited_once()
    session.exit.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_main_wires_options_into_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Endpoint, env file, output file, and stream seconds are passed through."""
    module = load_dump_module()
    config = object()
    session = FakeClientSession()
    created: dict[str, Any] = {}
    client = FakeClient()
    endpoint_result = {
        "endpoint": "system_info",
        "method": "get_system_info",
        "warning": None,
        "data": {"system": "running"},
    }
    run_calls: list[tuple[object, str, float]] = []
    write_calls: list[tuple[dict[str, Any], Path | None]] = []

    monkeypatch.setattr(module, "load_live_config", MagicMock(return_value=config))
    monkeypatch.setattr(module.aiohttp, "ClientSession", lambda: session)

    def fake_create_client(config_value: object, session_value: object) -> FakeClient:
        """Capture the resolved dependencies from async_main wiring."""
        created["cfg"] = config_value
        created["session"] = session_value
        return client

    async def fake_run_endpoint(
        client_arg: Any, endpoint_name: str, stream_seconds: float
    ) -> dict[str, Any]:
        """Record the endpoint wiring and return the canned result."""
        run_calls.append((client_arg, endpoint_name, stream_seconds))
        return endpoint_result

    def fake_write_output(payload: dict[str, Any], output: Path | None) -> None:
        """Capture output write intent for assertable side effects."""
        write_calls.append((payload, output))

    monkeypatch.setattr(module, "create_client", fake_create_client)
    monkeypatch.setattr(module, "run_endpoint", fake_run_endpoint)
    monkeypatch.setattr(module, "write_output", fake_write_output)

    env_path = Path("/tmp/custom.env")
    output_path = Path("/tmp/dump.json")

    exit_code = await module.async_main(
        [
            "--endpoint",
            "system_info",
            "--env-file",
            str(env_path),
            "--output",
            str(output_path),
            "--stream-seconds",
            "9.25",
        ]
    )
    assert exit_code == 0
    module.load_live_config.assert_called_once_with(env_path)
    assert created["cfg"] is config
    assert created["session"] is session
    assert run_calls == [(client, "system_info", 9.25)]
    assert write_calls == [(endpoint_result, output_path)]
    client.validate.assert_awaited_once()
    client.async_close.assert_awaited_once()
    session.enter.assert_awaited_once()
    session.exit.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_main_closes_client_when_run_endpoint_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``async_close`` is still awaited when run_endpoint raises."""
    module = load_dump_module()
    config = object()
    session = FakeClientSession()
    client = FakeClient()

    monkeypatch.setattr(module, "load_live_config", MagicMock(return_value=config))
    monkeypatch.setattr(module.aiohttp, "ClientSession", lambda: session)
    monkeypatch.setattr(module, "create_client", lambda _config, _session: client)
    monkeypatch.setattr(module, "run_endpoint", AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(module, "write_output", MagicMock())

    with pytest.raises(RuntimeError, match="boom"):
        await module.async_main(["--endpoint", "system_info"])

    client.validate.assert_awaited_once()
    client.async_close.assert_awaited_once()
    module.write_output.assert_not_called()
    session.enter.assert_awaited_once()
    session.exit.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_main_preserves_run_endpoint_error_when_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``async_close`` failures do not mask the primary endpoint error."""
    module = load_dump_module()
    config = object()
    session = FakeClientSession()
    client = FakeClient()
    client.async_close = AsyncMock(side_effect=RuntimeError("close failed"))

    monkeypatch.setattr(module, "load_live_config", MagicMock(return_value=config))
    monkeypatch.setattr(module.aiohttp, "ClientSession", lambda: session)
    monkeypatch.setattr(module, "create_client", lambda _config, _session: client)
    monkeypatch.setattr(module, "run_endpoint", AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(module, "write_output", MagicMock())

    with pytest.raises(RuntimeError, match="boom"):
        await module.async_main(["--endpoint", "system_info"])

    client.validate.assert_awaited_once()
    client.async_close.assert_awaited_once()
    module.write_output.assert_not_called()
    session.enter.assert_awaited_once()
    session.exit.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_main_preserves_primary_oserror_when_close_raises_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`OSError` from the endpoint remains visible when close also raises `OSError`."""
    module = load_dump_module()
    config = object()
    session = FakeClientSession()
    client = FakeClient()
    client.async_close = AsyncMock(side_effect=OSError("close failed"))

    monkeypatch.setattr(module, "load_live_config", MagicMock(return_value=config))
    monkeypatch.setattr(module.aiohttp, "ClientSession", lambda: session)
    monkeypatch.setattr(module, "create_client", lambda _config, _session: client)
    monkeypatch.setattr(module, "run_endpoint", AsyncMock(side_effect=OSError("primary failed")))
    monkeypatch.setattr(module, "write_output", MagicMock())

    with pytest.raises(OSError, match="primary failed"):
        await module.async_main(["--endpoint", "system_info"])

    client.validate.assert_awaited_once()
    client.async_close.assert_awaited_once()
    module.write_output.assert_not_called()
    session.enter.assert_awaited_once()
    session.exit.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_main_preserves_cancelled_error_when_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``async_close`` failures do not mask cancellation."""
    module = load_dump_module()
    config = object()
    session = FakeClientSession()
    client = FakeClient()
    client.async_close = AsyncMock(side_effect=RuntimeError("close failed"))

    monkeypatch.setattr(module, "load_live_config", MagicMock(return_value=config))
    monkeypatch.setattr(module.aiohttp, "ClientSession", lambda: session)
    monkeypatch.setattr(module, "create_client", lambda _config, _session: client)
    monkeypatch.setattr(module, "run_endpoint", AsyncMock(side_effect=asyncio.CancelledError()))
    monkeypatch.setattr(module, "write_output", MagicMock())

    with pytest.raises(asyncio.CancelledError):
        await module.async_main(["--endpoint", "system_info"])

    client.validate.assert_awaited_once()
    client.async_close.assert_awaited_once()
    module.write_output.assert_not_called()
    session.enter.assert_awaited_once()
    session.exit.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_main_closes_client_when_validate_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``async_close`` is still awaited when ``validate`` raises."""
    module = load_dump_module()
    config = object()
    session = FakeClientSession()
    client = FakeClient()
    client.validate = AsyncMock(side_effect=RuntimeError("validation failed"))

    monkeypatch.setattr(module, "load_live_config", MagicMock(return_value=config))
    monkeypatch.setattr(module.aiohttp, "ClientSession", lambda: session)
    monkeypatch.setattr(module, "create_client", lambda _config, _session: client)
    monkeypatch.setattr(module, "run_endpoint", AsyncMock())
    monkeypatch.setattr(module, "write_output", MagicMock())

    with pytest.raises(RuntimeError, match="validation failed"):
        await module.async_main(["--endpoint", "system_info"])

    client.validate.assert_awaited_once()
    client.async_close.assert_awaited_once()
    module.run_endpoint.assert_not_awaited()
    module.write_output.assert_not_called()
    session.enter.assert_awaited_once()
    session.exit.assert_awaited_once()


def test_main_returns_zero_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """``main`` returns 0 when async_main completes successfully."""
    module = load_dump_module()

    monkeypatch.setattr(module, "async_main", AsyncMock(return_value=0))

    assert module.main() == 0


def test_main_turns_live_config_error_into_system_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``main`` converts ``LiveConfigError`` from async_main into ``SystemExit``."""
    module = load_dump_module()

    async def raise_config_error() -> None:
        raise module.LiveConfigError("bad config")

    monkeypatch.setattr(module, "async_main", raise_config_error)

    with pytest.raises(SystemExit) as excinfo:
        module.main()

    assert "bad config" in str(excinfo.value)


def test_main_turns_opnsense_error_into_system_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``main`` converts aiopnsense live failures into concise ``SystemExit``."""
    module = load_dump_module()

    async def raise_opnsense_error() -> None:
        raise module.OPNsenseError("connection failed")

    monkeypatch.setattr(module, "async_main", raise_opnsense_error)

    with pytest.raises(SystemExit) as excinfo:
        module.main()

    assert "OPNsenseError: connection failed" in str(excinfo.value)


def test_main_turns_aiohttp_client_error_into_system_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``main`` converts aiohttp live failures into concise ``SystemExit``."""
    module = load_dump_module()

    async def raise_client_error() -> None:
        raise module.aiohttp.ClientConnectionError("connection failed")

    monkeypatch.setattr(module, "async_main", raise_client_error)

    with pytest.raises(SystemExit) as excinfo:
        module.main()

    assert "ClientConnectionError: connection failed" in str(excinfo.value)


def test_main_turns_runtime_error_into_system_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``main`` converts runtime failures into concise ``SystemExit``."""
    module = load_dump_module()

    async def raise_runtime_error() -> None:
        raise RuntimeError("runtime validation failed")

    monkeypatch.setattr(module, "async_main", raise_runtime_error)

    with pytest.raises(SystemExit) as excinfo:
        module.main()

    assert "RuntimeError: runtime validation failed" in str(excinfo.value)


def test_main_turns_timeout_error_into_system_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``main`` converts timeout failures into concise ``SystemExit``."""
    module = load_dump_module()

    async def raise_timeout_error() -> None:
        raise TimeoutError("request timed out")

    monkeypatch.setattr(module, "async_main", raise_timeout_error)

    with pytest.raises(SystemExit) as excinfo:
        module.main()

    assert "TimeoutError: request timed out" in str(excinfo.value)


def test_main_converts_os_error_to_system_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``main`` converts I/O write failures into concise ``SystemExit``."""
    module = load_dump_module()

    async def raise_os_error() -> None:
        raise OSError("output is unavailable")

    monkeypatch.setattr(module, "async_main", raise_os_error)

    with pytest.raises(SystemExit) as excinfo:
        module.main()

    assert "OSError: output is unavailable" in str(excinfo.value)


def test_reexec_with_repo_venv_uses_local_python(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bootstrap re-exec uses the repo venv when launched outside it."""
    module = load_dump_module()
    calls: list[tuple[str, list[str]]] = []
    script_path = tmp_path / "scripts" / "aiopnsense_dump.py"
    script_path.parent.mkdir()
    script_path.write_text("", encoding="utf-8")
    repo_venv = tmp_path / ".venv"
    expected_python = repo_venv / "bin" / "python"
    expected_python.parent.mkdir(parents=True)
    expected_python.write_text("", encoding="utf-8")

    monkeypatch.delenv("AIOPNSENSE_LIVE_SCRIPT_BOOTSTRAPPED", raising=False)
    monkeypatch.setattr(module, "__file__", str(script_path))
    monkeypatch.setattr(module.sys, "prefix", "/usr/local")
    monkeypatch.setattr(module.sys, "argv", ["aiopnsense_dump.py", "--help"])
    monkeypatch.setattr(module.os, "execv", lambda path, args: calls.append((path, args)))

    module._reexec_with_repo_venv()

    assert module.os.environ["AIOPNSENSE_LIVE_SCRIPT_BOOTSTRAPPED"] == "1"
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
    module = load_dump_module()
    repo_venv = Path(module.__file__).resolve().parents[1] / ".venv"
    execv = MagicMock()

    monkeypatch.delenv("AIOPNSENSE_LIVE_SCRIPT_BOOTSTRAPPED", raising=False)
    monkeypatch.setattr(module.sys, "prefix", str(repo_venv))
    monkeypatch.setattr(module.os, "execv", execv)

    module._reexec_with_repo_venv()

    execv.assert_not_called()
