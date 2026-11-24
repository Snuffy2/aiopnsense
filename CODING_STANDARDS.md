# Coding Standards

Standards for code in `aiopnsense/` and `tests/`. Rules that `ruff`, `mypy` or `pydoclint` already enforce are configured in `pyproject.toml` and are not repeated here; run `prek` before committing. Use the terms defined in [GLOSSARY.md](GLOSSARY.md) in code, docstrings, log messages and tests, and read the ADRs in [docs/adr/](docs/adr/) before changing the behavior they record.

## Language and tooling

- Target Python 3.14 or newer. PEP 695 type parameters and PEP 758 grouped exception handlers are allowed.
- Run Python, `pytest`, and `prek` through the repository virtualenv (`./.venv/bin/...`). A worktree without dependency changes may use the main checkout's virtualenv. Follow the [worktree import guidance](AGENTS.md#local-tooling-note) for ad hoc Python commands.
- Run the full test suite by default. When running a subset, say why.

## Structure

- Each domain lives in its own module as a `<Domain>Mixin(AiopnsenseClientProtocol)`, and `OPNsenseClient` in `client.py` composes the mixins. Shared transport, queue and endpoint logic belongs in the `client_*` modules, not in a domain mixin. Queued requests are described by `_RequestKind` in `client_transport.py` and sent through its one `_dispatch_request`; the queue worker never chooses among transport methods, and queue entries are `_QueueEntry` objects, not tuples. Caller naming (`_public_caller_name`) lives in `client_transport.py` too. Toggles are run by `client_toggle.py`: a domain mixin describes its endpoints as a `ToggleSpec` and calls `_run_toggle`, instead of writing its own save-then-apply pipeline.
- Expose new public functionality as a method on its domain mixin (a `@staticmethod` is fine when it needs no client state) and its models as nested frozen dataclasses. `docs/source/_ext/opnsense_client_api.py` generates the API reference from their docstrings, so add no module-level public functions, no package-root exports for them and no hand-written `docs/source/api` entries.
- A domain mixin reaches other mixins and shared state only through `AiopnsenseClientProtocol` in `_typing.py`. Add the member there instead of importing another mixin.
- `_typing.py` holds protocol members as typed signatures ending in `...`, with no per-method docstrings and no `abstractmethod`.
- Endpoint paths are module-level `UPPER_CASE` constants at the top of the module that uses them. An endpoint that exists in both Endpoint Styles is one `_StyledEndpoint` constant, never two separate constants. Firmware thresholds and shared tunables live in `const.py`.
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
- Public methods follow the client's `throw_errors` setting: with it off they return the documented empty value (`None`, `False` or an empty mapping) after logging, and with it on they raise a subclass of `OPNsenseError`. `aiopnsense/_error_policy.py` centralizes this decision and shared error handling for wrappers. Request, endpoint and queue layers also map and log errors. It is the only code that reads `_throw_errors`, apart from the client's setter and toggle and `validate`, which sets it for its own duration. Get the behavior from `@_log_errors` for a whole method, from `_attempt` for one step that has its own empty value (pass `empty=`, and `errors=` or `log_level=` only when the default does not fit), or from `_raise_if_throwing` for a failure you detect without an exception. The shared request reporter uses `_error_if_throwing` to mark errors it expects callers to raise, and endpoint outcomes use `_throws_errors` for cache decisions. Do not write a new `try/except` around requests or read `_throw_errors` in domain code, unless the method needs its own error semantics, as the VPN Toggle does for cancellation.
- The policy marks exceptions it reports at the default level before propagating them. Wrappers skip exceptions that the policy or transport has already marked. Best-effort steps can log at debug and still receive the usual outer report. Outer handlers report newly detected failures. Raise a fresh exception instance per failure.
- The immediate GET and POST, endpoint status check and both streaming reads report failed responses and transport errors through the shared classification in `_request_failure.py`. It maps failures to public exceptions, suppresses repeated outage logs and marks raised exceptions as already logged, so the queue worker and `@_log_errors` do not log them again. Endpoint outcomes cache HTTP 404 as unavailable before invoking the failure reporter. Stream outage logging distinguishes HTTP 401, HTTP 403 and other failures so that authentication or permission changes are visible. Firmware installation requests intentionally use their own polling and error policy and do not use this reporter. Each request path applies the shared error policy and handles its own empty result; do not copy the 403, other-status and transport-error handling into a new path.
- New optional or version-dependent reads use `_fetch_endpoint_outcome` (or `_fetch_post_endpoint_outcome` for POST payloads). `is_data` carries the payload, `is_absent` means 404 in either mode or 403 with errors disabled, and `is_transient` means a transient HTTP/transport failure. In throw mode, 403 and transient failures raise in the endpoint module while 404 stays absent. The helper owns availability caching: cached absence may skip a request, and each fetch sends at most one. Use `_check_endpoint_status` only when a bounded fetch is unsuitable, such as a stream or state-changing GET. Undecodable JSON is separate and follows `@_log_errors` unless a method handles decoding explicitly.
- When a Router response drives client state or a reported outcome, such as a cooldown, retry, cache or success result, the author and reviewer read the OPNsense controller action and its backend failure path, and the code relies only on the fields shown to prove success.
- Log with `%`-style lazy arguments, never f-strings. Prefix debug messages in public methods with the method name in brackets, for example `[toggle_firewall_rule]`.
- Never log credentials. Exception details go through the existing redaction in `helpers.py`.

## Public API and return shapes

- Names without a leading underscore are public. Deprecate with `warnings.deprecated`, naming the replacement, and keep the old behavior working, as `get_arp_table` does.
- The pytest suite enforces a leading underscore on module-scope classes, functions, type aliases, plain `=` assignments and annotated assignments in private modules, including declarations inside control flow. It skips declarations local to classes and functions. `AiopnsenseClientProtocol` in `_typing.py` is the internal mixin contract and keeps its established name.
- Return new structured data as frozen, slotted dataclasses rather than loose dictionaries.

## Firmware compatibility

- Behavior that differs between OPNsense releases is a Firmware Capability: a `FirmwareCapability` in `const.py` with its threshold constant and its answer for Unknown Firmware. Do not compare version strings or call `firmware_is_at_least` in feature code; ask the firmware module with `_firmware_capability` (known or unknown) or `_firmware_capability_enabled` (unknown mapped to the declared answer).
- Unknown Firmware is never remembered. A feature uses the capability's declared answer for that call and asks again on its next use. Startup validation (`validate`) is the one place where Unknown Firmware is an error.
- Only the firmware module writes the cached firmware version, through `_record_firmware_version`. A known version that differs from the last known one resets everything derived from it (Endpoint Style and the endpoint availability cache).
- Pass an endpoint with both Endpoint Styles (a `_StyledEndpoint`) directly to `_fetch_endpoint_outcome` or `_fetch_post_endpoint_outcome`, which resolve it for the Router's firmware. Call `_resolve_endpoint` only where the site needs the resolved string: a log, a comparison, a path reused across a loop, or a string-only helper.
- When a gate changes what a user sees below the Recommended Firmware, update the requirements section of `README.md`.

## Tests

- Use `pytest`, with one `tests/test_<module>.py` per module. Put shared fixtures in `tests/conftest.py`. Async tests need no marker, because `asyncio_mode` is `auto`.
- Type and document tests the same way as library code. Parametrize instead of copying a test.
- Test behavior through the public client interface, including the failure and no-op paths. Do not assert private call order or mirror the implementation.
- Fake a Router at the aiohttp session with `make_router_client` from `tests/conftest.py`, which builds the client and its `FakeRouter` and can report a firmware version. The `FakeRouter` answers by HTTP method and path (`routes`) and records each request, so a test sets Router state, calls a public client method and asserts the result or what the Router received (`router.count(method, path)`, `router.bodies(method, path)`, `router.paths(method)`, `router.calls()`). A route value may be a callable that receives the request parameters and returns the reply, for Router state that changes between requests. `HeldResponse` holds a request in flight and `SequencedResponse` answers successive reads in turn. A request without a route is recorded and answered with 404, as a real Router answers a missing endpoint, so list every path the call is expected to reach. The fake must not read, copy or replace the queue's method names or any private request method. Never call `pytest.fail` inside a fake request: it raises a `BaseException` that the queue worker does not catch, which kills the worker and leaves the caller waiting.
- Every client runs on real queue workers. The autouse `_no_queue_pacing` fixture in `tests/conftest.py` sets the queue pacing delay to zero, so do not add a sleep to wait for the queue, and `make_client` cleanup stops the workers. Tests that need real workers may live in any test module.
- Do not call, assign or patch private request methods (`_get`, `_post`, `_do_get`, `_do_post`, `_queue_request`, `_fetch_endpoint_outcome`, `_check_endpoint_status` and similar) in a domain test. Only tests of a layer with no public equivalent may, because they exercise that layer directly: the queue (`tests/test_client_queue.py`), the transport (`tests/test_client_transport.py`), the endpoint availability layer (`tests/test_client_endpoint.py`, which calls `_fetch_endpoint_outcome`, `_fetch_post_endpoint_outcome`, `_check_endpoint_status`, `_resolve_endpoint`, `_set_use_snake_case` and `_reset_endpoint_state`), the raw queue entry in `tests/test_client_base.py` (`_do_get` and the worker startup helpers) and the ADR 0002 guard tests in `tests/test_firmware_endpoint_guard.py` (the request entry points, to prove the boundary blocks each).
- Write endpoint paths in tests as string literals, never imported or read from the production `*_ENDPOINT` constants, because a changed production path would then move the code and the fake Router together and no test would fail. `tests/test_endpoint_literals_guard.py` enforces this for every test module.
- Inject errors into a mocked call with `AsyncMock(side_effect=[...])`. A test helper that does `raise exc` on a variable trips `pydoclint` (DOC501 and DOC503) and forces a `Raises` section that cannot match.
- Add or update tests with every behavior change, and update the documentation it affects.
