"""Enforce the firmware endpoint rule from ADR 0002.

The request boundary in ``aiopnsense/_firmware_request_guard.py`` blocks forbidden requests
at runtime, however their path is spelled. The static checks here cover only what the
boundary cannot see: that every request goes through it, that one method holds the
permission to send a firmware check, and where the update and upgrade paths are built.
"""

import ast
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
import re
from typing import Any
from unittest.mock import MagicMock

import pytest

import aiopnsense
from aiopnsense import OPNsenseInvalidArgument
from aiopnsense._firmware_request_guard import _allow_firmware_check
from tests.conftest import make_mock_session_client

PACKAGE = Path(__file__).resolve().parents[1] / "aiopnsense"
GUARD_MODULE = "_firmware_request_guard.py"
FIRMWARE_MODULE = "firmware.py"
CHECK_SCOPE = ("FirmwareMixin", "_request_firmware_check")
ACTION_SCOPES = {("FirmwareMixin", "install_firmware"), ("FirmwareMixin", "upgrade_firmware")}
ACTION_LITERAL = re.compile(r"/api/core/firmware/(?:update|upgrade)(?![\w-])", re.IGNORECASE)

BASE = "/api/core/firmware"
INFO_ACTION = "info"
STATUS_ACTION = "status"
CHECK_ACTION = "check"


PATH_FORMS: dict[str, Callable[[str], str]] = {
    "literal": lambda action: f"/api/core/firmware/{action}",
    "query": lambda action: f"{BASE}/{action}?force=1",
    "fragment": lambda action: f"{BASE}/{action}#top",
    "trailing-slash": lambda action: f"{BASE}/{action}/",
    "repeated-slash": lambda action: f"/api/core//firmware//{action}",
    "uppercase": lambda action: f"{BASE}/{action}".upper(),
    "dot-segment": lambda action: f"{BASE}/changelog/../{action}",
    "percent-encoded": lambda action: f"{BASE}/%{ord(action[0]):x}{action[1:]}",
    "parameter": lambda action: f"{BASE}/{action}/extra",
}


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
        "probe-get": lambda path: client._is_endpoint_available(path, method="get"),
        "probe-post": lambda path: client._is_endpoint_available(path, method="post"),
        "firmware-get": client._firmware_request,
        "firmware-post": lambda path: client._firmware_request(path, post=True),
    }


GET_SITES = ("get", "get-from-stream", "stream-events", "probe-get", "firmware-get")
POST_SITES = ("post", "probe-post", "firmware-post")
FIRMWARE_SITES = ("firmware-get", "firmware-post")
EMPTY_RESULTS: dict[str, Any] = {
    "get": None,
    "post": None,
    "get-from-stream": {},
    "stream-events": [],
    "probe-get": False,
    "probe-post": False,
}
FORBIDDEN = [
    *((site, INFO_ACTION) for site in (*GET_SITES, *POST_SITES)),
    *((site, STATUS_ACTION) for site in POST_SITES),
    *((site, CHECK_ACTION) for site in POST_SITES),
]


def _assert_nothing_sent(session: MagicMock) -> None:
    """Assert that the session sent no request.

    Args:
        session (MagicMock): Mocked aiohttp session.
    """
    session.get.assert_not_called()
    session.post.assert_not_called()


@pytest.mark.parametrize(("site", "action"), FORBIDDEN)
async def test_boundary_raises_for_forbidden_request_in_throw_mode(
    make_client: Callable[..., aiopnsense.OPNsenseClient], site: str, action: str
) -> None:
    """Raise the public error and send nothing at every request site.

    Args:
        make_client (Callable[..., aiopnsense.OPNsenseClient]): Client factory.
        site (str): Request site under test.
        action (str): Forbidden firmware action.
    """
    client, session = make_mock_session_client(make_client)
    client.toggle_throwing_errors(True)

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
    ("site", "action"), [item for item in FORBIDDEN if item[0] in FIRMWARE_SITES]
)
async def test_firmware_request_raises_for_forbidden_request_without_throwing(
    make_client: Callable[..., aiopnsense.OPNsenseClient], site: str, action: str
) -> None:
    """Raise the blocked error at firmware request sites even when errors are not thrown.

    Firmware requests have no empty value to return, so they raise whatever ``throw_errors`` is.

    Args:
        make_client (Callable[..., aiopnsense.OPNsenseClient]): Client factory.
        site (str): Firmware request site under test.
        action (str): Forbidden firmware action.
    """
    client, session = make_mock_session_client(make_client)

    with pytest.raises(OPNsenseInvalidArgument, match="Blocked"):
        await _send_sites(client)[site](f"{BASE}/{action}")

    _assert_nothing_sent(session)


@pytest.mark.parametrize("fail_inside_block", [False, True], ids=["normal-exit", "exception-exit"])
async def test_firmware_check_permission_ends_with_its_block(
    make_client: Callable[..., aiopnsense.OPNsenseClient], fail_inside_block: bool
) -> None:
    """Block a firmware check sent after the permission block has exited.

    Args:
        make_client (Callable[..., aiopnsense.OPNsenseClient]): Client factory.
        fail_inside_block (bool): Whether the block exits by raising an exception.
    """
    client, session = make_mock_session_client(make_client)
    client.toggle_throwing_errors(True)

    if fail_inside_block:
        with pytest.raises(ValueError), _allow_firmware_check():
            int("not a number")
    else:
        with _allow_firmware_check():
            pass

    with pytest.raises(OPNsenseInvalidArgument, match="Blocked"):
        await client._do_post(f"{BASE}/{CHECK_ACTION}")

    _assert_nothing_sent(session)


@pytest.mark.parametrize("form", PATH_FORMS.values(), ids=PATH_FORMS)
@pytest.mark.parametrize(
    ("site", "action"),
    [("get", INFO_ACTION), ("post", INFO_ACTION), ("post", STATUS_ACTION), ("post", CHECK_ACTION)],
)
async def test_boundary_blocks_every_spelling_of_the_path(
    make_client: Callable[..., aiopnsense.OPNsenseClient],
    site: str,
    action: str,
    form: Callable[[str], str],
) -> None:
    """Block a forbidden request whatever the spelling of its path.

    Args:
        make_client (Callable[..., aiopnsense.OPNsenseClient]): Client factory.
        site (str): Request site under test.
        action (str): Forbidden firmware action.
        form (Callable[[str], str]): Spells the path for the action.
    """
    client, session = make_mock_session_client(make_client)
    client.toggle_throwing_errors(True)

    with pytest.raises(OPNsenseInvalidArgument, match="Blocked"):
        await _send_sites(client)[site](form(action))

    _assert_nothing_sent(session)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
async def test_public_probe_blocks_info_request(
    make_client: Callable[..., aiopnsense.OPNsenseClient],
) -> None:
    """Report the info endpoint as unavailable through the public probe without sending it.

    Args:
        make_client (Callable[..., aiopnsense.OPNsenseClient]): Client factory.
    """
    client, session = make_mock_session_client(make_client)

    assert await client.is_endpoint_available(f"{BASE}/info") is False  # type: ignore[deprecated]

    _assert_nothing_sent(session)


@pytest.mark.parametrize(
    ("site", "path"),
    [
        ("get", f"{BASE}/status"),
        ("probe-get", f"{BASE}/status"),
        ("firmware-get", f"{BASE}/status"),
        ("get", f"{BASE}/running"),
        ("get", f"{BASE}/upgradestatus"),
        ("post", f"{BASE}/upgradestatus"),
        ("post", f"{BASE}/changelog/26.7.9"),
        ("post", f"{BASE}/changelog/info"),
        ("get", f"{BASE}/information"),
        ("get", "/api/core/service/info"),
        ("post", "/api/core/service/status"),
        ("firmware-post", f"{BASE}/update"),
    ],
)
async def test_boundary_allows_other_requests(
    make_client: Callable[..., aiopnsense.OPNsenseClient],
    fake_response_factory: Callable[..., Any],
    site: str,
    path: str,
) -> None:
    """Send permitted firmware requests and similarly named paths unchanged.

    Args:
        make_client (Callable[..., aiopnsense.OPNsenseClient]): Client factory.
        fake_response_factory (Callable[..., Any]): Builds fake HTTP responses.
        site (str): Request site under test.
        path (str): Permitted path.
    """
    client, session = make_mock_session_client(make_client)
    client.toggle_throwing_errors(True)
    method = session.post if site in POST_SITES else session.get
    method.return_value = fake_response_factory(json_payload={"status": "ok"})

    await _send_sites(client)[site](path)

    method.assert_called_once()
    assert method.call_args.args == (f"http://localhost{path}",)


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


def test_only_the_check_method_may_allow_a_firmware_check() -> None:
    """Fail unless one ``with`` block in the check method holds the permission."""
    modules = _modules()
    importers = [
        name
        for name, module in modules.items()
        for node in ast.walk(module)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "_allow_firmware_check" for alias in node.names)
    ]
    uses = [
        (name, scope, node)
        for name, module in modules.items()
        if name != GUARD_MODULE
        for node, scope in _scoped_nodes(module)
        if isinstance(node, ast.Name) and node.id == "_allow_firmware_check"
    ]
    blocks = [
        node
        for node, scope in _scoped_nodes(modules[FIRMWARE_MODULE])
        if scope == CHECK_SCOPE
        and isinstance(node, ast.With)
        and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Name)
            and item.context_expr.func.id == "_allow_firmware_check"
            for item in node.items
        )
    ]

    assert importers == [FIRMWARE_MODULE]
    assert [(name, scope) for name, scope, _ in uses] == [(FIRMWARE_MODULE, CHECK_SCOPE)]
    assert len(blocks) == 1
    assert [ast.unparse(statement) for statement in blocks[0].body] == [
        "response = await self._do_post(FIRMWARE_CHECK_ENDPOINT)"
    ]


def test_update_and_upgrade_paths_stay_in_the_install_methods() -> None:
    """Fail when the action prefix or a literal update path appears anywhere else."""
    violations: list[str] = []
    for name, module in _modules().items():
        for node, scope in _scoped_nodes(module):
            match node:
                case (
                    ast.Name(id="FIRMWARE_ACTION_ENDPOINT_PREFIX", ctx=ast.Load())
                    | ast.Attribute(attr="FIRMWARE_ACTION_ENDPOINT_PREFIX", ctx=ast.Load())
                ):
                    if name != FIRMWARE_MODULE or scope not in ACTION_SCOPES:
                        violations.append(f"aiopnsense/{name}:{node.lineno}: action prefix")
                case ast.Constant(value=str() as value) if ACTION_LITERAL.search(value):
                    violations.append(f"aiopnsense/{name}:{node.lineno}: literal action path")

    assert not violations, "\n".join(violations)
