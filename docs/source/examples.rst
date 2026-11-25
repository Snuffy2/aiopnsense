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

Fetch IPv4 and IPv6 neighbors as ``OPNsenseClient.ArpNdpEntry`` objects.

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
                   for entry in table.entries:
                       entry: OPNsenseClient.ArpNdpEntry
                       address = entry.ipv4_address or entry.ipv6_address
                       print(
                           f"{family}: MAC={entry.valid_mac}, IP={address}, "
                           f"hostname={entry.hostname}, interface={entry.interface_name}"
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
               if services is None:
                   print("Service inventory unavailable")
               else:
                   print(f"Available services: {[service.get('name') for service in services[:5]]}")

               restarted = await client.restart_service_if_running("unbound")
               print(f"Restarted unbound: {restarted}")

   asyncio.run(main())

Read the supported firmware requirements
----------------------------------------

``OPNsenseClient.get_firmware_requirements()`` returns the Minimum Firmware and
Recommended Firmware as an ``OPNsenseClient.FirmwareRequirements`` object. It is
a static method that never contacts the Router, so it works before any client
exists, for example to show the requirements in a setup form. ``validate()`` raises
``OPNsenseBelowMinFirmware`` for Router firmware older than the Minimum Firmware and
``OPNsenseUnknownFirmware`` when the Router's firmware version cannot be determined.
The example closes the client in ``finally`` so its background workers stop even
when validation fails.

.. code-block:: python

   import asyncio

   import aiohttp
   from aiopnsense import OPNsenseBelowMinFirmware, OPNsenseClient, OPNsenseUnknownFirmware
   from aiopnsense.helpers import firmware_is_at_least

   async def main() -> None:
       requirements: OPNsenseClient.FirmwareRequirements = (
           OPNsenseClient.get_firmware_requirements()
       )
       print(f"Minimum Firmware: {requirements.minimum}")
       print(f"Recommended Firmware: {requirements.recommended}")

       async with aiohttp.ClientSession() as session:
           client = OPNsenseClient(
               url="https://opnsense.example.com",
               username="YOUR_API_KEY",
               password="YOUR_API_SECRET",
               session=session,
           )
           try:
               await client.validate()
               firmware = await client.get_host_firmware_version()
               if firmware_is_at_least(firmware, requirements.recommended) is False:
                   print(
                       f"Firmware {firmware} is supported, but some features "
                       f"need {requirements.recommended} or later"
                   )
           except OPNsenseBelowMinFirmware:
               print(f"Upgrade the Router to {requirements.minimum} or later")
           except OPNsenseUnknownFirmware:
               print("The Router's firmware version could not be determined")
           finally:
               await client.async_close()

   asyncio.run(main())
