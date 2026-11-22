# Coding Standards

Standards for code in `aiopnsense/` and `tests/`. Rules that `ruff`, `mypy` or `pydoclint` already enforce are configured in `pyproject.toml` and are not repeated here; run `prek` before committing. Use the terms defined in [GLOSSARY.md](GLOSSARY.md) in code, docstrings, log messages and tests, and read the ADRs in [docs/adr/](docs/adr/) before changing the behavior they record.

## Language and tooling

- Target Python 3.14 or newer. PEP 695 type parameters and PEP 758 grouped exception handlers are allowed.
- Run Python, `pytest`, and `prek` through the repository virtualenv (`./.venv/bin/...`). A worktree without dependency changes may use the main checkout's virtualenv. Follow the [worktree import guidance](AGENTS.md#local-tooling-note) for ad hoc Python commands.
- Run the full test suite by default. When running a subset, say why.

## Structure

- Each domain lives in its own module as a `<Domain>Mixin(AiopnsenseClientProtocol)`, and `OPNsenseClient` in `client.py` composes the mixins. Shared transport, queue and endpoint logic belongs in the `client_*` modules, not in a domain mixin. Toggles are run by `client_toggle.py`: a domain mixin describes its endpoints as a `ToggleSpec` and calls `_run_toggle`, instead of writing its own save-then-apply pipeline.
- Expose new public functionality as a method on its domain mixin (a `@staticmethod` is fine when it needs no client state) and its models as nested frozen dataclasses. `docs/source/_ext/opnsense_client_api.py` generates the API reference from their docstrings, so add no module-level public functions, no package-root exports for them and no hand-written `docs/source/api` entries.
- A domain mixin reaches other mixins and shared state only through `AiopnsenseClientProtocol` in `_typing.py`. Add the member there instead of importing another mixin.
- `_typing.py` holds protocol members as typed signatures ending in `...`, with no per-method docstrings and no `abstractmethod`.
- Endpoint paths are module-level `UPPER_CASE` constants at the top of the module that uses them. Firmware thresholds and shared tunables live in `const.py`.
- Keep imports at the top of the file.

## Typing

- Annotate every function and class, including return types.
- Do not use `assert` or `cast` in code under `aiopnsense/`. In tests, keep `cast` and `Any` to the boundaries that need them.

## Docstrings and comments

- Give every file, class and method a docstring, including private and nested methods. Method docstrings use Google style with `Args`, `Returns` and, where relevant, `Raises`.
- Add a `Raises` section only when the method contains a literal `raise`. When errors surface through a helper or `@_log_errors` (the `throw_errors` mode), describe that behavior under `Returns`, because `pydoclint` rejects a `Raises` section without a literal `raise` and a per-method `noqa` does not suppress it.
- Update the docstring in the same change as the behavior it describes, and preserve existing comments unless they become wrong.

## Errors and logging

- Catch specific exceptions. A broad `except Exception` is allowed only where it already exists, in the shared `_log_errors` decorator, the queue worker and the cleanup guard in `OPNsenseClient.__aenter__` that keeps a failing `async_close()` from replacing the validation error, and carries a reason.
- Never swallow `asyncio.CancelledError`; re-raise it.
- Public methods follow the client's `throw_errors` setting: with it off they return the documented empty value (`None`, `False` or an empty mapping) after logging, and with it on they raise a subclass of `OPNsenseError`. `aiopnsense/_error_policy.py` centralizes this decision and shared error handling for wrappers. Request, endpoint and queue layers also map and log errors. It is the only code that reads `_throw_errors`, apart from the client's setter and toggle and `validate`, which sets it for its own duration. Get the behavior from `@_log_errors` for a whole method, from `_attempt` for one step that has its own empty value (pass `empty=`, and `errors=` or `log_level=` only when the default does not fit), or from `_raise_if_throwing` for a failure you detect without an exception. The shared request reporter uses `_error_if_throwing` to mark errors it expects callers to raise, and endpoint probes use `_throws_errors` for cache decisions. Do not write a new `try/except` around requests or read `_throw_errors` in domain code, unless the method needs its own error semantics, as the VPN Toggle does for cancellation.
- The policy marks exceptions it reports at the default level before propagating them. Wrappers skip exceptions that the policy or transport has already marked. Best-effort steps can log at debug and still receive the usual outer report. Outer handlers report newly detected failures. Raise a fresh exception instance per failure.
- Optional or version-dependent reads use `_fetch_endpoint_outcome` (or `_fetch_post_endpoint_outcome` for POST payloads). `is_data` carries the payload, `is_absent` means 404 in either mode or 403 with errors disabled, and `is_transient` means a transient HTTP/transport failure. In throw mode, 403 and transient failures raise in the endpoint module while 404 stays absent. The helper owns availability caching. Cached absence may skip a request, and each fetch sends at most one. Undecodable JSON is separate and normally follows `@_log_errors`, returning None with errors disabled or mapped `OPNsenseError` when enabled, unless a method handles decoding explicitly. Use `_check_endpoint_status` only when a bounded fetch is unsuitable, such as a stream or state-changing GET. Absence logs at debug. 403/transient failures log once per outage.
- Status errors raised by middleware before the response reaches the outcome callback count as endpoint results only when request metadata matches the endpoint method and URL. A middleware error raised after a redirect may lack redirect history, so that unattributed error remains transient.
- Immediate GET and POST requests and streaming reads report failed responses and transport errors through the shared classification in `_request_failure.py`. It maps failures to public exceptions, suppresses repeated outage logs and marks raised exceptions as already logged, so the queue worker and `@_log_errors` do not log them again. Bounded endpoint outcome fetches and status-only checks classify outcomes in `client_endpoint.py`, using the shared outage logging helpers with the probe phase. They cache 404 as unavailable; endpoint policy handles 403 and transient failures. Stream outage logging distinguishes HTTP 401, HTTP 403 and other failures so that authentication or permission changes are visible. Firmware installation requests intentionally use their own polling and error policy and do not use this reporter. Each request path applies the shared error policy and handles its own empty result; do not copy the 403, other-status and transport-error handling into a new path.
- Log with `%`-style lazy arguments, never f-strings. Prefix debug messages in public methods with the method name in brackets, for example `[toggle_firewall_rule]`.
- Never log credentials. Exception details go through the existing redaction in `helpers.py`.

## Public API and return shapes

- Names without a leading underscore are public. Deprecate with `warnings.deprecated`, naming the replacement, and keep the old behavior working, as `get_arp_table` does.
- The pytest suite enforces a leading underscore on module-scope classes, functions, type aliases, plain `=` assignments and annotated assignments in private modules, including declarations inside control flow. It skips declarations local to classes and functions. `AiopnsenseClientProtocol` in `_typing.py` is the internal mixin contract and keeps its established name.
- Return new structured data as frozen, slotted dataclasses rather than loose dictionaries.

## Firmware compatibility

- Behavior that differs between OPNsense releases is gated with `firmware_is_at_least` against a named constant in `const.py`. Do not compare version strings inline.
- Endpoints that exist in both snake_case and camelCase forms are chosen with `_get_endpoint_path`.
- When a gate changes what a user sees below the Recommended Firmware, update the requirements section of `README.md`.

## Tests

- Use `pytest`, with one `tests/test_<module>.py` per module. Put shared fixtures in `tests/conftest.py`. Async tests need no marker, because `asyncio_mode` is `auto`.
- Type and document tests the same way as library code. Parametrize instead of copying a test.
- Test behavior through the public client interface, including the failure and no-op paths. Do not assert private call order or mirror the implementation.
- Put tests that need real queue worker tasks in `tests/test_client_base.py` or `tests/test_client_queue.py`. The autouse `_patch_asyncio_create_task` fixture in `tests/conftest.py` replaces worker tasks with stubs in every other test module.
- Test an endpoint read by installing a fake Router with `install_fake_router` from `tests/conftest.py` and calling the public method. Give the Router a status code, an exception, a payload or a text body per path, then assert the returned value, the raised error and `router.count(...)`. Do not mock the availability step. The fixture runs queued requests immediately, so these tests need no queue workers.
- Inject errors into a mocked call with `AsyncMock(side_effect=[...])`. A test helper that does `raise exc` on a variable trips `pydoclint` (DOC501 and DOC503) and forces a `Raises` section that cannot match.
- Add or update tests with every behavior change, and update the documentation it affects.
