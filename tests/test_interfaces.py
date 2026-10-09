"""Tests for OPNsense interface assignment updates."""

from collections.abc import Callable, MutableMapping
from typing import Any
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
@pytest.mark.parametrize("apply_response", [{}, {"status": "failed"}])
async def test_toggle_interface_reports_apply_failure(
    make_client: ClientType, apply_response: dict[str, str]
) -> None:
    """Report failed apply while leaving any queued state for operator handling.

    Args:
        make_client (ClientType): Fixture factory returning OPNsense clients.
        apply_response (dict[str, str]): Response returned when applying interface changes.
    """
    client, session = make_mock_session_client(make_client)
    client.get_host_firmware_version = AsyncMock(return_value="26.7.6")
    requests = install_responses(
        client,
        session,
        get_payloads=[{"status": "ok"}, INTERFACE_ITEM],
        post_payloads=[{"result": "saved"}, apply_response],
    )
    try:
        assert await client.toggle_interface("opt8", "off") is False
        assert any(url.endswith("/reconfigure") for _method, url, _body in requests)
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
async def test_toggle_interface_rejects_invalid_arguments(
    make_client: ClientType, if_name: str, target: Any
) -> None:
    """Reject physical names, path content, and malformed targets without toggling.

    Args:
        make_client (ClientType): Fixture factory returning OPNsense clients.
        if_name (str): Interface identifier supplied to the public method.
        target (Any): Explicit target state or omitted toggle action.
    """
    client, session = make_mock_session_client(make_client)
    requests = install_responses(client, session)
    client.toggle_throwing_errors(True)
    try:
        with pytest.raises(OPNsenseInvalidArgument):
            await client.toggle_interface(if_name, target)
        assert requests == []
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_toggle_interface_transport_failure_uses_error_convention(
    make_client: ClientType,
) -> None:
    """Map GET transport failures according to the client's throw-errors setting.

    Args:
        make_client (ClientType): Fixture factory returning OPNsense clients.
    """
    client, session = make_mock_session_client(make_client)
    client.get_host_firmware_version = AsyncMock(return_value="26.7.6")
    install_responses(client, session, get_payloads=[aiohttp.ClientError("offline")])
    try:
        client.toggle_throwing_errors(True)
        with pytest.raises(OPNsenseConnectionError):
            await client.toggle_interface("opt8", "off")
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
async def test_toggle_interface_transport_failures_stop_at_the_failed_step(
    make_client: ClientType,
    get_payloads: list[Any],
    post_payloads: list[Any],
    expected_reconfigure_count: int,
) -> None:
    """Propagate transport errors and avoid progressing after a failed save.

    Args:
        make_client (ClientType): Fixture factory returning OPNsense clients.
        get_payloads (list[Any]): Configured GET response payloads or transport errors.
        post_payloads (list[Any]): Configured POST response payloads or transport errors.
        expected_reconfigure_count (int): Expected number of attempted apply requests.
    """
    client, session = make_mock_session_client(make_client)
    client.get_host_firmware_version = AsyncMock(return_value="26.7.6")
    requests = install_responses(
        client, session, get_payloads=get_payloads, post_payloads=post_payloads
    )
    client.toggle_throwing_errors(True)
    try:
        with pytest.raises(OPNsenseConnectionError):
            await client.toggle_interface("opt8", "off")
        reconfigure_count = sum(url.endswith("/reconfigure") for _method, url, _body in requests)
        assert reconfigure_count == expected_reconfigure_count
    finally:
        await client.async_close()
