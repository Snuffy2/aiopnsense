"""Tests for `aiopnsense.system`."""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta, tzinfo
from functools import partial
from typing import Any

import aiohttp
import pytest

from aiopnsense import OPNsenseClient
from aiopnsense.exceptions import OPNsenseMissingDeviceUniqueID
from tests.conftest import (
    FIRMWARE_STATUS_PATH,
    FakeResponse,
    FakeRouter,
    install_fake_router,
    make_mock_session_client,
)

ClientType = Callable[..., OPNsenseClient]

SYSTEM_TIME_PATH = "/api/diagnostics/system/system_time"
INTERFACE_EXPORT_PATH = "/api/interfaces/overview/export"
VIP_STATUS_PATH = "/api/diagnostics/interface/get_vip_status"
VIP_SETTINGS_PATH = "/api/interfaces/vip_settings/get"
CORE_STATUS_PATH = "/api/core/system/status"
CERT_SEARCH_PATH = "/api/trust/cert/search"
GATEWAY_STATUS_PATH = "/api/routes/gateway/status"
REBOOT_PATH = "/api/core/system/reboot"
HALT_PATH = "/api/core/system/halt"
WOL_PATH = "/api/wol/wol/set"
DISMISS_SNAKE_PATH = "/api/core/system/dismiss_status"
INFO_SNAKE_PATH = "/api/diagnostics/system/system_information"

# Firmware versions that select each Endpoint Style.
SNAKE_CASE_FIRMWARE = "26.1"
CAMEL_CASE_FIRMWARE = "25.1"


class _SequencedReplies(FakeResponse):
    """One prepared JSON response that answers successive requests with successive bodies.

    Use it where one public call sends several requests to the same path and the
    Router answers each one differently.
    """

    def __init__(self, bodies: list[Any]) -> None:
        """Store the bodies to hand out, one per request.

        Args:
            bodies (list[Any]): JSON bodies, in the order the Router answers them.
        """
        super().__init__()
        self._bodies = list(bodies)

    async def json(self, content_type: Any = None) -> Any:
        """Return the next body.

        Args:
            content_type (Any, optional): Requested content type (unused by the fake).

        Returns:
            Any: The next JSON body.
        """
        del content_type
        return self._bodies.pop(0)


def _make_router(
    make_client: ClientType,
    routes: Mapping[tuple[str, str], Any] | None = None,
    *,
    firmware: str | None = None,
    throw_errors: bool = False,
) -> tuple[OPNsenseClient, FakeRouter]:
    """Return a client backed by a fake Router that answers only the given routes.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        routes (Mapping[tuple[str, str], Any] | None): Router replies keyed by method and path.
        firmware (str | None): Firmware version the Router reports, or ``None`` for a Router
            whose firmware status is unavailable (Unknown Firmware).
        throw_errors (bool): Public ``throw_errors`` setting of the client.

    Returns:
        tuple[OPNsenseClient, FakeRouter]: The client and the Router that answers it.
    """
    client, session = make_mock_session_client(partial(make_client, throw_errors=throw_errors))
    all_routes: dict[tuple[str, str], Any] = dict(routes or {})
    if firmware is not None:
        all_routes[("GET", FIRMWARE_STATUS_PATH)] = {"product": {"product_version": firmware}}
    return client, install_fake_router(client, session, routes=all_routes)


def _posted_bodies(router: FakeRouter, path: str) -> list[Any]:
    """Return the JSON bodies of the POST requests the Router received for a path.

    Args:
        router (FakeRouter): Router that recorded the requests.
        path (str): Request path to match.

    Returns:
        list[Any]: Request bodies, in the order they were sent.
    """
    return [
        body
        for method, url, body in router.requests
        if method == "POST" and url.removeprefix("http://localhost") == path
    ]


def _assert_local_timezone(tz: tzinfo) -> None:
    """Assert that a timezone has the machine's current UTC offset.

    Args:
        tz (tzinfo): Timezone to compare with the local one.
    """
    now = datetime.now().astimezone()
    local_tz = now.tzinfo
    assert local_tz is not None
    assert tz.utcoffset(now) == local_tz.utcoffset(now)


def _carp_state(**overrides: Any) -> dict[str, Any]:
    """Return a healthy CARP status block, with optional field overrides.

    Args:
        overrides (Any): Fields that replace the healthy defaults.

    Returns:
        dict[str, Any]: The ``carp`` mapping of a VIP status payload.
    """
    return {
        "allow": "1",
        "demotion": "0",
        "maintenancemode": False,
        "status_msg": "",
    } | overrides


def _serve_carp(router: FakeRouter, status: Any, settings: Any) -> None:
    """Make the Router answer the CARP VIP status and settings reads.

    Args:
        router (FakeRouter): Router whose routes are replaced.
        status (Any): Body of the VIP status read.
        settings (Any): Body of the VIP settings read.
    """
    router.routes[("GET", VIP_STATUS_PATH)] = status
    router.routes[("GET", VIP_SETTINGS_PATH)] = settings


async def test_get_system_info(make_client: ClientType) -> None:
    """Verify system info is returned from the diagnostics endpoint.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _make_router(
        make_client,
        {("GET", INFO_SNAKE_PATH): {"name": "foo"}},
        firmware=SNAKE_CASE_FIRMWARE,
    )

    assert await client.get_system_info() == {"name": "foo"}

    assert router.count("GET", INFO_SNAKE_PATH) == 1


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
        instances (list[Any]): Interface rows the Router returns.
        expected_id (str | None): Optional preferred unique ID candidate.
        expected (str | None): Expected normalized unique ID result.
    """
    client, _router = _make_router(make_client, {("GET", INTERFACE_EXPORT_PATH): instances})

    assert await client.get_device_unique_id(expected_id=expected_id) == expected


@pytest.mark.parametrize("throw_errors", [False, True])
@pytest.mark.parametrize(
    "export",
    [
        pytest.param(404, id="endpoint-unavailable"),
        pytest.param([{"is_physical": False}], id="no-physical-mac"),
        pytest.param([], id="no-interfaces"),
    ],
)
async def test_get_device_unique_id_missing_follows_throw_errors_contract(
    make_client: ClientType, throw_errors: bool, export: Any
) -> None:
    """Verify a missing device ID is ``None`` with throw off and raises with throw on.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Whether the client raises public errors.
        export (Any): Router reply to the interface overview export.
    """
    client, _router = _make_router(
        make_client, {("GET", INTERFACE_EXPORT_PATH): export}, throw_errors=throw_errors
    )

    if throw_errors:
        with pytest.raises(OPNsenseMissingDeviceUniqueID):
            await client.get_device_unique_id()
    else:
        assert await client.get_device_unique_id() is None


async def test_get_opnsense_timezone_parse_and_fallback(make_client: ClientType) -> None:
    """Verify timezone parsing succeeds and falls back to local time on invalid data.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _make_router(make_client)

    for datetime_str, when, expected_offset in [
        ("2026-03-07 12:00:00 EST", datetime(2026, 3, 7, 12, 0, 0), timedelta(hours=-5)),
        ("2026-06-07 12:00:00 EDT", datetime(2026, 6, 7, 12, 0, 0), timedelta(hours=-4)),
        ("Sun Mar 22 21:36:07 EDT 2026", datetime(2026, 3, 22, 21, 36, 7), timedelta(hours=-4)),
    ]:
        router.routes[("GET", SYSTEM_TIME_PATH)] = {"datetime": datetime_str}
        parsed_tz = await client._get_opnsense_timezone()
        assert parsed_tz.utcoffset(when.replace(tzinfo=parsed_tz)) == expected_offset

    router.routes[("GET", SYSTEM_TIME_PATH)] = {"datetime": "not-a-datetime"}
    _assert_local_timezone(await client._get_opnsense_timezone())


@pytest.mark.parametrize(
    "time_reply",
    [
        pytest.param(aiohttp.ClientConnectionError("transient fetch error"), id="transport-error"),
        pytest.param(
            aiohttp.InvalidURL("https://user:password@api.example/diagnostics/system/system_time"),
            id="invalid-url",
        ),
    ],
)
async def test_get_opnsense_timezone_falls_back_when_time_read_fails(
    make_client: ClientType, time_reply: Exception
) -> None:
    """Verify a failed system-time read falls back to the local timezone.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        time_reply (Exception): Session error raised for the system-time read.
    """
    client, _router = _make_router(make_client, {("GET", SYSTEM_TIME_PATH): time_reply})

    _assert_local_timezone(await client._get_opnsense_timezone())


@pytest.mark.parametrize(
    ("time_reply", "throw_errors"),
    [
        pytest.param(404, False, id="endpoint-unavailable"),
        pytest.param(403, True, id="permission-denied-throwing"),
        pytest.param(aiohttp.ClientConnectionError("transient"), False, id="transport-error"),
        pytest.param({"datetime": "not-a-datetime"}, False, id="malformed-datetime"),
    ],
)
async def test_get_resolved_opnsense_timezone_returns_none_when_unresolvable(
    make_client: ClientType, time_reply: Any, throw_errors: bool
) -> None:
    """Verify resolved-only timezone lookup returns ``None`` instead of aborting enrichment.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        time_reply (Any): Router reply to the system-time read.
        throw_errors (bool): Whether the client raises public errors.
    """
    client, router = _make_router(
        make_client, {("GET", SYSTEM_TIME_PATH): time_reply}, throw_errors=throw_errors
    )

    assert await client._get_resolved_opnsense_timezone() is None

    assert router.count("GET", SYSTEM_TIME_PATH) == 1


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
        datetime_str (str): Datetime string the Router reports as its system time.
        expected_dt (datetime): Wall time used to evaluate the resolved timezone offset.
        expected_offset (timedelta): Expected UTC offset for the parsed timezone.
    """
    client, _router = _make_router(
        make_client, {("GET", SYSTEM_TIME_PATH): {"datetime": datetime_str}}
    )

    parsed_tz = await client._get_opnsense_timezone()

    assert parsed_tz.utcoffset(expected_dt.replace(tzinfo=parsed_tz)) == expected_offset


@pytest.mark.parametrize(
    ("reply_status", "expected_reboot"),
    [("ok", True), ("failed", False)],
)
async def test_system_actions_report_router_status(
    make_client: ClientType, reply_status: str, expected_reboot: bool
) -> None:
    """Verify reboot, halt and Wake-on-LAN post to the Router and report its status.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        reply_status (str): ``status`` field of every Router reply.
        expected_reboot (bool): Whether the actions that report success are expected to.
    """
    reply = {"status": reply_status}
    client, router = _make_router(
        make_client,
        {("POST", REBOOT_PATH): reply, ("POST", HALT_PATH): reply, ("POST", WOL_PATH): reply},
    )

    assert await client.system_reboot() is expected_reboot
    assert await client.system_halt() is None
    assert await client.send_wol("em0", "aa:bb:cc") is expected_reboot

    assert router.count("POST", REBOOT_PATH) == 1
    assert router.count("POST", HALT_PATH) == 1
    assert _posted_bodies(router, WOL_PATH) == [{"wake": {"interface": "em0", "mac": "aa:bb:cc"}}]


@pytest.mark.parametrize(
    ("firmware", "expected_path"),
    [
        pytest.param("25.1", "/api/diagnostics/interface/CarpStatus/maintenance", id="camelcase"),
        pytest.param(
            "26.1.10", "/api/diagnostics/interface/_carp_status/maintenance", id="below-threshold"
        ),
        pytest.param(
            "26.1.11", "/api/diagnostics/interface/carp_status/maintenance", id="at-threshold"
        ),
        pytest.param(
            "26.1.11_4",
            "/api/diagnostics/interface/carp_status/maintenance",
            id="at-threshold-with-revision",
        ),
        pytest.param(
            "26.2", "/api/diagnostics/interface/carp_status/maintenance", id="above-threshold"
        ),
        pytest.param(None, "/api/diagnostics/interface/_carp_status/maintenance", id="missing"),
        pytest.param(
            "not-a-version",
            "/api/diagnostics/interface/_carp_status/maintenance",
            id="not-comparable",
        ),
    ],
)
async def test_toggle_carp_maintenance_mode_uses_endpoint_for_firmware(
    make_client: ClientType, firmware: str | None, expected_path: str
) -> None:
    """CARP maintenance uses the new status endpoint only on known firmware that has it.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        firmware (str | None): Firmware version reported by the Router.
        expected_path (str): CARP maintenance endpoint the client is expected to post to.
    """
    client, router = _make_router(
        make_client, {("POST", expected_path): {"status": "ok"}}, firmware=firmware
    )

    assert await client.toggle_carp_maintenance_mode() is True

    assert router.count("POST", expected_path) == 1
    assert router.count("POST") == 1


async def test_get_certificates_handles_non_list_and_missing_description(
    make_client: ClientType,
) -> None:
    """Verify certificate parsing skips malformed rows and maps the remaining fields.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _make_router(make_client, {("GET", CERT_SEARCH_PATH): {"rows": "bad"}})
    assert await client.get_certificates() == {}

    router.routes[("GET", CERT_SEARCH_PATH)] = {
        "rows": [
            {"uuid": "skip-no-descr"},
            {
                "descr": "cert-ok",
                "uuid": "u1",
                "caref": "issuer",
                "rfc3280_purpose": "purpose",
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
    assert certs["cert-ok"]["uuid"] == "u1"
    assert certs["cert-ok"]["issuer"] == "issuer"
    assert certs["cert-ok"]["purpose"] == "purpose"
    assert certs["cert-ok"]["in_use"] is False
    assert certs["cert-used"]["in_use"] is True


async def test_get_gateways_normalizes_translated_status(make_client: ClientType) -> None:
    """Verify gateway rows are keyed by name with a lower-cased status.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _router = _make_router(
        make_client,
        {
            ("GET", GATEWAY_STATUS_PATH): {
                "items": [
                    {"name": "gw1", "status_translated": "Online"},
                    {"name": "gw2", "status_translated": "OK"},
                ]
            }
        },
    )

    gateways = await client.get_gateways()

    assert {name: gateway["status"] for name, gateway in gateways.items()} == {
        "gw1": "online",
        "gw2": "ok",
    }


async def test_get_notices_lists_only_pending_notices(make_client: ClientType) -> None:
    """Verify notices with a resolved status code are left out.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    created = int(datetime.now(tz=UTC).timestamp())
    client, _router = _make_router(
        make_client,
        {
            ("GET", CORE_STATUS_PATH): {
                "n1": {"statusCode": 2, "message": "resolved"},
                "n2": {"statusCode": 1, "message": "pending", "timestamp": created},
            }
        },
    )

    notices = await client.get_notices()

    assert notices["pending_notices_present"] is True
    assert [(n["id"], n["notice"]) for n in notices["pending_notices"]] == [("n2", "pending")]
    assert notices["pending_notices"][0]["created_at"] is not None


@pytest.mark.parametrize(
    ("reply_status", "expected"),
    [("ok", True), ("failed", False)],
)
async def test_close_notice_reports_router_status(
    make_client: ClientType, reply_status: str, expected: bool
) -> None:
    """Verify closing one notice posts its subject and reports the Router's status.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        reply_status (str): ``status`` field of the Router reply.
        expected (bool): Expected result of the call.
    """
    client, router = _make_router(
        make_client,
        {("POST", DISMISS_SNAKE_PATH): {"status": reply_status}},
        firmware=SNAKE_CASE_FIRMWARE,
    )

    assert await client.close_notice("single-notice") is expected

    assert _posted_bodies(router, DISMISS_SNAKE_PATH) == [{"subject": "single-notice"}]


@pytest.mark.parametrize(
    ("replies", "expected"),
    [
        pytest.param(["ok", "ok"], True, id="all-dismissed"),
        pytest.param(["ok", "failed"], False, id="one-failed"),
    ],
)
async def test_close_notice_all_dismisses_each_pending_notice(
    make_client: ClientType, replies: list[str], expected: bool
) -> None:
    """Verify closing all notices dismisses only pending ones and fails if any dismissal fails.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        replies (list[str]): ``status`` of the Router reply to each dismissal, in order.
        expected (bool): Expected result of the call.
    """
    client, router = _make_router(
        make_client,
        {
            ("GET", CORE_STATUS_PATH): {
                "n1": {"statusCode": 1},
                "n2": {"statusCode": 2},
                "n3": {"statusCode": 1},
                "bad": "invalid",
            },
            ("POST", DISMISS_SNAKE_PATH): _SequencedReplies([{"status": s} for s in replies]),
        },
        firmware=SNAKE_CASE_FIRMWARE,
    )

    assert await client.close_notice("all") is expected

    assert _posted_bodies(router, DISMISS_SNAKE_PATH) == [{"subject": "n1"}, {"subject": "n3"}]


async def test_get_opnsense_timezone_without_tzinfo_string_uses_local_fallback(
    make_client: ClientType,
) -> None:
    """Verify naive or empty datetime strings fall back to local timezone without a request.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _make_router(make_client)

    _assert_local_timezone(await client._get_opnsense_timezone("2026-03-07 12:00:00"))
    _assert_local_timezone(await client._get_opnsense_timezone(""))

    assert router.count() == 0


async def test_get_carp_handles_invalid_payloads_and_default_status(
    make_client: ClientType,
) -> None:
    """Verify CARP interface parsing tolerates malformed payloads and missing status.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _make_router(make_client)

    _serve_carp(router, {"rows": "bad"}, {"rows": "bad"})
    assert (await client.get_carp()).get("interfaces", []) == []

    _serve_carp(
        router,
        {
            "rows": [
                {"mode": "other", "interface": "em9"},
                {"mode": "carp", "interface": "em0", "subnet": "10.0.0.1", "vhid": "11"},
            ]
        },
        {"rows": [{"mode": "carp", "interface": "em1", "subnet": "10.0.0.9", "vhid": "12"}]},
    )
    carp = (await client.get_carp()).get("interfaces", [])
    assert len(carp) == 1
    assert carp[0]["interface"] == "em0"
    assert carp[0]["status"] == "DISABLED"

    _serve_carp(
        router,
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
    )
    carp = (await client.get_carp()).get("interfaces", [])
    assert len(carp) == 1
    assert carp[0]["interface"] == "wan"
    assert carp[0]["status"] == "BACKUP"

    _serve_carp(
        router,
        {"rows": [{"mode": "carp", "interface": "opt1", "vhid": "30", "status": "MASTER"}]},
        {"rows": []},
    )
    assert (await client.get_carp()).get("interfaces", []) == []


async def test_get_carp_matches_multiple_vips_on_same_interface(
    make_client: ClientType,
) -> None:
    """Verify CARP enrichment matches by VIP identity, not interface alone.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _make_router(make_client)
    _serve_carp(
        router,
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
    )

    carp = (await client.get_carp()).get("interfaces", [])

    assert len(carp) == 2
    by_subnet = {entry["subnet"]: entry for entry in carp}
    assert by_subnet["10.0.0.1"]["status"] == "MASTER"
    assert by_subnet["10.0.0.1"]["descr"] == "first"
    assert by_subnet["10.0.0.2"]["status"] == "BACKUP"
    assert by_subnet["10.0.0.2"]["descr"] == "second"


async def test_get_carp_returns_no_match_for_ambiguous_partial_key_collisions(
    make_client: ClientType,
) -> None:
    """Verify fallback selection rejects ambiguous VIP setting candidates.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _make_router(make_client)
    _serve_carp(
        router,
        {"rows": [{"mode": "carp", "interface": "lan", "vhid": "10", "status": "MASTER"}]},
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
    )

    assert (await client.get_carp()).get("interfaces", []) == []


@pytest.mark.parametrize(
    ("payload", "expected_state"),
    [
        (
            {
                "rows": [
                    {"mode": "carp", "status": "MASTER", "interface": "wan", "subnet": "1.2.3.4"}
                ],
                "carp": _carp_state(),
            },
            "healthy",
        ),
        (
            {"rows": [], "carp": _carp_state()},
            "not_configured",
        ),
        (
            {
                "rows": [
                    {"mode": "carp", "status": "MASTER", "interface": "wan", "subnet": "1.2.3.4"}
                ],
                "carp": _carp_state(maintenancemode=True),
            },
            "maintenance",
        ),
        (
            {
                "rows": [
                    {"mode": "carp", "status": "INIT", "interface": "wan", "subnet": "1.2.3.4"}
                ],
                "carp": _carp_state(demotion="2", status_msg="demoted"),
            },
            "degraded",
        ),
        (
            {
                "rows": [
                    {"mode": "carp", "status": "MASTER", "interface": "wan", "subnet": "1.2.3.4"}
                ],
                "carp": _carp_state(allow="0"),
            },
            "disabled",
        ),
        (
            {
                "rows": [
                    {"mode": "carp", "status": "MASTER", "interface": "wan", "subnet": "1.2.3.4"}
                ],
                "carp": _carp_state(status_msg=0),
            },
            "healthy",
        ),
        ({"rows": "bad", "carp": "bad"}, "unknown"),
        ({"rows": [{"mode": "carp", "status": "MASTER", "interface": "wan"}]}, "unknown"),
        (
            {
                "rows": [
                    {"mode": "carp", "status": "MASTER", "interface": "wan", "subnet": ""},
                    {"mode": "carp", "status": "BACKUP", "subnet": "1.2.3.4"},
                ],
                "carp": _carp_state(),
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
        payload (dict[str, Any]): Raw payload the Router returns for both CARP reads.
        expected_state (str): Expected normalized CARP summary state.
    """
    client, router = _make_router(make_client)
    _serve_carp(router, payload, payload)

    summary = dict((await client.get_carp()).get("status_summary", {}))

    assert summary["state"] == expected_state
    assert "vips" in summary
    assert "vip_count" in summary
    if isinstance(payload.get("carp"), dict) and not isinstance(
        payload["carp"].get("status_msg", ""), str
    ):
        assert summary["status_message"] == ""


async def test_get_carp_skips_whitespace_interface_values(
    make_client: ClientType,
) -> None:
    """Verify CARP merge drops entries whose interface is blank after normalization.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _make_router(make_client)
    payload = {
        "rows": [
            {"mode": "carp", "status": "MASTER", "interface": "   ", "subnet": "1.2.3.4"},
            {"mode": "carp", "status": "BACKUP", "interface": "  wan  ", "subnet": "5.6.7.8"},
        ],
        "carp": _carp_state(),
    }
    _serve_carp(router, payload, payload)

    snapshot = await client.get_carp()

    assert snapshot["interfaces"] == [
        {"interface": "wan", "mode": "carp", "status": "BACKUP", "subnet": "5.6.7.8"}
    ]
    assert snapshot["status_summary"]["vip_count"] == 1
    assert snapshot["status_summary"]["interfaces"] == ["wan"]


async def test_get_carp_status_uses_settings_merge_for_partial_rows(
    make_client: ClientType,
) -> None:
    """Verify summary uses the same status/settings merge as interface CARP discovery.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _make_router(make_client)
    _serve_carp(
        router,
        {
            "rows": [{"mode": "carp", "status": "MASTER", "interface": "wan", "vhid": "1"}],
            "carp": _carp_state(),
        },
        {"rows": [{"mode": "carp", "interface": "wan", "subnet": "1.2.3.4", "vhid": "1"}]},
    )

    summary = dict((await client.get_carp()).get("status_summary", {}))

    assert summary["state"] == "healthy"
    assert summary["vip_count"] == 1
    assert summary["interfaces"] == ["wan"]
    assert summary["vips"][0]["subnet"] == "1.2.3.4"


async def test_get_carp_returns_interfaces_and_summary_from_one_fetch(
    make_client: ClientType,
) -> None:
    """Ensure one CARP call returns both interface and summary payloads from one read each.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _make_router(make_client)
    _serve_carp(
        router,
        {
            "rows": [{"mode": "carp", "status": "MASTER", "interface": "wan", "vhid": "1"}],
            "carp": _carp_state(),
        },
        {"rows": [{"mode": "carp", "interface": "wan", "subnet": "1.2.3.4", "vhid": "1"}]},
    )

    snapshot = await client.get_carp()

    assert snapshot["interfaces"][0]["subnet"] == "1.2.3.4"
    assert snapshot["interfaces"][0]["status"] == "MASTER"
    summary = snapshot["status_summary"]
    assert summary["state"] == "healthy"
    assert summary["enabled"] is True
    assert summary["vip_count"] == 1
    assert summary["master_count"] == 1
    assert router.count("GET", VIP_STATUS_PATH) == 1
    assert router.count("GET", VIP_SETTINGS_PATH) == 1


@pytest.mark.parametrize(
    "settings",
    [
        pytest.param(404, id="settings-unavailable"),
        pytest.param(["bad-settings-payload"], id="settings-not-a-mapping"),
    ],
)
async def test_get_carp_uses_status_rows_when_settings_unusable(
    make_client: ClientType, settings: Any
) -> None:
    """Verify CARP discovery falls back to the status rows alone.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        settings (Any): Router reply to the VIP settings read.
    """
    client, router = _make_router(make_client)
    _serve_carp(
        router,
        {"rows": [{"mode": "carp", "interface": "wan", "subnet": "1.2.3.4", "vhid": "1"}]},
        settings,
    )

    interfaces = (await client.get_carp())["interfaces"]

    assert [(row["interface"], row["subnet"]) for row in interfaces] == [("wan", "1.2.3.4")]


async def test_carp_row_helpers_normalize_and_match_rows(make_client: ClientType) -> None:
    """Verify the pure CARP row helpers, which make no request.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _session = make_mock_session_client(make_client)

    parsed = client._parse_carp_vip_rows(
        [
            "invalid-row",
            {"mode": "other", "status": "master"},
            {"mode": "carp", "interface": "wan", "subnet": "1.2.3.4", "vhid": "1"},
        ]
    )
    assert len(parsed) == 1
    assert parsed[0]["status"] == "DISABLED"

    indexes = client._build_carp_settings_indexes(
        [{"interface": "wan", "vhid": "1", "subnet": "1.2.3.4"}]
    )
    assert (
        client._find_carp_settings_match(
            {"interface": "wan", "vhid": "1", "subnet": "1.2.3.4"}, indexes
        )
        is not None
    )


async def test_version_switched_system_endpoints_fail_closed(make_client: ClientType) -> None:
    """Missing system endpoints degrade to empty results without repeated or unsupported fetches.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _make_router(make_client, firmware=SNAKE_CASE_FIRMWARE)

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
    _assert_local_timezone(await client._get_opnsense_timezone())

    assert router.count("POST") == 0
    for path in (
        INFO_SNAKE_PATH,
        INTERFACE_EXPORT_PATH,
        VIP_STATUS_PATH,
        CERT_SEARCH_PATH,
        SYSTEM_TIME_PATH,
    ):
        assert router.count("GET", path) == 1
    # get_notices recorded the missing status endpoint, so close_notice did not ask again.
    assert router.count("GET", CORE_STATUS_PATH) == 1


@pytest.mark.parametrize(
    ("firmware", "expected_info", "expected_dismiss", "expected_reload"),
    [
        (
            SNAKE_CASE_FIRMWARE,
            "/api/diagnostics/system/system_information",
            "/api/core/system/dismiss_status",
            "/api/interfaces/overview/reload_interface/em0",
        ),
        (
            CAMEL_CASE_FIRMWARE,
            "/api/diagnostics/system/systemInformation",
            "/api/core/system/dismissStatus",
            "/api/interfaces/overview/reloadInterface/em0",
        ),
    ],
)
async def test_system_switched_endpoints_follow_selected_case(
    make_client: ClientType,
    firmware: str,
    expected_info: str,
    expected_dismiss: str,
    expected_reload: str,
) -> None:
    """Verify switched system endpoints follow the Endpoint Style of the reported firmware.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        firmware (str): Firmware version reported by the Router.
        expected_info (str): Expected system information endpoint path.
        expected_dismiss (str): Expected notice-dismiss endpoint path.
        expected_reload (str): Expected interface-reload endpoint path.
    """
    client, router = _make_router(
        make_client,
        {
            ("GET", expected_info): {"name": "fw"},
            ("POST", expected_dismiss): {"status": "ok"},
            ("POST", expected_reload): {"message": "OK reload"},
        },
        firmware=firmware,
    )

    assert await client.get_system_info() == {"name": "fw"}
    assert await client.close_notice("notice-1") is True
    assert await client.reload_interface("em0") is True

    assert router.count("GET", expected_info) == 1
    assert router.count("POST", expected_dismiss) == 1
    assert router.count("POST", expected_reload) == 1
