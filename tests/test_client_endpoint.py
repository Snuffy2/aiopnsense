"""Tests for client endpoint availability and endpoint-style selection.

Public behavior runs through ``make_router_client``. Tests that call
``_fetch_endpoint_outcome``, ``_check_endpoint_status``, ``_set_use_snake_case`` or
``_reset_endpoint_state`` target the endpoint availability layer itself, which has no
public equivalent for an outcome read, a status-only check or a cache reset.
"""

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock

import aiohttp
import pytest

from aiopnsense._endpoint_outcome import _EndpointOutcome
from aiopnsense._styled_endpoint import _StyledEndpoint
from aiopnsense.exceptions import (
    OPNsenseConnectionError,
    OPNsenseError,
    OPNsenseInvalidAuth,
    OPNsensePrivilegeMissing,
    OPNsenseSSLError,
    OPNsenseUnknownFirmware,
)
from tests.conftest import (
    FIRMWARE_STATUS_PATH,
    FakeRouter,
    HeldResponse,
    MakeClientFactory,
    firmware_status_payload,
    make_router_client,
)

DISMISS_SNAKE_PATH = "/api/core/system/dismiss_status"
DISMISS_CAMEL_PATH = "/api/core/system/dismissStatus"
SYSTEM_INFORMATION_SNAKE_PATH = "/api/diagnostics/system/system_information"
NUT_PATH = "/api/nut/diagnostics/upsstatus"
NUT_DATA = {"status": {"ups.status": "OL"}}
OUTCOME_PATH = "/api/test/outcome"
POST_PATH = "/api/test/post"


class _TestClientSSLError(aiohttp.ClientSSLError):
    """Minimal ``ClientSSLError`` subclass used for validation tests."""

    def __init__(self) -> None:
        """Initialize the synthetic SSL error instance."""
        Exception.__init__(self, "ssl")

    def __str__(self) -> str:
        """Return a stable string for logging and assertion output.

        Returns:
            str: Constant error message for deterministic test behavior.
        """
        return "ssl"


@pytest.mark.asyncio
async def test_validate_maps_endpoint_probe_ssl_to_opnsense_ssl_error(
    make_client: MakeClientFactory,
) -> None:
    """Verify validate maps probe-time SSL failures to ``OPNsenseSSLError``.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts validate SSL mapping through endpoint probing.
    """
    client, router = make_router_client(
        make_client, routes={("GET", FIRMWARE_STATUS_PATH): _TestClientSSLError()}
    )
    try:
        with pytest.raises(OPNsenseSSLError):
            await client.validate()
        assert router.count("GET", FIRMWARE_STATUS_PATH) == 1
        assert client._throw_errors is False
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_validate_reprobes_cached_403_in_throw_mode(
    make_client: MakeClientFactory,
) -> None:
    """Verify validation does not trust a cached permission failure.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts validation freshly probes cached permission failures.
    """
    client, router = make_router_client(make_client, routes={("GET", FIRMWARE_STATUS_PATH): 403})
    try:
        path = FIRMWARE_STATUS_PATH
        assert (await client._check_endpoint_status(path)).is_absent
        assert client._endpoint_availability[path] is False
        assert path in client._endpoint_permission_denied

        with pytest.raises(OPNsensePrivilegeMissing):
            await client.validate(require_device_id=False)

        assert router.count("GET", path) == 2
        assert client._throw_errors is False
        assert path not in client._endpoint_availability
        assert path not in client._endpoint_checked_at
        assert path not in client._endpoint_permission_denied
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_is_endpoint_available_is_deprecated_get_alias(
    make_client: MakeClientFactory,
) -> None:
    """Verify the legacy endpoint availability alias is marked deprecated.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts PEP 702 metadata, runtime warnings, and alias behavior.
    """
    client, router = make_router_client(make_client, routes={("GET", "/api/test/endpoint"): {}})
    try:
        legacy_alias = client.is_endpoint_available  # type: ignore[deprecated]
        assert "Endpoint availability probing is internal" in legacy_alias.__deprecated__  # type: ignore[attr-defined]
        with pytest.warns(DeprecationWarning, match="Endpoint availability probing is internal"):
            assert await legacy_alias("") is False
        with pytest.warns(DeprecationWarning, match="Endpoint availability probing is internal"):
            assert await legacy_alias(
                "/api/test/endpoint",
                force_refresh=True,
            )
        assert router.count() == 1
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    ("status", "expected_exception"),
    [
        (401, OPNsenseInvalidAuth),
        (403, OPNsensePrivilegeMissing),
        (500, OPNsenseConnectionError),
    ],
)
@pytest.mark.asyncio
async def test_validate_maps_endpoint_probe_http_errors_to_public_exceptions(
    status: int,
    expected_exception: type[Exception],
    make_client: MakeClientFactory,
) -> None:
    """Verify ``validate`` maps preliminary endpoint-probe HTTP failures.

    Args:
        status (int): HTTP response status returned by the endpoint probe.
        expected_exception (type[Exception]): Public exception expected from ``validate``.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts validation-time endpoint probe error mapping.
    """
    client, router = make_router_client(make_client, routes={("GET", FIRMWARE_STATUS_PATH): status})
    try:
        path = FIRMWARE_STATUS_PATH
        with pytest.raises(expected_exception):
            await client.validate()
        assert router.count("GET", path) == 1
        assert client._throw_errors is False
        assert path not in client._endpoint_checked_at
        assert path not in client._endpoint_availability
    finally:
        await client.async_close()


@pytest.mark.parametrize("initial", [False, True])
@pytest.mark.asyncio
async def test_set_use_snake_case_deprecated_wrapper(
    initial: bool,
    make_client: MakeClientFactory,
) -> None:
    """Verify the deprecated wrapper forwards the legacy ``initial`` keyword.

    Args:
        initial (bool): Legacy compatibility flag forwarded by the wrapper.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts compatibility warning and forwarding behavior.
    """
    client = make_client()
    try:
        client._set_use_snake_case = AsyncMock()
        wrapper = client.set_use_snake_case  # type: ignore[deprecated]

        assert "Endpoint style selection is internal" in wrapper.__deprecated__  # type: ignore[attr-defined]
        with pytest.warns(DeprecationWarning, match="Endpoint style selection is internal"):
            await wrapper(initial=initial)

        client._set_use_snake_case.assert_awaited_once_with(initial=initial)
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    ("firmware_version", "expected_use_snake_case", "expected_path"),
    [
        ("25.1", False, "/camelCase"),
        ("25.7", True, "/snake_case"),
        ("25.7_1", True, "/snake_case"),
        ("26.1.1", True, "/snake_case"),
        (None, None, "/snake_case"),
        ("invalid-version", None, "/snake_case"),
    ],
)
@pytest.mark.asyncio
async def test_set_use_snake_case_selects_expected_endpoint_style(
    firmware_version: str | None,
    expected_use_snake_case: bool | None,
    expected_path: str,
    make_client: MakeClientFactory,
) -> None:
    """Verify firmware-driven snake-case selection and endpoint resolution.

    Args:
        firmware_version (str | None): Firmware version returned by the mocked client.
        expected_use_snake_case (bool | None): Expected endpoint-style flag after initialization;
            ``None`` when unknown firmware leaves the style unresolved.
        expected_path (str): Expected endpoint path returned by the selector helper.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates firmware-based endpoint-style selection.
    """
    client = make_client()
    try:
        client.get_host_firmware_version = AsyncMock(return_value=firmware_version)

        await client._set_use_snake_case()

        assert client._use_snake_case is expected_use_snake_case
        client.get_host_firmware_version.assert_awaited_once_with()
        assert (
            await client._resolve_endpoint(_StyledEndpoint("/snake_case", "/camelCase"))
            == expected_path
        )
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    "firmware_version",
    [
        pytest.param("invalid-version", id="invalid-version"),
        pytest.param(None, id="missing-version"),
    ],
)
@pytest.mark.asyncio
async def test_set_use_snake_case_initial_raises_unknown_firmware(
    make_client: MakeClientFactory,
    firmware_version: str | None,
) -> None:
    """Verify legacy initial setup raises when firmware cannot be identified.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        firmware_version (str | None): Invalid or missing firmware version returned by the client.

    Returns:
        None: This test asserts the preserved compatibility path.
    """
    client = make_client()
    try:
        client.get_host_firmware_version = AsyncMock(return_value=firmware_version)

        with pytest.raises(OPNsenseUnknownFirmware):
            await client._set_use_snake_case(initial=True)

        client.get_host_firmware_version.assert_awaited_once_with()
        assert client._use_snake_case is None
    finally:
        await client.async_close()


@pytest.fixture
async def dismiss_router(
    make_client: MakeClientFactory,
) -> tuple[Any, FakeRouter]:
    """Return a client and a Router that serves both notice-dismiss endpoint styles.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        tuple[Any, FakeRouter]: Client and Router that accept either
            dismiss path. A test sets the firmware version through ``routes``.
    """
    return make_router_client(
        make_client,
        routes={
            ("POST", DISMISS_SNAKE_PATH): {"status": "ok"},
            ("POST", DISMISS_CAMEL_PATH): {"status": "ok"},
        },
    )


@pytest.mark.parametrize(
    ("firmware", "expected_path"),
    [
        pytest.param("25.1", DISMISS_CAMEL_PATH, id="below-threshold"),
        pytest.param("25.6.4", DISMISS_CAMEL_PATH, id="just-below-threshold"),
        pytest.param("25.7", DISMISS_SNAKE_PATH, id="at-threshold"),
        pytest.param("26.1.6", DISMISS_SNAKE_PATH, id="above-threshold"),
    ],
)
async def test_public_call_uses_endpoint_style_for_known_firmware(
    dismiss_router: tuple[Any, FakeRouter], firmware: str, expected_path: str
) -> None:
    """Verify a public method calls the endpoint style that the Router firmware uses.

    Args:
        dismiss_router (tuple[Any, FakeRouter]): Client and Router serving both styles.
        firmware (str): Firmware version reported by the Router.
        expected_path (str): Dismiss endpoint the client is expected to call.
    """
    client, router = dismiss_router
    router.routes[("GET", FIRMWARE_STATUS_PATH)] = firmware_status_payload(firmware)

    assert await client.close_notice("notice") is True

    assert router.paths("POST") == [expected_path]


@pytest.mark.parametrize(
    "firmware",
    [pytest.param(None, id="missing"), pytest.param("not-a-version", id="uncomparable")],
)
async def test_unknown_firmware_does_not_pin_endpoint_style(
    dismiss_router: tuple[Any, FakeRouter],
    firmware: str | None,
) -> None:
    """Verify a first call with unknown firmware does not fix the endpoint style.

    Args:
        dismiss_router (tuple[Any, FakeRouter]): Client and Router serving both styles.
        firmware (str | None): Initial firmware version reported by the Router.
    """
    client, router = dismiss_router
    router.routes[("GET", FIRMWARE_STATUS_PATH)] = firmware_status_payload(firmware)

    assert await client.close_notice("first") is True
    router.routes[("GET", FIRMWARE_STATUS_PATH)] = firmware_status_payload("25.1")
    assert await client.close_notice("second") is True

    assert router.paths("POST") == [DISMISS_SNAKE_PATH, DISMISS_CAMEL_PATH]


async def test_unreachable_firmware_status_does_not_pin_endpoint_style(
    dismiss_router: tuple[Any, FakeRouter],
) -> None:
    """Verify a failed firmware lookup is retried instead of being remembered.

    Args:
        dismiss_router (tuple[Any, FakeRouter]): Client and Router serving both styles.
    """
    client, router = dismiss_router
    router.routes[("GET", FIRMWARE_STATUS_PATH)] = aiohttp.ClientConnectionError(
        "router unreachable"
    )

    assert await client.close_notice("first") is True
    router.routes[("GET", FIRMWARE_STATUS_PATH)] = firmware_status_payload("25.1")
    assert await client.close_notice("second") is True

    assert router.paths("POST") == [DISMISS_SNAKE_PATH, DISMISS_CAMEL_PATH]


@pytest.mark.parametrize(
    ("firmware", "expected_level", "expected_text"),
    [
        pytest.param(None, logging.DEBUG, "firmware version is unavailable", id="missing"),
        pytest.param("weird", logging.ERROR, "cannot be compared", id="not-comparable"),
    ],
)
async def test_unknown_firmware_is_logged_once_per_condition(
    dismiss_router: tuple[Any, FakeRouter],
    caplog: pytest.LogCaptureFixture,
    firmware: str | None,
    expected_level: int,
    expected_text: str,
) -> None:
    """Verify unknown firmware logs its reason once however many calls hit it.

    Args:
        dismiss_router (tuple[Any, FakeRouter]): Client and Router serving both styles.
        caplog (pytest.LogCaptureFixture): Log capture fixture.
        firmware (str | None): Unknown firmware value reported by the Router.
        expected_level (int): Level the unknown-firmware message must use.
        expected_text (str): Text identifying the unknown-firmware reason.
    """
    client, router = dismiss_router
    router.routes[("GET", FIRMWARE_STATUS_PATH)] = firmware_status_payload(firmware)
    caplog.set_level(logging.DEBUG, logger="aiopnsense")

    for _ in range(3):
        assert await client.close_notice("notice") is True

    assert router.paths("POST") == [DISMISS_SNAKE_PATH] * 3
    records = [record for record in caplog.records if expected_text in record.getMessage()]
    assert [record.levelno for record in records] == [expected_level]


UPGRADE_PATH = "/api/core/firmware/upgrade"
UPDATE_PATH = "/api/core/firmware/update"
LATE_PATH = "/api/example/feature/get"


@pytest.fixture
def upgrade_router(
    dismiss_router: tuple[Any, FakeRouter],
) -> tuple[Any, FakeRouter]:
    """Return a scripted Router that accepts firmware update and upgrade actions.

    Args:
        dismiss_router (tuple[Any, FakeRouter]): Client and Router serving both styles.

    Returns:
        tuple[Any, FakeRouter]: Client and Router that accept firmware actions.
    """
    client, router = dismiss_router
    router.routes[("POST", UPGRADE_PATH)] = {"status": "ok"}
    router.routes[("POST", UPDATE_PATH)] = {"status": "ok"}
    return client, router


async def test_firmware_upgrade_across_threshold_switches_endpoint_style(
    upgrade_router: tuple[Any, FakeRouter],
) -> None:
    """Verify the client switches endpoint style after the Router upgrades across 25.7.

    Args:
        upgrade_router (tuple[Any, FakeRouter]): Client and Router accepting upgrades.
    """
    client, router = upgrade_router
    router.routes[("GET", FIRMWARE_STATUS_PATH)] = firmware_status_payload("25.1")
    assert await client.close_notice("before") is True

    await client.upgrade_firmware("upgrade")
    router.routes[("GET", FIRMWARE_STATUS_PATH)] = firmware_status_payload("25.7")
    await client.get_host_firmware_version()
    assert await client.close_notice("after") is True

    assert router.paths("POST") == [DISMISS_CAMEL_PATH, UPGRADE_PATH, DISMISS_SNAKE_PATH]


async def test_firmware_upgrade_refreshes_endpoint_availability(
    upgrade_router: tuple[Any, FakeRouter],
) -> None:
    """Verify endpoints that the new firmware adds are used without waiting for the TTL.

    Args:
        upgrade_router (tuple[Any, FakeRouter]): Client and Router accepting upgrades.
    """
    client, router = upgrade_router
    router.routes[("GET", FIRMWARE_STATUS_PATH)] = firmware_status_payload("25.1")
    assert await client.get_host_firmware_version() == "25.1"
    assert await client.is_endpoint_available(LATE_PATH) is False

    router.routes[("GET", LATE_PATH)] = {}
    assert await client.is_endpoint_available(LATE_PATH) is False  # cached until a change
    await client.upgrade_firmware("upgrade")
    router.routes[("GET", FIRMWARE_STATUS_PATH)] = firmware_status_payload("25.7")
    await client.get_host_firmware_version()

    assert await client.is_endpoint_available(LATE_PATH) is True


async def test_unchanged_firmware_version_keeps_endpoint_cache(
    upgrade_router: tuple[Any, FakeRouter],
) -> None:
    """Verify re-reading an unchanged firmware version does not discard cached endpoints.

    Args:
        upgrade_router (tuple[Any, FakeRouter]): Client and Router accepting upgrades.
    """
    client, router = upgrade_router
    router.routes[("GET", FIRMWARE_STATUS_PATH)] = firmware_status_payload("25.1")
    assert await client.is_endpoint_available(LATE_PATH) is False
    await client.get_host_firmware_version()

    router.routes[("GET", LATE_PATH)] = {}
    await client.upgrade_firmware("update")
    assert await client.get_host_firmware_version() == "25.1"

    assert await client.is_endpoint_available(LATE_PATH) is False
    assert router.count("GET", LATE_PATH) == 1


async def test_first_known_firmware_version_keeps_endpoint_cache(
    dismiss_router: tuple[Any, FakeRouter],
) -> None:
    """Verify the first known version has nothing to discard.

    Args:
        dismiss_router (tuple[Any, FakeRouter]): Client and Router serving both styles.
    """
    client, router = dismiss_router
    router.routes[("GET", FIRMWARE_STATUS_PATH)] = firmware_status_payload("25.1")
    assert await client.is_endpoint_available(LATE_PATH) is False

    assert await client.get_host_firmware_version() == "25.1"

    router.routes[("GET", LATE_PATH)] = {}
    assert await client.is_endpoint_available(LATE_PATH) is False


async def test_failed_firmware_lookup_after_known_version_resets_nothing(
    upgrade_router: tuple[Any, FakeRouter],
) -> None:
    """Verify an unreachable Router during an upgrade is not treated as a version change.

    Args:
        upgrade_router (tuple[Any, FakeRouter]): Client and Router accepting upgrades.
    """
    client, router = upgrade_router
    router.routes[("GET", FIRMWARE_STATUS_PATH)] = firmware_status_payload("25.1")
    assert await client.close_notice("before") is True
    assert await client.is_endpoint_available(LATE_PATH) is False

    await client.upgrade_firmware("upgrade")
    router.routes[("GET", FIRMWARE_STATUS_PATH)] = aiohttp.ClientConnectionError(
        "router unreachable"
    )
    router.routes[("GET", LATE_PATH)] = {}
    assert await client.get_host_firmware_version() is None
    assert await client.close_notice("during") is True
    assert await client.is_endpoint_available(LATE_PATH) is False

    assert router.paths("POST") == [DISMISS_CAMEL_PATH, UPGRADE_PATH, DISMISS_CAMEL_PATH]


async def test_not_comparable_firmware_version_resets_nothing(
    upgrade_router: tuple[Any, FakeRouter],
) -> None:
    """Verify a version that cannot be compared is not treated as a version change.

    Args:
        upgrade_router (tuple[Any, FakeRouter]): Client and Router accepting upgrades.
    """
    client, router = upgrade_router
    router.routes[("GET", FIRMWARE_STATUS_PATH)] = firmware_status_payload("25.1")
    assert await client.get_host_firmware_version() == "25.1"
    assert await client.is_endpoint_available(LATE_PATH) is False

    await client.upgrade_firmware("update")
    router.routes[("GET", FIRMWARE_STATUS_PATH)] = firmware_status_payload("weird")
    router.routes[("GET", LATE_PATH)] = {}
    assert await client.get_host_firmware_version() == "weird"
    assert await client.is_endpoint_available(LATE_PATH) is False

    await client.upgrade_firmware("update")
    router.routes[("GET", FIRMWARE_STATUS_PATH)] = firmware_status_payload("25.1")
    assert await client.get_host_firmware_version() == "25.1"
    assert await client.is_endpoint_available(LATE_PATH) is False


@pytest.mark.asyncio
async def test_old_endpoint_outcome_cannot_restore_cache_after_firmware_change(
    make_client: MakeClientFactory,
) -> None:
    """Keep a delayed old-firmware probe from overwriting refreshed endpoint state.

    Args:
        make_client (MakeClientFactory): Fixture factory returning clients.
    """
    held = HeldResponse(status=404, ok=False)
    client, router = make_router_client(
        make_client,
        routes={
            ("GET", FIRMWARE_STATUS_PATH): firmware_status_payload("26.1.1"),
            ("GET", SYSTEM_INFORMATION_SNAKE_PATH): held,
        },
    )
    # Learn the old firmware first so the later validate sees the change and resets the cache.
    assert await client.get_host_firmware_version() == "26.1.1"
    old_probe_task = asyncio.create_task(
        client._check_endpoint_status(SYSTEM_INFORMATION_SNAKE_PATH)
    )
    try:
        await asyncio.wait_for(held.entered.wait(), timeout=1)
        router.routes[("GET", FIRMWARE_STATUS_PATH)] = firmware_status_payload("26.1.11")
        await client.validate(require_device_id=False)

        router.routes[("GET", SYSTEM_INFORMATION_SNAKE_PATH)] = {"name": "router"}
        assert (await client._check_endpoint_status(SYSTEM_INFORMATION_SNAKE_PATH)).is_data

        held.release.set()
        assert (await old_probe_task).is_absent
        assert (await client._check_endpoint_status(SYSTEM_INFORMATION_SNAKE_PATH)).is_data
    finally:
        held.release.set()
        if not old_probe_task.done():
            await asyncio.gather(old_probe_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_present_endpoint_costs_one_request_per_read(make_client: MakeClientFactory) -> None:
    """A present endpoint costs one request per read, with no separate availability probe.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = make_router_client(make_client, routes={("GET", NUT_PATH): NUT_DATA})
    try:
        assert await client.get_nut_ups_status() == NUT_DATA
        assert router.count("GET", NUT_PATH) == 1
        assert await client.get_nut_ups_status() == NUT_DATA
        assert router.count("GET", NUT_PATH) == 2
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    ("status", "throw_errors"),
    [
        pytest.param(404, False, id="absent-default-mode"),
        pytest.param(404, True, id="absent-throw-mode"),
    ],
)
@pytest.mark.asyncio
async def test_absent_endpoint_is_cached_until_ttl_expires(
    make_client: MakeClientFactory, status: int, throw_errors: bool
) -> None:
    """A missing endpoint is not requested again until the cache TTL expires, in either mode.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        status (int): HTTP status the Router returns while the endpoint is absent.
        throw_errors (bool): Whether the client raises public errors.
    """
    client, router = make_router_client(make_client, routes={("GET", NUT_PATH): status})
    client.toggle_throwing_errors(throw_errors)
    try:
        assert await client.get_nut_ups_status() == {}
        assert await client.get_nut_ups_status() == {}
        assert router.count("GET", NUT_PATH) == 1

        client._endpoint_cache_ttl_seconds = 0
        router.routes[("GET", NUT_PATH)] = NUT_DATA
        assert await client.get_nut_ups_status() == NUT_DATA
        assert router.count("GET", NUT_PATH) == 2
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_cached_permission_error_is_trusted_by_default_and_rechecked_in_throw_mode(
    make_client: MakeClientFactory,
) -> None:
    """A cached 403 is trusted outside throw mode, and requested again and raised in throw mode.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = make_router_client(make_client, routes={("GET", NUT_PATH): 403})
    try:
        assert await client.get_nut_ups_status() == {}
        assert await client.get_nut_ups_status() == {}
        assert router.count("GET", NUT_PATH) == 1

        client.toggle_throwing_errors(True)
        with pytest.raises(OPNsensePrivilegeMissing):
            await client.get_nut_ups_status()
        assert router.count("GET", NUT_PATH) == 2
    finally:
        await client.async_close()


@pytest.mark.parametrize("failure", [500, TimeoutError("timed out")], ids=["http-500", "timeout"])
@pytest.mark.asyncio
async def test_transient_failure_is_not_cached_and_logs_once_per_outage(
    make_client: MakeClientFactory, caplog: pytest.LogCaptureFixture, failure: Any
) -> None:
    """A transient failure is requested again on the next read and logs once per outage.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        caplog (pytest.LogCaptureFixture): Fixture capturing log records.
        failure (Any): Router response that models the transient failure.
    """
    client, router = make_router_client(make_client, routes={("GET", NUT_PATH): failure})
    try:
        with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
            assert await client.get_nut_ups_status() == {}
            assert await client.get_nut_ups_status() == {}
            failures = [r for r in caplog.records if r.levelno == logging.ERROR]
            assert len(failures) == 1
            assert router.count("GET", NUT_PATH) == 2

            router.routes[("GET", NUT_PATH)] = NUT_DATA
            assert await client.get_nut_ups_status() == NUT_DATA
        recovered = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(recovered) == 1
        assert "recovered" in recovered[0].getMessage()
    finally:
        await client.async_close()


@pytest.mark.parametrize("failure", [500, TimeoutError("timed out")], ids=["http-500", "timeout"])
@pytest.mark.asyncio
async def test_transient_failure_raises_in_throw_mode_and_is_not_cached(
    make_client: MakeClientFactory, failure: Any
) -> None:
    """In throw mode a transient failure raises on every read, because it is never cached.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        failure (Any): Router response that models the transient failure.
    """
    client, router = make_router_client(make_client, routes={("GET", NUT_PATH): failure})
    client.toggle_throwing_errors(True)
    try:
        with pytest.raises(OPNsenseError):
            await client._fetch_endpoint_outcome(NUT_PATH)
        with pytest.raises(OPNsenseError):
            await client._fetch_endpoint_outcome(NUT_PATH)
        assert router.count("GET", NUT_PATH) == 2
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_transient_failure_clears_cached_present_status(
    make_client: MakeClientFactory,
) -> None:
    """A transient failure discards a cached present answer, so the next status check asks again.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = make_router_client(make_client, routes={("GET", NUT_PATH): NUT_DATA})
    try:
        assert (await client._check_endpoint_status(NUT_PATH)).is_data
        assert (await client._check_endpoint_status(NUT_PATH)).is_data
        assert router.count("GET", NUT_PATH) == 1

        router.routes[("GET", NUT_PATH)] = 500
        assert (await client._check_endpoint_status(NUT_PATH, force_refresh=True)).is_transient
        router.routes[("GET", NUT_PATH)] = NUT_DATA
        assert (await client._check_endpoint_status(NUT_PATH)).is_data
        assert router.count("GET", NUT_PATH) == 3
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_forbidden_in_throw_mode_clears_cached_present_status(
    make_client: MakeClientFactory,
) -> None:
    """A 403 raises in throw mode and discards a cached present answer.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = make_router_client(make_client, routes={("GET", NUT_PATH): NUT_DATA})
    client.toggle_throwing_errors(True)
    try:
        assert (await client._check_endpoint_status(NUT_PATH)).is_data
        router.routes[("GET", NUT_PATH)] = 403
        with pytest.raises(OPNsensePrivilegeMissing):
            await client._check_endpoint_status(NUT_PATH, force_refresh=True)
        router.routes[("GET", NUT_PATH)] = NUT_DATA
        assert (await client._check_endpoint_status(NUT_PATH)).is_data
        assert router.count("GET", NUT_PATH) == 3
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    "status", [200, 404, 403, 500], ids=["present", "absent", "forbidden", "transient"]
)
@pytest.mark.asyncio
async def test_reset_during_request_discards_its_cache_answer(
    make_client: MakeClientFactory, status: int
) -> None:
    """An answer that arrives after an endpoint cache reset is not cached.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        status (int): HTTP status the Router answers with.
    """
    client, router = make_router_client(make_client)

    def reset_then_answer(**_kwargs: Any) -> int:
        """Reset the endpoint cache before the client reads the answer.

        Args:
            _kwargs (Any): Request options passed by the transport.

        Returns:
            int: HTTP status the Router answers with.
        """
        client._reset_endpoint_state()
        return status

    router.routes[("GET", NUT_PATH)] = reset_then_answer
    try:
        await client._fetch_endpoint_outcome(NUT_PATH)
        router.routes[("GET", NUT_PATH)] = status
        await client._fetch_endpoint_outcome(NUT_PATH)
        assert router.count("GET", NUT_PATH) == 2
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_text_read_returns_body_text_in_one_request(make_client: MakeClientFactory) -> None:
    """A text read returns the body text of a present endpoint from one request.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = make_router_client(
        make_client, routes={("GET", OUTCOME_PATH): "ups.status: OL"}
    )
    try:
        outcome = await client._fetch_endpoint_outcome(OUTCOME_PATH, "text")
        assert outcome.is_data
        assert outcome.text() == "ups.status: OL"
        assert router.count("GET", OUTCOME_PATH) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_post_outcome_sends_its_payload_and_caches_absence(
    make_client: MakeClientFactory,
) -> None:
    """A POST read sends its real payload once, and a cached absence skips the next POST.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = make_router_client(make_client, routes={("POST", POST_PATH): 404})
    try:
        assert (await client._fetch_post_endpoint_outcome(POST_PATH, {"device": "ada0"})).is_absent
        assert (await client._fetch_post_endpoint_outcome(POST_PATH, {"device": "ada0"})).is_absent
        assert router.bodies("POST", POST_PATH) == [{"device": "ada0"}]
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    ("payload", "mapping", "sequence"),
    [
        ({"a": 1}, {"a": 1}, []),
        ([1, 2], {}, [1, 2]),
        (None, {}, []),
    ],
)
def test_endpoint_outcome_coerces_payload_shape(
    payload: Any, mapping: dict[str, Any], sequence: list
) -> None:
    """Outcome accessors return the payload shape asked for or its empty value.

    Args:
        payload (Any): Decoded payload carried by the outcome.
        mapping (dict[str, Any]): Expected ``mapping()`` result.
        sequence (list): Expected ``sequence()`` result.
    """
    outcome = _EndpointOutcome.data(payload)
    assert outcome.is_data
    assert outcome.mapping() == mapping
    assert outcome.sequence() == sequence


STYLED_SNAKE_PATH = "/api/test/styled_read"
STYLED_CAMEL_PATH = "/api/test/styledRead"
STYLED_ENDPOINT = _StyledEndpoint(snake_case=STYLED_SNAKE_PATH, camel_case=STYLED_CAMEL_PATH)


@pytest.mark.parametrize(
    ("firmware", "expected_path"),
    [
        pytest.param("25.6.4", STYLED_CAMEL_PATH, id="camel-case-firmware"),
        pytest.param("25.7", STYLED_SNAKE_PATH, id="snake-case-firmware"),
        pytest.param(None, STYLED_SNAKE_PATH, id="unknown-firmware"),
    ],
)
@pytest.mark.asyncio
async def test_resolve_endpoint_selects_spelling_for_firmware(
    make_client: MakeClientFactory, firmware: str | None, expected_path: str
) -> None:
    """Verify a styled endpoint resolves to the spelling the Router firmware uses.

    Args:
        make_client (MakeClientFactory): Fixture factory returning clients.
        firmware (str | None): Firmware version the Router reports, if any.
        expected_path (str): Expected resolved path.
    """
    client, _router = make_router_client(make_client, firmware=firmware)

    assert await client._resolve_endpoint(STYLED_ENDPOINT) == expected_path


@pytest.mark.asyncio
async def test_resolve_endpoint_returns_plain_string_without_firmware_read(
    make_client: MakeClientFactory,
) -> None:
    """Verify a plain string is returned unchanged and never reads the firmware.

    Args:
        make_client (MakeClientFactory): Fixture factory returning clients.
    """
    client, router = make_router_client(make_client, firmware="25.6.4")

    assert await client._resolve_endpoint("/api/plain/path?x=1") == "/api/plain/path?x=1"
    assert router.count() == 0


@pytest.mark.parametrize(
    ("firmware", "expected_path"),
    [
        pytest.param("25.6.4", STYLED_CAMEL_PATH, id="camel-case-firmware"),
        pytest.param("25.7", STYLED_SNAKE_PATH, id="snake-case-firmware"),
    ],
)
@pytest.mark.asyncio
async def test_fetch_outcomes_resolve_styled_endpoint_for_the_firmware(
    make_client: MakeClientFactory, firmware: str, expected_path: str
) -> None:
    """Verify the GET and POST outcome fetches send the resolved spelling.

    Args:
        make_client (MakeClientFactory): Fixture factory returning clients.
        firmware (str): Firmware version the Router reports.
        expected_path (str): Spelling the Router must receive.
    """
    client, router = make_router_client(
        make_client,
        firmware=firmware,
        routes={
            ("GET", STYLED_SNAKE_PATH): {"ok": 1},
            ("GET", STYLED_CAMEL_PATH): {"ok": 1},
            ("POST", STYLED_SNAKE_PATH): {"ok": 1},
            ("POST", STYLED_CAMEL_PATH): {"ok": 1},
        },
    )

    assert (await client._fetch_endpoint_outcome(STYLED_ENDPOINT)).is_data
    assert (await client._fetch_post_endpoint_outcome(STYLED_ENDPOINT, {"a": 1})).is_data

    assert router.paths("GET") == [FIRMWARE_STATUS_PATH, expected_path]
    assert router.paths("POST") == [expected_path]


@pytest.mark.asyncio
async def test_both_spellings_keep_separate_availability_entries(
    make_client: MakeClientFactory,
) -> None:
    """Verify a cached absence for one spelling does not hide the other.

    Args:
        make_client (MakeClientFactory): Fixture factory returning clients.
    """
    client, router = make_router_client(
        make_client, routes={("GET", STYLED_SNAKE_PATH): 404, ("GET", STYLED_CAMEL_PATH): {"ok": 1}}
    )

    assert (await client._fetch_endpoint_outcome(STYLED_SNAKE_PATH)).is_absent
    assert (await client._fetch_endpoint_outcome(STYLED_CAMEL_PATH)).is_data
    assert (await client._fetch_endpoint_outcome(STYLED_SNAKE_PATH)).is_absent
    assert router.count("GET", STYLED_SNAKE_PATH) == 1
    assert router.count("GET", STYLED_CAMEL_PATH) == 1


@pytest.mark.asyncio
async def test_firmware_change_during_styled_fetch_discards_its_answer(
    make_client: MakeClientFactory,
) -> None:
    """Verify a styled fetch overtaken by a firmware change does not cache its answer.

    Args:
        make_client (MakeClientFactory): Fixture factory returning clients.
    """
    client, router = make_router_client(make_client, firmware="26.1.1")

    def change_firmware_then_answer(**_kwargs: Any) -> int:
        """Reset the endpoint state as a firmware change does, then answer absent.

        Args:
            _kwargs (Any): Request options passed by the transport.

        Returns:
            int: HTTP status the Router answers with.
        """
        client._reset_endpoint_state()
        return 404

    router.routes[("GET", STYLED_SNAKE_PATH)] = change_firmware_then_answer

    assert (await client._fetch_endpoint_outcome(STYLED_ENDPOINT)).is_absent
    router.routes[("GET", STYLED_SNAKE_PATH)] = {"ok": 1}
    assert (await client._fetch_endpoint_outcome(STYLED_ENDPOINT)).is_data
    assert router.count("GET", STYLED_SNAKE_PATH) == 2
