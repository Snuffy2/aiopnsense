# aiopnsense
[![PyPI Downloads][pypi-downloads-shield]](https://pypi.org/project/aiopnsense/)
[![GitHub Release][releases-shield]][releases]
[![GitHub Release Date][release-date-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![Coverage][coverage-shield]][coverage]
[![License][license-shield]](LICENSE)


`aiopnsense` is an async Python client library for [OPNsense](https://opnsense.org).

**Requires OPNsense Firmware 26.1.1+**

## What this library does

`aiopnsense` wraps supported OPNsense REST endpoints behind a single async client,
[`OPNsenseClient`](./aiopnsense/client.py). It is designed for applications that need to query router state or trigger supported OPNsense actions without manually building HTTP requests.

The client currently includes helpers for:

- system information, notices, certificates, CARP, Wake-on-LAN, reboot, and interface reloads
- firmware version checks, update status, and upgrade actions
- interface, gateway, CPU, memory, filesystem, and temperature telemetry
- DHCP lease and ARP table access
- firewall rules, NAT rules, alias toggling, and state killing
- service status lookup and service start/stop/restart operations
- Unbound blocklist management
- OpenVPN and WireGuard status plus VPN instance toggling
- vnStat metrics, captive portal vouchers, and speed test data

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install aiopnsense
```

## OPNsense documentation

- OPNsense API reference: <https://docs.opnsense.org/development/api.html>
- OPNsense API usage guide: <https://docs.opnsense.org/development/how-tos/api.html>
- Creating and maintaining API keys: <https://docs.opnsense.org/manual/how-tos/user-local.html#creating-and-maintaining-api-keys>

In practice, use the generated OPNsense API key as the `username` and the generated secret as the `password` when constructing `OPNsenseClient`.

## Simple usage

The client expects an existing `aiohttp.ClientSession`. Most applications create one session for the lifetime of the integration or service and reuse it for all requests.

Call `await client.validate()` when you want an explicit startup check before reusing a long-lived client. If you prefer context-manager lifecycle handling, `async with client:` validates on entry and calls `async_close()` on exit.

### Read system state and telemetry

```python
import asyncio
import aiohttp
from aiopnsense import OPNsenseClient

async def main() -> None:
    async with aiohttp.ClientSession() as session:
        client = OPNsenseClient(
            url="https://opnsense.example.com",
            username="YOUR_API_KEY",
            password="YOUR_API_SECRET",
            session=session,
        )
        await client.validate()

        system_info = await client.get_system_info()
        telemetry = await client.get_telemetry()

        print(f"Firewall name: {system_info.get('name')}")
        print(f"CPU telemetry: {telemetry.get('cpu')}")
        print(f"Filesystem telemetry: {telemetry.get('filesystems')}")

        await client.async_close()


asyncio.run(main())
```

### Check firmware or control a service with `async with`

```python
import asyncio
import aiohttp
from aiopnsense import OPNsenseClient

async def main() -> None:
    async with aiohttp.ClientSession() as session:
        async with OPNsenseClient(
            url="https://opnsense.example.com",
            username="YOUR_API_KEY",
            password="YOUR_API_SECRET",
            session=session,
            opts={"verify_ssl": True},
        ) as client:
            firmware = await client.get_firmware_update_info()
            services = await client.get_services()

            print(f"Current firmware: {firmware.get('product', {}).get('product_version')}")
            print(f"Available services: {[service.get('name') for service in services[:5]]}")

            restarted = await client.restart_service_if_running("unbound")
            print(f"Restarted unbound: {restarted}")

asyncio.run(main())
```

If the OPNsense router uses a private CA or self-signed certificate, pass `opts={"verify_ssl": False}`.

## Development

Install the package in editable mode with test dependencies and run:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install --group dev -e .
pytest
prek run --all-files
```

## Origin and Purpose

`aiopnsense` was initially extracted from the [`hass-opnsense`](https://github.com/travisghansen/hass-opnsense) integration. It is primarily for use as an external dependency by [Home Assistant](https://www.home-assistant.io) for its [OPNsense Integration](https://www.home-assistant.io/integrations/opnsense).

[commits-shield]: https://img.shields.io/github/last-commit/Snuffy2/aiopnsense?style=for-the-badge
[commits]: https://github.com/Snuffy2/aiopnsense/commits/main
[license-shield]: https://img.shields.io/github/license/Snuffy2/aiopnsense.svg?style=for-the-badge
[release-date-shield]: https://img.shields.io/github/release-date/Snuffy2/aiopnsense?display_date=published_at&style=for-the-badge
[releases-shield]: https://img.shields.io/github/v/release/Snuffy2/aiopnsense?style=for-the-badge
[releases]: https://github.com/Snuffy2/aiopnsense/releases
[coverage]: https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html
[coverage-shield]: https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FSnuffy2%2Faiopnsense%2Fpython-coverage-comment-action-data%2Fendpoint.json&style=for-the-badge
[pypi-downloads-shield]: https://img.shields.io/pypi/dm/aiopnsense?style=for-the-badge
