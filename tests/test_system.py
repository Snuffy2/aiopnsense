"""Tests for `aiopnsense.system`."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import aiohttp
import pytest

from aiopnsense import OPNsenseClient, OPNsenseError, OPNsenseInvalidURL
from aiopnsense.exceptions import OPNsenseMissingDeviceUniqueID
from tests.conftest import FakeRouter, install_fake_router, make_mock_session_client

ClientType = Callable[..., OPNsenseClient]

SYSTEM_INFORMATION_PATH = "/api/diagnostics/system/system_information"
SYSTEM_TIME_PATH = "/api/diagnostics/system/system_time"
INTERFACE_OVERVIEW_PATH = "/api/interfaces/overview/export"
CARP_VIP_STATUS_PATH = "/api/diagnostics/interface/get_vip_status"
VIP_SETTINGS_PATH = "/api/interfaces/vip_settings/get"
CORE_SYSTEM_STATUS_PATH = "/api/core/system/status"
CERT_SEARCH_PATH = "/api/trust/cert/search"
GATEWAY_PATH = "/api/routes/gateway/status"

EMPTY_CARP_SNAPSHOT: dict[str, Any] = {
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
EMPTY_NOTICES: dict[str, Any] = {"pending_notices_present": False, "pending_notices": []}


def _client_with_router(
    make_client: ClientType,
    routes: dict[tuple[str, str], Any],
    *,
    throw_errors: bool = False,
) -> tuple[OPNsenseClient, FakeRouter]:
    """Return a snake_case client backed by a fake Router answering ``routes``.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        routes (dict[tuple[str, str], Any]): Values keyed by ``(method, path)``.
        throw_errors (bool): Whether the client should raise public errors.

    Returns:
        tuple[OPNsenseClient, FakeRouter]: Client and the fake Router recording its requests.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes=routes)
    client._use_snake_case = True
    if throw_errors:
        client.toggle_throwing_errors(True)
    return client, router


# Each system read with the endpoint it requests and the value it returns when that endpoint is unavailable.
SYSTEM_READS: list[Any] = [
    pytest.param(
        lambda client: client.get_system_info(),
        SYSTEM_INFORMATION_PATH,
        {},
        id="system-info",
    ),
    pytest.param(
        lambda client: client.get_notices(), CORE_SYSTEM_STATUS_PATH, EMPTY_NOTICES, id="notices"
    ),
    pytest.param(
        lambda client: client.close_notice("all"),
        CORE_SYSTEM_STATUS_PATH,
        False,
        id="close-all-notices",
    ),
    pytest.param(lambda client: client.get_certificates(), CERT_SEARCH_PATH, {}, id="certificates"),
    pytest.param(
        lambda client: client.get_carp(), CARP_VIP_STATUS_PATH, EMPTY_CARP_SNAPSHOT, id="carp"
    ),
]


@pytest.mark.asyncio
async def test_get_system_info(make_client: ClientType) -> None:
    """Verify system info is returned from the diagnostics endpoint.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _client_with_router(
        make_client, {("GET", SYSTEM_INFORMATION_PATH): {"name": "foo"}}
    )
    try:
        info = await client.get_system_info()
        assert info["name"] == "foo"
        assert router.count("GET", SYSTEM_INFORMATION_PATH) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("read", "path", "empty"), SYSTEM_READS)
@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(404, id="absent-404"),
        pytest.param(403, id="forbidden-403"),
        pytest.param(500, id="transient-500"),
        pytest.param(TimeoutError(), id="transient-timeout"),
    ],
)
async def test_system_read_returns_empty_value_when_endpoint_fails(
    make_client: ClientType,
    read: Callable[[OPNsenseClient], Awaitable[Any]],
    path: str,
    empty: Any,
    failure: Any,
) -> None:
    """Each system read returns its empty value for 404, 403 and transient failures.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        read (Callable[[OPNsenseClient], Awaitable[Any]]): Calls the public read under test.
        path (str): Endpoint path the read requests.
        empty (Any): Value the read returns when the endpoint is unavailable.
        failure (Any): HTTP status or exception the fake Router answers with.
    """
    client, router = _client_with_router(make_client, {("GET", path): failure})
    try:
        assert await read(client) == empty
        assert router.count("GET", path) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("read", "path", "empty"), SYSTEM_READS)
@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(403, id="forbidden-403"),
        pytest.param(500, id="transient-500"),
        pytest.param(TimeoutError(), id="transient-timeout"),
    ],
)
async def test_system_read_raises_for_forbidden_or_transient_failure_in_throw_mode(
    make_client: ClientType,
    read: Callable[[OPNsenseClient], Awaitable[Any]],
    path: str,
    empty: Any,
    failure: Any,
) -> None:
    """In throw mode a 403 or transient failure raises an ``OPNsenseError``.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        read (Callable[[OPNsenseClient], Awaitable[Any]]): Calls the public read under test.
        path (str): Endpoint path the read requests.
        empty (Any): Value the read returns in default mode.
        failure (Any): HTTP status or exception the fake Router answers with.
    """
    client, _router = _client_with_router(make_client, {("GET", path): failure}, throw_errors=True)
    try:
        with pytest.raises(OPNsenseError):
            await read(client)
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("read", "path", "empty"), SYSTEM_READS)
async def test_system_read_returns_empty_without_raising_for_absent_endpoint_in_throw_mode(
    make_client: ClientType,
    read: Callable[[OPNsenseClient], Awaitable[Any]],
    path: str,
    empty: Any,
) -> None:
    """In throw mode a 404 is still the absent endpoint and returns the empty value.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        read (Callable[[OPNsenseClient], Awaitable[Any]]): Calls the public read under test.
        path (str): Endpoint path the read requests.
        empty (Any): Value the read returns for an absent endpoint.
    """
    client, _router = _client_with_router(make_client, {("GET", path): 404}, throw_errors=True)
    try:
        assert await read(client) == empty
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
        instances (list[Any]): Interface rows returned by the fake Router.
        expected_id (str | None): Optional preferred unique ID candidate.
        expected (str | None): Expected normalized unique ID result.
    """
    client, _router = _client_with_router(
        make_client, {("GET", INTERFACE_OVERVIEW_PATH): instances}
    )
    try:
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
        instances (list[Any]): Interface rows returned by the fake Router.
    """
    client, _router = _client_with_router(
        make_client, {("GET", INTERFACE_OVERVIEW_PATH): instances}, throw_errors=True
    )
    try:
        with pytest.raises(OPNsenseMissingDeviceUniqueID):
            await client.get_device_unique_id()
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(404, id="absent-404"),
        pytest.param(500, id="transient-500"),
    ],
)
async def test_get_device_unique_id_returns_none_for_absent_or_failing_overview(
    make_client: ClientType, failure: int
) -> None:
    """A missing or failing interface overview yields no device ID in default mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        failure (int): HTTP status the fake Router answers the interface overview with.
    """
    client, router = _client_with_router(make_client, {("GET", INTERFACE_OVERVIEW_PATH): failure})
    try:
        assert await client.get_device_unique_id() is None
        assert router.count("GET", INTERFACE_OVERVIEW_PATH) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_device_unique_id_raises_missing_identifier_for_absent_overview_in_throw_mode(
    make_client: ClientType,
) -> None:
    """In throw mode an absent interface overview reports the missing device identifier.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _router = _client_with_router(
        make_client, {("GET", INTERFACE_OVERVIEW_PATH): 404}, throw_errors=True
    )
    try:
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
    answer = 404 if missing == "endpoint-unavailable" else [{"is_physical": False}]
    client, _router = _client_with_router(
        make_client, {("GET", INTERFACE_OVERVIEW_PATH): answer}, throw_errors=throw_errors
    )
    try:
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
    """
    client, router = _client_with_router(
        make_client, {("GET", SYSTEM_TIME_PATH): {"datetime": "2026-03-07 12:00:00 EST"}}
    )
    try:
        parsed_tz = await client._get_opnsense_timezone()
        assert parsed_tz is not None
        parsed_dt = datetime(2026, 3, 7, 12, 0, 0, tzinfo=parsed_tz)
        assert parsed_tz.utcoffset(parsed_dt) == timedelta(hours=-5)

        router.routes[("GET", SYSTEM_TIME_PATH)] = {"datetime": "2026-06-07 12:00:00 EDT"}
        parsed_tz = await client._get_opnsense_timezone()
        assert parsed_tz is not None
        parsed_dt = datetime(2026, 6, 7, 12, 0, 0, tzinfo=parsed_tz)
        assert parsed_tz.utcoffset(parsed_dt) == timedelta(hours=-4)

        router.routes[("GET", SYSTEM_TIME_PATH)] = {"datetime": "Sun Mar 22 21:36:07 EDT 2026"}
        parsed_tz = await client._get_opnsense_timezone()
        assert parsed_tz is not None
        parsed_dt = datetime(2026, 3, 22, 21, 36, 7, tzinfo=parsed_tz)
        assert parsed_tz.utcoffset(parsed_dt) == timedelta(hours=-4)

        router.routes[("GET", SYSTEM_TIME_PATH)] = {"datetime": "not-a-datetime"}
        fallback_tz = await client._get_opnsense_timezone()
        assert fallback_tz is not None
        local_tz = datetime.now().astimezone().tzinfo
        assert local_tz is not None
        now_local = datetime.now().astimezone()
        assert fallback_tz == local_tz or fallback_tz.utcoffset(now_local) == local_tz.utcoffset(
            now_local
        )

        router.routes[("GET", SYSTEM_TIME_PATH)] = aiohttp.ClientError("transient fetch error")
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
    """
    client, _router = _client_with_router(
        make_client,
        {
            ("GET", SYSTEM_TIME_PATH): OPNsenseInvalidURL(
                "https://user:password@api.example/diagnostics/system/system_time"
            )
        },
    )
    try:
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
async def test_get_resolved_opnsense_timezone_returns_none_on_absent_endpoint(
    make_client: ClientType,
) -> None:
    """Verify resolved-only timezone lookup returns ``None`` with one request when absent.

    Args:
        make_client (ClientType): Client fixture whose timezone endpoint is absent.
    """
    client, router = _client_with_router(make_client, {("GET", SYSTEM_TIME_PATH): 404})
    try:
        resolved_tz = await client._get_resolved_opnsense_timezone()

        assert resolved_tz is None
        assert router.count("GET", SYSTEM_TIME_PATH) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_successful_timezone_resolution_reads_system_time_once(
    make_client: ClientType,
) -> None:
    """A successful timezone resolution reads the system-time endpoint with one request.

    Args:
        make_client (ClientType): Client fixture whose system-time endpoint is present.
    """
    client, router = _client_with_router(
        make_client,
        {("GET", SYSTEM_TIME_PATH): {"datetime": "2026-03-07 12:00:00 EST"}},
    )
    try:
        tz = await client._get_opnsense_timezone()

        assert tz.utcoffset(datetime(2026, 3, 7, 12, tzinfo=UTC)) == timedelta(hours=-5)
        assert router.count("GET", SYSTEM_TIME_PATH) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_resolved_opnsense_timezone_returns_none_on_forbidden_in_throw_mode(
    make_client: ClientType,
) -> None:
    """Verify a permission error on the timezone read does not abort timezone enrichment.

    Args:
        make_client (ClientType): Client fixture whose timezone read is not permitted.
    """
    client, router = _client_with_router(
        make_client, {("GET", SYSTEM_TIME_PATH): 403}, throw_errors=True
    )
    try:
        resolved_tz = await client._get_resolved_opnsense_timezone()

        assert resolved_tz is None
        assert router.count("GET", SYSTEM_TIME_PATH) == 1
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
    client, router = _client_with_router(
        make_client, {("GET", SYSTEM_TIME_PATH): aiohttp.ClientError("transient fetch error")}
    )
    try:
        resolved_tz = await client._get_resolved_opnsense_timezone()

        assert resolved_tz is None
        assert router.count("GET", SYSTEM_TIME_PATH) == 1
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
    client, _router = _client_with_router(
        make_client, {("GET", SYSTEM_TIME_PATH): {"datetime": "not-a-datetime"}}
    )
    try:
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
        datetime_str (str): Datetime string returned by the fake Router.
        expected_dt (datetime): Datetime whose wall time is used to evaluate the
            resolved timezone offset.
        expected_offset (timedelta): Expected UTC offset for the parsed timezone.
    """
    client, _router = _client_with_router(
        make_client, {("GET", SYSTEM_TIME_PATH): {"datetime": datetime_str}}
    )
    try:
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
    """
    carp_status = {
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
    client, router = _client_with_router(
        make_client,
        {
            ("GET", CARP_VIP_STATUS_PATH): carp_status,
            ("GET", VIP_SETTINGS_PATH): carp_status,
        },
    )
    try:
        summary = dict((await client.get_carp()).get("status_summary", {}))
        assert summary["state"] == "healthy"
        assert summary["enabled"] is True
        assert summary["vip_count"] == 1
        assert summary["master_count"] == 1

        router.routes[("GET", CARP_VIP_STATUS_PATH)] = {
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
        router.routes[("GET", VIP_SETTINGS_PATH)] = {
            "rows": [{"mode": "carp", "interface": "em0", "subnet": "10.0.0.1", "vhid": "1"}]
        }
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
    """
    client, _router = _client_with_router(
        make_client,
        {
            ("GET", CERT_SEARCH_PATH): {
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
            },
            ("GET", GATEWAY_PATH): {"items": [{"name": "gw1", "status_translated": "Online"}]},
        },
    )
    try:
        client._safe_dict_post = AsyncMock(return_value={"message": "OK reload"})
        ok = await client.reload_interface("em0")
        assert ok is True

        certs = await client.get_certificates()
        assert "cert1" in certs and certs["cert1"]["issuer"] == "issuer"

        gws = await client.get_gateways()
        assert "gw1" in gws and gws["gw1"]["status"] == "online"
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_gateways_notices_and_close_notice_all(make_client: ClientType) -> None:
    """Verify gateway notices reporting and close-all notice handling.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _client_with_router(
        make_client,
        {("GET", GATEWAY_PATH): {"items": [{"name": "gw1", "status_translated": "OK"}]}},
    )
    try:
        gws = await client.get_gateways()
        assert "gw1" in gws and gws["gw1"]["status"] == "ok"

        # notices: include a pending notice
        router.routes[("GET", CORE_SYSTEM_STATUS_PATH)] = {
            "n1": {
                "statusCode": 1,
                "message": "m",
                "timestamp": int(datetime.now(tz=UTC).timestamp()),
            }
        }
        notices = await client.get_notices()
        assert notices["pending_notices_present"] is True

        # close_notice all: prepare multiple notices and simulate dismiss responses
        router.routes[("GET", CORE_SYSTEM_STATUS_PATH)] = {
            "n1": {"statusCode": 1},
            "n2": {"statusCode": 1},
        }
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
    """
    client, router = _client_with_router(
        make_client,
        {
            ("GET", CARP_VIP_STATUS_PATH): {"rows": "bad"},
            ("GET", VIP_SETTINGS_PATH): {"rows": "bad"},
        },
    )
    try:
        assert (await client.get_carp()).get("interfaces", []) == []

        router.routes[("GET", CARP_VIP_STATUS_PATH)] = {
            "rows": [
                {"mode": "other", "interface": "em9"},
                {"mode": "carp", "interface": "em0", "subnet": "10.0.0.1", "vhid": "11"},
            ]
        }
        router.routes[("GET", VIP_SETTINGS_PATH)] = {
            "rows": [{"mode": "carp", "interface": "em1", "subnet": "10.0.0.9", "vhid": "12"}]
        }
        carp = (await client.get_carp()).get("interfaces", [])
        assert len(carp) == 1
        assert carp[0]["interface"] == "em0"
        assert carp[0]["status"] == "DISABLED"

        router.routes[("GET", CARP_VIP_STATUS_PATH)] = {
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
        router.routes[("GET", VIP_SETTINGS_PATH)] = {"rows": "not-a-list"}
        carp = (await client.get_carp()).get("interfaces", [])
        assert len(carp) == 1
        assert carp[0]["interface"] == "wan"
        assert carp[0]["status"] == "BACKUP"

        router.routes[("GET", CARP_VIP_STATUS_PATH)] = {
            "rows": [{"mode": "carp", "interface": "opt1", "vhid": "30", "status": "MASTER"}]
        }
        router.routes[("GET", VIP_SETTINGS_PATH)] = {"rows": []}
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
    """
    client, _router = _client_with_router(
        make_client,
        {
            ("GET", CARP_VIP_STATUS_PATH): {
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
            ("GET", VIP_SETTINGS_PATH): {
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
        },
    )
    try:
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
    """
    client, _router = _client_with_router(
        make_client,
        {
            ("GET", CARP_VIP_STATUS_PATH): {
                "rows": [{"mode": "carp", "interface": "lan", "vhid": "10", "status": "MASTER"}]
            },
            ("GET", VIP_SETTINGS_PATH): {
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
        },
    )
    try:
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
        payload (dict[str, Any]): Raw endpoint payload returned by the fake Router.
        expected_state (str): Expected normalized CARP summary state.
    """
    client, _router = _client_with_router(
        make_client,
        {
            ("GET", CARP_VIP_STATUS_PATH): payload,
            ("GET", VIP_SETTINGS_PATH): payload,
        },
    )
    try:
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
    """
    payload = {
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
    client, _router = _client_with_router(
        make_client,
        {
            ("GET", CARP_VIP_STATUS_PATH): payload,
            ("GET", VIP_SETTINGS_PATH): payload,
        },
    )
    try:
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
    """
    client, _router = _client_with_router(
        make_client,
        {
            ("GET", CARP_VIP_STATUS_PATH): {
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
            ("GET", VIP_SETTINGS_PATH): {
                "rows": [
                    {
                        "mode": "carp",
                        "interface": "wan",
                        "subnet": "1.2.3.4",
                        "vhid": "1",
                    }
                ]
            },
        },
    )
    try:
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
    """Ensure one CARP call returns both interface and summary payloads with one request each.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _client_with_router(
        make_client,
        {
            ("GET", CARP_VIP_STATUS_PATH): {
                "rows": [{"mode": "carp", "status": "MASTER", "interface": "wan", "vhid": "1"}],
                "carp": {
                    "allow": "1",
                    "maintenancemode": False,
                    "demotion": "0",
                    "status_msg": "",
                },
            },
            ("GET", VIP_SETTINGS_PATH): {
                "rows": [{"mode": "carp", "interface": "wan", "subnet": "1.2.3.4", "vhid": "1"}]
            },
        },
    )
    try:
        snapshot = await client.get_carp()
        assert snapshot["interfaces"][0]["subnet"] == "1.2.3.4"
        assert snapshot["status_summary"]["state"] == "healthy"
        assert router.count("GET", CARP_VIP_STATUS_PATH) == 1
        assert router.count("GET", VIP_SETTINGS_PATH) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_system_actions_and_notice_closing_failure_paths(
    make_client: ClientType,
) -> None:
    """Verify failure-path behavior for system actions and notice closing.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _client_with_router(make_client, {})
    try:
        client._safe_dict_post = AsyncMock(return_value={"status": "failed"})
        assert await client.system_reboot() is False
        assert await client.system_halt() is None
        assert await client.send_wol("em0", "aa:bb:cc") is False

        client._safe_dict_post = AsyncMock(return_value={"status": "failed"})
        assert await client.close_notice("single-notice") is False

        router.routes[("GET", CORE_SYSTEM_STATUS_PATH)] = {
            "n1": {"statusCode": 1},
            "n2": {"statusCode": 2},
            "n3": {"statusCode": 1},
            "bad": "invalid",
        }
        client._safe_dict_post = AsyncMock(side_effect=[{"status": "ok"}, {"status": "failed"}])
        assert await client.close_notice("all") is False
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_version_switched_system_endpoints_fail_closed(make_client: ClientType) -> None:
    """Switched system endpoints should degrade without unsupported requests.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    absent = {
        ("GET", SYSTEM_INFORMATION_PATH): 404,
        ("GET", INTERFACE_OVERVIEW_PATH): 404,
        ("GET", CARP_VIP_STATUS_PATH): 404,
        ("GET", CORE_SYSTEM_STATUS_PATH): 404,
        ("GET", CERT_SEARCH_PATH): 404,
        ("GET", SYSTEM_TIME_PATH): 404,
    }
    client, router = _client_with_router(make_client, absent)
    try:
        assert await client.get_system_info() == {}
        assert await client.get_device_unique_id() is None
        assert await client.get_carp() == EMPTY_CARP_SNAPSHOT
        assert await client.get_notices() == EMPTY_NOTICES
        assert await client.close_notice("all") is False
        assert await client.get_certificates() == {}
        tz = await client._get_opnsense_timezone()
        now = datetime.now().astimezone()
        assert tz.utcoffset(now) == client._get_local_timezone().utcoffset(now)
        assert router.count("POST") == 0
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_certificates_handles_non_list_and_missing_description(
    make_client: ClientType,
) -> None:
    """Verify certificate parsing skips malformed rows and missing descriptions.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _client_with_router(make_client, {("GET", CERT_SEARCH_PATH): {"rows": "bad"}})
    try:
        assert await client.get_certificates() == {}

        router.routes[("GET", CERT_SEARCH_PATH)] = {
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
    """
    client, router = _client_with_router(make_client, {("GET", expected_info): {"name": "fw"}})
    try:
        client._use_snake_case = use_snake_case
        client._safe_dict_post = AsyncMock(side_effect=[{"status": "ok"}, {"message": "OK reload"}])

        await client.get_system_info()
        await client.close_notice("notice-1")
        await client.reload_interface("em0")

        assert router.count("GET", expected_info) == 1
        assert client._safe_dict_post.await_args_list[0].args[0] == expected_dismiss
        assert client._safe_dict_post.await_args_list[1].args[0] == expected_reload
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_system_branches_for_carp_candidate_and_notice_paths(make_client: ClientType) -> None:
    """Exercise additional CARP helper and notice-iteration branch behavior.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _client_with_router(
        make_client,
        {
            ("GET", CARP_VIP_STATUS_PATH): {
                "rows": [{"mode": "carp", "interface": "wan", "subnet": "1.2.3.4", "vhid": "1"}]
            },
            ("GET", VIP_SETTINGS_PATH): 404,
        },
    )
    try:
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

        vip_status_raw, merged = await client._fetch_and_merge_carp_vips()
        assert isinstance(vip_status_raw, dict)
        assert len(merged) == 1
        assert router.count("GET", VIP_SETTINGS_PATH) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_carp_absent_status_and_non_mapping_settings_keep_expected_rows(
    make_client: ClientType,
) -> None:
    """An absent VIP status gives an empty snapshot, and a non-mapping settings body keeps status rows.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    absent_client, _absent_router = _client_with_router(
        make_client, {("GET", CARP_VIP_STATUS_PATH): 404}
    )
    client, router = _client_with_router(
        make_client,
        {
            ("GET", CARP_VIP_STATUS_PATH): {
                "rows": [{"mode": "carp", "interface": "wan", "subnet": "1.2.3.4", "vhid": "1"}]
            },
            ("GET", VIP_SETTINGS_PATH): ["bad-settings-payload"],
        },
    )
    try:
        vip_status_raw, merged = await absent_client._fetch_and_merge_carp_vips()
        assert vip_status_raw == {}
        assert merged == []

        _vip_status_raw, merged = await client._fetch_and_merge_carp_vips()
        assert len(merged) == 1
        assert router.count("GET", VIP_SETTINGS_PATH) == 1
    finally:
        await client.async_close()
        await absent_client.async_close()


@pytest.mark.asyncio
async def test_notices_and_timezone_fallback_after_carp_helpers(make_client: ClientType) -> None:
    """Notice iteration counts only pending notices and naive timezone input falls back to local.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _router = _client_with_router(
        make_client,
        {
            ("GET", CORE_SYSTEM_STATUS_PATH): {
                "n1": {"statusCode": 2, "message": "resolved"},
                "n2": {"statusCode": 1, "message": "pending"},
            }
        },
    )
    try:
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
