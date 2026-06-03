"""Tests for client endpoint availability and endpoint-style selection."""

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import aiohttp
import pytest

from aiopnsense.exceptions import (
    OPNsenseConnectionError,
    OPNsenseInvalidAuth,
    OPNsensePrivilegeMissing,
    OPNsenseSSLError,
)
from tests.conftest import FakeResponse, MakeClientFactory, make_mock_session_client


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
async def test_is_get_endpoint_available_caches_success(make_client: MakeClientFactory) -> None:
    """Verify endpoint availability results are cached after a successful probe.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts positive endpoint cache behavior.
    """
    client, session = make_mock_session_client(make_client)
    calls = 0

    def _get(*args: Any, **kwargs: Any) -> Any:
        """Get.

        Args:
            *args (Any): Positional arguments forwarded to the wrapped callable.
            **kwargs (Any): Keyword arguments forwarded to the wrapped callable.

        Returns:
            Any: Decoded response payload returned by the GET request.
        """
        nonlocal calls
        calls += 1
        return FakeResponse(status=200, ok=True)

    session.get = _get
    try:
        assert await client.is_get_endpoint_available("/api/test/endpoint") is True
        assert await client.is_get_endpoint_available("/api/test/endpoint") is True
        assert calls == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_is_get_endpoint_available_cache_false_by_ttl_and_force_refresh(
    make_client: MakeClientFactory,
) -> None:
    """Verify negative endpoint results are cached until TTL expiry or force refresh.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts TTL and force-refresh behavior.
    """
    client, session = make_mock_session_client(make_client)
    calls = 0

    def _get(*args: Any, **kwargs: Any) -> Any:
        """Get.

        Args:
            *args (Any): Positional arguments forwarded to the wrapped callable.
            **kwargs (Any): Keyword arguments forwarded to the wrapped callable.

        Returns:
            Any: Decoded response payload returned by the GET request.
        """
        nonlocal calls
        calls += 1
        if calls == 1:
            return FakeResponse(status=404, reason="ERR", ok=False)
        return FakeResponse(status=200, reason="ERR", ok=True)

    session.get = _get
    try:
        path = "/api/test/endpoint"
        assert await client.is_get_endpoint_available(path) is False
        assert await client.is_get_endpoint_available(path) is False
        assert calls == 1
        assert path in client._endpoint_checked_at
        client._endpoint_checked_at[path] = datetime.now().astimezone() - timedelta(
            seconds=client._endpoint_cache_ttl_seconds + 1
        )
        assert await client.is_get_endpoint_available(path) is True
        assert calls == 2
        assert await client.is_get_endpoint_available(path) is True
        assert calls == 2
        assert await client.is_get_endpoint_available(path, force_refresh=True) is True
        assert calls == 3
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_is_get_endpoint_available_handles_timeout(make_client: MakeClientFactory) -> None:
    """Verify endpoint probing returns ``False`` and retries after timeouts.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts timeout handling and retry behavior.
    """
    client, session = make_mock_session_client(make_client)
    calls = 0

    def _get(*args: Any, **kwargs: Any) -> Any:
        """Get.

        Args:
            *args (Any): Positional arguments forwarded to the wrapped callable.
            **kwargs (Any): Keyword arguments forwarded to the wrapped callable.

        Returns:
            Any: Decoded response payload returned by the GET request.
        """
        nonlocal calls
        calls += 1
        raise TimeoutError("timeout")

    session.get = _get
    try:
        assert await client.is_get_endpoint_available("/api/test/endpoint") is False
        assert await client.is_get_endpoint_available("/api/test/endpoint") is False
        assert calls == 2
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_is_get_endpoint_available_raises_transport_error_when_throw_enabled(
    make_client: MakeClientFactory,
) -> None:
    """Verify endpoint probing re-raises transport errors in throw mode.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts throw-mode transport exception behavior.
    """
    client, session = make_mock_session_client(make_client)
    client._throw_errors = True
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
        """
        del _url, auth, timeout, ssl
        nonlocal calls
        calls += 1
        raise _TestClientSSLError()

    session.get = _get
    try:
        path = "/api/test/endpoint"
        with pytest.raises(_TestClientSSLError):
            await client.is_get_endpoint_available(path)
        assert calls == 1
        assert path not in client._endpoint_checked_at
        assert path not in client._endpoint_availability
    finally:
        await client.async_close()


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
async def test_is_get_endpoint_available_does_not_cache_non_404_http_errors(
    make_client: MakeClientFactory,
) -> None:
    """Verify non-404 HTTP failures are not cached for endpoint availability.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts retry behavior for non-404 failures.
    """
    client, session = make_mock_session_client(make_client)
    calls = 0

    def _get(*args: Any, **kwargs: Any) -> Any:
        """Get.

        Args:
            *args (Any): Positional arguments forwarded to the wrapped callable.
            **kwargs (Any): Keyword arguments forwarded to the wrapped callable.

        Returns:
            Any: Decoded response payload returned by the GET request.
        """
        nonlocal calls
        calls += 1
        return FakeResponse(status=500, reason="ERR", ok=False)

    session.get = _get
    try:
        path = "/api/test/endpoint"
        assert await client.is_get_endpoint_available(path) is False
        assert await client.is_get_endpoint_available(path) is False
        assert calls == 2
        assert path not in client._endpoint_checked_at
        assert path not in client._endpoint_availability
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_is_get_endpoint_available_raises_non_404_http_errors_when_throw_enabled(
    make_client: MakeClientFactory,
) -> None:
    """Verify throw-mode endpoint probing re-raises non-404 HTTP failures.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts throw-mode HTTP exception behavior.
    """
    client, session = make_mock_session_client(make_client)
    client._throw_errors = True
    calls = 0

    def _get(*args: Any, **kwargs: Any) -> Any:
        """Get.

        Args:
            *args (Any): Positional arguments forwarded to the wrapped callable.
            **kwargs (Any): Keyword arguments forwarded to the wrapped callable.

        Returns:
            Any: Decoded response payload returned by the GET request.
        """
        nonlocal calls
        calls += 1
        return FakeResponse(
            status=401,
            reason="Unauthorized",
            ok=False,
            include_request_info=True,
        )

    session.get = _get
    try:
        path = "/api/test/endpoint"
        with pytest.raises(aiohttp.ClientResponseError) as err:
            await client.is_get_endpoint_available(path)
        assert err.value.status == 401
        assert calls == 1
        assert path not in client._endpoint_checked_at
        assert path not in client._endpoint_availability
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_is_post_endpoint_available_caches_success(make_client: MakeClientFactory) -> None:
    """Verify POST endpoint availability results are cached after success.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts positive POST endpoint cache behavior.
    """
    client, session = make_mock_session_client(make_client)
    calls = 0

    def _post(*args: Any, **kwargs: Any) -> Any:
        """Post."""
        del args, kwargs
        nonlocal calls
        calls += 1
        return FakeResponse(status=200, ok=True)

    session.post = _post
    try:
        assert await client.is_post_endpoint_available("/api/test/endpoint") is True
        assert await client.is_post_endpoint_available("/api/test/endpoint") is True
        assert calls == 1
        assert "post:/api/test/endpoint" in client._endpoint_checked_at
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_is_post_endpoint_available_caches_404_missing_plugin(
    make_client: MakeClientFactory,
) -> None:
    """Verify POST endpoint availability caches 404 results using a method-aware key.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts quiet retry avoidance for missing POST endpoints.
    """
    client, session = make_mock_session_client(make_client)
    calls = 0

    def _post(*args: Any, **kwargs: Any) -> Any:
        """Post."""
        del args, kwargs
        nonlocal calls
        calls += 1
        return FakeResponse(status=404, reason="ERR", ok=False)

    session.post = _post
    try:
        path = "/api/test/endpoint"
        assert await client.is_post_endpoint_available(path) is False
        assert await client.is_post_endpoint_available(path) is False
        assert calls == 1
        assert f"post:{path}" in client._endpoint_checked_at
        assert path not in client._endpoint_checked_at
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
            *args (Any): Positional arguments forwarded to the wrapped callable.
            **kwargs (Any): Keyword arguments forwarded to the wrapped callable.

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


@pytest.mark.parametrize(
    ("firmware_version", "expected_use_snake_case", "expected_path"),
    [
        ("25.1", False, "/camelCase"),
        ("25.7", True, "/snake_case"),
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

        await client.set_use_snake_case()

        assert client._use_snake_case is expected_use_snake_case
        client.get_host_firmware_version.assert_awaited_once_with()
        assert await client._get_endpoint_path("/snake_case", "/camelCase") == expected_path
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

        client.set_use_snake_case = AsyncMock(side_effect=fake_set_use_snake_case)

        assert await client._get_endpoint_path("/snake_case", "/camelCase") == "/camelCase"
        client.set_use_snake_case.assert_awaited_once_with()
    finally:
        await client.async_close()
