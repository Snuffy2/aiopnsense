"""Outcome of one availability-gated request to the Router."""

from collections.abc import MutableMapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .exceptions import OPNsenseError


class _EndpointStatus(StrEnum):
    """What the Router said about one endpoint request."""

    DATA = "data"
    ABSENT = "absent"
    TRANSIENT = "transient"


@dataclass(frozen=True, slots=True)
class _EndpointOutcome:
    """Result of a single request that also tells absent from transient failure.

    Attributes:
        status (_EndpointStatus): ``DATA`` for a successful response, ``ABSENT``
            when the Router reports the endpoint missing (404) or not permitted
            (403), and ``TRANSIENT`` for any other failure.
        payload (MutableMapping[str, Any] | list | str | None): Decoded response
            body when ``status`` is ``DATA``: JSON, or the body text for a text read.
        http_status (int | None): HTTP status of a failed response, when known.
        error (OPNsenseError | None): Public exception describing a 403 or a
            transient failure. Absent 404 outcomes carry none.
    """

    status: _EndpointStatus
    payload: MutableMapping[str, Any] | list | str | None = None
    http_status: int | None = None
    error: OPNsenseError | None = None

    @classmethod
    def data(cls, payload: MutableMapping[str, Any] | list | str | None) -> _EndpointOutcome:
        """Build the outcome of a successful request.

        Args:
            payload (MutableMapping[str, Any] | list | str | None): Decoded JSON body, or
                the body text for a text read.

        Returns:
            _EndpointOutcome: Outcome with the ``DATA`` status.
        """
        return cls(_EndpointStatus.DATA, payload=payload)

    @classmethod
    def absent(cls, http_status: int = 404) -> _EndpointOutcome:
        """Build the outcome of an endpoint the Router does not provide.

        Args:
            http_status (int): HTTP status that reported the absence, 404 or a cached 403.

        Returns:
            _EndpointOutcome: Outcome with the ``ABSENT`` status.
        """
        return cls(_EndpointStatus.ABSENT, http_status=http_status)

    @classmethod
    def from_http_error(cls, status: int, error: OPNsenseError) -> _EndpointOutcome:
        """Classify a failed HTTP response that the request already reported.

        Args:
            status (int): HTTP response status, not 2xx. A 404 reaches here only from a
                plain request, which reports it as a failure; an outcome request returns
                its unreported 404 through ``absent`` instead.
            error (OPNsenseError): Public exception mapped from the response.

        Returns:
            _EndpointOutcome: ``ABSENT`` with its error attached for 403 or 404,
                otherwise ``TRANSIENT``.
        """
        if status in (403, 404):
            return cls(_EndpointStatus.ABSENT, http_status=status, error=error)
        return cls(_EndpointStatus.TRANSIENT, http_status=status, error=error)

    @classmethod
    def transient(cls, error: OPNsenseError) -> _EndpointOutcome:
        """Build the outcome of a transport failure.

        Args:
            error (OPNsenseError): Public exception for the failure.

        Returns:
            _EndpointOutcome: Outcome with the ``TRANSIENT`` status.
        """
        return cls(_EndpointStatus.TRANSIENT, error=error)

    @property
    def is_data(self) -> bool:
        """Return whether the request succeeded.

        Returns:
            bool: ``True`` when ``status`` is ``DATA``.
        """
        return self.status is _EndpointStatus.DATA

    @property
    def is_absent(self) -> bool:
        """Return whether the Router reported the endpoint missing or not permitted.

        Returns:
            bool: ``True`` when ``status`` is ``ABSENT``.
        """
        return self.status is _EndpointStatus.ABSENT

    @property
    def is_transient(self) -> bool:
        """Return whether the request failed in a way that says nothing about the endpoint.

        Returns:
            bool: ``True`` when ``status`` is ``TRANSIENT``.
        """
        return self.status is _EndpointStatus.TRANSIENT

    def mapping(self) -> dict[str, Any]:
        """Return the payload as a dictionary.

        Returns:
            dict[str, Any]: Copy of a mapping payload, otherwise an empty dictionary.
        """
        return dict(self.payload) if isinstance(self.payload, MutableMapping) else {}

    def sequence(self) -> list:
        """Return the payload as a list.

        Returns:
            list: The list payload, otherwise an empty list.
        """
        return self.payload if isinstance(self.payload, list) else []

    def text(self) -> str:
        """Return the payload as body text.

        Returns:
            str: The text payload of a text read, otherwise an empty string.
        """
        return self.payload if isinstance(self.payload, str) else ""
