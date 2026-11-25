# Query Stats report Complete Buckets only, and the library computes derived values

OPNsense's `unbound/overview/rolling/<hours>` returns Buckets keyed by start time, and the newest one is still filling. Reported as a count, that partial Bucket reads low and then jumps, and every caller would have to know to drop it. aiopnsense drops it. `get_unbound_query_stats` reports the last Complete Bucket of the last hour, sums the Complete Buckets of the last 24 hours, and exposes the Blocked share as a property, so callers such as hass-opnsense read finished numbers and never see raw Buckets.

## Considered Options

- **Return raw Buckets per endpoint and let callers sum them.** Rejected: the still-filling rule, the Bucket widths (60 s for one hour, 600 s otherwise) and the trailing dot on domains are OPNsense knowledge, and every caller would have to repeat it.
- **Use Retention Window totals for 24-hour figures.** Rejected: the totals span about a week and can fall as old queries age out, so they are neither 24-hour figures nor monotonic.

## Consequences

- The 24-hour sums leave out the newest, still-filling 10-minute Bucket, so they lag by up to 10 minutes.
- Blocked counts DNSBL hits only. Dropped is reported separately from Buckets; Retention Window totals have no Dropped figure.
- With errors disabled, both reads keep three cases apart, so a caller such as hass-opnsense can create entities once a read is not absent and mark them unavailable instead of reporting zero queries:
  - **Read failed this time** (a transient failure, or a body that cannot be decoded or has the wrong shape): `None`, as from `@_log_errors` and under [ADR 0004](0004-results-report-their-access-level.md).
  - **Endpoint absent** (404, or 403 including a cached 403): a `QueryStats` or `QueryLog` with no data and `absent` set to `"not_found"` or `"forbidden"`.
  - **Data:** `absent` is `None`. Empty `QueryLog.entries` means nothing matched.
- No Firmware Capability gates these reads, because every supported release has the endpoints.
