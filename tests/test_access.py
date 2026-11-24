"""Tests for collection results that report their access level."""

from copy import deepcopy
import pickle

import pytest

from aiopnsense import Access, AccessDict, AccessList, result_access


@pytest.mark.parametrize(
    ("result", "plain"),
    [
        (AccessDict({"name": "server"}, access=Access.STATUS), {"name": "server"}),
        (AccessList(["server"], access=Access.STATUS), ["server"]),
    ],
)
def test_access_result_preserves_collection_behavior(
    result: AccessDict[str, str] | AccessList[str], plain: dict[str, str] | list[str]
) -> None:
    """Keep access metadata with the result through deep copying and pickling.

    Args:
        result (AccessDict[str, str] | AccessList[str]): Result with access metadata.
        plain (dict[str, str] | list[str]): Equivalent ordinary collection.
    """
    assert isinstance(result, type(plain))
    assert result == plain
    assert result_access(deepcopy(result)) is Access.STATUS
    assert result_access(pickle.loads(pickle.dumps(result))) is Access.STATUS
    assert result_access(plain) is None
    assert result_access(None) is None
