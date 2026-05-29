"""Sphinx configuration for the aiopnsense documentation site."""

from __future__ import annotations

from datetime import datetime
import inspect
from pathlib import Path
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sphinx.application import Sphinx  # type: ignore[import-not-found]

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent / "_ext"))

project: str = "aiopnsense"
author: str = "Snuffy2"
copyright: str = f"{datetime.now():%Y}, {author}"

extensions: list[str] = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.doctest",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "myst_parser",
    "opnsense_client_api",
]

templates_path: list[str] = ["_templates"]
exclude_patterns: list[str] = ["_build", "_generated"]

autosummary_generate = True
autodoc_typehints = "description"
autodoc_member_order = "bysource"
autoclass_content = "both"
napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_use_param = True
napoleon_use_rtype = True

html_theme: str = "sphinx_rtd_theme"
html_title: str = "aiopnsense documentation"

html_theme_options = {
    "collapse_navigation": True,  # Items collapse when not in use (default)
    "navigation_depth": 2,  # Max depth of the TOC tree
    "version_selector": True,  # Display a version selector below the title.
}

intersphinx_mapping: dict[str, tuple[str, None]] = {
    "python": ("https://docs.python.org/3", None),
}


def append_pep702_deprecation(
    app: Sphinx,
    what: str,
    name: str,
    obj: object,
    options: object,
    lines: list[str],
) -> None:
    """Append PEP 702 deprecation metadata to autodoc docstrings.

    Args:
        app: The Sphinx application emitting the event.
        what: The type of object being documented.
        name: The fully qualified object name.
        obj: The object being documented.
        options: The autodoc options for the object.
        lines: The docstring lines Sphinx will render.
    """
    del app, what, name, options

    deprecated_obj = obj.fget if isinstance(obj, property) else obj
    if not (
        deprecated_obj is not None
        and (inspect.isroutine(deprecated_obj) or inspect.isclass(deprecated_obj))
    ):
        return

    if message := getattr(deprecated_obj, "__deprecated__", None):
        # PEP 702 does not include a "deprecated since" version. If we add our
        # own version metadata later, this can become a Sphinx versioned
        # deprecation directive.
        lines[:0] = ["", ".. admonition:: Deprecated", "", f"   {message}", ""]


def setup(app: Sphinx) -> dict[str, bool]:
    """Register Sphinx event hooks.

    Args:
        app: The Sphinx application to configure.

    Returns:
        Sphinx extension metadata.
    """
    app.connect("autodoc-process-docstring", append_pep702_deprecation)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
