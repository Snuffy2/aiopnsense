Access Levels and Unknown Inventories
=====================================

An OPNsense API user can be granted some privileges and not others. A getter
therefore has to tell an inventory the Router reports as empty apart from one
the API user may not see. Applications that delete items missing from an
inventory, as hass-opnsense does, depend on this distinction. The rules below
apply with ``throw_errors=False``. Strict mode keeps each getter's existing
exceptions.

Empty, missing and unknown inventories
--------------------------------------

Getters whose result is an inventory a caller reconciles follow one rule:

- An empty inventory means the Router has nothing to report. A missing (404)
  endpoint means the feature is not installed, so the getter returns an empty
  inventory or the feature's missing marker, such as ``{"available": False}``
  from ``get_speedtest()``.
- ``None`` means the inventory is unknown: the read was forbidden (403), failed,
  or returned a body that could not be decoded or had the wrong shape. Keep the
  items you already have and reconcile on a later successful read.
- When one result holds several independent inventories, each is decided on
  its own. ``get_telemetry()`` sets only ``filesystems`` or ``temps`` to
  ``None``, and ``get_firewall()`` does the same for ``rules`` and each NAT
  group under ``nat``.
- A read that only adds detail to items already listed leaves those details
  unset instead of making the inventory unknown.

These getters follow the rule: ``get_services()``, ``get_unbound_blocklist()``,
``get_interfaces()``, ``get_gateways()``, ``get_telemetry()`` (``filesystems``
and ``temps``), ``get_smart()``, ``get_smart_info()``,
``get_nut_ups_status()``, ``get_speedtest()``, ``get_vnstat()``,
``get_carp()``, ``get_firewall()`` (each rule group) and
``get_dhcp_leases()``.

Status Access and Configuration Access
--------------------------------------

Some categories can still return useful data when the API user may read their
live status but not their configuration. These getters report the access level
detected from the Router's answers on the result itself:

- ``get_openvpn()``, ``get_wireguard()`` and ``get_ipsec()``
- ``get_dhcp_leases()``
- ``get_carp()``

Read the level with :func:`aiopnsense.result_access`:

.. code-block:: python

   from aiopnsense import Access, result_access

   openvpn = await client.get_openvpn()
   if openvpn is None:
       print("OpenVPN status unavailable; keep existing items")
   elif result_access(openvpn) is Access.STATUS:
       print("Inventory may be incomplete; do not remove missing items")
   else:
       print("Configuration Access; the inventory is complete")

Under Status Access the result may lack items or detail that the configuration
reads would add, such as disabled VPN Instances or CARP VIP descriptions, and
nothing can be Toggled. Treat it as incomplete: add and update items, but do
not remove the ones it lacks. The level comes only from the Router; there is no
option to set it.

For ``get_dhcp_leases()``, a fresh or cached 403 from a lease or interface
configuration endpoint permits the getter to return readable leases and
interfaces with Status Access, even if another interface configuration read
fails or its body is undecodable or malformed. Without a denial, an unreadable
interface configuration read makes the inventory unknown. An unreadable lease
read makes the inventory unknown even when another endpoint returns 403.

The result is an ``AccessDict`` or ``AccessList``, which compares equal to and
passes ``isinstance`` checks as a plain ``dict`` or ``list``. The level survives
``copy.deepcopy`` and pickling, but plain copies made with ``dict()``,
``list()``, ``{**result}`` or JSON drop it. ``result_access()`` then returns
``None``, meaning the level was not reported. Handle that as you would a result
from a release without access reporting.

The design and the per-category details are recorded in ADR 0004,
`Results report their access level
<https://github.com/Snuffy2/aiopnsense/blob/main/docs/adr/0004-results-report-their-access-level.md>`_.

API reference
-------------

.. autoclass:: aiopnsense.Access
   :members:
   :undoc-members:

.. autoclass:: aiopnsense.AccessDict

.. autoclass:: aiopnsense.AccessList

.. autofunction:: aiopnsense.result_access
