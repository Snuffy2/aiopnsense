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
async def test_carp_and_reboot_and_wol(make_client: ClientType) -> None:
    """Verify CARP discovery and core system control endpoints.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates CARP status parsing and control endpoint behavior.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._safe_dict_get = AsyncMock(return_value={"carp": {"allow": "1"}})
        assert await client.get_carp_status() is True

        client._safe_dict_get = AsyncMock(
            side_effect=[
                {"rows": [{"mode": "carp", "interface": "em0"}]},
                {"rows": [{"interface": "em0", "status": "OK"}]},
            ]
        )
        carp = await client.get_carp_interfaces()
        assert isinstance(carp, list)

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
async def test_get_carp_interfaces_handles_invalid_payloads_and_default_status(
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
        assert await client.get_carp_interfaces() == []

        client._safe_dict_get = AsyncMock(
            side_effect=[
                {
                    "rows": [
                        {"mode": "other", "interface": "em9"},
                        {"mode": "carp", "interface": "em0"},
                    ]
                },
                {"rows": [{"interface": "em1", "status": "UP"}]},
            ]
        )
        carp = await client.get_carp_interfaces()
        assert len(carp) == 1
        assert carp[0]["interface"] == "em0"
        assert carp[0]["status"] == "DISABLED"
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
