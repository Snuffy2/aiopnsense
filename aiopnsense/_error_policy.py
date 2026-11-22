"""Single home of the ``throw_errors`` contract.

Public client methods either return a documented empty value after logging a
failure (``throw_errors`` off) or raise a subclass of ``OPNsenseError``
(``throw_errors`` on). Every raise-or-empty decision, the mapping of arbitrary
errors to the public exception hierarchy, the redacted exception logging and the
"already logged" marker live here, so each failure is logged once.
"""

import asyncio
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, Protocol

import aiohttp

from .exceptions import OPNsenseError, OPNsenseTimeoutError, _map_opnsense_exception
from .helpers import (
    _LOGGER,
    _ExceptionLogDetails,
    _is_request_error_logged,
    _mark_request_error_logged,
)

# Errors a scoped step handles by default: everything a request can raise that
# the shared transport maps to a public error, plus bad response data.
_DEFAULT_SCOPED_ERRORS: tuple[type[Exception], ...] = (
    OPNsenseError,
    TimeoutError,
    aiohttp.ClientError,
    ValueError,
    TypeError,
)


class _ThrowErrorsClient(Protocol):
    """Client state the error policy reads."""

    _throw_errors: bool


def _handle_error(client: _ThrowErrorsClient, error: Exception, name: str) -> None:
    """Log one failure and raise its public form when the client throws errors.

    Args:
        client (_ThrowErrorsClient): Client whose ``_throw_errors`` setting selects
            between raising and returning to the caller.
        error (Exception): Caught failure to report.
        name (str): Operation name used in the log message.

    Raises:
        public_error: The failure itself when it is already an ``OPNsenseError``,
            otherwise its mapped ``OPNsenseError``, when ``_throw_errors`` is true.
            It is marked as logged so wrappers further up do not log it again.
    """
    if not _is_request_error_logged(error):
        if isinstance(error, (TimeoutError, aiohttp.ServerTimeoutError, OPNsenseTimeoutError)):
            _LOGGER.warning("Timeout Error in %s. Will retry. %s", name, error)
        else:
            _LOGGER.error("Error in %s. %s", name, _ExceptionLogDetails(error))
    if not client._throw_errors:
        return
    public_error = _map_opnsense_exception(error)
    _mark_request_error_logged(public_error)
    if public_error is error:
        raise public_error
    raise public_error from error


def _log_errors(func: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap coroutine methods with shared timeout/error logging behavior.

    Args:
        func (Callable[..., Any]): Coroutine function to decorate and execute
            with shared behavior.

    Returns:
        Callable[..., Any]: Decorator wrapper that applies shared exception
            logging and returns the wrapped coroutine result, or ``None`` when an
            error is suppressed.
    """

    @wraps(func)
    async def inner(self: Any, *args: Any, **kwargs: Any) -> Any:
        """Execute wrapped coroutine with shared timeout/exception logging.

        Args:
            self (Any): Client instance whose ``_throw_errors`` setting controls
                whether caught errors are mapped and propagated or logged and
                suppressed.
            args (Any): Positional arguments forwarded to the wrapped coroutine.
            kwargs (Any): Keyword arguments forwarded to the wrapped coroutine.

        Returns:
            Any: Wrapped coroutine result, or ``None`` when an error is
                suppressed. When ``_throw_errors`` is true, caught errors are
                raised as ``OPNsenseError`` subclasses instead.

        Raises:
            asyncio.CancelledError: Re-raised when the wrapped coroutine is
                cancelled; cancellation is never suppressed.
        """
        try:
            return await func(self, *args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            # Last line of defense for public methods: any failure follows the policy.
            _handle_error(self, e, func.__name__.strip("_"))
        return None

    return inner


async def _attempt[T, E](
    client: _ThrowErrorsClient,
    call: Callable[[], Awaitable[T]],
    *,
    empty: E,
    name: str,
    errors: tuple[type[Exception], ...] = _DEFAULT_SCOPED_ERRORS,
) -> T | E:
    """Run one step of a public method that has its own documented empty value.

    Args:
        client (_ThrowErrorsClient): Client whose ``_throw_errors`` setting selects
            between raising and returning ``empty``.
        call (Callable[[], Awaitable[T]]): Zero-argument callable that performs the step.
        empty (E): Value returned after logging when the step fails and the client
            does not throw errors.
        name (str): Step name used in the log message.
        errors (tuple[type[Exception], ...]): Failures the step handles. Anything
            else, and cancellation, propagates untouched.

    Returns:
        T | E: The step result, or ``empty`` when the step fails and
            ``_throw_errors`` is false. When ``_throw_errors`` is true, a failure
            is raised as an ``OPNsenseError`` subclass instead.
    """
    try:
        return await call()
    except errors as err:
        _handle_error(client, err, name)
        return empty
