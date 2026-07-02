"""Shared aiopnsense helper utilities and decorators."""

import asyncio
from collections.abc import Callable, MutableMapping
from datetime import UTC, datetime
from functools import wraps
import ipaddress
import logging
import re
import traceback
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
import awesomeversion

_LOGGER: logging.Logger = logging.getLogger(__name__)


def _log_errors(func: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap coroutine methods with shared timeout/error logging behavior.

    Args:
        func (Callable[..., Any]): Coroutine function to decorate and execute
            with shared behavior.

    Returns:
        Callable[..., Any]: Decorator wrapper that applies shared exception
            logging and returns the wrapped coroutine result.
    """

    @wraps(func)
    async def inner(self: Any, *args: Any, **kwargs: Any) -> Any:
        """Execute wrapped coroutine with shared timeout/exception logging.

        Args:
            *args (Any): Positional arguments forwarded to the wrapped callable.
            **kwargs (Any): Keyword arguments forwarded to the wrapped callable.

        Returns:
            Any: Value produced by the wrapped callable, or ``None`` when an
                error is suppressed.
        """
        try:
            return await func(self, *args, **kwargs)
        except asyncio.CancelledError:
            raise
        except (TimeoutError, aiohttp.ServerTimeoutError) as e:
            _LOGGER.warning("Timeout Error in %s. Will retry. %s", func.__name__.strip("_"), e)
            if self._throw_errors:
                raise
        except Exception as e:
            redacted_message = re.sub(
                r"(://)([^:/@\s]+):([^@\s]+)@",
                r"\1<redacted>:<redacted>@",
                str(e),
            )
            _LOGGER.error(
                "Error in %s. %s: %s\n%s",
                func.__name__.strip("_"),
                type(e).__name__,
                redacted_message,
                "".join(traceback.format_tb(e.__traceback__)),
            )
            if self._throw_errors:
                raise
        return None

    return inner


def human_friendly_duration(seconds: int) -> str:
    """Convert a duration in seconds into a human-readable string.

    Args:
        seconds (int): Duration value, in seconds.

    Returns:
        str: Duration rendered as a readable string with
            month/week/day/hour/minute/second units.
    """
    months, seconds = divmod(
        seconds, 2419200
    )  # 28 days in a month (28 * 24 * 60 * 60 = 2419200 seconds)
    weeks, seconds = divmod(seconds, 604800)  # 604800 seconds in a week
    days, seconds = divmod(seconds, 86400)  # 86400 seconds in a day
    hours, seconds = divmod(seconds, 3600)  # 3600 seconds in an hour
    minutes, seconds = divmod(seconds, 60)  # 60 seconds in a minute

    duration: list = []
    if months > 0:
        duration.append(f"{months} month{'s' if months > 1 else ''}")
    if weeks > 0:
        duration.append(f"{weeks} week{'s' if weeks > 1 else ''}")
    if days > 0:
        duration.append(f"{days} day{'s' if days > 1 else ''}")
    if hours > 0:
        duration.append(f"{hours} hour{'s' if hours > 1 else ''}")
    if minutes > 0:
        duration.append(f"{minutes} minute{'s' if minutes > 1 else ''}")
    if seconds > 0 or not duration:
        duration.append(f"{seconds} second{'s' if seconds != 1 else ''}")

    return ", ".join(duration)


def trim_firmware_suffix(firmware_version: str | None) -> str | None:
    """Return the version part before an optional build metadata suffix.

    Args:
        firmware_version (str | None): Raw version string from OPNsense.

    Returns:
        str | None: Trimmed version if parseable as a non-empty string, otherwise ``None``.
    """
    if not firmware_version:
        return None
    trimmed_version = firmware_version.strip()
    if not trimmed_version:
        return None
    return trimmed_version.split("_", 1)[0]


def firmware_is_at_least(firmware_version: str | None, minimum_version: str) -> bool | None:
    """Compare firmware versions after trimming optional build suffixes.

    Args:
        firmware_version (str | None): Raw firmware version reported by OPNsense.
        minimum_version (str): Minimum comparable firmware version.

    Returns:
        bool | None: ``True`` when the installed version is at or above the
            minimum, ``False`` when it is below the minimum, or ``None`` when
            the version cannot be compared.
    """
    comparable_firmware = trim_firmware_suffix(firmware_version)
    if comparable_firmware is None:
        return None
    try:
        return awesomeversion.AwesomeVersion(comparable_firmware) >= awesomeversion.AwesomeVersion(
            minimum_version
        )
    except (
        awesomeversion.exceptions.AwesomeVersionCompareException,
        TypeError,
        ValueError,
    ):
        return None


def get_ip_key(item: MutableMapping[str, Any]) -> tuple:
    """Produce a sorting key for DHCP leases based on their IP addresses.

    Args:
        item (MutableMapping[str, Any]): Lease record used to derive an IP-aware
            sort key.

    Returns:
        tuple: Sort key that prioritizes valid IPv4/IPv6 addresses and pushes
            invalid or empty entries last.
    """
    address = item.get("address", None)

    if not address:
        # If the address is empty, place it at the end
        return (3, "")
    try:
        ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(address)
    except ValueError:
        return (2, "")
    else:
        # Sort by IP version (IPv4 first, IPv6 second), then by numerical value
        return (0 if ip_obj.version == 4 else 1, ip_obj)


def dict_get(data: MutableMapping[str, Any], path: str, default: Any | None = None) -> Any | None:
    """Extract a nested value from a mapping using dot notation.

    Args:
        data (MutableMapping[str, Any]): Source mapping to traverse.
        path (str): Dot-separated lookup path, including numeric list indexes.
        default (Any | None): Value returned when the path does not exist.

    Returns:
        Any | None: Nested value resolved from the dotted path, or the default
            when the path is missing.
    """
    path_parts: list = re.split(r"\.", path, flags=re.IGNORECASE)
    result: Any | None = data
    for key in path_parts:
        if key.isnumeric():
            key = int(key)
        if (isinstance(result, MutableMapping) and key in result) or (
            isinstance(result, list) and isinstance(key, int) and 0 <= key < len(result)
        ):
            result = result[key]
        else:
            result = default
            break

    return result


def timestamp_to_datetime(timestamp: int | None) -> datetime | None:
    """Convert a Unix timestamp into a timezone-aware datetime.

    Args:
        timestamp (int | None): Unix timestamp value to convert.

    Returns:
        datetime | None: Timezone-aware datetime derived from the timestamp, or
            ``None`` if no timestamp was provided.
    """
    if timestamp is None:
        return None
    utc_datetime = datetime.fromtimestamp(int(timestamp), tz=UTC)
    local_tzinfo = datetime.now().astimezone().tzinfo
    if isinstance(local_tzinfo, ZoneInfo):
        return utc_datetime.astimezone(local_tzinfo)
    local_tz_key = getattr(local_tzinfo, "key", None)
    if isinstance(local_tz_key, str):
        try:
            return utc_datetime.astimezone(ZoneInfo(local_tz_key))
        except ZoneInfoNotFoundError:
            pass
    return utc_datetime.astimezone()


def try_to_int(value: Any | None, retval: int | None = None) -> int | None:
    """Convert a value to ``int`` and return a fallback on conversion failure.

    Args:
        value (Any | None): Value to coerce.
        retval (int | None): Value returned when conversion fails.

    Returns:
        int | None: Converted integer value, or ``retval`` when conversion is
            not possible.
    """
    if value is None:
        return retval
    try:
        return int(value)
    except ValueError, TypeError:
        return retval


def try_to_float(value: Any | None, retval: float | None = None) -> float | None:
    """Convert a value to ``float`` and return a fallback on conversion failure.

    Args:
        value (Any | None): Value to coerce.
        retval (float | None): Value returned when conversion fails.

    Returns:
        float | None: Converted float value, or ``retval`` when conversion is
            not possible.
    """
    if value is None:
        return retval
    try:
        return float(value)
    except ValueError, TypeError:
        return retval


def coerce_bool(value: Any) -> bool:
    """Normalize values that may represent booleans.

    Args:
        value (Any): Arbitrary state value returned by backend APIs.

    Returns:
        bool: Parsed boolean interpretation for common numeric/string variants.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def normalize_lookup_token(value: Any) -> str:
    """Normalize values for case-insensitive token matching.

    Args:
        value (Any): Arbitrary value to normalize.

    Returns:
        str: Lower-cased, stripped string token used for comparisons.
    """
    if value is None:
        return ""
    return str(value).strip().lower()


def api_value_matches(value: object, expected: str) -> bool:
    """Compare OPNsense API values across string, numeric, and boolean forms.

    Args:
        value: Raw value returned by OPNsense APIs.
        expected: Normalized expected value.

    Returns:
        ``True`` when the normalized API value matches ``expected``.
    """
    if isinstance(value, bool):
        value = int(value)
    return str(value) == expected
