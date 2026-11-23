"""Tests for `aiopnsense.smart`."""

from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from aiopnsense import OPNsenseClient
from aiopnsense._endpoint_outcome import _EndpointOutcome
from tests.conftest import install_fake_router, make_mock_session_client, stub_endpoint_outcome

ClientType = Callable[..., OPNsenseClient]


@pytest.mark.asyncio
async def test_get_smart_returns_device_rows(make_client: ClientType) -> None:
    """SMART list queries should return detailed device rows unchanged.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates SMART list payload handling.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        read = stub_endpoint_outcome(
            client,
            _EndpointOutcome.data(
                {
                    "devices": [
                        {"ident": "nvme0", "device": "nvme0", "status": "PASSED"},
                        {"ident": "ada0", "device": "ada0", "status": "FAILED"},
                    ]
                }
            ),
            read="post",
        )

        smart_devices = await client.get_smart()

        assert smart_devices == [
            {"ident": "nvme0", "device": "nvme0", "status": "PASSED"},
            {"ident": "ada0", "device": "ada0", "status": "FAILED"},
        ]
        read.assert_awaited_once_with("/api/smart/service/list/1")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_smart_skips_devices_with_blank_ident(make_client: ClientType) -> None:
    """SMART list queries should skip device rows without an identifier.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates filtering of blank SMART identifiers.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        read = stub_endpoint_outcome(
            client,
            _EndpointOutcome.data(
                {
                    "devices": [
                        {"ident": "", "device": "nvme0", "status": "PASSED"},
                        {"ident": "nvme0", "device": "nvme0", "status": "PASSED"},
                    ]
                }
            ),
            read="post",
        )

        smart_devices = await client.get_smart()

        assert smart_devices == [{"ident": "nvme0", "device": "nvme0", "status": "PASSED"}]
        read.assert_awaited_once_with("/api/smart/service/list/1")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_smart_skips_non_mapping_rows(
    make_client: ClientType,
) -> None:
    """SMART list queries should skip non-mapping device rows.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
    Returns:
        None: This test validates handling for non-mapping rows.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        read = stub_endpoint_outcome(
            client,
            _EndpointOutcome.data(
                {
                    "devices": [
                        "unexpected-string-row",
                        {"ident": "nvme0", "device": "nvme0", "status": "PASSED"},
                    ]
                }
            ),
            read="post",
        )

        smart_devices = await client.get_smart()

        assert smart_devices == [{"ident": "nvme0", "device": "nvme0", "status": "PASSED"}]
        read.assert_awaited_once_with("/api/smart/service/list/1")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_smart_returns_empty_list_for_non_list_payload(make_client: ClientType) -> None:
    """SMART list queries should fail closed when the payload shape is wrong.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates fail-closed SMART list payload handling.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        read = stub_endpoint_outcome(
            client, _EndpointOutcome.data({"devices": "ignored"}), read="post"
        )

        smart_devices = await client.get_smart()

        assert smart_devices == []
        read.assert_awaited_once_with("/api/smart/service/list/1")
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "endpoint", "expected"),
    [
        ("list", "/api/smart/service/list/1", []),
        ("info", "/api/smart/service/info", {}),
    ],
)
async def test_smart_fails_closed_when_endpoint_is_unavailable(
    make_client: ClientType,
    operation: str,
    endpoint: str,
    expected: object,
) -> None:
    """SMART POST operations should fail closed when the Router reports the endpoint absent.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        operation (str): SMART operation under test.
        endpoint (str): Expected endpoint of the POST request.
        expected (object): Expected fail-closed payload.

    Returns:
        None: This test validates fail-closed behavior for SMART POST operations.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        read = stub_endpoint_outcome(client, _EndpointOutcome.absent(), read="post")

        if operation == "list":
            got = await client.get_smart()
        else:
            got = await client.get_smart_info("nvme0")

        assert got == expected
        assert read.await_args.args[0] == endpoint
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "response", "expected", "post_args"),
    [
        pytest.param(
            "list",
            {"devices": [{"ident": "nvme0", "device": "nvme0", "status": "PASSED"}]},
            [{"ident": "nvme0", "device": "nvme0", "status": "PASSED"}],
            ("/api/smart/service/list/1",),
            id="list",
        ),
        pytest.param(
            "info",
            {"output": {"smart_status": "PASSED"}},
            {"smart_status": "PASSED"},
            ("/api/smart/service/info", {"device": "nvme0", "type": "a", "json": True}),
            id="info",
        ),
    ],
)
async def test_smart_does_not_probe_post_only_endpoint(
    make_client: ClientType,
    operation: str,
    response: dict[str, object],
    expected: object,
    post_args: tuple[object, ...],
) -> None:
    """SMART operations should not use GET endpoint probes for POST-only APIs.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        operation (str): SMART operation under test.
        response (dict[str, object]): Mocked response from the POST request.
        expected (object): Expected result from the SMART operation.
        post_args (tuple[object, ...]): Expected arguments for the SMART POST request.

    Returns:
        None: This test validates that SMART POST requests do not depend on GET probes.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._is_get_endpoint_available = AsyncMock(
            side_effect=AssertionError("GET probe should not run")
        )
        read = stub_endpoint_outcome(client, _EndpointOutcome.data(response), read="post")

        if operation == "list":
            got = await client.get_smart()
        else:
            got = await client.get_smart_info("nvme0")

        assert got == expected
        client._is_get_endpoint_available.assert_not_awaited()
        read.assert_awaited_once_with(*post_args)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_smart_info_returns_json_output(make_client: ClientType) -> None:
    """SMART info queries should request JSON output and return the decoded payload.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates SMART info payload construction and response handling.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        read = stub_endpoint_outcome(
            client,
            _EndpointOutcome.data({"output": {"smart_status": "PASSED", "temperature": 35}}),
            read="post",
        )

        smart_info = await client.get_smart_info("nvme0")

        assert smart_info == {"smart_status": "PASSED", "temperature": 35}
        read.assert_awaited_once_with(
            "/api/smart/service/info",
            {"device": "nvme0", "type": "a", "json": True},
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_smart_info_wraps_non_mapping_output(make_client: ClientType) -> None:
    """SMART info queries should preserve non-mapping outputs in a stable wrapper.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates fallback handling for non-mapping SMART info payloads.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        read = stub_endpoint_outcome(
            client,
            _EndpointOutcome.data({"output": ["line1", "line2"]}),
            read="post",
        )

        smart_info = await client.get_smart_info("nvme0", info_type="H")

        assert smart_info == {"output": ["line1", "line2"]}
        read.assert_awaited_once_with(
            "/api/smart/service/info",
            {"device": "nvme0", "type": "H", "json": True},
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_smart_fails_closed_when_list_endpoint_unavailable(
    make_client: ClientType,
) -> None:
    """SMART list queries should fail closed when the Router reports the list absent.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates fail-closed behavior for the SMART list.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        read = stub_endpoint_outcome(client, _EndpointOutcome.absent(), read="post")

        assert await client.get_smart() == []
        read.assert_awaited_once_with("/api/smart/service/list/1")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_smart_post_endpoint_availability_caches_missing_plugin(
    make_client: ClientType,
) -> None:
    """Shared POST endpoint availability should cache a 404 SMART list result.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates quiet missing-plugin caching for SMART polling.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(
        client, session, routes={("POST", "/api/smart/service/list/1"): 404}
    )
    try:
        assert await client.get_smart() == []
        assert await client.get_smart() == []
        assert router.count("POST", "/api/smart/service/list/1") == 1
    finally:
        await client.async_close()
