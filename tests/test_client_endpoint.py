"""Tests for client endpoint availability and endpoint-style selection."""

import asyncio
import logging
from time import monotonic
from typing import Any
from unittest.mock import AsyncMock

import aiohttp
import pytest

from aiopnsense.exceptions import (
    OPNsenseConnectionError,
    OPNsenseInvalidAuth,
    OPNsenseInvalidArgument,
    OPNsensePrivilegeMissing,
    OPNsenseSSLError,
    OPNsenseUnknownFirmware,
)
from aiopnsense.const import DEFAULT_NEGATIVE_CACHE_TTL_SECONDS
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
        cache_key = ("get", path)
        assert await client._is_get_endpoint_available(path) is False
        assert await client._is_get_endpoint_available(path) is False
        assert calls == 1
        assert cache_key in client._endpoint_checked_at
        client._endpoint_checked_at[cache_key] = monotonic() - (
            client._endpoint_cache_ttl_seconds + 1
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
        """
        del _url, auth, timeout, ssl
        nonlocal calls
        calls += 1
        raise _TestClientSSLError()

    session.get = _get
    try:
        path = "/api/test/endpoint"
        cache_key = ("get", path)
        with pytest.raises(OPNsenseSSLError):
            await client._is_get_endpoint_available(path)
        assert calls == 1
        assert cache_key not in client._endpoint_checked_at
        assert cache_key not in client._endpoint_availability
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
        cache_key = ("get", path)
        assert await client._is_get_endpoint_available(path) is False
        assert await client._is_get_endpoint_available(path) is False
        assert calls == 2
        assert cache_key not in client._endpoint_checked_at
        assert cache_key not in client._endpoint_availability
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
        cache_key = ("get", path)
        with pytest.raises(OPNsenseInvalidAuth) as err:
            await client._is_get_endpoint_available(path)
        assert err.value.status == 401
        assert calls == 1
        assert cache_key not in client._endpoint_checked_at
        assert cache_key not in client._endpoint_availability
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
            *args (object): Positional arguments forwarded to the stub.
            **kwargs (object): Keyword arguments forwarded to the stub.

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
            *args (object): Positional arguments forwarded to the stub.
            **kwargs (object): Keyword arguments forwarded to the stub.

        Returns:
            FakeResponse: Synthetic HTTP response used by the test.
        """
        del args, kwargs
        nonlocal calls
        calls += 1
        return FakeResponse(status=200, ok=True)

    session.post = _post
    try:
        path = "/api/test/endpoint"
        cache_key = ("post", path)
        assert await client._is_post_endpoint_available(path) is True
        assert await client._is_post_endpoint_available(path) is True
        assert calls == 1
        assert cache_key in client._endpoint_checked_at
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
            *args (object): Positional arguments forwarded to the stub.
            **kwargs (object): Keyword arguments forwarded to the stub.

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
        cache_key = ("post", path)
        assert await client._is_post_endpoint_available(path) is False
        assert await client._is_post_endpoint_available(path) is False
        assert calls == 1
        assert cache_key in client._endpoint_checked_at
        assert ("get", path) not in client._endpoint_checked_at
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

    Returns:
        None: This test validates unknown availability behavior for unsafe POST paths.
    """
    client, session = make_mock_session_client(make_client)
    calls = 0

    def _post(*_args: object, **_kwargs: object) -> FakeResponse:
        """Return a fake POST response.

        Args:
            *_args (object): Positional arguments forwarded to the stub.
            **_kwargs (object): Keyword arguments forwarded to the stub.

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
        assert ("post", path) not in client._endpoint_availability
        assert ("post", path) not in client._endpoint_checked_at
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_check_optional_get_endpoint_refreshes_positive_state_and_payload(
    monkeypatch: pytest.MonkeyPatch, make_client: MakeClientFactory
) -> None:
    """Verify optional endpoint success keeps fetching fresh payload and updates timestamp."""
    client, _session = make_mock_session_client(make_client)
    times = iter([1000.0, 1001.0, 1002.0])
    path = "/api/speedtest/service/showrecent"
    cache_key = ("get", path)
    monkeypatch.setattr(
        "aiopnsense.client_endpoint.monotonic",
        lambda: next(times),
    )
    client._get_optional = AsyncMock(side_effect=[("available", {"a": 1}), ("available", {"a": 2})])

    try:
        first_state, first_payload = await client._check_optional_get_endpoint(path)
        second_state, second_payload = await client._check_optional_get_endpoint(path)

        assert first_state == "available"
        assert second_state == "available"
        assert first_payload == {"a": 1}
        assert second_payload == {"a": 2}
        assert first_payload != second_payload
        assert cache_key in client._endpoint_checked_at
        assert client._endpoint_checked_at[cache_key] == 1002.0
        assert client._endpoint_checked_at[cache_key] > 1000.0
        assert client._endpoint_availability[cache_key] is True
        assert client._get_optional.await_count == 2
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_check_optional_get_endpoint_missing_drops_cached_positive_without_transient_warning(
    caplog: pytest.LogCaptureFixture, make_client: MakeClientFactory
) -> None:
    """Verify cached optional availability transitions from True to missing cleanly."""
    client, _session = make_mock_session_client(make_client)
    path = "/api/nut/diagnostics/upsstatus"
    cache_key = ("get", path)
    client._get_optional = AsyncMock(
        side_effect=[("available", {"status": {"x": 1}}), ("missing", {})]
    )
    client._is_core_firmware_endpoint_healthy = AsyncMock(return_value=True)

    try:
        assert await client._check_optional_get_endpoint(path) == (
            "available",
            {"status": {"x": 1}},
        )
        assert cache_key in client._endpoint_availability
        assert cache_key in client._endpoint_checked_at

        with caplog.at_level(logging.WARNING):
            assert await client._check_optional_get_endpoint(path) == ("missing", {})

        assert cache_key not in client._endpoint_availability
        assert cache_key not in client._endpoint_checked_at
        assert cache_key in client._optional_endpoint_missing_pending_confirmation
        client._is_core_firmware_endpoint_healthy.assert_awaited_once_with()
        assert not any(
            "Transient optional GET endpoint failure" in record.message for record in caplog.records
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_check_optional_get_endpoint_rechecks_pending_and_caches_negative_for_ttl(
    make_client: MakeClientFactory,
) -> None:
    """Verify pending optional miss gets confirmed by firmware status once then cached."""
    client, _session = make_mock_session_client(make_client)
    path = "/api/speedtest/service/showstat"
    cache_key = ("get", path)
    client._get_optional = AsyncMock(side_effect=[("missing", {}), ("missing", {})])
    client._is_core_firmware_endpoint_healthy = AsyncMock(return_value=True)

    try:
        first_state, first_payload = await client._check_optional_get_endpoint(path)
        second_state, second_payload = await client._check_optional_get_endpoint(path)
        third_state, third_payload = await client._check_optional_get_endpoint(path)

        assert first_state == "missing"
        assert first_payload == {}
        assert second_state == "missing"
        assert second_payload == {}
        assert third_state == "missing"
        assert third_payload == {}
        assert client._get_optional.await_count == 2
        assert client._is_core_firmware_endpoint_healthy.await_count == 2
        assert client._endpoint_availability[cache_key] is False
        assert cache_key in client._endpoint_checked_at

        # second pending confirmation should apply the optional negative-cache window
        checked_age = monotonic() - client._endpoint_checked_at[cache_key]
        assert checked_age >= 0
        assert checked_age < DEFAULT_NEGATIVE_CACHE_TTL_SECONDS
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_check_optional_get_endpoint_stale_negative_recovers_after_ttl_expiry(
    monkeypatch: pytest.MonkeyPatch, make_client: MakeClientFactory
) -> None:
    """Verify stale optional misses are retried and can recover to available."""
    client, _session = make_mock_session_client(make_client)
    path = "/api/speedtest/service/showrecent"
    cache_key = ("get", path)
    client._endpoint_availability[cache_key] = False
    times = iter([1000.0, 1001.0])
    monkeypatch.setattr(
        "aiopnsense.client_endpoint.monotonic",
        lambda: next(times),
    )
    client._endpoint_checked_at[cache_key] = 1000.0 - (DEFAULT_NEGATIVE_CACHE_TTL_SECONDS + 1)
    client._get_optional = AsyncMock(return_value=("available", {"status": "recovered"}))

    try:
        state, payload = await client._check_optional_get_endpoint(path)

        assert state == "available"
        assert payload == {"status": "recovered"}
        assert client._endpoint_availability[cache_key] is True
        assert client._endpoint_checked_at[cache_key] == 1001.0
        assert client._endpoint_checked_at[cache_key] != 1000.0 - (
            DEFAULT_NEGATIVE_CACHE_TTL_SECONDS + 1
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_check_optional_get_endpoint_stale_negative_renews_with_one_probe(
    monkeypatch: pytest.MonkeyPatch, make_client: MakeClientFactory
) -> None:
    """A persistently missing endpoint costs one optional probe per negative TTL."""
    client, _session = make_mock_session_client(make_client)
    path = "/api/speedtest/service/showrecent"
    cache_key = ("get", path)
    client._endpoint_availability[cache_key] = False
    client._endpoint_checked_at[cache_key] = 1000.0 - (DEFAULT_NEGATIVE_CACHE_TTL_SECONDS + 1)
    times = iter([1000.0, 1001.0])
    monkeypatch.setattr("aiopnsense.client_endpoint.monotonic", lambda: next(times))
    client._get_optional = AsyncMock(return_value=("missing", {}))
    client._is_core_firmware_endpoint_healthy = AsyncMock(return_value=True)

    try:
        assert await client._check_optional_get_endpoint(path) == ("missing", {})
        assert client._endpoint_availability[cache_key] is False
        assert client._endpoint_checked_at[cache_key] == 1001.0
        assert cache_key not in client._optional_endpoint_missing_pending_confirmation
        client._get_optional.assert_awaited_once_with(path)
        client._is_core_firmware_endpoint_healthy.assert_awaited_once_with()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_check_optional_get_endpoint_force_refresh_allows_recovery_before_confirmation(
    make_client: MakeClientFactory,
) -> None:
    """Force refresh should prioritize live optional probe over pending confirmation."""
    client, _session = make_mock_session_client(make_client)
    path = "/api/speedtest/service/showstat"
    cache_key = ("get", path)
    client._get_optional = AsyncMock(return_value=("available", {"samples": 1}))
    client._endpoint_availability[cache_key] = False
    client._endpoint_checked_at[cache_key] = 100.0
    client._optional_endpoint_missing_pending_confirmation.add(cache_key)

    try:
        state, payload = await client._check_optional_get_endpoint(path, force_refresh=True)

        assert state == "available"
        assert payload == {"samples": 1}
        assert cache_key in client._endpoint_availability
        assert client._endpoint_availability[cache_key] is True
        assert cache_key not in client._optional_endpoint_missing_pending_confirmation
        assert client._get_optional.await_count == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "core_healthy", "expected_state", "expected_cached"),
    [
        ("missing", True, "missing", False),
        ("missing", False, "unavailable", None),
        ("unavailable", True, "unavailable", None),
    ],
)
async def test_check_optional_get_endpoint_force_refresh_preserves_confirmation_contract(
    state: str,
    core_healthy: bool,
    expected_state: str,
    expected_cached: bool | None,
    make_client: MakeClientFactory,
) -> None:
    """Force refresh bypasses cache freshness without bypassing confirmation."""
    client, _session = make_mock_session_client(make_client)
    path = "/api/speedtest/service/showrecent"
    cache_key = ("get", path)
    client._optional_endpoint_missing_pending_confirmation.add(cache_key)
    client._get_optional = AsyncMock(return_value=(state, {}))
    client._is_core_firmware_endpoint_healthy = AsyncMock(return_value=core_healthy)

    try:
        result_state, payload = await client._check_optional_get_endpoint(path, force_refresh=True)

        assert result_state == expected_state
        assert payload == {}
        if expected_cached is None:
            assert cache_key not in client._endpoint_availability
            assert cache_key in client._optional_endpoint_missing_pending_confirmation
        else:
            assert client._endpoint_availability[cache_key] is expected_cached
            assert cache_key not in client._optional_endpoint_missing_pending_confirmation
        if state == "missing":
            client._is_core_firmware_endpoint_healthy.assert_awaited_once_with()
        else:
            client._is_core_firmware_endpoint_healthy.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_check_optional_get_endpoint_pending_confirmation_does_not_cache_when_core_unavailable(
    make_client: MakeClientFactory,
) -> None:
    """Core confirmation failure keeps optional cache uncommitted and pending."""
    client, _session = make_mock_session_client(make_client)
    path = "/api/nut/diagnostics/upsstatus"
    cache_key = ("get", path)
    client._get_optional = AsyncMock(return_value=("missing", {}))
    client._is_core_firmware_endpoint_healthy = AsyncMock(return_value=False)

    try:
        first_state, first_payload = await client._check_optional_get_endpoint(path)
        assert first_state == "unavailable"
        assert cache_key in client._optional_endpoint_missing_pending_confirmation

        second_state, second_payload = await client._check_optional_get_endpoint(path)
        assert second_state == "unavailable"
        assert second_payload == {}
        assert cache_key in client._optional_endpoint_missing_pending_confirmation
        assert cache_key not in client._endpoint_availability
        assert cache_key not in client._endpoint_checked_at
        assert first_payload == {}
        assert second_payload == {}
        assert client._get_optional.await_count == 2
        assert client._is_core_firmware_endpoint_healthy.await_count == 2
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_check_optional_get_endpoint_recovers_before_core_confirmation(
    make_client: MakeClientFactory,
) -> None:
    """A recovered optional route wins before a pending miss is confirmed."""
    client, _session = make_mock_session_client(make_client)
    path = "/api/speedtest/service/showrecent"
    cache_key = ("get", path)
    client._get_optional = AsyncMock(
        side_effect=[("missing", {}), ("available", {"recovered": True})]
    )
    client._is_core_firmware_endpoint_healthy = AsyncMock(return_value=True)

    try:
        assert await client._check_optional_get_endpoint(path) == ("missing", {})
        assert await client._check_optional_get_endpoint(path) == (
            "available",
            {"recovered": True},
        )
        assert client._endpoint_availability[cache_key] is True
        assert cache_key not in client._optional_endpoint_missing_pending_confirmation
        client._is_core_firmware_endpoint_healthy.assert_awaited_once_with()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_optional_endpoint_core_health_uses_fresh_raw_firmware_request(
    make_client: MakeClientFactory,
) -> None:
    """Core confirmation bypasses endpoint cache and the request queue."""
    client, session = make_mock_session_client(make_client)
    requested_urls: list[str] = []

    def get(url: str, **_kwargs: Any) -> FakeResponse:
        """Capture and satisfy the direct firmware health request."""
        requested_urls.append(url)
        return FakeResponse(status=200, ok=True)

    session.get = get
    client._endpoint_availability[("get", "/api/core/firmware/status")] = False
    client._endpoint_checked_at[("get", "/api/core/firmware/status")] = monotonic()
    query_count = client._rest_api_query_count

    try:
        assert await client._is_core_firmware_endpoint_healthy() is True
        assert requested_urls == [f"{client._url}/api/core/firmware/status"]
        assert client._rest_api_query_count == query_count + 1
        assert client._endpoint_availability[("get", "/api/core/firmware/status")] is False
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_optional_endpoint_calls_are_serialized_per_cache_key(
    make_client: MakeClientFactory,
) -> None:
    """Concurrent calls for one optional route never overlap requests."""
    client, _session = make_mock_session_client(make_client)
    path = "/api/nut/diagnostics/upsstatus"
    active = 0
    maximum_active = 0

    async def optional_get(_path: str) -> tuple[str, object]:
        """Track concurrent entry into the optional transport boundary."""
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0)
        active -= 1
        return "available", {"status": {"ups.status": "OL"}}

    client._get_optional = AsyncMock(side_effect=optional_get)
    try:
        results = await asyncio.gather(
            client._check_optional_get_endpoint(path),
            client._check_optional_get_endpoint(path),
        )
        assert maximum_active == 1
        assert all(state == "available" for state, _payload in results)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_check_optional_get_endpoint_cached_positive_not_mutated_by_transient_states(
    make_client: MakeClientFactory,
) -> None:
    """Verify transient optional states do not erase prior positive cache."""
    client, _session = make_mock_session_client(make_client)
    path = "/api/speedtest/service/showrecent"
    cache_key = ("get", path)
    client._endpoint_availability[cache_key] = True
    client._endpoint_checked_at[cache_key] = monotonic()

    client._get_optional = AsyncMock(return_value=("unavailable", {}))

    try:
        state, payload = await client._check_optional_get_endpoint(path)

        assert state == "unavailable"
        assert payload == {}
        assert client._endpoint_availability[cache_key] is True
        assert cache_key in client._endpoint_checked_at
        assert cache_key not in client._optional_endpoint_missing_pending_confirmation
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["available", "malformed"])
async def test_check_optional_post_endpoint_refreshes_positive_exact_cache_key(
    state: str,
    make_client: MakeClientFactory,
) -> None:
    """Read-only SMART POST success refreshes its explicit probe cache key."""
    client, _session = make_mock_session_client(make_client)
    client._post_optional = AsyncMock(return_value=(state, {"devices": []}))
    cache_key = ("post", "/api/smart/service/list")
    try:
        result_state, payload = await client._check_optional_post_endpoint(
            "/api/smart/service/list/1",
            cache_path="/api/smart/service/list",
        )
        assert result_state == state
        assert payload == {"devices": []}
        assert client._endpoint_availability[cache_key] is True
        assert ("post", "/api/smart/service/list/1") not in client._endpoint_availability
        client._post_optional.assert_awaited_once_with("/api/smart/service/list/1", None)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_check_optional_post_endpoint_confirms_second_404_with_core_health(
    make_client: MakeClientFactory,
) -> None:
    """A second read-only POST 404 plus healthy core stores a short negative."""
    client, _session = make_mock_session_client(make_client)
    cache_key = ("post", "/api/smart/service/info")
    client._post_optional = AsyncMock(return_value=("missing", {}))
    client._is_core_firmware_endpoint_healthy = AsyncMock(return_value=True)
    try:
        assert await client._check_optional_post_endpoint(
            "/api/smart/service/info", payload={"device": "ada0"}
        ) == ("missing", {})
        assert cache_key in client._optional_endpoint_missing_pending_confirmation

        assert await client._check_optional_post_endpoint(
            "/api/smart/service/info", payload={"device": "ada0"}
        ) == ("missing", {})
        assert client._endpoint_availability[cache_key] is False
        assert cache_key not in client._optional_endpoint_missing_pending_confirmation
        assert client._post_optional.await_count == 2
        assert client._is_core_firmware_endpoint_healthy.await_count == 2
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_check_optional_post_endpoint_stale_negative_renews_with_one_probe(
    monkeypatch: pytest.MonkeyPatch, make_client: MakeClientFactory
) -> None:
    """An expired SMART absence is renewed with one read-only POST probe."""
    client, _session = make_mock_session_client(make_client)
    path = "/api/smart/service/info"
    cache_key = ("post", path)
    client._endpoint_availability[cache_key] = False
    client._endpoint_checked_at[cache_key] = 1000.0 - (DEFAULT_NEGATIVE_CACHE_TTL_SECONDS + 1)
    times = iter([1000.0, 1001.0])
    monkeypatch.setattr("aiopnsense.client_endpoint.monotonic", lambda: next(times))
    client._post_optional = AsyncMock(return_value=("missing", {}))
    client._is_core_firmware_endpoint_healthy = AsyncMock(return_value=True)
    payload = {"device": "ada0"}

    try:
        assert await client._check_optional_post_endpoint(path, payload=payload) == (
            "missing",
            {},
        )
        assert client._endpoint_availability[cache_key] is False
        assert client._endpoint_checked_at[cache_key] == 1001.0
        assert cache_key not in client._optional_endpoint_missing_pending_confirmation
        client._post_optional.assert_awaited_once_with(path, payload)
        client._is_core_firmware_endpoint_healthy.assert_awaited_once_with()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_check_optional_post_endpoint_transient_failure_preserves_positive(
    make_client: MakeClientFactory,
) -> None:
    """A non-404 SMART failure must not mutate the positive observation."""
    client, _session = make_mock_session_client(make_client)
    cache_key = ("post", "/api/smart/service/info")
    client._endpoint_availability[cache_key] = True
    checked_at = monotonic()
    client._endpoint_checked_at[cache_key] = checked_at
    client._post_optional = AsyncMock(return_value=("unavailable", {}))
    try:
        assert await client._check_optional_post_endpoint("/api/smart/service/info") == (
            "unavailable",
            {},
        )
        assert client._endpoint_availability[cache_key] is True
        assert client._endpoint_checked_at[cache_key] == checked_at
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_check_optional_post_endpoint_rejects_unregistered_mapping(
    make_client: MakeClientFactory,
) -> None:
    """Derived request paths cannot update unrelated endpoint cache keys."""
    client, _session = make_mock_session_client(make_client)
    client._post_optional = AsyncMock()
    try:
        assert await client._check_optional_post_endpoint(
            "/api/smart/service/list/1",
            cache_path="/api/smart/service/info",
        ) == ("unavailable", {})
        client._post_optional.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    "opts",
    [
        {"endpoint_positive_cache_ttl_seconds": 0},
        {"endpoint_positive_cache_ttl_seconds": True},
        {"endpoint_negative_cache_ttl_seconds": -1},
        {"endpoint_negative_cache_ttl_seconds": False},
    ],
)
def test_endpoint_cache_ttls_require_positive_integers(
    make_client: MakeClientFactory,
    opts: dict[str, object],
) -> None:
    """Endpoint TTL overrides reject booleans and non-positive integers."""
    with pytest.raises(OPNsenseInvalidArgument, match="must be a positive integer"):
        make_client(opts=opts)


def test_endpoint_cache_ttls_are_independently_configurable(
    make_client: MakeClientFactory,
) -> None:
    """Positive and confirmed-negative cache windows accept separate overrides."""
    client = make_client(
        opts={
            "endpoint_positive_cache_ttl_seconds": 600,
            "endpoint_negative_cache_ttl_seconds": 30,
        }
    )
    assert client._endpoint_cache_ttl_seconds == 600
    assert client._endpoint_negative_cache_ttl_seconds == 30


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
            *args (object): Positional arguments forwarded to the stub.
            **kwargs (object): Keyword arguments forwarded to the stub.

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
        assert ("post", path) in client._endpoint_checked_at
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
        cache_key = ("get", path)
        with pytest.raises(expected_exception):
            await client.validate()
        assert calls == 1
        assert client._throw_errors is False
        assert cache_key not in client._endpoint_checked_at
        assert cache_key not in client._endpoint_availability
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


@pytest.mark.asyncio
async def test_set_use_snake_case_initial_raises_unknown_firmware(
    make_client: MakeClientFactory,
) -> None:
    """Verify legacy initial setup still raises for invalid firmware versions.

    Args:
        make_client (MakeClientFactory): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts the preserved compatibility path.
    """
    client = make_client()
    try:
        client.get_host_firmware_version = AsyncMock(return_value="invalid-version")

        with pytest.raises(OPNsenseUnknownFirmware):
            await client._set_use_snake_case(initial=True)

        client.get_host_firmware_version.assert_awaited_once_with()
        assert client._use_snake_case is True
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_set_use_snake_case_initial_raises_unknown_firmware_when_version_missing(
    make_client: MakeClientFactory,
) -> None:
    """Verify missing firmware version still raises unknown-firmware in legacy init mode."""

    client = make_client()
    try:
        client.get_host_firmware_version = AsyncMock(return_value=None)

        with pytest.raises(OPNsenseUnknownFirmware):
            await client._set_use_snake_case(initial=True)

        client.get_host_firmware_version.assert_awaited_once_with()
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
