"""Enforce the rule that tests write endpoint paths as string literals.

A test that reads the production path constant moves with the code when the path changes,
so the fake Router follows the code and no test fails. Each test spells the path out.
"""

import ast
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _parse(directory: str) -> dict[Path, ast.Module]:
    """Parse every Python module in a repository directory.

    Args:
        directory (str): Directory name below the repository root.

    Returns:
        dict[Path, ast.Module]: Parsed modules by path.
    """
    return {
        path: ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted((ROOT / directory).rglob("*.py"))
    }


def _constant_names(modules: Iterable[ast.Module]) -> set[str]:
    """Return the names modules assign an endpoint path to.

    A path constant is a name containing ``ENDPOINT``, a string starting with ``/api/``, or
    anything assigned from a ``_StyledEndpoint(...)`` call, whatever its name.

    Args:
        modules (Iterable[ast.Module]): Parsed modules to scan.

    Returns:
        set[str]: Constant names that a test must not read.
    """
    names: set[str] = set()
    for module in modules:
        for node in ast.walk(module):
            match node:
                case (
                    ast.Assign(targets=targets, value=value)
                    | ast.AnnAssign(target=targets, value=value)
                ):
                    is_path = (
                        isinstance(value, ast.Constant) and str(value.value).startswith("/api/")
                    ) or (
                        isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Name)
                        and value.func.id == "_StyledEndpoint"
                    )
                    for target in targets if isinstance(targets, list) else [targets]:
                        if isinstance(target, ast.Name) and (is_path or "ENDPOINT" in target.id):
                            names.add(target.id)
    return names


def _path_constants() -> set[str]:
    """Return the names the package assigns an endpoint or ``/api/`` path to.

    Returns:
        set[str]: Production constant names that a test must not read.
    """
    return _constant_names(_parse("aiopnsense").values())


def test_guard_treats_any_styled_endpoint_assignment_as_a_path_constant() -> None:
    """Verify a styled constant is caught even when its name omits ``ENDPOINT``."""
    module = ast.parse(
        "RELOAD_PREFIX = _StyledEndpoint(snake_case='/a', camel_case='/b')\nPLAIN = 'x'\n"
    )

    assert _constant_names([module]) == {"RELOAD_PREFIX"}


def test_tests_do_not_read_production_endpoint_constants() -> None:
    """Fail when a test module imports or attribute-reads a production path constant."""
    names = _path_constants()
    assert "FIRMWARE_RUNNING_ENDPOINT" in names
    assert "SYSTEM_INFORMATION_ENDPOINT" in names  # a styled constant
    violations: list[str] = []
    for path, module in _parse("tests").items():
        for node in ast.walk(module):
            match node:
                case ast.alias(name=name) | ast.Attribute(attr=name) if name in names:
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: {name}")

    assert not violations, "Write endpoint paths as string literals:\n" + "\n".join(violations)
