"""Firmware-related methods for OPNsenseClient."""

import asyncio
from collections.abc import MutableMapping
from datetime import datetime, timedelta
import math
from typing import Any
from warnings import deprecated

import aiohttp
from dateutil.parser import ParserError, UnknownTimezoneWarning, parse

from ._typing import AiopnsenseClientProtocol
from .const import AMBIGUOUS_TZINFOS, DEFAULT_REQUEST_TIMEOUT_SECONDS
from .exceptions import (
    OPNsenseConnectionError,
    OPNsenseError,
    OPNsenseInvalidArgument,
    _map_opnsense_exception,
    _opnsense_http_error,
)
from .helpers import (
    _LOGGER,
    _log_errors,
    dict_get,
    firmware_is_at_least,
    firmware_is_newer,
    trim_firmware_suffix,
)

FIRMWARE_STATUS_ENDPOINT = "/api/core/firmware/status"
FIRMWARE_START_TIMEOUT_SECONDS = 60
FIRMWARE_RUNNING_ENDPOINT = "/api/core/firmware/running"
FIRMWARE_CHECK_ENDPOINT = "/api/core/firmware/check"
FIRMWARE_UPGRADE_STATUS_ENDPOINT = "/api/core/firmware/upgradestatus"
FIRMWARE_ACTION_ENDPOINT_PREFIX = "/api/core/firmware/"
FIRMWARE_CHANGELOG_ENDPOINT_PREFIX = "/api/core/firmware/changelog/"


class FirmwareMixin(AiopnsenseClientProtocol):
    """Firmware methods for OPNsenseClient."""

    _firmware_version: str | None
    _firmware_install_active: bool

    async def _store_host_firmware_version(self) -> None:
        """Cache the installed OPNsense firmware version or product series."""
        if not await self._is_get_endpoint_available(FIRMWARE_STATUS_ENDPOINT):
            _LOGGER.debug("Firmware status endpoint unavailable")
            self._firmware_version = None
            return

        firmware_info = await self._safe_dict_get(FIRMWARE_STATUS_ENDPOINT)
        firmware: str | None = dict_get(firmware_info, "product.product_version")
        comparable_firmware = trim_firmware_suffix(firmware)
        if not comparable_firmware or firmware_is_at_least(comparable_firmware, "0") is None:
            old = firmware
            firmware = dict_get(firmware_info, "product.product_series", old)
            if firmware != old:
                _LOGGER.debug(
                    "[get_host_firmware_version] firmware: %s not valid SemVer, using %s",
                    old,
                    firmware,
                )
        else:
            _LOGGER.debug("[get_host_firmware_version] firmware: %s", firmware)
        self._firmware_version = firmware

    @_log_errors
    async def get_host_firmware_version(self) -> None | str:
        """Return the cached OPNsense firmware version.

        Returns:
            None | str: Installed firmware version, falling back to the product
                series for non-SemVer version strings, or ``None`` when the
                firmware status endpoint is unavailable.
        """
        if self._firmware_version is None:
            await self._store_host_firmware_version()
        return self._firmware_version

    @_log_errors
    async def get_firmware_update_info(
        self, *, check_for_updates: bool = True
    ) -> MutableMapping[str, Any]:
        """Return firmware status and trigger a refresh when cached data is stale.

        Args:
            check_for_updates (bool): Allow automatic refresh checks when idle.
                Disable for strictly read-only firmware status requests.

        Returns:
            MutableMapping[str, Any]: Firmware status payload from OPNsense,
                including product version, latest version, check status, and
                update metadata when available. Returns an empty mapping when
                the firmware status endpoint is unavailable.
        """
        if not await self._is_get_endpoint_available(FIRMWARE_STATUS_ENDPOINT):
            _LOGGER.debug("Firmware status endpoint unavailable")
            return {}

        status = await self._safe_dict_get(FIRMWARE_STATUS_ENDPOINT)

        # if error or too old trigger check (only if check is not already in progress)
        # {'status_msg': 'Firmware status check was aborted internally. Please try again.', 'status': 'error'}
        # error could be because data has not been refreshed at all OR an upgrade is currently in progress
        if error_status := bool(status.get("status") == "error"):
            _LOGGER.debug("Last firmware status check returned an error")

        product_version = dict_get(status, "product.product_version")
        product_latest = dict_get(status, "product.product_latest")
        missing_data = False
        if (
            not product_version
            or not product_latest
            or not isinstance(dict_get(status, "product.product_check"), MutableMapping)
            or not dict_get(status, "product.product_check")
        ):
            _LOGGER.debug("Missing data in firmware status")
            missing_data = True

        update_needs_info = False
        latest_is_newer = firmware_is_newer(product_latest, product_version)
        if latest_is_newer is None:
            _LOGGER.debug("Unable to compare product firmware versions")
            update_needs_info = True
        elif latest_is_newer and status.get("status_msg", "").strip() == (
            "There are no updates available on the selected mirror."
        ):
            _LOGGER.debug("Update available but missing details")
            update_needs_info = True

        last_check_str = status.get("last_check")
        last_check_expired = True
        if last_check_str:
            try:
                last_check_dt = parse(last_check_str, tzinfos=AMBIGUOUS_TZINFOS)
                if last_check_dt.tzinfo is None:
                    opnsense_tz = await self._get_opnsense_timezone()
                    last_check_dt = last_check_dt.replace(tzinfo=opnsense_tz)
                last_check_expired = (datetime.now().astimezone() - last_check_dt) > timedelta(
                    days=1
                )
                if last_check_expired:
                    _LOGGER.debug("Firmware status last check > 1 day ago")
            except (ValueError, TypeError, ParserError, UnknownTimezoneWarning) as e:
                _LOGGER.debug(
                    "Error getting firmware status last check. %s: %s", type(e).__name__, e
                )
        else:
            _LOGGER.debug("Firmware status last check is missing")

        if (
            check_for_updates
            and not self._firmware_install_active
            and (error_status or last_check_expired or missing_data or update_needs_info)
        ):
            # Serialize our check dispatch with install activation. The server's
            # lock is also shared with checks from other clients/the web GUI.
            async with self._firmware_action_lock:
                if not self._firmware_install_active:
                    running = await self._safe_dict_get(FIRMWARE_RUNNING_ENDPOINT)
                    if running.get("status") == "ready":
                        _LOGGER.info("Triggering firmware check")
                        self._firmware_version = None
                        # Do not queue this mutation: a cancelled queue caller
                        # can leave a CHECK that runs after the action lock releases.
                        await self._do_post(FIRMWARE_CHECK_ENDPOINT)

        return status

    async def _firmware_request(self, path: str, *, post: bool = False) -> MutableMapping[str, Any]:
        """Read firmware state or dispatch once without cached endpoint probes.

        Args:
            path (str): Firmware API endpoint.
            post (bool): Dispatch a POST instead of a read-only GET.

        Returns:
            MutableMapping[str, Any]: Decoded firmware response.

        Raises:
            OPNsenseConnectionError: Missing or malformed firmware response.
            _map_opnsense_exception: Mapped transport failure.
            _opnsense_http_error: Non-successful HTTP response.
        """
        self._rest_api_query_count += 1
        request = self._session.post if post else self._session.get
        try:
            async with request(
                f"{self._url}{path}",
                auth=aiohttp.BasicAuth(self._username, self._password),
                timeout=aiohttp.ClientTimeout(total=DEFAULT_REQUEST_TIMEOUT_SECONDS),
                ssl=self._verify_ssl,
            ) as response:
                if not response.ok:
                    raise _opnsense_http_error(response.status, response.reason)
                result = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise _map_opnsense_exception(err) from err
        except ValueError as err:
            # A restarting GUI may briefly return HTML or an incomplete body.
            # HTTP response receipt is not evidence of a reboot outage.
            raise OPNsenseConnectionError(
                f"Invalid firmware response from {path}", status=200
            ) from err
        if not isinstance(result, MutableMapping) or not result:
            raise OPNsenseConnectionError(f"No firmware response from {path}", status=200)
        return result

    async def install_firmware(
        self,
        type: str = "update",
        *,
        target_version: str,
        timeout: float = 1800,
        poll_interval: float = 5,
    ) -> None:
        """Install firmware and verify completion, including server-managed reboot.

        The action is dispatched exactly once. Transport ambiguity never causes
        a retry. A new action log, terminal state, idle server and installed
        target version must agree before this method returns. Rebooting updates
        additionally require an observed outage followed by reconnection.

        Args:
            type (str): ``update`` for a minor update or ``upgrade`` for a major one.
            target_version (str): Expected firmware version (including hotfix revision).
            timeout (float): Overall deadline in seconds, including waiting for idle.
            poll_interval (float): Delay in seconds between status observations.

        Raises:
            OPNsenseInvalidArgument: Invalid action, target or timing argument.
            OPNsenseError: Concurrent install, rejected dispatch, failed/unconfirmed
                update, or deadline exceeded. Errors propagate regardless of
                ``throw_errors``. Cancellation releases local state and never
                cancels or reboots the server's firmware process.
        """
        if type not in ("update", "upgrade"):
            raise OPNsenseInvalidArgument("Firmware action must be update or upgrade")
        if (
            not isinstance(target_version, str)
            or not target_version.strip()
            or firmware_is_newer(target_version, target_version) is None
        ):
            raise OPNsenseInvalidArgument("A comparable target_version is required")
        for name, value in (("timeout", timeout), ("poll_interval", poll_interval)):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise OPNsenseInvalidArgument(f"{name} must be finite and positive")
        # No await between checking and setting the flag: reject concurrent calls
        # rather than queuing another destructive dispatch after the first one.
        if self._firmware_install_active:
            raise OPNsenseError("A firmware installation is already active on this client")
        self._firmware_install_active = True
        self._firmware_version = None
        try:
            async with asyncio.timeout(timeout):
                # An already-dispatched local check must finish before preflight.
                async with self._firmware_action_lock:
                    await self._wait_firmware_idle(poll_interval)
                    baseline = await self._firmware_request(FIRMWARE_UPGRADE_STATUS_ENDPOINT)
                    baseline_log = baseline.get("log", "")
                    if not isinstance(baseline_log, str):
                        raise OPNsenseError("Malformed pre-install firmware log")
                    try:
                        response = await self._firmware_request(
                            f"{FIRMWARE_ACTION_ENDPOINT_PREFIX}{type}", post=True
                        )
                    except OPNsenseError as err:
                        if isinstance(err, OPNsenseConnectionError) and err.status in (401, 403):
                            raise
                        # The request may have reached the daemon. Observe it;
                        # never retry a potentially accepted firmware action.
                        _LOGGER.warning("Firmware dispatch unconfirmed; observing without retry")
                    else:
                        if response.get("status") != "ok":
                            raise OPNsenseError(f"Firmware dispatch rejected: {response}")
                await self._observe_firmware_install(
                    type, target_version, baseline_log, poll_interval
                )
        except TimeoutError as err:
            raise OPNsenseError("Firmware installation deadline exceeded") from err
        finally:
            self._firmware_install_active = False

    async def _wait_firmware_idle(self, poll_interval: float) -> None:
        """Wait for the server's actual firmware lock before dispatch.

        Args:
            poll_interval (float): Delay between lock observations.

        Raises:
            OPNsenseError: The server does not provide a recognized lock state.
        """
        while True:
            running = await self._firmware_request(FIRMWARE_RUNNING_ENDPOINT)
            if running.get("status") == "ready":
                return
            if running.get("status") != "busy":
                raise OPNsenseError(f"Unknown firmware lock state: {running}")
            await asyncio.sleep(poll_interval)

    async def _observe_firmware_install(
        self, action: str, target: str, baseline_log: str, poll_interval: float
    ) -> None:
        """Observe a new action through terminal state and installed target.

        Args:
            action (str): Requested update or upgrade action.
            target (str): Minimum expected installed version.
            baseline_log (str): Progress log captured before dispatch.
            poll_interval (float): Delay between observations.

        Raises:
            OPNsenseError: Failed, unrelated, or unconfirmed firmware action.
            OPNsenseConnectionError: Authentication or permission failure.
        """
        marker = f"***GOT REQUEST TO {action.upper()}***"
        # The line following the marker includes the host version and timestamp.
        baseline_header = baseline_log.splitlines()[:2]
        start_deadline = asyncio.get_running_loop().time() + FIRMWARE_START_TIMEOUT_SECONDS
        started = reboot = disconnected = False
        active_header: list[str] = []
        while True:
            if not started and asyncio.get_running_loop().time() >= start_deadline:
                raise OPNsenseError("Firmware action did not start within 60 seconds")
            try:
                async with asyncio.timeout_at(
                    start_deadline if not started else None
                ) as start_guard:
                    progress = await self._firmware_request(FIRMWARE_UPGRADE_STATUS_ENDPOINT)
                    log = progress.get("log", "")
                    if not isinstance(log, str):
                        raise OPNsenseError("Malformed firmware progress log")
                    lines = log.splitlines()
                    if not started and log != baseline_log and lines[:2] != baseline_header:
                        if lines and lines[0] == marker:
                            if len(lines) >= 2:
                                started = True
                                active_header = lines[:2]
                                start_guard.reschedule(None)
                        elif lines and lines[0].startswith("***GOT REQUEST TO "):
                            raise OPNsenseError("Firmware progress belongs to a different action")
                    if started and not (reboot and disconnected):
                        if any(
                            failure in log.casefold()
                            for failure in (
                                "partial update failure",
                                "no further actions will be taken",
                            )
                        ):
                            raise OPNsenseError("Firmware installation reported a partial failure")
                        state = progress.get("status")
                        if log and lines[:2] != active_header:
                            raise OPNsenseError("Firmware action log was replaced")
                        if state == "reboot" and "***REBOOT***" in log:
                            # Record the marker before further reads: the GUI
                            # may disconnect immediately after returning it.
                            reboot = True
                        elif state not in ("running", "done") and not (
                            reboot and state == "error" and not log
                        ):
                            raise OPNsenseError(f"Firmware installation reported {state!r}")
                    running = await self._firmware_request(FIRMWARE_RUNNING_ENDPOINT)
                    status = await self._firmware_request(FIRMWARE_STATUS_ENDPOINT)
            except TimeoutError as err:
                raise OPNsenseError("Firmware action did not start within 60 seconds") from err
            except OPNsenseConnectionError as err:
                if err.status in (401, 403):
                    raise
                if reboot and err.status is None:
                    disconnected = True
                _LOGGER.debug("Firmware state temporarily unavailable: %s", err)
            else:
                lock_state = running.get("status")
                if lock_state not in ("ready", "busy"):
                    raise OPNsenseError(f"Unknown firmware lock state: {running}")
                if started:
                    terminal = (
                        reboot
                        and disconnected
                        or not reboot
                        and state == "done"
                        and "***DONE***" in log
                    )
                    if terminal and lock_state == "ready":
                        installed = dict_get(status, "product.product_version")
                        target_is_newer = firmware_is_newer(target, installed)
                        if target_is_newer is False:
                            self._firmware_version = installed
                            return
                        if not reboot:
                            raise OPNsenseError(
                                "Firmware completed without installing target version"
                            )
            if not started and asyncio.get_running_loop().time() >= start_deadline:
                raise OPNsenseError("Firmware action did not start within 60 seconds")
            delay = (
                poll_interval
                if started
                else min(poll_interval, max(0, start_deadline - asyncio.get_running_loop().time()))
            )
            await asyncio.sleep(delay)

    @deprecated(
        "Use install_firmware(target_version=...) to install firmware and verify completion."
    )
    @_log_errors
    async def upgrade_firmware(self, type: str = "update") -> MutableMapping[str, Any] | None:
        """Trigger a firmware upgrade without waiting for installation to complete.

        Deprecated: use ``install_firmware(target_version=...)`` to install
        firmware and verify completion.

        Args:
            type (str): Firmware action to trigger. ``update`` applies minor
                updates on the current series, while ``upgrade`` starts a major
                series upgrade.

        Returns:
            MutableMapping[str, Any] | None: Firmware action response for
                supported action types, or ``None`` when ``type`` is not
                ``update`` or ``upgrade``.

        Raises:
            OPNsenseError: A firmware installation is already active when
                ``_throw_errors`` is enabled.
        """
        # update = minor updates of the same opnsense version
        # upgrade = major updates to a new opnsense version
        if type in ("update", "upgrade"):
            async with self._firmware_action_lock:
                if self._firmware_install_active:
                    raise OPNsenseError("A firmware installation is already active on this client")
                self._firmware_version = None
                response = await self._do_post(
                    f"{FIRMWARE_ACTION_ENDPOINT_PREFIX}{type}", caller="upgrade_firmware"
                )
                return dict(response) if isinstance(response, MutableMapping) else {}
        return None

    @_log_errors
    async def upgrade_status(self) -> MutableMapping[str, Any]:
        """Return the status of the active firmware upgrade.

        Returns:
            MutableMapping[str, Any]: Upgrade status payload, or an empty
                mapping when the upgrade-status endpoint is unavailable.
        """
        if not await self._is_get_endpoint_available(FIRMWARE_UPGRADE_STATUS_ENDPOINT):
            _LOGGER.debug("Firmware upgrade status endpoint unavailable")
            return {}
        return await self._safe_dict_get(FIRMWARE_UPGRADE_STATUS_ENDPOINT)

    @_log_errors
    async def firmware_changelog(self, version: str) -> MutableMapping[str, Any]:
        """Return the changelog for the firmware upgrade.

        Args:
            version (str): Firmware version whose changelog should be fetched.

        Returns:
            MutableMapping[str, Any]: Changelog response for the requested
                firmware version.
        """
        return await self._safe_dict_post(f"{FIRMWARE_CHANGELOG_ENDPOINT_PREFIX}{version}")
