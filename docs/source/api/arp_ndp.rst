ARP and NDP Table Normalization
===============================

The standalone :func:`aiopnsense.arp_ndp.normalize_neighbor_entry` helper converts
one raw ARP or NDP row into a :class:`aiopnsense.arp_ndp.NeighborEntry`.
The client methods still return raw rows; normalization is optional and performs
no network requests.

Usage
-----

Within an existing asynchronous client session (see :doc:`../quickstart`):

.. code-block:: python

   from collections.abc import Mapping

   from aiopnsense.arp_ndp import normalize_neighbor_entry

   arp_rows = await client.get_arp_table(resolve_hostnames=True)
   if arp_rows is not None:
       for row in arp_rows:
           if not isinstance(row, Mapping):
               continue
           entry = normalize_neighbor_entry(row)
           if entry.usable:
               print(entry.mac, entry.ipv4_address, entry.hostname)

   ndp_rows = await client.get_ndp_table()
   if ndp_rows is not None:
       for row in ndp_rows:
           if not isinstance(row, Mapping):
               continue
           entry = normalize_neighbor_entry(row)
           if entry.usable and entry.valid_mac and entry.ipv6_address:
               print(entry.valid_mac, entry.ipv6_address)

An unavailable table is returned as ``None``; an empty list is a successful
lookup with no rows. If a response contains non-mapping rows, skip calling the
helper for those rows and retain that incompleteness when deciding whether the
result can replace a saved inventory. Filtering a malformed response does not
make it an authoritative empty table.

For example, a raw row can also be normalized independently of a client:

.. doctest::

   >>> from aiopnsense.arp_ndp import normalize_neighbor_entry
   >>> entry = normalize_neighbor_entry({
   ...     "mac-address": "AA-BB-CC-DD-EE-01", "ip-address": "2001:0db8::1"
   ... })
   >>> entry.valid_mac, entry.ipv6_address
   ('aa:bb:cc:dd:ee:01', '2001:db8::1')

Returned values
---------------

* ``mac`` is stripped, lowercased, and uses colon separators. It preserves
  incomplete or invalid MAC text for consumers that need permissive matching.
  ``valid_mac`` is set only for a complete six-octet hexadecimal MAC address.
* ``ipv4_address`` and ``ipv6_address`` contain canonical addresses of their
  respective families, or ``None``. IPv6 interface scopes are preserved.
* ``mac``/``mac-address`` and ``ip``/``ip-address`` input aliases are supported.
  Hostnames lose surrounding question marks; absent or unusable string metadata
  becomes ``None``.
* ``interface`` prefers ``intf_description`` over ``intf``. ``expires`` and
  ``arp_type`` preserve the supplied metadata; ``expired`` uses its truth value.
* ``usable`` records whether the input was a mutable mapping. Read-only mappings
  are normalized but classified as unusable; the flag does not guarantee a valid
  MAC address or IP address.

Fields cannot be reassigned on the frozen result. Metadata is deep-copied from
the source row, so later source changes do not alter the result; nested metadata
objects themselves are not frozen. The helper does not combine rows, decide
presence or freshness, retain cached data, or reconcile device inventories.

API reference
-------------

.. automodule:: aiopnsense.arp_ndp
   :members: NeighborEntry, normalize_neighbor_entry
