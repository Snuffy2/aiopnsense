Quickstart
==========

Minimal client setup
--------------------

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
               system_info = await client.get_system_info()
               print(f"Firewall name: {system_info.get('name')}")

   asyncio.run(main())

Guidance
--------

Use ``async with`` for normal lifecycle management. Entering the client context already
calls ``validate()``. Call ``await client.validate()`` yourself only when you want an
explicit startup check before reusing a long-lived client outside the context manager.
