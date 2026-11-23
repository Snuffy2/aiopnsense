# Firmware reads never contact upstream, and one guarded path requests a check

OPNsense firmware endpoints that look like harmless reads can make the Router contact its package mirrors. hass-opnsense polls aiopnsense every 30 seconds by default, so one wrong endpoint, HTTP method or always-true condition turns into thousands of upstream checks per day for every install. To prevent this, aiopnsense follows two rules:

- **Reading firmware details:** it uses `GET /api/core/firmware/status`, which returns the result of the last check without contacting the mirrors.
- **Looking for new firmware:** it sends `POST /api/core/firmware/check`. During polling, only `get_firmware_update_info` may send this request, and an acknowledged check prevents another for one hour.

| Request | Effect on the Router |
| --- | --- |
| `GET /api/core/firmware/status` | Returns the result of the last check. No upstream contact. |
| `POST /api/core/firmware/status` | Runs a full check synchronously, then returns the result. |
| `POST /api/core/firmware/check` | Starts a full check in the background. |
| `GET` or `POST /api/core/firmware/info` | Runs `pkg update` against the mirrors on either method. |
| `GET /api/core/firmware/running` | Reports whether the firmware lock is `ready` or `busy`. No upstream contact. |

This was confirmed against `FirmwareController.php`, `actions_firmware.conf` and `query.sh` in OPNsense core. The `status` and `check` behavior is the same on `stable/25.1`, the Minimum Firmware.

## Considered Options

- **Read the installed version from `/info`.** Rejected: it contacts the mirrors on every call. This caused the first reports.
- **Use `POST /status` for fresh data.** Rejected: it blocks for seconds while the check runs, and a poll would run one each time.
- **Never request a check and rely on the Router's scheduled firmware poll.** Rejected: OPNsense's default cron only fetches the changelog, and the scheduled poll is optional. Status can stay empty or incomplete until a check runs, and the update entity needs those details.
- **Fix each repeating condition without a rate limit.** Rejected: this is what past fixes did, and each new condition brought the problem back.

## Consequences

- A new firmware read must use a request that the table above marks as having no upstream contact. Any other endpoint or method must first be traced through its controller action and configd script to show it does not reach `pkg update`. An endpoint's name and HTTP method do not establish this.
- `aiopnsense/_firmware_request_guard.py` checks every HTTP request before it is sent. It blocks `/info` on any method and `POST /status`. It judges the path aiohttp would send, so how the string was built does not matter, and it ignores the query, fragment, letter case, repeated or trailing slashes and any segments after the action, and resolves dot segments and percent-encoding. A blocked request sends nothing and follows the client's `throw_errors` setting: it raises `OPNsenseInvalidArgument`, or logs and returns the empty value. `_firmware_request`, used by firmware installation, always raises it. The guard does not inspect a redirect returned by the Router or model any other routing alias the Router might accept.
- `tests/test_firmware_endpoint_guard.py` tests that boundary and keeps two static checks for what it cannot see: every `_session` request goes through it, and the check endpoint stays in its one dispatch in `FirmwareMixin._request_firmware_check` while the `/api/core/firmware/` action prefix stays in `install_firmware` and `upgrade_firmware`. The static checks match the constant names and the exact literals `/api/core/firmware/check`, `/api/core/firmware/update`, `/api/core/firmware/upgrade` and `/api/core/firmware/`, so a path built at runtime or spelled differently, such as with a trailing slash, query or different letter case, is not detected. Other firmware actions are not restricted.
- A check is requested only when the status is an error, the last check is older than one day or missing or unreadable, data is missing, a newer version has no details, or versions cannot be compared. Some of these conditions cannot be cleared by a check, such as a mirror outage, a non-comparable version or an unreadable `last_check`, so each client requests at most one automatic check per hour regardless of the status. The cooldown is the backstop for the next condition nobody anticipated.
- The cooldown starts only when the Router acknowledges a check with `status: ok` and a non-empty `msg_uuid`. OPNsense returns `status: ok` even when configd fails, and then `msg_uuid` is empty. A failed, rejected or cancelled request and a `busy` Router do not start it. Dispatching an update or upgrade ends it, because that flushes the Router's availability data. Known limit: a request that repeatedly reaches the Router without returning a usable response is retried on each poll. During the cooldown, a poll whose status needs a refresh still clears the cached version when the version the normal lookup would use, including the `product_series` fallback for a non-SemVer product version, has changed to a comparable version, without sending a check.
- The cooldown lives in memory on one client. A restart or a second client can request its own check. After an acknowledged check that did not produce update details, they can take up to an hour to appear.
- Endpoint status checks use GET. Firmware reads never dispatch `POST /api/core/firmware/check`.
- Other clients and the web GUI can also start checks, so the `running` test does not reserve the firmware lock (see `docs/diagnostics/firmware-update-debugging.md`).

## History

| Date | Report | Cause | Fix |
| --- | --- | --- | --- |
| 2024-11 | hass-opnsense #315, #322 | Version reads moved from `status` to `/info` in #288 | Returned to `GET status` |
| 2025-07 | #418 | Rewritten trigger logic in #393 | #419 |
| 2026-03 | #504 | Cause not confirmed | Closed by hass-opnsense #510, which cached firmware-version reads |
| 2026-04 | #584 | Plugin detection called `/info` on every poll | Cached in #574 (v0.6.6) |
| 2026-08 | #692 | Not confirmed: closed without diagnostics, reporter was on hotfix 26.7.1_1 | None recorded |
| 2026-10 | #763 | Hotfix `26.7.6_3` compared as older than `26.7.6`, so every poll saw a missing update | aiopnsense #146 |
