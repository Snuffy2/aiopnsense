"""Tests for `aiopnsense.vouchers`."""

from collections.abc import Callable
from functools import partial

import pytest

import aiopnsense as aiopnsense_module
from aiopnsense import OPNsenseClient
from aiopnsense.exceptions import OPNsenseConnectionError
from tests.conftest import SequencedResponse, firmware_status_payload, make_router_client

ClientType = Callable[..., OPNsenseClient]

PROVIDERS_PATH = "/api/captiveportal/voucher/list_providers"
GENERATE_PATH = "/api/captiveportal/voucher/generate_vouchers"
CAMELCASE_PROVIDERS_PATH = "/api/captiveportal/voucher/listProviders"
CAMELCASE_GENERATE_PATH = "/api/captiveportal/voucher/generateVouchers"
SNAKE_CASE_FIRMWARE = "26.7"
CAMELCASE_FIRMWARE = "25.1"
_voucher_client = partial(make_router_client, firmware=SNAKE_CASE_FIRMWARE)


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


async def test_generate_vouchers_returns_empty_when_provider_endpoint_is_missing(
    make_client: ClientType,
) -> None:
    """A 404 provider listing returns no vouchers and sends no POST.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _voucher_client(make_client, {("GET", PROVIDERS_PATH): 404})

    assert await client.generate_vouchers({}) == []

    assert router.count("GET", PROVIDERS_PATH) == 1
    assert router.count("POST") == 0


async def test_generate_vouchers_reports_transient_provider_failure(
    make_client: ClientType,
) -> None:
    """A transient provider failure (HTTP 503) fails voucher generation before any POST is sent.

    The failure is reported as a voucher server error caused by the connection error.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = _voucher_client(make_client, {("GET", PROVIDERS_PATH): 503})

    with pytest.raises(aiopnsense_module.OPNsenseVoucherServerError) as caught:
        await client.generate_vouchers({})

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
    assert router.bodies("POST", generate_path) == [{}]


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
    assert router.bodies("POST", generate_path) == [{}]


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
    assert router.bodies("POST", generate_path) == [{}]


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
    assert router.bodies("POST", generate_path) == [{}]


@pytest.mark.parametrize(
    ("firmware", "providers_path", "generate_path", "reads_with_server", "reads_without_server"),
    [
        pytest.param(
            SNAKE_CASE_FIRMWARE,
            "/api/captiveportal/voucher/list_providers",
            "/api/captiveportal/voucher/generate_vouchers/srv/",
            1,
            1,
            id="snake_case",
        ),
        pytest.param(
            CAMELCASE_FIRMWARE,
            "/api/captiveportal/voucher/listProviders",
            "/api/captiveportal/voucher/generateVouchers/srv/",
            1,
            1,
            id="camelCase",
        ),
        pytest.param(
            None,
            "/api/captiveportal/voucher/list_providers",
            "/api/captiveportal/voucher/generate_vouchers/srv/",
            # A supplied server needs no provider path, so only the generate path is resolved.
            1,
            2,
            id="unknown-firmware",
        ),
    ],
)
@pytest.mark.parametrize("server_supplied", [True, False], ids=["server-supplied", "no-server"])
async def test_generate_vouchers_request_sequence(
    make_client: ClientType,
    firmware: str | None,
    providers_path: str,
    generate_path: str,
    reads_with_server: int,
    reads_without_server: int,
    server_supplied: bool,
) -> None:
    """Pin every request ``generate_vouchers`` sends, with and without a supplied server.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        firmware (str | None): Firmware version the Router reports, ``None`` when it is unknown.
        providers_path (str): Provider list endpoint the Router is expected to receive.
        generate_path (str): Generate endpoint, server segment included.
        reads_with_server (int): Firmware status reads when the caller names the server.
        reads_without_server (int): Firmware status reads when the server comes from the list.
        server_supplied (bool): Whether the caller names the voucher server.
    """
    client, router = make_router_client(
        make_client,
        {("GET", providers_path): ["srv"], ("POST", generate_path): []},
        firmware=firmware,
    )

    assert (
        await client.generate_vouchers({"voucher_server": "srv"} if server_supplied else {}) == []
    )

    firmware_read = ("GET", "/api/core/firmware/status")
    if server_supplied:
        expected = [firmware_read] * reads_with_server + [("POST", generate_path)]
    else:
        expected = [firmware_read] * reads_without_server + [
            ("GET", providers_path),
            ("POST", generate_path),
        ]
    assert router.calls() == expected


async def test_generate_vouchers_when_endpoint_style_becomes_known(
    make_client: ClientType,
) -> None:
    """Keep the provider and generate paths in their original resolution order.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    firmware_read = ("GET", "/api/core/firmware/status")
    provider_read = ("GET", "/api/captiveportal/voucher/list_providers")
    generate = ("POST", "/api/captiveportal/voucher/generateVouchers/srv/")
    client, router = make_router_client(
        make_client,
        {
            firmware_read: SequencedResponse({}, firmware_status_payload(CAMELCASE_FIRMWARE)),
            provider_read: ["srv"],
            generate: [{"username": "voucher"}],
        },
    )

    assert await client.generate_vouchers({}) == [{"username": "voucher"}]
    assert router.calls() == [firmware_read, firmware_read, provider_read, generate]
