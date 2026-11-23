"""Outcome of one availability-gated request to the Router."""

from collections.abc import MutableMapping
from dataclasses import dataclass
from enum import StrEnum
from json import JSONDecodeError
from typing import Any

from .exceptions import OPNsenseError, _opnsense_http_error


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
            transient failure.
        decode_error (UnicodeDecodeError | JSONDecodeError | None): Decoding error
            from a successful HTTP response.
        detail (str): Short failure description for log messages.
        observation_id (int): Client-local response order or dispatch order when
            no response was delivered. Zero for
            an outcome that did not come from transport.
    """

    status: _EndpointStatus
    payload: MutableMapping[str, Any] | list | str | None = None
    http_status: int | None = None
    error: OPNsenseError | None = None
    decode_error: UnicodeDecodeError | JSONDecodeError | None = None
    detail: str = ""
    observation_id: int = 0

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
    def absent(cls) -> _EndpointOutcome:
        """Build the outcome of an endpoint the Router does not provide.

        Returns:
            _EndpointOutcome: Outcome with the ``ABSENT`` status.
        """
        return cls(_EndpointStatus.ABSENT, http_status=404)

    @classmethod
    def from_http_status(
        cls, status: int, reason: str | None, cause: BaseException | None = None
    ) -> _EndpointOutcome:
        """Classify a non-successful HTTP response.

        Args:
            status (int): HTTP response status.
            reason (str | None): HTTP response reason phrase.
            cause (BaseException | None): Exception that reported the status. When given, it
                becomes the ``__cause__`` of the outcome's public error.

        Returns:
            _EndpointOutcome: ``ABSENT`` for 404 and 403, otherwise ``TRANSIENT``.
        """
        if status == 404:
            return cls.absent()
        error = _opnsense_http_error(status, reason)
        if cause is not None:
            error.__cause__ = cause
        detail = f"Response {status}: {reason}"
        if status == 403:
            return cls(_EndpointStatus.ABSENT, http_status=status, error=error, detail=detail)
        return cls(_EndpointStatus.TRANSIENT, http_status=status, error=error, detail=detail)

    @classmethod
    def transient(cls, error: OPNsenseError, detail: str) -> _EndpointOutcome:
        """Build the outcome of a transport failure.

        Args:
            error (OPNsenseError): Public exception for the failure.
            detail (str): Short failure description for log messages.

        Returns:
            _EndpointOutcome: Outcome with the ``TRANSIENT`` status.
        """
        return cls(_EndpointStatus.TRANSIENT, error=error, detail=detail)

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
