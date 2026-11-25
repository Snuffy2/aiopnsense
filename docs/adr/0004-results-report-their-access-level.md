# Results report their access level on the result object, detected from the Router

An API user may have Status Access to a category, or to one VPN type, without Configuration Access. A forbidden (403) configuration read then means "this user may not see the configured inventory", not "nothing is configured". A caller such as hass-opnsense, which deletes entities missing from an inventory it believes complete, must be able to tell the two apart. aiopnsense therefore returns the data that Status Access allows and reports the access level with it: a getter that supports access reporting returns a `dict` or `list` subclass carrying the level (Status Access or Configuration Access), read through an exported `result_access()` helper. The level is detected from the Router's answers, never set by the caller. A 403 on an access-determining configuration endpoint means Status Access. A 404 on a configuration endpoint still means the endpoint is missing and its inventory is empty. A 403 or other failure on the primary status endpoint returns `None`, with no level.

The first getters to report are `get_openvpn`, `get_wireguard` and `get_ipsec`. Other categories adopt the same shape when they gain Status Access support.

## Mixed VPN configuration failures

The original VPN Status Access spec preserved the old behavior for all transient failures and malformed payloads. With `throw_errors=False`, that rule now applies only to data required for the result. A definitive configuration denial, including a cached 403, lets a getter use the status payload even if another configuration read fails or is malformed. A transient failure or malformed payload alone cannot establish Status Access.

- OpenVPN determines access from `instances/search`. After a 403, the getter skips provider export and instance details. Routes remain optional. A provider 403 alone omits provider enrichment and leaves Configuration Access.
- Either WireGuard configuration endpoint can establish Status Access. The getter checks the other endpoint after a suppressed failure to look for a 403. Readable summary rows are required at either access level.
- IPsec determines access from `connections/search_connection`. After a 403, the getter skips child configuration and builds `children` from phase 2 sessions. If any required phase 2 session list is unreadable, the result still includes readable phase 1 `connections` and omits `children`. A child configuration 403 alone leaves Configuration Access.

A configuration 404 still supplies an empty inventory. Strict mode still propagates request errors in the original order.

## CARP

`get_carp` reports its access level too. OPNsense grants the two CARP reads through separate privileges: `diagnostics/interface/get_vip_status` under "Interfaces: Virtual IPs: Status" and `interfaces/vip_settings/*` under "Interfaces: Virtual IPs: Settings".

- The VIP inventory comes from `get_vip_status` at both levels. That endpoint lists every configured CARP VIP, including disabled ones, so a Status Access result is not missing VIPs.
- `interfaces/vip_settings/get` determines access. A 403, including a cached 403, means Status Access, and the result omits the settings enrichment of each VIP: fields the status rows lack, such as `descr` and `subnet_bits`. Data or a 404 means Configuration Access. A transient settings failure leaves Configuration Access with status-only VIP data, because a failure alone cannot establish Status Access.
- A failed `get_vip_status` read returns `None` with no level, instead of an empty snapshot that a caller could take for "no VIPs". Strict mode raises the status error first, then the settings error.

## Unknown inventories

The 404-versus-failure rule applies to every getter whose result is an inventory a caller reconciles, whether or not the category supports Status Access. hass-opnsense removes entities missing from an inventory it believes complete, so an empty result must mean the Router has nothing to report, never that the read failed.

With `throw_errors=False`:

- A 404 on the endpoint that supplies an inventory means the feature is missing. The getter returns an empty inventory, or the feature's existing missing marker, such as `{"available": False}` from `get_speedtest`.
- A 403 on that endpoint, including a cached 403, a transient failure, and an undecodable or wrongly shaped body all make the inventory unknown. The getter returns `None`. Callers treat `None` as unknown and defer reconciliation.
- When several endpoints each contribute items to one inventory, as the vnStat periods do, each must return data or a 404. Otherwise the inventory is `None`.
- When a getter returns independent inventories in one result, the rule applies to each one. `get_telemetry` sets only the affected `filesystems` or `temps` to `None` and leaves the other values unchanged. `get_firewall` does the same for each rule group: `rules` and each NAT type under `nat` (`d_nat`, `one_to_one`, `source_nat`, `npt`) come from their own configuration endpoint, so one group can be `None` while the others keep their rules. OPNsense grants these as separate privileges (Firewall: Rules, and Firewall: NAT: Destination NAT, 1:1, Source NAT and NPTv6 in its ACL definitions), so an API user can be denied one group and not the others.
- A read that only adds detail to items already listed does not make the inventory unknown. Its fields stay unset, as with Speedtest averages from `showstat`, or the failed item's detail is `None`, as with one device's `get_smart_info` result under its key in `smart_info`.

Strict mode keeps each getter's existing exceptions and their order.

Getters that follow this rule: `get_services`, `get_unbound_blocklist`, `get_interfaces`, `get_gateways`, the `filesystems` and `temps` values of `get_telemetry`, `get_smart`, `get_smart_info`, `get_nut_ups_status`, `get_speedtest`, `get_vnstat` and the rule groups of `get_firewall`. Other inventory getters adopt it when they are next changed.

## Considered Options

- **A top-level `access` key in the result mapping.** Rejected: additive for mapping results, but a category whose getter returns a list could only adopt it by wrapping its result, a breaking change for each such category.
- **A separate client call reporting the last access level.** Rejected: the level is not tied to the data it describes, so an interleaved call to the same getter can make them disagree, and every caller must remember to ask immediately after each read.
- **Returning `None` for a forbidden configuration read.** Rejected where a status endpoint can still supply data: safe against deletion, but it discards the status data a Status Access user is entitled to and leaves a downstream inventory permanently incomplete. When no status endpoint exists, nothing is discarded, and `None` is the answer (see Unknown inventories).
- **A user-chosen access level in the downstream sync options.** Rejected: it can disagree with the Router's actual permissions, and choosing a level the Router does not grant has no sensible meaning.

## Consequences

- Results still compare equal to, and pass `isinstance` checks as, plain `dict` and `list` values, so existing callers and tests are unaffected. Declared return types stay unchanged.
- The level survives `copy.deepcopy` and pickling, but a plain copy (`dict(x)`, `{**x}`, `list(x)`, JSON) silently drops it. `result_access()` then returns `None`, meaning "not reported", which callers must treat as the behavior from before access reporting existed.
- Getters that follow the unknown-inventory rule can now return `None` where they returned an empty value, so every caller must handle `None`. hass-opnsense already does. Home Assistant Core's OPNsense integration calls `get_interfaces()` during entry setup and config flow and iterates the result, so it must handle `None` before it raises its aiopnsense version requirement.
- While a read keeps failing, for example because the API user is permanently denied a category that is still enabled in hass-opnsense, setup never sees a complete inventory, so hass-opnsense's Device ID repair stays pending. Completing it would need hass-opnsense to reconcile the inventories it can read and keep the rest.
- A Router that answers a failed read with an empty HTTP 200 body is still indistinguishable from an empty inventory.
- Under Status Access, items the status does not show, such as disabled VPN Instances, are absent, and nothing can be Toggled.
- The WireGuard status endpoint exposes no UUIDs, so every WireGuard Server and WireGuard Client entry carries a `key_hash` derived from its public key, at both access levels. Under Status Access, entries are keyed by `key_hash` with no UUID; under Configuration Access, they stay keyed by UUID. Callers that need one identity across both levels use `key_hash`, so its derivation is part of the public contract: changing it changes every identity built from it.
