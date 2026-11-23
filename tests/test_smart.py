"""Tests for `aiopnsense.smart`."""

from collections.abc import Callable

import pytest

from aiopnsense import OPNsenseClient
from tests.conftest import make_router_client

ClientType = Callable[..., OPNsenseClient]

SMART_LIST_PATH = "/api/smart/service/list/1"
SMART_INFO_PATH = "/api/smart/service/info"
NVME_ROW = {"ident": "nvme0", "device": "nvme0", "status": "PASSED"}


@pytest.mark.parametrize(
    ("devices", "expected"),
    [
        pytest.param(
            [NVME_ROW, {"ident": "ada0", "device": "ada0", "status": "FAILED"}],
            [NVME_ROW, {"ident": "ada0", "device": "ada0", "status": "FAILED"}],
            id="rows-unchanged",
        ),
        pytest.param(
            [{"ident": "", "device": "nvme0", "status": "PASSED"}, NVME_ROW],
            [NVME_ROW],
            id="blank-ident-skipped",
        ),
        pytest.param(["unexpected-string-row", NVME_ROW], [NVME_ROW], id="non-mapping-skipped"),
        pytest.param("ignored", [], id="non-list-payload"),
    ],
)
async def test_get_smart_returns_device_rows(
    make_client: ClientType, devices: object, expected: list[dict[str, str]]
) -> None:
    """SMART list queries read the POST-only list and keep only valid device rows.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        devices (object): ``devices`` value the Router returns.
        expected (list[dict[str, str]]): Device rows the client returns.
    """
    client, router = make_router_client(
        make_client, {("POST", SMART_LIST_PATH): {"devices": devices}}
    )

    assert await client.get_smart() == expected

    # One POST with no body and no other request, so the list is read by POST only.
    assert router.requests == [("POST", SMART_LIST_PATH, None)]


@pytest.mark.parametrize("throw_errors", [False, True], ids=["errors-off", "errors-on"])
@pytest.mark.parametrize(
    ("operation", "path", "expected"),
    [
        pytest.param("list", SMART_LIST_PATH, [], id="list"),
        pytest.param("info", SMART_INFO_PATH, {}, id="info"),
    ],
)
async def test_smart_fails_closed_when_endpoint_is_unavailable(
    make_client: ClientType,
    throw_errors: bool,
    operation: str,
    path: str,
    expected: object,
) -> None:
    """SMART POST reads fail closed when the Router answers 404, in either error mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Public ``throw_errors`` setting of the client.
        operation (str): SMART operation under test.
        path (str): Path of the POST request the operation sends.
        expected (object): Expected fail-closed result.
    """
    client, router = make_router_client(
        make_client, {("POST", path): 404}, throw_errors=throw_errors
    )

    if operation == "list":
        got = await client.get_smart()
    else:
        got = await client.get_smart_info("nvme0")

    assert got == expected
    assert router.count("POST", path) == 1


async def test_get_smart_info_returns_json_output(make_client: ClientType) -> None:
    """SMART info queries request JSON output and return the decoded payload.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = make_router_client(
        make_client,
        {("POST", SMART_INFO_PATH): {"output": {"smart_status": "PASSED", "temperature": 35}}},
    )

    assert await client.get_smart_info("nvme0") == {"smart_status": "PASSED", "temperature": 35}

    assert router.requests == [
        (
            "POST",
            SMART_INFO_PATH,
            {"device": "nvme0", "type": "a", "json": True},
        )
    ]


async def test_get_smart_info_wraps_non_mapping_output(make_client: ClientType) -> None:
    """SMART info queries preserve non-mapping outputs in a stable wrapper.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = make_router_client(
        make_client, {("POST", SMART_INFO_PATH): {"output": ["line1", "line2"]}}
    )

    assert await client.get_smart_info("nvme0", info_type="H") == {"output": ["line1", "line2"]}

    assert router.requests == [
        (
            "POST",
            SMART_INFO_PATH,
            {"device": "nvme0", "type": "H", "json": True},
        )
    ]


async def test_smart_post_endpoint_availability_caches_missing_plugin(
    make_client: ClientType,
) -> None:
    """A 404 for the SMART list is cached, so polling again sends no request.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = make_router_client(make_client, {("POST", SMART_LIST_PATH): 404})

    assert await client.get_smart() == []
    assert await client.get_smart() == []

    assert router.count("POST", SMART_LIST_PATH) == 1
