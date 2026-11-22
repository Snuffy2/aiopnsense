# Firmware reads never contact upstream, and one guarded path requests a check

OPNsense firmware endpoints that look like harmless reads can make the Router contact its package mirrors. hass-opnsense polls aiopnsense every 30 seconds by default, so one wrong endpoint, HTTP method or always-true condition turns into thousands of upstream checks per day for every install. aiopnsense therefore reads firmware state only with `GET /api/core/firmware/status`, and `get_firmware_update_info` is the only polling path allowed to request a check, using `POST /api/core/firmware/check`.

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
- **Never request a check and rely on the Router's daily cron.** Rejected: the status can be empty or incomplete until a check runs, and the update entity needs those details.
- **Fix each repeating condition without a rate limit.** Rejected: this is what past fixes did, and each new condition brought the problem back.

## Consequences

- A new firmware read must use an endpoint and method from the table above, or first be traced through its controller action and configd script to show it does not reach `pkg update`. An endpoint's name and HTTP method do not establish this.
- A check is requested only when the status is an error, older than one day, missing data, or reports a newer version without details. Some of these conditions cannot be cleared by a check, such as a mirror outage, a non-comparable version or an invalid `last_check`, so each client requests at most one automatic check per hour regardless of the status. The cooldown is the backstop for the next condition nobody anticipated.
- The cooldown starts when a check POST is attempted, because a failed or cancelled POST may still have reached the Router. A Router that reports `busy` does not start it. A verified `install_firmware` ends it, because the update flushes the Router's availability data.
- The cooldown lives in memory on one client. A restart or a second client can request its own check, and update details can take up to an hour to appear after a failed check.
- `check` is on the unsafe POST probe list, so an availability probe can never start one.
- Other clients and the web GUI can also start checks, so the `running` test does not reserve the firmware lock (see `docs/diagnostics/firmware-update-debugging.md`).

## History

| Date | Report | Cause | Fix |
| --- | --- | --- | --- |
| 2024-11 | hass-opnsense #315, #322 | Version reads moved from `status` to `/info` in #288 | Returned to `GET status` |
| 2025-07 | #418 | Rewritten trigger logic in #393 | #419 |
| 2026-03 to 04 | #504, #584 | Plugin detection called `/info` on every poll | Cached in #574 (v0.6.6) |
| 2026-08 | #692 | Not confirmed: closed without diagnostics, reporter was on hotfix 26.7.1_1 | None recorded |
| 2026-10 | #763 | Hotfix `26.7.6_3` compared as older than `26.7.6`, so every poll saw a missing update | aiopnsense #146 |
