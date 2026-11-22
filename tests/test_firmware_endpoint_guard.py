"""Enforce the firmware endpoint rule from ADR 0002 on production code.

The guard resolves string constants, imports, concatenation and f-strings, then
fails closed: a status or check path is allowed only in the uses listed here, so
copying one into a variable or helper is a violation. Paths computed entirely at
runtime, such as ``f"{PREFIX}{type}"``, cannot be resolved and are not checked.
"""

import ast
from collections.abc import Iterator
from pathlib import Path
import re

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "aiopnsense"
FIRMWARE_MODULE = "firmware.py"
CHECK_DISPATCH_SCOPE = ("FirmwareMixin", "_request_firmware_check")
ENDPOINT_DEFINITIONS = {
    "FIRMWARE_CHECK_ENDPOINT": "check",
    "FIRMWARE_STATUS_ENDPOINT": "status",
}
GET_HELPERS = frozenset(
    {
        "_do_get",
        "_firmware_request",
        "_get",
        "_get_text",
        "_is_get_endpoint_available",
        "_safe_dict_get",
        "_safe_dict_get_with_timeout",
        "_safe_list_get",
    }
)
GUARDED_PATH = re.compile(r"/api/core/firmware/(info|status|check)(?![\w-])", re.IGNORECASE)
UNKNOWN = "\0"

FIRMWARE_SOURCE = """
FIRMWARE_STATUS_ENDPOINT = "/api/core/firmware/status"
FIRMWARE_CHECK_ENDPOINT = "/api/core/firmware/check"
FIRMWARE_ACTION_ENDPOINT_PREFIX = "/api/core/firmware/"

class FirmwareMixin:
    async def _request_firmware_check(self):
        await self._do_post(FIRMWARE_CHECK_ENDPOINT)

    async def probe(self, value):
        {statement}
"""


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
        case ast.Name(id=name):
            return constants.get(name, UNKNOWN)
        case ast.JoinedStr(values=values):
            return "".join(_resolve(value, constants) for value in values)
        case ast.FormattedValue(value=value):
            return _resolve(value, constants)
        case ast.BinOp(left=left, op=ast.Add(), right=right):
            return _resolve(left, constants) + _resolve(right, constants)
    return UNKNOWN


def _package_constants(modules: dict[str, ast.Module]) -> dict[str, dict[str, str]]:
    """Return each module's string constants, following relative imports.

    Args:
        modules (dict[str, ast.Module]): Parsed modules by package-relative path.

    Returns:
        dict[str, dict[str, str]]: Constant values by module and name.
    """
    constants: dict[str, dict[str, str]] = {path: {} for path in modules}
    changed = True
    while changed:
        changed = False
        for path, module in modules.items():
            scope = constants[path]
            found: dict[str, str] = {}
            for statement in module.body:
                match statement:
                    case ast.ImportFrom(module=str() as source, names=names, level=1):
                        imported = constants.get(f"{source.replace('.', '/')}.py", {})
                        for alias in names:
                            if alias.name in imported:
                                found[alias.asname or alias.name] = imported[alias.name]
                    case (
                        ast.Assign(targets=[ast.Name(id=name)], value=value)
                        | ast.AnnAssign(target=ast.Name(id=name), value=ast.expr() as value)
                    ):
                        resolved = _resolve(value, scope)
                        if UNKNOWN not in resolved:
                            found[name] = resolved
            if found.items() - scope.items():
                scope.update(found)
                changed = True
    return constants


def _references(node: ast.AST, constants: dict[str, str]) -> Iterator[tuple[ast.expr, str]]:
    """Yield the outermost expressions that build a guarded firmware path.

    Args:
        node (ast.AST): Node to search.
        constants (dict[str, str]): String constants visible in the module.

    Yields:
        tuple[ast.expr, str]: Expression and the endpoint name it builds.
    """
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
        return  # Docstrings can explain the rule without sending a request.
    match node:
        case ast.Constant() | ast.JoinedStr() | ast.BinOp() | ast.Name(ctx=ast.Load()):
            if found := GUARDED_PATH.search(_resolve(node, constants)):
                yield node, found[1].lower()
                return
    for child in ast.iter_child_nodes(node):
        yield from _references(child, constants)


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


def _is_status_read(call: ast.Call) -> bool:
    """Return whether a call only reads the path passed as its first argument.

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


def _check_dispatches(module: ast.Module) -> list[ast.Call]:
    """Return ``_do_post`` calls inside the approved check dispatch method.

    Args:
        module (ast.Module): Parsed firmware module.

    Returns:
        list[ast.Call]: Calls whose first argument may be the check endpoint.
    """
    constants = {name: f"/api/core/firmware/{path}" for name, path in ENDPOINT_DEFINITIONS.items()}
    for statement in module.body:
        if isinstance(statement, ast.ClassDef) and statement.name == CHECK_DISPATCH_SCOPE[0]:
            for method in statement.body:
                if (
                    isinstance(method, ast.AsyncFunctionDef)
                    and method.name == CHECK_DISPATCH_SCOPE[1]
                ):
                    return [
                        node
                        for node in ast.walk(method)
                        if isinstance(node, ast.Call)
                        and _self_method(node) == "_do_post"
                        and node.args
                        and _resolve(node.args[0], constants)
                        == constants["FIRMWARE_CHECK_ENDPOINT"]
                    ]
    return []


def _module_violations(
    module: ast.Module, relative_path: str, constants: dict[str, str]
) -> list[str]:
    """Return references to firmware endpoints that ADR 0002 does not allow.

    Args:
        module (ast.Module): Parsed Python source.
        relative_path (str): Module path relative to the package.
        constants (dict[str, str]): String constants visible in the module.

    Returns:
        list[str]: Formatted source locations and the rule each one breaks.
    """
    allowed: dict[int, str] = {}
    for node in ast.walk(module):
        if isinstance(node, ast.Call) and node.args and _is_status_read(node):
            allowed[id(node.args[0])] = "status"
    if relative_path == FIRMWARE_MODULE:
        for statement in module.body:
            match statement:
                case ast.Assign(targets=[ast.Name(id=name)], value=value) if (
                    name in ENDPOINT_DEFINITIONS
                ):
                    allowed[id(value)] = ENDPOINT_DEFINITIONS[name]
        for call in _check_dispatches(module):
            allowed[id(call.args[0])] = "check"

    return [
        f"aiopnsense/{relative_path}:{expression.lineno}: /api/core/firmware/{endpoint}"
        for expression, endpoint in _references(module, constants)
        if allowed.get(id(expression)) != endpoint
    ]


def _source_violations(sources: dict[str, str]) -> list[str]:
    """Parse sources as one package and return their endpoint violations.

    Args:
        sources (dict[str, str]): Python source by package-relative path.

    Returns:
        list[str]: Violations from every module.
    """
    modules = {path: ast.parse(source, filename=path) for path, source in sources.items()}
    constants = _package_constants(modules)
    return [
        violation
        for path, module in modules.items()
        for violation in _module_violations(module, path, constants[path])
    ]


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
    dispatches = _check_dispatches(ast.parse(sources[FIRMWARE_MODULE]))
    assert len(dispatches) == 1, (
        "FirmwareMixin._request_firmware_check must contain the one check dispatch"
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
        ("await self._session.post(FIRMWARE_STATUS_ENDPOINT)", "status"),
        ('await self._post(f"{FIRMWARE_ACTION_ENDPOINT_PREFIX}status")', "status"),
        ("path = FIRMWARE_STATUS_ENDPOINT", "status"),
        ("await self._do_post(FIRMWARE_CHECK_ENDPOINT)", "check"),
        ("await self._post(FIRMWARE_CHECK_ENDPOINT)", "check"),
        ("await self._firmware_request(FIRMWARE_CHECK_ENDPOINT, post=True)", "check"),
        ('await self._do_post(f"{FIRMWARE_ACTION_ENDPOINT_PREFIX}check")', "check"),
        ("path = FIRMWARE_CHECK_ENDPOINT", "check"),
    ],
)
def test_guard_rejects_forbidden_firmware_requests(statement: str, endpoint: str) -> None:
    """Report each forbidden way of reaching a firmware endpoint.

    Args:
        statement (str): Statement added to a firmware method.
        endpoint (str): Firmware endpoint the statement must be reported for.
    """
    source = FIRMWARE_SOURCE.replace("{statement}", statement)

    violations = _source_violations({FIRMWARE_MODULE: source})

    assert len(violations) == 1
    assert violations[0].endswith(f"/api/core/firmware/{endpoint}")


def test_guard_follows_constants_imported_by_another_module() -> None:
    """Report a check dispatched from another module through an imported alias."""
    other = """
from .firmware import FIRMWARE_CHECK_ENDPOINT as CHECK

async def request(client):
    await client._do_post(CHECK)
"""
    source = FIRMWARE_SOURCE.replace("{statement}", "pass")

    violations = _source_violations({FIRMWARE_MODULE: source, "other.py": other})

    assert violations == ["aiopnsense/other.py:5: /api/core/firmware/check"]


@pytest.mark.parametrize(
    "statement",
    [
        "await self._safe_dict_get(FIRMWARE_STATUS_ENDPOINT)",
        "await self._is_get_endpoint_available(FIRMWARE_STATUS_ENDPOINT)",
        "await self._firmware_request(FIRMWARE_STATUS_ENDPOINT)",
        "await self._firmware_request(FIRMWARE_STATUS_ENDPOINT, post=False)",
        "self._endpoint_availability.get(FIRMWARE_STATUS_ENDPOINT)",
        'await self._do_post(f"{FIRMWARE_ACTION_ENDPOINT_PREFIX}{value}")',
        'await self._safe_dict_get("/api/core/firmware/upgradestatus")',
        '"""Never call /api/core/firmware/info."""',
    ],
)
def test_guard_allows_safe_firmware_requests(statement: str) -> None:
    """Accept status reads, unrelated endpoints and documentation.

    Args:
        statement (str): Statement added to a firmware method.
    """
    source = FIRMWARE_SOURCE.replace("{statement}", statement)

    assert _source_violations({FIRMWARE_MODULE: source}) == []
