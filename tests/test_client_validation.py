"""Public validation behavior against the FakeRouter."""

import asyncio

import pytest

from aiopnsense import OPNsenseClient
from aiopnsense.const import OPNSENSE_RECOMMENDED_FIRMWARE
from aiopnsense.exceptions import (
    OPNsenseConnectionError,
    OPNsenseInvalidAuth,
    OPNsenseMissingDeviceUniqueID,
)
from tests.conftest import (
    HeldResponse,
    MakeClientFactory,
    firmware_status_payload,
    make_router_client,
)

_FIRMWARE_STATUS = "/api/core/firmware/status"
_SERVICE_SEARCH = "/api/core/service/search"
_DEVICE_ID = "/api/interfaces/overview/export"
_UNBOUND_SETTINGS = "/api/unbound/settings/get"
_TRAFFIC_PROBE = "/api/diagnostics/traffic/stream/1"


async def _assert_service_failure(client: OPNsenseClient, *, throw_errors: bool) -> None:
    """Check how a service search reports the Router's HTTP 503 response.

    Args:
        client (OPNsenseClient): Client using the FakeRouter.
        throw_errors (bool): Whether the service search should raise.
    """
    if throw_errors:
        with pytest.raises(OPNsenseConnectionError):
            await client.get_services()
    else:
        assert await client.get_services() == []


@pytest.mark.parametrize("start_workers_first", [True, False], ids=["existing", "first-use"])
@pytest.mark.parametrize("throw_errors", [False, True], ids=["default", "strict"])
async def test_validation_does_not_change_service_failure_policy(
    make_client: MakeClientFactory, *, start_workers_first: bool, throw_errors: bool
) -> None:
    """A service search follows the configured policy while validation is in flight.

    Args:
        make_client (MakeClientFactory): Factory for clients with real queue workers.
        start_workers_first (bool): Whether a service search starts the workers.
        throw_errors (bool): Configured error mode for ordinary calls.
    """
    firmware = HeldResponse(json_payload=firmware_status_payload(OPNSENSE_RECOMMENDED_FIRMWARE))
    client, router = make_router_client(
        make_client,
        {("GET", _FIRMWARE_STATUS): firmware, ("GET", _SERVICE_SEARCH): 503},
        throw_errors=throw_errors,
    )
    if start_workers_first:
        await _assert_service_failure(client, throw_errors=throw_errors)

    async with asyncio.timeout(3), asyncio.TaskGroup() as tasks:
        validation = tasks.create_task(client.validate(require_device_id=False))
        await firmware.entered.wait()
        try:
            await _assert_service_failure(client, throw_errors=throw_errors)
        finally:
            firmware.release.set()
        await validation

    await _assert_service_failure(client, throw_errors=throw_errors)
    assert router.count("GET", _SERVICE_SEARCH) == (3 if start_workers_first else 2)
    assert router.count("GET", _FIRMWARE_STATUS) == 1
    assert router.paths("POST") == []


async def test_session_callback_task_keeps_default_after_validation(
    make_client: MakeClientFactory,
) -> None:
    """A session callback can schedule ordinary polling during a validation request.

    Args:
        make_client (MakeClientFactory): Factory for clients with real queue workers.
    """
    client, router = make_router_client(make_client, {("GET", _SERVICE_SEARCH): 503})
    validation_finished = asyncio.Event()

    async def poll_after_validation() -> None:
        """Run a service search independently after validation returns."""
        await validation_finished.wait()
        assert await client.get_services() == []

    async with asyncio.timeout(3), asyncio.TaskGroup() as tasks:

        def firmware_reply(**_request: object) -> dict[str, dict[str, str]]:
            """Schedule the poll in the request callback and return firmware status.

            Args:
                _request (object): Unused request parameters.

            Returns:
                dict[str, dict[str, str]]: Firmware status reported by the Router.
            """
            tasks.create_task(poll_after_validation())
            return {"product": {"product_version": OPNSENSE_RECOMMENDED_FIRMWARE}}

        router.routes[("GET", _FIRMWARE_STATUS)] = firmware_reply
        await client.validate(require_device_id=False)
        validation_finished.set()

    assert router.count("GET", _SERVICE_SEARCH) == 1


@pytest.mark.parametrize("cancel", [False, True], ids=["completion", "cancellation"])
async def test_default_change_survives_validation(
    make_client: MakeClientFactory, *, cancel: bool
) -> None:
    """The public toggle changes the default while validation is in flight.

    Args:
        make_client (MakeClientFactory): Factory for clients with real queue workers.
        cancel (bool): Whether validation is cancelled instead of completing.
    """
    firmware = HeldResponse(json_payload=firmware_status_payload(OPNSENSE_RECOMMENDED_FIRMWARE))
    client, _router = make_router_client(
        make_client, {("GET", _FIRMWARE_STATUS): firmware, ("GET", _SERVICE_SEARCH): 503}
    )

    async with asyncio.timeout(3), asyncio.TaskGroup() as tasks:
        validation = tasks.create_task(client.validate(require_device_id=False))
        await firmware.entered.wait()
        try:
            assert client.toggle_throwing_errors(True) is True
            await _assert_service_failure(client, throw_errors=True)
            if cancel:
                validation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await validation
        finally:
            firmware.release.set()
        if not cancel:
            await validation

    await _assert_service_failure(client, throw_errors=True)


@pytest.mark.parametrize("cancel_first", [False, True], ids=["completion", "cancellation"])
async def test_overlapping_validations_keep_their_error_policy(
    make_client: MakeClientFactory, caplog: pytest.LogCaptureFixture, *, cancel_first: bool
) -> None:
    """One validation can exit while a second still handles its failure strictly.

    Args:
        make_client (MakeClientFactory): Factory for clients with real queue workers.
        caplog (pytest.LogCaptureFixture): Captures duplicate failure reports.
        cancel_first (bool): Whether the first validation is cancelled.
    """
    first = HeldResponse(json_payload=firmware_status_payload(OPNSENSE_RECOMMENDED_FIRMWARE))
    second = HeldResponse(status=401, reason="Unauthorized", ok=False)
    client, router = make_router_client(
        make_client, {("GET", _FIRMWARE_STATUS): first, ("GET", _SERVICE_SEARCH): 503}
    )

    async def expect_invalid_auth() -> None:
        """Check that validation preserves the Router's mapped authentication error."""
        with pytest.raises(OPNsenseInvalidAuth):
            await client.validate()

    async with asyncio.timeout(3), asyncio.TaskGroup() as tasks:
        validation = tasks.create_task(client.validate(require_device_id=False))
        await first.entered.wait()
        router.routes[("GET", _FIRMWARE_STATUS)] = second
        failing_validation = tasks.create_task(expect_invalid_auth())
        await second.entered.wait()
        try:
            if cancel_first:
                validation.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await validation
            first.release.set()
            if not cancel_first:
                await validation
        finally:
            second.release.set()
        await failing_validation

    assert router.count("GET", _FIRMWARE_STATUS) == 2
    [failure] = [
        record.getMessage() for record in caplog.records if record.name.startswith("aiopnsense")
    ]
    assert _FIRMWARE_STATUS in failure
    assert "401" in failure
    await _assert_service_failure(client, throw_errors=False)


@pytest.mark.parametrize("throw_errors", [False, True], ids=["default", "strict"])
async def test_queued_and_direct_failures_during_validation(
    make_client: MakeClientFactory, *, throw_errors: bool
) -> None:
    """A queued plain request and a direct status probe keep their failure behavior.

    Args:
        make_client (MakeClientFactory): Factory for clients with real queue workers.
        throw_errors (bool): Configured error mode for ordinary calls.
    """
    legacy_firmware = "25.1"
    firmware = HeldResponse(json_payload=firmware_status_payload(legacy_firmware))
    client, router = make_router_client(
        make_client,
        {("GET", _UNBOUND_SETTINGS): 503, ("GET", _TRAFFIC_PROBE): 503},
        firmware=legacy_firmware,
        throw_errors=throw_errors,
    )
    assert await client.get_host_firmware_version() == legacy_firmware
    router.routes[("GET", _FIRMWARE_STATUS)] = firmware

    async with asyncio.timeout(3), asyncio.TaskGroup() as tasks:
        validation = tasks.create_task(client.validate(require_device_id=False))
        await firmware.entered.wait()
        try:
            if throw_errors:
                with pytest.raises(OPNsenseConnectionError):
                    await client.get_unbound_blocklist()
                with pytest.raises(OPNsenseConnectionError):
                    _ = [sample async for sample in client.stream_interface_traffic()]
            else:
                assert await client.get_unbound_blocklist() == {
                    "legacy": {
                        "enabled": "",
                        "safesearch": "",
                        "nxdomain": "",
                        "address": "",
                        "type": "",
                        "lists": "",
                        "whitelists": "",
                        "blocklists": "",
                        "wildcards": "",
                    }
                }
                assert [sample async for sample in client.stream_interface_traffic()] == []
        finally:
            firmware.release.set()
        await validation

    assert router.count("GET", _FIRMWARE_STATUS) == 2
    assert router.count("GET", _UNBOUND_SETTINGS) == 1
    assert router.count("GET", _TRAFFIC_PROBE) == 1


async def test_validation_requires_device_id_unless_skipped(make_client: MakeClientFactory) -> None:
    """Validate the default Device ID requirement and the firmware cache.

    Args:
        make_client (MakeClientFactory): Factory for clients with real queue workers.
    """
    client, router = make_router_client(
        make_client,
        {("GET", _DEVICE_ID): []},
        firmware=OPNSENSE_RECOMMENDED_FIRMWARE,
    )

    await client.validate(require_device_id=False)
    assert router.calls() == [("GET", _FIRMWARE_STATUS)]

    with pytest.raises(OPNsenseMissingDeviceUniqueID):
        await client.validate()
    router.routes[("GET", _DEVICE_ID)] = [{"is_physical": True, "macaddr_hw": "02:00:00:00:00:01"}]
    await client.validate()
    assert router.calls() == [("GET", _FIRMWARE_STATUS), ("GET", _DEVICE_ID), ("GET", _DEVICE_ID)]
