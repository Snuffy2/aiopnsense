"""Shared pytest fixtures for aiopnsense."""

import asyncio
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Callable,
    Collection,
    Coroutine,
    Iterator,
    Mapping,
    MutableMapping,
)
import contextlib
from dataclasses import dataclass
from functools import partial
import json
from types import TracebackType
from typing import Any, Never, Self, cast
from unittest.mock import MagicMock
from urllib.parse import urlsplit

import aiohttp
import pytest
from yarl import URL

import aiopnsense
from aiopnsense import client_queue

MakeClientFactory = Callable[..., aiopnsense.OPNsenseClient]


@pytest.fixture
def legacy_dnsbl_payload() -> dict[str, Any]:
    """Return a reusable legacy Unbound DNSBL payload.

    Returns:
        dict[str, Any]: Legacy Unbound DNSBL payload returned by older OPNsense
        firmware.
    """
    return {
        "unbound": {
            "dnsbl": {
                "enabled": "0",
                "safesearch": "0",
                "nxdomain": "1",
                "address": "0.0.0.0",
                "type": {},
                "lists": {},
                "whitelists": {},
                "blocklists": {},
                "wildcards": {},
            }
        }
    }


class FakeClientSession:
    """Minimal fake aiohttp session for unit tests."""

    async def __aenter__(self) -> Self:
        """Enter async context.

        Returns:
            Self: The context-managed instance.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        """Exit async context.

        Args:
            exc_type (type[BaseException] | None): Exception type raised in
                async context teardown.
            exc (BaseException | None): Exception instance raised in async
                context teardown.
            tb (TracebackType | None): Traceback associated with async context
                teardown.

        Returns:
            bool: False so exceptions are not suppressed.
        """
        await self.close()
        return False

    async def close(self) -> bool:
        """Close the fake session.

        Returns:
            bool: True when close succeeds.
        """
        return True


@dataclass(slots=True)
class _FakeRequestInfo:
    """Minimal request_info replacement for aiohttp response stubs."""

    real_url: URL


class FakeStreamContent:
    """Stream payload iterator used by fake HTTP response objects."""

    def __init__(self, payload: list[bytes]) -> None:
        """Initialize the content iterator.

        Args:
            payload (list[bytes]): Chunk payload returned by ``iter_chunked``.
        """
        self._payload = payload

    async def iter_chunked(self, _chunk_size: int) -> AsyncGenerator[bytes]:
        """Yield payload chunks for stream parsing tests.

        Args:
            _chunk_size (int): Requested chunk size (unused by the fake implementation).

        Yields:
            bytes: Next payload chunk.
        """
        for chunk in self._payload:
            yield chunk


class FakeResponse:
    """Reusable aiohttp response context-manager stub for tests."""

    def __init__(
        self,
        *,
        status: int = 200,
        reason: str = "OK",
        ok: bool = True,
        json_payload: Any = None,
        text_payload: str = "",
        stream_chunks: list[bytes] | None = None,
        include_request_info: bool = False,
        request_url: str = "http://localhost",
        headers: MutableMapping[str, Any] | None = None,
        history: list[Any] | None = None,
    ) -> None:
        """Initialize a fake HTTP response.

        Args:
            status (int): HTTP status code.
            reason (str): HTTP reason phrase.
            ok (bool): Whether the response should be treated as successful.
            json_payload (Any): Payload emitted by the fake ``json()`` method.
            text_payload (str): Text emitted by the fake ``text()`` method.
            stream_chunks (list[bytes] | None): Payload yielded by ``content.iter_chunked``.
            include_request_info (bool): Whether to include minimal ``request_info`` metadata.
            request_url (str): URL exposed via ``request_info.real_url``.
            headers (MutableMapping[str, Any] | None): HTTP headers for error construction.
            history (list[Any] | None): Redirect history for error construction.
        """
        self.status = status
        self.reason = reason
        self.ok = ok
        self._json_payload = json_payload
        self._text_payload = text_payload
        self._stream_chunks = list(stream_chunks or [])
        self.headers = dict(headers or {})
        self.history = list(history or [])
        self.request_info: _FakeRequestInfo | None = None

        if include_request_info:
            self.request_info = _FakeRequestInfo(real_url=URL(request_url))

    async def __aenter__(self) -> Self:
        """Enter the asynchronous context.

        Returns:
            Self: The context-managed instance.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        """Exit the asynchronous context.

        Args:
            exc_type (type[BaseException] | None): Exception type raised during context usage.
            exc (BaseException | None): Exception instance raised during context usage.
            tb (TracebackType | None): Traceback for any raised exception.

        Returns:
            bool: ``False`` so exceptions are not suppressed.
        """
        return False

    async def json(self, content_type: Any = None) -> Any:
        """Return a preconfigured JSON payload.

        Args:
            content_type (Any, optional): Requested content type (unused by the fake).

        Returns:
            Any: Mock JSON payload.
        """
        del content_type
        return self._json_payload

    async def text(self) -> str:
        """Return a preconfigured text payload.

        Returns:
            str: Mock text payload.
        """
        return self._text_payload

    @property
    def content(self) -> FakeStreamContent:
        """Return stream content iterator wrapper.

        Returns:
            FakeStreamContent: Content wrapper used by stream tests.
        """
        return FakeStreamContent(self._stream_chunks)


class MalformedJSONResponse(FakeResponse):
    """Successful response whose body is not valid JSON."""

    async def json(self, content_type: Any = None) -> Never:
        """Raise the error aiohttp raises for a body that is not valid JSON.

        Args:
            content_type (Any, optional): Requested content type (unused by the fake).

        Returns:
            Never: This method always raises.

        Raises:
            json.JSONDecodeError: Always.
        """
        del content_type
        raise json.JSONDecodeError("Expecting value", "<html>", 0)


FakeStreamResponseFactory = Callable[[list[bytes]], FakeResponse]


def make_mock_session_client(
    make_client: Callable[..., aiopnsense.OPNsenseClient],
    *,
    username: str = "u",
    password: str = "p",
    url: str = "http://localhost",
) -> tuple[aiopnsense.OPNsenseClient, MagicMock]:
    """Build a client backed by a shared MagicMock aiohttp session.

    Args:
        make_client (Callable[..., aiopnsense.OPNsenseClient]): ``make_client`` fixture factory.
        username (str): Client username.
        password (str): Client password.
        url (str): Base URL of the OPNsense instance.

    Returns:
        tuple[aiopnsense.OPNsenseClient, MagicMock]: Created client and mocked session.
    """
    session = MagicMock(spec=aiohttp.ClientSession)
    return make_client(session=session, username=username, password=password, url=url), session


FIRMWARE_STATUS_PATH = "/api/core/firmware/status"


def firmware_status_payload(version: str | None) -> dict[str, Any]:
    """Return the firmware status a Router reports for an installed version.

    Args:
        version (str | None): Installed product version, or an empty value for a status without one.

    Returns:
        dict[str, Any]: Status payload with ``product.product_version``, or an empty payload.
    """
    return {"product": {"product_version": version}} if version else {}


class HeldResponse(FakeResponse):
    """Response whose request stays in flight until the test releases it.

    Attributes:
        entered (asyncio.Event): Set when the request reaches the Router.
        release (asyncio.Event): Set by the test to let the request finish.
        exited (asyncio.Event): Set when the response context is left.
    """

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the held response.

        Args:
            kwargs (Any): Options accepted by ``FakeResponse``.
        """
        super().__init__(**kwargs)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.exited = asyncio.Event()

    async def __aenter__(self) -> Self:
        """Report that the request reached the Router, then wait to be released.

        Returns:
            Self: This response once the test releases it.
        """
        self.entered.set()
        await self.release.wait()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        """Report that the request has finished.

        Args:
            exc_type (type[BaseException] | None): Exception type raised in the context.
            exc (BaseException | None): Exception instance raised in the context.
            tb (TracebackType | None): Traceback of the exception.

        Returns:
            bool: ``False`` so exceptions are not suppressed.
        """
        self.exited.set()
        return False


class SequencedResponse(FakeResponse):
    """200 JSON response that answers successive reads in turn, and can hold chosen reads.

    The last payload answers every read after the list runs out. A held read has already
    taken its answer when it waits, as a slow Router response does. ``held`` receives one
    gate per held read, which the test sets to let that read finish.

    Attributes:
        held (asyncio.Queue[asyncio.Event]): Gates of reads waiting to be released.
        reads (int): Number of reads answered or held so far.
    """

    def __init__(self, *payloads: Any, hold: Collection[int] = ()) -> None:
        """Script the answers.

        Args:
            *payloads (Any): Answers for the first read, the second read, and so on.
            hold (Collection[int]): Reads to hold, counted from 1.
        """
        super().__init__()
        self._payloads = list(payloads)
        self._hold = set(hold)
        self.held: asyncio.Queue[asyncio.Event] = asyncio.Queue()
        self.reads = 0
        self._gates: list[asyncio.Event] = []

    def release_all(self) -> None:
        """Let every held read finish, including reads that are not waiting yet."""
        self._hold.clear()
        for gate in self._gates:
            gate.set()

    def replace(self, payload: Any) -> None:
        """Answer every later read with one payload.

        Args:
            payload (Any): Answer for each read that has not started yet.
        """
        self._payloads = [payload]

    async def json(self, content_type: Any = None) -> Any:
        """Return this read's answer, after the gate opens when the read is held.

        Args:
            content_type (Any): Unused decoding option.

        Returns:
            Any: Payload scripted for this read.
        """
        del content_type
        self.reads += 1
        payload = self._payloads.pop(0) if len(self._payloads) > 1 else self._payloads[0]
        if self.reads in self._hold:
            gate = asyncio.Event()
            self._gates.append(gate)
            self.held.put_nowait(gate)
            await gate.wait()
        return payload


class FakeRouter:
    """Fake Router that answers HTTP requests at the aiohttp session boundary.

    Responses come from ``routes`` (keyed by method and request path, query
    included). A route value is turned into a response this way: a callable is
    called with the request parameters (``json``, ``timeout`` and so on) and its
    result is used instead, an exception instance is raised by the session call,
    an ``int`` is an HTTP status, a ``str`` is a 200 text body, a ``FakeResponse``
    is used as is, and anything else is a 200 JSON body. Tests may change
    ``routes`` between calls to model the Router changing.

    A request with no route is recorded and answered with 404, as a real Router
    answers a missing endpoint.
    """

    def __init__(self, routes: Mapping[tuple[str, str], Any] | None = None) -> None:
        """Initialize the fake Router.

        Args:
            routes (Mapping[tuple[str, str], Any] | None): Values keyed by ``(method, path)``.
        """
        self.requests: list[tuple[str, str, Any]] = []
        self.routes: dict[tuple[str, str], Any] = {
            (method.upper(), path): value for (method, path), value in (routes or {}).items()
        }

    def _matching(self, method: str | None, path: str | None) -> Iterator[tuple[str, str, Any]]:
        """Yield the recorded requests that match a method and path.

        Args:
            method (str | None): HTTP method to match, or ``None`` for any method.
            path (str | None): Request path (query included) to match, or ``None`` for any path.

        Yields:
            tuple[str, str, Any]: ``(method, path, JSON body)`` of each matching request.
        """
        for request in self.requests:
            if (method is None or request[0] == method.upper()) and path in (None, request[1]):
                yield request

    def count(self, method: str | None = None, path: str | None = None) -> int:
        """Count recorded requests.

        Args:
            method (str | None): HTTP method to match, or ``None`` for any method.
            path (str | None): Request path (query included) to match, or ``None`` for any path.

        Returns:
            int: Number of matching requests.
        """
        return sum(1 for _request in self._matching(method, path))

    def calls(self, method: str | None = None) -> list[tuple[str, str]]:
        """Return the method and path of each recorded request, in arrival order.

        Args:
            method (str | None): HTTP method to keep, or ``None`` for every method.

        Returns:
            list[tuple[str, str]]: ``(method, path)`` pairs.
        """
        return [(request[0], request[1]) for request in self._matching(method, None)]

    def paths(self, method: str) -> list[str]:
        """Return the paths of the requests sent with one method, in arrival order.

        Args:
            method (str): HTTP method to match.

        Returns:
            list[str]: Request paths (query included).
        """
        return [request[1] for request in self._matching(method, None)]

    def bodies(self, method: str, path: str) -> list[Any]:
        """Return the JSON bodies of the requests for one method and path.

        Args:
            method (str): HTTP method to match.
            path (str): Request path (query included) to match.

        Returns:
            list[Any]: Request bodies in arrival order.
        """
        return [request[2] for request in self._matching(method, path)]

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        """Record one request and return the configured response.

        Args:
            method (str): HTTP method of the request.
            url (str): Full requested URL.
            kwargs (Any): Request parameters passed by the HTTP transport.

        Returns:
            FakeResponse: Configured response for the request.

        Raises:
            value: Configured transport error for this request.
        """
        parts = urlsplit(url)
        path = f"{parts.path}?{parts.query}" if parts.query else parts.path
        self.requests.append((method, path, kwargs.get("json")))
        value = self.routes.get((method, path), 404)
        if callable(value):
            value = value(**kwargs)
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, FakeResponse):
            return value
        if isinstance(value, bool) or not isinstance(value, int):
            if isinstance(value, str):
                return FakeResponse(text_payload=value)
            return FakeResponse(json_payload=value)
        return FakeResponse(status=value, reason="OK" if value < 400 else "ERR", ok=value < 400)


def make_router_client(
    make_client: Callable[..., aiopnsense.OPNsenseClient],
    routes: Mapping[tuple[str, str], Any] | None = None,
    *,
    firmware: str | None = None,
    throw_errors: bool = False,
    **client_kwargs: Any,
) -> tuple[aiopnsense.OPNsenseClient, FakeRouter]:
    """Build a client on the real request queue against a fake Router.

    The Router answers only at the aiohttp session. Every public call then runs
    through the client's real queue workers, endpoint cache, error policy and
    transport. Workers stop in the ``make_client`` cleanup.

    Args:
        make_client (Callable[..., aiopnsense.OPNsenseClient]): ``make_client`` fixture factory.
        routes (Mapping[tuple[str, str], Any] | None): Values keyed by ``(method, path)``. They
            override the firmware status route.
        firmware (str | None): Firmware version the Router reports, or an empty string
            or ``None`` to leave the firmware status unrouted, so the Router answers 404.
        throw_errors (bool): Public ``throw_errors`` argument of the client.
        client_kwargs (Any): Other arguments for ``make_client``.

    Returns:
        tuple[aiopnsense.OPNsenseClient, FakeRouter]: The client and the Router it talks to.
    """
    session = MagicMock(spec=aiohttp.ClientSession)
    router = FakeRouter(
        {
            **(
                {("GET", FIRMWARE_STATUS_PATH): firmware_status_payload(firmware)}
                if firmware
                else {}
            ),
            **(routes or {}),
        }
    )
    session.get = partial(router.request, "GET")
    session.post = partial(router.request, "POST")
    client = make_client(session=session, throw_errors=throw_errors, **client_kwargs)
    return client, router


@dataclass(frozen=True, slots=True)
class FirmwareVersionShape:
    """A firmware version as a Router reports it, with its expected handling.

    Expected values are written by hand so that they do not depend on the code under test.

    Attributes:
        id (str): Readable pytest case name.
        product_version (str | None): ``product.product_version`` in the status payload.
        product_series (str | None): ``product.product_series`` in the status payload.
        trimmed (str | None): Version left after removing the hotfix suffix.
        at_least (Mapping[str, bool | None]): Expected ``firmware_is_at_least`` result
            for ``product_version`` against each minimum, ``None`` when not comparable.
        lookup_version (str | None): Version returned by the firmware lookup.
        cached (bool): Whether the lookup keeps that version for later calls.
    """

    id: str
    product_version: str | None
    product_series: str | None
    trimmed: str | None
    at_least: Mapping[str, bool | None]
    lookup_version: str | None
    cached: bool

    @property
    def version_is_comparable(self) -> bool:
        """Return whether the reported product version itself can be compared.

        Returns:
            bool: ``True`` when no expected minimum comparison is ``None``.
        """
        return all(result is not None for result in self.at_least.values())


def _minimums(
    at_least_26_1_11: bool | None,
    at_least_26_7: bool | None,
    at_least_26_7_6: bool | None,
    at_least_27_1: bool | None,
) -> dict[str, bool | None]:
    """Label expected results with the minimum versions they were written against.

    Args:
        at_least_26_1_11 (bool | None): Expected result against ``26.1.11``.
        at_least_26_7 (bool | None): Expected result against ``26.7``.
        at_least_26_7_6 (bool | None): Expected result against ``26.7.6``.
        at_least_27_1 (bool | None): Expected result against ``27.1``.

    Returns:
        dict[str, bool | None]: Expected result for each minimum version.
    """
    return {
        "26.1.11": at_least_26_1_11,
        "26.7": at_least_26_7,
        "26.7.6": at_least_26_7_6,
        "27.1": at_least_27_1,
    }


_NOT_COMPARABLE = _minimums(None, None, None, None)

# Release and hotfix forms come from a live Router and its OPNsense changelogs. The beta is
# the `opnsense-devel` package version, and its series is inferred from that version. A beta
# cannot be compared with a release, so the lookup uses its product series. A release
# candidate compares as older than its final release. The last entry is the error status
# OPNsense returns without product data.
FIRMWARE_VERSION_SHAPES: tuple[FirmwareVersionShape, ...] = (
    FirmwareVersionShape(
        id="series-release",
        product_version="26.7",
        product_series="26.7",
        trimmed="26.7",
        at_least=_minimums(True, True, False, False),
        lookup_version="26.7",
        cached=True,
    ),
    FirmwareVersionShape(
        id="patch-release",
        product_version="26.7.6",
        product_series="26.7",
        trimmed="26.7.6",
        at_least=_minimums(True, True, True, False),
        lookup_version="26.7.6",
        cached=True,
    ),
    FirmwareVersionShape(
        id="series-release-hotfix",
        product_version="26.1_4",
        product_series="26.1",
        trimmed="26.1",
        at_least=_minimums(False, False, False, False),
        lookup_version="26.1_4",
        cached=True,
    ),
    FirmwareVersionShape(
        id="patch-hotfix",
        product_version="26.7.6_3",
        product_series="26.7",
        trimmed="26.7.6",
        at_least=_minimums(True, True, True, False),
        lookup_version="26.7.6_3",
        cached=True,
    ),
    FirmwareVersionShape(
        id="double-digit-hotfix",
        product_version="26.1.11_10",
        product_series="26.1",
        trimmed="26.1.11",
        at_least=_minimums(True, False, False, False),
        lookup_version="26.1.11_10",
        cached=True,
    ),
    FirmwareVersionShape(
        id="release-candidate",
        product_version="26.7.r1",
        product_series="26.7",
        trimmed="26.7.r1",
        at_least=_minimums(True, False, False, False),
        lookup_version="26.7.r1",
        cached=True,
    ),
    FirmwareVersionShape(
        id="release-candidate-hotfix",
        product_version="26.7.r2_3",
        product_series="26.7",
        trimmed="26.7.r2",
        at_least=_minimums(True, False, False, False),
        lookup_version="26.7.r2_3",
        cached=True,
    ),
    FirmwareVersionShape(
        id="beta-build",
        product_version="27.1.b_21",
        product_series="27.1",
        trimmed="27.1.b",
        at_least=_NOT_COMPARABLE,
        lookup_version="27.1",
        cached=True,
    ),
    FirmwareVersionShape(
        id="missing-product",
        product_version=None,
        product_series=None,
        trimmed=None,
        at_least=_NOT_COMPARABLE,
        lookup_version=None,
        cached=False,
    ),
)

# Comparable versions from oldest to newest, so every later entry is newer than every
# earlier one. Hotfixes 2, 8 and 11 check that revisions sort as numbers.
FIRMWARE_RELEASE_ORDER: tuple[str, ...] = (
    "26.1.r2",
    "26.1.r2_2",
    "26.1",
    "26.1_4",
    "26.1.1",
    "26.1.11",
    "26.1.11_10",
    "26.7.r1",
    "26.7.r2",
    "26.7.r2_3",
    "26.7",
    "26.7.1_1",
    "26.7.3_2",
    "26.7.3_8",
    "26.7.3_11",
    "26.7.4_1",
    "26.7.6",
    "26.7.6_3",
    "26.7.7",
    "26.7.9_3",
)


@pytest.fixture(autouse=True)
def _no_queue_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the production pause between queued requests for every test.

    Clients run on real queue workers, and a test never needs a sleep to wait for the
    queue. This is the only queue detail the test setup knows.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture used to set and restore the pacing delay.
    """
    monkeypatch.setattr(client_queue, "QUEUE_REQUEST_INTERVAL_SECONDS", 0)


@pytest.fixture
def fake_response_factory() -> Callable[..., FakeResponse]:
    """Return a factory that constructs reusable fake HTTP responses.

    Returns:
        Callable[..., FakeResponse]: Factory for configurable fake HTTP responses.
    """

    def _make(
        *,
        status: int = 200,
        reason: str = "OK",
        ok: bool = True,
        json_payload: Any = None,
        text_payload: str = "",
        chunks: list[bytes] | None = None,
        include_request_info: bool = False,
    ) -> FakeResponse:
        """Build a reusable fake HTTP response object.

        Args:
            status (int): Status value to evaluate or normalize.
            reason (str): Expected failure reason text.
            ok (bool): Mock HTTP success flag for test responses.
            json_payload (Any): JSON payload returned by ``response.json()``.
            text_payload (str): Text payload returned by ``response.text()``.
            chunks (list[bytes] | None): Stream payload for ``response.content.iter_chunked``.
            include_request_info (bool): Whether to include minimal request metadata.

        Returns:
            FakeResponse: Mock response object.
        """
        return FakeResponse(
            status=status,
            reason=reason,
            ok=ok,
            json_payload=json_payload,
            text_payload=text_payload,
            stream_chunks=chunks,
            include_request_info=include_request_info,
        )

    return _make


@pytest.fixture
def fake_stream_response_factory(
    fake_response_factory: Callable[..., FakeResponse],
) -> Callable[..., FakeResponse]:
    """Return a factory that constructs fake streaming responses.

    Args:
        fake_response_factory (Callable[..., FakeResponse]): Base response factory wrapped for stream tests.

    Returns:
        Callable[..., FakeResponse]: Factory for fake responses with byte-stream content.
    """

    def _make(
        chunks: list[bytes],
        status: int = 200,
        reason: str = "OK",
        *,
        ok: bool = True,
    ) -> FakeResponse:
        """Build a fake streaming response.

        Args:
            chunks (list[bytes]): Mock stream chunks returned by iter_chunked.
            status (int): Status value to evaluate or normalize.
            reason (str): Expected failure reason text.
            ok (bool): Mock HTTP success flag for test responses.

        Returns:
            FakeResponse: Mock response object.
        """
        return fake_response_factory(status=status, reason=reason, ok=ok, chunks=chunks)

    return _make


@pytest.fixture
async def make_client() -> AsyncGenerator[Callable[..., aiopnsense.OPNsenseClient]]:
    """Return a factory that constructs an OPNsenseClient for tests.

    Yields:
        Callable[..., aiopnsense.OPNsenseClient]: Factory that creates clients and registers them for cleanup.
    """

    clients: list[aiopnsense.OPNsenseClient] = []

    def _make(
        session: aiohttp.ClientSession | None = None,
        username: str = "u",
        password: str = "p",
        url: str = "http://localhost",
        **client_kwargs: Any,
    ) -> aiopnsense.OPNsenseClient:
        """Create an OPNsense test client with a fake session by default.

        Args:
            session (aiohttp.ClientSession | None): HTTP client session used for API requests.
            username (str): Username for API authentication.
            password (str): Password for API authentication.
            url (str): Base URL of the OPNsense instance.
            client_kwargs (Any): Keyword arguments accepted by `_make`.

        Returns:
            aiopnsense.OPNsenseClient: Configured client instance using the
                supplied credentials, URL, session, and extra constructor
                options.
        """
        if session is None:
            session = cast("aiohttp.ClientSession", FakeClientSession())
        client = aiopnsense.OPNsenseClient(
            url=url,
            username=username,
            password=password,
            session=session,
            **client_kwargs,
        )
        clients.append(client)
        return client

    try:
        yield _make
    finally:
        for client in clients:
            with contextlib.suppress(Exception):
                await client.async_close()


class VpnQueueTasks:
    """Track tasks started by a VPN queue test for fixture cleanup."""

    def __init__(self) -> None:
        """Initialize the task list."""
        self.tasks: list[asyncio.Task[Any]] = []

    def start[T](
        self, coroutine: Coroutine[Any, Any, T], *, eager_start: bool = False
    ) -> asyncio.Task[T]:
        """Start and track a test task.

        Args:
            coroutine (Coroutine[Any, Any, T]): Public operation or queue blocker.
            eager_start (bool): Run to first suspension before returning.

        Returns:
            asyncio.Task[T]: Task running the operation.
        """
        task = asyncio.create_task(coroutine, eager_start=eager_start)
        self.tasks.append(task)
        return task


@pytest.fixture
async def vpn_queue_tasks(make_client: MakeClientFactory) -> AsyncIterator[VpnQueueTasks]:
    """Cancel and join any test tasks left running before clients close.

    Args:
        make_client (MakeClientFactory): Provides clients and closes them after task cleanup.

    Yields:
        VpnQueueTasks: Task starter for the test.
    """
    tasks = VpnQueueTasks()
    yield tasks
    for task in tasks.tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks.tasks, return_exceptions=True)
