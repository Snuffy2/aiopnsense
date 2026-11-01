Examples
========

Read system state and telemetry
-------------------------------

.. code-block:: python

   import asyncio
   import aiohttp
   from aiopnsense import OPNsenseClient

   async def main() -> None:
       async with aiohttp.ClientSession() as session:
           client = OPNsenseClient(
               url="https://opnsense.example.com",
               username="YOUR_API_KEY",
               password="YOUR_API_SECRET",
               session=session,
           )
           try:
               await client.validate()

               system_info = await client.get_system_info()
               telemetry = await client.get_telemetry()

               print(f"Firewall name: {system_info.get('name')}")
               print(f"CPU telemetry: {telemetry.get('cpu')}")
               print(f"Filesystem telemetry: {telemetry.get('filesystems')}")
           finally:
               await client.async_close()

   asyncio.run(main())

Read diagnostics traffic
------------------------

.. code-block:: python

   import asyncio
   import aiohttp
   from aiopnsense import OPNsenseClient

   async def main() -> None:
       async with aiohttp.ClientSession() as session:
           async with OPNsenseClient(
               url="https://opnsense.example.com",
               username="YOUR_API_KEY",
               password="YOUR_API_SECRET",
               session=session,
           ) as client:
               traffic = await client.get_interface_traffic()
               wan = (traffic.get("interfaces") or {}).get("wan", {})
               print(f"WAN bytes received: {wan.get('rx_bytes', 0)}")

               async for sample in client.stream_interface_traffic(poll_interval=1):
                   sample_wan = (sample.get("interfaces") or {}).get("wan", {})
                   print(f"WAN receive rate: {sample_wan.get('rx_bits_per_second', 0)} bit/s")
                   break

   asyncio.run(main())

Fetch normalized ARP and NDP tables
-----------------------------------

``get_arp_entries()`` and ``get_ndp_entries()`` fetch a table once and return an
``OPNsenseClient.ArpNdpTable``, or ``None`` when the lookup is unavailable. Its ``entries`` are
frozen ``OPNsenseClient.ArpNdpEntry`` rows with normalized identity and copied metadata; rows
with invalid identities stay in the table; strict MAC/IP fields become ``None`` while
permissive MAC text is retained. ``unparsed_row_count``
reports source rows that were not mappings, so a nonzero count means ``entries`` may be partial.
Expiration and permanent-entry flags use the shared boolean coercion rules.
``interface_name`` and ``interface_description`` retain the identifier and description
separately; ``interface`` provides the preferred display value. ``raw_rows`` preserves every
original field, including unknown fields and invalid values. The raw ``get_arp_table()`` and
``get_ndp_table()`` methods are deprecated; use these normalized methods instead. Table parse quality is separate
from presence: normalized NDP inventory alone does not prove current reachability.

.. code-block:: python

   import asyncio

   import aiohttp
   from aiopnsense import OPNsenseClient

   async def main() -> None:
       async with aiohttp.ClientSession() as session:
           async with OPNsenseClient(
               url="https://opnsense.example.com",
               username="YOUR_API_KEY",
               password="YOUR_API_SECRET",
               session=session,
           ) as client:
               tables = (
                   ("ARP", await client.get_arp_entries()),
                   ("NDP", await client.get_ndp_entries()),
               )
               for family, table in tables:
                   if table is None:
                       print(f"{family} table unavailable")
                       continue
                   if table.unparsed_row_count:
                       print(f"{family} table is partial: {table.unparsed_row_count} unparsed rows")
                   for entry in table.entries:
                       entry: OPNsenseClient.ArpNdpEntry
                       address = entry.ipv4_address or entry.ipv6_address
                       if entry.valid_mac is None or address is None:
                           continue
                       print(
                           f"{family}: MAC={entry.valid_mac}, IP={address}, "
                           f"hostname={entry.hostname}, interface={entry.interface_name}, "
                           f"description={entry.interface_description}, permanent={entry.permanent}"
                       )

   asyncio.run(main())

Check firmware or control a service
-----------------------------------

.. code-block:: python

   import asyncio
   import aiohttp
   from aiopnsense import OPNsenseClient

   async def main() -> None:
       async with aiohttp.ClientSession() as session:
           async with OPNsenseClient(
               url="https://opnsense.example.com",
               username="YOUR_API_KEY",
               password="YOUR_API_SECRET",
               session=session,
               opts={"verify_ssl": True},
           ) as client:
               firmware = await client.get_firmware_update_info()
               services = await client.get_services()

               print(f"Current firmware: {firmware.get('product', {}).get('product_version')}")
               print(f"Available services: {[service.get('name') for service in services[:5]]}")

               restarted = await client.restart_service_if_running("unbound")
               print(f"Restarted unbound: {restarted}")

   asyncio.run(main())
