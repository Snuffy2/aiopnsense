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

Normalize ARP and NDP rows
--------------------------

``normalize_arp_ndp_entry()`` is synchronous and returns a frozen
``OPNsenseClient.ArpNdpEntry`` containing normalized identity and copied metadata
from either table. Normalized NDP inventory alone does not prove current reachability.

.. code-block:: python

   import asyncio
   from collections.abc import Mapping

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
                   ("ARP", await client.get_arp_table()),
                   ("NDP", await client.get_ndp_table()),
               )
               for family, rows in tables:
                   if rows is None:
                       print(f"{family} table unavailable")
                       continue
                   for row in rows:
                       if not isinstance(row, Mapping):
                           continue
                       entry: OPNsenseClient.ArpNdpEntry = client.normalize_arp_ndp_entry(row)
                       address = entry.ipv4_address or entry.ipv6_address
                       if not entry.usable or entry.valid_mac is None or address is None:
                           continue
                       print(
                           f"{family}: MAC={entry.valid_mac}, IP={address}, "
                           f"hostname={entry.hostname}, interface={entry.interface}"
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
