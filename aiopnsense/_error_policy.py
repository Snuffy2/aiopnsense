"""Centralize throw-or-return decisions and shared wrapper error handling.

Public client methods either return a documented empty value after logging a
failure (``throw_errors`` off) or raise a subclass of ``OPNsenseError``
(``throw_errors`` on). Request, endpoint and queue layers also map and log
errors. The policy marks exceptions it reports at the default level before
propagating them. Wrappers skip exceptions that the policy or transport has
already marked. Best-effort steps can log at debug and still receive the usual
outer report. Outer handlers report newly detected failures.
"""

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial, wraps
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

_STRICT_ERROR_CLIENT: ContextVar[object | None] = ContextVar("_STRICT_ERROR_CLIENT", default=None)


class _ThrowErrorsClient(Protocol):
    """Client state the error policy reads."""

    _throw_errors: bool


@contextmanager
def _strict_error_scope(client: object | None) -> Iterator[None]:
    """Scope the strict override to one client in the current context.

    Args:
        client (object | None): Client to make strict, or ``None`` to clear the override.

    Yields:
        None: The override stays in effect until the scope exits.
    """
    token = _STRICT_ERROR_CLIENT.set(client)
    try:
        yield
    finally:
        _STRICT_ERROR_CLIENT.reset(token)


def _has_strict_error_override(client: object) -> bool:
    """Return the scoped override separately from the client's configured default.

    Args:
        client (object): Client whose override is being checked.

    Returns:
        bool: Whether this context forces the client to raise errors.
    """
    return _STRICT_ERROR_CLIENT.get() is client


def _handle_error(
    client: _ThrowErrorsClient, error: Exception, name: str, log_level: int | None = None
) -> None:
    """Log one failure and raise its public form when the client throws errors.

    Args:
        client (_ThrowErrorsClient): Client whose effective error mode selects
            between raising and returning to the caller.
        error (Exception): Caught failure to report.
        name (str): Operation name used in the log message.
        log_level (int | None): Level for the failure log record, for best-effort
            steps whose failure is routine. ``None`` logs timeouts as warnings
            and every other failure as errors.

    Raises:
        public_error: The failure itself when it is already an ``OPNsenseError``,
            otherwise its mapped ``OPNsenseError``, in strict mode.
            Unless ``log_level`` is given, it is marked as logged so wrappers
            further up do not log it again.
    """
    if not _is_request_error_logged(error):
        if log_level is not None:
            _LOGGER.log(log_level, "Error in %s. %s", name, _ExceptionLogDetails(error))
        elif isinstance(error, (TimeoutError, aiohttp.ServerTimeoutError, OPNsenseTimeoutError)):
            _LOGGER.warning(
                "Timeout Error in %s. Will retry. %s", name, _ExceptionLogDetails(error)
            )
        else:
            _LOGGER.error("Error in %s. %s", name, _ExceptionLogDetails(error))
    if not _throws_errors(client):
        return
    public_error = _map_opnsense_exception(error)
    if log_level is None:
        # A best-effort step logs below the normal level, so wrappers further up
        # still report the failure at the normal level.
        _mark_request_error_logged(public_error)
    if public_error is error:
        raise public_error
    raise public_error from error


def _throws_errors(client: _ThrowErrorsClient) -> bool:
    """Resolve the strict override or read the client's current default.

    Args:
        client (_ThrowErrorsClient): Client whose effective error mode is used.

    Returns:
        bool: ``True`` when failures are raised as ``OPNsenseError`` subclasses.
    """
    return _has_strict_error_override(client) or client._throw_errors


def _error_if_throwing(client: _ThrowErrorsClient, error: OPNsenseError) -> OPNsenseError | None:
    """Return the error a request will raise, for marking it as already logged.

    Args:
        client (_ThrowErrorsClient): Client whose effective error mode is used.
        error (OPNsenseError): Public error the request raises when throwing.

    Returns:
        OPNsenseError | None: ``error`` when the client throws errors, otherwise ``None``.
    """
    return error if _throws_errors(client) else None


def _raise_if_throwing(
    client: _ThrowErrorsClient, error: OPNsenseError, cause: BaseException | None = None
) -> None:
    """Raise an already-public error when the client throws errors.

    For a failure the caller detected without catching an exception, such as an
    unusable result, and for a transport error that was already logged. The error
    is raised as given and its logged marker is left as it is, so a wrapper
    further up logs a freshly detected failure once and does not repeat one that
    was already logged.

    Args:
        client (_ThrowErrorsClient): Client whose effective error mode selects
            between raising and returning to the caller.
        error (OPNsenseError): Public error to raise.
        cause (BaseException | None): Original failure that ``error`` was mapped
            from, chained as the cause.

    Raises:
        error: ``error`` itself in strict mode.
    """
    if _throws_errors(client):
        if cause is None:
            raise error
        raise error from cause


def _log_errors(func: Callable[..., Any], *, fallback: bool | None = None) -> Callable[..., Any]:
    """Wrap coroutine methods with shared timeout/error logging behavior.

    Args:
        func (Callable[..., Any]): Coroutine function to decorate and execute
            with shared behavior.
        fallback (bool | None): Result when an error is suppressed.

    Returns:
        Callable[..., Any]: Decorator wrapper that applies shared exception
            logging and returns the wrapped coroutine result, or the fallback when an
            error is suppressed.
    """

    @wraps(func)
    async def inner(self: Any, *args: Any, **kwargs: Any) -> Any:
        """Execute wrapped coroutine with shared timeout/exception logging.

        Args:
            self (Any): Client instance whose effective error mode controls
                whether caught errors are mapped and propagated or logged and
                suppressed.
            args (Any): Positional arguments forwarded to the wrapped coroutine.
            kwargs (Any): Keyword arguments forwarded to the wrapped coroutine.

        Returns:
            Any: Wrapped coroutine result, or the fallback when an error is
                suppressed. In strict mode, caught errors are
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
        return fallback

    return inner


_log_boolean_errors = partial(_log_errors, fallback=False)
"""Log suppressed errors and return False for methods with boolean results."""


async def _attempt[T, E](
    client: _ThrowErrorsClient,
    call: Callable[[], Awaitable[T]],
    *,
    empty: E,
    name: str,
    errors: tuple[type[Exception], ...] = _DEFAULT_SCOPED_ERRORS,
    log_level: int | None = None,
) -> T | E:
    """Run one step of a public method that has its own documented empty value.

    Args:
        client (_ThrowErrorsClient): Client whose effective error mode selects
            between raising and returning ``empty``.
        call (Callable[[], Awaitable[T]]): Zero-argument callable that performs the step.
        empty (E): Value returned after logging when the step fails and the client
            does not throw errors.
        name (str): Step name used in the log message.
        errors (tuple[type[Exception], ...]): Failures the step handles. Anything
            else, and cancellation, propagates untouched.
        log_level (int | None): Level for the failure log record when the step is
            best-effort and its failure is routine. ``None`` uses the default
            levels.

    Returns:
        T | E: The step result, or ``empty`` when the step fails and
            errors are suppressed. In strict mode, a failure
            is raised as an ``OPNsenseError`` subclass instead.
    """
    try:
        return await call()
    except errors as err:
        _handle_error(client, err, name, log_level)
        return empty
