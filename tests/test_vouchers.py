"""Tests for `aiopnsense.vouchers`."""

from collections.abc import Callable
from typing import Any

import pytest

from aiopnsense import OPNsenseClient, OPNsenseVoucherServerError
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
    client, router = _make_voucher_client(make_client, providers)
    try:
        with pytest.raises(expect_exc):
            await client.generate_vouchers(data)
        assert router.count("POST") == 0
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_generate_vouchers_with_explicit_server_normalizes_vouchers(
    make_client: ClientType,
) -> None:
    """An explicit voucher server is URL-quoted, skips the provider lookup and normalizes the result.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates successful voucher generation output.
    """
    client, router = _make_voucher_client(make_client, ["unused"])
    try:
        generate_path = "/api/captiveportal/voucher/generate_vouchers/srv%20one/"
        router.routes[("POST", generate_path)] = [
            {
                "username": "u",
                "password": "p",
                "vouchergroup": "g",
                "starttime": "t",
                "expirytime": 1700000000,
                "validity": 65,
            }
        ]
        got = await client.generate_vouchers({"voucher_server": "srv one"})

        assert got[0]["username"] == "u"
        assert "expiry_timestamp" in got[0]
        assert "validity_str" in got[0]
        assert router.count("GET", PROVIDERS_PATH) == 0
        assert router.count("POST", generate_path) == 1
        assert [payload for method, _, payload in router.requests if method == "POST"] == [{}]
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
        assert await client.generate_vouchers({}) == []
        assert router.count("GET", PROVIDERS_PATH) == 1
        assert router.count("POST") == 0
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
        generate_path = "/api/captiveportal/voucher/generate_vouchers/srv%20one/"
        router.routes[("POST", generate_path)] = [
            {"username": "u", "validity": 60, "expirytime": 1700000000}
        ]

        vouchers = await client.generate_vouchers({})

        assert vouchers[0]["username"] == "u"
        assert vouchers[0]["validity_str"] == "1 minute"
        assert vouchers[0]["expiry_timestamp"] == 1700000000
        assert vouchers[0]["expirytime"].timestamp() == 1700000000
        assert router.count("GET", PROVIDERS_PATH) == 1
        assert router.count("POST", generate_path) == 1
        assert [payload for method, _, payload in router.requests if method == "POST"] == [{}]
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
        generate_path = "/api/captiveportal/voucher/generate_vouchers/srv/"
        router.routes[("POST", generate_path)] = 404

        got = await client.generate_vouchers({"voucher_server": "srv"})

        assert got == []
        assert router.count("GET") == 0
        assert router.count("POST", generate_path) == 1
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
        router.routes[("POST", expected_generate)] = []

        await client.generate_vouchers({})

        assert router.count("GET", expected_provider) == 1
        assert router.count("POST", expected_generate) == 1
    finally:
        await client.async_close()
