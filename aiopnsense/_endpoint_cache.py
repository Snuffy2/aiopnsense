"""Cache of what the Router last said about each endpoint."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class _CachedAnswer(StrEnum):
    """What the Router last said about one endpoint."""

    PRESENT = "present"
    ABSENT = "absent"
    PERMISSION_DENIED = "permission_denied"


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    """One cached answer and the time the Router gave it.

    Attributes:
        answer (_CachedAnswer): The cached answer.
        checked_at (datetime): Time the Router answered, which starts the TTL.
    """

    answer: _CachedAnswer
    checked_at: datetime


@dataclass(frozen=True, slots=True)
class _CacheTicket:
    """Proof of the reset generation a request was sent under.

    Attributes:
        generation (int): Reset generation the cache was in when the ticket was issued.
    """

    generation: int


class _EndpointCache:
    """Answers per endpoint key, each fresh for a TTL, discarded by a reset.

    The cache knows nothing about the client, HTTP or the error policy. Callers pass
    every time in, and treat keys as opaque strings.
    """

    def __init__(self, ttl_seconds: float) -> None:
        """Create an empty cache.

        Args:
            ttl_seconds (float): Seconds an answer stays fresh after the Router gave it.
        """
        self._ttl_seconds = ttl_seconds
        self._entries: dict[str, _CacheEntry] = {}
        self._generation = 0

    def lookup(self, key: str, now: datetime) -> tuple[_CachedAnswer | None, _CacheTicket]:
        """Return the fresh answer for a key and a ticket for the current generation.

        An entry is fresh only while its age is strictly less than the TTL, so an age
        equal to the TTL is expired. A clock that moved backward gives a negative age,
        which is fresh. Lookup does not change the cache.

        Args:
            key (str): Opaque endpoint key.
            now (datetime): Timezone-aware time to measure the entry's age against.

        Returns:
            tuple[_CachedAnswer | None, _CacheTicket]: The fresh answer, or ``None`` when the
                entry is missing or expired, and the ticket to pass to ``record`` or
                ``clear`` for the request this lookup precedes.
        """
        ticket = _CacheTicket(self._generation)
        entry = self._entries.get(key)
        if entry is None or (now - entry.checked_at).total_seconds() >= self._ttl_seconds:
            return None, ticket
        return entry.answer, ticket

    def record(
        self, key: str, answer: _CachedAnswer, checked_at: datetime, ticket: _CacheTicket
    ) -> None:
        """Store an answer for a key, unless a reset happened since the ticket was issued.

        Args:
            key (str): Opaque endpoint key.
            answer (_CachedAnswer): Answer the Router gave.
            checked_at (datetime): Time the Router answered, which starts the TTL.
            ticket (_CacheTicket): Ticket from the lookup that preceded the request.
        """
        if ticket.generation == self._generation:
            self._entries[key] = _CacheEntry(answer, checked_at)

    def clear(self, key: str, ticket: _CacheTicket) -> None:
        """Remove a key's entry, unless a reset happened since the ticket was issued.

        Args:
            key (str): Opaque endpoint key.
            ticket (_CacheTicket): Ticket from the lookup that preceded the request.
        """
        if ticket.generation == self._generation:
            self._entries.pop(key, None)

    def reset(self) -> None:
        """Remove every entry and invalidate all outstanding tickets."""
        self._generation += 1
        self._entries.clear()
