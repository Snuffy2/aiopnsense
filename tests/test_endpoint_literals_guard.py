"""Enforce the rule that tests write endpoint paths as string literals.

A test that reads the production path constant moves with the code when the path changes,
so the fake Router follows the code and no test fails. Each test spells the path out.
"""

import ast
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


def _path_constants() -> set[str]:
    """Return the names the package assigns an endpoint or ``/api/`` path to.

    Returns:
        set[str]: Production constant names that a test must not read.
    """
    names: set[str] = set()
    for module in _parse("aiopnsense").values():
        for node in ast.walk(module):
            match node:
                case (
                    ast.Assign(targets=targets, value=value)
                    | ast.AnnAssign(target=targets, value=value)
                ):
                    is_path = isinstance(value, ast.Constant) and str(value.value).startswith(
                        "/api/"
                    )
                    for target in targets if isinstance(targets, list) else [targets]:
                        if isinstance(target, ast.Name) and (is_path or "ENDPOINT" in target.id):
                            names.add(target.id)
    return names


def test_tests_do_not_read_production_endpoint_constants() -> None:
    """Fail when a test module imports or attribute-reads a production path constant."""
    names = _path_constants()
    assert "FIRMWARE_RUNNING_ENDPOINT" in names
    violations = [
        f"{path.relative_to(ROOT)}:{node.lineno}: {name}"
        for path, module in _parse("tests").items()
        for node in ast.walk(module)
        for name in (
            node.name if isinstance(node, ast.alias) else None,
            node.attr if isinstance(node, ast.Attribute) else None,
        )
        if name in names
    ]

    assert not violations, "Write endpoint paths as string literals:\n" + "\n".join(violations)
