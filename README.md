# aiopnsense
[![PyPI Downloads][pypi-downloads-shield]](https://pypi.org/project/aiopnsense/)
[![GitHub Release][releases-shield]][releases]
[![GitHub Release Date][release-date-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![Coverage][coverage-shield]][coverage]
[![Documentation][docs-shield]][docs]
[![License][license-shield]](LICENSE)

`aiopnsense` is an async Python client library for [OPNsense](https://opnsense.org).

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

## Requirements

* ### Requires OPNsense Firmware >= 25.1

* #### Recommended OPNsense Firmware >= 26.1.1

  - For firmware < 26.1.1, the `Firewall and NAT` methods will return empty data.

## Documentation

- Read the Docs: <https://aiopnsense.readthedocs.io>

## Origin and Purpose

`aiopnsense` was initially extracted from the [`hass-opnsense`](https://github.com/travisghansen/hass-opnsense) integration. It is primarily for use as an external dependency by [Home Assistant](https://www.home-assistant.io) for its [OPNsense Integration](https://www.home-assistant.io/integrations/opnsense).

[commits-shield]: https://img.shields.io/github/last-commit/Snuffy2/aiopnsense?style=for-the-badge
[commits]: https://github.com/Snuffy2/aiopnsense/commits/main
[license-shield]: https://img.shields.io/github/license/Snuffy2/aiopnsense.svg?style=for-the-badge
[docs]: https://aiopnsense.readthedocs.io
[docs-shield]: https://app.readthedocs.org/projects/aiopnsense/badge/?version=stable&style=for-the-badge
[release-date-shield]: https://img.shields.io/github/release-date/Snuffy2/aiopnsense?display_date=published_at&style=for-the-badge
[releases-shield]: https://img.shields.io/github/v/release/Snuffy2/aiopnsense?style=for-the-badge
[releases]: https://github.com/Snuffy2/aiopnsense/releases
[coverage]: https://htmlpreview.github.io/?https://github.com/Snuffy2/aiopnsense/blob/python-coverage-comment-action-data/htmlcov/index.html
[coverage-shield]: https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FSnuffy2%2Faiopnsense%2Fpython-coverage-comment-action-data%2Fendpoint.json&style=for-the-badge
[pypi-downloads-shield]: https://img.shields.io/pypi/dm/aiopnsense?style=for-the-badge
