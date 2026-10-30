"""Tests for `aiopnsense.firmware` behaviors."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import aiohttp
import pytest

from aiopnsense.exceptions import OPNsenseError, OPNsenseInvalidArgument
import aiopnsense.firmware as firmware_module
from tests.conftest import make_mock_session_client


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
async def test_get_firmware_update_info_triggers_check_when_status_is_incomplete(
    make_client: Callable[..., Any],
) -> None:
    """Missing firmware status details should trigger a background firmware check.

    Args:
        make_client (Callable[..., Any]): Factory used to configure an incomplete firmware-status response.
    """
    client = make_client()
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        status = {
            "product": {"product_version": "1.0", "product_latest": "2.0", "product_check": {}}
        }
        client._safe_dict_get = AsyncMock(
            side_effect=lambda path: {"status": "ready"} if path.endswith("/running") else status
        )
        client._do_post = AsyncMock(return_value={})

        result = await client.get_firmware_update_info()

        assert result == status
        client._do_post.assert_awaited_once_with("/api/core/firmware/check")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_firmware_update_info_triggers_check_when_last_check_is_stale(
    make_client: Callable[..., Any],
) -> None:
    """A stale `last_check` should trigger a firmware refresh.

    Args:
        make_client (Callable[..., Any]): Factory used to configure a stale firmware-status response.
    """
    client, _ = make_mock_session_client(make_client)
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        status = {
            "product": {
                "product_version": "1.0.0",
                "product_latest": "1.0.0",
                "product_check": {"status": "ok"},
            },
            "last_check": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
        }
        client._safe_dict_get = AsyncMock(
            side_effect=lambda path: {"status": "ready"} if path.endswith("/running") else status
        )
        client._get_opnsense_timezone = AsyncMock(return_value=UTC)
        client._do_post = AsyncMock(return_value={})

        await client.get_firmware_update_info()

        client._do_post.assert_awaited_once_with("/api/core/firmware/check")
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
    make_client: Callable[..., Any], product_version: str, product_latest: str
) -> None:
    """Healthy firmware status should not trigger checks across repeated polls.

    Args:
        make_client (Callable[..., Any]): Factory used to configure a healthy recent firmware status.
        product_version (str): Installed firmware version in the status response.
        product_latest (str): Equal or older advertised firmware version.
    """
    client = make_client()
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
        client._do_post = AsyncMock(return_value={})

        for _ in range(3):
            result = await client.get_firmware_update_info()
            assert result == status
        client._do_post.assert_not_awaited()
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("product_version", "product_latest"),
    [
        pytest.param("26.1.2", "26.1.3_1", id="suffixed-latest-version"),
        pytest.param("25.7_9", "25.7.1", id="underscore-revision-version"),
        pytest.param("26.1.11_1", "26.1.11_2", id="hotfix-only-latest-revision"),
        pytest.param("26.7.6", "26.7.6_3", id="first-hotfix-available"),
        pytest.param("26.7.6.0", "26.7.6_3", id="hotfix-available-with-zero-component-base"),
        pytest.param("26.7.6_9", "26.7.6_10", id="numeric-hotfix-ordering"),
    ],
)
async def test_get_firmware_update_info_triggers_check_for_newer_latest_versions(
    make_client: Callable[..., Any], product_version: str, product_latest: str
) -> None:
    """Newer latest firmware revisions should trigger a details refresh.

    Args:
        make_client (Callable[..., Any]): Factory used to configure the version comparison response.
        product_version (str): Installed firmware version in the status response.
        product_latest (str): Newer advertised firmware version in the status response.
    """
    client = make_client()
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
        client._do_post = AsyncMock(return_value={})

        result = await client.get_firmware_update_info()

        assert result == status
        client._do_post.assert_awaited_once_with("/api/core/firmware/check")
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_get_firmware_update_info_triggers_check_when_versions_cannot_compare(
    make_client: Callable[..., Any],
) -> None:
    """Uncomparable firmware versions should trigger a status refresh.

    Args:
        make_client (Callable[..., Any]): Factory used to configure an uncomparable version response.
    """
    client = make_client()
    try:
        client._is_get_endpoint_available = AsyncMock(return_value=True)
        status = {
            "product": {
                "product_version": "26.1.11_1",
                "product_latest": "26.1.11_bad",
                "product_check": {"status": "ok"},
            },
            "status_msg": "There are no updates available on the selected mirror.",
            "last_check": datetime.now(UTC).isoformat(),
        }
        client._safe_dict_get = AsyncMock(
            side_effect=lambda path: {"status": "ready"} if path.endswith("/running") else status
        )
        client._get_opnsense_timezone = AsyncMock(return_value=UTC)
        client._do_post = AsyncMock(return_value={})

        result = await client.get_firmware_update_info()

        assert result == status
        client._do_post.assert_awaited_once_with("/api/core/firmware/check")
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("upgrade_type", "expected_path"),
    [("update", "/api/core/firmware/update"), ("upgrade", "/api/core/firmware/upgrade")],
)
async def test_upgrade_firmware_calls_expected_endpoint(
    make_client: Callable[..., Any], upgrade_type: str, expected_path: str
) -> None:
    """Supported upgrade types should call the matching firmware endpoint.

    Args:
        make_client (Callable[..., Any]): Factory used to create the upgrade client.
        upgrade_type (str): Supported upgrade action supplied to the client.
        expected_path (str): Firmware API path expected for the action.
    """
    client = make_client()
    try:
        client._firmware_version = "26.1.1"
        client._safe_dict_post = AsyncMock(return_value={"status": "ok"})

        result = await client.upgrade_firmware(upgrade_type)

        assert result == {"status": "ok"}
        assert client._firmware_version is None
        client._safe_dict_post.assert_awaited_once_with(expected_path)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_upgrade_firmware_rejects_unknown_type(make_client: Callable[..., Any]) -> None:
    """Unknown firmware upgrade types should return `None` without calling the API.

    Args:
        make_client (Callable[..., Any]): Factory used to create the client rejecting the action.
    """
    client = make_client()
    try:
        client._safe_dict_post = AsyncMock()

        result = await client.upgrade_firmware("invalid")

        assert result is None
        client._safe_dict_post.assert_not_awaited()
    finally:
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
        client._throw_errors = throw_errors
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
    client, session = install_client(
        [
            (_install_progress("running", action=action.upper()), "busy", "26.7.8"),
            (progress, "busy", "26.7.8"),
            (progress, "ready", "26.7.8"),
        ],
        throw_errors=throw_errors,
        preflight=["busy", "ready"],
    )
    await client.install_firmware(action, target_version="26.7.8", poll_interval=0.001)
    assert client._firmware_version == "26.7.8"
    assert session.get.call_count == 12
    session.post.assert_called_once()
    assert session.post.call_args.args[0].endswith(f"/{action}")
    assert client._throw_errors is throw_errors
    assert not client._firmware_install_active


@pytest.mark.asyncio
@pytest.mark.parametrize("throw_errors", [False, True])
async def test_install_firmware_reboot_recovers_empty_log(
    install_client: Callable[..., Any], throw_errors: bool
) -> None:
    """Server reboot requires outage and recovery despite postboot empty ERROR.

    Args:
        install_client (Callable[..., Any]): Lifecycle transport factory.
        throw_errors (bool): Shared client error propagation mode.
    """

    reboot = _install_progress("reboot", "***REBOOT***")
    client, session = install_client(
        [
            (reboot, "busy", "26.7.8"),
            (reboot, "ready", "26.7.8"),  # pre-reboot five-second window
            aiohttp.ClientConnectionError("server rebooted"),
            ({"status": "error", "log": ""}, "busy", "26.7.8"),
            ({"status": "error", "log": ""}, "ready", "26.7.8"),
        ],
        throw_errors=throw_errors,
    )
    await client.install_firmware(target_version="26.7.8", poll_interval=0.001)
    assert session.get.call_count == 15
    session.post.assert_called_once()


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
    install_client: Callable[..., Any], progress: dict[str, str], version: str, error: str | None
) -> None:
    """Terminal logs and actual installed versions jointly establish success.

    Args:
        install_client (Callable[..., Any]): Lifecycle transport factory.
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
    assert not client._firmware_install_active


@pytest.mark.asyncio
@pytest.mark.parametrize("baseline", ["old check", _install_progress("done", "***DONE***")["log"]])
async def test_install_firmware_stale_log_times_out_and_cleans_up(
    install_client: Callable[..., Any], baseline: str
) -> None:
    """Stale progress cannot confirm a new installation even at target version.

    Args:
        install_client (Callable[..., Any]): Lifecycle transport factory.
        baseline (str): Previous daemon log.
    """

    client, session = install_client(
        [({"status": "done", "log": baseline}, "ready", "26.7.8")], baseline=baseline
    )
    with pytest.raises(OPNsenseError, match="deadline"):
        await client.install_firmware(target_version="26.7.8", timeout=0.01, poll_interval=0.1)
    assert not client._firmware_install_active
    session.post.assert_called_once()


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
    install_client: Callable[..., Any], monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """Cancellation releases local ownership; concurrent installs never queue.

    Args:
        install_client (Callable[..., Any]): Lifecycle transport factory.
        monkeypatch (pytest.MonkeyPatch): Async wait patch fixture.
        phase (str): Cancellation before or after firmware dispatch.
    """

    client, session = install_client([])
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_wait(*args: Any) -> None:
        """Hold the preflight idle wait until the owning task is cancelled.

        Args:
            args (Any): Unused lifecycle observation arguments.
        """
        entered.set()
        await release.wait()

    method = "_wait_firmware_idle" if phase == "preflight" else "_observe_firmware_install"
    monkeypatch.setattr(client, method, blocked_wait)
    task = asyncio.create_task(client.install_firmware(target_version="26.7.8"))
    await entered.wait()
    with pytest.raises(OPNsenseError, match="already active"):
        await client.install_firmware(target_version="26.7.8")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not client._firmware_install_active
    assert session.post.call_count == (0 if phase == "preflight" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "active,check,server",
    [
        (True, True, "ready"),
        (False, False, "ready"),
        (False, True, "busy"),
        (False, True, "unknown"),
    ],
)
async def test_firmware_automatic_check_is_suppressed(
    make_client: Callable[..., Any], active: bool, check: bool, server: str
) -> None:
    """Read-only and active/unknown server states never dispatch automatic checks.

    Args:
        make_client (Callable[..., Any]): Public client factory.
        active (bool): Local install state.
        check (bool): Whether automatic checking is permitted.
        server (str): Server lock state.
    """
    client = make_client()
    client._firmware_install_active = active
    client._is_get_endpoint_available = AsyncMock(return_value=True)
    client._safe_dict_get = AsyncMock(
        side_effect=lambda path: {"status": server} if path.endswith("/running") else {}
    )
    client._do_post = AsyncMock()
    await client.get_firmware_update_info(check_for_updates=check)
    client._do_post.assert_not_awaited()


@pytest.mark.asyncio
async def test_install_firmware_start_deadline(
    install_client: Callable[..., Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unconfirmed dispatch gets a short bounded wait independent of overall timeout.

    Args:
        install_client (Callable[..., Any]): Lifecycle transport factory.
        monkeypatch (pytest.MonkeyPatch): Start-deadline override fixture.
    """
    monkeypatch.setattr(firmware_module, "FIRMWARE_START_TIMEOUT_SECONDS", 0.005)
    client, session = install_client([({"status": "done", "log": "old check"}, "ready", "26.7.8")])
    with pytest.raises(OPNsenseError, match="did not start"):
        await client.install_firmware(target_version="26.7.8", poll_interval=100, timeout=1)
    session.post.assert_called_once()
    assert not client._firmware_install_active


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
@pytest.mark.parametrize("http_status", [401, 403])
@pytest.mark.parametrize("phase", ["dispatch", "observation"])
async def test_install_firmware_auth_failure_is_fatal(
    install_client: Callable[..., Any],
    fake_response_factory: Callable[..., Any],
    http_status: int,
    phase: str,
) -> None:
    """Permission failures must not be treated as a reboot outage or retried.

    Args:
        install_client (Callable[..., Any]): Lifecycle transport factory.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
        http_status (int): Fatal HTTP permission status.
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
    with pytest.raises(OPNsenseError, match=str(http_status)):
        await client.install_firmware(target_version="26.7.8", poll_interval=0.001)
    session.post.assert_called_once()
    assert not client._firmware_install_active


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
        assert not client._firmware_action_lock.locked()
    finally:
        release_worker.set()
        finish_check.set()
        await client.async_close()
