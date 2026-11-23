"""Enforce the firmware endpoint rule from ADR 0002.

The request boundary in ``aiopnsense/_firmware_request_guard.py`` blocks forbidden requests
at runtime, however their path is spelled. The static checks here cover only what the
boundary cannot see: that every request goes through it, and that the check endpoint and
the update and upgrade action prefix stay in the methods allowed to dispatch them.
"""

import ast
from collections import Counter
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import aiopnsense
from aiopnsense import OPNsenseInvalidArgument
from tests.conftest import make_mock_session_client

PACKAGE = Path(__file__).resolve().parents[1] / "aiopnsense"
FIRMWARE_MODULE = "firmware.py"
CHECK_SCOPE = ("FirmwareMixin", "_request_firmware_check")
ACTION_SCOPES = {("FirmwareMixin", "install_firmware"), ("FirmwareMixin", "upgrade_firmware")}
DISPATCH_SCOPES = {
    "FIRMWARE_CHECK_ENDPOINT": {CHECK_SCOPE},
    "FIRMWARE_ACTION_ENDPOINT_PREFIX": ACTION_SCOPES,
}

BASE = "/api/core/firmware"
DISPATCH_LITERALS = {f"{BASE}/check", f"{BASE}/update", f"{BASE}/upgrade", f"{BASE}/"}


def _send_sites(client: aiopnsense.OPNsenseClient) -> dict[str, Callable[[str], Awaitable[Any]]]:
    """Return one caller for each place the client sends a request.

    Args:
        client (aiopnsense.OPNsenseClient): Client under test.

    Returns:
        dict[str, Callable[[str], Awaitable[Any]]]: Request callers by site and method.
    """

    async def stream(path: str) -> list[dict[str, Any]]:
        """Read every event from the streaming endpoint.

        Args:
            path (str): Requested path.

        Returns:
            list[dict[str, Any]]: Stream events.
        """
        return [event async for event in client._stream_json_events(path)]

    return {
        "get": client._do_get,
        "post": client._do_post,
        "get-from-stream": client._do_get_from_stream,
        "stream-events": stream,
        "firmware-get": client._firmware_request,
        "firmware-post": lambda path: client._firmware_request(path, post=True),
    }


GET_SITES = ("get", "get-from-stream", "stream-events", "firmware-get")
POST_SITES = ("post", "firmware-post")
FIRMWARE_SITES = ("firmware-get", "firmware-post")
EMPTY_RESULTS: dict[str, Any] = {
    "get": None,
    "post": None,
    "get-from-stream": {},
    "stream-events": [],
}
FORBIDDEN = [
    *((site, "info") for site in (*GET_SITES, *POST_SITES)),
    *((site, "status") for site in POST_SITES),
]


def _assert_nothing_sent(session: MagicMock) -> None:
    """Assert that the session sent no request.

    Args:
        session (MagicMock): Mocked aiohttp session.
    """
    session.get.assert_not_called()
    session.post.assert_not_called()


@pytest.mark.parametrize(
    ("site", "action", "throw_errors"),
    [
        *((site, action, True) for site, action in FORBIDDEN),
        *((site, action, False) for site, action in FORBIDDEN if site in FIRMWARE_SITES),
    ],
)
async def test_boundary_raises_for_forbidden_request(
    make_client: Callable[..., aiopnsense.OPNsenseClient],
    site: str,
    action: str,
    throw_errors: bool,
) -> None:
    """Raise the public error and send nothing at every request site.

    Firmware request sites raise whatever ``throw_errors`` is, because they have no empty value to return.

    Args:
        make_client (Callable[..., aiopnsense.OPNsenseClient]): Client factory.
        site (str): Request site under test.
        action (str): Forbidden firmware action.
        throw_errors (bool): Client ``throw_errors`` setting.
    """
    client, session = make_mock_session_client(make_client)
    client.toggle_throwing_errors(throw_errors)

    with pytest.raises(OPNsenseInvalidArgument, match="Blocked"):
        await _send_sites(client)[site](f"{BASE}/{action}")

    _assert_nothing_sent(session)


@pytest.mark.parametrize(
    ("site", "action"), [item for item in FORBIDDEN if item[0] in EMPTY_RESULTS]
)
async def test_boundary_returns_empty_for_forbidden_request_without_throwing(
    make_client: Callable[..., aiopnsense.OPNsenseClient], site: str, action: str
) -> None:
    """Return each site's empty value and send nothing when errors are not thrown.

    Args:
        make_client (Callable[..., aiopnsense.OPNsenseClient]): Client factory.
        site (str): Request site under test.
        action (str): Forbidden firmware action.
    """
    client, session = make_mock_session_client(make_client)

    assert await _send_sites(client)[site](f"{BASE}/{action}") == EMPTY_RESULTS[site]

    _assert_nothing_sent(session)


@pytest.mark.parametrize(
    ("method", "action"), [("get", "info"), ("post", "info"), ("post", "status")]
)
async def test_boundary_returns_blocked_outcome_for_forbidden_request(
    make_client: Callable[..., aiopnsense.OPNsenseClient], method: str, action: str
) -> None:
    """Return a transient outcome carrying the blocked error and send nothing.

    The outcome requests report a failed request as an outcome and leave the
    ``throw_errors`` decision to the endpoint module, so they never raise here.

    Args:
        make_client (Callable[..., aiopnsense.OPNsenseClient]): Client factory.
        method (str): HTTP method of the immediate outcome request.
        action (str): Forbidden firmware action.
    """
    client, session = make_mock_session_client(make_client)
    request = client._do_get_outcome if method == "get" else client._do_post_outcome

    outcome = await request(f"{BASE}/{action}")

    assert outcome.is_transient
    assert isinstance(outcome.error, OPNsenseInvalidArgument)
    assert "Blocked" in str(outcome.error)
    _assert_nothing_sent(session)


@pytest.mark.parametrize(
    "path",
    [
        f"{BASE}/info?force=1",
        f"{BASE}/info#top",
        f"{BASE}/info/",
        "/api/core//firmware//info",
        "/API/CORE/FIRMWARE/INFO",
        f"{BASE}/changelog/../info",
        f"{BASE}/%69nfo",
        f"{BASE}/info/extra",
    ],
    ids=["query", "fragment", "trailing", "repeated", "uppercase", "dot", "encoded", "parameter"],
)
async def test_boundary_blocks_every_spelling_of_the_path(
    make_client: Callable[..., aiopnsense.OPNsenseClient], path: str
) -> None:
    """Block the info request whatever the spelling of its path, through the get site.

    Args:
        make_client (Callable[..., aiopnsense.OPNsenseClient]): Client factory.
        path (str): Non-literal spelling of the info action path.
    """
    client, session = make_mock_session_client(make_client)
    client.toggle_throwing_errors(True)

    with pytest.raises(OPNsenseInvalidArgument, match="Blocked"):
        await _send_sites(client)["get"](path)

    _assert_nothing_sent(session)


LOCAL_URL = "http://localhost"
# yarl rejects this port, so the guard cannot judge the path and must pass the request on.
UNPARSABLE_URL = "http://localhost:notaport"


@pytest.mark.parametrize(
    ("url", "site", "path"),
    [
        (LOCAL_URL, "get", f"{BASE}/status"),
        (LOCAL_URL, "post", f"{BASE}/changelog/info"),
        (LOCAL_URL, "get", f"{BASE}/information"),
        (LOCAL_URL, "get", "/api/core/service/info"),
        (LOCAL_URL, "post", "/api/core/service/status"),
        (LOCAL_URL, "firmware-post", f"{BASE}/update"),
        (LOCAL_URL, "get", BASE),
        (LOCAL_URL, "get", "/api/core"),
        (LOCAL_URL, "get", "/api/other/firmware/info"),
        (LOCAL_URL, "get", "/other/core/firmware/info"),
        (UNPARSABLE_URL, "get", f"{BASE}/info"),
    ],
)
async def test_boundary_sends_other_requests_unchanged(
    make_client: Callable[..., aiopnsense.OPNsenseClient],
    fake_response_factory: Callable[..., Any],
    url: str,
    site: str,
    path: str,
) -> None:
    """Send each request the guard permits to the session at the URL the client built.

    Args:
        make_client (Callable[..., aiopnsense.OPNsenseClient]): Client factory.
        fake_response_factory (Callable[..., Any]): Builds fake HTTP responses.
        url (str): Base URL the client is built with.
        site (str): Request site under test.
        path (str): Request path that the guard must not block.
    """
    client, session = make_mock_session_client(make_client, url=url)
    client.toggle_throwing_errors(True)
    method = session.post if site in POST_SITES else session.get
    method.return_value = fake_response_factory(json_payload={"status": "ok"})

    await _send_sites(client)[site](path)

    method.assert_called_once()
    assert method.call_args.args == (f"{url}{path}",)


def _modules() -> dict[str, ast.Module]:
    """Parse every production module.

    Returns:
        dict[str, ast.Module]: Parsed modules by package-relative path.
    """
    return {
        path.relative_to(PACKAGE).as_posix(): ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(PACKAGE.rglob("*.py"))
    }


def _scoped_nodes(
    node: ast.AST, scope: tuple[str, ...] = ()
) -> Iterator[tuple[ast.AST, tuple[str, ...]]]:
    """Yield each node below ``node`` with its enclosing class and function names.

    Args:
        node (ast.AST): Node to walk.
        scope (tuple[str, ...]): Names enclosing ``node``.

    Yields:
        tuple[ast.AST, tuple[str, ...]]: Node and its enclosing scope.
    """
    yield node, scope
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        scope = (*scope, node.name)
    for child in ast.iter_child_nodes(node):
        yield from _scoped_nodes(child, scope)


def test_every_session_request_goes_through_the_boundary() -> None:
    """Fail when production code uses the session outside ``_send_request``."""
    violations: list[str] = []
    for name, module in _modules().items():
        guarded = {
            id(node.args[0])
            for node in ast.walk(module)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_send_request"
            and node.args
        }
        violations += [
            f"aiopnsense/{name}:{node.lineno}"
            for node in ast.walk(module)
            if isinstance(node, ast.Attribute)
            and node.attr == "_session"
            and isinstance(node.ctx, ast.Load)
            and id(node) not in guarded
        ]

    assert not violations, "Send requests through _send_request:\n" + "\n".join(violations)


def test_firmware_dispatch_paths_stay_in_their_methods() -> None:
    """Fail when the check or action path is used outside the methods that dispatch it."""
    violations: list[str] = []
    check_dispatches = 0
    literals: Counter[str] = Counter()
    for name, module in _modules().items():
        for node, scope in _scoped_nodes(module):
            match node:
                case (
                    ast.Name(id=constant, ctx=ast.Load())
                    | ast.Attribute(attr=constant, ctx=ast.Load())
                ) if constant in DISPATCH_SCOPES:
                    if name != FIRMWARE_MODULE or scope not in DISPATCH_SCOPES[constant]:
                        violations.append(f"aiopnsense/{name}:{node.lineno}: {constant}")
                    check_dispatches += constant == "FIRMWARE_CHECK_ENDPOINT"
                case ast.alias(name=constant) if constant in DISPATCH_SCOPES:
                    violations.append(f"aiopnsense/{name}:{node.lineno}: import {constant}")
                case ast.Constant(value=str() as value) if value in DISPATCH_LITERALS:
                    literals[value] += 1

    assert not violations, "\n".join(violations)
    assert literals == Counter({f"{BASE}/check": 1, f"{BASE}/": 1})
    assert check_dispatches == 1
