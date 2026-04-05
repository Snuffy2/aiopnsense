"""Sphinx configuration for the aiopnsense documentation site."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

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
    "display_version": True,  # If True, the version number is shown at the top of the sidebar.
    "version_selector": True,  # Display a version selector below the title.
}

intersphinx_mapping: dict[str, tuple[str, None]] = {
    "python": ("https://docs.python.org/3", None),
}
