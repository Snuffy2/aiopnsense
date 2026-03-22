"""Tests for `aiopnsense.helpers` utility and decorator helpers."""

from datetime import datetime, timezone
from typing import Any, Callable, NoReturn

import aiohttp
import pytest

from aiopnsense import OPNsenseClient, helpers as pyopnsense_helpers
from tests.conftest import make_mock_session_client

ClientType = Callable[..., OPNsenseClient]


def test_human_friendly_duration() -> None:
    """Convert seconds into a human-friendly duration string."""
    assert pyopnsense_helpers.human_friendly_duration(65) == "1 minute, 5 seconds"
    assert pyopnsense_helpers.human_friendly_duration(0) == "0 seconds"
    assert "month" in pyopnsense_helpers.human_friendly_duration(2419200)


def test_human_friendly_duration_singular_and_plural() -> None:
    """Verify singular and plural forms for all supported units.

    This covers seconds, minutes, hours, days, weeks and months and ensures
    the function emits the singular form when the value is 1 and plural
    otherwise.
    """
    # seconds
    assert pyopnsense_helpers.human_friendly_duration(1) == "1 second"
    assert pyopnsense_helpers.human_friendly_duration(2) == "2 seconds"

    # minutes + seconds
    assert pyopnsense_helpers.human_friendly_duration(60) == "1 minute"
    assert pyopnsense_helpers.human_friendly_duration(61) == "1 minute, 1 second"

    # hours
    assert pyopnsense_helpers.human_friendly_duration(3600) == "1 hour"
    assert pyopnsense_helpers.human_friendly_duration(7200) == "2 hours"

    # days
    assert pyopnsense_helpers.human_friendly_duration(86400) == "1 day"

    # weeks
    assert pyopnsense_helpers.human_friendly_duration(604800) == "1 week"
    assert pyopnsense_helpers.human_friendly_duration(1209600) == "2 weeks"

    # months (28-day month used in implementation)
    assert pyopnsense_helpers.human_friendly_duration(2419200) == "1 month"
    assert pyopnsense_helpers.human_friendly_duration(4838400) == "2 months"


def test_get_ip_key() -> None:
    """Compute sorting key for IP addresses across IPv4, IPv6, and invalid forms."""
    assert pyopnsense_helpers.get_ip_key({"address": "192.168.1.1"})[0] == 0
    assert pyopnsense_helpers.get_ip_key({"address": "::1"})[0] == 1
    assert pyopnsense_helpers.get_ip_key({"address": "notanip"})[0] == 2
    assert pyopnsense_helpers.get_ip_key({"address": "notanip"}) == (2, "")
    assert pyopnsense_helpers.get_ip_key({})[0] == 3
    assert pyopnsense_helpers.get_ip_key({}) == (3, "")


def test_dict_get() -> None:
    """Retrieve nested values from dicts and lists using dotted paths."""
    data = {"a": {"b": {"c": 1}}, "x": [0, 1, 2]}
    assert pyopnsense_helpers.dict_get(data, "a.b.c") == 1
    assert pyopnsense_helpers.dict_get(data, "x.1") == 1
    assert pyopnsense_helpers.dict_get(data, "x.10", default=42) == 42
    assert pyopnsense_helpers.dict_get({"a": {"b": [10, {"c": 3}]}}, "a.b") == [10, {"c": 3}]
    assert pyopnsense_helpers.dict_get(data, "missing.path", default=5) == 5


def test_timestamp_to_datetime() -> None:
    """Convert timestamp integers to datetime objects, handling None."""
    ts = int(datetime.now(tz=timezone.utc).timestamp())
    dt = pyopnsense_helpers.timestamp_to_datetime(ts)
    assert isinstance(dt, datetime)
    assert dt.tzinfo is not None
    assert pyopnsense_helpers.timestamp_to_datetime(None) is None


def test_try_to_int_and_float() -> None:
    """Coerce numeric-like strings to int/float with defaults."""
    assert pyopnsense_helpers.try_to_int("5") == 5
    assert pyopnsense_helpers.try_to_int(None, 7) == 7
    assert pyopnsense_helpers.try_to_float("5.5") == 5.5
    assert pyopnsense_helpers.try_to_float(None, 3.3) == 3.3


def test_get_ip_key_sorting() -> None:
    """Sort IP-like items using get_ip_key ordering."""
    items = [
        {"address": "192.168.1.2"},
        {"address": "::1"},
        {"address": "notanip"},
        {},
    ]
    sorted_items = sorted(items, key=pyopnsense_helpers.get_ip_key)
    assert sorted_items[0]["address"] == "192.168.1.2"
    assert sorted_items[1]["address"] == "::1"
    assert sorted_items[2]["address"] == "notanip"
    assert sorted_items[3] == {}


@pytest.mark.asyncio
async def test_log_errors_decorator_re_raise_and_suppress() -> None:
    """The _log_errors decorator should re-raise when self._initial is True, otherwise suppress."""

    class Dummy:
        def __init__(self, initial: bool):
            """Initialize the Dummy instance.

            Args:
                initial (bool): Whether the client runs in initial-connectivity mode.
            """
            self._initial = initial

        @pyopnsense_helpers._log_errors
        async def boom(self) -> None:
            """Raise RuntimeError for testing error handling."""
            raise RuntimeError("boom")

    # When not initial, errors are logged and suppressed (function returns None)
    d = Dummy(initial=False)
    res = await d.boom()
    assert res is None

    # When initial, errors are re-raised
    d2 = Dummy(initial=True)
    with pytest.raises(RuntimeError):
        await d2.boom()


@pytest.mark.asyncio
async def test_log_errors_timeout_re_raise_and_suppress(make_client: ClientType) -> None:
    """Verify ``_log_errors`` re-raises or suppresses ``TimeoutError`` by init mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates timeout error propagation behavior.
    """
    client, _ = make_mock_session_client(make_client, url="http://x")
    try:

        async def raising_timeout(*args: Any, **kwargs: Any) -> NoReturn:
            """Raising timeout.

            Args:
                *args (Any): Positional arguments forwarded to the wrapped callable.
                **kwargs (Any): Keyword arguments forwarded to the wrapped callable.

            Returns:
                NoReturn: This helper always raises ``TimeoutError``.
            """
            raise TimeoutError("boom")

        # wrap the coroutine with the decorator
        decorated = pyopnsense_helpers._log_errors(raising_timeout)

        # When initial is True we expect the TimeoutError to propagate
        client._initial = True
        with pytest.raises(TimeoutError):
            await decorated(client)

        # When initial is False the decorator should suppress TimeoutError and return None
        client._initial = False
        res = await decorated(client)
        assert res is None
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_log_errors_server_timeout_re_raise_and_suppress(make_client: ClientType) -> None:
    """Verify ``_log_errors`` re-raises or suppresses ``ServerTimeoutError`` by init mode.

    Args:
        make_client (ClientType): Fixture factory returning ``OPNsenseClient`` instances.

    Returns:
        None: This test validates server-timeout error propagation behavior.
    """
    client, _ = make_mock_session_client(make_client, url="http://x")
    try:

        async def raising_server_timeout(*args: Any, **kwargs: Any) -> Any:
            """Raising server timeout.

            Args:
                *args (Any): Positional arguments forwarded to the wrapped callable.
                **kwargs (Any): Keyword arguments forwarded to the wrapped callable.

            Returns:
                Any: Value produced by the wrapped callable.
            """
            raise aiohttp.ServerTimeoutError("srv")

        decorated = pyopnsense_helpers._log_errors(raising_server_timeout)

        client._initial = True
        with pytest.raises(aiohttp.ServerTimeoutError):
            await decorated(client)

        client._initial = False
        assert await decorated(client) is None
    finally:
        await client.async_close()
