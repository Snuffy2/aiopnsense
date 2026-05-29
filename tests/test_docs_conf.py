"""Tests for the Sphinx documentation configuration."""

from __future__ import annotations

from pathlib import Path
import runpy
from typing import Any
from warnings import deprecated

CONF_PATH = Path(__file__).resolve().parents[1] / "docs" / "source" / "conf.py"


def _append_deprecation(obj: object) -> list[str]:
    """Run the PEP 702 autodoc hook for an object.

    Args:
        obj: The object to pass to the Sphinx autodoc hook.

    Returns:
        The docstring lines mutated by the hook.
    """
    namespace: dict[str, Any] = runpy.run_path(str(CONF_PATH))
    lines = ["Existing docs."]
    namespace["append_pep702_deprecation"](None, "object", "example", obj, None, lines)
    return lines


def test_append_pep702_deprecation_handles_deprecated_property() -> None:
    """Verify deprecated properties render deprecation admonitions."""

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

    assert _append_deprecation(Example.old_value) == [  # type: ignore[deprecated]
        "",
        ".. admonition:: Deprecated",
        "",
        "   Use new_value instead.",
        "   It supports the replacement workflow.",
        "",
        "Existing docs.",
    ]
