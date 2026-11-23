"""Stop firmware requests that ADR 0002 forbids before they leave the client.

Every HTTP request goes through ``_send_request``. It judges the path that aiohttp would
send, so it does not matter how the caller built the string. A blocked request raises
``_BlockedFirmwareRequest``, an ``aiohttp.ClientError``, inside the caller's existing
transport error handling, so it follows the same ``throw_errors`` policy as a failed request.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import aiohttp
from yarl import URL

from .exceptions import _BlockedFirmwareRequest

_FIRMWARE_API_SEGMENTS = ("api", "core", "firmware")
_CHECK_DISPATCH_ALLOWED: ContextVar[bool] = ContextVar(
    "aiopnsense_firmware_check_dispatch_allowed", default=False
)


@contextmanager
def _allow_firmware_check() -> Iterator[None]:
    """Allow ``POST /api/core/firmware/check`` in the current context.

    Only ``FirmwareMixin._request_firmware_check`` may use this. A request sent through
    the queue runs in a worker's context and is still blocked.

    Yields:
        None: Control while the check may be dispatched.
    """
    token = _CHECK_DISPATCH_ALLOWED.set(True)
    try:
        yield
    finally:
        _CHECK_DISPATCH_ALLOWED.reset(token)


def _firmware_action(url: str) -> str | None:
    """Return the lowercase firmware controller action that a URL addresses.

    yarl applies the same parsing as aiohttp, which removes the query and fragment and
    resolves dot segments and percent-encoding. Empty segments are ignored, so repeated
    and trailing slashes do not change the result. Segments after the action are
    controller parameters and do not change which action runs.

    Args:
        url (str): Full request URL.

    Returns:
        str | None: Action name, or ``None`` for any other path or for a URL that aiohttp
            will itself reject.
    """
    try:
        path = URL(url).path
    except ValueError:
        return None
    segments = [segment for segment in path.lower().split("/") if segment]
    prefix_length = len(_FIRMWARE_API_SEGMENTS)
    if len(segments) <= prefix_length or tuple(segments[:prefix_length]) != _FIRMWARE_API_SEGMENTS:
        return None
    return segments[prefix_length]


def _send_request(session: aiohttp.ClientSession, method: str, url: str, **kwargs: Any) -> Any:
    """Start an HTTP request unless ADR 0002 forbids it.

    Args:
        session (aiohttp.ClientSession): Session that sends the request.
        method (str): Lowercase session method, ``get`` or ``post``.
        url (str): Full request URL.
        kwargs (Any): Options passed to the session method.

    Returns:
        Any: The session's request context manager.

    Raises:
        _BlockedFirmwareRequest: The request is forbidden. No request is sent.
    """
    action = _firmware_action(url)
    if (
        action == "info"
        or (method == "post" and action == "status")
        or (method == "post" and action == "check" and not _CHECK_DISPATCH_ALLOWED.get())
    ):
        raise _BlockedFirmwareRequest(
            f"Blocked {method.upper()} firmware {action} request: "
            "see docs/adr/0002-firmware-reads-never-contact-upstream.md"
        )
    return getattr(session, method)(url, **kwargs)
