"""Tests for the fake Router that answers at the aiohttp session boundary."""

import asyncio
from functools import partial

import aiohttp
import pytest

import aiopnsense
from aiopnsense import OPNsenseConnectionError
from tests.conftest import MakeClientFactory, install_fake_router, make_mock_session_client

NUT_PATH = "/api/nut/diagnostics/upsstatus"
NUT_DATA = {"ups.status": "OL"}
SMART_PATH = "/api/smart/service/list/1"


def _live_workers(client: aiopnsense.OPNsenseClient) -> list[asyncio.Task[object]]:
    """Return the client's queue worker tasks that are still running.

    Args:
        client (aiopnsense.OPNsenseClient): Client whose queue workers are inspected.

    Returns:
        list[asyncio.Task[object]]: Real worker tasks that have not finished.
    """
    return [w for w in client._workers if isinstance(w, asyncio.Task) and not w.done()]


async def test_public_read_runs_on_real_queue_workers(make_client: MakeClientFactory) -> None:
    """A public read reaches the fake Router through real workers, which stop on close.

    Args:
        make_client (MakeClientFactory): Factory for a client using a mocked HTTP session.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", NUT_PATH): NUT_DATA})

    assert await client.get_nut_ups_status() == NUT_DATA

    assert router.count("GET", NUT_PATH) == 1
    assert _live_workers(client)

    await client.async_close()

    assert not _live_workers(client)


async def test_zero_pacing_lets_sequential_reads_finish_promptly(
    make_client: MakeClientFactory,
) -> None:
    """Many reads on the real queue finish quickly because the pacing delay is zero.

    Args:
        make_client (MakeClientFactory): Factory for a client using a mocked HTTP session.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", NUT_PATH): NUT_DATA})

    async with asyncio.timeout(2):
        for _ in range(20):
            assert await client.get_nut_ups_status() == NUT_DATA

    assert router.count("GET", NUT_PATH) == 20


async def test_unrouted_request_is_recorded_and_answered_with_404(
    make_client: MakeClientFactory,
) -> None:
    """An unrouted request is recorded and treated by the client as a missing endpoint.

    Args:
        make_client (MakeClientFactory): Factory for a client using a mocked HTTP session.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session)

    assert await client.get_nut_ups_status() == {}
    assert await client.get_nut_ups_status() == {}

    # Only a 404 is cached as absent, so the second read sent no request.
    assert router.count("GET", NUT_PATH) == 1


async def test_unrouted_request_does_not_kill_the_worker(make_client: MakeClientFactory) -> None:
    """After an unrouted request the same client still gets answers for routed ones.

    Args:
        make_client (MakeClientFactory): Factory for a client using a mocked HTTP session.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", NUT_PATH): NUT_DATA})

    assert await client.get_smart() == []
    async with asyncio.timeout(2):
        assert await client.get_nut_ups_status() == NUT_DATA

    assert router.count("POST", SMART_PATH) == 1
    assert router.count("GET", NUT_PATH) == 1


async def test_session_error_reaches_caller_instead_of_hanging(
    make_client: MakeClientFactory,
) -> None:
    """A transport error raised by the session surfaces as a public error, not a hang.

    Args:
        make_client (MakeClientFactory): Factory for a client using a mocked HTTP session.
    """
    client, session = make_mock_session_client(partial(make_client, throw_errors=True))
    install_fake_router(
        client,
        session,
        routes={("GET", NUT_PATH): aiohttp.ClientConnectionError("connection reset")},
    )

    async with asyncio.timeout(2):
        with pytest.raises(OPNsenseConnectionError):
            await client.get_nut_ups_status()
