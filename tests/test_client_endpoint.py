"""Tests for client endpoint availability and endpoint-style selection."""

import asyncio
from datetime import UTC, datetime, timedelta
import logging
from typing import Any, Self
from unittest.mock import AsyncMock

import aiohttp
import pytest

from aiopnsense._endpoint_outcome import _EndpointOutcome
from aiopnsense._request_failure import _PERMISSION_GUIDANCE
import aiopnsense.client_endpoint
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
    MalformedJSONResponse,
    install_fake_router,
    make_mock_session_client,
)

DISMISS_SNAKE_PATH = "/api/core/system/dismiss_status"
DISMISS_CAMEL_PATH = "/api/core/system/dismissStatus"
SYSTEM_INFORMATION_SNAKE_PATH = "/api/diagnostics/system/system_information"


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
async def test_validate_maps_firmware_request_ssl_to_opnsense_ssl_error(
    make_client: MakeClientFactory,
) -> None:
    """Verify validate maps an SSL failure on the firmware status request to ``OPNsenseSSLError``.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts validate SSL mapping through the firmware status request.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(
        client, session, routes={("GET", "/api/core/firmware/status"): _TestClientSSLError()}
    )
    try:
        with pytest.raises(OPNsenseSSLError):
            await client.validate()
        assert router.count("GET") == 1
        assert await client.get_host_firmware_version() is None
        assert router.count("GET") == 2
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_validate_requests_again_for_cached_403_in_throw_mode(
    make_client: MakeClientFactory,
) -> None:
    """Verify validation does not trust a cached permission failure.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts validation requests cached permission failures again.
    """
    client, session = make_mock_session_client(make_client)
    path = "/api/core/firmware/status"
    router = install_fake_router(client, session, routes={("GET", path): 403})
    try:
        assert await client.get_host_firmware_version() is None
        assert router.count("GET", path) == 1

        with pytest.raises(OPNsensePrivilegeMissing):
            await client.validate(require_device_id=False)

        assert router.count("GET", path) == 2
        assert await client.get_host_firmware_version() is None
        assert router.count("GET", path) == 3
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_is_endpoint_available_is_deprecated_get_alias(
    make_client: MakeClientFactory,
) -> None:
    """Verify the legacy endpoint availability alias is marked deprecated and still answers.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts PEP 702 metadata, runtime warnings, and alias behavior.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session)
    try:
        legacy_alias = client.is_endpoint_available  # type: ignore[deprecated]
        assert isinstance(getattr(legacy_alias, "__deprecated__", None), str)
        with pytest.warns(DeprecationWarning):
            assert await legacy_alias(
                "/api/test/endpoint",
                force_refresh=True,
            )
        assert router.count("GET", "/api/test/endpoint") == 1
    finally:
        await client.async_close()


STATUS_PATH = "/api/test/status"


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        pytest.param(200, True, id="present-200"),
        pytest.param(404, False, id="absent-404"),
        pytest.param(403, False, id="forbidden-403"),
        pytest.param(500, False, id="transient-500"),
        pytest.param(TimeoutError("timed out"), False, id="transient-timeout"),
    ],
)
@pytest.mark.asyncio
async def test_endpoint_status_reports_true_only_for_present_endpoint(
    make_client: MakeClientFactory, answer: Any, expected: bool
) -> None:
    """The deprecated alias is true only when the Router answers the endpoint successfully.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        answer (Any): Router answer for the status endpoint.
        expected (bool): Boolean result the alias must return.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", STATUS_PATH): answer})
    try:
        with pytest.warns(DeprecationWarning):
            assert await client.is_endpoint_available(STATUS_PATH) is expected  # type: ignore[deprecated]
        assert router.count("GET", STATUS_PATH) == 1
        assert router.count() == 1
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    ("answer", "error_type"),
    [
        pytest.param(403, OPNsensePrivilegeMissing, id="forbidden-403"),
        pytest.param(500, OPNsenseError, id="transient-500"),
        pytest.param(TimeoutError("timed out"), OPNsenseError, id="transient-timeout"),
    ],
)
@pytest.mark.asyncio
async def test_endpoint_status_raises_for_forbidden_or_transient_in_throw_mode(
    make_client: MakeClientFactory, answer: Any, error_type: type[OPNsenseError]
) -> None:
    """In throw mode the alias raises for a 403 or a transient failure, and never for a 404.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        answer (Any): Router answer for the status endpoint.
        error_type (type[OPNsenseError]): Public error type the alias must raise.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", STATUS_PATH): answer})
    client.toggle_throwing_errors(True)
    try:
        with pytest.warns(DeprecationWarning), pytest.raises(error_type):
            await client.is_endpoint_available(STATUS_PATH)  # type: ignore[deprecated]
        assert router.count("GET", STATUS_PATH) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_endpoint_status_404_is_false_without_raising_in_throw_mode(
    make_client: MakeClientFactory,
) -> None:
    """A 404 is a normal absent answer, so the alias returns False even in throw mode.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", STATUS_PATH): 404})
    client.toggle_throwing_errors(True)
    try:
        with pytest.warns(DeprecationWarning):
            assert await client.is_endpoint_available(STATUS_PATH) is False  # type: ignore[deprecated]
        assert router.count("GET", STATUS_PATH) == 1
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    "absent_answer",
    [
        pytest.param(404, id="absent-404"),
        pytest.param(403, id="forbidden-403"),
    ],
)
@pytest.mark.asyncio
async def test_endpoint_status_caches_absent_answer_until_force_refresh(
    make_client: MakeClientFactory, absent_answer: int
) -> None:
    """A cached absent answer is reused without a request until the caller forces a refresh.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        absent_answer (int): Router answer that marks the endpoint absent.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", STATUS_PATH): absent_answer})
    try:
        for _ in range(2):
            with pytest.warns(DeprecationWarning):
                assert await client.is_endpoint_available(STATUS_PATH) is False  # type: ignore[deprecated]
        assert router.count("GET", STATUS_PATH) == 1

        router.routes[("GET", STATUS_PATH)] = 200
        with pytest.warns(DeprecationWarning):
            assert (
                await client.is_endpoint_available(STATUS_PATH, force_refresh=True)  # type: ignore[deprecated]
                is True
            )
        assert router.count("GET", STATUS_PATH) == 2
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_endpoint_status_reuses_cached_present_answer_until_force_refresh(
    make_client: MakeClientFactory,
) -> None:
    """A cached present answer is reused without a request until the caller forces a refresh.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", STATUS_PATH): 200})
    try:
        for _ in range(3):
            with pytest.warns(DeprecationWarning):
                assert await client.is_endpoint_available(STATUS_PATH) is True  # type: ignore[deprecated]
        assert router.count("GET", STATUS_PATH) == 1

        with pytest.warns(DeprecationWarning):
            assert (
                await client.is_endpoint_available(STATUS_PATH, force_refresh=True)  # type: ignore[deprecated]
                is True
            )
        assert router.count("GET", STATUS_PATH) == 2
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_endpoint_status_cache_keeps_query_variants_separate(
    make_client: MakeClientFactory,
) -> None:
    """The deprecated public status alias caches outcomes per exact query path.

    Args:
        make_client (MakeClientFactory): Factory for clients using a mocked HTTP session.
    """
    client, session = make_mock_session_client(make_client)
    yes_path = "/api/diagnostics/interface/search_arp?resolve=yes"
    no_path = "/api/diagnostics/interface/search_arp?resolve=no"
    router = install_fake_router(
        client,
        session,
        routes={("GET", yes_path): 403, ("GET", no_path): 200},
    )
    try:
        for _ in range(2):
            with pytest.warns(DeprecationWarning):
                assert await client.is_endpoint_available(yes_path) is False  # type: ignore[deprecated]
        for _ in range(2):
            with pytest.warns(DeprecationWarning):
                assert await client.is_endpoint_available(no_path) is True  # type: ignore[deprecated]

        assert router.count("GET", yes_path) == 1
        assert router.count("GET", no_path) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_endpoint_status_transient_failure_is_not_cached(
    make_client: MakeClientFactory,
) -> None:
    """A transient failure is not cached, so the next status check asks the Router again.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", STATUS_PATH): 500})
    try:
        for _ in range(2):
            with pytest.warns(DeprecationWarning):
                assert await client.is_endpoint_available(STATUS_PATH) is False  # type: ignore[deprecated]
        assert router.count("GET", STATUS_PATH) == 2
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_endpoint_status_empty_path_is_false_without_request(
    make_client: MakeClientFactory,
) -> None:
    """An empty path is reported unavailable without sending a request.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session)
    try:
        with pytest.warns(DeprecationWarning):
            assert await client.is_endpoint_available("") is False  # type: ignore[deprecated]
        assert router.requests == []
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
async def test_validate_maps_firmware_request_http_errors_to_public_exceptions(
    status: int,
    expected_exception: type[Exception],
    make_client: MakeClientFactory,
) -> None:
    """Verify ``validate`` maps HTTP failures on the firmware status request.

    Args:
        status (int): HTTP response status returned by the firmware status request.
        expected_exception (type[Exception]): Public exception expected from ``validate``.
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts validation-time firmware request error mapping.
    """
    client, session = make_mock_session_client(make_client)
    path = "/api/core/firmware/status"
    router = install_fake_router(client, session, routes={("GET", path): status})
    try:
        with pytest.raises(expected_exception):
            await client.validate()
        assert router.count("GET", path) == 1
        assert await client.get_host_firmware_version() is None
        assert router.count("GET", path) == 2
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


# The tests below drive NUT status, a plain availability-gated read, through the public
# client over a fake Router to pin the shared outcome and cache behavior.
NUT_PATH = "/api/nut/diagnostics/upsstatus"
NUT_DATA = {"status": {"ups.status": "OL"}}


@pytest.mark.asyncio
async def test_present_endpoint_costs_one_request_per_read(make_client: MakeClientFactory) -> None:
    """A present endpoint sends exactly one request per read, with no separate probe.

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


@pytest.mark.parametrize("status", [404, 403])
@pytest.mark.asyncio
async def test_absent_endpoint_is_cached_until_ttl_expires(
    make_client: MakeClientFactory, status: int
) -> None:
    """A missing or forbidden endpoint is not requested again until the cache TTL expires.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        status (int): HTTP status the Router returns while the endpoint is absent.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", NUT_PATH): status})
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
async def test_absent_read_ttl_starts_after_request_completion(
    make_client: MakeClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A public absent-read cache entry keeps its full TTL after a delayed request.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing the endpoint clock.
    """
    client, session = make_mock_session_client(make_client)
    client._endpoint_cache_ttl_seconds = 10
    router = install_fake_router(client, session, routes={("GET", NUT_PATH): 404})
    current_time = [datetime(2026, 1, 1, tzinfo=UTC)]

    class ControlledClock:
        """Clock whose current time can be advanced by the fake HTTP request."""

        @staticmethod
        def now() -> datetime:
            """Return the current controlled time.

            Returns:
                datetime: Current fake time.
            """
            return current_time[0]

    monkeypatch.setattr(aiopnsense.client_endpoint, "datetime", ControlledClock)
    original_get = session.get

    def delayed_get(url: str, **kwargs: Any) -> Any:
        """Advance time while dispatching the request, then return its response.

        Args:
            url (str): Requested URL.
            kwargs (Any): Request options passed to the fake session.

        Returns:
            Any: Fake response for the request.
        """
        response = original_get(url, **kwargs)
        current_time[0] += timedelta(seconds=5)
        return response

    session.get = delayed_get
    try:
        assert await client.get_nut_ups_status() == {}
        router.routes[("GET", NUT_PATH)] = NUT_DATA
        current_time[0] += timedelta(seconds=9)
        assert await client.get_nut_ups_status() == {}
        assert router.count("GET", NUT_PATH) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_endpoint_lost_after_success_is_recorded_as_absent(
    make_client: MakeClientFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """A 404 on a previously present endpoint is cached as absent and logged quietly.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        caplog (pytest.LogCaptureFixture): Fixture capturing log records.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", NUT_PATH): NUT_DATA})
    try:
        assert await client.get_nut_ups_status() == NUT_DATA
        router.routes[("GET", NUT_PATH)] = 404
        with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
            assert await client.get_nut_ups_status() == {}
            assert await client.get_nut_ups_status() == {}
        assert router.count("GET", NUT_PATH) == 2
        assert not [record for record in caplog.records if record.levelno >= logging.WARNING]
    finally:
        await client.async_close()


@pytest.mark.parametrize("failure", [500, TimeoutError("timed out")])
@pytest.mark.asyncio
async def test_transient_failure_is_not_cached_and_logs_once_per_outage(
    make_client: MakeClientFactory, caplog: pytest.LogCaptureFixture, failure: Any
) -> None:
    """A transient failure is retried on the next read and warns once per outage.

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
            warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
            assert len(warnings) == 1
            assert router.count("GET", NUT_PATH) == 2

            router.routes[("GET", NUT_PATH)] = NUT_DATA
            assert await client.get_nut_ups_status() == NUT_DATA
        recovered = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(recovered) == 1
        assert "recovered" in recovered[0].getMessage()
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    ("throw_errors", "expected_requests"),
    [
        pytest.param(False, 1, id="default-mode-trusts-cached-403"),
        pytest.param(True, 2, id="throw-mode-rechecks"),
    ],
)
@pytest.mark.asyncio
async def test_cached_permission_error_is_rechecked_only_in_throw_mode(
    make_client: MakeClientFactory, throw_errors: bool, expected_requests: int
) -> None:
    """A cached 403 is trusted in default mode but requested again, and raised, in throw mode.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Whether the client raises public errors.
        expected_requests (int): Requests the Router should see across two reads.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", NUT_PATH): 403})
    try:
        assert await client.get_nut_ups_status() == {}
        client.toggle_throwing_errors(throw_errors)
        if throw_errors:
            with pytest.raises(OPNsensePrivilegeMissing):
                await client.get_nut_ups_status()
        else:
            assert await client.get_nut_ups_status() == {}
        assert router.count("GET", NUT_PATH) == expected_requests
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_cached_permission_outcome_preserves_forbidden_status(
    make_client: MakeClientFactory,
) -> None:
    """A cached permission outcome retains its original 403 classification.

    Args:
        make_client (MakeClientFactory): Factory returning an OPNsense client.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", NUT_PATH): 403})
    try:
        assert (await client._fetch_endpoint_outcome(NUT_PATH)).http_status == 403
        cached = await client._fetch_endpoint_outcome(NUT_PATH)
        assert cached.is_absent
        assert cached.http_status == 403
        assert router.count("GET", NUT_PATH) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_older_data_read_cannot_overwrite_newer_status_success(
    make_client: MakeClientFactory,
) -> None:
    """A delayed queued read cannot replace a newer successful availability check.

    Args:
        make_client (MakeClientFactory): Factory returning an OPNsense client.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", NUT_PATH): 404})
    original_queue = client._queue_request
    old_response_ready = asyncio.Event()
    release_old_response = asyncio.Event()
    delay_first = True

    async def delayed_queue(method: str, path: str, payload: Any = None) -> Any:
        """Delay the first completed NUT read until ``release_old_response`` is set.

        Args:
            method (str): HTTP method passed to the queued request.
            path (str): Endpoint path passed to the queued request.
            payload (Any): Optional request payload.

        Returns:
            Any: NUT endpoint outcome returned after the release event.
        """
        nonlocal delay_first
        outcome = await original_queue(method, path, payload)
        if delay_first:
            delay_first = False
            old_response_ready.set()
            await release_old_response.wait()
        return outcome

    client._queue_request = delayed_queue
    old_read = asyncio.create_task(client.get_nut_ups_status())
    try:
        await asyncio.wait_for(old_response_ready.wait(), timeout=1)
        router.routes[("GET", NUT_PATH)] = NUT_DATA
        assert (await client._check_endpoint_status(NUT_PATH, force_refresh=True)).is_data
        release_old_response.set()
        assert await old_read == {}
        assert await client.get_nut_ups_status() == NUT_DATA
        assert router.count("GET", NUT_PATH) == 3
    finally:
        release_old_response.set()
        await old_read
        await client.async_close()


@pytest.mark.asyncio
async def test_older_result_can_publish_when_newer_observation_is_cancelled(
    make_client: MakeClientFactory,
) -> None:
    """A started but cancelled newer check does not discard an older completed result.

    Args:
        make_client (MakeClientFactory): Factory returning an OPNsense client.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", NUT_PATH): 404})
    original_queue = client._queue_request
    original_get = client._do_get_outcome
    old_ready = asyncio.Event()
    release_old = asyncio.Event()
    new_ready = asyncio.Event()
    release_new = asyncio.Event()

    async def delayed_queue(method: str, path: str, payload: Any = None) -> Any:
        """Hold the older NUT read until ``release_old`` allows publication.

        Args:
            method (str): HTTP method passed to the queued request.
            path (str): Endpoint path passed to the queued request.
            payload (Any): Optional request payload.

        Returns:
            Any: NUT endpoint outcome returned after ``release_old``.
        """
        outcome = await original_queue(method, path, payload)
        old_ready.set()
        await release_old.wait()
        return outcome

    async def delayed_status(*args: Any, **kwargs: Any) -> _EndpointOutcome:
        """Pause the forced NUT status probe until cancelled or ``release_new`` is set.

        Args:
            *args (Any): Arguments passed to the original status request.
            **kwargs (Any): Keyword arguments passed to the original status request.

        Returns:
            _EndpointOutcome: Result of the original status request after release.
        """
        outcome = await original_get(*args, **kwargs)
        if len(args) > 1 and args[1] == "_check_endpoint_status":
            new_ready.set()
            await release_new.wait()
        return outcome

    client._queue_request = delayed_queue
    client._do_get_outcome = delayed_status
    old_read = asyncio.create_task(client.get_nut_ups_status())
    newer_check: asyncio.Task[_EndpointOutcome] | None = None
    try:
        await asyncio.wait_for(old_ready.wait(), timeout=1)
        router.routes[("GET", NUT_PATH)] = NUT_DATA
        newer_check = asyncio.create_task(
            client._check_endpoint_status(NUT_PATH, force_refresh=True)
        )
        await asyncio.wait_for(new_ready.wait(), timeout=1)
        newer_check.cancel()
        with pytest.raises(asyncio.CancelledError):
            await newer_check
        release_old.set()
        assert await old_read == {}
        assert await client.get_nut_ups_status() == {}
        assert router.count("GET", NUT_PATH) == 2
    finally:
        release_old.set()
        release_new.set()
        if newer_check is not None and not newer_check.done():
            newer_check.cancel()
            with pytest.raises(asyncio.CancelledError):
                await newer_check
        await old_read
        await client.async_close()


@pytest.mark.asyncio
async def test_old_generation_success_does_not_clear_new_outage(
    make_client: MakeClientFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """A success started before firmware reset cannot clear the new outage marker.

    Args:
        make_client (MakeClientFactory): Factory returning an OPNsense client.
        caplog (pytest.LogCaptureFixture): Fixture capturing log records.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", NUT_PATH): NUT_DATA})
    original_queue = client._queue_request
    old_response_ready = asyncio.Event()
    release_old_response = asyncio.Event()
    delay_first = True

    async def delayed_queue(method: str, path: str, payload: Any = None) -> Any:
        """Hold the first NUT response until ``release_old_response`` is set.

        Args:
            method (str): HTTP method passed to the queued request.
            path (str): Endpoint path passed to the queued request.
            payload (Any): Optional request payload.

        Returns:
            Any: NUT endpoint outcome from the original queue call after release.
        """
        nonlocal delay_first
        outcome = await original_queue(method, path, payload)
        if delay_first:
            delay_first = False
            old_response_ready.set()
            await release_old_response.wait()
        return outcome

    client._queue_request = delayed_queue
    old_read = asyncio.create_task(client.get_nut_ups_status())
    try:
        await asyncio.wait_for(old_response_ready.wait(), timeout=1)
        client._reset_endpoint_state()
        router.routes[("GET", NUT_PATH)] = 500
        with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
            assert await client.get_nut_ups_status() == {}
            release_old_response.set()
            assert await old_read == NUT_DATA
            assert await client.get_nut_ups_status() == {}
        warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
        recoveries = [record for record in caplog.records if "recovered" in record.getMessage()]
        assert len(warnings) == 1
        assert not recoveries
        assert router.count("GET", NUT_PATH) == 3
    finally:
        release_old_response.set()
        await old_read
        await client.async_close()


@pytest.mark.asyncio
async def test_probe_success_does_not_suppress_bounded_read_recovery(
    make_client: MakeClientFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """A probe and bounded request keep independent outage publication order.

    Args:
        make_client (MakeClientFactory): Factory returning an OPNsense client.
        caplog (pytest.LogCaptureFixture): Fixture capturing log records.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", NUT_PATH): 500})
    original_queue = client._queue_request
    old_response_ready = asyncio.Event()
    release_old_response = asyncio.Event()
    delay_next = False

    async def delayed_queue(method: str, path: str, payload: Any = None) -> Any:
        """Hold the selected NUT read until ``release_old_response`` is set.

        Args:
            method (str): HTTP method passed to the queued request.
            path (str): Endpoint path passed to the queued request.
            payload (Any): Optional request payload.

        Returns:
            Any: NUT endpoint outcome from the original queue call after release.
        """
        nonlocal delay_next
        outcome = await original_queue(method, path, payload)
        if delay_next:
            delay_next = False
            old_response_ready.set()
            await release_old_response.wait()
        return outcome

    client._queue_request = delayed_queue
    try:
        with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
            assert await client.get_nut_ups_status() == {}
            router.routes[("GET", NUT_PATH)] = NUT_DATA
            delay_next = True
            old_read = asyncio.create_task(client.get_nut_ups_status())
            await asyncio.wait_for(old_response_ready.wait(), timeout=1)
            assert (await client._check_endpoint_status(NUT_PATH, force_refresh=True)).is_data
            release_old_response.set()
            assert await old_read == NUT_DATA
            router.routes[("GET", NUT_PATH)] = 500
            assert await client.get_nut_ups_status() == {}

        warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
        recoveries = [record for record in caplog.records if "recovered" in record.getMessage()]
        assert len(warnings) == 2
        assert len(recoveries) == 1
        assert router.count("GET", NUT_PATH) == 4
    finally:
        release_old_response.set()
        await client.async_close()


@pytest.mark.asyncio
async def test_permission_failure_is_logged_separately_from_transient_outage(
    make_client: MakeClientFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """A 403 after a 500 is logged once as an escalation to permission failure.

    Args:
        make_client (MakeClientFactory): Factory returning an OPNsense client.
        caplog (pytest.LogCaptureFixture): Fixture capturing log records.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", NUT_PATH): 500})
    try:
        with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
            assert await client.get_nut_ups_status() == {}
            router.routes[("GET", NUT_PATH)] = 403
            assert await client.get_nut_ups_status() == {}
            client._endpoint_cache_ttl_seconds = 0
            assert await client.get_nut_ups_status() == {}
        warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
        errors = [record for record in caplog.records if record.levelno == logging.ERROR]
        assert len(warnings) == 1
        assert len(errors) == 1
        assert "Permission Error" in errors[0].getMessage()
        assert router.count("GET", NUT_PATH) == 3
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_cached_404_is_trusted_in_throw_mode(make_client: MakeClientFactory) -> None:
    """A cached missing endpoint stays an empty result in throw mode without another request.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", NUT_PATH): 404})
    client.toggle_throwing_errors(True)
    try:
        assert await client.get_nut_ups_status() == {}
        assert await client.get_nut_ups_status() == {}
        assert router.count("GET", NUT_PATH) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_transient_failure_clears_cached_present_status(
    make_client: MakeClientFactory,
) -> None:
    """A transient failure discards a cached present answer, so the next check asks again.

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
async def test_decode_error_clears_cached_absence(make_client: MakeClientFactory) -> None:
    """A malformed 200 supersedes a cached 404, so the next read asks the Router again.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", NUT_PATH): 404})
    try:
        assert await client.get_nut_ups_status() == {}
        assert await client.get_nut_ups_status() == {}
        assert router.count("GET", NUT_PATH) == 1

        # Expire the absence only for the read that meets the malformed response.
        client._endpoint_cache_ttl_seconds = 0
        router.routes[("GET", NUT_PATH)] = MalformedJSONResponse()
        assert await client.get_nut_ups_status() is None
        assert router.count("GET", NUT_PATH) == 2

        client._endpoint_cache_ttl_seconds = 3600
        router.routes[("GET", NUT_PATH)] = NUT_DATA
        assert await client.get_nut_ups_status() == NUT_DATA
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
    ("operation", "operation_label"),
    [
        pytest.param("status", "check_endpoint_status", id="status-check"),
        pytest.param("get", "fetch_endpoint_outcome", id="get-read"),
        pytest.param("post", "fetch_post_endpoint_outcome", id="post-read"),
    ],
)
@pytest.mark.asyncio
async def test_permission_error_log_names_the_failing_operation(
    make_client: MakeClientFactory,
    caplog: pytest.LogCaptureFixture,
    operation: str,
    operation_label: str,
) -> None:
    """The first 403 logs a permission error labeled with the operation that met it.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        caplog (pytest.LogCaptureFixture): Fixture capturing log records.
        operation (str): Endpoint operation that receives the 403.
        operation_label (str): Operation name expected in the log message.
    """
    client, session = make_mock_session_client(make_client)
    method = "POST" if operation == "post" else "GET"
    router = install_fake_router(client, session, routes={(method, NUT_PATH): 403})
    try:
        with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
            if operation == "status":
                assert (await client._check_endpoint_status(NUT_PATH)).is_absent
            elif operation == "get":
                assert (await client._fetch_endpoint_outcome(NUT_PATH)).is_absent
            else:
                assert (await client._fetch_post_endpoint_outcome(NUT_PATH)).is_absent

        errors = [
            record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR
        ]
        assert len(errors) == 1
        assert errors[0].startswith(f"Permission Error in {operation_label}. Path: ")
        assert errors[0].endswith(_PERMISSION_GUIDANCE)
        assert router.count(method, NUT_PATH) == 1
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
    assert router.get_paths.count(LATE_PATH) == 1


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
async def test_old_endpoint_probe_cannot_restore_cache_after_firmware_change(
    make_client: MakeClientFactory,
) -> None:
    """Keep a delayed old-firmware probe from overwriting refreshed endpoint state.

    Args:
        make_client (MakeClientFactory): Fixture factory returning clients.
    """
    client, session = make_mock_session_client(make_client)
    router = FirmwareRouter(client, session)
    router.firmware = "26.1.1"
    original_probe = session.get.side_effect
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
    old_probe_task = asyncio.create_task(client.get_system_info())
    try:
        await asyncio.wait_for(probe_started.wait(), timeout=1)
        router.firmware = "26.1.11"
        await client.validate(require_device_id=False)

        router.endpoints[SYSTEM_INFORMATION_SNAKE_PATH] = {"name": "router"}
        assert await client.get_system_info() == {"name": "router"}

        release_probe.set()
        assert await old_probe_task == {}
        assert await client.get_system_info() == {"name": "router"}
    finally:
        release_probe.set()
        if not old_probe_task.done():
            await asyncio.gather(old_probe_task, return_exceptions=True)
