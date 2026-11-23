"""Endpoint value carrying both Endpoint Style spellings of one Router path."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class _StyledEndpoint:
    """One Router endpoint that exists in both Endpoint Styles.

    The value deliberately has no string form, so formatting or concatenating it
    cannot produce a usable path. Resolve it for the Router's firmware instead.

    Attributes:
        snake_case (str): Path for newer snake_case firmware.
        camel_case (str): Path for older camelCase firmware.
    """

    snake_case: str
    camel_case: str

    def with_suffix(self, suffix: str) -> _StyledEndpoint:
        """Return a new value with a suffix appended verbatim to both spellings.

        Args:
            suffix (str): Text appended unchanged, with no encoding or separator.

        Returns:
            _StyledEndpoint: The suffixed value.
        """
        return _StyledEndpoint(self.snake_case + suffix, self.camel_case + suffix)


_EndpointPath = str | _StyledEndpoint
"""A plain endpoint path, or a value that resolves to one per Endpoint Style."""
