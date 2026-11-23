"""Keep tests from adding private client stubs.

The guard recognizes the suite's `client`, `*_client` and `client_*` names
and simple names bound directly to an OPNsenseClient constructor. It checks
attribute writes and positional setattr, monkeypatch.setattr and patch.object
calls. Patch names can be literals, ternaries of literals, or names with a
single plain assignment to either form in the file. The scan does not resolve
aliases, scopes, indirect receivers, computed names or dotted patch strings.

To refresh the baseline after removing stubs, run from the repository root::

    python tests/test_private_client_stub_guard.py > tests/private_client_stubs.txt

Review the diff for decreases before committing it.
"""

from __future__ import annotations

import ast
from collections import Counter, defaultdict
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
BASELINE = TESTS / "private_client_stubs.txt"

EXEMPT_FILES = {
    "test_client_base.py",  # Exercises internal client lifecycle and transport state.
    "test_client_queue.py",  # Exercises the request queue and its worker directly.
}

# The shared fixture owns the endpoint-outcome seam used by the domain tests.
EXEMPT_HELPERS = {"conftest.py": {"stub_endpoint_outcome"}}


def _patch_names(expression: ast.expr) -> set[str]:
    """Resolve the literal leaves of a patch name.

    Args:
        expression (ast.expr): Expression passed as the attribute name.

    Returns:
        set[str]: Possible literal names.
    """
    match expression:
        case ast.Constant(value=str() as name):
            return {name}
        case ast.IfExp(body=body, orelse=orelse):
            return _patch_names(body) | _patch_names(orelse)
        case _:
            return set()


def _stub_counts(source: str, filename: str) -> Counter[str]:
    """Count private client stubs in one file.

    Args:
        source (str): Python source to scan.
        filename (str): Path relative to the tests directory.

    Returns:
        Counter[str]: Count by private attribute.
    """
    counts: Counter[str] = Counter()
    if filename in EXEMPT_FILES:
        return counts

    module = ast.parse(source, filename=filename)
    skipped = {
        descendant
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name in EXEMPT_HELPERS.get(filename, set())
        for descendant in ast.walk(node)
    }
    nodes = [node for node in ast.walk(module) if node not in skipped]
    bindings: dict[str, list[ast.expr]] = defaultdict(list)
    for node in nodes:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id].append(node.value)

    constructed = {
        name
        for name, values in bindings.items()
        if any(
            isinstance(value, ast.Call)
            and ast.unparse(value.func).split(".")[-1] == "OPNsenseClient"
            for value in values
        )
    }
    for node in nodes:
        match node:
            case ast.Attribute(value=receiver, attr=attr, ctx=ast.Store()):
                names = {attr}
            case ast.Call(func=callee, args=[receiver, attr_expr, *_]) if ast.unparse(callee) in {
                "setattr",
                "monkeypatch.setattr",
                "patch.object",
                "mock.patch.object",
            }:
                names = _patch_names(attr_expr)
                if isinstance(attr_expr, ast.Name) and len(bindings[attr_expr.id]) == 1:
                    names = _patch_names(bindings[attr_expr.id][0])
            case _:
                continue

        if isinstance(receiver, ast.Name) and (
            receiver.id == "client"
            or receiver.id.startswith("client_")
            or receiver.id.endswith("_client")
            or receiver.id in constructed
        ):
            counts.update(name for name in names if name.startswith("_"))
    return counts


def _current_counts() -> dict[tuple[str, str], int]:
    """Scan Python files under the tests directory.

    Returns:
        dict[tuple[str, str], int]: Counts indexed by relative file and attribute name.
    """
    return {
        (filename, name): count
        for path in sorted(TESTS.rglob("*.py"))
        for filename in [path.relative_to(TESTS).as_posix()]
        for name, count in _stub_counts(path.read_text(encoding="utf-8"), filename).items()
    }


def _count_errors(
    recorded: dict[tuple[str, str], int], found: dict[tuple[str, str], int]
) -> list[str]:
    """Report new stubs and stale baseline entries.

    Args:
        recorded (dict[tuple[str, str], int]): Counts in the checked-in baseline.
        found (dict[tuple[str, str], int]): Current counts from the scan.

    Returns:
        list[str]: Actionable diagnostics for each changed count.
    """
    errors = []
    for filename, name in sorted(recorded.keys() | found.keys()):
        old = recorded.get((filename, name), 0)
        new = found.get((filename, name), 0)
        location = f"tests/{filename} {name}"
        if new > old:
            errors.append(
                f"{location}: found {new}, recorded {old}. "
                "Stub at the HTTP session or use the shared scripted_router fixture."
            )
        elif new < old:
            action = f"lower the count to {new}" if new else "remove the entry"
            errors.append(
                f"{location}: found {new}, recorded {old}. Please {action} in {BASELINE.name}."
            )
    return errors


def test_private_client_stub_counts_match_baseline() -> None:
    """Reject new private stubs and require ratcheting down after removals."""
    recorded = {}
    for line in BASELINE.read_text(encoding="utf-8").splitlines():
        filename, name, count = line.split()
        recorded[filename, name] = int(count)
    assert not (errors := _count_errors(recorded, _current_counts())), "\n".join(errors)


@pytest.mark.parametrize(
    "source",
    [
        "client._get = double",
        "router = aiopnsense.OPNsenseClient(); router._get = double",
        'monkeypatch.setattr(client, "_get", double)',
        'with patch.object(client, "_get", double): pass',
        'setattr(client, "_get", double)',
        'name = "_get" if slow else "public"; monkeypatch.setattr(client, name, double)',
    ],
)
def test_guard_catches_private_stubs(source: str) -> None:
    """Catch the supported spellings of private client stubs.

    Args:
        source (str): Sample containing a private stub.
    """
    assert _stub_counts(source, "test_domain.py") == {"_get": 1}


def test_guard_allows_shared_seam_and_unrelated_operations() -> None:
    """Allow the shared helper and ignore private reads and unrelated writes."""
    source = """
def stub_endpoint_outcome(client):
    setattr(client, "_get", double)

client.public = double
session._get = double
client._get()
"""
    assert not _stub_counts(source, "conftest.py")
    assert _stub_counts(source, "test_domain.py") == {"_get": 1}


def test_guard_demands_lower_baseline() -> None:
    """Require a lower count for a file with fewer matching stubs."""
    filename = "test_domain.py"
    source = "client._get = double"
    found = {(filename, name): count for name, count in _stub_counts(source, filename).items()}
    assert "lower the count to 1" in _count_errors({(filename, "_get"): 2}, found)[0]


if __name__ == "__main__":
    for (file, attribute), total in sorted(_current_counts().items()):
        print(file, attribute, total, sep="\t")
