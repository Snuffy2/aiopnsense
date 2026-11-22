"""Enforce private declaration names in private package modules."""

import ast
from collections.abc import Iterator
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "aiopnsense"
EXCEPTIONS = {("_typing.py", "AiopnsenseClientProtocol")}


def _declaration_names(statement: ast.stmt) -> Iterator[str]:
    """Yield names introduced by a top-level declaration or assignment.

    Args:
        statement (ast.stmt): A statement directly in the module body.

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
        for statement in module.body:
            for name in _declaration_names(statement):
                if not name.startswith("_") and (relative_path, name) not in EXCEPTIONS:
                    violations.append(f"aiopnsense/{relative_path}:{statement.lineno}: {name}")

    assert not violations, (
        "Private module declarations must start with an underscore:\n" + "\n".join(violations)
    )
