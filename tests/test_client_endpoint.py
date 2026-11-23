"""Tests for client endpoint availability and endpoint-style selection."""

import asyncio
from collections.abc import MutableMapping
import logging
from typing import Any, Self
from unittest.mock import AsyncMock

import aiohttp
import pytest

import aiopnsense
from aiopnsense._endpoint_outcome import _EndpointOutcome
from aiopnsense.exceptions import (
    OPNsenseConnectionError,
    OPNsenseError,
    OPNsenseInvalidAuth,
    OPNsensePrivilegeMissing,
    OPNsenseSSLError,
    OPNsenseUnknownFirmware,
)
from tests.conftest import (
    FakeResponse,
    FirmwareRouter,
    MakeClientFactory,
    install_fake_router,
    make_mock_session_client,
)

DISMISS_SNAKE_PATH = "/api/core/system/dismiss_status"
DISMISS_CAMEL_PATH = "/api/core/system/dismissStatus"
SYSTEM_INFORMATION_SNAKE_PATH = "/api/diagnostics/system/system_information"
NUT_PATH = "/api/nut/diagnostics/upsstatus"
NUT_DATA = {"status": {"ups.status": "OL"}}
OUTCOME_PATH = "/api/test/outcome"
POST_PATH = "/api/test/post"


@pytest.fixture(autouse=True)
def _run_outcome_reads_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run queued outcome reads directly, because the shared fixture stops the queue worker here.

    Firmware status reads are queued outcome reads. Only those are dispatched inline;
    every other queued operation keeps its normal path.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture that replaces the queue entry point.
    """
    queue_request = aiopnsense.OPNsenseClient._queue_request

    async def queue_outcome_inline(
        self: aiopnsense.OPNsenseClient,
        method: str,
        path: str,
        payload: MutableMapping[str, Any] | None = None,
    ) -> Any:
        """Dispatch a queued outcome read to its transport method, as the worker would.

        Args:
            self (aiopnsense.OPNsenseClient): Client whose request is queued.
            method (str): Queued operation name.
            path (str): API endpoint path to request.
            payload (MutableMapping[str, Any] | None): Request body, if any.

        Returns:
            Any: The outcome the worker would have set.
        """
        if method == "get_outcome":
            return await self._do_get_outcome(path)
        if method == "get_text_outcome":
            return await self._do_get_outcome(path, response_format="text")
        return await queue_request(self, method, path, payload)

    monkeypatch.setattr(aiopnsense.OPNsenseClient, "_queue_request", queue_outcome_inline)


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
    client, session = make_mock_session_client(make_client)
    client._throw_errors = False
    calls = 0

    def _get(
        _url: object,
        *,
        auth: object,
        timeout: object,
        ssl: object,
    ) -> FakeResponse:
        """Get.

        Args:
            _url (object): Requested endpoint URL.
            auth (object): Authentication object passed by the client.
            timeout (object): Timeout object passed by the client.
            ssl (object): SSL verification setting passed by the client.

        Returns:
            FakeResponse: Response object returned by the mocked session getter.

        Raises:
            _TestClientSSLError: Always raised to simulate an SSL transport failure.
        """
        del _url, auth, timeout, ssl
        nonlocal calls
        calls += 1
        raise _TestClientSSLError()

    session.get = _get
    try:
        with pytest.raises(OPNsenseSSLError):
            await client.validate()
        assert calls == 1
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
    client, session = make_mock_session_client(make_client)
    calls = 0

    def _get(*args: object, **kwargs: object) -> FakeResponse:
        """Return a permission-denied endpoint response.

        Args:
            args (object): Positional arguments accepted by `_get`.
            kwargs (object): Keyword arguments accepted by `_get`.

        Returns:
            FakeResponse: Synthetic permission-denied response returned by the GET request.
        """
        del args, kwargs
        nonlocal calls
        calls += 1
        return FakeResponse(
            status=403,
            reason="Forbidden",
            ok=False,
            include_request_info=True,
        )

    session.get = _get
    try:
        path = "/api/core/firmware/status"
        assert (await client._check_endpoint_status(path)).is_absent
        assert client._endpoint_availability[path] is False
        assert path in client._endpoint_permission_denied

        with pytest.raises(OPNsensePrivilegeMissing):
            await client.validate(require_device_id=False)

        assert calls == 2
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
    client, session = make_mock_session_client(make_client)
    calls = 0

    def _get(*args: object, **kwargs: object) -> FakeResponse:
        """Return a fake GET response.

        Args:
            args (object): Positional arguments accepted by `_get`.
            kwargs (object): Keyword arguments accepted by `_get`.

        Returns:
            FakeResponse: Synthetic HTTP response used by the test.
        """
        del args, kwargs
        nonlocal calls
        calls += 1
        return FakeResponse(status=200, ok=True)

    session.get = _get
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
        assert calls == 1
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
    client, session = make_mock_session_client(make_client)
    client._throw_errors = False
    calls = 0

    def _get(*args: Any, **kwargs: Any) -> Any:
        """Get.

        Args:
            args (Any): Positional arguments accepted by `_get`.
            kwargs (Any): Keyword arguments accepted by `_get`.

        Returns:
            Any: Decoded response payload returned by the GET request.
        """
        nonlocal calls
        calls += 1
        return FakeResponse(
            status=status,
            reason="ERR",
            ok=False,
            include_request_info=True,
        )

    session.get = _get
    try:
        path = "/api/core/firmware/status"
        with pytest.raises(expected_exception):
            await client.validate()
        assert calls == 1
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
        assert await client._get_endpoint_path("/snake_case", "/camelCase") == expected_path
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


@pytest.mark.asyncio
async def test_get_endpoint_path_lazily_initializes_snake_case_state(
    make_client: MakeClientFactory,
) -> None:
    """Verify endpoint selection lazily initializes snake-case mode.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates lazy snake-case initialization behavior.
    """
    client = make_client()
    try:

        async def fake_set_use_snake_case() -> None:
            """Populate snake-case mode for the lazy helper.

            Returns:
                None: This helper mutates client state for the test.
            """
            client._use_snake_case = False

        set_use_snake_case = AsyncMock(side_effect=fake_set_use_snake_case)
        client._set_use_snake_case = set_use_snake_case

        assert await client._get_endpoint_path("/snake_case", "/camelCase") == "/camelCase"
        set_use_snake_case.assert_awaited_once_with()
    finally:
        await client.async_close()


@pytest.fixture
def dismiss_router(
    firmware_router: tuple[Any, FirmwareRouter],
) -> tuple[Any, FirmwareRouter]:
    """Return a scripted Router that serves both notice-dismiss endpoint styles.

    Args:
        firmware_router (tuple[Any, FirmwareRouter]): Client and the Router that scripts it.

    Returns:
        tuple[Any, FirmwareRouter]: Client and Router that accept either dismiss path.
    """
    client, router = firmware_router
    router.endpoints[DISMISS_SNAKE_PATH] = {"status": "ok"}
    router.endpoints[DISMISS_CAMEL_PATH] = {"status": "ok"}
    return client, router


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
    dismiss_router: tuple[Any, FirmwareRouter], firmware: str, expected_path: str
) -> None:
    """Verify a public method calls the endpoint style that the Router firmware uses.

    Args:
        dismiss_router (tuple[Any, FirmwareRouter]): Client and Router serving both styles.
        firmware (str): Firmware version reported by the Router.
        expected_path (str): Dismiss endpoint the client is expected to call.
    """
    client, router = dismiss_router
    router.firmware = firmware

    assert await client.close_notice("notice") is True

    assert router.post_paths == [expected_path]


@pytest.mark.parametrize(
    "firmware",
    [pytest.param(None, id="missing"), pytest.param("not-a-version", id="uncomparable")],
)
async def test_unknown_firmware_does_not_pin_endpoint_style(
    dismiss_router: tuple[Any, FirmwareRouter],
    firmware: str | None,
) -> None:
    """Verify a first call with unknown firmware does not fix the endpoint style.

    Args:
        dismiss_router (tuple[Any, FirmwareRouter]): Client and Router serving both styles.
        firmware (str | None): Initial firmware version reported by the Router.
    """
    client, router = dismiss_router
    router.firmware = firmware

    assert await client.close_notice("first") is True
    router.firmware = "25.1"
    assert await client.close_notice("second") is True

    assert router.post_paths == [DISMISS_SNAKE_PATH, DISMISS_CAMEL_PATH]


async def test_unreachable_firmware_status_does_not_pin_endpoint_style(
    dismiss_router: tuple[Any, FirmwareRouter],
) -> None:
    """Verify a failed firmware lookup is retried instead of being remembered.

    Args:
        dismiss_router (tuple[Any, FirmwareRouter]): Client and Router serving both styles.
    """
    client, router = dismiss_router
    router.firmware = "25.1"
    router.firmware_reachable = False

    assert await client.close_notice("first") is True
    router.firmware_reachable = True
    assert await client.close_notice("second") is True

    assert router.post_paths == [DISMISS_SNAKE_PATH, DISMISS_CAMEL_PATH]


@pytest.mark.parametrize(
    ("firmware", "expected_level", "expected_text"),
    [
        pytest.param(None, logging.DEBUG, "firmware version is unavailable", id="missing"),
        pytest.param("weird", logging.ERROR, "cannot be compared", id="not-comparable"),
    ],
)
async def test_unknown_firmware_is_logged_once_per_condition(
    dismiss_router: tuple[Any, FirmwareRouter],
    caplog: pytest.LogCaptureFixture,
    firmware: str | None,
    expected_level: int,
    expected_text: str,
) -> None:
    """Verify unknown firmware logs its reason once however many calls hit it.

    Args:
        dismiss_router (tuple[Any, FirmwareRouter]): Client and Router serving both styles.
        caplog (pytest.LogCaptureFixture): Log capture fixture.
        firmware (str | None): Unknown firmware value reported by the Router.
        expected_level (int): Level the unknown-firmware message must use.
        expected_text (str): Text identifying the unknown-firmware reason.
    """
    client, router = dismiss_router
    router.firmware = firmware
    caplog.set_level(logging.DEBUG, logger="aiopnsense")

    for _ in range(3):
        assert await client.close_notice("notice") is True

    assert router.post_paths == [DISMISS_SNAKE_PATH] * 3
    records = [record for record in caplog.records if expected_text in record.getMessage()]
    assert [record.levelno for record in records] == [expected_level]


UPGRADE_PATH = "/api/core/firmware/upgrade"
UPDATE_PATH = "/api/core/firmware/update"
LATE_PATH = "/api/example/feature/get"


@pytest.fixture
def upgrade_router(
    dismiss_router: tuple[Any, FirmwareRouter],
) -> tuple[Any, FirmwareRouter]:
    """Return a scripted Router that accepts firmware update and upgrade actions.

    Args:
        dismiss_router (tuple[Any, FirmwareRouter]): Client and Router serving both styles.

    Returns:
        tuple[Any, FirmwareRouter]: Client and Router that accept firmware actions.
    """
    client, router = dismiss_router
    router.endpoints[UPGRADE_PATH] = {"status": "ok"}
    router.endpoints[UPDATE_PATH] = {"status": "ok"}
    return client, router


async def test_firmware_upgrade_across_threshold_switches_endpoint_style(
    upgrade_router: tuple[Any, FirmwareRouter],
) -> None:
    """Verify the client switches endpoint style after the Router upgrades across 25.7.

    Args:
        upgrade_router (tuple[Any, FirmwareRouter]): Client and Router accepting upgrades.
    """
    client, router = upgrade_router
    router.firmware = "25.1"
    assert await client.close_notice("before") is True

    await client.upgrade_firmware("upgrade")
    router.firmware = "25.7"
    await client.get_host_firmware_version()
    assert await client.close_notice("after") is True

    assert router.post_paths == [DISMISS_CAMEL_PATH, UPGRADE_PATH, DISMISS_SNAKE_PATH]


async def test_firmware_upgrade_refreshes_endpoint_availability(
    upgrade_router: tuple[Any, FirmwareRouter],
) -> None:
    """Verify endpoints that the new firmware adds are used without waiting for the TTL.

    Args:
        upgrade_router (tuple[Any, FirmwareRouter]): Client and Router accepting upgrades.
    """
    client, router = upgrade_router
    router.firmware = "25.1"
    assert await client.get_host_firmware_version() == "25.1"
    assert await client.is_endpoint_available(LATE_PATH) is False

    router.endpoints[LATE_PATH] = {}
    assert await client.is_endpoint_available(LATE_PATH) is False  # cached until a change
    await client.upgrade_firmware("upgrade")
    router.firmware = "25.7"
    await client.get_host_firmware_version()

    assert await client.is_endpoint_available(LATE_PATH) is True


async def test_unchanged_firmware_version_keeps_endpoint_cache(
    upgrade_router: tuple[Any, FirmwareRouter],
) -> None:
    """Verify re-reading an unchanged firmware version does not discard cached endpoints.

    Args:
        upgrade_router (tuple[Any, FirmwareRouter]): Client and Router accepting upgrades.
    """
    client, router = upgrade_router
    router.firmware = "25.1"
    assert await client.is_endpoint_available(LATE_PATH) is False
    await client.get_host_firmware_version()

    router.endpoints[LATE_PATH] = {}
    await client.upgrade_firmware("update")
    assert await client.get_host_firmware_version() == "25.1"

    assert await client.is_endpoint_available(LATE_PATH) is False
    assert router.probe_paths.count(LATE_PATH) == 1


async def test_first_known_firmware_version_keeps_endpoint_cache(
    dismiss_router: tuple[Any, FirmwareRouter],
) -> None:
    """Verify the first known version has nothing to discard.

    Args:
        dismiss_router (tuple[Any, FirmwareRouter]): Client and Router serving both styles.
    """
    client, router = dismiss_router
    router.firmware = "25.1"
    assert await client.is_endpoint_available(LATE_PATH) is False

    assert await client.get_host_firmware_version() == "25.1"

    router.endpoints[LATE_PATH] = {}
    assert await client.is_endpoint_available(LATE_PATH) is False


async def test_failed_firmware_lookup_after_known_version_resets_nothing(
    upgrade_router: tuple[Any, FirmwareRouter],
) -> None:
    """Verify an unreachable Router during an upgrade is not treated as a version change.

    Args:
        upgrade_router (tuple[Any, FirmwareRouter]): Client and Router accepting upgrades.
    """
    client, router = upgrade_router
    router.firmware = "25.1"
    assert await client.close_notice("before") is True
    assert await client.is_endpoint_available(LATE_PATH) is False

    await client.upgrade_firmware("upgrade")
    router.firmware_reachable = False
    router.endpoints[LATE_PATH] = {}
    assert await client.get_host_firmware_version() is None
    assert await client.close_notice("during") is True
    assert await client.is_endpoint_available(LATE_PATH) is False

    assert router.post_paths == [DISMISS_CAMEL_PATH, UPGRADE_PATH, DISMISS_CAMEL_PATH]


async def test_not_comparable_firmware_version_resets_nothing(
    upgrade_router: tuple[Any, FirmwareRouter],
) -> None:
    """Verify a version that cannot be compared is not treated as a version change.

    Args:
        upgrade_router (tuple[Any, FirmwareRouter]): Client and Router accepting upgrades.
    """
    client, router = upgrade_router
    router.firmware = "25.1"
    assert await client.get_host_firmware_version() == "25.1"
    assert await client.is_endpoint_available(LATE_PATH) is False

    await client.upgrade_firmware("update")
    router.firmware = "weird"
    router.endpoints[LATE_PATH] = {}
    assert await client.get_host_firmware_version() == "weird"
    assert await client.is_endpoint_available(LATE_PATH) is False

    await client.upgrade_firmware("update")
    router.firmware = "25.1"
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
    client, session = make_mock_session_client(make_client)
    router = FirmwareRouter(client, session)
    router.firmware = "26.1.1"
    original_probe = router._probe
    probe_started = asyncio.Event()
    release_probe = asyncio.Event()
    delayed_probe_pending = True

    class DelayedNotFoundResponse(FakeResponse):
        """404 response held until the test releases an old firmware probe."""

        async def __aenter__(self) -> Self:
            """Wait for release after the stale probe reaches its response.

            Returns:
                Self: This response after the test releases the probe.
            """
            probe_started.set()
            await release_probe.wait()
            return self

    def _probe(url: str, **kwargs: Any) -> FakeResponse:
        """Delay the first system-information probe and delegate all others.

        Args:
            url (str): Full URL requested by the availability check.
            kwargs (Any): Request options passed to the scripted Router.

        Returns:
            FakeResponse: Delayed 404 or the scripted Router response.
        """
        nonlocal delayed_probe_pending
        if url.endswith(SYSTEM_INFORMATION_SNAKE_PATH) and delayed_probe_pending:
            delayed_probe_pending = False
            return DelayedNotFoundResponse(status=404, ok=False)
        return original_probe(url, **kwargs)

    session.get.side_effect = _probe
    # Learn the old firmware first so the later validate sees the change and resets the cache.
    assert await client.get_host_firmware_version() == "26.1.1"
    old_probe_task = asyncio.create_task(
        client._check_endpoint_status(SYSTEM_INFORMATION_SNAKE_PATH)
    )
    try:
        await asyncio.wait_for(probe_started.wait(), timeout=1)
        router.firmware = "26.1.11"
        await client.validate(require_device_id=False)

        router.endpoints[SYSTEM_INFORMATION_SNAKE_PATH] = {"name": "router"}
        assert (await client._check_endpoint_status(SYSTEM_INFORMATION_SNAKE_PATH)).is_data

        release_probe.set()
        assert (await old_probe_task).is_absent
        assert (await client._check_endpoint_status(SYSTEM_INFORMATION_SNAKE_PATH)).is_data
    finally:
        release_probe.set()
        if not old_probe_task.done():
            await asyncio.gather(old_probe_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_present_endpoint_costs_one_request_per_read(make_client: MakeClientFactory) -> None:
    """A present endpoint costs one request per read, with no separate availability probe.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", NUT_PATH): NUT_DATA})
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
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", NUT_PATH): status})
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
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", NUT_PATH): 403})
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
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", NUT_PATH): failure})
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
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", NUT_PATH): failure})
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
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", NUT_PATH): NUT_DATA})
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
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", NUT_PATH): NUT_DATA})
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
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", NUT_PATH): status})
    original_get = session.get

    def get_then_reset(url: str, **kwargs: Any) -> Any:
        """Answer the request, then reset the endpoint cache before the answer is read.

        Args:
            url (str): Requested URL.
            kwargs (Any): Request options passed to the fake session.

        Returns:
            Any: Fake response for the request.
        """
        response = original_get(url, **kwargs)
        client._reset_endpoint_state()
        return response

    session.get = get_then_reset
    try:
        await client._fetch_endpoint_outcome(NUT_PATH)
        session.get = original_get
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
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", OUTCOME_PATH): "ups.status: OL"})
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
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("POST", POST_PATH): 404})
    try:
        assert (await client._fetch_post_endpoint_outcome(POST_PATH, {"device": "ada0"})).is_absent
        assert (await client._fetch_post_endpoint_outcome(POST_PATH, {"device": "ada0"})).is_absent
        assert router.count("POST", POST_PATH) == 1
        assert router.requests[0][2] == {"device": "ada0"}
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
