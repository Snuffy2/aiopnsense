"""Tests for the private ``_StyledEndpoint`` value."""

import dataclasses

import pytest

from aiopnsense._styled_endpoint import _StyledEndpoint


def test_with_suffix_appends_verbatim_to_both_spellings() -> None:
    """Verify a suffix is appended unchanged to both spellings."""
    endpoint = _StyledEndpoint(snake_case="/api/a/snake_x/", camel_case="/api/a/camelX/")

    suffixed = endpoint.with_suffix("my name/ä?q=1")

    assert suffixed == _StyledEndpoint(
        snake_case="/api/a/snake_x/my name/ä?q=1", camel_case="/api/a/camelX/my name/ä?q=1"
    )
    assert endpoint.snake_case == "/api/a/snake_x/"


def test_value_is_frozen_and_slotted() -> None:
    """Verify the value cannot be mutated and carries no instance dict."""
    endpoint = _StyledEndpoint(snake_case="/s", camel_case="/c")

    with pytest.raises(dataclasses.FrozenInstanceError):
        endpoint.snake_case = "/other"  # type: ignore[misc]
    assert not hasattr(endpoint, "__dict__")


def test_value_has_no_usable_string_form() -> None:
    """Verify formatting or concatenating the value never yields a path."""
    endpoint = _StyledEndpoint(snake_case="/api/snake", camel_case="/api/camel")

    assert f"{endpoint}" not in {"/api/snake", "/api/camel"}
    assert str(endpoint) not in {"/api/snake", "/api/camel"}
    with pytest.raises(TypeError):
        _ = "/prefix" + endpoint  # type: ignore[operator]
    with pytest.raises(TypeError):
        _ = endpoint + "/suffix"  # type: ignore[operator]
