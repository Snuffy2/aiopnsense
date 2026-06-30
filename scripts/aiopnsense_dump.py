#!/usr/bin/env python3
"""Dump selected live OPNsense endpoint payloads as JSON."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _reexec_with_repo_venv() -> None:
    """Re-run the script with the repo virtualenv when launched directly."""
    if os.environ.get("AIOPNSENSE_LIVE_SCRIPT_BOOTSTRAPPED") == "1":
        return

    venv_dir = Path(__file__).resolve().parents[1] / ".venv"
    venv_python = venv_dir / "bin" / "python"
    if not venv_python.exists() or Path(sys.prefix).resolve() == venv_dir.resolve():
        return

    os.environ["AIOPNSENSE_LIVE_SCRIPT_BOOTSTRAPPED"] = "1"
    os.execv(str(venv_python), [str(venv_python), __file__, *sys.argv[1:]])


if __name__ == "__main__":
    _reexec_with_repo_venv()

import aiohttp  # noqa: E402
from aiopnsense.exceptions import OPNsenseError  # noqa: E402

_common = importlib.import_module("_opnsense_live_common")
DEFAULT_ENV_FILE = _common.DEFAULT_ENV_FILE
LiveConfigError = _common.LiveConfigError
create_client = _common.create_client
load_live_config = _common.load_live_config
write_output = _common.write_output

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class EndpointSpec:
    """Specification for a non-mutating dump endpoint."""

    method_name: str
    warning: str | None = None


ENDPOINTS: dict[str, EndpointSpec] = {
    "arp_table": EndpointSpec("get_arp_table"),
    "carp": EndpointSpec("get_carp"),
    "certificates": EndpointSpec("get_certificates"),
    "device_unique_id": EndpointSpec("get_device_unique_id"),
    "dhcp_leases": EndpointSpec("get_dhcp_leases"),
    "firewall": EndpointSpec("get_firewall"),
    "firmware_update_info": EndpointSpec(
        "get_firmware_update_info",
        warning="May trigger a firmware check if OPNsense cached firmware status is stale.",
    ),
    "gateways": EndpointSpec("get_gateways"),
    "host_firmware_version": EndpointSpec("get_host_firmware_version"),
    "interfaces": EndpointSpec("get_interfaces"),
    "interface_traffic": EndpointSpec("get_interface_traffic"),
    "interface_traffic_stream": EndpointSpec("stream_interface_traffic"),
    "notices": EndpointSpec("get_notices"),
    "openvpn": EndpointSpec("get_openvpn"),
    "services": EndpointSpec("get_services"),
    "smart": EndpointSpec("get_smart"),
    "speedtest": EndpointSpec("get_speedtest"),
    "system_info": EndpointSpec("get_system_info"),
    "telemetry": EndpointSpec("get_telemetry"),
    "unbound_blocklist": EndpointSpec("get_unbound_blocklist"),
    "upgrade_status": EndpointSpec("upgrade_status"),
    "vnstat": EndpointSpec("get_vnstat"),
    "wireguard": EndpointSpec("get_wireguard"),
}


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser for the aiopnsense endpoint dumper."""
    parser = argparse.ArgumentParser(description="Dump live OPNsense endpoint JSON payloads.")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help="Path to the env file with live credentials.",
    )
    parser.add_argument(
        "--endpoint",
        choices=sorted(ENDPOINTS),
        help="Endpoint to dump.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print all endpoint metadata as JSON and exit.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path to save output JSON.",
    )
    parser.add_argument(
        "--stream-seconds",
        type=float,
        default=30.0,
        help="Streaming duration in seconds for interface traffic stream.",
    )
    return parser


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the dump script.

    Args:
        args: Optional argument override for testability.

    Returns:
        Parsed arguments.
    """
    parser = build_parser()
    return parser.parse_args(args=args)


def list_endpoints() -> list[dict[str, Any]]:
    """Return sorted endpoint metadata entries."""
    return [
        {"endpoint": endpoint_name, "method": spec.method_name, "warning": spec.warning}
        for endpoint_name, spec in sorted(ENDPOINTS.items())
    ]


def choose_endpoint_from_menu(endpoint_names: list[str]) -> str:
    """Prompt for a 1-based endpoint selection and return the chosen name.

    Args:
        endpoint_names: Stable, sorted endpoint names.

    Returns:
        Endpoint name selected by the user.

    Raises:
        SystemExit: Raised for non-integer or out-of-range selections.
    """
    for index, endpoint_name in enumerate(endpoint_names, 1):
        endpoint_spec = ENDPOINTS[endpoint_name]
        warning_suffix = f" [{endpoint_spec.warning}]" if endpoint_spec.warning else ""
        print(f"{index}. {endpoint_name} -> {endpoint_spec.method_name}{warning_suffix}")
    try:
        selection = input("Select endpoint by number: ").strip()
        index = int(selection)
    except (ValueError, EOFError, KeyboardInterrupt) as err:
        raise SystemExit("Invalid endpoint selection") from err
    if index < 1 or index > len(endpoint_names):
        raise SystemExit("Invalid endpoint selection")
    return endpoint_names[index - 1]


async def run_endpoint(client: Any, endpoint_name: str, stream_seconds: float) -> dict[str, Any]:
    """Run a single endpoint and return a normalized dump payload."""
    spec = ENDPOINTS[endpoint_name]
    method_name = spec.method_name
    method = getattr(client, method_name)
    if method_name == "stream_interface_traffic":
        data: list[Any] = []
        if stream_seconds <= 0:
            return {
                "endpoint": endpoint_name,
                "method": method_name,
                "warning": spec.warning,
                "data": data,
            }

        deadline = time.monotonic() + stream_seconds
        stream = method(poll_interval=1)
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    sample = await asyncio.wait_for(anext(stream), timeout=remaining)
                except TimeoutError:
                    break
                except StopAsyncIteration:
                    break
                data.append(sample)
        finally:
            await stream.aclose()
        return {
            "endpoint": endpoint_name,
            "method": method_name,
            "warning": spec.warning,
            "data": data,
        }
    else:
        data = await method()

    return {
        "endpoint": endpoint_name,
        "method": method_name,
        "warning": spec.warning,
        "data": data,
    }


async def async_main(argv: list[str] | None = None) -> int:
    """Run the dump command.

    Args:
        argv: Optional argument list for testability.

    Returns:
        Exit status code.
    """
    args = parse_args(argv)

    if args.list:
        write_output(list_endpoints(), args.output)
        return 0

    endpoint = args.endpoint
    if endpoint is None:
        endpoint = choose_endpoint_from_menu(sorted(ENDPOINTS))

    config = load_live_config(args.env_file)
    async with aiohttp.ClientSession() as session:
        client = create_client(config, session)
        primary_error: BaseException | None = None
        try:
            await client.validate()
            result = await run_endpoint(client, endpoint, args.stream_seconds)
            write_output(result, args.output)
        except (OPNsenseError, aiohttp.ClientError, TimeoutError, RuntimeError, OSError) as err:
            primary_error = err
            raise
        finally:
            try:
                await client.async_close()
            except (OPNsenseError, aiohttp.ClientError, TimeoutError, RuntimeError) as err:
                if primary_error is None:
                    raise
                _LOGGER.debug("Failed to close OPNsense client after primary failure", exc_info=err)
    return 0


def main() -> int:
    """CLI entrypoint."""
    try:
        return asyncio.run(async_main())
    except (LiveConfigError, OPNsenseError, aiohttp.ClientError, TimeoutError) as err:
        raise SystemExit(f"{type(err).__name__}: {err}") from err


if __name__ == "__main__":
    raise SystemExit(main())
