"""Tests for client endpoint availability and endpoint-style selection."""

import logging
from typing import Any
from unittest.mock import AsyncMock

import aiohttp
import pytest

from aiopnsense._endpoint_outcome import EndpointOutcome
from aiopnsense.exceptions import (
    OPNsenseConnectionError,
    OPNsenseError,
    OPNsenseInvalidAuth,
    OPNsensePrivilegeMissing,
    OPNsenseSSLError,
    OPNsenseUnknownFirmware,
)
from tests.conftest import MakeClientFactory, install_fake_router, make_mock_session_client


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
    client._throw_errors = False
    router = install_fake_router(client, session, get_payloads=[_TestClientSSLError()])
    try:
        with pytest.raises(OPNsenseSSLError):
            await client.validate()
        assert router.count("GET") == 1
        assert client._throw_errors is False
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
    router = install_fake_router(client, session, get_payloads=[403, 403])
    try:
        path = "/api/core/firmware/status"
        assert await client.get_host_firmware_version() is None
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
        assert "Endpoint availability probing is internal" in legacy_alias.__deprecated__  # type: ignore[attr-defined]
        with pytest.warns(DeprecationWarning, match="Endpoint availability probing is internal"):
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
        assert {method for method, _url, _body in router.requests} == {"GET"}
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
    install_fake_router(client, session, routes={("GET", STATUS_PATH): answer})
    client.toggle_throwing_errors(True)
    try:
        with pytest.warns(DeprecationWarning), pytest.raises(error_type):
            await client.is_endpoint_available(STATUS_PATH)  # type: ignore[deprecated]
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
    install_fake_router(client, session, routes={("GET", STATUS_PATH): 404})
    client.toggle_throwing_errors(True)
    try:
        with pytest.warns(DeprecationWarning):
            assert await client.is_endpoint_available(STATUS_PATH) is False  # type: ignore[deprecated]
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
    client._throw_errors = False
    router = install_fake_router(client, session, get_payloads=[status])
    try:
        path = "/api/core/firmware/status"
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
        (None, True, "/snake_case"),
        ("invalid-version", True, "/snake_case"),
    ],
)
@pytest.mark.asyncio
async def test_set_use_snake_case_selects_expected_endpoint_style(
    firmware_version: str | None,
    expected_use_snake_case: bool,
    expected_path: str,
    make_client: MakeClientFactory,
) -> None:
    """Verify firmware-driven snake-case selection and endpoint resolution.

    Args:
        firmware_version (str | None): Firmware version returned by the mocked client.
        expected_use_snake_case (bool): Expected endpoint-style flag after initialization.
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
    ("firmware_version", "expected_use_snake_case"),
    [
        pytest.param("invalid-version", True, id="invalid-version"),
        pytest.param(None, None, id="missing-version"),
    ],
)
@pytest.mark.asyncio
async def test_set_use_snake_case_initial_raises_unknown_firmware(
    make_client: MakeClientFactory,
    firmware_version: str | None,
    expected_use_snake_case: bool | None,
) -> None:
    """Verify legacy initial setup raises when firmware cannot be identified.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        firmware_version (str | None): Invalid or missing firmware version returned by the client.
        expected_use_snake_case (bool | None): Expected state to assert, when covered by the
            original case.

    Returns:
        None: This test asserts the preserved compatibility path.
    """
    client = make_client()
    try:
        client.get_host_firmware_version = AsyncMock(return_value=firmware_version)

        with pytest.raises(OPNsenseUnknownFirmware):
            await client._set_use_snake_case(initial=True)

        client.get_host_firmware_version.assert_awaited_once_with()
        if expected_use_snake_case is not None:
            assert client._use_snake_case is expected_use_snake_case
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
    outcome = EndpointOutcome.data(payload)

    assert outcome.is_data
    assert outcome.mapping() == mapping
    assert outcome.sequence() == sequence
