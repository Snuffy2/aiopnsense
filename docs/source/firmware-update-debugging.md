# Firmware update debugging: 26.7.7 to 26.7.8

This incident was investigated on 2026-10-30. The two failed Home Assistant
attempts used hass-opnsense v1.3.0 with aiopnsense 1.3.2. A subsequent single
authorized CLI update succeeded. This document records the API contract and
observations needed to reproduce the failure safely in automated tests.

The [deidentified capture](../diagnostics/firmware-update-26.7.8.json) contains
the request/state transitions and complete terminal update log. Credentials,
cookies, connection identifiers, absolute event times, and request UUIDs are
excluded. Package and repository names are retained for technical context.

## Failed Home Assistant attempts

Router configd and audit logs established these relative sequences:

| Event | Attempt 1 | Attempt 2 |
| --- | ---: | ---: |
| Firmware check started | -9 seconds | -4 seconds |
| Home Assistant requested update | 0 seconds | 0 seconds |
| First upgrade-progress poll | +10 seconds | +10 seconds |
| Home Assistant requested system reboot | +11 seconds | +10 seconds |

Both attempts left firmware at 26.7.7. The exact progress payloads were not
retained, so contention remains a strongly supported explanation rather than
a directly recorded lock failure. No package/download error from either
attempt was recovered. An older retained major-upgrade log was unrelated and
must not be used to explain these attempts.

## Observed server contract

These behaviors were checked against the router's installed controller and
scripts, in addition to the live API capture:

- `GET /api/core/firmware/running` returns `ready` or `busy` based on the
  firmware lock. Both checks and installations use this lock.
- `POST /api/core/firmware/update` flushes cached availability data and
  dispatches the update launcher. Its `ok` and `msg_uuid` acknowledge dispatch,
  not successful lock acquisition or installation.
- The launcher uses nonblocking `flock -n`. An overlapping check can prevent
  the update script from running. There is no atomic cross-client reservation
  provided by the ready check and update POST.
- `GET /api/core/firmware/upgradestatus` reads the shared progress log. The
  first line identifies `CHECK FOR UPDATES`, `UPDATE`, or `UPGRADE`; the next
  line records the running version and start time. A completed check can
  therefore return `done` from this endpoint.
- `done` also occurs after a partial update failure. Terminal state alone
  does not prove success; the log's failure messages must be checked.
- Availability flags such as `needs_reboot` describe the offered update's
  requirements, not successful installation or permission to reboot now.
- `***REBOOT***` is written by the installer before OPNsense invokes its own
  reboot. A client must not issue an additional system reboot.

Relevant server sources: `FirmwareController.php`, firmware `launcher.sh`,
`running.sh`, `read.sh`, `config.sh`, and `update.sh`. No recent API change
was established as the cause. Local history shows the unsafe Home Assistant
completion/reboot logic predates this incident, consistent with a timing
dependent race rather than proof of a new API regression. The
[OPNsense 25.1 controller](https://github.com/opnsense/core/blob/25.1/src/opnsense/mvc/app/controllers/OPNsense/Core/Api/FirmwareController.php)
and [25.1 firmware script](https://github.com/opnsense/core/blob/25.1/src/opnsense/scripts/firmware/config.sh)
already expose the busy/ready endpoint, timestamped action markers, and
server-managed reboot used by the fix. These are not new 26.7-only assumptions.

## Successful single-attempt capture

The capture waited for `ready`, recorded the baseline progress log, dispatched
exactly one POST update, and then used only GET requests. A persistent local
guard prevented accidental redispatch. Neither the capture nor Home Assistant
requested a reboot. The target was confirmed independently over SSH afterward.

| Elapsed time | Observation |
| --- | --- |
| 0.31 s | POST returned `ok`; a new `UPDATE` log appeared with `running`/`busy` |
| 29.31 s | Product version already read 26.7.8 while installation remained busy |
| 64.10 s | Installer was still installing base/kernel files |
| 66.34 s | Matching update log reached `***REBOOT***`; lock remained busy |
| 81–115 s | API requests timed out during OPNsense's automatic reboot |
| 121.79 s | Version 26.7.8, firmware `ready`, progress `error` with empty log |

The last `error` reflects the vanished temporary progress file after reboot;
it does not mean installation failed. Conversely, observing the new product
version at 29 seconds did not mean installation was complete. The SSH boot
time changed, independently confirming the reboot.

## Required regression cases

1. An existing check owns the lock: wait before dispatch, never infer an
   installation from a completed check.
2. Dispatch is rejected, loses its response, or does not create a new matching
   update log: fail safely and never retry the mutating POST automatically.
3. The product version changes before base/kernel work finishes: keep waiting.
4. A matching log reports partial failure followed by `done`: report failure.
5. A matching update completes without reboot: verify idle state and target.
6. A matching update reports `reboot`: wait through the actual reboot and
   verify the target after reconnecting; tolerate the empty postboot log.
7. Timeout or cancellation: release local coordination state and never issue
   a reboot or retry an ambiguously dispatched installation.

Firmware status reads used during installation must not trigger fresh checks.
Other clients can still start checks, so local serialization alone cannot
replace confirmation of the newly dispatched operation.
