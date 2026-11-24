# aiopnsense

An async client library that reads state from, and triggers supported actions on, an OPNsense firewall appliance over its REST API. Applications such as Home Assistant use it to monitor and control that appliance.

## Language

### Network devices

**Router**:
The OPNsense appliance that the library connects to and manages. **Firewall** is an equally acceptable name for it.
_Avoid_: Device, box

**Firewall Rule**:
A rule on the Router that permits or blocks traffic. Always called a rule, so that "Firewall" on its own means the Router.
_Avoid_: Filter, ACL

**Host**:
A LAN endpoint, identified by its MAC address, that the Router knows about through ARP/NDP Entries, DHCP Leases, or Host Observations. No single record is the main record of a Host; each is evidence about it.
_Avoid_: Device, machine

**ARP/NDP Entry**:
A MAC and IP pairing in the Router's IPv4 (ARP) or IPv6 (NDP) neighbor table, showing a Host the Router has recently exchanged traffic with.
_Avoid_: Neighbor, ARP row

**DHCP Lease**:
An address the Router's DHCP server assigned to a Host, with its hostname and expiry when known. It does not show that the Host is present.
_Avoid_: Reservation (a reservation is a configured future assignment, not a lease)

**Host Observation**:
A timestamped sighting of a Host's MAC and IP by the Router's Host Discovery service, recording when the Host was last seen.
_Avoid_: Sighting, discovery entry

### VPN

**VPN Instance**:
A single independently enable-able VPN configuration entry on the Router: an OpenVPN instance, a WireGuard server, a WireGuard client, or an IPsec connection.
_Avoid_: Tunnel, VPN connection

**WireGuard Server**:
A local WireGuard interface on the Router that accepts connections from remote endpoints.
_Avoid_: Instance, interface

**WireGuard Client**:
A remote WireGuard endpoint configured on the Router and linked to one or more WireGuard Servers.
_Avoid_: Peer

### DNS

**Query Log**:
The Router's record of individual Unbound DNS queries, with client, domain, action, blocklist and policy. Only its last 1,000 queries are searchable, so it covers minutes of traffic, not days.
_Avoid_: DNS log, query history

**Query Stats**:
Aggregated counts of Unbound DNS queries: per-Bucket counters plus Retention Window totals and top-domain lists.
_Avoid_: DNS overview, DNS statistics (as a type name)

**Bucket**:
One fixed-width time slot of Query Stats, keyed by its start time. Buckets are 60 seconds wide when reading the last hour and 600 seconds wide otherwise.
_Avoid_: Interval, slot

**Complete Bucket**:
A Bucket whose time slot has ended. The newest Bucket the Router returns is still filling and is never reported as a count.
_Avoid_: Closed bucket, last bucket

**Retention Window**:
The span of query history the Router keeps, about a week. Totals and top-domain lists cover this window, so their counts can fall as old queries age out.
_Avoid_: All time, since boot

**Blocked**:
A query answered by a DNSBL blocklist policy. Counts of Blocked queries never include Dropped ones.
_Avoid_: Filtered, denied

**Dropped**:
A query Unbound discarded for a reason other than a blocklist, such as a rate limit or an access list.
_Avoid_: Blocked, rejected

### Actions

**Toggle**:
Switching a Router item, such as a VPN Instance, Firewall Rule or alias, on or off, either to an explicit target state or by flipping its current state. Making the change take effect on the Router is part of the Toggle, never a separate step.
_Avoid_: Apply, enable/disable (as separate operations)

### Firmware support

**Minimum Firmware**:
The oldest OPNsense release the library supports. Routers running an older release are rejected.
_Avoid_: Required firmware

**Recommended Firmware**:
The oldest OPNsense release on which every library feature works. Routers running an older release are accepted, but some features return empty or missing data.
_Avoid_: LTD firmware, limited firmware

**Firmware Capability**:
A behavior of the library that depends on the Router's firmware release, such as which Endpoint Style it calls or which Unbound blocklist method it uses. Each has a threshold release and a fixed fallback for Unknown Firmware.
_Avoid_: Feature flag, version gate

**Unknown Firmware**:
A Router whose firmware release is missing or cannot be compared. Startup validation rejects it; everywhere else each Firmware Capability uses its fallback and checks again on its next use.
_Avoid_: Invalid firmware, undetected firmware

**Endpoint Style**:
The naming form, snake_case or camelCase, that a Router's API endpoints use for its firmware release. Older Routers use camelCase and newer ones use snake_case.
_Avoid_: Endpoint format, API version
