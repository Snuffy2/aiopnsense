"""Tests for the autouse fixture that stubs API request queues."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from tests.conftest import MakeClientFactory

PATH = "/api/test/queued-read"


async def test_unanswered_queued_read_fails_fast(make_client: MakeClientFactory) -> None:
    """Report the method and path when a queued read has no answer.

    Args:
        make_client (MakeClientFactory): Factory for a client with stubbed workers.
    """
    client = make_client()

    with pytest.raises(pytest.fail.Exception, match=f"queued GET {PATH} was not answered"):
        async with asyncio.timeout(1):
            await client._get(PATH)


async def test_answered_queued_read_returns_payload(
    make_client: MakeClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Allow a test double to answer a queued read.

    Args:
        make_client (MakeClientFactory): Factory for a client with stubbed workers.
        monkeypatch (pytest.MonkeyPatch): Fixture that installs the test double.
    """
    client = make_client()
    payload = {"status": "ok"}
    monkeypatch.setattr(client, "_queue_request", AsyncMock(return_value=payload))

    async with asyncio.timeout(1):
        assert await client._get(PATH) == payload
