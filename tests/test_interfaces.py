"""Tests for OPNsense interface assignment updates."""

import asyncio
from collections.abc import Callable, MutableMapping
from types import TracebackType
from typing import Any, Self
from unittest.mock import AsyncMock

import aiohttp
import pytest

from aiopnsense import OPNsenseClient, OPNsenseConnectionError, OPNsenseInvalidArgument
from tests.conftest import FakeResponse, make_mock_session_client

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


def install_responses(
    client: OPNsenseClient,
    session: Any,
    *,
    get_payloads: list[Any] | None = None,
    post_payloads: list[Any] | None = None,
) -> list[tuple[str, str, Any]]:
    """Install fake transport responses and record requests at the HTTP boundary.

    Args:
        client (OPNsenseClient): Client whose queued requests will use direct transport.
        session (Any): Mock aiohttp session to configure.
        get_payloads (list[Any] | None): JSON payloads or errors returned by GET calls.
        post_payloads (list[Any] | None): JSON payloads or errors returned by POST calls.

    Returns:
        list[tuple[str, str, Any]]: Method, URL, and JSON body for each request.
    """
    requests: list[tuple[str, str, Any]] = []
    gets = list(get_payloads or [])
    posts = list(post_payloads or [])

    def get(url: str, **kwargs: Any) -> FakeResponse:
        """Return the next configured GET response.

        Args:
            url (str): Requested endpoint URL.
            kwargs (Any): Request parameters passed by the HTTP transport.

        Returns:
            FakeResponse: Configured response for the request.

        Raises:
            payload: Configured transport error for this request.
        """
        requests.append(("GET", url, kwargs.get("json")))
        payload = gets.pop(0) if gets else {}
        if isinstance(payload, BaseException):
            raise payload
        return FakeResponse(json_payload=payload)

    def post(url: str, **kwargs: Any) -> FakeResponse:
        """Return the next configured POST response.

        Args:
            url (str): Requested endpoint URL.
            kwargs (Any): Request parameters passed by the HTTP transport.

        Returns:
            FakeResponse: Configured response for the request.

        Raises:
            payload: Configured transport error for this request.
        """
        requests.append(("POST", url, kwargs.get("json")))
        payload = posts.pop(0) if posts else {}
        if isinstance(payload, BaseException):
            raise payload
        return FakeResponse(json_payload=payload)

    async def get_immediately(path: str) -> MutableMapping[str, Any] | list | None:
        """Run GET transport work directly for the fake session.

        Args:
            path (str): API endpoint path to request.

        Returns:
            MutableMapping[str, Any] | list | None: Decoded response payload.
        """
        response = await client._do_get(path, caller="interface test")
        return None if isinstance(response, str) else response

    async def post_immediately(
        path: str, payload: MutableMapping[str, Any] | None = None
    ) -> MutableMapping[str, Any] | list | None:
        """Run POST transport work directly for the fake session.

        Args:
            path (str): API endpoint path to request.
            payload (MutableMapping[str, Any] | None): JSON body to send.

        Returns:
            MutableMapping[str, Any] | list | None: Decoded response payload.
        """
        return await client._do_post(path, payload=payload, caller="interface test")

    session.get = get
    session.post = post
    client._get = get_immediately
    client._post = post_immediately
    return requests


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "current", "desired"),
    [("on", "0", "1"), ("off", "1", "0"), (None, "1", "0"), (None, "0", "1")],
)
async def test_toggle_interface_saves_then_applies(
    make_client: ClientType, target: str | None, current: str, desired: str
) -> None:
    """Set explicit or inverted interface state and apply after OPNsense saves it.

    Args:
        make_client (ClientType): Fixture factory returning OPNsense clients.
        target (str | None): Explicit target state or omitted toggle action.
        current (str): Configured enable flag returned by the router.
        desired (str): Expected enable flag sent to the router.
    """
    client, session = make_mock_session_client(make_client)
    client.get_host_firmware_version = AsyncMock(return_value="26.7.6")
    item = {"interface": {**INTERFACE_ITEM["interface"], "enable": current}}
    requests = install_responses(
        client,
        session,
        get_payloads=[{"status": "ok"}, item],
        post_payloads=[{"result": "saved"}, {"status": "ok"}],
    )
    try:
        assert await client.toggle_interface("opt8", target) is True
        assert [request[1].removeprefix("http://localhost") for request in requests] == [
            "/api/interfaces/assignment/pending",
            "/api/interfaces/assignment/get_item/opt8",
            "/api/interfaces/assignment/set_item/opt8",
            "/api/interfaces/assignment/reconfigure",
        ]
        assert requests[2][2] == {"interface": {"enable": desired}}
        assert requests[3][2] == {}
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("target", "current"), [("on", "1"), ("off", "0")])
async def test_toggle_interface_noop_does_not_write(
    make_client: ClientType, target: str, current: str
) -> None:
    """Return success without writes when the requested state already matches.

    Args:
        make_client (ClientType): Fixture factory returning OPNsense clients.
        target (str): Explicit target state or omitted toggle action.
        current (str): Configured enable flag returned by the router.
    """
    client, session = make_mock_session_client(make_client)
    client.get_host_firmware_version = AsyncMock(return_value="26.7.6")
    item = {"interface": {**INTERFACE_ITEM["interface"], "enable": current}}
    requests = install_responses(client, session, get_payloads=[{"status": "ok"}, item])
    try:
        assert await client.toggle_interface("opt8", target) is True
        assert all(method == "GET" for method, _url, _body in requests)
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("firmware", ["26.7.5", None, "not-a-version"])
async def test_toggle_interface_requires_supported_firmware(
    make_client: ClientType, firmware: str | None
) -> None:
    """Refuse updates without a known supported firmware version.

    Args:
        make_client (ClientType): Fixture factory returning OPNsense clients.
        firmware (str | None): Installed firmware version reported by the client.
    """
    client, session = make_mock_session_client(make_client)
    client.get_host_firmware_version = AsyncMock(return_value=firmware)
    requests = install_responses(client, session)
    try:
        assert await client.toggle_interface("opt8", "off") is False
        assert requests == []
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [{"status": "pending"}, {}, {"status": "unknown"}])
async def test_toggle_interface_refuses_pending_or_unreadable_queue(
    make_client: ClientType, pending: dict[str, str]
) -> None:
    """Avoid touching the assignment item while the global queue is not clear.

    Args:
        make_client (ClientType): Fixture factory returning OPNsense clients.
        pending (dict[str, str]): Response reporting the global interface queue state.
    """
    client, session = make_mock_session_client(make_client)
    client.get_host_firmware_version = AsyncMock(return_value="26.7.6")
    requests = install_responses(client, session, get_payloads=[pending])
    try:
        assert await client.toggle_interface("opt8", "off") is False
        assert len(requests) == 1
        assert requests[0][1].endswith("/api/interfaces/assignment/pending")
    finally:
        await client.async_close()


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
    make_client: ClientType, item: dict[str, Any]
) -> None:
    """Do not write when the item identity or current enable state is unreadable.

    Args:
        make_client (ClientType): Fixture factory returning OPNsense clients.
        item (dict[str, Any]): Interface assignment response to validate.
    """
    client, session = make_mock_session_client(make_client)
    client.get_host_firmware_version = AsyncMock(return_value="26.7.6")
    requests = install_responses(client, session, get_payloads=[{"status": "ok"}, item])
    try:
        assert await client.toggle_interface("opt8", "off") is False
        assert all(method == "GET" for method, _url, _body in requests)
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("save_response", [{}, {"result": "failed"}, {"result": "unknown"}])
async def test_toggle_interface_does_not_apply_failed_save(
    make_client: ClientType, save_response: dict[str, str]
) -> None:
    """Never reconfigure after the assignment endpoint fails to save.

    Args:
        make_client (ClientType): Fixture factory returning OPNsense clients.
        save_response (dict[str, str]): Response returned when saving the enable flag.
    """
    client, session = make_mock_session_client(make_client)
    client.get_host_firmware_version = AsyncMock(return_value="26.7.6")
    requests = install_responses(
        client,
        session,
        get_payloads=[{"status": "ok"}, INTERFACE_ITEM],
        post_payloads=[save_response],
    )
    try:
        assert await client.toggle_interface("opt8", "off") is False
        assert all(not url.endswith("/reconfigure") for _method, url, _body in requests)
    finally:
        await client.async_close()


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
    [{}, {"status": "failed"}, aiohttp.ServerTimeoutError("apply timed out")],
)
async def test_toggle_interface_reports_apply_failure(
    make_client: ClientType,
    apply_response: dict[str, Any] | aiohttp.ServerTimeoutError,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Refuse retry while failed apply leaves pending work for operator review.

    Args:
        make_client (ClientType): Fixture factory returning OPNsense clients.
        apply_response (dict[str, Any] | aiohttp.ServerTimeoutError): Apply response or timeout.
        caplog (pytest.LogCaptureFixture): Captured warning logs from the client.
    """
    client, session = make_mock_session_client(make_client)
    client.get_host_firmware_version = AsyncMock(return_value="26.7.6")
    requests = install_responses(
        client,
        session,
        get_payloads=[{"status": "ok"}, INTERFACE_ITEM, {"status": "pending"}],
        post_payloads=[{"result": "saved"}, apply_response],
    )
    try:
        assert await client.toggle_interface("opt8", "off") is False
        assert await client.toggle_interface("opt8", "off") is False
        assert [url.removeprefix("http://localhost") for _method, url, _body in requests] == [
            "/api/interfaces/assignment/pending",
            "/api/interfaces/assignment/get_item/opt8",
            "/api/interfaces/assignment/set_item/opt8",
            "/api/interfaces/assignment/reconfigure",
            "/api/interfaces/assignment/pending",
        ]
        assert "Review pending configuration in the OPNsense UI before retrying" in caplog.text
    finally:
        await client.async_close()


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
    make_client: ClientType, if_name: str, target: Any, throw_errors: bool
) -> None:
    """Apply the client error convention to invalid arguments without requests.

    Args:
        make_client (ClientType): Fixture factory returning OPNsense clients.
        if_name (str): Interface identifier supplied to the public method.
        target (Any): Explicit target state or omitted toggle action.
        throw_errors (bool): Whether the client propagates public errors.
    """
    client, session = make_mock_session_client(make_client)
    requests = install_responses(client, session)
    client.toggle_throwing_errors(throw_errors)
    try:
        if throw_errors:
            with pytest.raises(OPNsenseInvalidArgument):
                await client.toggle_interface(if_name, target)
        else:
            assert await client.toggle_interface(if_name, target) is None
        assert requests == []
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("get_payloads", "post_payloads", "expected_reconfigure_count"),
    [
        ([aiohttp.ClientError("pending read failed")], [], 0),
        ([{"status": "ok"}, INTERFACE_ITEM], [aiohttp.ClientError("save failed")], 0),
        (
            [{"status": "ok"}, INTERFACE_ITEM],
            [{"result": "saved"}, aiohttp.ClientError("apply failed")],
            1,
        ),
    ],
)
@pytest.mark.parametrize("throw_errors", [False, True])
async def test_toggle_interface_transport_failures_stop_at_the_failed_step(
    make_client: ClientType,
    get_payloads: list[Any],
    post_payloads: list[Any],
    expected_reconfigure_count: int,
    throw_errors: bool,
) -> None:
    """Respect error mode and stop progressing after a transport failure.

    Args:
        make_client (ClientType): Fixture factory returning OPNsense clients.
        get_payloads (list[Any]): Configured GET response payloads or transport errors.
        post_payloads (list[Any]): Configured POST response payloads or transport errors.
        expected_reconfigure_count (int): Expected number of attempted apply requests.
        throw_errors (bool): Whether the client propagates public errors.
    """
    client, session = make_mock_session_client(make_client)
    client.get_host_firmware_version = AsyncMock(return_value="26.7.6")
    requests = install_responses(
        client, session, get_payloads=get_payloads, post_payloads=post_payloads
    )
    client.toggle_throwing_errors(throw_errors)
    try:
        if throw_errors:
            with pytest.raises(OPNsenseConnectionError):
                await client.toggle_interface("opt8", "off")
        else:
            assert await client.toggle_interface("opt8", "off") is False
        reconfigure_count = sum(url.endswith("/reconfigure") for _method, url, _body in requests)
        assert reconfigure_count == expected_reconfigure_count
    finally:
        await client.async_close()
