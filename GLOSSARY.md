# aiopnsense

An async client library that reads state from, and triggers supported actions on, an OPNsense firewall appliance over its REST API. Applications such as Home Assistant use it to monitor and control that appliance. Its main consumer, hass-opnsense, keeps a glossary of its own Home Assistant terms and uses the terms below with the same meaning.

## Language

### Network devices

**Router**:
The OPNsense appliance that the library connects to and manages. **Firewall** is an equally acceptable name for it.
_Avoid_: Device, box

**Firewall Rule**:
A rule on the Router that permits or blocks traffic. Always called a rule, so that "Firewall" on its own means the Router.
_Avoid_: Filter, ACL

**Host**:
A LAN endpoint, identified by its MAC address (hass-opnsense calls a Host it tracks a Tracked client), that the Router knows about through Neighbor Entries, DHCP Leases, or Host Observations. No single record is the main record of a Host; each is evidence about it.
_Avoid_: Device, machine

**Neighbor Entry**:
A MAC and IP pairing in the Router's IPv4 (ARP) or IPv6 (NDP) neighbor table, showing a Host the Router has recently exchanged traffic with. It shows an address was seen, not that the Host is present.
_Avoid_: ARP/NDP Entry, ARP row

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

### Actions

**Toggle**:
Switching a Router item, such as a VPN Instance, Firewall Rule or alias, on or off, either to an explicit target state or by flipping its current state. Making the change take effect on the Router is part of the Toggle, never a separate step.
_Avoid_: Apply, enable/disable (as separate operations)

### Access

**Status Access**:
The level at which the Router lets the API user read the live status of a category, or of one VPN type, but denies (403) a read that completes its result. Usually the denied read is the configured inventory; for DHCP leases it can also be one backend's lease endpoint. A Status Access result can therefore lack items or detail that the denied read would add, such as disabled VPN Instances or CARP VIP settings, and nothing can be Toggled. Callers treat it as possibly incomplete. The Router's answers decide the level, not a user setting.
_Avoid_: Read-only, status-only user

**Configuration Access**:
The level at which the API user can also read the configured inventory of a category, or of one VPN type, and Toggle its items.
_Avoid_: Read/write, full access

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
