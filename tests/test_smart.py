"""Tests for `aiopnsense.smart`."""

from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from aiopnsense import OPNsenseClient
from tests.conftest import FakeResponse, make_mock_session_client

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
        client._check_optional_post_endpoint = AsyncMock(
            return_value=(
                "available",
                {
                    "devices": [
                        {"ident": "nvme0", "device": "nvme0", "status": "PASSED"},
                        {"ident": "ada0", "device": "ada0", "status": "FAILED"},
                    ]
                },
            )
        )

        smart_devices = await client.get_smart()

        assert smart_devices == [
            {"ident": "nvme0", "device": "nvme0", "status": "PASSED"},
            {"ident": "ada0", "device": "ada0", "status": "FAILED"},
        ]
        client._check_optional_post_endpoint.assert_awaited_once_with(
            "/api/smart/service/list/1", cache_path="/api/smart/service/list"
        )
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
        client._check_optional_post_endpoint = AsyncMock(
            return_value=(
                "available",
                {
                    "devices": [
                        {"ident": "", "device": "nvme0", "status": "PASSED"},
                        {"ident": "nvme0", "device": "nvme0", "status": "PASSED"},
                    ]
                },
            )
        )

        smart_devices = await client.get_smart()

        assert smart_devices == [{"ident": "nvme0", "device": "nvme0", "status": "PASSED"}]
        client._check_optional_post_endpoint.assert_awaited_once_with(
            "/api/smart/service/list/1", cache_path="/api/smart/service/list"
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_smart_logs_and_skips_non_mapping_rows(
    make_client: ClientType,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """SMART list queries should log and skip non-mapping device rows.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        caplog (pytest.LogCaptureFixture): Pytest log capture fixture.

    Returns:
        None: This test validates debug logging for skipped non-mapping rows.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_post_endpoint = AsyncMock(
            return_value=(
                "available",
                {
                    "devices": [
                        "unexpected-string-row",
                        {"ident": "nvme0", "device": "nvme0", "status": "PASSED"},
                    ]
                },
            )
        )

        with caplog.at_level("DEBUG"):
            smart_devices = await client.get_smart()

        assert smart_devices == [{"ident": "nvme0", "device": "nvme0", "status": "PASSED"}]
        assert (
            "Discarding SMART device row because item is not a mapping: 'unexpected-string-row'"
            in caplog.text
        )
        client._check_optional_post_endpoint.assert_awaited_once_with(
            "/api/smart/service/list/1", cache_path="/api/smart/service/list"
        )
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
        client._check_optional_post_endpoint = AsyncMock(
            return_value=("available", {"devices": "ignored"})
        )

        smart_devices = await client.get_smart()

        assert smart_devices == []
        client._check_optional_post_endpoint.assert_awaited_once_with(
            "/api/smart/service/list/1", cache_path="/api/smart/service/list"
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_smart_result_marks_non_mapping_payload_malformed(
    make_client: ClientType,
) -> None:
    """SMART result queries should reject non-mapping available payloads.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates the SMART result availability envelope.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_post_endpoint = AsyncMock(return_value=("available", []))

        result = await client.get_smart_result()

        assert result.data == []
        assert result.state == "malformed"
        assert result.authoritative is False
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "endpoint", "expected"),
    [
        ("list", "/api/smart/service/list", []),
        ("info", "/api/smart/service/info", {}),
    ],
)
@pytest.mark.parametrize("availability_status", ["missing", "unavailable", "malformed"])
async def test_smart_fails_closed_when_endpoint_is_unavailable(
    make_client: ClientType,
    operation: str,
    endpoint: str,
    expected: object,
    availability_status: str,
) -> None:
    """SMART POST operations should fail closed when endpoint availability checks fail.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        operation (str): SMART operation under test.
        endpoint (str): Expected endpoint for availability check.
        expected (object): Expected fail-closed payload.
        availability_status (str): Simulated optional endpoint availability state.

    Returns:
        None: This test validates fail-closed behavior for SMART POST operations.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_post_endpoint = AsyncMock(return_value=(availability_status, {}))

        if operation == "list":
            got = await client.get_smart()
        else:
            got = await client.get_smart_info("nvme0")

        assert got == expected
        if operation == "list":
            client._check_optional_post_endpoint.assert_awaited_once_with(
                "/api/smart/service/list/1", cache_path="/api/smart/service/list"
            )
        else:
            client._check_optional_post_endpoint.assert_awaited_once_with(
                "/api/smart/service/info",
                payload={"device": "nvme0", "type": "a", "json": True},
            )
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
        client._is_get_endpoint_available = AsyncMock(
            side_effect=AssertionError("GET probe should not run")
        )
        client._check_optional_post_endpoint = AsyncMock(
            return_value=(
                "available",
                {"devices": [{"ident": "nvme0", "device": "nvme0", "status": "PASSED"}]},
            )
        )

        assert await client.get_smart() == [
            {"ident": "nvme0", "device": "nvme0", "status": "PASSED"}
        ]
        client._check_optional_post_endpoint.assert_awaited_once_with(
            "/api/smart/service/list/1", cache_path="/api/smart/service/list"
        )
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
        client._check_optional_post_endpoint = AsyncMock(
            return_value=("available", {"output": {"smart_status": "PASSED", "temperature": 35}})
        )

        smart_info = await client.get_smart_info("nvme0")

        assert smart_info == {"smart_status": "PASSED", "temperature": 35}
        client._check_optional_post_endpoint.assert_awaited_once_with(
            "/api/smart/service/info",
            payload={"device": "nvme0", "type": "a", "json": True},
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
        client._check_optional_post_endpoint = AsyncMock(
            return_value=("available", {"output": ["line1", "line2"]})
        )

        smart_info = await client.get_smart_info("nvme0", info_type="H")

        assert smart_info == {"output": ["line1", "line2"]}
        client._check_optional_post_endpoint.assert_awaited_once_with(
            "/api/smart/service/info",
            payload={"device": "nvme0", "type": "H", "json": True},
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
        client._is_get_endpoint_available = AsyncMock(
            side_effect=AssertionError("GET probe should not run")
        )
        client._check_optional_post_endpoint = AsyncMock(
            return_value=("available", {"output": {"smart_status": "PASSED"}})
        )

        assert await client.get_smart_info("nvme0") == {"smart_status": "PASSED"}
        client._check_optional_post_endpoint.assert_awaited_once_with(
            "/api/smart/service/info",
            payload={"device": "nvme0", "type": "a", "json": True},
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_smart_fails_closed_when_list_endpoint_unavailable(
    make_client: ClientType,
) -> None:
    """SMART list queries should use the cheap list preflight.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates fail-closed behavior for the SMART list.
    """
    client, _session = make_mock_session_client(make_client)
    try:
        client._check_optional_post_endpoint = AsyncMock(return_value=("unavailable", {}))

        assert await client.get_smart() == []
        client._check_optional_post_endpoint.assert_awaited_once_with(
            "/api/smart/service/list/1", cache_path="/api/smart/service/list"
        )
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
        assert await client._is_post_endpoint_available("/api/smart/service/list") is False
        assert await client._is_post_endpoint_available("/api/smart/service/list") is False
        assert calls == 1
    finally:
        await client.async_close()
