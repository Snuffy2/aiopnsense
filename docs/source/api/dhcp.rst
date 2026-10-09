DHCP, ARP, and NDP
=================

``get_ndp_table()`` reads IPv6 neighbors from
``/api/diagnostics/interface/search_ndp``. Grant the API user
``Diagnostics: NDP Table`` for this method. NDP discovery does not require
DHCPv6 and preserves multiple IPv6 addresses per MAC, including scoped
link-local and temporary/privacy addresses. Rows are returned as provided by
OPNsense; consumers should use the MAC rather than an IPv6 address as the
stable device identifier.

As with ``get_arp_table()``, an empty list is an authoritative empty table,
while ``None`` indicates a failed or unavailable lookup. With
``throw_errors=True``, request errors propagate as public OPNsense exceptions.

.. opnsense-client-api:: aiopnsense.dhcp.DHCPMixin
