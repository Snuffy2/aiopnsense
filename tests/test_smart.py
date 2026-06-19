"""Tests for `aiopnsense.smart`."""

from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from aiopnsense import OPNsenseClient
from tests.conftest import FakeResponse, make_mock_session_client

ClientType = Callable[..., OPNsenseClient]


@pytest.mark.asyncio
async def test_get_smart_normalizes_device_names(make_client: ClientType) -> None:
    """SMART list queries should normalize bare device names into mappings.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates normalization of SMART device name rows.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.is_post_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_post = AsyncMock(return_value={"devices": ["nvme0", "ada0"]})

        smart_devices = await client.get_smart(details=False)

        assert smart_devices == [{"device": "nvme0"}, {"device": "ada0"}]
        client.is_post_endpoint_available.assert_awaited_once_with("/api/smart/service/list")
        client._safe_dict_post.assert_awaited_once_with("/api/smart/service/list")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_smart_details_preserves_mapping_rows(make_client: ClientType) -> None:
    """Detailed SMART list queries should preserve mapping rows and infer devices.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates detailed SMART row normalization.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.is_post_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_post = AsyncMock(
            return_value={
                "devices": [
                    {"dev": "nvme0", "status": "PASSED"},
                    {"device": "ada0", "status": "FAILED"},
                    "ignored",
                ]
            }
        )

        smart_devices = await client.get_smart(details=True)

        assert smart_devices == [
            {"dev": "nvme0", "status": "PASSED", "device": "nvme0"},
            {"device": "ada0", "status": "FAILED"},
        ]
        client.is_post_endpoint_available.assert_awaited_once_with("/api/smart/service/list")
        client._safe_dict_post.assert_awaited_once_with("/api/smart/service/list/1")
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "endpoint", "expected"),
    [
        ("list", "/api/smart/service/list", []),
        ("detail", "/api/smart/service/list", []),
        ("info", "/api/smart/service/info", {}),
    ],
)
async def test_smart_fails_closed_when_endpoint_is_unavailable(
    make_client: ClientType,
    operation: str,
    endpoint: str,
    expected: object,
) -> None:
    """SMART POST operations should fail closed when endpoint availability checks fail.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        operation (str): SMART operation under test.
        endpoint (str): Expected endpoint for availability check.
        expected (object): Expected fail-closed payload.

    Returns:
        None: This test validates fail-closed behavior for SMART POST operations.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.is_post_endpoint_available = AsyncMock(return_value=False)
        client._safe_dict_post = AsyncMock(return_value={})

        if operation == "list":
            got = await client.get_smart(details=False)
        elif operation == "detail":
            got = await client.get_smart(details=True)
        else:
            got = await client.get_smart_info("nvme0")

        assert got == expected
        client.is_post_endpoint_available.assert_awaited_once_with(endpoint)
        client._safe_dict_post.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_smart_does_not_probe_post_only_endpoint(make_client: ClientType) -> None:
    """SMART list queries should not use GET endpoint probes for POST-only APIs.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates that SMART list requests do not depend on GET probes.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.is_get_endpoint_available = AsyncMock(
            side_effect=AssertionError("GET probe should not run")
        )
        client.is_post_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_post = AsyncMock(return_value={"devices": ["nvme0"]})

        assert await client.get_smart(details=False) == [{"device": "nvme0"}]
        client.is_post_endpoint_available.assert_awaited_once_with("/api/smart/service/list")
        client._safe_dict_post.assert_awaited_once_with("/api/smart/service/list")
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
        client.is_post_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_post = AsyncMock(
            return_value={"output": {"smart_status": "PASSED", "temperature": 35}}
        )

        smart_info = await client.get_smart_info("nvme0")

        assert smart_info == {"smart_status": "PASSED", "temperature": 35}
        client.is_post_endpoint_available.assert_awaited_once_with("/api/smart/service/info")
        client._safe_dict_post.assert_awaited_once_with(
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
        client.is_post_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_post = AsyncMock(return_value={"output": ["line1", "line2"]})

        smart_info = await client.get_smart_info("nvme0", info_type="H")

        assert smart_info == {"output": ["line1", "line2"]}
        client.is_post_endpoint_available.assert_awaited_once_with("/api/smart/service/info")
        client._safe_dict_post.assert_awaited_once_with(
            "/api/smart/service/info",
            {"device": "nvme0", "type": "H", "json": True},
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_smart_info_does_not_probe_post_only_endpoint(make_client: ClientType) -> None:
    """SMART info queries should not use GET endpoint probes for POST-only APIs.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates that SMART info requests do not depend on GET probes.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.is_get_endpoint_available = AsyncMock(
            side_effect=AssertionError("GET probe should not run")
        )
        client.is_post_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_post = AsyncMock(return_value={"output": {"smart_status": "PASSED"}})

        assert await client.get_smart_info("nvme0") == {"smart_status": "PASSED"}
        client.is_post_endpoint_available.assert_awaited_once_with("/api/smart/service/info")
        client._safe_dict_post.assert_awaited_once_with(
            "/api/smart/service/info",
            {"device": "nvme0", "type": "a", "json": True},
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_smart_details_fails_closed_when_detail_endpoint_unavailable(
    make_client: ClientType,
) -> None:
    """Detailed SMART list queries should use the cheap list preflight.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates fail-closed behavior for the detailed SMART list.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client.is_post_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_post = AsyncMock(return_value={})

        assert await client.get_smart(details=True) == []
        client.is_post_endpoint_available.assert_awaited_once_with("/api/smart/service/list")
        client._safe_dict_post.assert_awaited_once_with("/api/smart/service/list/1")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_smart_post_endpoint_availability_caches_missing_plugin(
    make_client: ClientType,
) -> None:
    """Shared POST endpoint availability should cache a 404 SMART probe result.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates quiet missing-plugin caching for SMART polling.
    """
    client, session = make_mock_session_client(make_client)
    calls = 0

    def _post(*_args: object, **_kwargs: object) -> FakeResponse:
        """Return a repeated 404 response to emulate a missing plugin probe result.

        Args:
            *_args (object): Positional arguments ignored by this test stub.
            **_kwargs (object): Keyword arguments ignored by this test stub.

        Returns:
            FakeResponse: A fixed 404 response object used by availability checks.
        """
        nonlocal calls
        calls += 1
        return FakeResponse(status=404, reason="Not Found", ok=False)

    session.post = _post
    try:
        assert await client.is_post_endpoint_available("/api/smart/service/list") is False
        assert await client.is_post_endpoint_available("/api/smart/service/list") is False
        assert calls == 1
    finally:
        await client.async_close()
