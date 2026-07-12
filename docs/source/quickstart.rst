Quickstart
==========

The client expects an existing ``aiohttp.ClientSession``. Most applications create one
session for the lifetime of the integration or service and reuse it for all requests.

Minimal client setup
--------------------

.. code-block:: python

   import asyncio
   import aiohttp
   from aiopnsense import OPNsenseClient

   async def main() -> None:
       async with aiohttp.ClientSession() as session, OPNsenseClient(
           url="https://opnsense.example.com",
           username="YOUR_API_KEY",
           password="YOUR_API_SECRET",
           session=session,
           opts={"verify_ssl": True},
       ) as client:
           system_info = await client.get_system_info()
           print(f"Firewall name: {system_info.get('name')}")

   asyncio.run(main())

Explicit validation
-------------------

Use ``async with`` for normal lifecycle management. Entering the client context already
calls ``validate()``. Call ``await client.validate()`` yourself only when you want an
explicit startup check before reusing a long-lived client outside the context manager.

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
               opts={"verify_ssl": True},
           )
           try:
               await client.validate()
               system_info = await client.get_system_info()
               print(f"Firewall name: {system_info.get('name')}")
           finally:
               await client.async_close()

   asyncio.run(main())

Virtual endpoints
-----------------

Applications that intentionally connect to a virtual endpoint without stable physical-device
identity can retain connection, authentication, and firmware validation while skipping the
device-ID requirement:

.. code-block:: python

   await client.validate(require_device_id=False)

This option skips only the device-ID request. Applications remain responsible for validating
any endpoint-specific payloads they require.
