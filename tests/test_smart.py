"""Tests for `aiopnsense.smart`."""

from collections.abc import Callable
import logging
from typing import Any

import pytest

from aiopnsense import OPNsenseClient, OPNsenseConnectionError, OPNsensePrivilegeMissing
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


def _empty(operation: str) -> Any:
    """Return the public result of a SMART read whose plugin is unavailable.

    Args:
        operation (str): ``list`` for the device list or ``info`` for one device's details.

    Returns:
        Any: Empty list for the device list, empty mapping for device info.
    """
    return [] if operation == "list" else {}


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


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["list", "info"])
@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        pytest.param(403, OPNsensePrivilegeMissing, id="forbidden"),
        pytest.param(500, OPNsenseConnectionError, id="server-error"),
        pytest.param(TimeoutError("timed out"), OPNsenseConnectionError, id="timeout"),
    ],
)
async def test_smart_read_raises_for_forbidden_or_transient_when_throwing(
    make_client: ClientType, operation: str, failure: Any, expected_error: type[Exception]
) -> None:
    """In throw mode a forbidden or transient SMART read raises a public error.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        operation (str): ``list`` for the device list or ``info`` for one device's details.
        failure (Any): Router response that models the failing endpoint.
        expected_error (type[Exception]): Public exception type expected from the call.
    """
    client, session = make_mock_session_client(make_client)
    install_fake_router(client, session, routes={("POST", _endpoint(operation)): failure})
    client.toggle_throwing_errors(True)
    try:
        with pytest.raises(expected_error):
            await _read(client, operation)
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["list", "info"])
async def test_smart_missing_plugin_does_not_raise_in_throw_mode(
    make_client: ClientType, operation: str
) -> None:
    """A missing SMART plugin (404) is an empty result, not an error, even in throw mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        operation (str): ``list`` for the device list or ``info`` for one device's details.
    """
    client, session = make_mock_session_client(make_client)
    install_fake_router(client, session, routes={("POST", _endpoint(operation)): 404})
    client.toggle_throwing_errors(True)
    try:
        assert await _read(client, operation) == _empty(operation)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["list", "info"])
@pytest.mark.parametrize("status", [404, 403])
async def test_smart_absent_endpoint_is_cached_until_ttl_expires(
    make_client: ClientType, operation: str, status: int
) -> None:
    """A missing or forbidden SMART endpoint is not requested again until the cache TTL expires.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        operation (str): ``list`` for the device list or ``info`` for one device's details.
        status (int): HTTP status the Router returns while the endpoint is absent.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("POST", _endpoint(operation)): status})
    try:
        assert await _read(client, operation) == _empty(operation)
        assert await _read(client, operation) == _empty(operation)
        assert router.count() == 1

        client._endpoint_cache_ttl_seconds = 0
        router.routes[("POST", _endpoint(operation))] = _payload(operation)
        assert await _read(client, operation) == _result(operation)
        assert router.count() == 2
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["list", "info"])
async def test_smart_endpoint_lost_after_success_is_recorded_as_absent(
    make_client: ClientType, operation: str, caplog: pytest.LogCaptureFixture
) -> None:
    """A 404 on a previously present SMART endpoint is cached as absent and logged quietly.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        operation (str): ``list`` for the device list or ``info`` for one device's details.
        caplog (pytest.LogCaptureFixture): Fixture capturing log records.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(
        client, session, routes={("POST", _endpoint(operation)): _payload(operation)}
    )
    try:
        assert await _read(client, operation) == _result(operation)
        router.routes[("POST", _endpoint(operation))] = 404
        with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
            assert await _read(client, operation) == _empty(operation)
            assert await _read(client, operation) == _empty(operation)
        assert router.count() == 2
        assert not [record for record in caplog.records if record.levelno >= logging.WARNING]
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["list", "info"])
@pytest.mark.parametrize("failure", [500, TimeoutError("timed out")])
async def test_smart_transient_failure_is_retried_and_warns_once(
    make_client: ClientType, operation: str, failure: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """A transient SMART failure is not cached, is retried, and warns once per outage.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        operation (str): ``list`` for the device list or ``info`` for one device's details.
        failure (Any): Router response that models the transient failure.
        caplog (pytest.LogCaptureFixture): Fixture capturing log records.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("POST", _endpoint(operation)): failure})
    try:
        with caplog.at_level(logging.DEBUG, logger="aiopnsense"):
            assert await _read(client, operation) == _empty(operation)
            assert await _read(client, operation) == _empty(operation)
            warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
            assert len(warnings) == 1
            assert router.count() == 2

            router.routes[("POST", _endpoint(operation))] = _payload(operation)
            assert await _read(client, operation) == _result(operation)
        recovered = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(recovered) == 1
        assert "recovered" in recovered[0].getMessage()
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["list", "info"])
async def test_smart_cached_404_is_trusted_in_throw_mode(
    make_client: ClientType, operation: str
) -> None:
    """A cached missing SMART endpoint stays an empty result in throw mode without a request.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        operation (str): ``list`` for the device list or ``info`` for one device's details.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("POST", _endpoint(operation)): 404})
    try:
        assert await _read(client, operation) == _empty(operation)
        client.toggle_throwing_errors(True)
        assert await _read(client, operation) == _empty(operation)
        assert router.count() == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["list", "info"])
@pytest.mark.parametrize(
    ("throw_errors", "expected_requests"),
    [
        pytest.param(False, 1, id="default-mode-trusts-cached-403"),
        pytest.param(True, 2, id="throw-mode-rechecks"),
    ],
)
async def test_smart_cached_403_is_rechecked_only_in_throw_mode(
    make_client: ClientType, operation: str, throw_errors: bool, expected_requests: int
) -> None:
    """A cached 403 is trusted in default mode but requested again, and raised, in throw mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        operation (str): ``list`` for the device list or ``info`` for one device's details.
        throw_errors (bool): Whether the client raises public errors.
        expected_requests (int): POSTs the Router should see across two reads.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("POST", _endpoint(operation)): 403})
    try:
        assert await _read(client, operation) == _empty(operation)
        client.toggle_throwing_errors(throw_errors)
        if throw_errors:
            with pytest.raises(OPNsensePrivilegeMissing):
                await _read(client, operation)
        else:
            assert await _read(client, operation) == _empty(operation)
        assert router.count() == expected_requests
    finally:
        await client.async_close()
