"""Tests for `aiopnsense.smart`."""

from collections.abc import Callable
from typing import Any

import pytest

from aiopnsense import OPNsenseClient
from tests.conftest import install_fake_router, make_mock_session_client

ClientType = Callable[..., OPNsenseClient]
SMART_SERVICE_LIST_PATH = "/api/smart/service/list"
SMART_SERVICE_DETAIL_PATH = "/api/smart/service/list/1"
SMART_SERVICE_INFO_PATH = "/api/smart/service/info"
SMART_DEVICE = {"ident": "nvme0", "device": "nvme0", "status": "PASSED"}
SMART_INFO_OUTPUT = {"smart_status": "PASSED", "temperature": 35}
SMART_INFO_BODY = {"device": "nvme0", "type": "a", "json": True}
PERMANENT_FAILURES = [
    pytest.param(404, id="not-found"),
    pytest.param(403, id="forbidden"),
    pytest.param(500, id="server-error"),
    pytest.param(TimeoutError("timed out"), id="timeout"),
]


def _endpoint(operation: str) -> str:
    """Return the data endpoint a SMART read posts to.

    Args:
        operation (str): ``list`` for the device list or ``info`` for one device's details.

    Returns:
        str: API path of the POST that carries the read.
    """
    return SMART_SERVICE_DETAIL_PATH if operation == "list" else SMART_SERVICE_INFO_PATH


def _payload(operation: str) -> Any:
    """Return the Router response for a successful SMART read.

    Args:
        operation (str): ``list`` for the device list or ``info`` for one device's details.

    Returns:
        Any: JSON body the Router returns for the read.
    """
    return {"devices": [SMART_DEVICE]} if operation == "list" else {"output": SMART_INFO_OUTPUT}


def _result(operation: str) -> Any:
    """Return the public result of a successful SMART read.

    Args:
        operation (str): ``list`` for the device list or ``info`` for one device's details.

    Returns:
        Any: Value the public method returns for a present endpoint.
    """
    return [SMART_DEVICE] if operation == "list" else SMART_INFO_OUTPUT


async def _read(client: OPNsenseClient, operation: str) -> Any:
    """Run one SMART read through its public client method.

    Args:
        client (OPNsenseClient): Client under test.
        operation (str): ``list`` for the device list or ``info`` for one device's details.

    Returns:
        Any: Value returned by the public method.
    """
    if operation == "list":
        return await client.get_smart()
    return await client.get_smart_info("nvme0")


@pytest.mark.asyncio
async def test_get_smart_returns_device_rows_from_one_post(make_client: ClientType) -> None:
    """SMART list queries should return detailed device rows from a single POST.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(
        client, session, routes={("POST", SMART_SERVICE_DETAIL_PATH): _payload("list")}
    )
    try:
        assert await client.get_smart() == [SMART_DEVICE]
        assert router.count() == 1
        assert router.count("POST", SMART_SERVICE_DETAIL_PATH) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        pytest.param(
            {"devices": [{"ident": "", "device": "nvme0", "status": "PASSED"}, SMART_DEVICE]},
            [SMART_DEVICE],
            id="skips-blank-ident",
        ),
        pytest.param(
            {"devices": ["unexpected-string-row", SMART_DEVICE]},
            [SMART_DEVICE],
            id="skips-non-mapping-rows",
        ),
        pytest.param({"devices": "ignored"}, [], id="non-list-devices"),
        pytest.param(["not", "a", "mapping"], [], id="non-mapping-payload"),
    ],
)
async def test_get_smart_discards_malformed_device_rows(
    make_client: ClientType, payload: Any, expected: list[dict[str, Any]]
) -> None:
    """SMART list queries should keep valid device rows and fail closed on a wrong shape.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        payload (Any): Response the Router returns for the device list.
        expected (list[dict[str, Any]]): Device rows the public method should return.
    """
    client, session = make_mock_session_client(make_client)
    install_fake_router(client, session, routes={("POST", SMART_SERVICE_DETAIL_PATH): payload})
    try:
        assert await client.get_smart() == expected
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_smart_info_posts_device_and_returns_output(make_client: ClientType) -> None:
    """SMART info queries should post the device selector and return the decoded output.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(
        client, session, routes={("POST", SMART_SERVICE_INFO_PATH): _payload("info")}
    )
    try:
        assert await client.get_smart_info("nvme0") == SMART_INFO_OUTPUT
        assert router.count("POST", SMART_SERVICE_INFO_PATH) == 1
        assert router.requests[0][2] == SMART_INFO_BODY
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_smart_info_wraps_non_mapping_output(make_client: ClientType) -> None:
    """SMART info queries should preserve non-mapping outputs in a stable wrapper.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(
        client,
        session,
        routes={("POST", SMART_SERVICE_INFO_PATH): {"output": ["line1", "line2"]}},
    )
    try:
        assert await client.get_smart_info("nvme0", info_type="H") == {"output": ["line1", "line2"]}
        assert router.requests[0][2] == {"device": "nvme0", "type": "H", "json": True}
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["list", "info"])
async def test_smart_present_endpoint_costs_one_post_per_read(
    make_client: ClientType, operation: str
) -> None:
    """A present SMART endpoint costs one POST per read, with no payload-less probe.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        operation (str): ``list`` for the device list or ``info`` for one device's details.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(
        client, session, routes={("POST", _endpoint(operation)): _payload(operation)}
    )
    try:
        assert await _read(client, operation) == _result(operation)
        assert await _read(client, operation) == _result(operation)
        assert router.count("POST", _endpoint(operation)) == 2
        assert router.count() == 2
        assert router.count("POST", SMART_SERVICE_LIST_PATH) == 0
    finally:
        await client.async_close()


def _empty(operation: str) -> Any:
    """Return the public result of a SMART read whose plugin is unavailable.

    Args:
        operation (str): ``list`` for the device list or ``info`` for one device's details.

    Returns:
        Any: Empty list for the device list, empty mapping for device info.
    """
    return [] if operation == "list" else {}


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["list", "info"])
@pytest.mark.parametrize("failure", PERMANENT_FAILURES)
async def test_smart_read_returns_empty_value_when_absent_or_transient(
    make_client: ClientType, operation: str, failure: Any
) -> None:
    """SMART reads return their empty value for absent and transient endpoints, one POST each.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        operation (str): ``list`` for the device list or ``info`` for one device's details.
        failure (Any): Router response that models the absent or failing endpoint.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("POST", _endpoint(operation)): failure})
    try:
        assert await _read(client, operation) == _empty(operation)
        assert router.count() == 1
    finally:
        await client.async_close()
