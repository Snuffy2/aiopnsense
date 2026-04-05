"""Custom Sphinx directive for aiopnsense client API method pages."""

from __future__ import annotations

from importlib import import_module
import inspect
from typing import Any

from docutils import nodes  # type: ignore[import-untyped]
from docutils.parsers.rst import Directive, directives  # type: ignore[import-untyped]
from docutils.statemachine import ViewList  # type: ignore[import-untyped]
from sphinx.application import Sphinx  # type: ignore[import-not-found]


def _import_object(dotted_path: str) -> Any:
    """Import and return an object from a dotted module path.

    Args:
        dotted_path (str): Fully qualified path to the object.

    Returns:
        Any: Imported Python object.

    Raises:
        ImportError: Raised when the module path is invalid.
        AttributeError: Raised when the target attribute does not exist.
    """
    module_name, _, attr_name = dotted_path.rpartition(".")
    if not module_name or not attr_name:
        msg = f"Expected a fully qualified object path, got: {dotted_path!r}"
        raise ImportError(msg)
    module = import_module(module_name)
    return getattr(module, attr_name)


def _iter_public_method_names(mixin_class: type[Any]) -> list[str]:
    """Return public method names defined directly on a mixin class.

    Args:
        mixin_class (type[Any]): Mixin class to inspect.

    Returns:
        list[str]: Ordered list of public method names defined on the mixin.
    """
    public_methods: list[str] = []
    for name, value in mixin_class.__dict__.items():
        if name.startswith("_"):
            continue
        if isinstance(value, (staticmethod, classmethod)):
            value = value.__func__
        if inspect.isfunction(value):
            public_methods.append(name)
    return public_methods


class OPNsenseClientAPIDirective(Directive):
    """Render public mixin methods as ``OPNsenseClient`` API entries."""

    has_content = False
    required_arguments = 1
    optional_arguments = 0
    final_argument_whitespace = False
    option_spec = {
        "client": directives.unchanged_required,
    }

    def run(self) -> list[nodes.Node]:
        """Generate ``automethod`` entries for one mixin.

        Returns:
            list[nodes.Node]: Parsed docutils nodes for the generated method docs.
        """
        mixin_path = self.arguments[0]
        client_path = self.options.get("client", "aiopnsense.OPNsenseClient")

        try:
            mixin_class = _import_object(mixin_path)
        except (AttributeError, ImportError) as err:
            raise self.error(str(err)) from err
        if not isinstance(mixin_class, type):
            msg = (
                f"opnsense-client-api target {mixin_path!r} resolved to "
                f"{mixin_class!r}, which is not a class"
            )
            raise self.error(msg)

        method_names = _iter_public_method_names(mixin_class)
        if not method_names:
            msg = (
                f"opnsense-client-api target {mixin_path!r} resolved to class "
                f"{mixin_class.__qualname__!r}, but it defines no public methods"
            )
            raise self.error(msg)

        generated_lines = ViewList()
        for method_name in method_names:
            generated_lines.append(
                f".. automethod:: {client_path}.{method_name}",
                source=mixin_path,
            )
            generated_lines.append("", source=mixin_path)

        container = nodes.container()
        container.document = self.state.document
        self.state.nested_parse(generated_lines, self.content_offset, container)
        return container.children


def setup(app: Sphinx) -> dict[str, bool]:
    """Register the aiopnsense client API directive with Sphinx.

    Args:
        app (Sphinx): Active Sphinx application.

    Returns:
        dict[str, bool]: Extension metadata for Sphinx.
    """
    app.add_directive("opnsense-client-api", OPNsenseClientAPIDirective)
    return {
        "parallel_read_safe": True,
        "parallel_write_safe": True,
    }
