"""Tests for `aiopnsense.vouchers`."""

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest

from aiopnsense import (
    OPNsenseClient,
    OPNsenseConnectionError,
    OPNsensePrivilegeMissing,
    OPNsenseVoucherServerError,
)
from tests.conftest import FakeRouter, install_fake_router, make_mock_session_client

ClientType = Callable[..., OPNsenseClient]
PROVIDERS_PATH = "/api/captiveportal/voucher/list_providers"
PROVIDERS_CAMEL_PATH = "/api/captiveportal/voucher/listProviders"


def _make_voucher_client(
    make_client: ClientType,
    providers: Any,
    *,
    path: str = PROVIDERS_PATH,
    use_snake_case: bool = True,
) -> tuple[OPNsenseClient, FakeRouter]:
    """Build a client whose Router answers the provider listing with ``providers``.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        providers (Any): Response the Router gives for the provider listing endpoint.
        path (str): Provider listing path the Router answers.
        use_snake_case (bool): Whether the client prefers snake_case endpoints.

    Returns:
        tuple[OPNsenseClient, FakeRouter]: Client and the fake Router backing it.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", path): providers})
    client._use_snake_case = use_snake_case
    return client, router


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "providers,data,expect_exc",
    [
        pytest.param([], {}, OPNsenseVoucherServerError, id="no-providers"),
        pytest.param(["s1", "s2"], {}, OPNsenseVoucherServerError, id="multiple-providers"),
    ],
)
async def test_generate_vouchers_server_selection_errors(
    make_client: ClientType,
    providers: list[str],
    data: dict[str, Any],
    expect_exc: type[Exception],
) -> None:
    """Provider-selection failures raise and never reach the generation POST.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        providers (list[str]): Provider list the Router returns.
        data (dict[str, Any]): Input arguments passed to ``generate_vouchers``.
        expect_exc (type[Exception]): Expected exception type.

    Returns:
        None: This test validates provider-selection error paths via assertions.
    """
    client, _router = _make_voucher_client(make_client, providers)
    try:
        client._safe_list_post = AsyncMock()
        with pytest.raises(expect_exc):
            await client.generate_vouchers(data)
        client._safe_list_post.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_generate_vouchers_with_explicit_server_normalizes_vouchers(
    make_client: ClientType,
) -> None:
    """An explicit voucher server skips the provider lookup and normalizes the result.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates successful voucher generation output.
    """
    client, router = _make_voucher_client(make_client, ["unused"])
    try:
        client._safe_list_post = AsyncMock(
            return_value=[
                {
                    "username": "u",
                    "password": "p",
                    "vouchergroup": "g",
                    "starttime": "t",
                    "expirytime": 1700000000,
                    "validity": 65,
                }
            ]
        )
        got = await client.generate_vouchers({"voucher_server": "srv"})

        assert got[0]["username"] == "u"
        assert "expiry_timestamp" in got[0]
        assert "validity_str" in got[0]
        assert router.count("GET", PROVIDERS_PATH) == 0
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route_value",
    [
        pytest.param(404, id="missing-endpoint"),
        pytest.param(403, id="permission-denied"),
        pytest.param(500, id="server-error"),
        pytest.param(TimeoutError("timed out"), id="timeout"),
    ],
)
async def test_generate_vouchers_returns_empty_when_provider_lookup_unavailable(
    make_client: ClientType, route_value: Any
) -> None:
    """An absent or failing provider listing yields ``[]`` after one request and no POST.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        route_value (Any): Response the Router gives for the provider listing endpoint.

    Returns:
        None: This test asserts voucher generation returns an empty list when unavailable.
    """
    client, router = _make_voucher_client(make_client, route_value)
    try:
        client._safe_list_post = AsyncMock()

        assert await client.generate_vouchers({}) == []
        assert router.count("GET", PROVIDERS_PATH) == 1
        client._safe_list_post.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route_value, expected_error",
    [
        pytest.param(403, OPNsensePrivilegeMissing, id="permission-denied"),
        pytest.param(500, OPNsenseConnectionError, id="server-error"),
        pytest.param(TimeoutError("timed out"), OPNsenseConnectionError, id="timeout"),
    ],
)
async def test_generate_vouchers_provider_lookup_raises_in_throw_mode(
    make_client: ClientType, route_value: Any, expected_error: type[Exception]
) -> None:
    """The provider lookup raises a public error for permission and transient failures.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        route_value (Any): Response the Router gives for the provider listing endpoint.
        expected_error (type[Exception]): Public exception type expected from the call.

    Returns:
        None: This test validates throw-mode errors from the provider lookup.
    """
    client, _router = _make_voucher_client(make_client, route_value)
    client._throw_errors = True
    try:
        client._safe_list_post = AsyncMock()
        with pytest.raises(expected_error):
            await client.generate_vouchers({})
        client._safe_list_post.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_generate_vouchers_missing_provider_endpoint_does_not_raise_in_throw_mode(
    make_client: ClientType,
) -> None:
    """A missing provider listing (404) returns ``[]`` rather than raising in throw mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates 404 is not an error in throw mode.
    """
    client, _router = _make_voucher_client(make_client, 404)
    client._throw_errors = True
    try:
        assert await client.generate_vouchers({}) == []
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_generate_vouchers_auto_selects_single_provider(make_client: ClientType) -> None:
    """`generate_vouchers` should auto-select a single provider when none is specified.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates automatic single-provider selection and voucher normalization.
    """
    client, router = _make_voucher_client(make_client, ["srv one"])
    try:
        client._safe_list_post = AsyncMock(
            return_value=[{"username": "u", "validity": 60, "expirytime": 1700000000}]
        )

        vouchers = await client.generate_vouchers({})

        assert vouchers[0]["username"] == "u"
        assert vouchers[0]["validity_str"] == "1 minute"
        assert vouchers[0]["expiry_timestamp"] == 1700000000
        assert vouchers[0]["expirytime"].timestamp() == 1700000000
        assert router.count("GET", PROVIDERS_PATH) == 1
        client._safe_list_post.assert_awaited_once_with(
            "/api/captiveportal/voucher/generate_vouchers/srv%20one/",
            payload={},
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_generate_vouchers_posts_provider_qualified_url_without_provider_lookup(
    make_client: ClientType,
) -> None:
    """`generate_vouchers` posts to the given server's URL without listing providers.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates the POST URL and the absence of a provider lookup.
    """
    client, router = _make_voucher_client(make_client, ["other"])
    try:
        client._safe_list_post = AsyncMock(return_value=[])

        assert await client.generate_vouchers({"voucher_server": "srv one"}) == []
        assert router.count("GET") == 0
        client._safe_list_post.assert_awaited_once_with(
            "/api/captiveportal/voucher/generate_vouchers/srv%20one/",
            payload={},
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_generate_vouchers_returns_empty_on_404_generate_endpoint_with_throw_errors(
    make_client: ClientType,
) -> None:
    """Missing voucher-generation POST endpoints fail closed with ``[]`` under throw mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test verifies 404 errors at generation time are treated as empty results.
    """
    client, router = _make_voucher_client(make_client, [])
    try:
        client._throw_errors = True
        client._safe_list_post = AsyncMock(
            side_effect=OPNsenseConnectionError("not found", status=404)
        )

        got = await client.generate_vouchers({"voucher_server": "srv"})

        assert got == []
        assert router.count("GET") == 0
        client._safe_list_post.assert_awaited_once_with(
            "/api/captiveportal/voucher/generate_vouchers/srv/",
            payload={},
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("use_snake_case", "expected_provider", "expected_generate"),
    [
        (
            True,
            PROVIDERS_PATH,
            "/api/captiveportal/voucher/generate_vouchers/srv/",
        ),
        (
            False,
            PROVIDERS_CAMEL_PATH,
            "/api/captiveportal/voucher/generateVouchers/srv/",
        ),
    ],
)
async def test_voucher_switched_endpoints_follow_selected_case(
    make_client: ClientType,
    use_snake_case: bool,
    expected_provider: str,
    expected_generate: str,
) -> None:
    """Verify voucher helpers choose snake_case or camelCase endpoints consistently.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        use_snake_case (bool): Whether the client should prefer snake_case endpoints.
        expected_provider (str): Expected voucher-provider endpoint path.
        expected_generate (str): Expected voucher-generation endpoint path.

    Returns:
        None: This test validates voucher endpoint selection behavior.
    """
    client, router = _make_voucher_client(
        make_client, ["srv"], path=expected_provider, use_snake_case=use_snake_case
    )
    try:
        client._safe_list_post = AsyncMock(return_value=[])

        await client.generate_vouchers({})

        assert router.count("GET", expected_provider) == 1
        client._safe_list_post.assert_awaited_once_with(expected_generate, payload={})
    finally:
        await client.async_close()
