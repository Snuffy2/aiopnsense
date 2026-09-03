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


def _load_conf_namespace() -> dict[str, Any]:
    """Execute the Sphinx config and return its namespace.

    Returns:
        dict[str, Any]: Namespace produced by executing ``docs/source/conf.py``.
    """
    return runpy.run_path(str(CONF_PATH))


def _append_deprecation(obj: object) -> list[str]:
    """Run the PEP 702 autodoc hook for an object.

    Args:
        obj (object): Object whose PEP 702 metadata is passed to the autodoc hook.

    Returns:
        list[str]: The docstring lines mutated by the hook.
    """
    namespace = _load_conf_namespace()
    lines = EXISTING_DOCS.copy()
    namespace["append_pep702_deprecation"](None, "object", "example", obj, None, lines)
    return lines


def test_sphinx_argparse_cli_extension_is_enabled() -> None:
    """Read the Docs config enables generated argparse CLI documentation."""
    namespace = _load_conf_namespace()

    assert "sphinx_argparse_cli" in namespace["extensions"]


def test_conf_adds_scripts_directory_to_sys_path() -> None:
    """Sphinx config exposes repo scripts as importable modules for CLI docs."""
    namespace = _load_conf_namespace()

    assert str(namespace["ROOT"] / "scripts") in namespace["sys"].path


def _deprecated_property() -> object:
    """Return a deprecated property with a multiline message.

    Returns:
        object: Deprecated property used to verify multiline warning rendering.
    """

    class Example:
        """Example class with a deprecated property."""

        @property
        @deprecated("Use new_value instead.\nIt supports the replacement workflow.")
        def old_value(self) -> str:
            """Return the old value.

            Returns:
                str: The old value.
            """
            return "old"

    return Example.old_value  # type: ignore[deprecated]


def _deprecated_function() -> object:
    """Return a deprecated function with a single-line message.

    Returns:
        object: Deprecated function used to verify single-line warning rendering.
    """

    @deprecated("Use new_function instead.")
    def old_function() -> None:
        """Old function."""

    return old_function  # type: ignore[deprecated]


def _deprecated_class() -> object:
    """Return a deprecated class.

    Returns:
        object: Deprecated class used to verify class warning rendering.
    """

    @deprecated("Use NewExample instead.")
    class OldExample:
        """Old example class."""

    return OldExample  # type: ignore[deprecated]


def _non_string_deprecated_class() -> object:
    """Return a class with malformed deprecation metadata.

    Returns:
        object: Class carrying an invalid non-string deprecation marker.
    """

    class BrokenDeprecation:
        """Class with a malformed deprecation marker."""

        __deprecated__ = 1

    return BrokenDeprecation


def _plain_object() -> object:
    """Return an object without deprecation metadata.

    Returns:
        object: Plain object without PEP 702 deprecation metadata.
    """
    return object()


def _empty_property() -> object:
    """Return a property without an accessor.

    Returns:
        object: Property lacking an accessor and deprecation metadata.
    """
    return property()


@pytest.mark.parametrize(
    ("obj_factory", "expected_lines"),
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
            id="deprecated-property",
        ),
        pytest.param(
            _deprecated_function,
            [*DEPRECATED_PREFIX, "   Use new_function instead.", "", *EXISTING_DOCS],
            id="deprecated-function",
        ),
        pytest.param(
            _deprecated_class,
            [*DEPRECATED_PREFIX, "   Use NewExample instead.", "", *EXISTING_DOCS],
            id="deprecated-class",
        ),
        pytest.param(
            _non_string_deprecated_class,
            EXISTING_DOCS,
            id="non-string-message",
        ),
        pytest.param(_plain_object, EXISTING_DOCS, id="no-deprecation"),
        pytest.param(_empty_property, EXISTING_DOCS, id="property-without-fget"),
    ],
)
def test_append_pep702_deprecation(
    obj_factory: Callable[[], object],
    expected_lines: list[str],
) -> None:
    """Verify PEP 702 metadata handling for supported autodoc objects.

    Args:
        obj_factory (Callable[[], object]): Builds the object supplied to the autodoc hook.
        expected_lines (list[str]): Expected docstring lines after hook processing.
    """
    assert _append_deprecation(obj_factory()) == expected_lines
