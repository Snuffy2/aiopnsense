"""Single home of the ``throw_errors`` contract.

Public client methods either return a documented empty value after logging a
failure (``throw_errors`` off) or raise a subclass of ``OPNsenseError``
(``throw_errors`` on). Every raise-or-empty decision, the mapping of arbitrary
errors to the public exception hierarchy, the redacted exception logging and the
"already logged" marker live here. The policy marks exceptions it reports at the
default level before propagating them. Wrappers skip exceptions that the policy
or transport has already marked. Best-effort steps can log at debug and still
receive the usual outer report. Outer handlers report newly detected failures.
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


def _handle_error(
    client: _ThrowErrorsClient, error: Exception, name: str, log_level: int | None = None
) -> None:
    """Log one failure and raise its public form when the client throws errors.

    Args:
        client (_ThrowErrorsClient): Client whose ``_throw_errors`` setting selects
            between raising and returning to the caller.
        error (Exception): Caught failure to report.
        name (str): Operation name used in the log message.
        log_level (int | None): Level for the failure log record, for best-effort
            steps whose failure is routine. ``None`` logs timeouts as warnings
            and every other failure as errors.

    Raises:
        public_error: The failure itself when it is already an ``OPNsenseError``,
            otherwise its mapped ``OPNsenseError``, when ``_throw_errors`` is true.
            Unless ``log_level`` is given, it is marked as logged so wrappers
            further up do not log it again.
    """
    if not _is_request_error_logged(error):
        if log_level is not None:
            _LOGGER.log(log_level, "Error in %s. %s", name, _ExceptionLogDetails(error))
        elif isinstance(error, (TimeoutError, aiohttp.ServerTimeoutError, OPNsenseTimeoutError)):
            _LOGGER.warning("Timeout Error in %s. Will retry. %s", name, error)
        else:
            _LOGGER.error("Error in %s. %s", name, _ExceptionLogDetails(error))
    if not client._throw_errors:
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
    """Return whether the client raises public errors instead of returning empty values.

    Args:
        client (_ThrowErrorsClient): Client holding the ``_throw_errors`` setting.

    Returns:
        bool: ``True`` when failures are raised as ``OPNsenseError`` subclasses.
    """
    return client._throw_errors


def _error_if_throwing(client: _ThrowErrorsClient, error: OPNsenseError) -> OPNsenseError | None:
    """Return the error a request will raise, for marking it as already logged.

    Args:
        client (_ThrowErrorsClient): Client holding the ``_throw_errors`` setting.
        error (OPNsenseError): Public error the request raises when throwing.

    Returns:
        OPNsenseError | None: ``error`` when the client throws errors, otherwise ``None``.
    """
    return error if client._throw_errors else None


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
        client (_ThrowErrorsClient): Client whose ``_throw_errors`` setting selects
            between raising and returning to the caller.
        error (OPNsenseError): Public error to raise.
        cause (BaseException | None): Original failure that ``error`` was mapped
            from, chained as the cause.

    Raises:
        error: ``error`` itself when ``_throw_errors`` is true.
    """
    if client._throw_errors:
        if cause is None:
            raise error
        raise error from cause


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
    log_level: int | None = None,
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
        log_level (int | None): Level for the failure log record when the step is
            best-effort and its failure is routine. ``None`` uses the default
            levels.

    Returns:
        T | E: The step result, or ``empty`` when the step fails and
            ``_throw_errors`` is false. When ``_throw_errors`` is true, a failure
            is raised as an ``OPNsenseError`` subclass instead.
    """
    try:
        return await call()
    except errors as err:
        _handle_error(client, err, name, log_level)
        return empty
