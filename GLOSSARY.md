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
