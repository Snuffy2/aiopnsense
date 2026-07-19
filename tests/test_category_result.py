"""Tests for authoritative optional-category result contracts."""

from dataclasses import FrozenInstanceError

import pytest

from aiopnsense import CategoryResult, CategoryState


def test_category_result_is_exported_immutable_and_generic() -> None:
    """The public result envelope is frozen, slotted, and carries typed state."""
    state: CategoryState = "available"
    result = CategoryResult([1], state, True)

    assert result.data == [1]
    assert result.state == "available"
    assert result.authoritative is True
    assert not hasattr(result, "__dict__")
    with pytest.raises(FrozenInstanceError):
        result.state = "missing"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        (["available", "missing"], ("available", True)),
        (["missing", "missing"], ("missing", True)),
        (["available", "pending"], ("pending", False)),
        (["available", "transient"], ("transient", False)),
        (["available", "malformed"], ("malformed", True)),
    ],
)
def test_dhcp_source_authority_ignores_only_confirmed_inapplicable_sources(
    states: list[CategoryState], expected: tuple[CategoryState, bool]
) -> None:
    """Confirmed missing providers are inapplicable; uncertain providers are not."""
    from aiopnsense.dhcp import DHCPMixin

    assert DHCPMixin._aggregate_dhcp_source_states(states) == expected
