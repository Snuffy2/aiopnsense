"""Stop firmware requests that ADR 0002 forbids before they leave the client.

Every HTTP request goes through ``_send_request``. It judges the path that aiohttp would
send, so it does not matter how the caller built the string. A blocked request raises
``_BlockedFirmwareRequest``, an ``aiohttp.ClientError``, inside the caller's existing
transport error handling, so it follows the same ``throw_errors`` policy as a failed request.
"""

from typing import Any

import aiohttp
from yarl import URL

from .exceptions import _BlockedFirmwareRequest


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
    match [segment for segment in path.lower().split("/") if segment]:
        case ["api", "core", "firmware", action, *_]:
            return action
    return None


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
    if action == "info" or (method == "post" and action == "status"):
        raise _BlockedFirmwareRequest(
            f"Blocked {method.upper()} firmware {action} request: "
            "see docs/adr/0002-firmware-reads-never-contact-upstream.md"
        )
    return getattr(session, method)(url, **kwargs)
