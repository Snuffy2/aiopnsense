"""Tests for client endpoint availability and endpoint-style selection."""

import asyncio
from datetime import datetime, timedelta
import logging
from typing import Any, Self
from unittest.mock import AsyncMock

import aiohttp
import pytest

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
            args (Any): Positional arguments accepted by `_get`.
            kwargs (Any): Keyword arguments accepted by `_get`.

        Returns:
            Any: Decoded response payload returned by the GET request.
        """
        nonlocal calls
        calls += 1
        return FakeResponse(status=200, ok=True)

    session.get = _get
    try:
        assert await client._is_get_endpoint_available("/api/test/endpoint") is True
        assert await client._is_get_endpoint_available("/api/test/endpoint") is True
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
            args (Any): Positional arguments accepted by `_get`.
            kwargs (Any): Keyword arguments accepted by `_get`.

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
        assert await client._is_get_endpoint_available(path) is False
        assert await client._is_get_endpoint_available(path) is False
        assert calls == 1
        assert path in client._endpoint_checked_at
        client._endpoint_checked_at[path] = datetime.now().astimezone() - timedelta(
            seconds=client._endpoint_cache_ttl_seconds + 1
        )
        assert await client._is_get_endpoint_available(path) is True
        assert calls == 2
        assert await client._is_get_endpoint_available(path) is True
        assert calls == 2
        assert await client._is_get_endpoint_available(path, force_refresh=True) is True
        assert calls == 3
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_is_get_endpoint_available_uses_cached_404_in_throw_mode(
    make_client: MakeClientFactory,
) -> None:
    """Verify throw mode continues to trust a cached missing endpoint.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts throw mode preserves cached 404 behavior.
    """
    client, session = make_mock_session_client(make_client)
    calls = 0

    def _get(*args: object, **kwargs: object) -> FakeResponse:
        """Return a missing-endpoint response.

        Args:
            args (object): Positional arguments accepted by `_get`.
            kwargs (object): Keyword arguments accepted by `_get`.

        Returns:
            FakeResponse: Synthetic missing-endpoint response returned by the GET request.
        """
        del args, kwargs
        nonlocal calls
        calls += 1
        return FakeResponse(status=404, reason="Not Found", ok=False)

    session.get = _get
    try:
        path = "/api/test/missing"
        assert await client._is_get_endpoint_available(path) is False

        client._throw_errors = True
        assert await client._is_get_endpoint_available(path) is False

        assert calls == 1
        assert client._endpoint_availability[path] is False
        assert path in client._endpoint_checked_at
        assert path not in client._endpoint_permission_denied
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
            args (Any): Positional arguments accepted by `_get`.
            kwargs (Any): Keyword arguments accepted by `_get`.

        Returns:
            Any: Decoded response payload returned by the GET request.

        Raises:
            TimeoutError: Always raised to simulate a probe timeout.
        """
        nonlocal calls
        calls += 1
        raise TimeoutError("timeout")

    session.get = _get
    try:
        assert await client._is_get_endpoint_available("/api/test/endpoint") is False
        assert await client._is_get_endpoint_available("/api/test/endpoint") is False
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

        Raises:
            _TestClientSSLError: Always raised to simulate an SSL transport failure.
        """
        del _url, auth, timeout, ssl
        nonlocal calls
        calls += 1
        raise _TestClientSSLError()

    session.get = _get
    try:
        path = "/api/test/endpoint"
        with pytest.raises(OPNsenseSSLError):
            await client._is_get_endpoint_available(path)
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
            args (Any): Positional arguments accepted by `_get`.
            kwargs (Any): Keyword arguments accepted by `_get`.

        Returns:
            Any: Decoded response payload returned by the GET request.
        """
        nonlocal calls
        calls += 1
        return FakeResponse(status=500, reason="ERR", ok=False)

    session.get = _get
    try:
        path = "/api/test/endpoint"
        assert await client._is_get_endpoint_available(path) is False
        assert await client._is_get_endpoint_available(path) is False
        assert calls == 2
        assert path not in client._endpoint_checked_at
        assert path not in client._endpoint_availability
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_is_get_endpoint_available_caches_403_permission_error(
    make_client: MakeClientFactory,
) -> None:
    """Verify permission failures are cached until cache expiry.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
    Returns:
        None: This test asserts quiet retry avoidance for permission failures.
    """
    client, session = make_mock_session_client(make_client)
    calls = 0

    def _get(*args: object, **kwargs: object) -> FakeResponse:
        """Return a permission denial followed by a successful endpoint response.

        Args:
            args (object): Positional arguments accepted by `_get`.
            kwargs (object): Keyword arguments accepted by `_get`.

        Returns:
            FakeResponse: Synthetic response returned by the GET request.
        """
        del args, kwargs
        nonlocal calls
        calls += 1
        if calls == 1:
            return FakeResponse(status=403, reason="Forbidden", ok=False)
        return FakeResponse(status=200, ok=True)

    session.get = _get
    try:
        path = "/api/test/endpoint"
        assert await client._is_get_endpoint_available(path) is False
        assert await client._is_get_endpoint_available(path) is False
        assert calls == 1
        assert client._endpoint_availability[path] is False
        assert path in client._endpoint_checked_at
        assert path in client._endpoint_permission_denied
        expired_at = datetime.now().astimezone() - timedelta(
            seconds=client._endpoint_cache_ttl_seconds + 1
        )
        client._endpoint_checked_at[path] = expired_at

        assert await client._is_get_endpoint_available(path) is True
        assert calls == 2
        assert client._endpoint_availability[path] is True
        assert client._endpoint_checked_at[path] > expired_at
        assert path not in client._endpoint_permission_denied
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
        assert await client._is_get_endpoint_available(path) is False
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
            args (Any): Positional arguments accepted by `_get`.
            kwargs (Any): Keyword arguments accepted by `_get`.

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
        with pytest.raises(OPNsenseInvalidAuth) as err:
            await client._is_get_endpoint_available(path)
        assert err.value.status == 401
        assert calls == 1
        assert path not in client._endpoint_checked_at
        assert path not in client._endpoint_availability
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
            assert await legacy_alias(
                "/api/test/endpoint",
                force_refresh=True,
            )
        assert calls == 1
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

    def _post(*args: object, **kwargs: object) -> FakeResponse:
        """Return a fake POST response.

        Args:
            args (object): Positional arguments accepted by `_post`.
            kwargs (object): Keyword arguments accepted by `_post`.

        Returns:
            FakeResponse: Synthetic HTTP response used by the test.
        """
        del args, kwargs
        nonlocal calls
        calls += 1
        return FakeResponse(status=200, ok=True)

    session.post = _post
    try:
        assert await client._is_post_endpoint_available("/api/test/endpoint") is True
        assert await client._is_post_endpoint_available("/api/test/endpoint") is True
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

    def _post(*args: object, **kwargs: object) -> FakeResponse:
        """Return a fake POST response.

        Args:
            args (object): Positional arguments accepted by `_post`.
            kwargs (object): Keyword arguments accepted by `_post`.

        Returns:
            FakeResponse: Synthetic HTTP response used by the test.
        """
        del args, kwargs
        nonlocal calls
        calls += 1
        return FakeResponse(status=404, reason="ERR", ok=False)

    session.post = _post
    try:
        path = "/api/test/endpoint"
        assert await client._is_post_endpoint_available(path) is False
        assert await client._is_post_endpoint_available(path) is False
        assert calls == 1
        assert f"post:{path}" in client._endpoint_checked_at
        assert path not in client._endpoint_checked_at
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [None, 123, False, {}, [], ""])
async def test_is_post_endpoint_available_returns_none_for_invalid_paths(
    make_client: MakeClientFactory,
    path: object,
) -> None:
    """Verify invalid POST probe paths do not raise and return ``None``.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        path (object): Non-string path candidate.

    Returns:
        None: This test asserts unknown availability behavior for malformed probes.
    """
    client, session = make_mock_session_client(make_client)

    try:
        assert await client._is_post_endpoint_available(path) is None
        session.post.assert_not_called()
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/captiveportal/voucher/generate_vouchers/srv/",
        "/api/captiveportal/voucher/generateVouchers/srv/",
        "/api/core/firmware/check",
        "/api/core/firmware/update",
        "/api/core/firmware/upgrade",
        "/api/core/service/restart/unbound",
        "/api/core/service/start/unbound",
        "/api/core/service/stop/unbound",
        "/api/core/system/dismiss_status",
        "/api/core/system/dismissStatus",
        "/api/core/system/halt",
        "/api/core/system/reboot",
        "/api/diagnostics/firewall/kill_states/",
        "/api/firewall/alias/reconfigure",
        "/api/firewall/alias/set",
        "/api/firewall/alias/toggle_item/alias-uuid/1",
        "/api/firewall/alias/toggleItem/alias-uuid/1",
        "/api/firewall/d_nat/apply",
        "/api/firewall/d_nat/toggle_rule/rule-uuid/0",
        "/api/firewall/filter/apply",
        "/api/firewall/filter/toggle_rule/rule-uuid/1",
        "/api/firewall/npt/apply",
        "/api/firewall/npt/toggle_rule/rule-uuid/1",
        "/api/firewall/one_to_one/apply",
        "/api/firewall/one_to_one/toggle_rule/rule-uuid/1",
        "/api/firewall/source_nat/apply",
        "/api/firewall/source_nat/toggle_rule/rule-uuid/1",
        "/api/interfaces/overview/reload_interface/wan",
        "/api/interfaces/overview/reloadInterface/wan",
        "/api/openvpn/instances/toggle/vpn-uuid",
        "/api/openvpn/service/reconfigure",
        "/api/unbound/service/restart",
        "/api/unbound/settings/set",
        "/api/unbound/settings/toggle_dnsbl/dnsbl-uuid/1",
        "/api/wireguard/client/toggle_client/wg-uuid",
        "/api/wireguard/client/toggleClient/wg-uuid",
        "/api/wireguard/server/toggle_server/wg-uuid",
        "/api/wireguard/server/toggleServer/wg-uuid",
        "/api/wireguard/service/reconfigure",
        "/api/wol/wol/set",
    ],
)
async def test_is_post_endpoint_available_returns_none_for_unsafe_path_without_http_request(
    make_client: MakeClientFactory,
    path: str,
) -> None:
    """Verify unsafe POST availability probes return ``None`` without calling HTTP.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        path (str): Unsafe POST path that must not trigger an HTTP probe.

    Returns:
        None: This test validates unknown availability behavior for unsafe POST paths.
    """
    client, session = make_mock_session_client(make_client)
    calls = 0

    def _post(*_args: object, **_kwargs: object) -> FakeResponse:
        """Return a fake POST response.

        Args:
            _args (object): Positional arguments accepted by `_post`.
            _kwargs (object): Keyword arguments accepted by `_post`.

        Returns:
            FakeResponse: Synthetic HTTP response used by the test.
        """
        nonlocal calls
        calls += 1
        return FakeResponse(status=200, ok=True)

    session.post = _post
    try:
        assert await client._is_post_endpoint_available(path) is None
        assert calls == 0
        assert path not in client._endpoint_availability
        assert f"post:{path}" not in client._endpoint_checked_at
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    "path",
    [
        "/api/firewall/filter/toggle_rule/rule-uuid/1",
        "/api/firewall/alias/toggleItem/alias-uuid/1",
        "/api/interfaces/overview/reloadInterface/wan",
        "/api/captiveportal/voucher/generateVouchers/srv/",
        "/api/unbound/settings/toggle_dnsbl/dnsbl-uuid/1",
        "/api/wireguard/client/toggleClient/wg-uuid",
    ],
)
def test_unsafe_post_endpoint_action_tokens_match_snake_and_camel_case(
    make_client: MakeClientFactory,
    path: str,
) -> None:
    """Verify mutating action tokens catch snake_case and camelCase path segments.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.
        path (str): Endpoint path with a mutating action token.

    Returns:
        None: This test asserts token-based unsafe matching catches dynamic endpoints.
    """
    client = make_client()
    assert client._is_post_endpoint_probe_blocked(path) is True


def test_unsafe_post_endpoint_action_tokens_do_not_match_non_action_words(
    make_client: MakeClientFactory,
) -> None:
    """Verify action-token matching does not block non-mutating containing words.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts segments such as ``settings`` do not match the
            mutating ``set`` token.
    """
    client = make_client()
    assert client._is_post_endpoint_probe_blocked("/api/unbound/settings/search_dnsbl") is False


@pytest.mark.asyncio
async def test_is_post_endpoint_available_allows_read_only_post_path(
    make_client: MakeClientFactory,
) -> None:
    """Verify read-only POST endpoints are still probed normally.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates the unsafe list does not block read-only POST endpoints.
    """
    client, session = make_mock_session_client(make_client)
    calls = 0

    def _post(*args: object, **kwargs: object) -> FakeResponse:
        """Return a fake POST response.

        Args:
            args (object): Positional arguments accepted by `_post`.
            kwargs (object): Keyword arguments accepted by `_post`.

        Returns:
            FakeResponse: Synthetic HTTP response used by the test.
        """
        del args, kwargs
        nonlocal calls
        calls += 1
        return FakeResponse(status=200, ok=True)

    session.post = _post
    try:
        path = "/api/core/firmware/changelog/26.1.1"
        assert await client._is_post_endpoint_available(path) is True
        assert calls == 1
        assert f"post:{path}" in client._endpoint_checked_at
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
