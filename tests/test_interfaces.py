"""Tests for OPNsense interface assignment updates."""

import asyncio
from collections.abc import MutableMapping
import logging
from typing import Any

import aiohttp
import pytest

from aiopnsense import OPNsenseClient, OPNsenseConnectionError, OPNsenseInvalidArgument
from tests.conftest import FakeRouter, HeldResponse, MakeClientFactory, make_router_client

SERVER_ERROR = 500
"""HTTP status of a Router reply that fails the request."""
INTERFACE_ITEM = {
    "interface": {
        "identifier": "opt8",
        "descr": "Guest_150",
        "enable": "1",
        "if": "vlan150",
        "ipaddr": "192.0.2.1",
        "options": {"vlan_tag": "150", "vlan_parent": "igc0"},
        "pending_action": "",
    }
}


PENDING = "/api/interfaces/assignment/pending"
GET_ITEM = "/api/interfaces/assignment/get_item/opt8"
SET_ITEM = "/api/interfaces/assignment/set_item/opt8"
RECONFIGURE = "/api/interfaces/assignment/reconfigure"


def interface_client(
    make_client: MakeClientFactory,
    *,
    item: Any = INTERFACE_ITEM,
    pending: Any = None,
    firmware: str | None = "26.7.6",
    routes: MutableMapping[tuple[str, str], Any] | None = None,
    **client_kwargs: Any,
) -> tuple[OPNsenseClient, FakeRouter]:
    """Create a client for a Router with a clear queue and one readable interface.

    A request with no route is answered with 404 and recorded.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        item (Any): Reply to reading the interface item.
        pending (Any): Reply to reading the pending queue; defaults to a clear queue.
        firmware (str | None): Firmware version the Router reports, or ``None`` for a
            Router with no firmware status.
        routes (MutableMapping[tuple[str, str], Any] | None): Further routes keyed by
            ``(method, path)``, replacing the defaults of the same key.
        client_kwargs (Any): Keyword arguments forwarded to the client factory.

    Returns:
        tuple[OPNsenseClient, FakeRouter]: Client and the Router that records its requests.
    """
    return make_router_client(
        make_client,
        {
            ("GET", PENDING): {"status": "ok"} if pending is None else pending,
            ("GET", GET_ITEM): item,
            **(routes or {}),
        },
        firmware=firmware,
        **client_kwargs,
    )


def interface_requests(router: FakeRouter) -> list[tuple[str, str]]:
    """Return the interface assignment requests the Router received, in order.

    Args:
        router (FakeRouter): Router that recorded the requests.

    Returns:
        list[tuple[str, str]]: ``(method, path)`` of each interface assignment request.
    """
    return [call for call in router.calls() if call[1].startswith("/api/interfaces/assignment/")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "current", "desired"),
    [
        ("on", "0", "1"),
        ("off", "1", "0"),
        (None, "1", "0"),
        (None, "0", "1"),
        ("toggle", "1", "0"),
    ],
)
async def test_toggle_interface_saves_then_applies(
    make_client: MakeClientFactory, target: str | None, current: str, desired: str
) -> None:
    """Set explicit or inverted interface state and apply after OPNsense saves it.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        target (str | None): Explicit target state or flip.
        current (str): Configured enable flag returned by the router.
        desired (str): Expected enable flag sent to the router.
    """
    item = {"interface": {**INTERFACE_ITEM["interface"], "enable": current}}
    client, router = interface_client(
        make_client,
        item=item,
        routes={("POST", SET_ITEM): {"result": "saved"}, ("POST", RECONFIGURE): {"status": "ok"}},
    )

    assert await client.toggle_interface("opt8", target) is True
    assert router.bodies("POST", SET_ITEM) == [{"interface": {"enable": desired}}]
    # One operation: the save is followed by exactly one apply.
    assert [request for request in interface_requests(router) if request[0] == "POST"] == [
        ("POST", SET_ITEM),
        ("POST", RECONFIGURE),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(("target", "current"), [("on", "1"), ("off", "0")])
async def test_toggle_interface_noop_does_not_write(
    make_client: MakeClientFactory, target: str, current: str
) -> None:
    """Return success without a save or apply when the requested state already matches.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        target (str): Explicit target state.
        current (str): Configured enable flag returned by the router.
    """
    item = {"interface": {**INTERFACE_ITEM["interface"], "enable": current}}
    client, router = interface_client(make_client, item=item)

    assert await client.toggle_interface("opt8", target) is True
    assert router.count("POST") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("firmware", ["26.7.5", None, "not-a-version"])
async def test_toggle_interface_requires_supported_firmware(
    make_client: MakeClientFactory, firmware: str | None
) -> None:
    """Refuse updates without a known supported firmware version.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        firmware (str | None): Firmware version the Router reports.
    """
    client, router = interface_client(make_client, firmware=firmware)

    assert await client.toggle_interface("opt8", "off") is False
    assert interface_requests(router) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [{"status": "pending"}, {}, {"status": "unknown"}])
async def test_toggle_interface_refuses_pending_or_unreadable_queue(
    make_client: MakeClientFactory, pending: dict[str, str]
) -> None:
    """Avoid saving while the global queue is not clear.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        pending (dict[str, str]): Response reporting the global interface queue state.
    """
    client, router = interface_client(make_client, pending=pending)

    assert await client.toggle_interface("opt8", "off") is False
    assert router.count("POST") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "item",
    [
        {"interface": {"identifier": "opt7", "enable": "1"}},
        {"interface": {"identifier": "opt8", "enable": "unknown"}},
        {"interface": []},
        {},
    ],
)
async def test_toggle_interface_refuses_unverified_items(
    make_client: MakeClientFactory, item: dict[str, Any]
) -> None:
    """Do not save when the item identity or current enable state is unreadable.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        item (dict[str, Any]): Interface assignment response to validate.
    """
    client, router = interface_client(make_client, item=item)

    assert await client.toggle_interface("opt8", "off") is False
    assert router.count("POST") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "save_response",
    [SERVER_ERROR, {}, {"result": "failed"}, {"result": "unknown"}, {"result": "Saved"}],
)
async def test_toggle_interface_does_not_apply_unacknowledged_save(
    make_client: MakeClientFactory, save_response: Any
) -> None:
    """Never reconfigure unless the Router reports exactly ``saved``.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        save_response (Any): Reply to saving the enable flag; a status fails the request.
    """
    client, router = interface_client(make_client, routes={("POST", SET_ITEM): save_response})

    assert await client.toggle_interface("opt8", "off") is False
    assert router.count("POST", SET_ITEM) == 1
    assert router.count("POST", RECONFIGURE) == 0


@pytest.mark.asyncio
async def test_toggle_interface_cancellation_during_save_leaves_change_pending(
    make_client: MakeClientFactory,
) -> None:
    """Finish an in-flight save after cancellation without queuing reconfigure.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
    """
    save = HeldResponse(json_payload={"result": "saved"})
    client, router = interface_client(
        make_client,
        routes={
            ("POST", SET_ITEM): save,
            ("POST", RECONFIGURE): {"status": "ok"},
        },
    )
    caller = asyncio.get_running_loop().create_task(client.toggle_interface("opt8", "off"))
    try:
        await asyncio.wait_for(save.entered.wait(), timeout=2)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller

        save.release.set()
        await asyncio.wait_for(save.exited.wait(), timeout=2)
        # Closing the client finishes any work still queued, so a reconfigure queued
        # by the cancelled caller would reach the Router before the check below.
        await asyncio.wait_for(client.async_close(), timeout=2)

        assert interface_requests(router) == [
            ("GET", PENDING),
            ("GET", GET_ITEM),
            ("POST", SET_ITEM),
        ]
    finally:
        save.release.set()
        if not caller.done():
            caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        await asyncio.wait_for(client.async_close(), timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "apply_response",
    [SERVER_ERROR, {}, {"status": "failed"}],
)
async def test_toggle_interface_reports_apply_failure(
    make_client: MakeClientFactory,
    apply_response: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Return False and warn when a saved change's apply is not confirmed.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        apply_response (Any): Apply reply; a status fails the request.
        caplog (pytest.LogCaptureFixture): Captured warning logs from the client.
    """
    client, _router = interface_client(
        make_client,
        routes={("POST", SET_ITEM): {"result": "saved"}, ("POST", RECONFIGURE): apply_response},
    )

    assert await client.toggle_interface("opt8", "off") is False
    assert any(
        record.levelno == logging.WARNING
        and "opt8" in record.getMessage()
        and "uncertain" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_toggle_interface_failed_save_does_not_warn_about_apply(
    make_client: MakeClientFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """An unacknowledged save is not reported as an uncertain apply.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        caplog (pytest.LogCaptureFixture): Captured warning logs from the client.
    """
    client, router = interface_client(
        make_client, routes={("POST", SET_ITEM): {"result": "failed"}}
    )

    assert await client.toggle_interface("opt8", "off") is False
    assert router.count("POST", RECONFIGURE) == 0
    assert not any(
        record.levelno == logging.WARNING
        and "opt8" in record.getMessage()
        and "uncertain" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("if_name", "target"),
    [
        ("em0", "on"),
        ("opt0", "on"),
        ("opt/1", "on"),
        ("", "on"),
        ("opt8", "enable"),
        ("opt8", []),
    ],
)
@pytest.mark.parametrize("throw_errors", [False, True])
async def test_toggle_interface_rejects_invalid_arguments(
    make_client: MakeClientFactory, if_name: str, target: Any, throw_errors: bool
) -> None:
    """Apply the client error convention to invalid arguments without requests.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        if_name (str): Interface identifier supplied to the public method.
        target (Any): Explicit target state or omitted toggle action.
        throw_errors (bool): Whether the client propagates public errors.
    """
    client, router = interface_client(make_client, throw_errors=throw_errors)
    if throw_errors:
        with pytest.raises(OPNsenseInvalidArgument):
            await client.toggle_interface(if_name, target)
    else:
        assert await client.toggle_interface(if_name, target) is None
    assert router.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failing", "method", "expect_reconfigure"),
    [
        (PENDING, "GET", False),
        (SET_ITEM, "POST", False),
        (RECONFIGURE, "POST", True),
    ],
)
@pytest.mark.parametrize("throw_errors", [False, True])
async def test_toggle_interface_transport_failures_stop_at_the_failed_step(
    make_client: MakeClientFactory,
    failing: str,
    method: str,
    expect_reconfigure: bool,
    throw_errors: bool,
) -> None:
    """Respect error mode and stop progressing after a transport failure.

    Args:
        make_client (MakeClientFactory): Client factory fixture.
        failing (str): Endpoint whose transport fails.
        method (str): HTTP method of the failing endpoint.
        expect_reconfigure (bool): Whether the apply request is expected to be sent.
        throw_errors (bool): Whether the client propagates public errors.
    """
    client, router = interface_client(
        make_client,
        throw_errors=throw_errors,
        routes={
            ("POST", SET_ITEM): {"result": "saved"},
            (method, failing): aiohttp.ClientConnectionError("transport failed"),
        },
    )

    async with asyncio.timeout(2):
        if throw_errors:
            with pytest.raises(OPNsenseConnectionError):
                await client.toggle_interface("opt8", "off")
        else:
            assert await client.toggle_interface("opt8", "off") is False
    assert router.count(method, failing) == 1
    assert (router.count("POST", RECONFIGURE) == 1) is expect_reconfigure
