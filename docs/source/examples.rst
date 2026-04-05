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
