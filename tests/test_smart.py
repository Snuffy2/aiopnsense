"""Tests for `aiopnsense.smart`."""

from collections.abc import Callable

import pytest

from aiopnsense import OPNsenseClient
from aiopnsense.exceptions import (
    OPNsenseConnectionError,
    OPNsenseError,
    OPNsensePrivilegeMissing,
    OPNsenseTimeoutError,
)
from tests.conftest import MalformedJSONResponse, make_router_client

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
        pytest.param([], [], id="no-devices"),
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
    "payload",
    [
        pytest.param({"devices": None}, id="null-devices"),
        pytest.param({"devices": "unexpected"}, id="non-list-devices"),
        pytest.param({"message": "Unable to run list action"}, id="missing-devices"),
        pytest.param([], id="non-mapping-body"),
    ],
)
async def test_get_smart_reports_malformed_inventory(
    make_client: ClientType, throw_errors: bool, payload: dict[str, object] | list[object]
) -> None:
    """A malformed SMART inventory does not prevent the next read from recovering.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Public ``throw_errors`` setting of the client.
        payload (dict[str, object] | list[object]): Decoded response body from the Router.
    """
    client, router = make_router_client(
        make_client, {("POST", SMART_LIST_PATH): payload}, throw_errors=throw_errors
    )

    result = await client.get_smart()

    if throw_errors:
        assert result == []
    else:
        assert result is None

    router.routes[("POST", SMART_LIST_PATH)] = {"devices": [NVME_ROW]}
    assert await client.get_smart() == [NVME_ROW]
    assert router.requests == [("POST", SMART_LIST_PATH, None)] * 2


@pytest.mark.parametrize("throw_errors", [False, True], ids=["errors-off", "errors-on"])
@pytest.mark.parametrize(
    ("operation", "path", "expected"),
    [
        pytest.param("list", SMART_LIST_PATH, [], id="list"),
        pytest.param("info", SMART_INFO_PATH, {}, id="info"),
    ],
)
async def test_smart_returns_empty_when_endpoint_is_missing(
    make_client: ClientType,
    throw_errors: bool,
    operation: str,
    path: str,
    expected: object,
) -> None:
    """SMART POST reads report an empty result for 404 in either error mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Public ``throw_errors`` setting of the client.
        operation (str): SMART operation under test.
        path (str): Path of the POST request the operation sends.
        expected (object): Empty result for the missing endpoint.
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


@pytest.mark.parametrize("throw_errors", [False, True], ids=["errors-off", "errors-on"])
@pytest.mark.parametrize("path", [SMART_LIST_PATH, SMART_INFO_PATH], ids=["list", "info"])
@pytest.mark.parametrize(
    ("reply", "error"),
    [
        pytest.param(403, OPNsensePrivilegeMissing, id="forbidden"),
        pytest.param(503, OPNsenseConnectionError, id="http-failure"),
        pytest.param(TimeoutError(), OPNsenseTimeoutError, id="timeout"),
        pytest.param(MalformedJSONResponse(), OPNsenseError, id="undecodable"),
    ],
)
async def test_smart_reports_failed_reads(
    make_client: ClientType,
    throw_errors: bool,
    path: str,
    reply: int | TimeoutError | MalformedJSONResponse,
    error: type[OPNsenseError],
) -> None:
    """SMART read failures suppress to None or raise the corresponding public exception.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        throw_errors (bool): Public ``throw_errors`` setting of the client.
        path (str): SMART POST endpoint under test.
        reply (int | TimeoutError | MalformedJSONResponse): Router reply or transport error.
        error (type[OPNsenseError]): Expected exception with strict mode enabled.
    """
    client, router = make_router_client(
        make_client, {("POST", path): reply}, throw_errors=throw_errors
    )
    read = client.get_smart if path == SMART_LIST_PATH else lambda: client.get_smart_info("nvme0")

    if throw_errors:
        with pytest.raises(error) as exc_info:
            await read()
        assert type(exc_info.value) is error
    else:
        assert await read() is None
        if reply == 403:
            assert await read() is None

    assert router.count("POST", path) == 1


async def test_get_smart_retries_cached_denial_in_strict_mode(make_client: ClientType) -> None:
    """Strict SMART inventory reads retry a denial previously cached in default mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, router = make_router_client(make_client, {("POST", SMART_LIST_PATH): 403})

    assert await client.get_smart() is None
    client.toggle_throwing_errors(True)
    with pytest.raises(OPNsensePrivilegeMissing):
        await client.get_smart()

    assert router.requests == [("POST", SMART_LIST_PATH, None)] * 2


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
