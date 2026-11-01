DHCP, ARP, and NDP
==================

Timestamped host observations
-----------------------------

To use ``get_host_observations()``, enable automatic host discovery on OPNsense
and grant the API user ``Interfaces: Neighbors: Automatic discovery``.
Group observations from either address family by MAC and apply your own presence
window. Raw NDP entries alone do not prove current reachability.

API reference
-------------

.. opnsense-client-api:: aiopnsense.dhcp.DHCPMixin
