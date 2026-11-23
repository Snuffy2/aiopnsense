"""Enforce the firmware endpoint rule from ADR 0002 on production code.

The guard is a static AST check that reads each module on its own. It resolves string
constants, f-strings, concatenation and imports that rename a guarded constant, then
reports each expression that builds a firmware path the rule does not allow. Status reads
are allowed only through the GET helpers and the ``_endpoint_availability.get`` lookup,
which sends no request. The check path is allowed only in the one dispatch in
``FirmwareMixin._request_firmware_check``, and the action prefix and the ``update`` and
``upgrade`` paths only in ``FirmwareMixin.install_firmware`` and
``FirmwareMixin.upgrade_firmware``. Each of those two methods must compare ``type`` only
with exactly ``("update", "upgrade")``, so the action prefix cannot send another action.

Known limits: it does not follow values held in local variables, class attributes, ``+=``
or values imported from another module, other than the guarded constants. It does not
cover other literal firmware actions, and it does not check that the ``type`` comparison
actually controls the dispatch. Outside the two install methods, the action prefix
fails when it is bare, followed by a runtime value or another suffix, or copied into a
local. A prefix-built path that resolves to ``status`` or ``check`` is judged as that
literal request: ``f"{FIRMWARE_ACTION_ENDPOINT_PREFIX}status"`` is accepted in a GET helper,
and ``f"{FIRMWARE_ACTION_ENDPOINT_PREFIX}check"`` in the check dispatch. It cannot see
paths built with ``str.format``, ``%`` or ``join``.
"""

import ast
from collections.abc import Iterator
from pathlib import Path
import re

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "aiopnsense"
FIRMWARE_MODULE = "firmware.py"
CHECK_DISPATCH_SCOPE = ("FirmwareMixin", "_request_firmware_check")
ACTION_SCOPES = frozenset(
    {("FirmwareMixin", "install_firmware"), ("FirmwareMixin", "upgrade_firmware")}
)
ACTION_TYPES = frozenset({"update", "upgrade"})
ENDPOINT_DEFINITIONS = {
    "FIRMWARE_CHECK_ENDPOINT": "check",
    "FIRMWARE_STATUS_ENDPOINT": "status",
    "FIRMWARE_ACTION_ENDPOINT_PREFIX": "action",
}
GUARDED_CONSTANTS: dict[str, str] = {
    "FIRMWARE_ACTION_ENDPOINT_PREFIX": "/api/core/firmware/",
    "FIRMWARE_CHECK_ENDPOINT": "/api/core/firmware/check",
    "FIRMWARE_STATUS_ENDPOINT": "/api/core/firmware/status",
}
GET_HELPERS = frozenset(
    {
        "_check_endpoint_status",
        "_do_get",
        "_fetch_endpoint_outcome",
        "_firmware_request",
        "_get",
        "_get_text",
        "_is_get_endpoint_available",
        "_safe_dict_get",
        "_safe_dict_get_with_timeout",
        "_safe_list_get",
    }
)
# Matches a named endpoint, the update or upgrade action (no endpoint group), or the bare
# prefix and the prefix before an unresolved part.
GUARDED_PATH = re.compile(
    r"/api/core/firmware/"
    r"(?:(?P<endpoint>info|status|check)(?![\w-])|(?:update|upgrade)(?![\w-])|(?=\0|\Z))",
    re.IGNORECASE,
)
UNKNOWN = "\0"
ACTION_LABEL = "firmware action prefix outside install_firmware or upgrade_firmware"
CHECK_DISPATCH = "await self._do_post(FIRMWARE_CHECK_ENDPOINT)"
ACTION_STATEMENT = 'await self._do_post(f"{FIRMWARE_ACTION_ENDPOINT_PREFIX}{type}")'


def _violation_label(endpoint: str) -> str:
    """Return the text that describes a reported endpoint in a violation.

    Args:
        endpoint (str): Endpoint name the guard reports.

    Returns:
        str: ``ACTION_LABEL`` for ``action``, otherwise the firmware path.
    """
    return ACTION_LABEL if endpoint == "action" else f"/api/core/firmware/{endpoint}"


def _resolve(node: ast.AST, constants: dict[str, str]) -> str:
    """Return the string an expression builds, marking unknown parts.

    Args:
        node (ast.AST): Expression to resolve.
        constants (dict[str, str]): String constants visible in the module.

    Returns:
        str: Resolved text, with ``UNKNOWN`` in place of runtime values.
    """
    match node:
        case ast.Constant(value=str() as value):
            return value
        case ast.Name(id=name) | ast.Attribute(attr=name):
            return constants.get(name, GUARDED_CONSTANTS.get(name, UNKNOWN))
        case ast.JoinedStr(values=values):
            return "".join(_resolve(value, constants) for value in values)
        case ast.FormattedValue(value=value):
            return _resolve(value, constants)
        case ast.BinOp(left=left, op=ast.Add(), right=right):
            return _resolve(left, constants) + _resolve(right, constants)
    return UNKNOWN


def _module_constants(module: ast.Module) -> dict[str, str]:
    """Return one module's string constants by replaying its statements in source order.

    Each top-level assignment is resolved against the names defined before it, and a name
    whose new value cannot be resolved is removed. An import that renames a guarded
    constant binds its alias to the guarded value.

    Args:
        module (ast.Module): Parsed module.

    Returns:
        dict[str, str]: Constant values by name.
    """
    scope: dict[str, str] = {}
    statements: list[ast.stmt] = [
        node for node in ast.walk(module) if isinstance(node, ast.ImportFrom)
    ]
    statements += [node for node in module.body if isinstance(node, (ast.Assign, ast.AnnAssign))]
    for statement in sorted(statements, key=lambda node: (node.lineno, node.col_offset)):
        match statement:
            case ast.ImportFrom(names=names):
                for alias in names:
                    if alias.asname and alias.name in GUARDED_CONSTANTS:
                        scope[alias.asname] = GUARDED_CONSTANTS[alias.name]
            case (
                ast.Assign(targets=[ast.Name(id=name)], value=value)
                | ast.AnnAssign(target=ast.Name(id=name), value=ast.expr() as value)
            ):
                resolved = _resolve(value, scope)
                if UNKNOWN in resolved:
                    scope.pop(name, None)
                else:
                    scope[name] = resolved
    return scope


def _child_scope(node: ast.AST, scope: tuple[str, ...]) -> tuple[str, ...]:
    """Return the enclosing-scope names that apply to the children of a node.

    Args:
        node (ast.AST): Parent node.
        scope (tuple[str, ...]): Names of the classes and functions enclosing ``node``.

    Returns:
        tuple[str, ...]: ``scope`` extended by ``node``'s name when it is a class or function.
    """
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return (*scope, node.name)
    return scope


def _scoped_nodes(
    node: ast.AST, scope: tuple[str, ...] = ()
) -> Iterator[tuple[ast.AST, tuple[str, ...]]]:
    """Yield every node below ``node`` with the names of its enclosing classes and functions.

    Args:
        node (ast.AST): Node to walk.
        scope (tuple[str, ...]): Names of the classes and functions enclosing ``node``.

    Yields:
        tuple[ast.AST, tuple[str, ...]]: Node and its enclosing scope.
    """
    yield node, scope
    for child in ast.iter_child_nodes(node):
        yield from _scoped_nodes(child, _child_scope(node, scope))


def _references(
    node: ast.AST, constants: dict[str, str], scope: tuple[str, ...] = ()
) -> Iterator[tuple[ast.expr, str, tuple[str, ...]]]:
    """Yield the outermost expressions that build a guarded firmware path.

    Args:
        node (ast.AST): Node to search.
        constants (dict[str, str]): String constants visible in the module.
        scope (tuple[str, ...]): Names of the classes and functions enclosing ``node``.

    Yields:
        tuple[ast.expr, str, tuple[str, ...]]: Expression, the endpoint it builds (``action``
            for the bare prefix or a prefix followed by a runtime value), and its scope.
    """
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
        return  # Docstrings can explain the rule without sending a request.
    match node:
        case (
            ast.Constant()
            | ast.JoinedStr()
            | ast.BinOp()
            | ast.Name(ctx=ast.Load())
            | ast.Attribute(ctx=ast.Load())
        ):
            if found := GUARDED_PATH.search(_resolve(node, constants)):
                yield node, (found["endpoint"] or "action").lower(), scope
                return
    for child in ast.iter_child_nodes(node):
        yield from _references(child, constants, _child_scope(node, scope))


def _self_method(call: ast.Call) -> str | None:
    """Return the method name of a ``self.<method>(...)`` call.

    Args:
        call (ast.Call): Call to inspect.

    Returns:
        str | None: Method name, or ``None`` for any other call target.
    """
    match call.func:
        case ast.Attribute(value=ast.Name(id="self"), attr=method):
            return method
    return None


def _call_path(call: ast.Call) -> ast.expr | None:
    """Return the path argument of a call, positional or keyword.

    Args:
        call (ast.Call): Call to inspect.

    Returns:
        ast.expr | None: The first positional argument, else the ``path=`` keyword
        value, else ``None``.
    """
    if call.args:
        return call.args[0]
    for keyword in call.keywords:
        if keyword.arg == "path":
            return keyword.value
    return None


def _is_status_read(call: ast.Call) -> bool:
    """Return whether a call only reads the path it is given.

    Args:
        call (ast.Call): Call to inspect.

    Returns:
        bool: ``True`` for a GET helper or the endpoint availability cache lookup.
    """
    match call.func:
        case ast.Attribute(
            value=ast.Attribute(value=ast.Name(id="self"), attr="_endpoint_availability"),
            attr="get",
        ):
            return True
    if _self_method(call) not in GET_HELPERS:
        return False
    return not any(
        keyword.arg in ("post", None)
        and not (isinstance(keyword.value, ast.Constant) and keyword.value.value is False)
        for keyword in call.keywords
    )


def _check_dispatch_paths(module: ast.Module, constants: dict[str, str]) -> list[ast.expr]:
    """Return the path arguments of ``_do_post`` calls in the approved check dispatch.

    Args:
        module (ast.Module): Parsed firmware module.
        constants (dict[str, str]): String constants visible in the module.

    Returns:
        list[ast.expr]: Path arguments in ``FirmwareMixin._request_firmware_check`` that
            resolve to the check endpoint.
    """
    return [
        path
        for node, scope in _scoped_nodes(module)
        if scope == CHECK_DISPATCH_SCOPE
        and isinstance(node, ast.Call)
        and _self_method(node) == "_do_post"
        and (path := _call_path(node)) is not None
        and _resolve(path, constants) == GUARDED_CONSTANTS["FIRMWARE_CHECK_ENDPOINT"]
    ]


def _type_comparison_values(node: ast.AST) -> list[frozenset[str]]:
    """Return the string values of each ``type in`` or ``type not in`` tuple below a node.

    Args:
        node (ast.AST): Node to search, including its descendants.

    Returns:
        list[frozenset[str]]: One value set per comparison of the Name ``type`` with ``in``
            or ``not in`` against a tuple of string constants.
    """
    values: list[frozenset[str]] = []
    for candidate in ast.walk(node):
        match candidate:
            case ast.Compare(
                left=ast.Name(id="type"),
                ops=[ast.In() | ast.NotIn()],  # codespell:ignore
                comparators=[ast.Tuple(elts=elts)],
            ):
                strings = {
                    elt.value
                    for elt in elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                }
                if len(strings) == len(elts):
                    values.append(frozenset(strings))
    return values


def _action_gate_violations(module: ast.Module) -> list[str]:
    """Report each present action method whose ``type`` comparison is not update and upgrade.

    A method passes only when its one comparison of ``type`` is an ``in`` or ``not in`` tuple
    of exactly ``ACTION_TYPES``. An extra comparison, such as ``or type == "check"``, fails
    it. A method missing from the module is not checked.

    Args:
        module (ast.Module): Parsed firmware module.

    Returns:
        list[str]: One violation for each action method that allows another value or lacks
            the comparison.
    """
    violations: list[str] = []
    for class_name, method in sorted(ACTION_SCOPES):
        methods = [
            node
            for node, scope in _scoped_nodes(module)
            if scope == (class_name,)
            and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == method
        ]
        if not methods:
            continue
        comparisons = [
            candidate
            for node in methods
            for candidate in ast.walk(node)
            if isinstance(candidate, ast.Compare)
            and any(
                isinstance(side, ast.Name) and side.id == "type"
                for side in (candidate.left, *candidate.comparators)
            )
        ]
        values = [value for node in methods for value in _type_comparison_values(node)]
        if len(comparisons) != 1 or values != [ACTION_TYPES]:
            violations.append(
                f"aiopnsense/{FIRMWARE_MODULE}: {class_name}.{method} "
                "must allow only update and upgrade"
            )
    return violations


def _module_violations(
    module: ast.Module, relative_path: str, constants: dict[str, str]
) -> list[str]:
    """Return references to firmware endpoints that ADR 0002 does not allow.

    For the firmware module, also report unless ``FirmwareMixin._request_firmware_check``
    contains exactly one check dispatch.

    Args:
        module (ast.Module): Parsed Python source.
        relative_path (str): Module path relative to the package.
        constants (dict[str, str]): String constants visible in the module.

    Returns:
        list[str]: Formatted source locations and the rule each one breaks.
    """
    allowed: dict[int, str] = {}
    for node in ast.walk(module):
        if (
            isinstance(node, ast.Call)
            and _is_status_read(node)
            and (path := _call_path(node)) is not None
        ):
            allowed[id(path)] = "status"
    is_firmware = relative_path == FIRMWARE_MODULE
    violations: list[str] = []
    if is_firmware:
        for statement in module.body:
            match statement:
                case (
                    ast.Assign(targets=[ast.Name(id=name)], value=value)
                    | ast.AnnAssign(target=ast.Name(id=name), value=ast.expr() as value)
                ) if name in ENDPOINT_DEFINITIONS:
                    allowed[id(value)] = ENDPOINT_DEFINITIONS[name]
        dispatches = _check_dispatch_paths(module, constants)
        for path in dispatches:
            allowed[id(path)] = "check"
        if len(dispatches) != 1:
            violations.append(
                f"aiopnsense/{relative_path}: FirmwareMixin._request_firmware_check "
                f"must contain exactly one check dispatch, found {len(dispatches)}"
            )
        violations += _action_gate_violations(module)

    violations += [
        f"aiopnsense/{relative_path}:{expression.lineno}: {_violation_label(endpoint)}"
        for expression, endpoint, scope in _references(module, constants)
        if allowed.get(id(expression)) != endpoint
        and not (is_firmware and endpoint == "action" and scope in ACTION_SCOPES)
    ]
    return violations


def _source_violations(sources: dict[str, str]) -> list[str]:
    """Parse sources as one package and return their endpoint violations.

    Args:
        sources (dict[str, str]): Python source by package-relative path.

    Returns:
        list[str]: Violations from every module.
    """
    violations: list[str] = []
    for path, source in sources.items():
        module = ast.parse(source, filename=path)
        violations += _module_violations(module, path, _module_constants(module))
    return violations


def _action_gate(method: str, values: str | None, extra: str = "") -> str:
    """Return the ``type`` comparison that an action method holds in the fixture.

    Args:
        method (str): Action method that receives the comparison.
        values (str | None): Tuple contents, such as ``'"update", "upgrade"'``, or ``None``
            for a method without the comparison.
        extra (str): Text appended after the tuple comparison, such as
            ``' or type == "check"'``. Empty by default.

    Returns:
        str: ``if type not in (...)`` for ``install_firmware`` and ``if type in (...)``
            otherwise, each with a ``pass`` body; empty when ``values`` is ``None``.
    """
    if values is None:
        return ""
    operator = "not in" if method == "install_firmware" else "in"
    return f"if type {operator} ({values}){extra}:\n            pass"


def _firmware_source(
    statement: str = "pass",
    *,
    method: str = "probe",
    dispatch: str = CHECK_DISPATCH,
    gate: str = "",
) -> str:
    """Return a minimal firmware module with a statement in one method.

    Args:
        statement (str): Body of ``method``, indented to sit inside it.
        method (str): Name of the method that receives ``statement``.
        dispatch (str): Body of ``FirmwareMixin._request_firmware_check``.
        gate (str): Optional ``type`` comparison placed before ``statement`` in ``method``.

    Returns:
        str: Python source of the fixture module.
    """
    return f"""
FIRMWARE_STATUS_ENDPOINT = "/api/core/firmware/status"
FIRMWARE_CHECK_ENDPOINT = "/api/core/firmware/check"
FIRMWARE_ACTION_ENDPOINT_PREFIX = "/api/core/firmware/"
FIRMWARE_CHANGELOG_ENDPOINT_PREFIX = "/api/core/firmware/changelog/"

class FirmwareMixin:
    async def _request_firmware_check(self):
        {dispatch}

    async def {method}(self, value):
        {gate}
        {statement}
"""


def test_production_code_follows_firmware_endpoint_rule() -> None:
    """Fail when production code can contact upstream outside the ADR 0002 path."""
    sources = {
        path.relative_to(PACKAGE).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(PACKAGE.rglob("*.py"))
    }

    violations = _source_violations(sources)

    assert not violations, (
        "Firmware endpoint use breaks docs/adr/0002-firmware-reads-never-contact-upstream.md:\n"
        + "\n".join(violations)
    )


@pytest.mark.parametrize(
    ("statement", "endpoint"),
    [
        ('await self._safe_dict_get("/api/core/firmware/info")', "info"),
        ('await self._safe_dict_post("/api/core/firmware/info")', "info"),
        ('await self._get(f"{FIRMWARE_ACTION_ENDPOINT_PREFIX}info")', "info"),
        ('await self._get(FIRMWARE_ACTION_ENDPOINT_PREFIX + "info")', "info"),
        ('await self._get(f"/api/core/firmware/info?{value}")', "info"),
        ("await self._do_post(FIRMWARE_STATUS_ENDPOINT)", "status"),
        ("await self._post(FIRMWARE_STATUS_ENDPOINT)", "status"),
        ("await self._safe_dict_post(FIRMWARE_STATUS_ENDPOINT)", "status"),
        ("await self._safe_list_post(FIRMWARE_STATUS_ENDPOINT)", "status"),
        ("await self._firmware_request(FIRMWARE_STATUS_ENDPOINT, post=True)", "status"),
        ("await self._firmware_request(FIRMWARE_STATUS_ENDPOINT, post=value)", "status"),
        ("await self._safe_dict_get(path=FIRMWARE_STATUS_ENDPOINT, post=True)", "status"),
        ("await self._safe_dict_get(FIRMWARE_STATUS_ENDPOINT, **value)", "status"),
        ("await self._session.post(FIRMWARE_STATUS_ENDPOINT)", "status"),
        ("await other._safe_dict_get(FIRMWARE_STATUS_ENDPOINT)", "status"),
        ('await self._post(f"{FIRMWARE_ACTION_ENDPOINT_PREFIX}status")', "status"),
        ("path = FIRMWARE_STATUS_ENDPOINT", "status"),
        ("await self._do_post(fw.FIRMWARE_STATUS_ENDPOINT)", "status"),
        ("await self._do_post(FIRMWARE_CHECK_ENDPOINT)", "check"),
        ("await self._post(FIRMWARE_CHECK_ENDPOINT)", "check"),
        ("await self._firmware_request(FIRMWARE_CHECK_ENDPOINT, post=True)", "check"),
        ('await self._do_post(f"{FIRMWARE_ACTION_ENDPOINT_PREFIX}check")', "check"),
        ("await self._do_post(path=FIRMWARE_CHECK_ENDPOINT)", "check"),
        ("path = FIRMWARE_CHECK_ENDPOINT", "check"),
        ("await self._do_post(fw.FIRMWARE_CHECK_ENDPOINT)", "check"),
        ("await self._safe_dict_get(FIRMWARE_CHECK_ENDPOINT)", "check"),
        ("self._endpoint_availability.get(FIRMWARE_CHECK_ENDPOINT)", "check"),
        ('await self._do_post(f"{FIRMWARE_ACTION_ENDPOINT_PREFIX}{value}")', "action"),
        (
            'action = "check"\n        await self._do_post(f"{FIRMWARE_ACTION_ENDPOINT_PREFIX}{action}")',
            "action",
        ),
        (
            'action = "info"\n        await self._get(f"{FIRMWARE_ACTION_ENDPOINT_PREFIX}{action}")',
            "action",
        ),
        ("base = FIRMWARE_ACTION_ENDPOINT_PREFIX", "action"),
        ('await self._do_post("/api/core/firmware/" + value)', "action"),
        ('await self._do_post("/api/core/firmware/update")', "action"),
        ('await self._do_post("/api/core/firmware/upgrade")', "action"),
    ],
)
def test_guard_rejects_forbidden_firmware_requests(statement: str, endpoint: str) -> None:
    """Report each forbidden way of reaching a firmware endpoint.

    Args:
        statement (str): Statement added to a firmware method.
        endpoint (str): Firmware endpoint the statement must be reported for.
    """
    violations = _source_violations({FIRMWARE_MODULE: _firmware_source(statement)})

    assert len(violations) == 1
    assert violations[0].endswith(_violation_label(endpoint))


def _dispatching_module(call: str, imports: str = "") -> str:
    """Return a second module whose ``request`` dispatches the check path.

    Args:
        call (str): Path argument passed to ``client._do_post``.
        imports (str): Import line placed above ``request``, if any.

    Returns:
        str: Python source of the second module.
    """
    return f"{imports}\n\nasync def request(client):\n    await client._do_post({call})\n"


@pytest.mark.parametrize(
    ("other", "endpoint"),
    [
        (_dispatching_module("firmware.FIRMWARE_CHECK_ENDPOINT"), "check"),
        (
            _dispatching_module(
                "FIRMWARE_CHECK_ENDPOINT", "from aiopnsense.firmware import FIRMWARE_CHECK_ENDPOINT"
            ),
            "check",
        ),
        (
            _dispatching_module(
                "CHECK", "from aiopnsense.firmware import FIRMWARE_CHECK_ENDPOINT as CHECK"
            ),
            "check",
        ),
        (
            _dispatching_module("CHECK", "from .firmware import FIRMWARE_CHECK_ENDPOINT as CHECK"),
            "check",
        ),
        (
            """
async def request(client):
    from .firmware import FIRMWARE_CHECK_ENDPOINT as CHECK
    await client._do_post(CHECK)
""",
            "check",
        ),
        (_dispatching_module("FIRMWARE_CHECK_ENDPOINT", "from .firmware import *"), "check"),
        ('FIRMWARE_ACTION_ENDPOINT_PREFIX = "/api/core/firmware/"\n', "action"),
        (_dispatching_module('"/api/core/firmware/update"'), "action"),
        (
            """
class FirmwareMixin:
    async def install_firmware(self, value):
        await self._do_post("/api/core/firmware/update")
""",
            "action",
        ),
    ],
    ids=[
        "attribute",
        "absolute",
        "absolute-alias",
        "relative-alias",
        "function-local",
        "star",
        "prefix-literal",
        "update-literal",
        "install-method-outside-firmware",
    ],
)
def test_guard_rejects_firmware_path_from_another_module(other: str, endpoint: str) -> None:
    """Report a firmware path built in another module by any supported form.

    Args:
        other (str): Source of a second module that reaches a firmware path.
        endpoint (str): Firmware endpoint the other module must be reported for.
    """
    violations = _source_violations({FIRMWARE_MODULE: _firmware_source(), "other.py": other})

    assert len(violations) == 1
    assert violations[0].startswith("aiopnsense/other.py:")
    assert violations[0].endswith(_violation_label(endpoint))


@pytest.mark.parametrize(
    ("other", "expected"),
    [
        ('BASE = "/a"\nBASE = BASE + "/b"\n', []),
        (
            """
BASE = "/api/core/firmware/st"
BASE = BASE + "atus"

async def request(client):
    await client._do_post(BASE)
""",
            [
                "aiopnsense/other.py:3: /api/core/firmware/status",
                "aiopnsense/other.py:6: /api/core/firmware/status",
            ],
        ),
        (
            """
BASE: str = "/api/core/firmware/status"

async def request(client):
    await client._do_post(BASE)
""",
            [
                "aiopnsense/other.py:2: /api/core/firmware/status",
                "aiopnsense/other.py:5: /api/core/firmware/status",
            ],
        ),
        (
            """
BASE = "/api/core/firmware/status"
BASE = compute()

async def request(client):
    await client._do_post(BASE)
""",
            ["aiopnsense/other.py:2: /api/core/firmware/status"],
        ),
    ],
    ids=[
        "unguarded-final-value",
        "guarded-final-value",
        "annotated-value-replayed",
        "unknown-reassignment-forgets",
    ],
)
def test_module_constant_is_replayed_in_source_order(other: str, expected: list[str]) -> None:
    """Resolve module constants by replaying their assignments in source order.

    Assignments resolve in source order, so ``BASE = BASE + "atus"`` sees the value before it.
    Uses inside function bodies resolve to the module's final value.

    Args:
        other (str): Source of a second module that rebuilds a constant from its own value.
        expected (list[str]): Violations the other module must produce.
    """
    assert _source_violations({FIRMWARE_MODULE: _firmware_source(), "other.py": other}) == expected


@pytest.mark.parametrize(
    ("dispatch", "found", "other_endpoints"),
    [
        ("pass", 0, []),
        (f"{CHECK_DISPATCH}\n        {CHECK_DISPATCH}", 2, []),
        ('await self._do_post("/api/core/firmware/running")', 0, []),
        ("await self._post(FIRMWARE_CHECK_ENDPOINT)", 0, ["check"]),
    ],
    ids=["removed", "duplicated", "other-path", "other-method"],
)
def test_guard_requires_exactly_one_check_dispatch(
    dispatch: str, found: int, other_endpoints: list[str]
) -> None:
    """Report a firmware module whose check dispatch is removed, duplicated or replaced.

    The found-count message comes first. Each further violation must be located in the
    firmware module and must describe the endpoint given in ``other_endpoints``, so the
    test does not depend on the fixture's line numbers. An empty list proves there are no
    further violations.

    Args:
        dispatch (str): Replacement body for ``_request_firmware_check``.
        found (int): Number of check dispatches the report must state.
        other_endpoints (list[str]): Endpoint name of each further violation, in order.
    """
    violations = _source_violations({FIRMWARE_MODULE: _firmware_source(dispatch=dispatch)})

    message = "FirmwareMixin._request_firmware_check must contain exactly one check dispatch"
    assert len(violations) == 1 + len(other_endpoints)
    assert violations[0] == f"aiopnsense/firmware.py: {message}, found {found}"
    for violation, endpoint in zip(violations[1:], other_endpoints, strict=True):
        location, _, text = violation.partition(": ")
        assert location.startswith("aiopnsense/firmware.py:")
        assert text == _violation_label(endpoint)


def test_guard_allows_annotated_endpoint_definitions() -> None:
    """Accept annotated endpoint definitions in the firmware module."""
    source = _firmware_source()
    for name in ENDPOINT_DEFINITIONS:
        source = source.replace(f"{name} = ", f"{name}: str = ")

    assert _source_violations({FIRMWARE_MODULE: source}) == []


def test_guard_allows_literal_update_path_in_install_firmware() -> None:
    """Accept the literal update path in install_firmware under the exact update and upgrade gate."""
    source = _firmware_source(
        'await self._do_post("/api/core/firmware/update")',
        method="install_firmware",
        gate=_action_gate("install_firmware", '"update", "upgrade"'),
    )

    assert _source_violations({FIRMWARE_MODULE: source}) == []


@pytest.mark.parametrize("method", ["install_firmware", "upgrade_firmware"])
def test_guard_ignores_tuple_order_in_update_upgrade_gate(method: str) -> None:
    """Accept the action prefix when the reversed update and upgrade gate is in place.

    The gate compares the tuple's values as a set, so tuple order does not matter. The
    ``"update", "upgrade"`` order is covered by the production guard test and the literal
    update path test.

    Args:
        method (str): Action method that dispatches the action.
    """
    gate = _action_gate(method, '"upgrade", "update"')
    source = _firmware_source(ACTION_STATEMENT, method=method, gate=gate)

    assert _source_violations({FIRMWARE_MODULE: source}) == []


@pytest.mark.parametrize("method", ["install_firmware", "upgrade_firmware"])
@pytest.mark.parametrize(
    ("values", "extra"),
    [
        pytest.param('"update", "upgrade", "check"', "", id="check-added"),
        pytest.param(None, "", id="comparison-removed"),
        pytest.param('"update", "upgrade"', ' or type == "check"', id="equality-added"),
    ],
)
def test_guard_rejects_action_method_without_exact_update_upgrade_gate(
    method: str, values: str | None, extra: str
) -> None:
    """Report an action method whose ``type`` gate adds a value, adds a comparison or is missing.

    The action prefix itself stays exempt, so the gate is the only violation reported.

    Args:
        method (str): Action method that dispatches the action.
        values (str | None): Tuple contents of the ``type`` comparison, or ``None`` when
            the method has no comparison.
        extra (str): Text appended after the tuple comparison, such as an extra
            ``or type == "check"`` comparison.
    """
    gate = _action_gate(method, values, extra)
    source = _firmware_source(ACTION_STATEMENT, method=method, gate=gate)

    assert _source_violations({FIRMWARE_MODULE: source}) == [
        f"aiopnsense/firmware.py: FirmwareMixin.{method} must allow only update and upgrade"
    ]


@pytest.mark.parametrize(
    ("statement", "endpoint"),
    [
        ('await self._safe_dict_get("/api/core/firmware/info")', "info"),
        ("await self._do_post(FIRMWARE_STATUS_ENDPOINT)", "status"),
        ("await self._do_post(FIRMWARE_CHECK_ENDPOINT)", "check"),
    ],
)
def test_guard_rejects_non_action_paths_in_install_method(statement: str, endpoint: str) -> None:
    """Report status and other endpoint paths that ``install_firmware`` reaches.

    Only the action prefix is exempt in the install methods, so every other endpoint
    reached from ``install_firmware`` is reported for its own endpoint.

    Args:
        statement (str): Statement added to ``install_firmware``.
        endpoint (str): Firmware endpoint the statement must be reported for.
    """
    violations = _source_violations(
        {
            FIRMWARE_MODULE: _firmware_source(
                statement,
                method="install_firmware",
                gate=_action_gate("install_firmware", '"update", "upgrade"'),
            )
        }
    )

    assert len(violations) == 1
    assert violations[0].endswith(_violation_label(endpoint))


@pytest.mark.parametrize(
    "statement",
    [
        "await self._safe_dict_get(FIRMWARE_STATUS_ENDPOINT)",
        "await self._safe_dict_get(path=FIRMWARE_STATUS_ENDPOINT)",
        "await self._is_get_endpoint_available(FIRMWARE_STATUS_ENDPOINT)",
        "await self._firmware_request(FIRMWARE_STATUS_ENDPOINT)",
        "await self._firmware_request(FIRMWARE_STATUS_ENDPOINT, post=False)",
        "self._endpoint_availability.get(FIRMWARE_STATUS_ENDPOINT)",
        'await self._safe_dict_get("/api/core/firmware/upgradestatus")',
        'await self._do_post("/api/core/firmware/upgradestatus")',
        'await self._safe_dict_post(f"{FIRMWARE_CHANGELOG_ENDPOINT_PREFIX}{value}")',
        '"""Never call /api/core/firmware/info."""',
    ],
)
def test_guard_allows_safe_firmware_requests(statement: str) -> None:
    """Accept status reads, unrelated endpoints and documentation.

    Args:
        statement (str): Statement added to a firmware method.
    """
    assert _source_violations({FIRMWARE_MODULE: _firmware_source(statement)}) == []
