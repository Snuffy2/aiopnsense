"""Tests for `aiopnsense.firmware` behaviors."""

import asyncio
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

import aiopnsense
from aiopnsense._endpoint_outcome import _EndpointOutcome
from aiopnsense.const import (
    FIRMWARE_CHECK_COOLDOWN_SECONDS,
    OPNSENSE_MIN_FIRMWARE,
    OPNSENSE_RECOMMENDED_FIRMWARE,
)
from aiopnsense.exceptions import (
    OPNsenseConnectionError,
    OPNsenseError,
    OPNsenseInvalidArgument,
    OPNsenseInvalidAuth,
    OPNsensePrivilegeMissing,
)
import aiopnsense.firmware as firmware_module
from aiopnsense.helpers import firmware_is_at_least
from aiopnsense.system import (
    CORE_SYSTEM_DISMISS_CAMELCASE_ENDPOINT,
    CORE_SYSTEM_DISMISS_SNAKE_ENDPOINT,
)
from tests.conftest import (
    FIRMWARE_VERSION_SHAPES,
    FakeResponse,
    FakeRouter,
    FirmwareRouter,
    FirmwareVersionShape,
    install_fake_router,
    make_mock_session_client,
)

STATUS_PATH = "/api/core/firmware/status"
RUNNING_PATH = "/api/core/firmware/running"
CHECK_PATH = "/api/core/firmware/check"
UPGRADE_STATUS_PATH = "/api/core/firmware/upgradestatus"

# Router responses and public exceptions shared by the read-outcome tests.
UNAVAILABLE_ROUTES = [
    pytest.param(404, id="missing"),
    pytest.param(403, id="permission-denied"),
    pytest.param(500, id="server-error"),
    pytest.param(TimeoutError("timed out"), id="timeout"),
]
THROWING_ROUTES = [
    pytest.param(403, OPNsensePrivilegeMissing, id="permission-denied"),
    pytest.param(500, OPNsenseConnectionError, id="server-error"),
    pytest.param(TimeoutError("timed out"), OPNsenseConnectionError, id="timeout"),
]

_SHAPE_PARAMS = [pytest.param(shape, id=shape.id) for shape in FIRMWARE_VERSION_SHAPES]
_CACHED_SHAPE_PARAMS = [
    pytest.param(shape, id=shape.id) for shape in FIRMWARE_VERSION_SHAPES if shape.cached
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("product", "expected"),
    [
        pytest.param({"product_version": "25.8.0"}, "25.8.0", id="semver"),
        pytest.param(
            {"product_version": "weird", "product_series": "seriesX"},
            "seriesX",
            id="falls-back-to-series",
        ),
    ],
)
async def test_get_host_firmware_version_and_fallback(
    make_client: Callable[..., Any], product: dict[str, str], expected: str
) -> None:
    """Firmware version lookup should prefer semver and fall back to product series.

    Args:
        make_client (Callable[..., Any]): Factory used to create the firmware client.
        product (dict[str, str]): Product block the Router returns for the firmware status.
        expected (str): Version the public method should return.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(
        client, session, routes={("GET", STATUS_PATH): {"product": product}}
    )
    try:
        assert await client.get_host_firmware_version() == expected
        assert router.count("GET", STATUS_PATH) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("route_value", UNAVAILABLE_ROUTES)
async def test_get_host_firmware_version_is_none_when_status_unavailable(
    make_client: Callable[..., Any], route_value: Any
) -> None:
    """An absent or failing firmware status endpoint leaves the version unknown.

    Args:
        make_client (Callable[..., Any]): Factory used to create the firmware client.
        route_value (Any): Response the Router gives for the firmware status endpoint.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", STATUS_PATH): route_value})
    try:
        assert await client.get_host_firmware_version() is None
        assert router.count("GET", STATUS_PATH) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("old_response", [{"product": {"product_version": "25.1"}}, 404])
async def test_delayed_firmware_read_cannot_overwrite_a_newer_observation(
    firmware_router: tuple[aiopnsense.OPNsenseClient, FirmwareRouter],
    monkeypatch: pytest.MonkeyPatch,
    old_response: dict[str, Any] | int,
) -> None:
    """Ignore an old version or absence after a newer lookup chooses endpoint style.

    Args:
        firmware_router (tuple[aiopnsense.OPNsenseClient, FirmwareRouter]): Client and Router.
        monkeypatch (pytest.MonkeyPatch): Pauses the first completed firmware request.
        old_response (dict[str, Any] | int): Firmware result superseded by the newer lookup.
    """
    client, router = firmware_router
    router.endpoints[CORE_SYSTEM_DISMISS_SNAKE_ENDPOINT] = {"status": "ok"}
    router.endpoints[CORE_SYSTEM_DISMISS_CAMELCASE_ENDPOINT] = {"status": "ok"}
    session = cast(MagicMock, client._session)
    original_get = session.get.side_effect
    first_read_started = asyncio.Event()
    release_first_read = asyncio.Event()
    fetch = client._fetch_endpoint_outcome

    async def delay_first_result(path: str, **kwargs: Any) -> _EndpointOutcome:
        result = await fetch(path, **kwargs)
        if not first_read_started.is_set():
            first_read_started.set()
            await release_first_read.wait()
        return result

    def serve_firmware(url: str, **kwargs: Any) -> FakeResponse:
        if url.endswith(STATUS_PATH) and not first_read_started.is_set():
            if isinstance(old_response, int):
                return FakeResponse(status=old_response, ok=False)
            return FakeResponse(json_payload=old_response)
        return original_get(url, **kwargs)

    session.get.side_effect = serve_firmware
    monkeypatch.setattr(client, "_fetch_endpoint_outcome", delay_first_result)
    router.firmware = "25.7"
    old_read_call = asyncio.get_running_loop().create_task(client.close_notice("old-read"))
    try:
        async with asyncio.timeout(1):
            await first_read_started.wait()
            assert await client.close_notice("new-read") is True
            release_first_read.set()
            assert await old_read_call is True
    finally:
        if not old_read_call.done():
            old_read_call.cancel()
        await asyncio.gather(old_read_call, return_exceptions=True)

    assert await client.get_host_firmware_version() == "25.7"
    assert router.post_paths == [CORE_SYSTEM_DISMISS_SNAKE_ENDPOINT] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize("interruption", ["check", "empty-status"])
async def test_validation_reads_firmware_across_an_empty_cache_observation(
    firmware_router: tuple[aiopnsense.OPNsenseClient, FirmwareRouter],
    monkeypatch: pytest.MonkeyPatch,
    interruption: str,
) -> None:
    """Keep validation working when a concurrent lookup leaves the cache empty.

    Args:
        firmware_router (tuple[aiopnsense.OPNsenseClient, FirmwareRouter]): Client and Router.
        monkeypatch (pytest.MonkeyPatch): Pauses the first firmware result.
        interruption (str): Whether a check or an empty status response wins the race.
    """
    client, router = firmware_router
    router.firmware = "26.7.8"
    router.endpoints[firmware_module.FIRMWARE_RUNNING_ENDPOINT] = {"status": "ready"}
    router.endpoints[CHECK_PATH] = {"status": "ok"}
    lookup_started = asyncio.Event()
    release_lookup = asyncio.Event()
    request = client._fetch_endpoint_outcome

    async def delay_first_status_request(path: str, **kwargs: Any) -> _EndpointOutcome:
        """Hold the first completed firmware status request.

        Args:
            path (str): Requested endpoint.
            kwargs (Any): Options passed to the endpoint fetch.

        Returns:
            _EndpointOutcome: The scripted Router response.
        """
        response = await request(path, **kwargs)
        if path == firmware_module.FIRMWARE_STATUS_ENDPOINT and not lookup_started.is_set():
            lookup_started.set()
            await release_lookup.wait()
        return response

    monkeypatch.setattr(client, "_fetch_endpoint_outcome", delay_first_status_request)
    validation = asyncio.get_running_loop().create_task(client.validate(require_device_id=False))
    try:
        async with asyncio.timeout(1):
            await lookup_started.wait()
            if interruption == "check":
                await client.get_firmware_update_info()
                assert router.post_paths == [firmware_module.FIRMWARE_CHECK_ENDPOINT]
            else:
                router.firmware = None
                assert await client.get_host_firmware_version() is None
                router.firmware = "26.7.8"
            release_lookup.set()
            await validation
    finally:
        if not validation.done():
            validation.cancel()
        await asyncio.gather(validation, return_exceptions=True)

    assert await client.get_host_firmware_version() == "26.7.8"


@pytest.mark.asyncio
async def test_validation_survives_repeated_automatic_firmware_checks(
    firmware_router: tuple[aiopnsense.OPNsenseClient, FirmwareRouter],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An automatic check preserves a fresh read once the old cache is clear.

    Args:
        firmware_router (tuple[aiopnsense.OPNsenseClient, FirmwareRouter]): Client and Router.
        monkeypatch (pytest.MonkeyPatch): Pauses firmware responses during validation.
    """
    client, router = firmware_router
    router.firmware = "26.7.7"
    router.endpoints[firmware_module.FIRMWARE_RUNNING_ENDPOINT] = {"status": "ready"}
    router.endpoints[CHECK_PATH] = {
        "status": "ok",
        "msg_uuid": "check",
    }
    assert await client.get_host_firmware_version() == "26.7.7"

    pending_reads: asyncio.Queue[asyncio.Event] = asyncio.Queue()
    request = client._fetch_endpoint_outcome

    async def delay_validation_read(path: str, **kwargs: Any) -> _EndpointOutcome:
        """Hold each validation response until its corresponding check has run.

        Args:
            path (str): Requested endpoint.
            kwargs (Any): Options passed to the endpoint fetch.

        Returns:
            _EndpointOutcome: The scripted Router response.
        """
        response = await request(path, **kwargs)
        if (
            asyncio.current_task() is validation
            and path == firmware_module.FIRMWARE_STATUS_ENDPOINT
        ):
            resume_read = asyncio.Event()
            pending_reads.put_nowait(resume_read)
            await resume_read.wait()
        return response

    now = 1000.0
    monkeypatch.setattr(firmware_module, "monotonic", lambda: now)
    monkeypatch.setattr(client, "_fetch_endpoint_outcome", delay_validation_read)
    validation = asyncio.get_running_loop().create_task(client.validate(require_device_id=False))
    try:
        async with asyncio.timeout(1):
            first_read = await pending_reads.get()
            await client.get_firmware_update_info()
            router.firmware = "26.7.8"
            first_read.set()
            retry = await pending_reads.get()
            now += FIRMWARE_CHECK_COOLDOWN_SECONDS
            await client.get_firmware_update_info()
            retry.set()
            await validation
    finally:
        if not validation.done():
            validation.cancel()
        await asyncio.gather(validation, return_exceptions=True)

    assert router.post_paths == [firmware_module.FIRMWARE_CHECK_ENDPOINT] * 2
    assert await client.get_host_firmware_version() == "26.7.8"


@pytest.mark.asyncio
async def test_delayed_firmware_read_cannot_overwrite_verified_install(
    install_client: Callable[..., Any],
) -> None:
    """A pre-install status response cannot undo the verified installed version.

    Args:
        install_client (Callable[..., Any]): Lifecycle transport factory.
    """
    target_version = "26.7.8"
    client, _session = install_client(
        [(_install_progress("done", "***DONE***"), "ready", target_version)]
    )
    old_read_started = asyncio.Event()
    release_old_read = asyncio.Event()

    async def read_old_firmware(_path: str, **_kwargs: Any) -> _EndpointOutcome:
        """Hold the older firmware result until the installation succeeds.

        Args:
            _path (str): Requested endpoint.
            _kwargs (Any): Unused endpoint fetch options.

        Returns:
            _EndpointOutcome: The old firmware version.
        """
        old_read_started.set()
        await release_old_read.wait()
        return _EndpointOutcome.data({"product": {"product_version": "26.1.1"}})

    client._fetch_endpoint_outcome = read_old_firmware
    old_read = asyncio.create_task(client.get_host_firmware_version())
    try:
        async with asyncio.timeout(1):
            await old_read_started.wait()
        await client.install_firmware(target_version=target_version, poll_interval=0.001)
        release_old_read.set()
        async with asyncio.timeout(1):
            assert await old_read == target_version
    finally:
        release_old_read.set()
        if not old_read.done():
            old_read.cancel()
        await asyncio.gather(old_read, return_exceptions=True)

    assert await client.get_host_firmware_version() == target_version


@pytest.mark.asyncio
async def test_firmware_status_lookup_retries_cached_forbidden_endpoint(
    firmware_router: tuple[aiopnsense.OPNsenseClient, FirmwareRouter],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A firmware status request denied once must be retried on the next capability lookup.

    Args:
        firmware_router (tuple[aiopnsense.OPNsenseClient, FirmwareRouter]): Client and Router.
        monkeypatch (pytest.MonkeyPatch): Enables the fixture's real queue worker.
    """
    client, router = firmware_router
    monkeypatch.setattr(asyncio, "create_task", asyncio.get_running_loop().create_task)
    router.firmware = "25.1"
    router.endpoints[CORE_SYSTEM_DISMISS_SNAKE_ENDPOINT] = {"status": "ok"}
    router.endpoints[CORE_SYSTEM_DISMISS_CAMELCASE_ENDPOINT] = {"status": "ok"}
    session = cast(MagicMock, client._session)
    session_get = session.get
    router_get = session_get.side_effect
    firmware_request_count = 0

    def deny_first_firmware_request(url: str, **kwargs: Any) -> FakeResponse:
        nonlocal firmware_request_count
        if url.endswith(firmware_module.FIRMWARE_STATUS_ENDPOINT):
            firmware_request_count += 1
            if firmware_request_count == 1:
                return FakeResponse(status=403, reason="Forbidden", ok=False)
        return router_get(url, **kwargs)

    session_get.side_effect = deny_first_firmware_request

    assert await client.close_notice("before-recovery") is True
    assert await client.close_notice("after-recovery") is True

    assert firmware_request_count == 2
    assert router.post_paths == [
        CORE_SYSTEM_DISMISS_SNAKE_ENDPOINT,
        CORE_SYSTEM_DISMISS_CAMELCASE_ENDPOINT,
    ]


@pytest.mark.asyncio
async def test_malformed_firmware_series_uses_unknown_capability_fallback(
    make_client: Callable[..., Any],
) -> None:
    """A malformed series value must not break endpoint selection.

    Args:
        make_client (Callable[..., Any]): Factory used to create an OPNsense client.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(
        client,
        session,
        routes={
            ("GET", STATUS_PATH): {"product": {"product_series": []}},
            ("POST", CORE_SYSTEM_DISMISS_SNAKE_ENDPOINT): {"status": "ok"},
        },
    )
    try:
        assert await client.close_notice("malformed-series") is True
        assert router.count("POST", CORE_SYSTEM_DISMISS_SNAKE_ENDPOINT) == 1
    finally:
        await client.async_close()


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
            "27.1.b_21",
            "27.1.b_22",
            {"status": "ok"},
            timedelta(0),
            "There are no updates available on the selected mirror.",
            id="newer-beta-build",
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
    product_version: str,
    product_latest: str,
    product_check: dict[str, str],
    last_check_age: timedelta | None,
    status_msg: str | None,
) -> None:
    """Incomplete, stale, newer, and incomparable status should trigger refresh.

    Args:
        make_client (Callable[..., Any]): Factory used to create the firmware client.
        product_version (str): Installed firmware version in the status response.
        product_latest (str): Advertised latest firmware version in the status response.
        product_check (dict[str, str]): Firmware check details in the response.
        last_check_age (timedelta | None): Age of the last check, or ``None`` if missing.
        status_msg (str | None): Optional status message from the firmware response.
    """
    client, session = make_mock_session_client(make_client)
    try:
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
        router = install_fake_router(
            client,
            session,
            routes={
                ("GET", STATUS_PATH): status,
                ("GET", RUNNING_PATH): {"status": "ready"},
                ("POST", CHECK_PATH): {},
            },
        )
        client._get_opnsense_timezone = AsyncMock(return_value=UTC)

        result = await client.get_firmware_update_info()

        assert result == status
        assert router.count("GET", STATUS_PATH) == 1
        assert router.count("POST") == 1
        assert router.count("POST", CHECK_PATH) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("product_version", "product_latest"),
    [
        *(
            pytest.param(shape.product_version, shape.product_version, id=f"at-latest-{shape.id}")
            for shape in FIRMWARE_VERSION_SHAPES
            if shape.product_version is not None
        ),
        pytest.param("27.1.b_21", "27.1.b", id="beta-latest-without-build"),
        pytest.param("26.7.6_3", "26.7.6", id="installed-hotfix-ahead-of-base"),
        pytest.param("26.7.6_3", "26.7.6.0", id="installed-hotfix-ahead-of-zero-component-base"),
        pytest.param("26.7.6_10", "26.7.6_9", id="installed-hotfix-ahead-of-revision"),
        pytest.param("26.7.7", "26.7.6_9", id="installed-base-ahead-of-hotfix"),
    ],
)
async def test_get_firmware_update_info_does_not_trigger_check_for_recent_healthy_status(
    make_client: Callable[..., Any],
    product_version: str,
    product_latest: str,
) -> None:
    """Healthy firmware status should not trigger checks across repeated polls.

    Args:
        make_client (Callable[..., Any]): Factory used to configure a healthy recent firmware status.
        product_version (str): Installed firmware version in the status response.
        product_latest (str): Equal or older advertised firmware version.
    """
    client, session = make_mock_session_client(make_client)
    try:
        status = {
            "product": {
                "product_version": product_version,
                "product_latest": product_latest,
                "product_check": {"status": "ok"},
            },
            "status_msg": "There are no updates available on the selected mirror.",
            "last_check": datetime.now(UTC).isoformat(),
        }
        router = install_fake_router(
            client,
            session,
            routes={("GET", STATUS_PATH): status, ("GET", RUNNING_PATH): {"status": "ready"}},
        )
        client._get_opnsense_timezone = AsyncMock(return_value=UTC)

        for _ in range(3):
            result = await client.get_firmware_update_info()
            assert result == status
        assert router.count("POST") == 0
        assert router.count("GET", STATUS_PATH) == 3
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("upgrade_type", "expected_path"),
    [("update", "/api/core/firmware/update"), ("upgrade", "/api/core/firmware/upgrade")],
)
async def test_upgrade_firmware_calls_expected_endpoint(
    make_client: Callable[..., Any],
    upgrade_type: str,
    expected_path: str,
) -> None:
    """Deprecated upgrade calls warn and still dispatch to the matching endpoint.

    Args:
        make_client (Callable[..., Any]): Factory used to create the upgrade client.
        upgrade_type (str): Supported upgrade action supplied to the client.
        expected_path (str): Firmware API path expected for the action.
    """
    client, session = make_mock_session_client(make_client)
    try:
        client._firmware_version = "26.1.1"
        router = install_fake_router(
            client, session, routes={("POST", expected_path): {"status": "ok"}}
        )

        legacy_upgrade = client.upgrade_firmware  # type: ignore[deprecated]
        assert "install_firmware(target_version=...)" in legacy_upgrade.__deprecated__  # type: ignore[attr-defined]
        with pytest.warns(DeprecationWarning, match=r"Use install_firmware\(target_version="):
            result = await legacy_upgrade(upgrade_type)

        assert result == {"status": "ok"}
        assert router.count("POST") == 1
        assert router.count("POST", expected_path) == 1
        router.routes[("GET", STATUS_PATH)] = {"product": {"product_version": "26.1.2"}}
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
    router = install_fake_router(
        client,
        session,
        get_payloads=[
            {"status": "ready"},
            {"status": "done", "log": "old check"},
            progress_response,
            {"status": "ready"},
            {"product": {"product_version": "26.7.8"}},
        ],
    )
    install_task = asyncio.create_task(client.install_firmware(target_version="26.7.8"))
    try:
        await observing.wait()

        # The install owns the queued GET responses; route only the status read.
        router.routes[("GET", STATUS_PATH)] = {"status": "error"}
        await client.get_firmware_update_info()
        del router.routes[("GET", STATUS_PATH)]

        if throw_errors:
            with pytest.raises(OPNsenseError, match="already active"):
                await client.upgrade_firmware("upgrade")
        else:
            assert await client.upgrade_firmware("upgrade") is None
        assert router.count("POST") == 1
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
    """Upgrade status and changelog helpers should use the expected endpoints.

    Args:
        make_client (Callable[..., Any]): Factory used to configure the status and changelog client.
    """
    client, session = make_mock_session_client(make_client)
    changelog_path = "/api/core/firmware/changelog/26.1.1"
    router = install_fake_router(
        client,
        session,
        routes={
            ("GET", UPGRADE_STATUS_PATH): {"status": "running"},
            ("POST", changelog_path): {"changelog": "..."},
        },
    )
    try:
        status = await client.upgrade_status()
        changelog = await client.firmware_changelog("26.1.1")

        assert status == {"status": "running"}
        assert changelog == {"changelog": "..."}
        assert router.count("GET", UPGRADE_STATUS_PATH) == 1
        assert router.count("POST", changelog_path) == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("route_value", UNAVAILABLE_ROUTES)
@pytest.mark.parametrize(
    ("read", "path"),
    [
        pytest.param("get_firmware_update_info", STATUS_PATH, id="update-info"),
        pytest.param("upgrade_status", UPGRADE_STATUS_PATH, id="upgrade-status"),
    ],
)
async def test_firmware_reads_return_empty_mapping_when_endpoint_unavailable(
    make_client: Callable[..., Any], read: str, path: str, route_value: Any
) -> None:
    """Firmware status reads fail closed to an empty mapping with a single request.

    Args:
        make_client (Callable[..., Any]): Fixture factory used to create client instances.
        read (str): Public client method under test.
        path (str): Endpoint the method reads.
        route_value (Any): Response the Router gives for that endpoint.
    """
    client, session = make_mock_session_client(make_client)
    router = install_fake_router(client, session, routes={("GET", path): route_value})
    try:
        assert await getattr(client, read)() == {}
        assert router.count("GET", path) == 1
        assert router.count("POST") == 0
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
@pytest.mark.parametrize(
    ("packages", "expected_latest"),
    [
        pytest.param(
            [
                {"name": "curl", "current_version": "8.1", "new_version": "8.2"},
                {
                    "name": "opnsense-devel",
                    "current_version": "27.1.b_21",
                    "new_version": "27.1.b_22",
                },
            ],
            "27.1.b_22",
            id="devel-update-available",
        ),
        pytest.param(
            [{"name": "curl", "current_version": "8.1", "new_version": "8.2"}],
            "27.1.b",
            id="other-package-update",
        ),
        pytest.param([], "27.1.b", id="no-updates"),
    ],
)
async def test_firmware_update_info_reports_exact_devel_build(
    make_client: Callable[..., Any],
    packages: list[dict[str, str]],
    expected_latest: str,
) -> None:
    """Report the available beta build, which OPNsense omits from ``product_latest``.

    Args:
        make_client (Callable[..., Any]): Public client factory.
        packages (list[dict[str, str]]): Package rows in the firmware check.
        expected_latest (str): Latest version the caller should receive.
    """
    client, session = make_mock_session_client(make_client)
    try:
        status = _firmware_status(
            status="update",
            status_msg="There are 2 updates available.",
            product={
                "product_version": "27.1.b_21",
                "product_latest": "27.1.b",
                "product_series": "27.1",
                "product_check": {"upgrade_packages": packages},
            },
        )
        _configure_firmware_polls(client, session, status)

        result = await client.get_firmware_update_info(check_for_updates=False)

        assert result["product"]["product_latest"] == expected_latest
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("installed", "error"),
    [
        pytest.param("27.1.b_22", None, id="target-build-installed"),
        pytest.param("27.1.b_21", "target", id="older-build-still-installed"),
    ],
)
async def test_install_firmware_verifies_beta_target(
    install_client: Callable[..., Any], installed: str, error: str | None
) -> None:
    """A beta target installs and succeeds only once its build is on the Router.

    Args:
        install_client (Callable[..., Any]): Lifecycle transport factory.
        installed (str): Firmware version reported after the action.
        error (str | None): Expected failure text, if any.
    """
    client, session = install_client(
        [(_install_progress("done", "***DONE***"), "ready", installed)]
    )
    if error:
        with pytest.raises(OPNsenseError, match=error):
            await client.install_firmware(target_version="27.1.b_22", poll_interval=0.001)
    else:
        await client.install_firmware(target_version="27.1.b_22", poll_interval=0.001)
    session.post.assert_called_once()


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
        {"target_version": "27.1.b"},
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
    check: bool,
    server: str,
) -> None:
    """Read-only checks and non-ready server states never dispatch automatic checks.

    Args:
        make_client (Callable[..., Any]): Public client factory.
        check (bool): Whether automatic checking is permitted.
        server (str): Server lock state.
    """
    client, session = make_mock_session_client(make_client)
    try:
        router = install_fake_router(
            client,
            session,
            routes={
                ("GET", STATUS_PATH): {},
                ("GET", RUNNING_PATH): {"status": server},
                ("POST", CHECK_PATH): {"status": "ok"},
            },
        )
        await client.get_firmware_update_info(check_for_updates=check)
        assert router.count("POST") == 0
    finally:
        await client.async_close()


def _firmware_status(**overrides: Any) -> dict[str, Any]:
    """Build a recent, healthy firmware status with optional field overrides.

    Args:
        **overrides (Any): Top-level status fields or ``product`` fields to replace.

    Returns:
        dict[str, Any]: Firmware status response.
    """
    product = {
        "product_version": "26.7.9",
        "product_latest": "26.7.9",
        "product_check": {"status": "ok"},
    }
    product.update(overrides.pop("product", {}))
    return {
        "product": product,
        "status_msg": "There are no updates available on the selected mirror.",
        "last_check": datetime.now(UTC).isoformat(),
        **overrides,
    }


def _firmware_status_for_shape(shape: FirmwareVersionShape) -> dict[str, Any]:
    """Build the firmware status a Router returns for a version shape.

    A shape without a product version models the error a Router returns when firmware
    metadata is unavailable, which has no ``product`` key at all.

    Args:
        shape (FirmwareVersionShape): Version form reported by the Router.

    Returns:
        dict[str, Any]: Error status, with the shape's product when it has a version.
    """
    if shape.product_version is None:
        return {
            "status_msg": "Firmware status check was aborted internally. Please try again.",
            "status": "error",
        }
    return _firmware_status(
        status="error",
        product={"product_version": shape.product_version, "product_series": shape.product_series},
    )


def _configure_firmware_polls(
    client: Any, session: MagicMock, status: dict[str, Any], running: list[str] | None = None
) -> FakeRouter:
    """Serve an unchanging firmware status and a sequence of lock states.

    Args:
        client (Any): Client under test.
        session (MagicMock): Mock HTTP session for poll and install requests.
        status (dict[str, Any]): Status returned on each poll.
        running (list[str] | None): Lock states to serve, ending on the last one.

    Returns:
        FakeRouter: Router recording firmware status fetches from the request queue.
    """
    states = list(running or ["ready"])

    def read(path: str) -> dict[str, Any]:
        """Return the next mocked lock state for the firmware running read.

        Args:
            path (str): Firmware endpoint path.

        Returns:
            dict[str, Any]: Lock state.
        """
        return {"status": states.pop(0) if len(states) > 1 else states[0]}

    original_get, original_post = session.get, session.post
    router = install_fake_router(client, session, routes={("GET", STATUS_PATH): status})
    poll_get = session.get

    def get(url: str, **kwargs: Any) -> FakeResponse:
        """Serve queued status reads separately from the install workflow.

        Args:
            url (str): Full request URL.
            kwargs (Any): HTTP request options.

        Returns:
            FakeResponse: Poll response or the next scripted install response.
        """
        if url.endswith(STATUS_PATH):
            return poll_get(url, **kwargs)
        return original_get(url, **kwargs)

    session.get = get
    session.post = original_post
    client._safe_dict_get = AsyncMock(side_effect=read)
    client._get_opnsense_timezone = AsyncMock(return_value=UTC)
    return router


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        pytest.param(_firmware_status(status="error"), id="mirror-error"),
        pytest.param(
            _firmware_status(product={"product_latest": "26.7.10"}),
            id="newer-version-without-details",
        ),
        pytest.param(
            _firmware_status(product={"product_latest": "26.7.9_bad"}),
            id="incomparable-version",
        ),
        pytest.param(_firmware_status(last_check="not a date"), id="invalid-last-check"),
        pytest.param(_firmware_status(product={"product_check": None}), id="empty-check"),
    ],
)
async def test_persistent_refresh_condition_checks_once_per_cooldown(
    make_client: Callable[..., Any],
    fake_response_factory: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    status: dict[str, Any],
) -> None:
    """A status that a check cannot clear requests one check per hour.

    Args:
        make_client (Callable[..., Any]): Public client factory.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
        monkeypatch (pytest.MonkeyPatch): Controls the cooldown clock.
        status (dict[str, Any]): Status that keeps requesting a refresh.
    """
    now = 1000.0
    monkeypatch.setattr(firmware_module, "monotonic", lambda: now)
    client, session = make_mock_session_client(make_client)
    try:
        _configure_firmware_polls(client, session, status)
        session.post.return_value = fake_response_factory(
            json_payload={"status": "ok", "msg_uuid": "check"}
        )

        for _ in range(3):
            await client.get_firmware_update_info()
        assert session.post.call_count == 1

        now += 3599
        await client.get_firmware_update_info()
        assert session.post.call_count == 1

        now += 1
        await client.get_firmware_update_info()
        assert session.post.call_count == 2
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_busy_router_does_not_start_firmware_check_cooldown(
    make_client: Callable[..., Any],
    fake_response_factory: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A poll that finds the Router busy can still check on the next poll.

    Args:
        make_client (Callable[..., Any]): Public client factory.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
        monkeypatch (pytest.MonkeyPatch): Controls the cooldown clock.
    """
    monkeypatch.setattr(firmware_module, "monotonic", lambda: 1000.0)
    client, session = make_mock_session_client(make_client)
    try:
        _configure_firmware_polls(
            client, session, _firmware_status(status="error"), ["busy", "ready"]
        )
        session.post.return_value = fake_response_factory(json_payload={"status": "ok"})

        await client.get_firmware_update_info()
        assert session.post.call_count == 0

        await client.get_firmware_update_info()
        assert session.post.call_count == 1
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unacknowledged",
    [
        pytest.param(aiohttp.ClientError("connection lost"), id="transport-failure"),
        pytest.param({"status": "failure"}, id="non-ok-response"),
        pytest.param({"status": "ok", "msg_uuid": ""}, id="empty-msg-uuid"),
    ],
)
async def test_unacknowledged_firmware_check_does_not_start_cooldown(
    make_client: Callable[..., Any],
    fake_response_factory: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    unacknowledged: Any,
) -> None:
    """Only an acknowledged check starts the cooldown, so a failed check is retried next poll.

    Args:
        make_client (Callable[..., Any]): Public client factory.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
        monkeypatch (pytest.MonkeyPatch): Controls the cooldown clock.
        unacknowledged (Any): Transport exception or non-ok response payload for the first check.
    """
    monkeypatch.setattr(firmware_module, "monotonic", lambda: 1000.0)
    client, session = make_mock_session_client(make_client)
    try:
        _configure_firmware_polls(client, session, _firmware_status(status="error"))
        first = (
            unacknowledged
            if isinstance(unacknowledged, BaseException)
            else fake_response_factory(json_payload=unacknowledged)
        )
        session.post.side_effect = [
            first,
            fake_response_factory(json_payload={"status": "ok", "msg_uuid": "check"}),
        ]

        await client.get_firmware_update_info()
        assert session.post.call_count == 1

        await client.get_firmware_update_info()
        assert session.post.call_count == 2

        await client.get_firmware_update_info()
        assert session.post.call_count == 2
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "observations"),
    [
        pytest.param(
            "verified-install",
            [(_install_progress("done", "***DONE***"), "ready", "26.7.9")],
            id="verified-install",
        ),
        pytest.param(
            "failed-install",
            [(_install_progress("error"), "ready", "26.7.9")],
            id="failed-install",
        ),
        pytest.param("deprecated-upgrade", [], id="deprecated-upgrade"),
    ],
)
async def test_dispatched_firmware_action_ends_check_cooldown(
    install_client: Callable[..., Any],
    fake_response_factory: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    observations: list[Any],
) -> None:
    """Dispatching an update or upgrade lets the next poll check again, whatever its outcome.

    Args:
        install_client (Callable[..., Any]): Lifecycle transport factory.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
        monkeypatch (pytest.MonkeyPatch): Controls the cooldown clock.
        action (str): Install outcome or deprecated call that dispatches the action.
        observations (list[Any]): Progress observations served after dispatch.
    """
    monkeypatch.setattr(firmware_module, "monotonic", lambda: 1000.0)
    client, session = install_client(observations)
    try:
        _configure_firmware_polls(client, session, _firmware_status(status="error"))
        session.post.return_value = fake_response_factory(
            json_payload={"status": "ok", "msg_uuid": "check"}
        )

        await client.get_firmware_update_info()
        await client.get_firmware_update_info()
        assert session.post.call_count == 1

        if action == "deprecated-upgrade":
            with pytest.warns(DeprecationWarning, match=r"Use install_firmware\(target_version="):
                await client.upgrade_firmware("upgrade")  # type: ignore[deprecated]
        elif action == "failed-install":
            with pytest.raises(OPNsenseError, match="reported 'error'"):
                await client.install_firmware(target_version="26.7.9", poll_interval=0.001)
        else:
            await client.install_firmware(target_version="26.7.9", poll_interval=0.001)
        assert session.post.call_count == 2

        await client.get_firmware_update_info()
        assert session.post.call_count == 3
        assert session.post.call_args.args[0] == "http://localhost/api/core/firmware/check"
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", _SHAPE_PARAMS)
async def test_firmware_lookup_handles_router_version_shapes(
    make_client: Callable[..., Any], shape: FirmwareVersionShape
) -> None:
    """Return each reported form and reread the Router only when it was not cached.

    Args:
        make_client (Callable[..., Any]): Public client factory.
        shape (FirmwareVersionShape): Version form reported by the Router.
    """
    client, session = make_mock_session_client(make_client)
    try:
        router = _configure_firmware_polls(client, session, _firmware_status_for_shape(shape))

        assert await client.get_host_firmware_version() == shape.lookup_version
        reads_before = router.count("GET", STATUS_PATH)
        assert await client.get_host_firmware_version() == shape.lookup_version
        assert router.count("GET", STATUS_PATH) - reads_before == (0 if shape.cached else 1)
    finally:
        await client.async_close()


async def _lookup_after_cooldown_poll(
    client: Any,
    session: MagicMock,
    fake_response_factory: Callable[..., Any],
    initial_status: dict[str, Any],
    changed_status: dict[str, Any],
) -> tuple[str | None, int]:
    """Change the firmware status during cooldown and run the next version lookup.

    The status object is updated in place, because the poll mock serves that same
    object. The returned version alone cannot show a wrongly cleared cache, because the
    next read returns the same value either way, so the status reads are returned as well.

    Args:
        client (Any): Client under test.
        session (MagicMock): Mock session receiving the check POST.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
        initial_status (dict[str, Any]): Firmware status served before the cooldown poll.
        changed_status (dict[str, Any]): Firmware status that replaces it during cooldown.

    Returns:
        tuple[str | None, int]: Version from the final lookup and its status reads.
    """
    router = _configure_firmware_polls(client, session, initial_status)
    session.post.return_value = fake_response_factory(
        json_payload={"status": "ok", "msg_uuid": "check"}
    )

    await client.get_firmware_update_info()
    assert session.post.call_count == 1
    await client.get_host_firmware_version()

    initial_status.clear()
    initial_status.update(changed_status)
    assert await client.get_firmware_update_info() == changed_status

    reads_before = router.count("GET", STATUS_PATH)
    version = await client.get_host_firmware_version()
    assert session.post.call_count == 1
    return version, router.count("GET", STATUS_PATH) - reads_before


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", _CACHED_SHAPE_PARAMS)
async def test_firmware_cooldown_keeps_cache_for_unchanged_version_shape(
    make_client: Callable[..., Any],
    fake_response_factory: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    shape: FirmwareVersionShape,
) -> None:
    """An unchanged status during cooldown does not clear a cached version.

    Args:
        make_client (Callable[..., Any]): Public client factory.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
        monkeypatch (pytest.MonkeyPatch): Controls the cooldown clock.
        shape (FirmwareVersionShape): Version form reported by the Router.
    """
    monkeypatch.setattr(firmware_module, "monotonic", lambda: 1000.0)
    client, session = make_mock_session_client(make_client)
    try:
        result = await _lookup_after_cooldown_poll(
            client,
            session,
            fake_response_factory,
            _firmware_status_for_shape(shape),
            _firmware_status_for_shape(shape),
        )
        assert result == (shape.lookup_version, 0)
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", _SHAPE_PARAMS)
async def test_firmware_cooldown_refreshes_cache_for_new_version_shape(
    make_client: Callable[..., Any],
    fake_response_factory: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    shape: FirmwareVersionShape,
) -> None:
    """A change to a usable version refreshes the cache, and any other change keeps it.

    Args:
        make_client (Callable[..., Any]): Public client factory.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
        monkeypatch (pytest.MonkeyPatch): Controls the cooldown clock.
        shape (FirmwareVersionShape): Version form the Router changes to.
    """
    previous_version = "25.7.1"
    monkeypatch.setattr(firmware_module, "monotonic", lambda: 1000.0)
    client, session = make_mock_session_client(make_client)
    try:
        result = await _lookup_after_cooldown_poll(
            client,
            session,
            fake_response_factory,
            _firmware_status(status="error", product={"product_version": previous_version}),
            _firmware_status_for_shape(shape),
        )
        assert result == ((shape.lookup_version, 1) if shape.cached else (previous_version, 0))
    finally:
        await client.async_close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("initial_product", "changed_product", "expected_version", "expected_reads"),
    [
        pytest.param(
            {"product_version": "26.7.9"},
            {"product_version": "not-a-version", "product_series": "not-a-series"},
            "26.7.9",
            0,
            id="no-comparable-version-or-series",
        ),
        pytest.param(
            {"product_version": "26.7.9_1"},
            {"product_version": "26.7.9_2"},
            "26.7.9_2",
            1,
            id="hotfix-bump",
        ),
    ],
)
async def test_firmware_cooldown_refreshes_only_comparable_version_changes(
    make_client: Callable[..., Any],
    fake_response_factory: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    initial_product: dict[str, Any],
    changed_product: dict[str, Any],
    expected_version: str,
    expected_reads: int,
) -> None:
    """A comparable version change seen during cooldown refreshes the cache without a check POST.

    The version compared is the one the normal lookup would use: the product
    version, or its product series when the version is not comparable. A status
    with no comparable version or series leaves the cached version in place.

    Args:
        make_client (Callable[..., Any]): Public client factory.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
        monkeypatch (pytest.MonkeyPatch): Controls the cooldown clock.
        initial_product (dict[str, Any]): Product fields before the cooldown poll.
        changed_product (dict[str, Any]): Product fields that change during cooldown.
        expected_version (str): Version returned after the cooldown poll.
        expected_reads (int): Status reads expected from the final version lookup.
    """
    monkeypatch.setattr(firmware_module, "monotonic", lambda: 1000.0)
    client, session = make_mock_session_client(make_client)
    try:
        result = await _lookup_after_cooldown_poll(
            client,
            session,
            fake_response_factory,
            _firmware_status(status="error", product=initial_product),
            _firmware_status(status="error", product=changed_product),
        )
        assert result == (expected_version, expected_reads)
    finally:
        await client.async_close()


@pytest.mark.asyncio
async def test_concurrent_firmware_update_info_sends_one_check(
    make_client: Callable[..., Any],
    fake_response_factory: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent polls on a status that stays stale share one acknowledged check.

    Args:
        make_client (Callable[..., Any]): Public client factory.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
        monkeypatch (pytest.MonkeyPatch): Controls the cooldown clock.
    """
    monkeypatch.setattr(firmware_module, "monotonic", lambda: 1000.0)
    client, session = make_mock_session_client(make_client)
    try:
        _configure_firmware_polls(client, session, _firmware_status(last_check="not a date"))
        response = fake_response_factory(json_payload={"status": "ok"})

        async def slow_json(*_args: Any, **_kwargs: Any) -> dict[str, str]:
            """Yield to the event loop while the check is in flight.

            Args:
                _args (Any): Unused decoding arguments.
                _kwargs (Any): Unused decoding options.

            Returns:
                dict[str, str]: Check dispatch acknowledgement.
            """
            await asyncio.sleep(0)
            return {"status": "ok", "msg_uuid": "check"}

        response.json = slow_json
        session.post.return_value = response

        await asyncio.gather(*(client.get_firmware_update_info() for _ in range(5)))
        assert session.post.call_count == 1
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
) -> None:
    """Cancelling a check must not leave a mutating request behind.

    Args:
        make_client (Callable[..., Any]): Public client factory.
        fake_response_factory (Callable[..., Any]): HTTP response factory.
    """
    client, session = make_mock_session_client(make_client)
    finish_check = asyncio.Event()
    dispatched = asyncio.Event()

    async def check_response(*args: Any, **kwargs: Any) -> dict[str, str]:
        """Hold the check HTTP response until its caller is cancelled.

        Args:
            args (Any): Unused decoding arguments.
            kwargs (Any): Unused decoding options.

        Returns:
            dict[str, str]: Check dispatch acknowledgement.
        """
        dispatched.set()
        await finish_check.wait()
        return {"status": "ok"}

    held_check = fake_response_factory(json_payload={"status": "ok"})
    held_check.json = check_response
    router = install_fake_router(
        client,
        session,
        routes={
            ("GET", STATUS_PATH): {},
            ("GET", RUNNING_PATH): {"status": "ready"},
            ("POST", CHECK_PATH): held_check,
        },
    )
    check = asyncio.create_task(client.get_firmware_update_info())
    try:
        await dispatched.wait()
        assert router.count("POST", CHECK_PATH) == 1
        check.cancel()
        with pytest.raises(asyncio.CancelledError):
            await check
        finish_check.set()
        await client.upgrade_status()
        assert router.count("POST", CHECK_PATH) == 1

        router.routes[("POST", CHECK_PATH)] = {"status": "ok"}
        await client.get_firmware_update_info()
        assert router.count("POST", CHECK_PATH) == 2
    finally:
        finish_check.set()
        await client.async_close()


def test_get_firmware_requirements_reports_library_thresholds() -> None:
    """Return the library's Minimum and Recommended Firmware as an immutable value.

    The Minimum Firmware is not above the Recommended Firmware.
    """
    requirements = aiopnsense.OPNsenseClient.get_firmware_requirements()

    assert isinstance(requirements, aiopnsense.OPNsenseClient.FirmwareRequirements)
    assert requirements.minimum == OPNSENSE_MIN_FIRMWARE
    assert requirements.recommended == OPNSENSE_RECOMMENDED_FIRMWARE
    assert firmware_is_at_least(requirements.recommended, requirements.minimum) is True
    with pytest.raises(AttributeError):
        requirements.minimum = "0"  # type: ignore[misc]
