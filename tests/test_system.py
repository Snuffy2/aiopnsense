"""Tests for `aiopnsense.system`."""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock

import aiohttp
import pytest

from aiopnsense import OPNsenseClient
from tests.conftest import make_mock_session_client

ClientType = Callable[..., OPNsenseClient]


@pytest.mark.asyncio
async def test_get_system_info(make_client: ClientType) -> None:
    """Verify system info is returned from the diagnostics endpoint.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates system info retrieval behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_dict_get = AsyncMock(return_value={"name": "foo"})
        info = await client.get_system_info()
        assert info["name"] == "foo"
        client._safe_dict_get.assert_awaited_once_with("/api/diagnostics/system/system_information")
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("instances", "expected_id", "expected"),
    [
        (
            [
                {"is_physical": True, "macaddr_hw": "aa:bb:cc"},
                {"is_physical": True, "macaddr_hw": "aa:bb:cc"},
            ],
            None,
            "aa_bb_cc",
        ),
        ([{"is_physical": False}], None, None),
        (
            [
                {"is_physical": True, "macaddr_hw": "aa:bb:cc"},
                {"is_physical": True, "macaddr_hw": "bb:cc:dd"},
            ],
            "bb_cc_dd",
            "bb_cc_dd",
        ),
        (
            [
                {"is_physical": True, "macaddr_hw": "aa:bb:cc"},
                {"is_physical": True, "macaddr_hw": "bb:cc:dd"},
            ],
            "cc_dd_ee",
            "aa_bb_cc",
        ),
        (["invalid-entry", {"is_physical": True, "macaddr_hw": "aa:bb:cc"}], None, "aa_bb_cc"),
    ],
)
async def test_get_device_unique_id_variants(
    make_client: ClientType,
    instances: list[Any],
    expected_id: str | None,
    expected: str | None,
) -> None:
    """Verify device unique ID selection with expected IDs and malformed rows.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        instances (list[Any]): Interface rows returned by the mocked API.
        expected_id (str | None): Optional preferred unique ID candidate.
        expected (str | None): Expected normalized unique ID result.

    Returns:
        None: This test validates unique-ID selection and fallback behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_list_get = AsyncMock(return_value=instances)
        assert await client.get_device_unique_id(expected_id=expected_id) == expected
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_opnsense_timezone_parse_and_fallback(make_client: ClientType) -> None:
    """Verify timezone parsing succeeds and falls back gracefully on invalid data.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates timezone parsing and fallback behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_dict_get = AsyncMock(return_value={"datetime": "2026-03-07 12:00:00 EST"})
        parsed_tz = await client._get_opnsense_timezone()
        assert parsed_tz is not None
        parsed_dt = datetime(2026, 3, 7, 12, 0, 0, tzinfo=parsed_tz)
        assert parsed_tz.utcoffset(parsed_dt) == timedelta(hours=-5)

        client._safe_dict_get = AsyncMock(return_value={"datetime": "2026-06-07 12:00:00 EDT"})
        parsed_tz = await client._get_opnsense_timezone()
        assert parsed_tz is not None
        parsed_dt = datetime(2026, 6, 7, 12, 0, 0, tzinfo=parsed_tz)
        assert parsed_tz.utcoffset(parsed_dt) == timedelta(hours=-4)

        client._safe_dict_get = AsyncMock(return_value={"datetime": "Sun Mar 22 21:36:07 EDT 2026"})
        parsed_tz = await client._get_opnsense_timezone()
        assert parsed_tz is not None
        parsed_dt = datetime(2026, 3, 22, 21, 36, 7, tzinfo=parsed_tz)
        assert parsed_tz.utcoffset(parsed_dt) == timedelta(hours=-4)

        client._safe_dict_get = AsyncMock(return_value={"datetime": "not-a-datetime"})
        fallback_tz = await client._get_opnsense_timezone()
        assert fallback_tz is not None
        local_tz = datetime.now().astimezone().tzinfo
        assert local_tz is not None
        now_local = datetime.now().astimezone()
        assert fallback_tz == local_tz or fallback_tz.utcoffset(now_local) == local_tz.utcoffset(
            now_local
        )

        client._safe_dict_get = AsyncMock(side_effect=aiohttp.ClientError("transient fetch error"))
        fetch_fallback_tz = await client._get_opnsense_timezone()
        assert fetch_fallback_tz is not None
        assert fetch_fallback_tz == local_tz or fetch_fallback_tz.utcoffset(
            now_local
        ) == local_tz.utcoffset(now_local)
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("datetime_str", "expected_dt", "expected_offset"),
    [
        ("2026-06-07 12:00:00 ADT", datetime(2026, 6, 7, 12, 0, 0), timedelta(hours=-3)),
        ("2026-01-07 12:00:00 AEDT", datetime(2026, 1, 7, 12, 0, 0), timedelta(hours=11)),
        ("2026-06-07 12:00:00 CEST", datetime(2026, 6, 7, 12, 0, 0), timedelta(hours=2)),
        ("2026-06-07 12:00:00 CDT", datetime(2026, 6, 7, 12, 0, 0), timedelta(hours=-5)),
        ("2026-06-07 12:00:00 EEST", datetime(2026, 6, 7, 12, 0, 0), timedelta(hours=3)),
        ("2026-06-07 12:00:00 MDT", datetime(2026, 6, 7, 12, 0, 0), timedelta(hours=-6)),
        ("2026-01-07 12:00:00 NZDT", datetime(2026, 1, 7, 12, 0, 0), timedelta(hours=13)),
        ("2026-06-07 12:00:00 PDT", datetime(2026, 6, 7, 12, 0, 0), timedelta(hours=-7)),
    ],
)
async def test_get_opnsense_timezone_supports_known_daylight_abbreviations(
    make_client: ClientType,
    datetime_str: str,
    expected_dt: datetime,
    expected_offset: timedelta,
) -> None:
    """Verify known daylight/summer abbreviations resolve to timezone-aware offsets.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        datetime_str (str): Datetime string parsed from mocked API output.
        expected_dt (datetime): Naive datetime used to evaluate the resolved timezone offset.
        expected_offset (timedelta): Expected UTC offset for the parsed timezone.

    Returns:
        None: This test validates timezone abbreviation support for DST-aware zones.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_dict_get = AsyncMock(return_value={"datetime": datetime_str})
        parsed_tz = await client._get_opnsense_timezone()
        assert parsed_tz is not None
        assert parsed_tz.utcoffset(expected_dt.replace(tzinfo=parsed_tz)) == expected_offset
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_carp_summary_and_reboot_and_wol(make_client: ClientType) -> None:
    """Verify CARP summary/discovery and core system control endpoints.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates CARP summary parsing and control endpoint behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_dict_get = AsyncMock(
            return_value={
                "rows": [
                    {
                        "interface": "em0",
                        "subnet": "10.0.0.1",
                        "status": "MASTER",
                        "mode": "carp",
                        "vhid": "1",
                        "advbase": "1",
                        "advskew": "0",
                    }
                ],
                "carp": {
                    "allow": "1",
                    "demotion": "0",
                    "maintenancemode": False,
                    "status_msg": "",
                },
            }
        )
        summary = dict((await client.get_carp()).get("status_summary", {}))
        assert summary["state"] == "healthy"
        assert summary["enabled"] is True
        assert summary["vip_count"] == 1
        assert summary["master_count"] == 1

        client._safe_dict_get = AsyncMock(
            side_effect=[
                {
                    "rows": [
                        {
                            "mode": "carp",
                            "interface": "em0",
                            "subnet": "10.0.0.1",
                            "vhid": "1",
                            "status": "MASTER",
                        }
                    ]
                },
                {"rows": [{"mode": "carp", "interface": "em0", "subnet": "10.0.0.1", "vhid": "1"}]},
            ]
        )
        carp = (await client.get_carp()).get("interfaces", [])
        assert isinstance(carp, list)
        assert carp[0]["status"] == "MASTER"
        assert carp[0]["interface"] == "em0"

        client._safe_dict_post = AsyncMock(return_value={"status": "ok"})
        assert await client.system_reboot() is True
        result = await client.system_halt()
        assert result is None

        client._safe_dict_post = AsyncMock(return_value={"status": "ok"})
        result = await client.send_wol("em0", "aa:bb:cc")
        assert isinstance(result, bool)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_reload_interface_and_certificates_and_gateways(
    make_client: ClientType,
) -> None:
    """Verify interface reload and certificate/gateway parsing helpers.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates reload and parsing helper behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_dict_post = AsyncMock(return_value={"message": "OK reload"})
        ok = await client.reload_interface("em0")
        assert ok is True

        # certificates
        client._safe_dict_get = AsyncMock(
            return_value={
                "rows": [
                    {
                        "descr": "cert1",
                        "uuid": "u1",
                        "caref": "issuer",
                        "rfc3280_purpose": "purpose",
                        "in_use": "1",
                        "valid_from": 0,
                        "valid_to": 0,
                    }
                ]
            }
        )
        certs = await client.get_certificates()
        assert "cert1" in certs and certs["cert1"]["issuer"] == "issuer"

        # gateways
        client._safe_dict_get = AsyncMock(
            return_value={"items": [{"name": "gw1", "status_translated": "Online"}]}
        )
        gws = await client.get_gateways()
        assert "gw1" in gws and gws["gw1"]["status"] == "online"
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_gateways_notices_and_close_notice_all(make_client: ClientType) -> None:
    """Verify gateway notices reporting and close-all notice handling.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates notice parsing and bulk-dismiss behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_dict_get = AsyncMock(
            return_value={"items": [{"name": "gw1", "status_translated": "OK"}]}
        )
        gws = await client.get_gateways()
        assert "gw1" in gws and gws["gw1"]["status"] == "ok"

        # notices: include a pending notice
        client._safe_dict_get = AsyncMock(
            return_value={
                "n1": {
                    "statusCode": 1,
                    "message": "m",
                    "timestamp": int(datetime.now(tz=timezone.utc).timestamp()),
                }
            }
        )
        notices = await client.get_notices()
        assert notices["pending_notices_present"] is True

        # close_notice all: prepare multiple notices and simulate dismiss responses
        client._safe_dict_get = AsyncMock(
            return_value={"n1": {"statusCode": 1}, "n2": {"statusCode": 1}}
        )
        client._safe_dict_post = AsyncMock(return_value={"status": "ok"})
        assert await client.close_notice("all") is True
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_opnsense_timezone_without_tzinfo_string_uses_local_fallback(
    make_client: ClientType,
) -> None:
    """Verify naive datetime strings fall back to local timezone resolution.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates local-timezone fallback behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        tz = await client._get_opnsense_timezone("2026-03-07 12:00:00")
        local_tz = client._get_local_timezone()
        now = datetime.now().astimezone()
        assert tz.utcoffset(now) == local_tz.utcoffset(now)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_carp_handles_invalid_payloads_and_default_status(
    make_client: ClientType,
) -> None:
    """Verify CARP interface parsing tolerates malformed payloads and missing status.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates robust CARP interface normalization behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_dict_get = AsyncMock(side_effect=[{"rows": "bad"}, {"rows": "bad"}])
        assert (await client.get_carp()).get("interfaces", []) == []

        client._safe_dict_get = AsyncMock(
            side_effect=[
                {
                    "rows": [
                        {"mode": "other", "interface": "em9"},
                        {"mode": "carp", "interface": "em0", "subnet": "10.0.0.1", "vhid": "11"},
                    ]
                },
                {
                    "rows": [
                        {"mode": "carp", "interface": "em1", "subnet": "10.0.0.9", "vhid": "12"}
                    ]
                },
            ]
        )
        carp = (await client.get_carp()).get("interfaces", [])
        assert len(carp) == 1
        assert carp[0]["interface"] == "em0"
        assert carp[0]["status"] == "DISABLED"

        client._safe_dict_get = AsyncMock(
            side_effect=[
                {
                    "rows": [
                        {
                            "mode": "carp",
                            "interface": "wan",
                            "subnet": "192.0.2.10",
                            "vhid": "20",
                            "status": "BACKUP",
                        }
                    ]
                },
                {"rows": "not-a-list"},
            ]
        )
        carp = (await client.get_carp()).get("interfaces", [])
        assert len(carp) == 1
        assert carp[0]["interface"] == "wan"
        assert carp[0]["status"] == "BACKUP"

        client._safe_dict_get = AsyncMock(
            side_effect=[
                {
                    "rows": [
                        {
                            "mode": "carp",
                            "interface": "opt1",
                            "vhid": "30",
                            "status": "MASTER",
                        }
                    ]
                },
                {"rows": []},
            ]
        )
        assert (await client.get_carp()).get("interfaces", []) == []
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_carp_matches_multiple_vips_on_same_interface(
    make_client: ClientType,
) -> None:
    """Verify CARP enrichment matches by VIP identity, not interface alone.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates multi-VIP matching correctness for shared interfaces.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_dict_get = AsyncMock(
            side_effect=[
                {
                    "rows": [
                        {
                            "mode": "carp",
                            "interface": "lan",
                            "subnet": "10.0.0.1",
                            "vhid": "1",
                            "status": "MASTER",
                        },
                        {
                            "mode": "carp",
                            "interface": "lan",
                            "subnet": "10.0.0.2",
                            "vhid": "2",
                            "status": "BACKUP",
                        },
                    ]
                },
                {
                    "rows": [
                        {
                            "mode": "carp",
                            "interface": "lan",
                            "subnet": "10.0.0.1",
                            "vhid": "1",
                            "descr": "first",
                        },
                        {
                            "mode": "carp",
                            "interface": "lan",
                            "subnet": "10.0.0.2",
                            "vhid": "2",
                            "descr": "second",
                        },
                    ]
                },
            ]
        )
        carp = (await client.get_carp()).get("interfaces", [])
        assert len(carp) == 2
        by_subnet = {entry["subnet"]: entry for entry in carp}
        assert by_subnet["10.0.0.1"]["status"] == "MASTER"
        assert by_subnet["10.0.0.1"]["descr"] == "first"
        assert by_subnet["10.0.0.2"]["status"] == "BACKUP"
        assert by_subnet["10.0.0.2"]["descr"] == "second"
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_carp_returns_no_match_for_ambiguous_partial_key_collisions(
    make_client: ClientType,
) -> None:
    """Verify fallback selection rejects ambiguous VIP setting candidates.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates ambiguous fallback matching behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_dict_get = AsyncMock(
            side_effect=[
                {
                    "rows": [
                        {
                            "mode": "carp",
                            "interface": "lan",
                            "vhid": "10",
                            "status": "MASTER",
                        }
                    ]
                },
                {
                    "rows": [
                        {
                            "mode": "carp",
                            "interface": "lan",
                            "subnet": "10.0.0.1",
                            "vhid": "10",
                            "descr": "first-candidate",
                        },
                        {
                            "mode": "carp",
                            "interface": "lan",
                            "subnet": "10.0.0.2",
                            "vhid": "10",
                            "descr": "second-candidate",
                        },
                    ]
                },
            ]
        )
        carp = (await client.get_carp()).get("interfaces", [])
        assert carp == []
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected_state"),
    [
        (
            {
                "rows": [
                    {"mode": "carp", "status": "MASTER", "interface": "wan", "subnet": "1.2.3.4"}
                ],
                "carp": {
                    "allow": "1",
                    "maintenancemode": False,
                    "demotion": "0",
                    "status_msg": "",
                },
            },
            "healthy",
        ),
        (
            {
                "rows": [],
                "carp": {
                    "allow": "1",
                    "maintenancemode": False,
                    "demotion": "0",
                    "status_msg": "",
                },
            },
            "not_configured",
        ),
        (
            {
                "rows": [
                    {"mode": "carp", "status": "MASTER", "interface": "wan", "subnet": "1.2.3.4"}
                ],
                "carp": {
                    "allow": "1",
                    "maintenancemode": True,
                    "demotion": "0",
                    "status_msg": "",
                },
            },
            "maintenance",
        ),
        (
            {
                "rows": [
                    {"mode": "carp", "status": "INIT", "interface": "wan", "subnet": "1.2.3.4"}
                ],
                "carp": {
                    "allow": "1",
                    "maintenancemode": False,
                    "demotion": "2",
                    "status_msg": "demoted",
                },
            },
            "degraded",
        ),
        (
            {
                "rows": [
                    {"mode": "carp", "status": "MASTER", "interface": "wan", "subnet": "1.2.3.4"}
                ],
                "carp": {
                    "allow": "0",
                    "maintenancemode": False,
                    "demotion": "0",
                    "status_msg": "",
                },
            },
            "disabled",
        ),
        (
            {
                "rows": [
                    {"mode": "carp", "status": "MASTER", "interface": "wan", "subnet": "1.2.3.4"}
                ],
                "carp": {
                    "allow": "1",
                    "maintenancemode": False,
                    "demotion": "0",
                    "status_msg": 0,
                },
            },
            "healthy",
        ),
        (
            {
                "rows": "bad",
                "carp": "bad",
            },
            "unknown",
        ),
        (
            {
                "rows": [{"mode": "carp", "status": "MASTER", "interface": "wan"}],
            },
            "unknown",
        ),
        (
            {
                "rows": [
                    {"mode": "carp", "status": "MASTER", "interface": "wan", "subnet": ""},
                    {"mode": "carp", "status": "BACKUP", "subnet": "1.2.3.4"},
                ],
                "carp": {
                    "allow": "1",
                    "maintenancemode": False,
                    "demotion": "0",
                    "status_msg": "",
                },
            },
            "not_configured",
        ),
    ],
)
async def test_get_carp_status_states(
    make_client: ClientType,
    payload: dict[str, Any],
    expected_state: str,
) -> None:
    """Verify CARP summary state mapping across common health scenarios.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        payload (dict[str, Any]): Raw endpoint payload returned by mocked REST API.
        expected_state (str): Expected normalized CARP summary state.

    Returns:
        None: This test validates summary-state classification behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_dict_get = AsyncMock(return_value=payload)
        summary = dict((await client.get_carp()).get("status_summary", {}))
        assert summary["state"] == expected_state
        assert "vips" in summary
        assert "vip_count" in summary
        if isinstance(payload.get("carp"), dict) and not isinstance(
            payload["carp"].get("status_msg", ""),
            str,
        ):
            assert summary["status_message"] == ""
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_carp_skips_whitespace_interface_values(
    make_client: ClientType,
) -> None:
    """Verify CARP merge drops entries whose interface is blank after normalization.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates normalized interface filtering behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_dict_get = AsyncMock(
            return_value={
                "rows": [
                    {
                        "mode": "carp",
                        "status": "MASTER",
                        "interface": "   ",
                        "subnet": "1.2.3.4",
                    },
                    {
                        "mode": "carp",
                        "status": "BACKUP",
                        "interface": "  wan  ",
                        "subnet": "5.6.7.8",
                    },
                ],
                "carp": {
                    "allow": "1",
                    "maintenancemode": False,
                    "demotion": "0",
                    "status_msg": "",
                },
            }
        )
        snapshot = await client.get_carp()
        assert snapshot["interfaces"] == [
            {
                "interface": "wan",
                "mode": "carp",
                "status": "BACKUP",
                "subnet": "5.6.7.8",
            }
        ]
        assert snapshot["status_summary"]["vip_count"] == 1
        assert snapshot["status_summary"]["interfaces"] == ["wan"]
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_carp_status_uses_settings_merge_for_partial_rows(
    make_client: ClientType,
) -> None:
    """Verify summary uses the same status/settings merge as interface CARP discovery.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates that partial status rows can be reconstructed from settings.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_dict_get = AsyncMock(
            side_effect=[
                {
                    "rows": [
                        {
                            "mode": "carp",
                            "status": "MASTER",
                            "interface": "wan",
                            "vhid": "1",
                        }
                    ],
                    "carp": {
                        "allow": "1",
                        "maintenancemode": False,
                        "demotion": "0",
                        "status_msg": "",
                    },
                },
                {
                    "rows": [
                        {
                            "mode": "carp",
                            "interface": "wan",
                            "subnet": "1.2.3.4",
                            "vhid": "1",
                        }
                    ]
                },
            ]
        )
        summary = dict((await client.get_carp()).get("status_summary", {}))
        assert summary["state"] == "healthy"
        assert summary["vip_count"] == 1
        assert summary["interfaces"] == ["wan"]
        assert summary["vips"][0]["subnet"] == "1.2.3.4"
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_carp_returns_interfaces_and_summary_from_one_fetch(
    make_client: ClientType,
) -> None:
    """Ensure one CARP call returns both interface and summary payloads.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates one-fetch snapshot payload behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_dict_get = AsyncMock(
            side_effect=[
                {
                    "rows": [{"mode": "carp", "status": "MASTER", "interface": "wan", "vhid": "1"}],
                    "carp": {
                        "allow": "1",
                        "maintenancemode": False,
                        "demotion": "0",
                        "status_msg": "",
                    },
                },
                {"rows": [{"mode": "carp", "interface": "wan", "subnet": "1.2.3.4", "vhid": "1"}]},
            ]
        )
        snapshot = await client.get_carp()
        assert snapshot["interfaces"][0]["subnet"] == "1.2.3.4"
        assert snapshot["status_summary"]["state"] == "healthy"
        assert client._safe_dict_get.await_count == 2
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_system_actions_and_notice_closing_failure_paths(
    make_client: ClientType,
) -> None:
    """Verify failure-path behavior for system actions and notice closing.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates non-OK status handling for action helpers.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_dict_post = AsyncMock(return_value={"status": "failed"})
        assert await client.system_reboot() is False
        assert await client.system_halt() is None
        assert await client.send_wol("em0", "aa:bb:cc") is False

        client._safe_dict_post = AsyncMock(return_value={"status": "failed"})
        assert await client.close_notice("single-notice") is False

        client._safe_dict_get = AsyncMock(
            return_value={
                "n1": {"statusCode": 1},
                "n2": {"statusCode": 2},
                "n3": {"statusCode": 1},
                "bad": "invalid",
            }
        )
        client._safe_dict_post = AsyncMock(side_effect=[{"status": "ok"}, {"status": "failed"}])
        assert await client.close_notice("all") is False
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_certificates_handles_non_list_and_missing_description(
    make_client: ClientType,
) -> None:
    """Verify certificate parsing skips malformed rows and missing descriptions.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates certificate-row filtering behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_dict_get = AsyncMock(return_value={"rows": "bad"})
        assert await client.get_certificates() == {}

        client._safe_dict_get = AsyncMock(
            return_value={
                "rows": [
                    {"uuid": "skip-no-descr"},
                    {
                        "descr": "cert-ok",
                        "uuid": "u1",
                        "in_use": "0",
                        "valid_from": 0,
                        "valid_to": 0,
                    },
                ]
            }
        )
        certs = await client.get_certificates()
        assert list(certs) == ["cert-ok"]
        assert certs["cert-ok"]["in_use"] is False
    finally:
        await client.async_close()
