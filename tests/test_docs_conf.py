"""Tests for the Sphinx documentation configuration."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import runpy
from typing import Any
from warnings import deprecated

import pytest

CONF_PATH = Path(__file__).resolve().parents[1] / "docs" / "source" / "conf.py"
EXISTING_DOCS = ["Existing docs."]
DEPRECATED_PREFIX = ["", ".. admonition:: Deprecated", ""]


def _append_deprecation(obj: object) -> list[str]:
    """Run the PEP 702 autodoc hook for an object.

    Args:
        obj: The object to pass to the Sphinx autodoc hook.

    Returns:
        The docstring lines mutated by the hook.
    """
    namespace: dict[str, Any] = runpy.run_path(str(CONF_PATH))
    lines = EXISTING_DOCS.copy()
    namespace["append_pep702_deprecation"](None, "object", "example", obj, None, lines)
    return lines


def _deprecated_property() -> object:
    """Return a deprecated property with a multiline message."""

    class Example:
        """Example class with a deprecated property."""

        @property
        @deprecated("Use new_value instead.\nIt supports the replacement workflow.")
        def old_value(self) -> str:
            """Return the old value.

            Returns:
                The old value.
            """
            return "old"

    return Example.old_value  # type: ignore[deprecated]


def _deprecated_function() -> object:
    """Return a deprecated function with a single-line message."""

    @deprecated("Use new_function instead.")
    def old_function() -> None:
        """Old function."""

    return old_function  # type: ignore[deprecated]


def _deprecated_class() -> object:
    """Return a deprecated class."""

    @deprecated("Use NewExample instead.")
    class OldExample:
        """Old example class."""

    return OldExample  # type: ignore[deprecated]


def _non_string_deprecated_class() -> object:
    """Return a class with malformed deprecation metadata."""

    class BrokenDeprecation:
        """Class with a malformed deprecation marker."""

        __deprecated__ = 1

    return BrokenDeprecation


def _plain_object() -> object:
    """Return an object without deprecation metadata."""
    return object()


def _empty_property() -> object:
    """Return a property without an accessor."""
    return property()


@pytest.mark.parametrize(
    ("obj_factory", "expected_lines", "expected_warning"),
    [
        pytest.param(
            _deprecated_property,
            [
                *DEPRECATED_PREFIX,
                "   Use new_value instead.",
                "   It supports the replacement workflow.",
                "",
                *EXISTING_DOCS,
            ],
            None,
            id="deprecated-property",
        ),
        pytest.param(
            _deprecated_function,
            [*DEPRECATED_PREFIX, "   Use new_function instead.", "", *EXISTING_DOCS],
            None,
            id="deprecated-function",
        ),
        pytest.param(
            _deprecated_class,
            [*DEPRECATED_PREFIX, "   Use NewExample instead.", "", *EXISTING_DOCS],
            None,
            id="deprecated-class",
        ),
        pytest.param(
            _non_string_deprecated_class,
            EXISTING_DOCS,
            "Ignoring non-string PEP 702 deprecation message",
            id="non-string-message",
        ),
        pytest.param(_plain_object, EXISTING_DOCS, None, id="no-deprecation"),
        pytest.param(_empty_property, EXISTING_DOCS, None, id="property-without-fget"),
    ],
)
def test_append_pep702_deprecation(
    obj_factory: Callable[[], object],
    expected_lines: list[str],
    expected_warning: str | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify PEP 702 metadata handling for supported autodoc objects."""
    assert _append_deprecation(obj_factory()) == expected_lines
    if expected_warning is not None:
        assert expected_warning in caplog.text
