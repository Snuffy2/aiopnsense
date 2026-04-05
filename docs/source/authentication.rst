Authentication
==============

Connection model
----------------

``aiopnsense`` uses asynchronous client access to an OPNsense endpoint and should be
initialized with the base URL, an ``aiohttp.ClientSession``, and API credentials
appropriate for your deployment. In OPNsense, the generated API key is typically used
as the ``username`` and the generated secret as the ``password``.

TLS verification
----------------

Enable certificate validation in production. Only disable TLS verification in controlled
test environments.

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
               opts={"verify_ssl": False},
           )
           await client.validate()

   asyncio.run(main())

Operational recommendation
--------------------------

Document the expected authentication prerequisites for each installation method and keep
certificate verification enabled unless you are working in a controlled environment with
private CA or self-signed certificates.
