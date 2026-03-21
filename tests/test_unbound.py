"""Tests for `aiopnsense.unbound`."""

from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

import aiopnsense as pyopnsense


@pytest.mark.asyncio
async def test_get_unbound_blocklist_returns_uuid_mapping(make_client) -> None:
    """The DNSBL search response should be normalized into a UUID-keyed mapping."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = make_client(session=session)
    try:
        client._safe_dict_get = AsyncMock(
            return_value={
                "rows": [
                    {"uuid": "dnsbl1", "enabled": "1"},
                    {"uuid": "dnsbl2", "enabled": "0"},
                    {"no_uuid": "skip-me"},
                    "bad-row",
                ]
            }
        )

        result = await client.get_unbound_blocklist()

        assert result == {
            "dnsbl1": {"uuid": "dnsbl1", "enabled": "1"},
            "dnsbl2": {"uuid": "dnsbl2", "enabled": "0"},
        }
        client._safe_dict_get.assert_awaited_once_with("/api/unbound/settings/search_dnsbl")
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("api_response", [{}, {"rows": []}, {"rows": "not-a-list"}, []])
async def test_get_unbound_blocklist_handles_empty_or_invalid_responses(
    make_client, api_response
) -> None:
    """Malformed or empty DNSBL responses should normalize to an empty mapping."""
    client = make_client()
    try:
        client._safe_dict_get = AsyncMock(return_value=api_response)

        result = await client.get_unbound_blocklist()

        assert result == {}
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method_name", "uuid", "toggle_result", "dnsbl_result", "expected"),
    [
        (
            "enable_unbound_blocklist",
            "uuid1",
            {"result": "Enabled"},
            {"status": "OK applied"},
            True,
        ),
        (
            "disable_unbound_blocklist",
            "uuid1",
            {"result": "Disabled"},
            {"status": "OK applied"},
            True,
        ),
        ("enable_unbound_blocklist", None, {"result": "Enabled"}, {"status": "OK applied"}, False),
        (
            "enable_unbound_blocklist",
            "uuid1",
            {"result": "failed"},
            {"status": "OK applied"},
            False,
        ),
        ("enable_unbound_blocklist", "uuid1", {"result": "Enabled"}, {"status": "failed"}, False),
    ],
)
async def test_enable_disable_unbound_blocklist(
    make_client, method_name, uuid, toggle_result, dnsbl_result, expected
) -> None:
    """DNSBL toggles should require a UUID, a successful toggle response, and an OK apply status."""
    client = make_client()
    try:
        client._safe_dict_post = AsyncMock(return_value=toggle_result)
        client._get = AsyncMock(return_value=dnsbl_result)

        result = await getattr(client, method_name)(uuid)

        assert result is expected
        if uuid:
            expected_state = "1" if method_name == "enable_unbound_blocklist" else "0"
            client._safe_dict_post.assert_awaited_once_with(
                f"/api/unbound/settings/toggle_dnsbl/{uuid}/{expected_state}"
            )
        else:
            client._safe_dict_post.assert_not_awaited()
            client._get.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_toggle_unbound_blocklist_handles_apply_exception(make_client) -> None:
    """Client errors during the DNSBL apply step should return False."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = make_client(session=session)
    try:
        client._safe_dict_post = AsyncMock(return_value={"result": "Enabled"})
        client._get = AsyncMock(side_effect=aiohttp.ClientError("boom"))

        result = await client.enable_unbound_blocklist("uuid1")

        assert result is False
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_toggle_unbound_blocklist_uses_expected_endpoints() -> None:
    """The shared toggle helper should hit the expected toggle and apply endpoints."""
    session = MagicMock(spec=aiohttp.ClientSession)
    client = pyopnsense.OPNsenseClient(
        url="http://localhost", username="u", password="p", session=session
    )
    try:
        client._safe_dict_post = AsyncMock(return_value={"result": "Disabled"})
        client._get = AsyncMock(return_value={"status": "OK"})

        result = await client.disable_unbound_blocklist("uuid1")

        assert result is True
        client._safe_dict_post.assert_awaited_once_with(
            "/api/unbound/settings/toggle_dnsbl/uuid1/0"
        )
        client._get.assert_awaited_once_with("/api/unbound/service/dnsbl")
    finally:
        await client.async_close()
