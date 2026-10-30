"""Tests for `aiopnsense.firmware` behaviors."""

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

import aiopnsense
from aiopnsense.exceptions import (
    OPNsenseConnectionError,
    OPNsenseError,
    OPNsenseInvalidArgument,
    OPNsenseInvalidAuth,
    OPNsensePrivilegeMissing,
)
import aiopnsense.firmware as firmware_module
from tests.conftest import FakeResponse, make_mock_session_client


@pytest.mark.asyncio
async def test_get_host_firmware_version_and_fallback(
    make_client: Callable[..., Any],
) -> None:
    """Firmware version lookup should prefer semver and fall back to product series.

    Args:
        make_client (Callable[..., Any]): Factory used to create primary and fallback firmware clients.
    """
    client, session = make_mock_session_client(make_client)

    client._is_get_endpoint_available = AsyncMock(return_value=True)
    client._safe_dict_get = AsyncMock(return_value={"product": {"product_version": "25.8.0"}})
    firmware = await client.get_host_firmware_version()
    assert firmware == "25.8.0"
    client._is_get_endpoint_available.assert_awaited_once_with("/api/core/firmware/status")
    await client.async_close()

    fallback_client = make_client(session=session)
    try:
        fallback_client._is_get_endpoint_available = AsyncMock(return_value=True)
        fallback_client._safe_dict_get = AsyncMock(
            return_value={"product": {"product_version": "weird", "product_series": "seriesX"}}
        )
        fallback = await fallback_client.get_host_firmware_version()
        assert fallback == "seriesX"
        fallback_client._is_get_endpoint_available.assert_awaited_once_with(
            "/api/core/firmware/status"
        )
    finally:
        await fallback_client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("product_version", "product_latest", "product_check", "last_check_age", "status_msg"),
    [
        pytest.param("1.0", "2.0", {}, None, None, id="incomplete-check-details"),
        pytest.param(
            "1.0.0",
            "1.0.0",
            {"status": "ok"},
            timedelta(days=2),
            None,
            id="stale-check",
        ),
        pytest.param(
            "26.1.2",
            "26.1.3_1",
            {"status": "ok"},
            timedelta(0),
            "There are no updates available on the selected mirror.",
            id="suffixed-latest-version",
        ),
        pytest.param(
            "25.7_9",
            "25.7.1",
            {"status": "ok"},
            timedelta(0),
            "There are no updates available on the selected mirror.",
            id="underscore-revision-version",
        ),
        pytest.param(
            "26.1.11_1",
            "26.1.11_2",
            {"status": "ok"},
            timedelta(0),
            "There are no updates available on the selected mirror.",
            id="hotfix-only-latest-revision",
        ),
        pytest.param(
            "26.7.6",
            "26.7.6_3",
            {"status": "ok"},
            timedelta(0),
            "There are no updates available on the selected mirror.",
            id="first-hotfix-available",
        ),
        pytest.param(
            "26.7.6.0",
            "26.7.6_3",
            {"status": "ok"},
            timedelta(0),
            "There are no updates available on the selected mirror.",
            id="hotfix-available-with-zero-component-base",
        ),
        pytest.param(
            "26.7.6_9",
            "26.7.6_10",
            {"status": "ok"},
            timedelta(0),
            "There are no updates available on the selected mirror.",
            id="numeric-hotfix-ordering",
        ),
        pytest.param(
            "26.1.11_1",
            "26.1.11_bad",
            {"status": "ok"},
            timedelta(0),
            "There are no updates available on the selected mirror.",
            id="incomparable-version",
        ),
    ],
)
async def test_get_firmware_update_info_triggers_check_when_refresh_is_needed(
    make_client: Callable[..., Any],
    fake_response_factory: Callable[..., Any],
    product_version: str,
    product_latest: str,
    product_check: dict[str, str],
    last_check_age: timedelta | None,
    status_msg: str | None,
) -> None:
    """Incomplete, stale, newer, and incomparable status should trigger refresh.

    Args:
        make_client (Callable[..., Any]): Factory used to create the firmware client.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
        product_version (str): Installed firmware version in the status response.
        product_latest (str): Advertised latest firmware version in the status response.
        product_check (dict[str, str]): Firmware check details in the response.
        last_check_age (timedelta | None): Age of the last check, or ``None`` if missing.
        status_msg (str | None): Optional status message from the firmware response.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        status: dict[str, object] = {
            "product": {
                "product_version": product_version,
                "product_latest": product_latest,
                "product_check": product_check,
            }
        }
        if status_msg is not None:
            status["status_msg"] = status_msg
        if last_check_age is not None:
            status["last_check"] = (datetime.now(UTC) - last_check_age).isoformat()
        client._safe_dict_get = AsyncMock(
            side_effect=lambda path: {"status": "ready"} if path.endswith("/running") else status
        )
        client._get_opnsense_timezone = AsyncMock(return_value=UTC)
        session.post.return_value = fake_response_factory(json_payload={})

        result = await client.get_firmware_update_info()

        assert result == status
        assert session.post.call_count == 1
        assert session.post.call_args.args[0] == "http://localhost/api/core/firmware/check"
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("product_version", "product_latest"),
    [
        pytest.param("26.1.1", "26.1.1", id="equal-base-versions"),
        pytest.param("26.7.6_3", "26.7.6", id="installed-hotfix-ahead-of-base"),
        pytest.param("26.7.6_3", "26.7.6.0", id="installed-hotfix-ahead-of-zero-component-base"),
        pytest.param("26.7.6_3", "26.7.6_3", id="equal-hotfix-versions"),
        pytest.param("26.7.6_10", "26.7.6_9", id="installed-hotfix-ahead-of-revision"),
        pytest.param("26.7.7", "26.7.6_9", id="installed-base-ahead-of-hotfix"),
    ],
)
async def test_get_firmware_update_info_does_not_trigger_check_for_recent_healthy_status(
    make_client: Callable[..., Any],
    fake_response_factory: Callable[..., Any],
    product_version: str,
    product_latest: str,
) -> None:
    """Healthy firmware status should not trigger checks across repeated polls.

    Args:
        make_client (Callable[..., Any]): Factory used to configure a healthy recent firmware status.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
        product_version (str): Installed firmware version in the status response.
        product_latest (str): Equal or older advertised firmware version.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        status = {
            "product": {
                "product_version": product_version,
                "product_latest": product_latest,
                "product_check": {"status": "ok"},
            },
            "status_msg": "There are no updates available on the selected mirror.",
            "last_check": datetime.now(UTC).isoformat(),
        }
        client._safe_dict_get = AsyncMock(
            side_effect=lambda path: {"status": "ready"} if path.endswith("/running") else status
        )
        client._get_opnsense_timezone = AsyncMock(return_value=UTC)
        session.post.return_value = fake_response_factory(json_payload={})

        for _ in range(3):
            result = await client.get_firmware_update_info()
            assert result == status
        assert session.post.call_count == 0
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("upgrade_type", "expected_path"),
    [("update", "/api/core/firmware/update"), ("upgrade", "/api/core/firmware/upgrade")],
)
async def test_upgrade_firmware_calls_expected_endpoint(
    make_client: Callable[..., Any],
    fake_response_factory: Callable[..., Any],
    upgrade_type: str,
    expected_path: str,
) -> None:
    """Deprecated upgrade calls warn and still dispatch to the matching endpoint.

    Args:
        make_client (Callable[..., Any]): Factory used to create the upgrade client.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
        upgrade_type (str): Supported upgrade action supplied to the client.
        expected_path (str): Firmware API path expected for the action.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._firmware_version = "26.1.1"
        session.post.return_value = fake_response_factory(json_payload={"status": "ok"})

        legacy_upgrade = client.upgrade_firmware  # type: ignore[deprecated]
        assert "install_firmware(target_version=...)" in legacy_upgrade.__deprecated__  # type: ignore[attr-defined]
        with pytest.warns(DeprecationWarning, match=r"Use install_firmware\(target_version="):
            result = await legacy_upgrade(upgrade_type)

        assert result == {"status": "ok"}
        assert session.post.call_count == 1
        assert session.post.call_args.args[0] == f"http://localhost{expected_path}"
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(return_value={"product": {"product_version": "26.1.2"}})
        assert await client.get_host_firmware_version() == "26.1.2"
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_upgrade_firmware_rejects_unknown_type(
    make_client: Callable[..., Any], fake_response_factory: Callable[..., Any]
) -> None:
    """Unknown firmware upgrade types should return `None` without calling the API.

    Args:
        make_client (Callable[..., Any]): Factory used to create the client rejecting the action.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
    """
    client, session = make_mock_session_client(make_client)
    try:
        session.post.return_value = fake_response_factory(json_payload={"status": "ok"})

        result = await client.upgrade_firmware("invalid")  # type: ignore[deprecated]

        assert result is None
        assert session.post.call_count == 0
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_install_firmware_waits_for_legacy_upgrade_response(
    install_client: Callable[..., Any], fake_response_factory: Callable[..., Any]
) -> None:
    """Install preflight waits until a legacy action response has completed.

    Args:
        install_client (Callable[..., Any]): Firmware lifecycle client factory.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
    """
    client, session = install_client([(_install_progress("done", "***DONE***"), "ready", "26.7.8")])
    legacy_started = asyncio.Event()
    release_legacy = asyncio.Event()

    async def legacy_json(content_type: Any = None) -> dict[str, str]:
        """Hold the legacy HTTP response until the install is waiting.

        Args:
            content_type (Any): Unused fake response option.

        Returns:
            dict[str, str]: Successful legacy action response.
        """
        legacy_started.set()
        await release_legacy.wait()
        return {"status": "ok"}

    legacy_response = fake_response_factory(json_payload={"status": "ok"})
    legacy_response.json = legacy_json
    session.post.side_effect = [
        legacy_response,
        fake_response_factory(json_payload={"status": "ok"}),
    ]
    legacy_task: asyncio.Task[Any] | None = None
    install_task: asyncio.Task[Any] | None = None
    try:
        legacy_task = asyncio.create_task(client.upgrade_firmware())
        await legacy_started.wait()
        install_task = asyncio.create_task(
            client.install_firmware(target_version="26.7.8", poll_interval=0.001)
        )
        await asyncio.sleep(0)
        assert not install_task.done()
        assert session.get.call_count == 0

        release_legacy.set()
        assert await legacy_task == {"status": "ok"}
        await install_task
        assert session.post.call_count == 2
    finally:
        release_legacy.set()
        tasks = [task for task in (legacy_task, install_task) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("throw_errors", [False, True])
async def test_firmware_actions_are_rejected_during_install(
    install_client: Callable[..., Any],
    fake_response_factory: Callable[..., Any],
    throw_errors: bool,
) -> None:
    """Legacy actions and automatic checks do not dispatch during an install.

    Args:
        install_client (Callable[..., Any]): Firmware lifecycle client factory.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
        throw_errors (bool): Whether public methods propagate OPNsense errors.
    """
    client, session = install_client([], throw_errors=throw_errors)
    observing = asyncio.Event()
    release_progress = asyncio.Event()

    async def held_progress_json(content_type: Any = None) -> dict[str, str]:
        """Hold a real firmware progress response while the install is active.

        Args:
            content_type (Any): Unused fake response option.

        Returns:
            dict[str, str]: Completed firmware progress response.
        """
        observing.set()
        await release_progress.wait()
        return _install_progress("done", "***DONE***")

    progress_response = fake_response_factory(json_payload={})
    progress_response.json = held_progress_json
    session.get.side_effect = [
        fake_response_factory(json_payload={"status": "ready"}),
        fake_response_factory(json_payload={"status": "done", "log": "old check"}),
        progress_response,
        fake_response_factory(json_payload={"status": "ready"}),
        fake_response_factory(json_payload={"product": {"product_version": "26.7.8"}}),
    ]
    install_task = asyncio.create_task(client.install_firmware(target_version="26.7.8"))
    try:
        await observing.wait()

        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(
            side_effect=lambda path: (
                {"status": "ready"} if path.endswith("/running") else {"status": "error"}
            )
        )
        await client.get_firmware_update_info()

        if throw_errors:
            with pytest.raises(OPNsenseError, match="already active"):
                await client.upgrade_firmware("upgrade")
        else:
            assert await client.upgrade_firmware("upgrade") is None
        assert session.post.call_count == 1
    finally:
        release_progress.set()
        await install_task
        await client.async_close()


@pytest.mark.asyncio
async def test_cancelled_legacy_upgrade_does_not_leave_deferred_post(
    make_client: Callable[..., Any], fake_response_factory: Callable[..., Any]
) -> None:
    """Cancelling the public action after dispatch does not queue another POST.

    Args:
        make_client (Callable[..., Any]): Public client factory.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
    """
    client, session = make_mock_session_client(make_client)
    response_started = asyncio.Event()
    hold_response = asyncio.Event()

    async def blocked_json(content_type: Any = None) -> dict[str, str]:
        """Hold the immediate POST response until its caller is cancelled.

        Args:
            content_type (Any): Unused fake response option.

        Returns:
            dict[str, str]: Successful action response if not cancelled.
        """
        response_started.set()
        await hold_response.wait()
        return {"status": "ok"}

    session.post.return_value = fake_response_factory(json_payload={"status": "ok"})
    session.post.return_value.json = blocked_json
    task = asyncio.create_task(client.upgrade_firmware())  # type: ignore[deprecated]
    try:
        await response_started.wait()
        assert session.post.call_count == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        hold_response.set()
        await asyncio.sleep(0)
        assert session.post.call_count == 1
    finally:
        hold_response.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await client.async_close()


@pytest.mark.asyncio
async def test_upgrade_status_and_changelog(make_client: Callable[..., Any]) -> None:
    """Upgrade status and changelog helpers should proxy the expected endpoints.

    Args:
        make_client (Callable[..., Any]): Factory used to configure the status and changelog client.
    """
    client = make_client()
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(return_value={"status": "running"})
        client._safe_dict_post = AsyncMock(return_value={"changelog": "..."})

        status = await client.upgrade_status()
        changelog = await client.firmware_changelog("26.1.1")

        assert status == {"status": "running"}
        assert changelog == {"changelog": "..."}
        client._safe_dict_get.assert_awaited_once_with("/api/core/firmware/upgradestatus")
        client._safe_dict_post.assert_awaited_once_with("/api/core/firmware/changelog/26.1.1")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_firmware_helpers_return_defaults_when_endpoints_unavailable(
    make_client: Callable[..., Any],
) -> None:
    """Firmware helpers should fail closed when status endpoints are unavailable.

    Args:
        make_client (Callable[..., Any]): Fixture factory used to create client instances.

    Returns:
        None: This test validates default-return behavior for unavailable firmware endpoints.
    """
    client = make_client()
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=False)
        client._safe_dict_get = AsyncMock()

        await client._store_host_firmware_version()
        host_version = await client.get_host_firmware_version()
        update_info = await client.get_firmware_update_info()
        upgrade_status = await client.upgrade_status()

        assert client._firmware_version is None
        assert host_version is None
        assert update_info == {}
        assert upgrade_status == {}
        client._safe_dict_get.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.fixture
def install_client(
    make_client: Callable[..., Any], fake_response_factory: Callable[..., Any]
) -> Callable[..., tuple[Any, Any]]:
    """Build a public client with ordered HTTP firmware observations.

    Args:
        make_client (Callable[..., Any]): Public client factory.
        fake_response_factory (Callable[..., Any]): HTTP response factory.

    Returns:
        Callable[..., tuple[Any, Any]]: Client and mocked HTTP session factory.
    """

    def make(
        observations: list[Any],
        *,
        baseline: str = "old check",
        dispatch: Any = None,
        throw_errors: bool = False,
        preflight: list[str] | None = None,
    ) -> tuple[Any, Any]:
        """Configure transport responses for a complete firmware lifecycle.

        Args:
            observations (list[Any]): Progress/lock/version tuples or outages.
            baseline (str): Preflight firmware log.
            dispatch (Any): POST payload or transport exception.
            throw_errors (bool): Client-wide error propagation setting.
            preflight (list[str] | None): Server lock states before dispatch.

        Returns:
            tuple[Any, Any]: Client and mocked session.
        """
        client, session = make_mock_session_client(make_client)
        client.toggle_throwing_errors(throw_errors)
        payloads: list[Any] = [{"status": state} for state in (preflight or ["ready"])]
        payloads.append({"status": "done", "log": baseline})
        for observation in observations:
            if isinstance(observation, BaseException):
                payloads.append(observation)
            else:
                progress, lock, version = observation
                payloads.extend(
                    [progress, {"status": lock}, {"product": {"product_version": version}}]
                )
        session.get.side_effect = [
            payload
            if isinstance(payload, BaseException)
            else fake_response_factory(json_payload=payload)
            for payload in payloads
        ]
        session.post.side_effect = dispatch if isinstance(dispatch, BaseException) else None
        session.post.return_value = fake_response_factory(
            json_payload={"status": "ok"} if dispatch is None else dispatch
        )
        return client, session

    return make


def _install_progress(state: str, tail: str = "", *, action: str = "UPDATE") -> dict[str, str]:
    """Build a progress response with a fresh timestamped action header.

    Args:
        state (str): Server progress state.
        tail (str): Output after the request header.
        action (str): Firmware action written by the daemon.

    Returns:
        dict[str, str]: Firmware progress response.
    """
    return {
        "status": state,
        "log": f"***GOT REQUEST TO {action}***\nCurrently running OPNsense at NEW DATE\n{tail}",
    }


async def _run_install_until_final_evidence(
    session: MagicMock,
    target_version: str,
    install: Coroutine[Any, Any, None],
) -> None:
    """Keep final firmware evidence pending until the install task reaches it.

    Args:
        session (MagicMock): Mock HTTP session supplying the final version response.
        target_version (str): Firmware version returned by the held response.
        install (Coroutine[Any, Any, None]): Installation operation to run.
    """
    final_evidence_observed = asyncio.Event()
    release_final_evidence = asyncio.Event()

    async def held_final_evidence(
        content_type: object | None = None,
    ) -> dict[str, dict[str, str]]:
        """Hold the installed-version response until released by the test helper.

        Args:
            content_type (object | None): Unused fake response option.

        Returns:
            dict[str, dict[str, str]]: Installed firmware version response.
        """
        final_evidence_observed.set()
        await release_final_evidence.wait()
        return {"product": {"product_version": target_version}}

    responses = list(session.get.side_effect)
    final_response: FakeResponse = responses[-1]
    final_response.json = held_final_evidence
    session.get.side_effect = responses
    install_task = asyncio.create_task(install)
    evidence_wait = asyncio.create_task(final_evidence_observed.wait())
    try:
        completed, _ = await asyncio.wait(
            {install_task, evidence_wait}, timeout=1, return_when=asyncio.FIRST_COMPLETED
        )
        assert evidence_wait in completed
        assert not install_task.done()

        release_final_evidence.set()
        await install_task
    finally:
        release_final_evidence.set()
        for task in (install_task, evidence_wait):
            if not task.done():
                task.cancel()
        await asyncio.gather(install_task, evidence_wait, return_exceptions=True)


async def _assert_install_can_start_after_cleanup(
    client: aiopnsense.OPNsenseClient,
    session: MagicMock,
    fake_response_factory: Callable[..., FakeResponse],
    previous_dispatches: int,
) -> None:
    """Prove a completed or cancelled install releases public action ownership.

    Args:
        client (aiopnsense.OPNsenseClient): Client whose previous install ended or was cancelled.
        session (MagicMock): Mock session receiving lifecycle requests.
        fake_response_factory (Callable[..., FakeResponse]): HTTP response factory.
        previous_dispatches (int): Dispatch count before this recovery probe.
    """
    session.get.side_effect = [
        fake_response_factory(json_payload={"status": "ready"}),
        fake_response_factory(json_payload={"status": "done", "log": "old check"}),
        fake_response_factory(json_payload=_install_progress("done", "***DONE***")),
        fake_response_factory(json_payload={"status": "ready"}),
        fake_response_factory(json_payload={"product": {"product_version": "26.7.8"}}),
    ]
    session.post.side_effect = None
    session.post.return_value = fake_response_factory(json_payload={"status": "ok"})

    await client.install_firmware(target_version="26.7.8", poll_interval=0.001)

    assert session.post.call_count == previous_dispatches + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("throw_errors", [False, True])
@pytest.mark.parametrize("action", ["update", "upgrade"])
async def test_install_firmware_waits_for_terminal_idle(
    install_client: Callable[..., Any], throw_errors: bool, action: str
) -> None:
    """Early version changes and DONE while busy cannot finish installation.

    Args:
        install_client (Callable[..., Any]): Lifecycle transport factory.
        throw_errors (bool): Shared client error propagation mode.
        action (str): Requested firmware action.
    """
    progress = _install_progress("done", "***DONE***", action=action.upper())
    target_version = "26.7.8"
    client, session = install_client(
        [
            (_install_progress("running", action=action.upper()), "busy", target_version),
            (progress, "busy", target_version),
            (progress, "ready", target_version),
        ],
        throw_errors=throw_errors,
        preflight=["busy", "ready"],
    )
    await _run_install_until_final_evidence(
        session,
        target_version,
        client.install_firmware(action, target_version=target_version, poll_interval=0.001),
    )
    assert await client.get_host_firmware_version() == target_version
    session.post.assert_called_once()
    assert session.post.call_args.args[0] == f"http://localhost/api/core/firmware/{action}"


@pytest.mark.asyncio
async def test_install_firmware_waits_for_complete_action_header(
    install_client: Callable[..., Any],
) -> None:
    """A marker-only first poll remains pending until its header is complete.

    Args:
        install_client (Callable[..., Any]): Lifecycle transport factory.
    """
    partial = {"status": "running", "log": "***GOT REQUEST TO UPDATE***"}
    complete = _install_progress("done", "***DONE***")
    target_version = "26.7.8"
    client, session = install_client(
        [
            (partial, "busy", "26.7.7"),
            (complete, "ready", target_version),
        ]
    )

    await client.install_firmware(target_version=target_version, poll_interval=0.001)

    assert await client.get_host_firmware_version() == target_version
    session.post.assert_called_once()


@pytest.mark.parametrize(
    "outage",
    [
        pytest.param(aiohttp.ClientConnectionError("server rebooted"), id="connection-error"),
        pytest.param(TimeoutError("server rebooted"), id="timeout-error"),
    ],
)
@pytest.mark.parametrize("throw_errors", [False, True])
@pytest.mark.asyncio
async def test_install_firmware_reboot_recovers_empty_log(
    install_client: Callable[..., Any],
    throw_errors: bool,
    outage: aiohttp.ClientConnectionError | TimeoutError,
) -> None:
    """Server reboot requires outage and recovery despite postboot empty ERROR.

    Args:
        install_client (Callable[..., Any]): Lifecycle transport factory.
        throw_errors (bool): Shared client error propagation mode.
        outage (aiohttp.ClientConnectionError | TimeoutError): Simulated reboot outage.
    """

    reboot = _install_progress("reboot", "***REBOOT***")
    target_version = "26.7.8"
    client, session = install_client(
        [
            (reboot, "busy", target_version),
            (reboot, "ready", target_version),  # pre-reboot five-second window
            outage,
            ({"status": "error", "log": ""}, "busy", target_version),
            ({"status": "error", "log": ""}, "ready", target_version),
        ],
        throw_errors=throw_errors,
    )
    await _run_install_until_final_evidence(
        session,
        target_version,
        client.install_firmware(target_version=target_version, poll_interval=0.001),
    )
    session.post.assert_called_once()
    assert session.post.call_args.args[0] == "http://localhost/api/core/firmware/update"


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch", [None, {"status": "error"}])
async def test_install_firmware_dispatch_rejected_or_lost(
    install_client: Callable[..., Any], dispatch: dict[str, str] | None
) -> None:
    """Lost responses are observed once; explicit rejection fails immediately.

    Args:
        install_client (Callable[..., Any]): Lifecycle transport factory.
        dispatch (dict[str, str] | None): Rejection payload or lost response.
    """

    client, session = install_client(
        [(_install_progress("done", "***DONE***"), "ready", "26.7.8")],
        dispatch=dispatch or aiohttp.ClientConnectionError("lost acknowledgement"),
    )
    if dispatch is None:
        await client.install_firmware(target_version="26.7.8", poll_interval=0.001)
    else:
        with pytest.raises(OPNsenseError, match="rejected"):
            await client.install_firmware(target_version="26.7.8", poll_interval=0.001)
    session.post.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("progress", "version", "error"),
    [
        (
            _install_progress("done", "Partial update failure detected!\n***DONE***"),
            "26.7.8",
            "partial",
        ),
        (
            _install_progress("done", "No further actions will be taken\n***DONE***"),
            "26.7.8",
            "partial",
        ),
        (_install_progress("done", "***DONE***", action="CHECK"), "26.7.8", "different"),
        (_install_progress("error", "failed"), "26.7.8", "reported"),
        (_install_progress("unknown", ""), "26.7.8", "reported"),
        (_install_progress("done", "***DONE***"), "26.7.7", "target"),
        (_install_progress("done", "***DONE***"), "26.7.8_1", None),
        (_install_progress("done", "***DONE***"), "26.7.8", None),
    ],
)
async def test_install_firmware_terminal_contract(
    install_client: Callable[..., Any],
    fake_response_factory: Callable[..., Any],
    progress: dict[str, str],
    version: str,
    error: str | None,
) -> None:
    """Terminal logs and actual installed versions jointly establish success.

    Args:
        install_client (Callable[..., Any]): Lifecycle transport factory.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
        progress (dict[str, str]): Terminal log response.
        version (str): Installed firmware version.
        error (str | None): Expected failure text, if any.
    """

    client, session = install_client([(progress, "ready", version)])
    if error:
        with pytest.raises(OPNsenseError, match=error):
            await client.install_firmware(target_version="26.7.8", poll_interval=0.001)
    else:
        await client.install_firmware(target_version="26.7.8", poll_interval=0.001)
    session.post.assert_called_once()
    await _assert_install_can_start_after_cleanup(client, session, fake_response_factory, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("baseline", ["old check", _install_progress("done", "***DONE***")["log"]])
async def test_install_firmware_stale_log_times_out_and_cleans_up(
    install_client: Callable[..., Any],
    fake_response_factory: Callable[..., Any],
    baseline: str,
) -> None:
    """Stale progress cannot confirm a new installation even at target version.

    Args:
        install_client (Callable[..., Any]): Lifecycle transport factory.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
        baseline (str): Previous daemon log.
    """

    client, session = install_client(
        [({"status": "done", "log": baseline}, "ready", "26.7.8")], baseline=baseline
    )
    with pytest.raises(OPNsenseError, match="deadline"):
        await client.install_firmware(target_version="26.7.8", timeout=0.01, poll_interval=0.1)
    session.post.assert_called_once()
    await _assert_install_can_start_after_cleanup(client, session, fake_response_factory, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"type": "reboot"},
        {"target_version": ""},
        {"target_version": None},
        {"timeout": float("nan")},
        {"timeout": float("inf")},
        {"timeout": 0},
        {"poll_interval": -1},
        {"poll_interval": True},
        {"poll_interval": "5"},
    ],
)
async def test_install_firmware_rejects_invalid_arguments(
    install_client: Callable[..., Any], kwargs: dict[str, Any]
) -> None:
    """Invalid arguments must fail before a destructive HTTP request.

    Args:
        install_client (Callable[..., Any]): Lifecycle transport factory.
        kwargs (dict[str, Any]): Invalid public argument.
    """

    client, session = install_client([])
    with pytest.raises(OPNsenseInvalidArgument):
        await client.install_firmware(**({"target_version": "26.7.8"} | kwargs))
    session.get.assert_not_called()
    session.post.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["preflight", "observation"])
async def test_install_firmware_concurrency_and_cancellation(
    install_client: Callable[..., Any],
    fake_response_factory: Callable[..., Any],
    phase: str,
) -> None:
    """Cancellation releases local ownership; concurrent installs never queue.

    Args:
        install_client (Callable[..., Any]): Lifecycle transport factory.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
        phase (str): Cancellation before or after firmware dispatch.
    """

    client, session = install_client([])
    entered = asyncio.Event()
    release = asyncio.Event()

    async def held_json(content_type: object | None = None) -> dict[str, str]:
        """Hold a public firmware response until the owning task is cancelled.

        Args:
            content_type (object | None): Unused response decoding option.

        Returns:
            dict[str, str]: Preflight-ready or in-progress firmware response.
        """
        entered.set()
        await release.wait()
        if phase == "preflight":
            return {"status": "ready"}
        return _install_progress("running")

    held_response = fake_response_factory(json_payload={})
    held_response.json = held_json
    if phase == "preflight":
        session.get.side_effect = [held_response]
    else:
        session.get.side_effect = [
            fake_response_factory(json_payload={"status": "ready"}),
            fake_response_factory(json_payload={"status": "done", "log": "old check"}),
            held_response,
        ]
    task = asyncio.create_task(client.install_firmware(target_version="26.7.8"))
    expected_dispatches = 0 if phase == "preflight" else 1
    try:
        await entered.wait()
        assert session.post.call_count == expected_dispatches
        with pytest.raises(OPNsenseError, match="already active"):
            await client.install_firmware(target_version="26.7.8")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert session.post.call_count == expected_dispatches
        await _assert_install_can_start_after_cleanup(
            client, session, fake_response_factory, expected_dispatches
        )
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "check,server",
    [
        pytest.param(False, "ready", id="read-only"),
        pytest.param(True, "busy", id="server-busy"),
        pytest.param(True, "unknown", id="server-unknown"),
    ],
)
async def test_firmware_automatic_check_is_suppressed(
    make_client: Callable[..., Any],
    fake_response_factory: Callable[..., Any],
    check: bool,
    server: str,
) -> None:
    """Read-only checks and non-ready server states never dispatch automatic checks.

    Args:
        make_client (Callable[..., Any]): Public client factory.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
        check (bool): Whether automatic checking is permitted.
        server (str): Server lock state.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        client._safe_dict_get = AsyncMock(
            side_effect=lambda path: {"status": server} if path.endswith("/running") else {}
        )
        session.post.return_value = fake_response_factory(json_payload={"status": "ok"})
        await client.get_firmware_update_info(check_for_updates=check)
        assert session.post.call_count == 0
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    "progress",
    [
        pytest.param({"status": "done", "log": "old check"}, id="unchanged-baseline"),
        pytest.param(
            {"status": "running", "log": "***GOT REQUEST TO UPDATE***"},
            id="incomplete-matching-header",
        ),
    ],
)
@pytest.mark.asyncio
async def test_install_firmware_start_deadline(
    install_client: Callable[..., Any], monkeypatch: pytest.MonkeyPatch, progress: dict[str, str]
) -> None:
    """Unconfirmed dispatch gets a short bounded wait independent of overall timeout.

    Args:
        install_client (Callable[..., Any]): Lifecycle transport factory.
        monkeypatch (pytest.MonkeyPatch): Start-deadline override fixture.
        progress (dict[str, str]): Progress response that cannot confirm startup.
    """
    monkeypatch.setattr(firmware_module, "FIRMWARE_START_TIMEOUT_SECONDS", 0.005)
    client, session = install_client([(progress, "ready", "26.7.8")])
    with pytest.raises(OPNsenseError, match="did not start"):
        await client.install_firmware(target_version="26.7.8", poll_interval=100, timeout=1)
    session.post.assert_called_once()


@pytest.mark.asyncio
async def test_install_firmware_reboot_marker_survives_following_outage(
    install_client: Callable[..., Any], fake_response_factory: Callable[..., Any]
) -> None:
    """An immediate disconnect after REBOOT and a postboot CHECK are accepted.

    Args:
        install_client (Callable[..., Any]): Lifecycle transport factory.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
    """
    client, session = install_client([])
    payloads = [
        {"status": "ready"},
        {"status": "done", "log": "old check"},
        _install_progress("reboot", "***REBOOT***"),
        aiohttp.ClientConnectionError("reboot during running read"),
        _install_progress("done", "***DONE***", action="CHECK"),
        {"status": "ready"},
        {"product": {"product_version": "26.7.8"}},
    ]
    session.get.side_effect = [
        payload
        if isinstance(payload, BaseException)
        else fake_response_factory(json_payload=payload)
        for payload in payloads
    ]
    await client.install_firmware(target_version="26.7.8", poll_interval=0.001)
    session.post.assert_called_once()


@pytest.mark.asyncio
async def test_install_firmware_rejects_replaced_same_action_log(
    install_client: Callable[..., Any],
) -> None:
    """A second UPDATE run cannot supply completion for the first installation.

    Args:
        install_client (Callable[..., Any]): Lifecycle transport factory.
    """
    replaced = _install_progress("done", "***DONE***")
    replaced["log"] = replaced["log"].replace("NEW DATE", "LATER DATE")
    client, _ = install_client(
        [
            (_install_progress("running"), "busy", "26.7.7"),
            (replaced, "ready", "26.7.8"),
        ]
    )
    with pytest.raises(OPNsenseError, match="replaced"):
        await client.install_firmware(target_version="26.7.8", poll_interval=0.001)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("http_status", "error_type"),
    [
        pytest.param(401, OPNsenseInvalidAuth, id="unauthorized"),
        pytest.param(403, OPNsensePrivilegeMissing, id="forbidden"),
    ],
)
@pytest.mark.parametrize("phase", ["dispatch", "observation"])
async def test_install_firmware_auth_failure_is_fatal(
    install_client: Callable[..., Any],
    fake_response_factory: Callable[..., Any],
    http_status: int,
    error_type: type[OPNsenseConnectionError],
    phase: str,
) -> None:
    """Permission failures must not be treated as a reboot outage or retried.

    Args:
        install_client (Callable[..., Any]): Lifecycle transport factory.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
        http_status (int): Fatal HTTP permission status.
        error_type (type[OPNsenseConnectionError]): Public error corresponding to the HTTP status.
        phase (str): Where permission failure occurs.
    """
    client, session = install_client([])
    failure = fake_response_factory(status=http_status, ok=False)
    if phase == "dispatch":
        session.post.return_value = failure
    else:
        session.get.side_effect = [
            fake_response_factory(json_payload={"status": "ready"}),
            fake_response_factory(json_payload={"status": "done", "log": "old check"}),
            failure,
        ]
    with pytest.raises(error_type) as error:
        await client.install_firmware(target_version="26.7.8", poll_interval=0.001)
    assert error.value.status == http_status
    session.post.assert_called_once()


@pytest.mark.asyncio
async def test_cancelled_firmware_check_does_not_dispatch_later(
    make_client: Callable[..., Any],
    fake_response_factory: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling a check must not leave a mutating request behind queued work.

    Args:
        make_client (Callable[..., Any]): Public client factory.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
        monkeypatch (pytest.MonkeyPatch): Controlled worker transport fixture.
    """
    client, session = make_mock_session_client(make_client)
    client._max_workers = 1
    worker_entered = asyncio.Event()
    release_worker = asyncio.Event()
    finish_check = asyncio.Event()

    async def blocked_read(*args: Any, **kwargs: Any) -> dict[str, Any]:
        """Keep the existing request worker occupied.

        Args:
            args (Any): Unused transport arguments.
            kwargs (Any): Unused transport options.

        Returns:
            dict[str, Any]: Completed unrelated read.
        """
        worker_entered.set()
        await release_worker.wait()
        return {}

    async def check_response(*args: Any, **kwargs: Any) -> dict[str, str]:
        """Hold the check HTTP response until its caller is cancelled.

        Args:
            args (Any): Unused decoding arguments.
            kwargs (Any): Unused decoding options.

        Returns:
            dict[str, str]: Check dispatch acknowledgement.
        """
        await finish_check.wait()
        return {"status": "ok"}

    monkeypatch.setattr(asyncio, "create_task", asyncio.get_running_loop().create_task)
    monkeypatch.setattr(client, "_do_get", blocked_read)
    response = fake_response_factory(json_payload={"status": "ok"})
    response.json = check_response
    session.post.return_value = response
    client._is_get_endpoint_available = AsyncMock(return_value=True)
    client._safe_dict_get = AsyncMock(
        side_effect=lambda path: {"status": "ready"} if path.endswith("/running") else {}
    )
    unrelated = asyncio.create_task(client._get("/unrelated/read"))
    await worker_entered.wait()
    check = asyncio.create_task(client.get_firmware_update_info())
    try:
        # Advance to the first suspended await, either in the queue or HTTP response.
        await asyncio.sleep(0)
        requests_before_cancel = session.post.call_count
        check.cancel()
        with pytest.raises(asyncio.CancelledError):
            await check
        release_worker.set()
        finish_check.set()
        await unrelated
        await client._get("/after-cancel/read")
        assert session.post.call_count == requests_before_cancel
        session.post.return_value = fake_response_factory(json_payload={"status": "ok"})
        dispatches_before_retry = session.post.call_count
        await client.get_firmware_update_info()
        assert session.post.call_count == dispatches_before_retry + 1
    finally:
        release_worker.set()
        finish_check.set()
        await client.async_close()
