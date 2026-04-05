"""Tests for `aiopnsense.vouchers`."""

from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, call

import pytest

import aiopnsense as aiopnsense_module
from aiopnsense import OPNsenseClient
from tests.conftest import make_mock_session_client

ClientType = Callable[..., OPNsenseClient]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "safe_get_ret,safe_post_ret,data,expect_exc,expect_username,expect_extras",
    [
        ([], None, {}, aiopnsense_module.OPNsenseVoucherServerError, None, None),
        (["s1", "s2"], None, {}, aiopnsense_module.OPNsenseVoucherServerError, None, None),
        (
            None,
            [
                {
                    "username": "u",
                    "password": "p",
                    "vouchergroup": "g",
                    "starttime": "t",
                    "expirytime": 253402300799,
                    "validity": 65,
                }
            ],
            {"voucher_server": "srv"},
            None,
            "u",
            ["expiry_timestamp", "validity_str"],
        ),
    ],
)
async def test_generate_vouchers_server_selection_errors_and_success(
    make_client: ClientType,
    safe_get_ret: list[str] | None,
    safe_post_ret: list[dict[str, Any]] | None,
    data: dict[str, Any],
    expect_exc: type[Exception] | None,
    expect_username: str | None,
    expect_extras: list[str] | None,
) -> None:
    """Cover voucher-server selection errors and successful voucher generation.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.
        safe_get_ret (list[str] | None): Mocked provider-list response payload.
        safe_post_ret (list[dict[str, Any]] | None): Mocked voucher-generation response payload.
        data (dict[str, Any]): Input arguments passed to ``generate_vouchers``.
        expect_exc (type[Exception] | None): Expected exception type for provider-selection cases.
        expect_username (str | None): Expected username in the first voucher entry on success.
        expect_extras (list[str] | None): Optional keys expected in successful voucher output.

    Returns:
        None: This test validates both error and success paths via assertions.
    """
    client, _ = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        if safe_get_ret is not None:
            client.is_endpoint_available = AsyncMock(return_value=True)
            client._safe_list_get = AsyncMock(return_value=safe_get_ret)
            assert expect_exc is not None
            with pytest.raises(expect_exc):
                await client.generate_vouchers(data)
            return

        # safe_post case: expect success and optional extra fields
        client.is_endpoint_available = AsyncMock(return_value=True)
        client._safe_list_post = AsyncMock(return_value=safe_post_ret)
        got = await client.generate_vouchers(data)
        assert isinstance(got, list)
        assert len(got) > 0
        assert got[0].get("username") == expect_username
        for key in expect_extras or []:
            assert key in got[0]
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_generate_vouchers_returns_empty_when_version_switched_endpoints_unavailable(
    make_client: ClientType,
) -> None:
    """`generate_vouchers` should return empty data when the provider listing endpoint is absent.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts voucher generation returns an empty list when unavailable.
    """
    client, _ = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        client.is_endpoint_available = AsyncMock(return_value=False)
        client._safe_list_get = AsyncMock()
        client._safe_list_post = AsyncMock()
        assert await client.generate_vouchers({}) == []
        client.is_endpoint_available.assert_awaited_once_with(
            "/api/captiveportal/voucher/list_providers"
        )
        client._safe_list_get.assert_not_awaited()
        client._safe_list_post.assert_not_awaited()
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
    client, _ = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        client.is_endpoint_available = AsyncMock(return_value=True)
        client._safe_list_get = AsyncMock(return_value=["srv one"])
        client._safe_list_post = AsyncMock(
            return_value=[{"username": "u", "validity": 60, "expirytime": 253402300799}]
        )

        vouchers = await client.generate_vouchers({})

        assert vouchers[0]["username"] == "u"
        assert vouchers[0]["validity_str"] == "1 minute"
        assert vouchers[0]["expiry_timestamp"] == 253402300799
        client._safe_list_get.assert_awaited_once_with("/api/captiveportal/voucher/list_providers")
        client._safe_list_post.assert_awaited_once_with(
            "/api/captiveportal/voucher/generate_vouchers/srv%20one/",
            payload={},
        )
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_generate_vouchers_returns_empty_when_generation_endpoint_unavailable(
    make_client: ClientType,
) -> None:
    """`generate_vouchers` should fail closed when generation endpoint is absent.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test asserts voucher generation returns an empty list when unavailable.
    """
    client, _ = make_mock_session_client(make_client)
    try:
        client._use_snake_case = True
        client.is_endpoint_available = AsyncMock(side_effect=[True, False])
        client._safe_list_get = AsyncMock(return_value=["srv one"])
        client._safe_list_post = AsyncMock()

        assert await client.generate_vouchers({}) == []
        assert client.is_endpoint_available.await_args_list == [
            call("/api/captiveportal/voucher/list_providers"),
            call("/api/captiveportal/voucher/generate_vouchers"),
        ]
        client._safe_list_get.assert_awaited_once_with("/api/captiveportal/voucher/list_providers")
        client._safe_list_post.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("use_snake_case", "expected_provider", "expected_generate"),
    [
        (
            True,
            "/api/captiveportal/voucher/list_providers",
            "/api/captiveportal/voucher/generate_vouchers/srv/",
        ),
        (
            False,
            "/api/captiveportal/voucher/listProviders",
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
    """Verify voucher helpers choose snake_case or camelCase endpoints consistently."""
    client, _ = make_mock_session_client(make_client)
    try:
        client._use_snake_case = use_snake_case
        client.is_endpoint_available = AsyncMock(return_value=True)
        client._safe_list_get = AsyncMock(return_value=["srv"])
        client._safe_list_post = AsyncMock(return_value=[])

        await client.generate_vouchers({})

        client._safe_list_get.assert_awaited_once_with(expected_provider)
        client._safe_list_post.assert_awaited_once_with(expected_generate, payload={})
    finally:
        await client.async_close()
