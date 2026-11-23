"""Tests for `aiopnsense.vouchers`."""

from collections.abc import Callable
from functools import partial
from typing import Any

import pytest

import aiopnsense as aiopnsense_module
from aiopnsense import OPNsenseClient
from aiopnsense.exceptions import OPNsenseConnectionError
from tests.conftest import (
    FIRMWARE_STATUS_PATH,
    FakeRouter,
    install_fake_router,
    make_mock_session_client,
)

ClientType = Callable[..., OPNsenseClient]

ROUTER_URL = "http://localhost"
PROVIDERS_PATH = "/api/captiveportal/voucher/list_providers"
GENERATE_PATH = "/api/captiveportal/voucher/generate_vouchers"
CAMELCASE_PROVIDERS_PATH = "/api/captiveportal/voucher/listProviders"
CAMELCASE_GENERATE_PATH = "/api/captiveportal/voucher/generateVouchers"
SNAKE_CASE_FIRMWARE = "26.7"
CAMELCASE_FIRMWARE = "25.1"


def _voucher_client(
    make_client: ClientType,
    routes: dict[tuple[str, str], Any],
    *,
    firmware: str = SNAKE_CASE_FIRMWARE,
    throw_errors: bool = False,
) -> tuple[OPNsenseClient, FakeRouter]:
    """Build a client whose Router reports a firmware version and answers only the given routes.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        routes (dict[tuple[str, str], Any]): Router answers keyed by HTTP method and path.
        firmware (str): Firmware version the Router reports, which selects the endpoint style.
        throw_errors (bool): Public ``throw_errors`` setting of the client.

    Returns:
        tuple[OPNsenseClient, FakeRouter]: Client and the Router that records its requests.
    """
    client, session = make_mock_session_client(partial(make_client, throw_errors=throw_errors))
    router = install_fake_router(
        client,
        session,
        routes={
            ("GET", FIRMWARE_STATUS_PATH): {"product": {"product_version": firmware}},
            **routes,
        },
    )
    return client, router


def _bodies(router: FakeRouter, method: str, path: str) -> list[Any]:
    """Return the bodies of the requests the Router received for one method and path.

    Args:
        router (FakeRouter): Router that recorded the requests.
        method (str): HTTP method to match.
        path (str): Request path to match.

    Returns:
        list[Any]: Request bodies in the order received.
    """
    return [
        body
        for request_method, url, body in router.requests
        if request_method == method and url == ROUTER_URL + path
    ]


@pytest.mark.parametrize(
    "servers",
    [pytest.param([], id="no-servers"), pytest.param(["s1", "s2"], id="several-servers")],
)
async def test_generate_vouchers_requires_exactly_one_server_when_none_is_named(
    make_client: ClientType, servers: list[str]
) -> None:
    """Without a named server, an empty or ambiguous provider list is an error and sends no POST.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        servers (list[str]): Voucher servers the Router lists.
    """
    client, router = _voucher_client(make_client, {("GET", PROVIDERS_PATH): servers})

    with pytest.raises(aiopnsense_module.OPNsenseVoucherServerError):
        await client.generate_vouchers({})

    assert router.count("GET", PROVIDERS_PATH) == 1
    assert router.count("POST") == 0


@pytest.mark.parametrize("throw_errors", [False, True], ids=["errors-off", "errors-on"])
async def test_generate_vouchers_returns_empty_when_provider_endpoint_is_missing(
    make_client: ClientType, throw_errors: bool
) -> None:
    """A 404 provider listing returns no vouchers in either error mode and sends no POST.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Public ``throw_errors`` setting of the client.
    """
    client, router = _voucher_client(
        make_client, {("GET", PROVIDERS_PATH): 404}, throw_errors=throw_errors
    )

    assert await client.generate_vouchers({}) == []

    assert router.count("GET", PROVIDERS_PATH) == 1
    assert router.count("POST") == 0


@pytest.mark.parametrize(
    ("throw_errors", "expected_error"),
    [
        pytest.param(False, aiopnsense_module.OPNsenseVoucherServerError, id="errors-off"),
        pytest.param(True, OPNsenseConnectionError, id="errors-on"),
    ],
)
async def test_generate_vouchers_reports_transient_provider_failure(
    make_client: ClientType,
    throw_errors: bool,
    expected_error: type[Exception],
) -> None:
    """A transient provider failure (HTTP 503) fails voucher generation before any POST is sent.

    With errors off the failure is reported as a voucher server error caused by the connection
    error. With errors on the connection error itself is raised.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Public ``throw_errors`` setting of the client.
        expected_error (type[Exception]): Exception the client raises in that mode.
    """
    client, router = _voucher_client(
        make_client, {("GET", PROVIDERS_PATH): 503}, throw_errors=throw_errors
    )

    with pytest.raises(expected_error) as caught:
        await client.generate_vouchers({})

    if not throw_errors:
        assert isinstance(caught.value.__cause__, OPNsenseConnectionError)
    assert router.count("POST") == 0


async def test_generate_vouchers_auto_selects_single_provider(make_client: ClientType) -> None:
    """A single listed provider is used when none is named, and vouchers are normalized.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    generate_path = f"{GENERATE_PATH}/srv%20one/"
    client, router = _voucher_client(
        make_client,
        {
            ("GET", PROVIDERS_PATH): ["srv one"],
            ("POST", generate_path): [{"username": "u", "validity": 60, "expirytime": 1700000000}],
        },
    )

    vouchers = await client.generate_vouchers({})

    assert vouchers[0]["username"] == "u"
    assert vouchers[0]["validity_str"] == "1 minute"
    assert vouchers[0]["expiry_timestamp"] == 1700000000
    assert vouchers[0]["expirytime"].timestamp() == 1700000000
    assert router.count("GET", PROVIDERS_PATH) == 1
    assert _bodies(router, "POST", generate_path) == [{}]


async def test_generate_vouchers_posts_to_named_provider_without_listing_providers(
    make_client: ClientType,
) -> None:
    """A named server skips the provider listing and receives the POST, without the server name.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    generate_path = f"{GENERATE_PATH}/srv%20one/"
    client, router = _voucher_client(
        make_client,
        {
            ("GET", PROVIDERS_PATH): ["other"],
            (
                "POST",
                generate_path,
            ): [
                {
                    "username": "u",
                    "password": "p",
                    "vouchergroup": "g",
                    "starttime": "t",
                    "expirytime": 1700000000,
                    "validity": 65,
                }
            ],
        },
    )

    vouchers = await client.generate_vouchers({"voucher_server": "srv one"})

    assert vouchers[0]["username"] == "u"
    assert "expiry_timestamp" in vouchers[0]
    assert "validity_str" in vouchers[0]
    assert router.count("GET", PROVIDERS_PATH) == 0
    assert _bodies(router, "POST", generate_path) == [{}]


async def test_generate_vouchers_returns_empty_on_404_generate_endpoint_with_throw_errors(
    make_client: ClientType,
) -> None:
    """A missing voucher-generation endpoint returns no vouchers even with errors on.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    generate_path = f"{GENERATE_PATH}/srv/"
    client, router = _voucher_client(
        make_client,
        {("GET", PROVIDERS_PATH): [], ("POST", generate_path): 404},
        throw_errors=True,
    )

    assert await client.generate_vouchers({"voucher_server": "srv"}) == []

    assert router.count("GET", PROVIDERS_PATH) == 0
    assert _bodies(router, "POST", generate_path) == [{}]


@pytest.mark.parametrize(
    ("firmware", "providers_path", "generate_path"),
    [
        pytest.param(SNAKE_CASE_FIRMWARE, PROVIDERS_PATH, f"{GENERATE_PATH}/srv/", id="snake-case"),
        pytest.param(
            CAMELCASE_FIRMWARE,
            CAMELCASE_PROVIDERS_PATH,
            f"{CAMELCASE_GENERATE_PATH}/srv/",
            id="camel-case",
        ),
    ],
)
async def test_voucher_switched_endpoints_follow_selected_case(
    make_client: ClientType, firmware: str, providers_path: str, generate_path: str
) -> None:
    """Voucher requests use the endpoint style of the firmware the Router reports.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        firmware (str): Firmware version the Router reports.
        providers_path (str): Expected voucher-provider endpoint path.
        generate_path (str): Expected voucher-generation endpoint path.
    """
    client, router = _voucher_client(
        make_client,
        {("GET", providers_path): ["srv"], ("POST", generate_path): []},
        firmware=firmware,
    )

    assert await client.generate_vouchers({}) == []

    assert router.count("GET", providers_path) == 1
    assert _bodies(router, "POST", generate_path) == [{}]
