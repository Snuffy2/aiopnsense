"""Enforce private declaration names in private package modules."""

import ast
from collections.abc import Iterator
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "aiopnsense"
EXCEPTIONS = {("_typing.py", "AiopnsenseClientProtocol")}


def _declaration_names(statement: ast.stmt) -> Iterator[str]:
    """Yield names introduced by a module-scope declaration or assignment.

    Args:
        statement (ast.stmt): A statement at module scope, including control flow.

    Yields:
        str: A name declared or assigned by the statement.
    """
    match statement:
        case ast.ClassDef(name=name) | ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name):
            yield name
        case ast.TypeAlias(name=ast.Name(id=name)):
            yield name
        case ast.Assign(targets=targets):
            for target in targets:
                yield from _assignment_names(target)
        case ast.AnnAssign(target=target):
            yield from _assignment_names(target)


def _module_scope_statements(node: ast.AST) -> Iterator[ast.stmt]:
    """Yield statements that bind names in the module scope.

    Args:
        node (ast.AST): A node in the parsed module.

    Yields:
        ast.stmt: A module-scope statement, excluding nested class and function bodies.
    """
    if isinstance(node, ast.stmt):
        yield node
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            return

    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.stmt, ast.ExceptHandler, ast.match_case)):
            yield from _module_scope_statements(child)


def _assignment_names(target: ast.expr) -> Iterator[str]:
    """Yield names bound by an assignment target, including unpacking.

    Args:
        target (ast.expr): Target of the assignment.

    Yields:
        str: A name directly bound by the target.
    """
    match target:
        case ast.Name(id=name):
            yield name
        case ast.Tuple(elts=elements) | ast.List(elts=elements):
            for element in elements:
                yield from _assignment_names(element)
        case ast.Starred(value=value):
            yield from _assignment_names(value)


def test_private_module_declarations_start_with_underscore() -> None:
    """Require private names for declarations defined in private modules."""
    violations = []
    for path in sorted(PACKAGE.rglob("_*.py")):
        if path.name == "__init__.py":
            continue
        relative_path = path.relative_to(PACKAGE).as_posix()
        module = ast.parse(path.read_bytes(), filename=relative_path)
        violations.extend(_module_violations(module, relative_path))

    assert not violations, (
        "Private module declarations must start with an underscore:\n" + "\n".join(violations)
    )


def test_private_module_guard_checks_control_flow_declarations() -> None:
    """Find module-scope declarations in control flow while skipping nested scopes."""
    module = ast.parse(
        """
if True:
    PublicConstant = 1
    def PublicFunction():
        LocalName = 2
    class PublicClass:
        pass
    type PublicAlias = int
    class _PrivateClass:
        PublicClassAttribute = 3
        def public_method(self):
            LocalName = 4
try:
    pass
except Exception:
    async def PublicAsyncFunction():
        LocalName = 5
"""
    )

    violations = _module_violations(module, "_probe.py")

    assert violations == [
        "aiopnsense/_probe.py:3: PublicConstant",
        "aiopnsense/_probe.py:4: PublicFunction",
        "aiopnsense/_probe.py:6: PublicClass",
        "aiopnsense/_probe.py:8: PublicAlias",
        "aiopnsense/_probe.py:16: PublicAsyncFunction",
    ]


def _module_violations(module: ast.Module, relative_path: str) -> list[str]:
    """Return public-style module-scope declarations found by the guard.

    Args:
        module (ast.Module): Parsed Python source.
        relative_path (str): Module path relative to the package.

    Returns:
        list[str]: Formatted source locations and declaration names.
    """
    return [
        f"aiopnsense/{relative_path}:{statement.lineno}: {name}"
        for statement in _module_scope_statements(module)
        for name in _declaration_names(statement)
        if not name.startswith("_") and (relative_path, name) not in EXCEPTIONS
    ]
