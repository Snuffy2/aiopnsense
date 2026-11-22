"""Tests for OPNsense interface assignment updates."""

import asyncio
from collections.abc import Callable
import logging
from types import TracebackType
from typing import Any, Self
from unittest.mock import AsyncMock

import pytest

from aiopnsense import OPNsenseClient, OPNsenseConnectionError, OPNsenseInvalidArgument
from tests.conftest import FakeResponse, ScriptedRouter, make_mock_session_client

ClientType = Callable[..., OPNsenseClient]
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
    router: ScriptedRouter,
    *,
    item: Any = INTERFACE_ITEM,
    pending: Any = None,
    firmware: str | None = "26.7.6",
    **client_kwargs: Any,
) -> OPNsenseClient:
    """Create a client for a Router with a clear queue and one readable interface.

    Args:
        router (ScriptedRouter): Router to script and create the client from.
        item (Any): Reply to reading the interface item.
        pending (Any): Reply to reading the pending queue; defaults to a clear queue.
        firmware (str | None): Installed firmware version reported by the client.
        client_kwargs (Any): Keyword arguments forwarded to the client factory.

    Returns:
        OPNsenseClient: Client whose firmware lookup is stubbed.
    """
    router.reply(PENDING, {"status": "ok"} if pending is None else pending, method="GET")
    router.reply(GET_ITEM, item, method="GET")
    client = router.client(**client_kwargs)
    client.get_host_firmware_version = AsyncMock(return_value=firmware)
    return client


def saves(router: ScriptedRouter) -> list[Any]:
    """Return the bodies of the saves the Router received.

    Args:
        router (ScriptedRouter): Router that recorded the requests.

    Returns:
        list[Any]: Payload of each save request.
    """
    return [r.payload for r in router.requests if r.path == SET_ITEM]


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
    scripted_router: ScriptedRouter, target: str | None, current: str, desired: str
) -> None:
    """Set explicit or inverted interface state and apply after OPNsense saves it.

    Args:
        scripted_router (ScriptedRouter): Scripted Router fixture.
        target (str | None): Explicit target state or flip.
        current (str): Configured enable flag returned by the router.
        desired (str): Expected enable flag sent to the router.
    """
    item = {"interface": {**INTERFACE_ITEM["interface"], "enable": current}}
    client = interface_client(scripted_router, item=item)
    scripted_router.reply(SET_ITEM, {"result": "saved"})
    scripted_router.reply(RECONFIGURE, {"status": "ok"})

    assert await client.toggle_interface("opt8", target) is True
    assert saves(scripted_router) == [{"interface": {"enable": desired}}]
    assert scripted_router.requested(RECONFIGURE)


@pytest.mark.asyncio
@pytest.mark.parametrize(("target", "current"), [("on", "1"), ("off", "0")])
async def test_toggle_interface_noop_does_not_write(
    scripted_router: ScriptedRouter, target: str, current: str
) -> None:
    """Return success without a save or apply when the requested state already matches.

    Args:
        scripted_router (ScriptedRouter): Scripted Router fixture.
        target (str): Explicit target state.
        current (str): Configured enable flag returned by the router.
    """
    item = {"interface": {**INTERFACE_ITEM["interface"], "enable": current}}
    client = interface_client(scripted_router, item=item)

    assert await client.toggle_interface("opt8", target) is True
    assert not scripted_router.requested(SET_ITEM)
    assert not scripted_router.requested(RECONFIGURE)


@pytest.mark.asyncio
@pytest.mark.parametrize("firmware", ["26.7.5", None, "not-a-version"])
async def test_toggle_interface_requires_supported_firmware(
    scripted_router: ScriptedRouter, firmware: str | None
) -> None:
    """Refuse updates without a known supported firmware version.

    Args:
        scripted_router (ScriptedRouter): Scripted Router fixture.
        firmware (str | None): Installed firmware version reported by the client.
    """
    client = interface_client(scripted_router, firmware=firmware)

    assert await client.toggle_interface("opt8", "off") is False
    assert scripted_router.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [{"status": "pending"}, {}, {"status": "unknown"}])
async def test_toggle_interface_refuses_pending_or_unreadable_queue(
    scripted_router: ScriptedRouter, pending: dict[str, str]
) -> None:
    """Avoid saving while the global queue is not clear.

    Args:
        scripted_router (ScriptedRouter): Scripted Router fixture.
        pending (dict[str, str]): Response reporting the global interface queue state.
    """
    client = interface_client(scripted_router, pending=pending)

    assert await client.toggle_interface("opt8", "off") is False
    assert not scripted_router.requested(SET_ITEM)
    assert not scripted_router.requested(RECONFIGURE)


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
    scripted_router: ScriptedRouter, item: dict[str, Any]
) -> None:
    """Do not save when the item identity or current enable state is unreadable.

    Args:
        scripted_router (ScriptedRouter): Scripted Router fixture.
        item (dict[str, Any]): Interface assignment response to validate.
    """
    client = interface_client(scripted_router, item=item)

    assert await client.toggle_interface("opt8", "off") is False
    assert not scripted_router.requested(SET_ITEM)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "save_response",
    [None, {}, {"result": "failed"}, {"result": "unknown"}, {"result": "Saved"}],
)
async def test_toggle_interface_does_not_apply_unacknowledged_save(
    scripted_router: ScriptedRouter, save_response: dict[str, str] | None
) -> None:
    """Never reconfigure unless the Router reports exactly ``saved``.

    Args:
        scripted_router (ScriptedRouter): Scripted Router fixture.
        save_response (dict[str, str] | None): Response returned when saving the enable flag.
    """
    client = interface_client(scripted_router)
    scripted_router.reply(SET_ITEM, save_response)

    assert await client.toggle_interface("opt8", "off") is False
    assert not scripted_router.requested(RECONFIGURE)


@pytest.mark.asyncio
async def test_toggle_interface_cancellation_during_save_leaves_change_pending(
    make_client: ClientType,
) -> None:
    """Finish an in-flight save after cancellation without queuing reconfigure.

    Args:
        make_client (ClientType): Fixture factory returning OPNsense clients.
    """
    client, session = make_mock_session_client(make_client)
    client.get_host_firmware_version = AsyncMock(return_value="26.7.6")
    loop = asyncio.get_running_loop()
    save_started = asyncio.Event()
    release_save = asyncio.Event()
    save_finished = asyncio.Event()
    requests: list[str] = []

    class BlockedSaveResponse(FakeResponse):
        """Pause the save response until the test releases the transport."""

        async def __aenter__(self) -> Self:
            """Signal that the save is in flight, then wait for release.

            Returns:
                Self: This response after the test releases the transport.
            """
            save_started.set()
            await release_save.wait()
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> bool:
            """Signal that the save transport context has completed.

            Args:
                exc_type (type[BaseException] | None): Exception type from the context.
                exc (BaseException | None): Exception raised in the context, if any.
                tb (TracebackType | None): Traceback for an exception from the context.

            Returns:
                bool: False so exceptions are not suppressed.
            """
            save_finished.set()
            return False

    def get(url: str, **kwargs: Any) -> FakeResponse:
        """Return the pending state or interface item through real transport.

        Args:
            url (str): Requested endpoint URL.
            kwargs (Any): Request options passed by the HTTP transport.

        Returns:
            FakeResponse: Configured response for the endpoint.
        """
        del kwargs
        path = url.removeprefix("http://localhost")
        requests.append(path)
        if path.endswith("/pending"):
            return FakeResponse(json_payload={"status": "ok"})
        return FakeResponse(json_payload=INTERFACE_ITEM)

    def post(url: str, **kwargs: Any) -> FakeResponse:
        """Record the queued save and return its blocked response.

        Args:
            url (str): Requested endpoint URL.
            kwargs (Any): Request options passed by the HTTP transport.

        Returns:
            FakeResponse: Response blocked until the test releases the save.
        """
        del kwargs
        path = url.removeprefix("http://localhost")
        requests.append(path)
        return BlockedSaveResponse(json_payload={"result": "saved"})

    session.get = get
    session.post = post
    client._max_workers = 1
    worker = loop.create_task(client._process_queue())
    client._workers = [worker]
    caller = loop.create_task(client.toggle_interface("opt8", "off"))
    try:
        await asyncio.wait_for(save_started.wait(), timeout=2)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller

        release_save.set()
        await asyncio.wait_for(save_finished.wait(), timeout=2)

        assert requests == [
            "/api/interfaces/assignment/pending",
            "/api/interfaces/assignment/get_item/opt8",
            "/api/interfaces/assignment/set_item/opt8",
        ]
    finally:
        release_save.set()
        if not caller.done():
            caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        await asyncio.wait_for(client.async_close(), timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "apply_response",
    [None, {}, {"status": "failed"}],
)
async def test_toggle_interface_reports_apply_failure(
    scripted_router: ScriptedRouter,
    apply_response: dict[str, Any] | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Return False and warn when a saved change's apply is not confirmed.

    Args:
        scripted_router (ScriptedRouter): Scripted Router fixture.
        apply_response (dict[str, Any] | None): Apply response, or None for a failed request.
        caplog (pytest.LogCaptureFixture): Captured warning logs from the client.
    """
    client = interface_client(scripted_router)
    scripted_router.reply(SET_ITEM, {"result": "saved"})
    scripted_router.reply(RECONFIGURE, apply_response)

    assert await client.toggle_interface("opt8", "off") is False
    assert any(
        record.levelno == logging.WARNING
        and "opt8" in record.getMessage()
        and "uncertain" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_toggle_interface_failed_save_does_not_warn_about_apply(
    scripted_router: ScriptedRouter, caplog: pytest.LogCaptureFixture
) -> None:
    """An unacknowledged save is not reported as an uncertain apply.

    Args:
        scripted_router (ScriptedRouter): Scripted Router fixture.
        caplog (pytest.LogCaptureFixture): Captured warning logs from the client.
    """
    client = interface_client(scripted_router)
    scripted_router.reply(SET_ITEM, {"result": "failed"})

    assert await client.toggle_interface("opt8", "off") is False
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


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
    scripted_router: ScriptedRouter, if_name: str, target: Any, throw_errors: bool
) -> None:
    """Apply the client error convention to invalid arguments without requests.

    Args:
        scripted_router (ScriptedRouter): Scripted Router fixture.
        if_name (str): Interface identifier supplied to the public method.
        target (Any): Explicit target state or omitted toggle action.
        throw_errors (bool): Whether the client propagates public errors.
    """
    client = interface_client(scripted_router, throw_errors=throw_errors)
    if throw_errors:
        with pytest.raises(OPNsenseInvalidArgument):
            await client.toggle_interface(if_name, target)
    else:
        assert await client.toggle_interface(if_name, target) is None
    assert scripted_router.requests == []


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
    scripted_router: ScriptedRouter,
    failing: str,
    method: str,
    expect_reconfigure: bool,
    throw_errors: bool,
) -> None:
    """Respect error mode and stop progressing after a transport failure.

    Args:
        scripted_router (ScriptedRouter): Scripted Router fixture.
        failing (str): Endpoint whose transport fails.
        method (str): HTTP method of the failing endpoint.
        expect_reconfigure (bool): Whether the apply request is expected to be sent.
        throw_errors (bool): Whether the client propagates public errors.
    """
    client = interface_client(scripted_router, throw_errors=throw_errors)
    scripted_router.reply(SET_ITEM, {"result": "saved"})
    failure = OPNsenseConnectionError("transport failed") if throw_errors else None
    scripted_router.reply(failing, failure, method=method)

    if throw_errors:
        with pytest.raises(OPNsenseConnectionError):
            await client.toggle_interface("opt8", "off")
    else:
        assert await client.toggle_interface("opt8", "off") is False
    assert scripted_router.requested(RECONFIGURE) is expect_reconfigure
