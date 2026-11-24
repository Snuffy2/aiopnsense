"""Access levels reported alongside Router data."""

from collections.abc import Iterable, Mapping
from enum import StrEnum


class Access(StrEnum):
    """Access level detected from the Router's responses."""

    STATUS = "status"
    CONFIGURATION = "configuration"


class AccessDict[K, V](dict[K, V]):
    """Dictionary result that carries its reported access level.

    Attributes:
        access (Access): Detected access level.
    """

    access: Access

    def __init__(self, data: Mapping[K, V], *, access: Access) -> None:
        """Initialize a dictionary result.

        Args:
            data (Mapping[K, V]): Result entries.
            access (Access): Detected access level.
        """
        super().__init__(data)
        self.access = access


class AccessList[T](list[T]):
    """List result that carries its reported access level.

    Attributes:
        access (Access): Detected access level.
    """

    access: Access

    def __init__(self, data: Iterable[T], *, access: Access) -> None:
        """Initialize a list result.

        Args:
            data (Iterable[T]): Result items.
            access (Access): Detected access level.
        """
        super().__init__(data)
        self.access = access


def result_access(result: object) -> Access | None:
    """Read the access level attached to a result.

    Args:
        result (object): Data returned by a getter.

    Returns:
        Access | None: Reported level, or ``None`` if the result does not carry one.
            Callers can handle an unreported level as Configuration Access.
            Deep copying and pickling preserve the level. Plain copies made with
            ``dict()``, ``list()`` or JSON serialization discard it.
    """
    if isinstance(result, (AccessDict, AccessList)):
        return result.access
    return None
