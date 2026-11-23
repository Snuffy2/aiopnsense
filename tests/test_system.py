"""Tests for `aiopnsense.system`."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import aiohttp
import pytest

from aiopnsense import OPNsenseClient, OPNsenseInvalidURL, OPNsensePrivilegeMissing
from aiopnsense._endpoint_outcome import _EndpointOutcome
from aiopnsense.exceptions import OPNsenseMissingDeviceUniqueID
from tests.conftest import make_mock_session_client, stub_endpoint_outcome

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
        client._use_snake_case = True
        read = stub_endpoint_outcome(client, _EndpointOutcome.data({"name": "foo"}))
        info = await client.get_system_info()
        assert info["name"] == "foo"
        read.assert_awaited_once_with("/api/diagnostics/system/system_information")
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
        stub_endpoint_outcome(client, _EndpointOutcome.data(instances))
        assert await client.get_device_unique_id(expected_id=expected_id) == expected
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "instances",
    [
        [{"is_physical": False}],
        [],
    ],
)
async def test_get_device_unique_id_raises_when_missing(
    make_client: ClientType, instances: list[Any]
) -> None:
    """Verify missing device IDs raise a public aiopnsense exception.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        instances (list[Any]): Interface rows returned by the mocked API.

    Returns:
        None: This test validates missing unique-ID error handling.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.toggle_throwing_errors(True)
        stub_endpoint_outcome(client, _EndpointOutcome.data(instances))
        with pytest.raises(OPNsenseMissingDeviceUniqueID):
            await client.get_device_unique_id()
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("throw_errors", [False, True])
@pytest.mark.parametrize("missing", ["endpoint-unavailable", "no-physical-mac"])
async def test_get_device_unique_id_missing_follows_throw_errors_contract(
    make_client: ClientType, throw_errors: bool, missing: str
) -> None:
    """Verify a missing device ID is ``None`` with throw off and raises with throw on.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Whether the client raises public errors.
        missing (str): How the ID is unavailable, endpoint absent or no physical MAC.

    Returns:
        None: This test validates both throw modes for a missing unique ID.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.toggle_throwing_errors(throw_errors)
        outcome = (
            _EndpointOutcome.absent()
            if missing == "endpoint-unavailable"
            else _EndpointOutcome.data([{"is_physical": False}])
        )
        stub_endpoint_outcome(client, outcome)
        if throw_errors:
            with pytest.raises(OPNsenseMissingDeviceUniqueID):
                await client.get_device_unique_id()
        else:
            assert await client.get_device_unique_id() is None
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
        stub_endpoint_outcome(
            client, _EndpointOutcome.data({"datetime": "2026-03-07 12:00:00 EST"})
        )
        parsed_tz = await client._get_opnsense_timezone()
        assert parsed_tz is not None
        parsed_dt = datetime(2026, 3, 7, 12, 0, 0, tzinfo=parsed_tz)
        assert parsed_tz.utcoffset(parsed_dt) == timedelta(hours=-5)

        stub_endpoint_outcome(
            client, _EndpointOutcome.data({"datetime": "2026-06-07 12:00:00 EDT"})
        )
        parsed_tz = await client._get_opnsense_timezone()
        assert parsed_tz is not None
        parsed_dt = datetime(2026, 6, 7, 12, 0, 0, tzinfo=parsed_tz)
        assert parsed_tz.utcoffset(parsed_dt) == timedelta(hours=-4)

        stub_endpoint_outcome(
            client, _EndpointOutcome.data({"datetime": "Sun Mar 22 21:36:07 EDT 2026"})
        )
        parsed_tz = await client._get_opnsense_timezone()
        assert parsed_tz is not None
        parsed_dt = datetime(2026, 3, 22, 21, 36, 7, tzinfo=parsed_tz)
        assert parsed_tz.utcoffset(parsed_dt) == timedelta(hours=-4)

        stub_endpoint_outcome(client, _EndpointOutcome.data({"datetime": "not-a-datetime"}))
        fallback_tz = await client._get_opnsense_timezone()
        assert fallback_tz is not None
        local_tz = datetime.now().astimezone().tzinfo
        assert local_tz is not None
        now_local = datetime.now().astimezone()
        assert fallback_tz == local_tz or fallback_tz.utcoffset(now_local) == local_tz.utcoffset(
            now_local
        )

        read = stub_endpoint_outcome(client, _EndpointOutcome.data({}))
        read.side_effect = aiohttp.ClientError("transient fetch error")
        fetch_fallback_tz = await client._get_opnsense_timezone()
        assert fetch_fallback_tz is not None
        assert fetch_fallback_tz == local_tz or fetch_fallback_tz.utcoffset(
            now_local
        ) == local_tz.utcoffset(now_local)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_opnsense_timezone_fallback_for_mapped_error(make_client: ClientType) -> None:
    """Verify timezone fallback for mapped client errors.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates fallback when the system-time read raises an OPNsenseError.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        read = stub_endpoint_outcome(client, _EndpointOutcome.data({}))
        read.side_effect = OPNsenseInvalidURL(
            "https://user:password@api.example/diagnostics/system/system_time"
        )

        parsed_tz = await client._get_opnsense_timezone()
        local_tz = datetime.now().astimezone().tzinfo
        now_local = datetime.now().astimezone()

        assert parsed_tz is not None
        assert parsed_tz == local_tz or parsed_tz.utcoffset(now_local) == local_tz.utcoffset(
            now_local
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_resolved_opnsense_timezone_returns_none_on_endpoint_unavailable(
    make_client: ClientType,
) -> None:
    """Verify resolved-only timezone lookup returns ``None`` when endpoint is unavailable.

    Args:
        make_client (ClientType): Client fixture whose timezone endpoint is unavailable.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        read = stub_endpoint_outcome(client, _EndpointOutcome.absent())

        resolved_tz = await client._get_resolved_opnsense_timezone()

        assert resolved_tz is None
        read.assert_awaited_once_with("/api/diagnostics/system/system_time")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_resolved_opnsense_timezone_returns_none_on_probe_error(
    make_client: ClientType,
) -> None:
    """Verify a mapped endpoint probe error does not abort timezone enrichment.

    Args:
        make_client (ClientType): Client fixture whose endpoint probe raises an error.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.toggle_throwing_errors(True)
        read = stub_endpoint_outcome(client, _EndpointOutcome.data({}))
        read.side_effect = OPNsensePrivilegeMissing("Missing privilege for system time")

        resolved_tz = await client._get_resolved_opnsense_timezone()

        assert resolved_tz is None
        read.assert_awaited_once_with("/api/diagnostics/system/system_time")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_resolved_opnsense_timezone_returns_none_on_fetch_error(
    make_client: ClientType,
) -> None:
    """Verify resolved-only timezone lookup returns ``None`` when fetch fails.

    Args:
        make_client (ClientType): Client fixture whose timezone fetch raises an error.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        read = stub_endpoint_outcome(client, _EndpointOutcome.data({}))
        read.side_effect = aiohttp.ClientError("transient fetch error")

        resolved_tz = await client._get_resolved_opnsense_timezone()

        assert resolved_tz is None
        read.assert_awaited_once_with("/api/diagnostics/system/system_time")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_resolved_opnsense_timezone_returns_none_on_malformed_datetime(
    make_client: ClientType,
) -> None:
    """Verify resolved-only timezone lookup returns ``None`` for malformed data.

    Args:
        make_client (ClientType): Client fixture returning a malformed timezone datetime.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        stub_endpoint_outcome(client, _EndpointOutcome.data({"datetime": "not-a-datetime"}))

        resolved_tz = await client._get_resolved_opnsense_timezone()

        assert resolved_tz is None
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("datetime_str", "expected_dt", "expected_offset"),
    [
        (
            "2026-06-07 12:00:00 ADT",
            datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC),
            timedelta(hours=-3),
        ),
        (
            "2026-01-07 12:00:00 AEDT",
            datetime(2026, 1, 7, 12, 0, 0, tzinfo=UTC),
            timedelta(hours=11),
        ),
        (
            "2026-06-07 12:00:00 CEST",
            datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC),
            timedelta(hours=2),
        ),
        (
            "2026-06-07 12:00:00 CDT",
            datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC),
            timedelta(hours=-5),
        ),
        (
            "2026-06-07 12:00:00 EEST",
            datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC),
            timedelta(hours=3),
        ),
        (
            "2026-06-07 12:00:00 MDT",
            datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC),
            timedelta(hours=-6),
        ),
        (
            "2026-01-07 12:00:00 NZDT",
            datetime(2026, 1, 7, 12, 0, 0, tzinfo=UTC),
            timedelta(hours=13),
        ),
        (
            "2026-06-07 12:00:00 PDT",
            datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC),
            timedelta(hours=-7),
        ),
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
        expected_dt (datetime): Datetime whose wall time is used to evaluate the
            resolved timezone offset.
        expected_offset (timedelta): Expected UTC offset for the parsed timezone.

    Returns:
        None: This test validates timezone abbreviation support for DST-aware zones.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        stub_endpoint_outcome(client, _EndpointOutcome.data({"datetime": datetime_str}))
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
        stub_endpoint_outcome(
            client,
            _EndpointOutcome.data(
                {
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
            ),
        )
        summary = dict((await client.get_carp()).get("status_summary", {}))
        assert summary["state"] == "healthy"
        assert summary["enabled"] is True
        assert summary["vip_count"] == 1
        assert summary["master_count"] == 1

        read = stub_endpoint_outcome(client, _EndpointOutcome.data({}))
        read.side_effect = [
            _EndpointOutcome.data(
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
                }
            ),
            _EndpointOutcome.data(
                {"rows": [{"mode": "carp", "interface": "em0", "subnet": "10.0.0.1", "vhid": "1"}]}
            ),
        ]
        carp = (await client.get_carp()).get("interfaces", [])
        assert isinstance(carp, list)
        assert carp[0]["status"] == "MASTER"
        assert carp[0]["interface"] == "em0"

        client._safe_dict_post = AsyncMock(return_value={"status": "ok"})
        assert await client.system_reboot() is True
        result = await client.system_halt()
        assert result is None
        client.get_host_firmware_version = AsyncMock(return_value="26.1.10")
        client._use_snake_case = True
        assert await client.toggle_carp_maintenance_mode() is True
        client._safe_dict_post.assert_awaited_with(
            "/api/diagnostics/interface/_carp_status/maintenance"
        )
        client.get_host_firmware_version = AsyncMock(return_value="26.1.11")
        assert await client.toggle_carp_maintenance_mode() is True
        client._safe_dict_post.assert_awaited_with(
            "/api/diagnostics/interface/carp_status/maintenance"
        )
        client.get_host_firmware_version = AsyncMock(return_value="26.1.11_4")
        assert await client.toggle_carp_maintenance_mode() is True
        client._safe_dict_post.assert_awaited_with(
            "/api/diagnostics/interface/carp_status/maintenance"
        )
        client.get_host_firmware_version = AsyncMock(return_value="not-a-version")
        assert await client.toggle_carp_maintenance_mode() is True
        client._safe_dict_post.assert_awaited_with(
            "/api/diagnostics/interface/_carp_status/maintenance"
        )
        client._use_snake_case = False
        assert await client.toggle_carp_maintenance_mode() is True
        client._safe_dict_post.assert_awaited_with(
            "/api/diagnostics/interface/CarpStatus/maintenance"
        )
        client._use_snake_case = None
        client.get_host_firmware_version = AsyncMock(return_value=None)
        assert await client.toggle_carp_maintenance_mode() is True
        client._safe_dict_post.assert_awaited_with(
            "/api/diagnostics/interface/_carp_status/maintenance"
        )

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
        client._use_snake_case = True
        client._safe_dict_post = AsyncMock(return_value={"message": "OK reload"})
        ok = await client.reload_interface("em0")
        assert ok is True

        # certificates
        stub_endpoint_outcome(
            client,
            _EndpointOutcome.data(
                {
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
            ),
        )
        certs = await client.get_certificates()
        assert "cert1" in certs and certs["cert1"]["issuer"] == "issuer"

        # gateways
        stub_endpoint_outcome(
            client,
            _EndpointOutcome.data({"items": [{"name": "gw1", "status_translated": "Online"}]}),
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
        client._use_snake_case = True
        stub_endpoint_outcome(
            client,
            _EndpointOutcome.data({"items": [{"name": "gw1", "status_translated": "OK"}]}),
        )
        gws = await client.get_gateways()
        assert "gw1" in gws and gws["gw1"]["status"] == "ok"

        # notices: include a pending notice
        stub_endpoint_outcome(
            client,
            _EndpointOutcome.data(
                {
                    "n1": {
                        "statusCode": 1,
                        "message": "m",
                        "timestamp": int(datetime.now(tz=UTC).timestamp()),
                    }
                }
            ),
        )
        notices = await client.get_notices()
        assert notices["pending_notices_present"] is True

        # close_notice all: prepare multiple notices and simulate dismiss responses
        stub_endpoint_outcome(
            client,
            _EndpointOutcome.data({"n1": {"statusCode": 1}, "n2": {"statusCode": 1}}),
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
        read = stub_endpoint_outcome(client, _EndpointOutcome.data({}))
        read.side_effect = [
            _EndpointOutcome.data({"rows": "bad"}),
            _EndpointOutcome.data({"rows": "bad"}),
        ]
        assert (await client.get_carp()).get("interfaces", []) == []

        read.side_effect = [
            _EndpointOutcome.data(
                {
                    "rows": [
                        {"mode": "other", "interface": "em9"},
                        {"mode": "carp", "interface": "em0", "subnet": "10.0.0.1", "vhid": "11"},
                    ]
                }
            ),
            _EndpointOutcome.data(
                {"rows": [{"mode": "carp", "interface": "em1", "subnet": "10.0.0.9", "vhid": "12"}]}
            ),
        ]
        carp = (await client.get_carp()).get("interfaces", [])
        assert len(carp) == 1
        assert carp[0]["interface"] == "em0"
        assert carp[0]["status"] == "DISABLED"

        read.side_effect = [
            _EndpointOutcome.data(
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
                }
            ),
            _EndpointOutcome.data({"rows": "not-a-list"}),
        ]
        carp = (await client.get_carp()).get("interfaces", [])
        assert len(carp) == 1
        assert carp[0]["interface"] == "wan"
        assert carp[0]["status"] == "BACKUP"

        read.side_effect = [
            _EndpointOutcome.data(
                {
                    "rows": [
                        {
                            "mode": "carp",
                            "interface": "opt1",
                            "vhid": "30",
                            "status": "MASTER",
                        }
                    ]
                }
            ),
            _EndpointOutcome.data({"rows": []}),
        ]
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
        read = stub_endpoint_outcome(client, _EndpointOutcome.data({}))
        read.side_effect = [
            _EndpointOutcome.data(
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
                }
            ),
            _EndpointOutcome.data(
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
                }
            ),
        ]
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
        read = stub_endpoint_outcome(client, _EndpointOutcome.data({}))
        read.side_effect = [
            _EndpointOutcome.data(
                {
                    "rows": [
                        {
                            "mode": "carp",
                            "interface": "lan",
                            "vhid": "10",
                            "status": "MASTER",
                        }
                    ]
                }
            ),
            _EndpointOutcome.data(
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
                }
            ),
        ]
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
        stub_endpoint_outcome(client, _EndpointOutcome.data(payload))
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
        stub_endpoint_outcome(
            client,
            _EndpointOutcome.data(
                {
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
            ),
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
        read = stub_endpoint_outcome(client, _EndpointOutcome.data({}))
        read.side_effect = [
            _EndpointOutcome.data(
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
                }
            ),
            _EndpointOutcome.data(
                {
                    "rows": [
                        {
                            "mode": "carp",
                            "interface": "wan",
                            "subnet": "1.2.3.4",
                            "vhid": "1",
                        }
                    ]
                }
            ),
        ]
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
        read = stub_endpoint_outcome(client, _EndpointOutcome.data({}))
        read.side_effect = [
            _EndpointOutcome.data(
                {
                    "rows": [{"mode": "carp", "status": "MASTER", "interface": "wan", "vhid": "1"}],
                    "carp": {
                        "allow": "1",
                        "maintenancemode": False,
                        "demotion": "0",
                        "status_msg": "",
                    },
                }
            ),
            _EndpointOutcome.data(
                {"rows": [{"mode": "carp", "interface": "wan", "subnet": "1.2.3.4", "vhid": "1"}]}
            ),
        ]
        snapshot = await client.get_carp()
        assert snapshot["interfaces"][0]["subnet"] == "1.2.3.4"
        assert snapshot["status_summary"]["state"] == "healthy"
        assert read.await_count == 2
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
        client._use_snake_case = True
        client._safe_dict_post = AsyncMock(return_value={"status": "failed"})
        assert await client.system_reboot() is False
        assert await client.system_halt() is None
        assert await client.send_wol("em0", "aa:bb:cc") is False

        client._safe_dict_post = AsyncMock(return_value={"status": "failed"})
        assert await client.close_notice("single-notice") is False

        stub_endpoint_outcome(
            client,
            _EndpointOutcome.data(
                {
                    "n1": {"statusCode": 1},
                    "n2": {"statusCode": 2},
                    "n3": {"statusCode": 1},
                    "bad": "invalid",
                }
            ),
        )
        client._safe_dict_post = AsyncMock(side_effect=[{"status": "ok"}, {"status": "failed"}])
        assert await client.close_notice("all") is False
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_version_switched_system_endpoints_fail_closed(make_client: ClientType) -> None:
    """Switched system endpoints should degrade without unsupported fetches.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates fail-closed behavior for switched system endpoints.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        read = stub_endpoint_outcome(client, _EndpointOutcome.absent())

        assert await client.get_system_info() == {}
        assert await client.get_device_unique_id() is None
        assert await client.get_carp() == {
            "interfaces": [],
            "status_summary": {
                "state": "unknown",
                "enabled": False,
                "maintenance_mode": False,
                "demotion": 0,
                "status_message": "",
                "vip_count": 0,
                "master_count": 0,
                "backup_count": 0,
                "other_count": 0,
                "interfaces": [],
                "vips": [],
            },
        }
        assert await client.get_notices() == {
            "pending_notices_present": False,
            "pending_notices": [],
        }
        assert await client.close_notice("all") is False
        assert await client.get_certificates() == {}
        tz = await client._get_opnsense_timezone()
        now = datetime.now().astimezone()
        assert tz.utcoffset(now) == client._get_local_timezone().utcoffset(now)
        session.post.assert_not_called()
        # One request per read, each answered absent; no payload is used.
        assert read.await_count == 7
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
        stub_endpoint_outcome(client, _EndpointOutcome.data({"rows": "bad"}))
        assert await client.get_certificates() == {}

        stub_endpoint_outcome(
            client,
            _EndpointOutcome.data(
                {
                    "rows": [
                        {"uuid": "skip-no-descr"},
                        {
                            "descr": "cert-ok",
                            "uuid": "u1",
                            "in_use": "0",
                            "valid_from": 0,
                            "valid_to": 0,
                        },
                        {
                            "descr": "cert-used",
                            "uuid": "u2",
                            "in_use": 1,
                            "valid_from": 0,
                            "valid_to": 0,
                        },
                    ]
                }
            ),
        )
        certs = await client.get_certificates()
        assert list(certs) == ["cert-ok", "cert-used"]
        assert certs["cert-ok"]["in_use"] is False
        assert certs["cert-used"]["in_use"] is True
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("use_snake_case", "expected_info", "expected_dismiss", "expected_reload"),
    [
        (
            True,
            "/api/diagnostics/system/system_information",
            "/api/core/system/dismiss_status",
            "/api/interfaces/overview/reload_interface/em0",
        ),
        (
            False,
            "/api/diagnostics/system/systemInformation",
            "/api/core/system/dismissStatus",
            "/api/interfaces/overview/reloadInterface/em0",
        ),
    ],
)
async def test_system_switched_endpoints_follow_selected_case(
    make_client: ClientType,
    use_snake_case: bool,
    expected_info: str,
    expected_dismiss: str,
    expected_reload: str,
) -> None:
    """Verify switched system endpoints follow the selected endpoint style.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        use_snake_case (bool): Whether the client should prefer snake_case endpoints.
        expected_info (str): Expected system information endpoint path.
        expected_dismiss (str): Expected notice-dismiss endpoint path.
        expected_reload (str): Expected interface-reload endpoint path.

    Returns:
        None: This test validates system endpoint selection behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = use_snake_case
        read = stub_endpoint_outcome(client, _EndpointOutcome.data({"name": "fw"}))
        client._safe_dict_post = AsyncMock(side_effect=[{"status": "ok"}, {"message": "OK reload"}])

        await client.get_system_info()
        await client.close_notice("notice-1")
        await client.reload_interface("em0")

        read.assert_awaited_once_with(expected_info)
        assert client._safe_dict_post.await_args_list[0].args[0] == expected_dismiss
        assert client._safe_dict_post.await_args_list[1].args[0] == expected_reload
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_system_branches_for_carp_candidate_and_notice_paths(make_client: ClientType) -> None:
    """Exercise additional CARP helper and notice-iteration branches.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates CARP candidate and notice iteration branch behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        parsed = client._parse_carp_vip_rows(
            [
                "invalid-row",
                {"mode": "other", "status": "master"},
                {"mode": "carp", "interface": "wan", "subnet": "1.2.3.4", "vhid": "1"},
            ]
        )
        assert len(parsed) == 1
        assert parsed[0]["status"] == "DISABLED"

        settings = [{"interface": "wan", "vhid": "1", "subnet": "1.2.3.4"}]
        indexes = client._build_carp_settings_indexes(settings)
        assert (
            client._find_carp_settings_match(
                {"interface": "wan", "vhid": "1", "subnet": "1.2.3.4"},
                indexes,
            )
            is not None
        )

        stub_endpoint_outcome(client, _EndpointOutcome.absent())
        vip_status_raw, merged = await client._fetch_and_merge_carp_vips()
        assert vip_status_raw == {}
        assert merged == []

        read = stub_endpoint_outcome(
            client,
            _EndpointOutcome.data(
                {"rows": [{"mode": "carp", "interface": "wan", "subnet": "1.2.3.4", "vhid": "1"}]}
            ),
        )
        read.side_effect = [
            _EndpointOutcome.data(
                {"rows": [{"mode": "carp", "interface": "wan", "subnet": "1.2.3.4", "vhid": "1"}]}
            ),
            _EndpointOutcome.absent(),
        ]
        vip_status_raw, merged = await client._fetch_and_merge_carp_vips()
        assert isinstance(vip_status_raw, dict)
        assert len(merged) == 1

        read.side_effect = [
            _EndpointOutcome.data(
                {"rows": [{"mode": "carp", "interface": "wan", "subnet": "1.2.3.4", "vhid": "1"}]}
            ),
            _EndpointOutcome.data(["bad-settings-payload"]),
        ]
        _vip_status_raw, merged = await client._fetch_and_merge_carp_vips()
        assert len(merged) == 1

        stub_endpoint_outcome(
            client,
            _EndpointOutcome.data(
                {
                    "n1": {"statusCode": 2, "message": "resolved"},
                    "n2": {"statusCode": 1, "message": "pending"},
                }
            ),
        )
        notices = await client.get_notices()
        assert notices["pending_notices_present"] is True
        assert len(notices["pending_notices"]) == 1

        client._safe_dict_post = AsyncMock(return_value={"status": "ok"})
        assert await client.close_notice("n2") is True

        tz = await client._get_opnsense_timezone("")
        now = datetime.now().astimezone()
        assert tz.utcoffset(now) == client._get_local_timezone().utcoffset(now)
    finally:
        await client.async_close()


CARP_OLD_PATH = "/api/diagnostics/interface/_carp_status/maintenance"
CARP_NEW_PATH = "/api/diagnostics/interface/carp_status/maintenance"
CARP_CAMEL_PATH = "/api/diagnostics/interface/CarpStatus/maintenance"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("firmware", "expected_path"),
    [
        pytest.param("25.1", CARP_CAMEL_PATH, id="camelcase-firmware"),
        pytest.param("26.1.10", CARP_OLD_PATH, id="below-threshold"),
        pytest.param("26.1.11", CARP_NEW_PATH, id="at-threshold"),
        pytest.param("26.1.11_4", CARP_NEW_PATH, id="at-threshold-with-revision"),
        pytest.param("26.2", CARP_NEW_PATH, id="above-threshold"),
        pytest.param(None, CARP_OLD_PATH, id="missing"),
        pytest.param("not-a-version", CARP_OLD_PATH, id="not-comparable"),
    ],
)
async def test_toggle_carp_maintenance_mode_uses_endpoint_for_firmware(
    firmware_router: tuple[OPNsenseClient, Any], firmware: str | None, expected_path: str
) -> None:
    """CARP maintenance uses the new status endpoint only on known firmware that has it.

    Args:
        firmware_router (tuple[OPNsenseClient, Any]): Client and the Router that scripts it.
        firmware (str | None): Firmware version reported by the Router.
        expected_path (str): CARP maintenance endpoint the client is expected to post to.
    """
    client, router = firmware_router
    router.firmware = firmware
    router.endpoints[expected_path] = {"status": "ok"}

    assert await client.toggle_carp_maintenance_mode() is True

    assert router.post_paths == [expected_path]
