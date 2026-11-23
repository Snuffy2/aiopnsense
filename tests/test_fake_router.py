"""Tests for the fake Router that answers at the aiohttp session boundary."""

import asyncio

from tests.conftest import MakeClientFactory, make_router_client

NUT_PATH = "/api/nut/diagnostics/upsstatus"
NUT_DATA = {"ups.status": "OL"}
SMART_PATH = "/api/smart/service/list/1"


async def test_unrouted_request_does_not_kill_the_worker(make_client: MakeClientFactory) -> None:
    """After an unrouted request the same client still gets answers for routed ones.

    Args:
        make_client (MakeClientFactory): Factory for a client using a mocked HTTP session.
    """
    client, router = make_router_client(make_client, {("GET", NUT_PATH): NUT_DATA})

    assert await client.get_smart() == []
    async with asyncio.timeout(2):
        assert await client.get_nut_ups_status() == NUT_DATA

    assert router.count("POST", SMART_PATH) == 1
    assert router.count("GET", NUT_PATH) == 1
