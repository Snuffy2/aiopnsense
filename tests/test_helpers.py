"""Tests for `aiopnsense.helpers` utility and decorator helpers."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone

import pytest

from aiopnsense import OPNsenseClient, helpers as aiopnsense_helpers
from tests.conftest import FIRMWARE_RELEASE_ORDER, FIRMWARE_VERSION_SHAPES

ClientType = Callable[..., OPNsenseClient]


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        pytest.param(0, "0 seconds", id="zero-seconds"),
        pytest.param(1, "1 second", id="singular-second"),
        pytest.param(2, "2 seconds", id="plural-seconds"),
        pytest.param(60, "1 minute", id="singular-minute"),
        pytest.param(61, "1 minute, 1 second", id="minute-and-singular-second"),
        pytest.param(65, "1 minute, 5 seconds", id="minute-and-plural-seconds"),
        pytest.param(3600, "1 hour", id="singular-hour"),
        pytest.param(7200, "2 hours", id="plural-hours"),
        pytest.param(86400, "1 day", id="singular-day"),
        pytest.param(604800, "1 week", id="singular-week"),
        pytest.param(1209600, "2 weeks", id="plural-weeks"),
        pytest.param(2419200, "1 month", id="singular-month"),
        pytest.param(4838400, "2 months", id="plural-months"),
    ],
)
def test_human_friendly_duration(seconds: int, expected: str) -> None:
    """Convert seconds to exact human-friendly duration strings.

    Args:
        seconds (int): Duration to format, in seconds.
        expected (str): Expected human-friendly duration.

    Returns:
        None: This test validates formatted output via assertions.
    """
    assert aiopnsense_helpers.human_friendly_duration(seconds) == expected


def test_get_ip_key() -> None:
    """Compute sorting key for IP addresses across IPv4, IPv6, and invalid forms."""
    assert aiopnsense_helpers.get_ip_key({"address": "192.168.1.1"})[0] == 0
    assert aiopnsense_helpers.get_ip_key({"address": "::1"})[0] == 1
    assert aiopnsense_helpers.get_ip_key({"address": "notanip"})[0] == 2
    assert aiopnsense_helpers.get_ip_key({"address": "notanip"}) == (2, "")
    assert aiopnsense_helpers.get_ip_key({})[0] == 3
    assert aiopnsense_helpers.get_ip_key({}) == (3, "")


def test_dict_get() -> None:
    """Retrieve nested values from dicts and lists using dotted paths."""
    data = {"a": {"b": {"c": 1}}, "x": [0, 1, 2]}
    assert aiopnsense_helpers.dict_get(data, "a.b.c") == 1
    assert aiopnsense_helpers.dict_get(data, "x.1") == 1
    assert aiopnsense_helpers.dict_get(data, "x.10", default=42) == 42
    assert aiopnsense_helpers.dict_get({"a": {"b": [10, {"c": 3}]}}, "a.b") == [10, {"c": 3}]
    assert aiopnsense_helpers.dict_get(data, "missing.path", default=5) == 5


def test_timestamp_to_datetime() -> None:
    """Convert timestamp integers to datetime objects, handling None."""
    ts = int(datetime.now(tz=UTC).timestamp())
    dt = aiopnsense_helpers.timestamp_to_datetime(ts)
    assert isinstance(dt, datetime)
    assert dt.tzinfo is not None
    assert aiopnsense_helpers.timestamp_to_datetime(None) is None


@pytest.mark.parametrize(
    ("value", "default_tz", "expected"),
    [
        ("2026-03-14T03:09:45", timezone(timedelta(hours=-4)), "2026-03-14T03:09:45-04:00"),
        ("2023-01-22 00:29:00", timezone(timedelta(hours=-4)), "2023-01-22T00:29:00-04:00"),
        ("2026-03-14T03:09:45+01:30", timezone(timedelta(hours=-4)), "2026-03-14T03:09:45+01:30"),
        ("2026-03-14T03:09:45", None, None),
        ("2026-03-14T03:09:45+01:30", None, "2026-03-14T03:09:45+01:30"),
        ("not-a-date", timezone(timedelta(hours=-4)), None),
        (12345, timezone(timedelta(hours=-4)), None),
    ],
)
def test_normalize_datetime(
    value: object, default_tz: timezone | None, expected: str | None
) -> None:
    """Normalize naive and aware datetimes while rejecting malformed values.

    Args:
        value (object): Raw datetime value under test.
        default_tz (timezone | None): Fallback timezone for naive values.
        expected (str | None): Expected timezone-aware ISO result.

    Returns:
        None: This test validates helper output via assertions.
    """
    assert (
        aiopnsense_helpers.normalize_datetime(
            value,
            default_tz,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("firmware_version", "expected"),
    [
        *(
            pytest.param(shape.product_version, shape.trimmed, id=shape.id)
            for shape in FIRMWARE_VERSION_SHAPES
        ),
        pytest.param("   ", None, id="blank"),
    ],
)
def test_trim_firmware_suffix_handles_empty_and_suffixed_versions(
    firmware_version: str | None,
    expected: str | None,
) -> None:
    """Verify firmware suffix trimming handles empty and revision-suffixed values.

    Args:
        firmware_version (str | None): Firmware version value to trim.
        expected (str | None): Expected comparable firmware version.

    Returns:
        None: This test validates trim output via assertions.
    """
    assert aiopnsense_helpers.trim_firmware_suffix(firmware_version) == expected


@pytest.mark.parametrize(
    ("firmware_version", "minimum_version", "expected"),
    [
        pytest.param("25.1", "25.1", True, id="identical"),
        pytest.param("25.1.0", "25.1", True, id="trailing-zero-on-firmware"),
        pytest.param("25.1", "25.1.0", True, id="trailing-zero-on-minimum"),
        pytest.param("25.1.0", "25.1.0.0", True, id="different-trailing-zero-counts"),
        pytest.param("25.1.1", "25.1", True, id="newer-patch"),
        pytest.param("25.1", "25.1.1", False, id="older-than-patch-minimum"),
        pytest.param("25.1.0", "25.1.1", False, id="zero-padded-older-than-patch-minimum"),
        pytest.param("25.0", "25.1", False, id="older-minor"),
        pytest.param("25.0.0", "25.1", False, id="zero-padded-older-minor"),
        pytest.param("25.1.0_2", "25.1", True, id="build-suffix-with-trailing-zero"),
        pytest.param("26.1.1_3", "26.1.2", False, id="build-suffix-older-patch"),
    ],
)
def test_firmware_is_at_least_ignores_trailing_zero_components(
    firmware_version: str, minimum_version: str, expected: bool
) -> None:
    """Treat versions differing only by trailing zero components as equal.

    Args:
        firmware_version (str): Firmware version reported by the Router.
        minimum_version (str): Minimum firmware version to satisfy.
        expected (bool): Whether the firmware satisfies the minimum.
    """
    assert aiopnsense_helpers.firmware_is_at_least(firmware_version, minimum_version) is expected


@pytest.mark.parametrize(
    ("firmware_version", "minimum_version", "expected"),
    [
        pytest.param(shape.product_version, minimum, expected, id=f"{shape.id}-vs-{minimum}")
        for shape in FIRMWARE_VERSION_SHAPES
        for minimum, expected in shape.at_least.items()
    ],
)
def test_firmware_is_at_least_handles_router_version_shapes(
    firmware_version: str | None, minimum_version: str, expected: bool | None
) -> None:
    """Compare each version form a Router reports against the minimum versions.

    Args:
        firmware_version (str | None): Firmware version reported by the Router.
        minimum_version (str): Minimum firmware version to satisfy.
        expected (bool | None): Expected result, or ``None`` when not comparable.
    """
    assert aiopnsense_helpers.firmware_is_at_least(firmware_version, minimum_version) is expected


@pytest.mark.parametrize(
    ("firmware_version", "minimum_version"),
    [
        pytest.param("", "25.1", id="empty-firmware"),
        pytest.param("   ", "25.1", id="blank-firmware"),
        pytest.param("not-a-version", "25.1", id="unparsable-firmware"),
        pytest.param("25.1", "not-a-version", id="unparsable-minimum"),
    ],
)
def test_firmware_is_at_least_returns_none_for_uncomparable_versions(
    firmware_version: str | None, minimum_version: str
) -> None:
    """Return ``None`` when the firmware or minimum cannot be compared.

    Args:
        firmware_version (str | None): Firmware version reported by the Router.
        minimum_version (str): Minimum firmware version to satisfy.
    """
    assert aiopnsense_helpers.firmware_is_at_least(firmware_version, minimum_version) is None


@pytest.mark.parametrize(
    ("firmware_version", "comparison_version", "expected"),
    [
        ("26.7.6_0", "26.7.6", False),
        ("26.7.6_3", "26.7.6.0", True),
        ("26.7.6.0", "26.7.6_3", False),
        (" 26.7.6_3 ", " 26.7.6 ", True),
        ("26.7.6rc1", "26.7.6", False),
        ("26.7.6", "26.7.6rc1", True),
    ],
)
def test_firmware_is_newer_orders_base_versions_and_hotfix_revisions(
    firmware_version: str, comparison_version: str, expected: bool
) -> None:
    """Order firmware bases before numeric hotfix revisions.

    Args:
        firmware_version (str): Candidate firmware version.
        comparison_version (str): Firmware version to compare against.
        expected (bool): Whether the candidate is newer.
    """
    assert aiopnsense_helpers.firmware_is_newer(firmware_version, comparison_version) is expected


def test_firmware_is_newer_follows_router_release_order() -> None:
    """Rank every pair of real releases, in both directions, by release order."""
    wrong_results = [
        (candidate, other, result)
        for candidate_index, candidate in enumerate(FIRMWARE_RELEASE_ORDER)
        for other_index, other in enumerate(FIRMWARE_RELEASE_ORDER)
        if (result := aiopnsense_helpers.firmware_is_newer(candidate, other))
        is not (candidate_index > other_index)
    ]

    assert wrong_results == []


@pytest.mark.parametrize(
    "beta_version",
    [
        pytest.param(shape.product_version, id=shape.id)
        for shape in FIRMWARE_VERSION_SHAPES
        if shape.product_version is not None and not shape.version_is_comparable
    ],
)
def test_firmware_is_newer_cannot_rank_beta_against_releases(beta_version: str) -> None:
    """Report a beta as not comparable against every real release, in both directions.

    Args:
        beta_version (str): Beta version reported by the Router.
    """
    for release in FIRMWARE_RELEASE_ORDER:
        assert aiopnsense_helpers.firmware_is_newer(beta_version, release) is None
        assert aiopnsense_helpers.firmware_is_newer(release, beta_version) is None


@pytest.mark.parametrize(
    ("firmware_version", "comparison_version"),
    [
        (None, "26.1.11"),
        ("   ", "26.1.11"),
        ("26.1.11_bad", "26.1.10"),
        ("26.1.11", "   "),
        ("26.1.11", None),
        ("26.1.11", "26.1.11_bad"),
        ("26.1.11_", "26.1.11"),
        ("_1", "26.1.11"),
        ("invalid_3", "invalid_2"),
        ("26.1.11", "invalid_2"),
    ],
)
def test_firmware_is_newer_returns_none_for_uncomparable_versions(
    firmware_version: str | None,
    comparison_version: str | None,
) -> None:
    """Verify uncomparable firmware update versions return ``None``.

    Args:
        firmware_version (str | None): Candidate firmware version.
        comparison_version (str | None): Firmware version to compare against.

    Returns:
        None: This test validates uncomparable-version handling via assertions.
    """
    assert aiopnsense_helpers.firmware_is_newer(firmware_version, comparison_version) is None


def test_firmware_is_newer_returns_none_when_version_comparison_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify firmware update comparison failures return ``None``.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for replacing AwesomeVersion.

    Returns:
        None: This test validates comparison exception handling via assertions.
    """

    class RaisingAwesomeVersion:
        """AwesomeVersion stand-in that raises during construction."""

        def __init__(self, _version: str) -> None:
            """Raise a comparison setup error.

            Args:
                _version (str): Version value passed by the helper.

            Raises:
                ValueError: Always raised to simulate a comparison failure.
            """
            raise ValueError("comparison failed")

    monkeypatch.setattr(aiopnsense_helpers.awesomeversion, "AwesomeVersion", RaisingAwesomeVersion)

    assert aiopnsense_helpers.firmware_is_newer("26.1.11", "26.1.10") is None


def test_try_to_int_and_float() -> None:
    """Coerce numeric-like strings to int/float with defaults."""
    assert aiopnsense_helpers.try_to_int("5") == 5
    assert aiopnsense_helpers.try_to_int(None, 7) == 7
    assert aiopnsense_helpers.try_to_float("5.5") == 5.5
    assert aiopnsense_helpers.try_to_float(None, 3.3) == 3.3


def test_coerce_bool() -> None:
    """Verify ``coerce_bool`` handles common bool-like edge cases."""
    assert aiopnsense_helpers.coerce_bool(True) is True
    assert aiopnsense_helpers.coerce_bool(False) is False
    assert aiopnsense_helpers.coerce_bool(1) is True
    assert aiopnsense_helpers.coerce_bool(0) is False
    assert aiopnsense_helpers.coerce_bool(0.0) is False
    assert aiopnsense_helpers.coerce_bool("1") is True
    assert aiopnsense_helpers.coerce_bool("true") is True
    assert aiopnsense_helpers.coerce_bool("yes") is True
    assert aiopnsense_helpers.coerce_bool("on") is True
    assert aiopnsense_helpers.coerce_bool("") is False
    assert aiopnsense_helpers.coerce_bool(None) is False


def test_normalize_lookup_token() -> None:
    """Verify ``normalize_lookup_token`` lower-cases and trims lookup values."""
    assert aiopnsense_helpers.normalize_lookup_token("Hello") == "hello"
    assert aiopnsense_helpers.normalize_lookup_token("  WORLD  ") == "world"
    assert aiopnsense_helpers.normalize_lookup_token(42) == "42"
    assert aiopnsense_helpers.normalize_lookup_token(None) == ""


@pytest.mark.parametrize(
    ("value", "expected", "matches"),
    [
        ("0", "0", True),
        (0, "0", True),
        (False, "0", True),
        ("1", "1", True),
        (1, "1", True),
        (True, "1", True),
        ("active", "active", True),
        (False, "1", False),
        (True, "0", False),
        (1, "0", False),
        (None, "0", False),
    ],
)
def test_api_value_matches(value: object, expected: str, matches: bool) -> None:
    """Compare API flag values consistently across mixed payload types.

    Args:
        value (object): API payload value evaluated for equivalence.
        expected (str): Normalized string expected from the API value.
        matches (bool): Whether the normalized comparison should succeed.
    """
    assert aiopnsense_helpers.api_value_matches(value, expected) is matches


def test_get_ip_key_sorting() -> None:
    """Sort IP-like items using get_ip_key ordering."""
    items = [
        {"address": "192.168.1.2"},
        {"address": "::1"},
        {"address": "notanip"},
        {},
    ]
    sorted_items = sorted(items, key=aiopnsense_helpers.get_ip_key)
    assert sorted_items[0]["address"] == "192.168.1.2"
    assert sorted_items[1]["address"] == "::1"
    assert sorted_items[2]["address"] == "notanip"
    assert sorted_items[3] == {}
