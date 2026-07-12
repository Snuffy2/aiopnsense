# Optional Device-ID Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow callers that represent a virtual endpoint, such as a CARP VIP, to validate connectivity, authentication, and firmware support without requiring a physical-device unique ID.

**Architecture:** Add one backward-compatible, keyword-only `require_device_id` argument to `OPNsenseClient.validate()`. The default remains `True`; callers opting out skip only the final `get_device_unique_id()` request and retain all existing validation behavior and exception mapping.

**Tech Stack:** Python 3.14+, aiohttp, pytest, prek, Sphinx/reStructuredText.

## Global Constraints

- Keep `require_device_id` keyword-only and default it to `True` so existing callers and `OPNsenseClient.__aenter__()` remain unchanged.
- The option controls only whether validation requires a resolvable device ID; it does not compare a device ID with an expected value.
- Do not add CARP-specific behavior or terminology to the library API.
- Do not change `get_device_unique_id()` or its exception behavior.
- Use repository tooling through `./.venv/bin/python -m pytest` and `./.venv/bin/python -m prek run -a`.

---

## File Structure

- `aiopnsense/client.py`: Add and document the optional device-ID requirement.
- `tests/test_client_base.py`: Prove the default remains strict and the opt-out skips only the device-ID request.
- `docs/source/quickstart.rst`: Document the exceptional virtual-endpoint validation form.

---

### Task 1: Make the device-ID validation probe optional

**Files:**
- Modify: `aiopnsense/client.py:119-166`
- Modify: `tests/test_client_base.py:288-324`
- Modify: `docs/source/quickstart.rst:24-52`

**Interfaces:**
- Produces: `OPNsenseClient.validate(*, require_device_id: bool = True) -> None`.
- Preserves: `OPNsenseClient.validate()` and `OPNsenseClient.__aenter__()` continue requiring a device ID.

- [ ] **Step 1: Write the failing opt-out test**

Add this focused test beside the existing missing-device-ID validation test:

```python
@pytest.mark.asyncio
async def test_validate_can_skip_device_unique_id_requirement(
    monkeypatch: pytest.MonkeyPatch,
    make_client: MakeClientFactory,
) -> None:
    """Verify validation can skip only the device unique ID request.

    Args:
        monkeypatch (pytest.MonkeyPatch): Fixture for overriding client methods.
        make_client (MakeClientFactory): Fixture factory returning `OPNsenseClient` instances.

    Returns:
        None: This test asserts opt-out behavior and state restoration.
    """
    client, _session = make_mock_session_client(make_client)
    client._throw_errors = False
    try:
        get_host_firmware_version = AsyncMock(return_value=OPNSENSE_LTD_FIRMWARE)
        get_device_unique_id = AsyncMock(side_effect=OPNsenseMissingDeviceUniqueID)
        _patch_validate_requests(
            monkeypatch,
            client,
            get_host_firmware_version,
            get_device_unique_id,
        )

        await client.validate(require_device_id=False)

        assert client._throw_errors is False
        get_host_firmware_version.assert_awaited_once()
        get_device_unique_id.assert_not_awaited()
    finally:
        await client.async_close()
```

Keep `test_validate_raises_when_device_unique_id_missing()` unchanged; it is the regression proof that the default remains strict.

- [ ] **Step 2: Run the targeted test and verify it fails**

Run:

```bash
./.venv/bin/python -m pytest tests/test_client_base.py::test_validate_can_skip_device_unique_id_requirement -q
```

Expected: FAIL because `validate()` does not yet accept `require_device_id`.

- [ ] **Step 3: Add the minimal keyword-only option**

Change the method signature:

```python
async def validate(self, *, require_device_id: bool = True) -> None:
```

Add this `Args` section to the existing method docstring before `Raises`:

```python
Args:
    require_device_id (bool): Whether validation must resolve a physical-device unique ID.
```

Change the existing `OPNsenseMissingDeviceUniqueID` description to:

```python
OPNsenseMissingDeviceUniqueID: Raised when no device unique ID can be
    resolved and `require_device_id` is `True`.
```

Replace only the existing final device-ID validation request with:

```python
if require_device_id:
    await self._run_validation_request(self.get_device_unique_id)
```

Do not change `__aenter__()`; its existing `await self.validate()` call must retain strict validation.

- [ ] **Step 4: Run the complete client validation test module**

Run:

```bash
./.venv/bin/python -m pytest tests/test_client_base.py -q
```

Expected: PASS, including the existing default missing-ID and device-ID transport-error tests.

- [ ] **Step 5: Document the opt-out**

After the explicit-validation example in `docs/source/quickstart.rst`, add:

```rst
Virtual endpoints
-----------------

Applications that intentionally connect to a virtual endpoint without stable physical-device
identity can retain connection, authentication, and firmware validation while skipping the
device-ID requirement:

.. code-block:: python

   await client.validate(require_device_id=False)

This option skips only the device-ID request. Applications remain responsible for validating
any endpoint-specific payloads they require.
```

- [ ] **Step 6: Run all repository gates**

Run:

```bash
./.venv/bin/python -m pytest
./.venv/bin/python -m prek run -a
git diff --check
```

Expected: all tests and checks pass.

- [ ] **Step 7: Commit the library change**

```bash
git add aiopnsense/client.py tests/test_client_base.py docs/source/quickstart.rst
git commit -m "Allow validation without device identity"
```

---

## Self-Review Checklist

- Spec coverage: the plan changes only the physical-device ID requirement while preserving every other validation probe and error mapping.
- Compatibility: no-argument validation and async context-manager entry remain strict.
- Terminology: the public option describes identity requirements without introducing integration-specific CARP semantics.
- Placeholder scan: every code change, test, command, and expected result is explicit.
