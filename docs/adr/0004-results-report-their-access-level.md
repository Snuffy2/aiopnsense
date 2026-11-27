# Results report their access level on the result object, detected from the Router

An API user may have Status Access to a category, or to one VPN type, without Configuration Access. A forbidden (403) configuration read then means "this user may not see the configured inventory", not "nothing is configured". A caller such as hass-opnsense, which deletes entities missing from an inventory it believes complete, must be able to tell the two apart. aiopnsense therefore returns the data that Status Access allows and reports the access level with it: a getter that supports access reporting returns a `dict` or `list` subclass carrying the level (Status Access or Configuration Access), read through an exported `result_access()` helper. The level is detected from the Router's answers, never set by the caller. A 403 on an access-determining configuration endpoint means Status Access. A 404 on a configuration endpoint still means the endpoint is missing and its inventory is empty. A 403 or other failure on the primary status endpoint returns `None`, with no level.

The first getters to report are `get_openvpn`, `get_wireguard` and `get_ipsec`. Other categories adopt the same shape when they gain Status Access support.

## Mixed VPN configuration failures

The original VPN Status Access spec preserved the old behavior for all transient failures and malformed payloads. With `throw_errors=False`, that rule now applies only to data required for the result. A definitive configuration denial, including a cached 403, lets a getter use the status payload even if another configuration read fails or is malformed. A transient failure or malformed payload alone cannot establish Status Access.

- OpenVPN determines access from `instances/search`. After a 403, the getter skips provider export and instance details. Routes remain optional. A provider 403 alone omits provider enrichment and leaves Configuration Access.
- Either WireGuard configuration endpoint can establish Status Access. The getter checks the other endpoint after a suppressed failure to look for a 403. Readable summary rows are required at either access level.
- IPsec determines access from `connections/search_connection`. After a 403, the getter skips child configuration and builds `children` from phase 2 sessions. If any required phase 2 session list is unreadable, the result still includes readable phase 1 `connections` and omits `children`. A child configuration 403 alone leaves Configuration Access.

A configuration 404 still supplies an empty inventory. Strict mode still propagates request errors in the original order.

## DHCP leases

`get_dhcp_leases` also reports its access level and follows the unknown-inventory rule below. It combines several DHCP backends, so one level covers the whole result. Its `lease_interfaces` inventory comes from the lease endpoints (Kea `leases4/search` and `leases6/search`, Dnsmasq `leases/search`, ISC `dhcpv4/leases/search_lease` and `dhcpv6/leases/search_lease`) and from two configuration endpoints that list interfaces a backend serves even without a lease: Kea `dhcpv4/get` and Dnsmasq `settings/search_range`. A 403 on any of these, including a cached 403, means Status Access: that endpoint contributes nothing, so the leases and interfaces it would have supplied may be missing, while every other source is still returned. Otherwise the result has Configuration Access.

- A 403 alone decides, without first proving the backend is in use, because a denied configuration read cannot show whether it is. A user denied a backend it does not use therefore always gets Status Access, which only makes a caller keep entities it could have removed.
- A 403 on Kea `dhcpv4/search_reservation` only drops reservation detail and leaves Configuration Access.
- A 404 on any of these endpoints still means the backend is missing and adds nothing.
- With `throw_errors=False`, a transient failure or malformed payload on a lease endpoint makes the result unknown (`None`), since its leases and lease counts cannot be trusted at either level. On a configuration endpoint it does the same, unless a 403 elsewhere already established Status Access, which tells the caller the interface inventory may be incomplete anyway. Neither can establish Status Access.
- ISC DHCP has no configuration endpoint that lists its interfaces, so it never affects the level.
- The lease endpoints are status endpoints, but none is primary: a Router normally lacks some backends, and an API user may be granted some backends and not others. A lease 403 therefore leaves the other backends' data and reports Status Access instead of returning `None`.

In the stock OPNsense ACL definitions, one privilege grants both the settings and the lease endpoints of each backend: "Services: DHCP: Kea(v4)" covers `api/kea/dhcpv4/*` and `api/kea/leases4/*` (25.1 through 26.7), and "Services: Dnsmasq DNS/DHCP: Settings" covers all of `api/dnsmasq/*` (25.7, which added that API, through 26.7). A user who can read Kea DHCPv4 or Dnsmasq leases can therefore read that backend's configuration too, so Status Access does not occur for a backend whose leases are visible. It occurs when the API user lacks a backend's privilege entirely, such as a user with only the Kea(v6) privilege, whose DHCPv4 interfaces are then unknown.

## CARP

`get_carp` reports its access level too. OPNsense grants the two CARP reads through separate privileges: `diagnostics/interface/get_vip_status` under "Interfaces: Virtual IPs: Status" and `interfaces/vip_settings/*` under "Interfaces: Virtual IPs: Settings".

- The VIP inventory comes from `get_vip_status` at both levels. That endpoint lists every configured CARP VIP, including disabled ones, so a Status Access result is not missing VIPs.
- `interfaces/vip_settings/get` determines access. A 403, including a cached 403, means Status Access, and the result omits the settings enrichment of each VIP: fields the status rows lack, such as `descr` and `subnet_bits`. Data or a 404 means Configuration Access. A transient settings failure leaves Configuration Access with status-only VIP data, because a failure alone cannot establish Status Access.
- A failed `get_vip_status` read, or a reply without a `rows` list, returns `None` with no level, instead of an empty snapshot that a caller could take for "no VIPs". Strict mode raises the status error first, then the settings error.

## Unknown inventories

The 404-versus-failure rule applies to every getter whose result is an inventory a caller reconciles, whether or not the category supports Status Access. hass-opnsense removes entities missing from an inventory it believes complete, so an empty result must mean the Router has nothing to report, never that the read failed.

With `throw_errors=False`:

- A 404 on the endpoint that supplies an inventory means the feature is missing. The getter returns an empty inventory, or the feature's existing missing marker, such as `{"available": False}` from `get_speedtest`.
- A 403 on that endpoint, including a cached 403, a transient failure, and an undecodable or wrongly shaped body all make the inventory unknown. The getter returns `None`. Callers treat `None` as unknown and defer reconciliation.
- When several endpoints each contribute items to one inventory, as the vnStat periods do, each must return data or a 404. Otherwise the inventory is `None`.
- When a getter returns independent inventories in one result, the rule applies to each one. `get_telemetry` sets only the affected `filesystems` or `temps` to `None` and leaves the other values unchanged. `get_firewall` does the same for each rule group: `rules` and each NAT type under `nat` (`d_nat`, `one_to_one`, `source_nat`, `npt`) come from their own configuration endpoint, so one group can be `None` while the others keep their rules. OPNsense grants these as separate privileges (Firewall: Rules, and Firewall: NAT: Destination NAT, 1:1, Source NAT and NPTv6 in its ACL definitions), so an API user can be denied one group and not the others.
- A read that only adds detail to items already listed does not make the inventory unknown. Its fields stay unset, as with Speedtest averages from `showstat`, or the failed item's detail is `None`, as with one device's `get_smart_info` result under its key in `smart_info`.

Strict mode keeps each getter's existing exceptions and their order.

Getters that follow this rule: `get_services`, `get_unbound_blocklist`, `get_interfaces`, `get_gateways`, the `filesystems` and `temps` values of `get_telemetry`, `get_smart`, `get_smart_info`, `get_nut_ups_status`, `get_speedtest`, `get_vnstat`, `get_carp`, the rule groups of `get_firewall` and `get_dhcp_leases` (see DHCP leases). Other inventory getters adopt it when they are next changed.

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
- The WireGuard status endpoint exposes no UUIDs, so every WireGuard Server and WireGuard Client entry carries a `key_hash` derived from its public key, at both access levels. Under Configuration Access, entries stay keyed by UUID. Under Status Access, they have no UUID: WireGuard Clients are keyed by `key_hash`, and WireGuard Servers by `<interface>:<key_hash>` (for example `wg0:cc021956c09576eb`). Callers that need one identity across both levels use `key_hash`, plus `interface` for a WireGuard Server, so the `key_hash` derivation is part of the public contract: changing it changes every identity built from it.
- WireGuard Servers were first keyed by `key_hash` alone under Status Access, merging instances that share a public key into one entry. OPNsense allows such instances (its Server model has had no public-key uniqueness constraint since opnsense/core#7229), and the merged entry could not be split back into instances, so each instance is now its own entry with its own interface, status, name, client links and counters. Interface alone is not an identity because OPNsense gives a new instance the lowest free number, so it can reuse a deleted instance's `wgN`. The key uses the same format whether or not the public key is shared, so adding a duplicate never changes an existing entry's key, and callers read identity from the `interface` and `key_hash` fields instead of parsing the key. Configuration Access matches each Server's live status by interface and public key for the same reason. WireGuard Clients keep one entry per public key, because live status cannot tell two Clients sharing a key from one Client linked to several Servers. OPNsense requires a Client public key to be unique only together with the Client's configured endpoint (opnsense/core#7213), so under Configuration Access several WireGuard Clients can share a `key_hash`, while Status Access reports them as one. A caller that needs the same Client identity at both levels treats Configuration Access Clients sharing a `key_hash` as one Client. When the status rows for one key carry different Client names, the Status Access entry takes the name of a row with its own name, choosing the first interface in lexical order among ties, so the name does not depend on row order. The entry also lists the sorted distinct names of its rows in `names`: more than one name shows that the key is shared, but one name does not prove it is not, because Clients with the same name, or linked to the same Server, report one name.
