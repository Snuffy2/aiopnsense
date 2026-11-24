"""Tests for the endpoint availability cache.

The cache takes every time as an argument, so these tests use explicit times and
neither sleep nor patch a clock. Rules that involve dispatch or ``throw_errors`` are
covered at the client seam in ``tests/test_client_endpoint.py``.
"""

from datetime import UTC, datetime, timedelta

import pytest

from aiopnsense._endpoint_cache import _CachedAnswer, _EndpointCache

TTL = 100
START = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
KEY = "/api/test/endpoint"


def _cache() -> _EndpointCache:
    """Build a cache with a 100 second TTL.

    Returns:
        _EndpointCache: Empty cache.
    """
    return _EndpointCache(TTL)


def test_unknown_key_has_no_answer() -> None:
    """A key that was never recorded has no answer."""
    answer, _ = _cache().lookup(KEY, START)
    assert answer is None


@pytest.mark.parametrize("answer", list(_CachedAnswer))
def test_each_answer_kind_is_returned_while_fresh(answer: _CachedAnswer) -> None:
    """A recorded answer of any kind is returned unchanged while it is fresh.

    Args:
        answer (_CachedAnswer): Answer kind to record.
    """
    cache = _cache()
    _, ticket = cache.lookup(KEY, START)
    cache.record(KEY, answer, START, ticket)
    assert cache.lookup(KEY, START + timedelta(seconds=1))[0] is answer


def test_recording_replaces_the_previous_answer() -> None:
    """One key holds one answer; a new record replaces the old one entirely."""
    cache = _cache()
    _, ticket = cache.lookup(KEY, START)
    cache.record(KEY, _CachedAnswer.PERMISSION_DENIED, START, ticket)
    cache.record(KEY, _CachedAnswer.PRESENT, START, ticket)
    assert cache.lookup(KEY, START)[0] is _CachedAnswer.PRESENT


def test_age_one_second_below_ttl_is_fresh_and_equal_to_ttl_is_expired() -> None:
    """The TTL comparison is strict: an age equal to the TTL is expired."""
    cache = _cache()
    _, ticket = cache.lookup(KEY, START)
    cache.record(KEY, _CachedAnswer.ABSENT, START, ticket)
    assert cache.lookup(KEY, START + timedelta(seconds=TTL - 1))[0] is _CachedAnswer.ABSENT
    assert cache.lookup(KEY, START + timedelta(seconds=TTL))[0] is None
    assert cache.lookup(KEY, START + timedelta(seconds=TTL + 1))[0] is None


def test_backward_clock_counts_as_fresh() -> None:
    """When the clock moves backward the negative age is fresh."""
    cache = _cache()
    _, ticket = cache.lookup(KEY, START)
    cache.record(KEY, _CachedAnswer.PRESENT, START, ticket)
    assert cache.lookup(KEY, START - timedelta(days=1))[0] is _CachedAnswer.PRESENT


def test_lookup_does_not_change_the_cache() -> None:
    """Looking up an expired entry leaves it in place for a backward clock to find."""
    cache = _cache()
    _, ticket = cache.lookup(KEY, START)
    cache.record(KEY, _CachedAnswer.PRESENT, START, ticket)
    assert cache.lookup(KEY, START + timedelta(seconds=TTL))[0] is None
    assert cache.lookup(KEY, START)[0] is _CachedAnswer.PRESENT


def test_clear_removes_only_its_own_key() -> None:
    """Clearing one key leaves other keys and does not invalidate tickets."""
    cache = _cache()
    _, ticket = cache.lookup(KEY, START)
    cache.record(KEY, _CachedAnswer.PRESENT, START, ticket)
    cache.record("post:" + KEY, _CachedAnswer.ABSENT, START, ticket)
    cache.clear(KEY, ticket)
    assert cache.lookup(KEY, START)[0] is None
    assert cache.lookup("post:" + KEY, START)[0] is _CachedAnswer.ABSENT
    cache.record(KEY, _CachedAnswer.ABSENT, START, ticket)
    assert cache.lookup(KEY, START)[0] is _CachedAnswer.ABSENT


def test_clearing_an_unknown_key_does_nothing() -> None:
    """Clearing a key with no entry is not an error."""
    cache = _cache()
    _, ticket = cache.lookup(KEY, START)
    cache.clear(KEY, ticket)
    assert cache.lookup(KEY, START)[0] is None


def test_reset_discards_every_entry() -> None:
    """A reset removes all entries."""
    cache = _cache()
    _, ticket = cache.lookup(KEY, START)
    cache.record(KEY, _CachedAnswer.PRESENT, START, ticket)
    cache.record("post:" + KEY, _CachedAnswer.PERMISSION_DENIED, START, ticket)
    cache.reset()
    assert cache.lookup(KEY, START)[0] is None
    assert cache.lookup("post:" + KEY, START)[0] is None


def test_record_with_a_ticket_from_before_a_reset_does_nothing() -> None:
    """An answer to a request sent before a reset cannot be recorded."""
    cache = _cache()
    _, stale = cache.lookup(KEY, START)
    cache.reset()
    cache.record(KEY, _CachedAnswer.PRESENT, START, stale)
    assert cache.lookup(KEY, START)[0] is None


def test_clear_with_a_ticket_from_before_a_reset_keeps_the_newer_answer() -> None:
    """A stale request cannot clear an answer recorded after the reset."""
    cache = _cache()
    _, stale = cache.lookup(KEY, START)
    cache.reset()
    _, fresh = cache.lookup(KEY, START)
    cache.record(KEY, _CachedAnswer.PRESENT, START, fresh)
    cache.clear(KEY, stale)
    assert cache.lookup(KEY, START)[0] is _CachedAnswer.PRESENT


def test_ticket_issued_after_a_reset_is_accepted() -> None:
    """A lookup after a reset issues a ticket the cache accepts."""
    cache = _cache()
    cache.reset()
    _, ticket = cache.lookup(KEY, START)
    cache.record(KEY, _CachedAnswer.ABSENT, START, ticket)
    assert cache.lookup(KEY, START)[0] is _CachedAnswer.ABSENT
