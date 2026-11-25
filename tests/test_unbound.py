"""Tests for `aiopnsense.unbound`."""

import asyncio
from collections.abc import Callable
import logging
from typing import Any

import aiohttp
import pytest

from aiopnsense import (
    OPNsenseClient,
    OPNsenseConnectionError,
    OPNsenseError,
    OPNsenseInvalidURL,
    OPNsensePrivilegeMissing,
    OPNsenseTimeoutError,
)
from tests.conftest import (
    FIRMWARE_STATUS_PATH,
    FakeRouter,
    HeldResponse,
    MalformedJSONResponse,
    make_router_client,
)

ClientType = Callable[..., OPNsenseClient]

SETTINGS_GET = "/api/unbound/settings/get"
SETTINGS_SET = "/api/unbound/settings/set"
SEARCH_DNSBL = "/api/unbound/settings/search_dnsbl"
DNSBL_APPLY = "/api/unbound/service/dnsbl"
RESTART = "/api/unbound/service/restart"
EXTENDED_FIRMWARE = "25.7.8"
LEGACY_FIRMWARE = "25.7.7"
OK_APPLIED = {"status": "OK applied"}
SAVED = {"result": "saved"}
RESTARTED = {"response": "OK"}
TRANSPORT_FAILURE = object()
"""Route value for a reply that fails at the session with a connection error."""

LEGACY_FIRMWARE_VERSIONS = ["25.7.7", "25.7.7_1"]
EXTENDED_FIRMWARE_VERSIONS = ["25.7.8", "26.1"]
UNKNOWN_FIRMWARE = [
    pytest.param(None, id="missing"),
    pytest.param("not-a-version", id="not-comparable"),
]

LEGACY_DNSBL_SELECTED = {
    "enabled": "1",
    "safesearch": "0",
    "nxdomain": "1",
    "address": "0.0.0.0",
    "type": {
        "ads": {"selected": 1},
        "malware": {"selected": 0},
        "tracking": {"selected": "1"},
    },
    "lists": {
        "list-a": {"selected": 1},
        "list-b": {"selected": 1},
        "list-c": {"selected": "1"},
    },
    "whitelists": {"allow-a": {"selected": 1}},
    "blocklists": {"deny-a": {"selected": 1}},
    "wildcards": {"wild-a": {"selected": 1}},
}


def _toggle_path(uuid: str, enabled: bool) -> str:
    """Return the extended DNSBL toggle endpoint.

    Args:
        uuid (str): DNSBL row UUID.
        enabled (bool): Requested state.

    Returns:
        str: Toggle endpoint path.
    """
    return f"/api/unbound/settings/toggle_dnsbl/{uuid}/{'1' if enabled else '0'}"


def _reply(value: Any) -> Any:
    """Return the route value for a Router reply.

    Args:
        value (Any): Reply body, or ``TRANSPORT_FAILURE`` for a connection error at the session.

    Returns:
        Any: ``value``, or a fresh connection error when the reply is a transport failure.
    """
    if value is TRANSPORT_FAILURE:
        return aiohttp.ClientConnectionError("connection reset")
    return value


def _legacy_routes(
    payload: Any,
    *,
    save: Any = SAVED,
    apply: Any = OK_APPLIED,
    restart: Any = RESTARTED,
) -> dict[tuple[str, str], Any]:
    """Return the routes of a Router that serves the legacy Unbound blocklist API.

    Args:
        payload (Any): Legacy settings the Router returns on read.
        save (Any): Reply to the settings save.
        apply (Any): Reply to the DNSBL apply GET.
        restart (Any): Reply to the service restart.

    Returns:
        dict[tuple[str, str], Any]: Routes for the legacy read, save, apply and restart.
    """
    return {
        ("GET", SETTINGS_GET): _reply(payload),
        ("POST", SETTINGS_SET): _reply(save),
        ("GET", DNSBL_APPLY): _reply(apply),
        ("POST", RESTART): _reply(restart),
    }


def _both_apis_routes(legacy_payload: dict[str, Any]) -> dict[tuple[str, str], Any]:
    """Return the routes of a Router that serves both blocklist APIs.

    Args:
        legacy_payload (dict[str, Any]): Legacy settings the Router returns on read.

    Returns:
        dict[tuple[str, str], Any]: Routes for the legacy and the extended blocklist APIs.
    """
    return {
        **_legacy_routes(legacy_payload),
        ("GET", SEARCH_DNSBL): {"rows": [{"uuid": "uuid1", "enabled": "1"}]},
        ("POST", DNSBL_APPLY): {"status": "OK"},
        ("POST", _toggle_path("uuid1", True)): {"result": "Enabled"},
        ("POST", _toggle_path("uuid1", False)): {"result": "Disabled"},
    }


def _blocklist_request_count(router: FakeRouter) -> int:
    """Count the requests the Router received other than firmware status reads.

    Args:
        router (FakeRouter): Router that recorded the requests.

    Returns:
        int: Number of recorded requests that were not firmware status reads.
    """
    return router.count() - router.count("GET", FIRMWARE_STATUS_PATH)


async def test_get_unbound_blocklist_returns_uuid_mapping(make_client: ClientType) -> None:
    """The DNSBL search response should be normalized into a UUID-keyed mapping.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = make_router_client(
        make_client,
        {
            ("GET", SEARCH_DNSBL): {
                "rows": [
                    {"uuid": "dnsbl1", "enabled": "1"},
                    {"uuid": "dnsbl2", "enabled": "0"},
                    {"no_uuid": "skip-me"},
                    "bad-row",
                ]
            }
        },
        firmware=EXTENDED_FIRMWARE,
    )

    result = await client.get_unbound_blocklist()

    assert result == {
        "dnsbl1": {"uuid": "dnsbl1", "enabled": "1"},
        "dnsbl2": {"uuid": "dnsbl2", "enabled": "0"},
    }
    assert router.count("GET", SEARCH_DNSBL) == 1


@pytest.mark.parametrize("api_response", [{}, {"rows": []}, {"rows": "not-a-list"}, []])
async def test_get_unbound_blocklist_handles_empty_or_invalid_responses(
    make_client: ClientType, api_response: object
) -> None:
    """Malformed or empty DNSBL responses should normalize to an empty mapping.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        api_response (object): Opaque malformed or empty API response.
    """
    client, _router = make_router_client(
        make_client, {("GET", SEARCH_DNSBL): api_response}, firmware=EXTENDED_FIRMWARE
    )

    assert await client.get_unbound_blocklist() == {}


@pytest.mark.parametrize(
    ("status", "throw_errors"),
    [
        pytest.param(404, False, id="404-errors-off"),
        pytest.param(404, True, id="404-errors-on"),
    ],
)
async def test_get_unbound_blocklist_returns_empty_when_endpoint_unavailable(
    make_client: ClientType, status: int, throw_errors: bool
) -> None:
    """When DNSBL endpoint is unavailable, blocklist retrieval should fail closed.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        status (int): HTTP status the Router answers the DNSBL search with.
        throw_errors (bool): Client error propagation setting.
    """
    client, router = make_router_client(
        make_client,
        {("GET", SEARCH_DNSBL): status},
        firmware=EXTENDED_FIRMWARE,
        throw_errors=throw_errors,
    )

    assert await client.get_unbound_blocklist() == {}
    assert router.count("GET", SEARCH_DNSBL) == 1


@pytest.mark.parametrize(
    ("firmware", "endpoint"),
    [(EXTENDED_FIRMWARE, SEARCH_DNSBL), (LEGACY_FIRMWARE, SETTINGS_GET)],
    ids=["extended", "legacy"],
)
@pytest.mark.parametrize(
    ("reply", "strict_error", "request_count"),
    [
        pytest.param(403, OPNsensePrivilegeMissing, 1, id="forbidden"),
        pytest.param(TimeoutError("timed out"), OPNsenseTimeoutError, 2, id="timeout"),
        pytest.param(MalformedJSONResponse(), OPNsenseError, 2, id="undecodable"),
    ],
)
@pytest.mark.parametrize("throw_errors", [False, True], ids=["default", "strict"])
async def test_get_unbound_blocklist_failed_read(
    make_client: ClientType,
    firmware: str,
    endpoint: str,
    reply: object,
    strict_error: type[OPNsenseError],
    request_count: int,
    throw_errors: bool,
) -> None:
    """Verify unreadable inventories follow error policy, including cached denial.

    Args:
        make_client (ClientType): Fixture factory returning client instances.
        firmware (str): Firmware version selecting the read endpoint.
        endpoint (str): Settings endpoint expected for the firmware.
        reply (object): Failure returned by the fake Router.
        strict_error (type[OPNsenseError]): Error the strict client propagates.
        request_count (int): Requests expected across two suppressed reads.
        throw_errors (bool): Client error propagation setting.
    """
    client, router = make_router_client(
        make_client, {("GET", endpoint): reply}, firmware=firmware, throw_errors=throw_errors
    )

    if throw_errors:
        with pytest.raises(strict_error):
            await client.get_unbound_blocklist()
        assert router.count("GET", endpoint) == 1
    else:
        results = [await client.get_unbound_blocklist(), await client.get_unbound_blocklist()]
        assert results == [None, None]
        assert router.count("GET", endpoint) == request_count


@pytest.mark.parametrize("throw_errors", [False, True], ids=["default", "strict"])
async def test_get_unbound_blocklist_legacy_endpoint_missing(
    make_client: ClientType, throw_errors: bool
) -> None:
    """Verify a missing legacy endpoint supplies empty inventory or raises in strict mode.

    Args:
        make_client (ClientType): Fixture factory returning client instances.
        throw_errors (bool): Client error propagation setting.
    """
    client, router = make_router_client(
        make_client,
        {("GET", SETTINGS_GET): 404},
        firmware=LEGACY_FIRMWARE,
        throw_errors=throw_errors,
    )

    if throw_errors:
        with pytest.raises(OPNsenseConnectionError):
            await client.get_unbound_blocklist()
    else:
        assert await client.get_unbound_blocklist() == {}
    assert router.count("GET", SETTINGS_GET) == 1


@pytest.mark.parametrize("firmware_version", ["25.7.7", "25.7.7_1"])
async def test_get_unbound_blocklist_returns_legacy_payload_for_older_firmware(
    make_client: ClientType,
    firmware_version: str,
) -> None:
    """Older firmware should return the legacy DNSBL payload under the legacy key.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        firmware_version (str): Older firmware version requiring the legacy endpoint.
    """
    client, router = make_router_client(
        make_client,
        {("GET", SETTINGS_GET): {"unbound": {"dnsbl": LEGACY_DNSBL_SELECTED}}},
        firmware=firmware_version,
    )

    result = await client.get_unbound_blocklist()

    assert result == {
        "legacy": {
            "enabled": "1",
            "safesearch": "0",
            "nxdomain": "1",
            "address": "0.0.0.0",
            "type": "ads,tracking",
            "lists": "list-a,list-b,list-c",
            "whitelists": "allow-a",
            "blocklists": "deny-a",
            "wildcards": "wild-a",
        }
    }
    assert router.count("GET", SETTINGS_GET) == 1
    assert router.count("GET", SEARCH_DNSBL) == 0


@pytest.mark.parametrize(
    "api_response",
    [
        {"unbound": []},
        {"unbound": {"dnsbl": []}},
    ],
)
async def test_get_unbound_blocklist_returns_empty_for_invalid_legacy_payloads(
    make_client: ClientType,
    api_response: dict[str, Any],
) -> None:
    """Verify malformed legacy DNSBL payloads normalize to an empty mapping.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        api_response (dict[str, Any]): Legacy API payload returned by OPNsense.
    """
    client, router = make_router_client(
        make_client, {("GET", SETTINGS_GET): api_response}, firmware=LEGACY_FIRMWARE
    )

    assert await client.get_unbound_blocklist() == {"legacy": {}}
    assert router.count("GET", SETTINGS_GET) == 1


async def test_get_unbound_blocklist_legacy_uses_empty_strings_for_non_mapping_selector_groups(
    make_client: ClientType,
) -> None:
    """Verify invalid selector groups in legacy DNSBL payloads normalize to empty strings.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, _router = make_router_client(
        make_client,
        {
            ("GET", SETTINGS_GET): {
                "unbound": {
                    "dnsbl": {
                        "enabled": "1",
                        "safesearch": "0",
                        "nxdomain": "1",
                        "address": "0.0.0.0",
                        "type": "invalid",
                        "lists": "invalid",
                        "whitelists": "invalid",
                        "blocklists": "invalid",
                        "wildcards": "invalid",
                    }
                }
            }
        },
        firmware=LEGACY_FIRMWARE,
    )

    assert await client.get_unbound_blocklist() == {
        "legacy": {
            "enabled": "1",
            "safesearch": "0",
            "nxdomain": "1",
            "address": "0.0.0.0",
            "type": "",
            "lists": "",
            "whitelists": "",
            "blocklists": "",
            "wildcards": "",
        }
    }


@pytest.mark.parametrize(
    ("method_name", "uuid", "toggle_reply", "apply_reply", "expected", "applied"),
    [
        ("enable_unbound_blocklist", "uuid1", {"result": "Enabled"}, OK_APPLIED, True, True),
        ("disable_unbound_blocklist", "uuid1", {"result": "Disabled"}, OK_APPLIED, True, True),
        ("enable_unbound_blocklist", None, {"result": "Enabled"}, OK_APPLIED, False, False),
        ("enable_unbound_blocklist", "uuid1", {"result": "failed"}, OK_APPLIED, False, False),
        ("enable_unbound_blocklist", "uuid1", {"result": "Disabled"}, OK_APPLIED, False, False),
        ("disable_unbound_blocklist", "uuid1", {"result": "Enabled"}, OK_APPLIED, False, False),
        (
            "enable_unbound_blocklist",
            "uuid1",
            {"result": "Enabled"},
            {"status": "failed"},
            False,
            True,
        ),
    ],
)
async def test_enable_disable_unbound_blocklist_extended(
    make_client: ClientType,
    method_name: str,
    uuid: str | None,
    toggle_reply: dict[str, Any],
    apply_reply: dict[str, Any],
    expected: bool,
    applied: bool,
) -> None:
    """Extended toggles need a UUID, a save result matching the requested state, and an OK apply.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        method_name (str): Client toggle method name.
        uuid (str | None): Optional DNSBL row UUID.
        toggle_reply (dict[str, Any]): Reply to the toggle request.
        apply_reply (dict[str, Any]): Reply to the DNSBL apply.
        expected (bool): Expected operation result.
        applied (bool): Whether the DNSBL apply should have been sent.
    """
    enabled = method_name == "enable_unbound_blocklist"
    client, router = make_router_client(
        make_client,
        {
            ("POST", _toggle_path("uuid1", enabled)): toggle_reply,
            ("POST", DNSBL_APPLY): apply_reply,
        },
        firmware=EXTENDED_FIRMWARE,
    )

    assert await getattr(client, method_name)(uuid) is expected
    assert (router.count("POST", DNSBL_APPLY) == 1) is applied
    assert router.count("GET", DNSBL_APPLY) == 0
    if uuid is None:
        assert _blocklist_request_count(router) == 0


@pytest.mark.parametrize(
    ("method_name", "expected_enabled"),
    [("enable_unbound_blocklist", "1"), ("disable_unbound_blocklist", "0")],
)
async def test_enable_disable_unbound_blocklist_legacy_for_older_firmware(
    make_client: ClientType,
    method_name: str,
    expected_enabled: str,
    legacy_dnsbl_payload: dict[str, Any],
) -> None:
    """Older firmware saves the requested state, applies the DNSBL with a GET, then restarts.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        method_name (str): Client method under test.
        expected_enabled (str): Expected enabled value in the saved payload.
        legacy_dnsbl_payload (dict[str, Any]): Legacy DNSBL settings payload.
    """
    legacy_dnsbl_payload["unbound"]["dnsbl"]["type"] = {"ads": {"selected": 1}}
    legacy_dnsbl_payload["unbound"]["dnsbl"]["lists"] = {"list-a": {"selected": 1}}
    client, router = make_router_client(
        make_client, _legacy_routes(legacy_dnsbl_payload), firmware=LEGACY_FIRMWARE
    )

    assert await getattr(client, method_name)() is True

    assert router.bodies("POST", SETTINGS_SET) == [
        {
            "unbound": {
                "dnsbl": {
                    "enabled": expected_enabled,
                    "safesearch": "0",
                    "nxdomain": "1",
                    "address": "0.0.0.0",
                    "type": "ads",
                    "lists": "list-a",
                    "whitelists": "",
                    "blocklists": "",
                    "wildcards": "",
                }
            }
        }
    ]
    assert router.count("GET", DNSBL_APPLY) == 1
    assert router.count("POST", DNSBL_APPLY) == 0
    assert router.count("POST", RESTART) == 1


async def test_enable_unbound_blocklist_legacy_returns_false_when_state_cannot_be_loaded(
    make_client: ClientType,
) -> None:
    """Legacy DNSBL updates fail closed when current settings cannot be read.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = make_router_client(
        make_client,
        _legacy_routes({"unbound": {"dnsbl": []}}),
        firmware=LEGACY_FIRMWARE,
    )

    assert await client.enable_unbound_blocklist() is False
    assert router.count("POST", SETTINGS_SET) == 0
    assert router.count("GET", DNSBL_APPLY) == 0


@pytest.mark.parametrize(
    "save_reply",
    [
        pytest.param(TRANSPORT_FAILURE, id="transport-failure"),
        {},
        {"result": "failed"},
        {"result": "SAVED"},
        [],
    ],
)
async def test_enable_unbound_blocklist_legacy_does_not_apply_or_restart_after_failed_save(
    make_client: ClientType,
    save_reply: Any,
    legacy_dnsbl_payload: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Legacy DNSBL updates stop before apply and restart when the save is not confirmed.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        save_reply (Any): Reply to the legacy settings save.
        legacy_dnsbl_payload (dict[str, Any]): Legacy DNSBL settings payload.
        caplog (pytest.LogCaptureFixture): Logs emitted while rejecting the save.
    """
    client, router = make_router_client(
        make_client,
        _legacy_routes(legacy_dnsbl_payload, save=save_reply),
        firmware=LEGACY_FIRMWARE,
    )

    assert await client.enable_unbound_blocklist() is False
    assert any(r.levelno == logging.ERROR and SETTINGS_SET in r.message for r in caplog.records)
    assert router.count("POST", SETTINGS_SET) == 1
    assert router.count("GET", DNSBL_APPLY) == 0
    assert router.count("POST", RESTART) == 0


@pytest.mark.parametrize(
    "apply_reply",
    [
        pytest.param(TRANSPORT_FAILURE, id="transport-failure"),
        {"status": None},
        {"status": 1},
        {"status": "failed"},
        [],
    ],
)
async def test_enable_unbound_blocklist_legacy_returns_false_for_bad_apply_status(
    make_client: ClientType,
    apply_reply: Any,
    legacy_dnsbl_payload: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Legacy DNSBL updates fail closed, without a restart, on a bad apply response.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        apply_reply (Any): Reply to the DNSBL apply.
        legacy_dnsbl_payload (dict[str, Any]): Legacy DNSBL settings payload.
        caplog (pytest.LogCaptureFixture): Logs emitted while rejecting the apply.
    """
    client, router = make_router_client(
        make_client,
        _legacy_routes(legacy_dnsbl_payload, apply=apply_reply),
        firmware=LEGACY_FIRMWARE,
    )

    assert await client.enable_unbound_blocklist() is False
    assert any(r.levelno == logging.ERROR and DNSBL_APPLY in r.message for r in caplog.records)
    assert router.count("GET", DNSBL_APPLY) == 1
    assert router.count("POST", RESTART) == 0


@pytest.mark.parametrize(
    ("restart_reply", "expected"),
    [
        ({"response": "OK"}, True),
        ({"response": "failed"}, False),
        ({"status": "OK"}, False),
        pytest.param(TRANSPORT_FAILURE, False, id="transport-failure"),
    ],
)
async def test_enable_unbound_blocklist_legacy_requires_restart_response(
    make_client: ClientType,
    restart_reply: Any,
    expected: bool,
    legacy_dnsbl_payload: dict[str, Any],
) -> None:
    """The legacy restart reports its outcome under ``response``.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        restart_reply (Any): Reply to the service restart.
        expected (bool): Expected Toggle result.
        legacy_dnsbl_payload (dict[str, Any]): Legacy DNSBL settings payload.
    """
    client, _router = make_router_client(
        make_client,
        _legacy_routes(legacy_dnsbl_payload, restart=restart_reply),
        firmware=LEGACY_FIRMWARE,
    )

    assert await client.enable_unbound_blocklist() is expected


async def test_legacy_blocklist_completion_log_omits_configuration_and_response_data(
    make_client: ClientType,
    caplog: pytest.LogCaptureFixture,
    legacy_dnsbl_payload: dict[str, Any],
) -> None:
    """Legacy completion logs should report outcome without logging API data.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        caplog (pytest.LogCaptureFixture): Captured logs for the public operation.
        legacy_dnsbl_payload (dict[str, Any]): Legacy DNSBL settings returned before the save.
    """
    legacy_dnsbl_payload["unbound"]["dnsbl"]["address"] = "CONFIG_MARKER"
    client, _router = make_router_client(
        make_client,
        _legacy_routes(
            legacy_dnsbl_payload,
            save={"result": "saved", "detail": "SAVE_RESPONSE_MARKER"},
            apply={"status": "OK applied", "detail": "APPLY_RESPONSE_MARKER"},
            restart={"response": "OK", "detail": "RESTART_RESPONSE_MARKER"},
        ),
        firmware=LEGACY_FIRMWARE,
    )

    with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
        result = await client.enable_unbound_blocklist()

    assert result is True
    assert "CONFIG_MARKER" not in caplog.text
    assert "SAVE_RESPONSE_MARKER" not in caplog.text
    assert "APPLY_RESPONSE_MARKER" not in caplog.text
    assert "RESTART_RESPONSE_MARKER" not in caplog.text


@pytest.mark.parametrize("method_name", ["enable_unbound_blocklist", "disable_unbound_blocklist"])
@pytest.mark.parametrize("failing_step", ["save", "apply", "restart"])
@pytest.mark.parametrize(
    ("make_error", "expected_error"),
    [
        (lambda: aiohttp.ClientError("boom"), OPNsenseConnectionError),
        (
            lambda: aiohttp.InvalidURL("https://user:password@api.example/api/unbound/x"),
            OPNsenseInvalidURL,
        ),
        (lambda: TimeoutError("slow"), OPNsenseTimeoutError),
    ],
)
@pytest.mark.parametrize("throw_errors", [False, True])
async def test_legacy_unbound_blocklist_request_failure_follows_throw_errors(
    make_client: ClientType,
    caplog: pytest.LogCaptureFixture,
    legacy_dnsbl_payload: dict[str, Any],
    method_name: str,
    failing_step: str,
    make_error: Callable[[], Exception],
    expected_error: type[Exception],
    throw_errors: bool,
) -> None:
    """Verify a failed legacy save, apply or restart request follows ``throw_errors``.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        caplog (pytest.LogCaptureFixture): Log capture used to check the failure log records.
        legacy_dnsbl_payload (dict[str, Any]): Legacy DNSBL settings returned before the save.
        method_name (str): Client toggle method name.
        failing_step (str): Legacy step whose request fails: ``save``, ``apply`` or ``restart``.
        make_error (Callable[[], Exception]): Factory for a fresh exception, because a raised
            public error is marked as logged.
        expected_error (type[Exception]): Public error expected when errors are thrown.
        throw_errors (bool): Client error propagation setting.
    """
    replies: dict[str, Any] = {"save": SAVED, "apply": OK_APPLIED, "restart": RESTARTED}
    replies[failing_step] = make_error()
    client, router = make_router_client(
        make_client,
        _legacy_routes(legacy_dnsbl_payload, **replies),
        firmware=LEGACY_FIRMWARE,
        throw_errors=throw_errors,
    )

    with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
        if throw_errors:
            with pytest.raises(expected_error):
                await getattr(client, method_name)()
        else:
            assert await getattr(client, method_name)() is False

    # Each step is sent once, and none after the failing one.
    sent = {
        "save": (1, 0, 0),
        "apply": (1, 1, 0),
        "restart": (1, 1, 1),
    }[failing_step]
    assert (
        router.count("POST", SETTINGS_SET),
        router.count("GET", DNSBL_APPLY),
        router.count("POST", RESTART),
    ) == sent
    assert "password" not in caplog.text
    # With errors off, a failed save or apply logs the transport cause and the failed Toggle step.
    expected_records = 2 if not throw_errors and failing_step in {"save", "apply"} else 1
    failure_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(failure_records) == expected_records


@pytest.mark.parametrize("method_name", ["enable_unbound_blocklist", "disable_unbound_blocklist"])
@pytest.mark.parametrize(
    ("make_apply_error", "expected_error"),
    [
        (lambda: aiohttp.ClientError("boom"), OPNsenseConnectionError),
        (
            lambda: aiohttp.InvalidURL(
                "https://user:password@api.example/api/unbound/service/dnsbl"
            ),
            OPNsenseInvalidURL,
        ),
        (lambda: TimeoutError("slow"), OPNsenseTimeoutError),
    ],
)
@pytest.mark.parametrize("throw_errors", [False, True])
async def test_toggle_unbound_blocklist_apply_exception_follows_throw_errors(
    make_client: ClientType,
    caplog: pytest.LogCaptureFixture,
    method_name: str,
    make_apply_error: Callable[[], Exception],
    expected_error: type[Exception],
    throw_errors: bool,
) -> None:
    """Verify a failed DNSBL apply request returns ``False`` or raises per ``throw_errors``.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        caplog (pytest.LogCaptureFixture): Log capture used to check the failure is logged once.
        method_name (str): Client toggle method name.
        make_apply_error (Callable[[], Exception]): Factory for a fresh apply-phase exception,
            because a raised public error is marked as logged.
        expected_error (type[Exception]): Public error expected when errors are thrown.
        throw_errors (bool): Client error propagation setting.
    """
    enabled = method_name.startswith("enable")
    client, router = make_router_client(
        make_client,
        {
            ("POST", _toggle_path("uuid1", enabled)): {
                "result": "Enabled" if enabled else "Disabled"
            },
            ("POST", DNSBL_APPLY): make_apply_error(),
        },
        firmware=EXTENDED_FIRMWARE,
        throw_errors=throw_errors,
    )

    with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
        if throw_errors:
            with pytest.raises(expected_error):
                await getattr(client, method_name)("uuid1")
        else:
            assert await getattr(client, method_name)("uuid1") is False

    assert router.count("POST", DNSBL_APPLY) == 1
    failure_records = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(failure_records) == 1
    assert "password" not in caplog.text


@pytest.mark.parametrize("throw_errors", [False, True])
async def test_toggle_unbound_blocklist_apply_cancellation_propagates(
    make_client: ClientType,
    throw_errors: bool,
) -> None:
    """Verify cancellation during the DNSBL apply request is never swallowed.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Client error propagation setting.
    """
    blocked = HeldResponse()
    client, router = make_router_client(
        make_client,
        {
            ("POST", _toggle_path("uuid1", True)): {"result": "Enabled"},
            ("POST", DNSBL_APPLY): blocked,
        },
        firmware=EXTENDED_FIRMWARE,
        throw_errors=throw_errors,
    )
    toggle = asyncio.create_task(client.enable_unbound_blocklist("uuid1"))

    async with asyncio.timeout(2):
        await blocked.entered.wait()
    toggle.cancel()

    with pytest.raises(asyncio.CancelledError):
        await toggle
    assert router.count("POST", DNSBL_APPLY) == 1


@pytest.mark.parametrize(
    ("firmware", "expect_legacy"),
    [
        *[
            pytest.param(firmware, True, id=f"legacy-{firmware}")
            for firmware in LEGACY_FIRMWARE_VERSIONS
        ],
        *[
            pytest.param(firmware, False, id=f"extended-{firmware}")
            for firmware in EXTENDED_FIRMWARE_VERSIONS
        ],
        *[
            pytest.param(param.values[0], True, id=f"unknown-{param.id}")
            for param in UNKNOWN_FIRMWARE
        ],
    ],
)
async def test_get_unbound_blocklist_uses_method_for_firmware(
    make_client: ClientType,
    legacy_dnsbl_payload: dict[str, Any],
    firmware: str | None,
    expect_legacy: bool,
) -> None:
    """Verify reads use the extended blocklist from 25.7.8 and the legacy one otherwise.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        legacy_dnsbl_payload (dict[str, Any]): Legacy DNSBL settings payload.
        firmware (str | None): Firmware version reported by the Router.
        expect_legacy (bool): Whether the legacy blocklist is expected.
    """
    client, router = make_router_client(
        make_client, _both_apis_routes(legacy_dnsbl_payload), firmware=firmware
    )

    result = await client.get_unbound_blocklist()

    if expect_legacy:
        assert list(result) == ["legacy"]
        assert router.count("GET", SETTINGS_GET) == 1
        assert router.count("GET", SEARCH_DNSBL) == 0
    else:
        assert result == {"uuid1": {"uuid": "uuid1", "enabled": "1"}}
        assert router.count("GET", SEARCH_DNSBL) == 1
        assert router.count("GET", SETTINGS_GET) == 0


@pytest.mark.parametrize("method_name", ["enable_unbound_blocklist", "disable_unbound_blocklist"])
@pytest.mark.parametrize(
    ("firmware", "uuid", "backend", "expected_result"),
    [
        *[
            pytest.param(firmware, None, "legacy", True, id=f"legacy-{firmware}")
            for firmware in LEGACY_FIRMWARE_VERSIONS
        ],
        pytest.param("25.7.7", "uuid1", "none", False, id="legacy-refuses-uuid"),
        pytest.param("25.7.8", "uuid1", "extended", True, id="extended-uuid"),
        pytest.param("25.7.8", None, "none", False, id="extended-needs-uuid"),
        pytest.param(None, None, "legacy", True, id="unknown-missing-no-uuid"),
        pytest.param(None, "uuid1", "extended", True, id="unknown-missing-uuid"),
        pytest.param(None, "", "none", False, id="unknown-missing-empty-uuid"),
        pytest.param("not-a-version", None, "legacy", True, id="unknown-not-comparable-no-uuid"),
        pytest.param("not-a-version", "uuid1", "extended", True, id="unknown-not-comparable-uuid"),
        pytest.param("not-a-version", "", "none", False, id="unknown-not-comparable-empty-uuid"),
    ],
)
async def test_unbound_blocklist_write_uses_method_for_firmware(
    make_client: ClientType,
    legacy_dnsbl_payload: dict[str, Any],
    method_name: str,
    firmware: str | None,
    uuid: str | None,
    backend: str,
    expected_result: bool,
) -> None:
    """Verify writes pick the blocklist method from firmware and the supplied UUID.

    Known firmware decides the method. Unknown firmware uses the extended blocklist only when
    an entry UUID is supplied, and the legacy one otherwise. The legacy method saves, applies
    with a GET and restarts. The extended method toggles the entry and applies with a POST.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        legacy_dnsbl_payload (dict[str, Any]): Legacy DNSBL settings payload.
        method_name (str): Public enable or disable method under test.
        firmware (str | None): Firmware version reported by the Router.
        uuid (str | None): Entry UUID passed to the method.
        backend (str): Backend expected to receive requests: ``legacy``, ``extended`` or ``none``.
        expected_result (bool): Expected method result.
    """
    client, router = make_router_client(
        make_client, _both_apis_routes(legacy_dnsbl_payload), firmware=firmware
    )
    toggle_path = _toggle_path("uuid1", method_name == "enable_unbound_blocklist")
    legacy = backend == "legacy"
    extended = backend == "extended"
    expected_requests = {
        ("POST", SETTINGS_SET): int(legacy),
        ("POST", RESTART): int(legacy),
        ("GET", DNSBL_APPLY): int(legacy),
        ("POST", toggle_path): int(extended),
        ("POST", DNSBL_APPLY): int(extended),
    }

    assert await getattr(client, method_name)(uuid) is expected_result

    assert {key: router.count(*key) for key in expected_requests} == expected_requests


@pytest.mark.parametrize(
    ("firmware", "expected_level", "expected_text"),
    [
        pytest.param(None, logging.DEBUG, "firmware version is unavailable", id="missing"),
        pytest.param("not-a-version", logging.ERROR, "cannot be compared", id="not-comparable"),
    ],
)
async def test_unbound_unknown_firmware_is_logged_once_by_the_capability_rule(
    make_client: ClientType,
    legacy_dnsbl_payload: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
    firmware: str | None,
    expected_level: int,
    expected_text: str,
) -> None:
    """Verify unknown firmware is reported once, at the level the capability rule sets.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        legacy_dnsbl_payload (dict[str, Any]): Legacy DNSBL settings payload.
        caplog (pytest.LogCaptureFixture): Log capture fixture.
        firmware (str | None): Unknown firmware value reported by the Router.
        expected_level (int): Level the unknown-firmware message must use.
        expected_text (str): Text identifying the unknown-firmware reason.
    """
    client, _router = make_router_client(
        make_client, _both_apis_routes(legacy_dnsbl_payload), firmware=firmware
    )
    caplog.set_level(logging.DEBUG, logger="aiopnsense")

    await client.get_unbound_blocklist()
    await client.enable_unbound_blocklist()
    await client.disable_unbound_blocklist()

    firmware_records = [
        record for record in caplog.records if "unknown-firmware answer" in record.getMessage()
    ]
    assert [record.levelno for record in firmware_records] == [expected_level]
    assert expected_text in firmware_records[0].getMessage()
