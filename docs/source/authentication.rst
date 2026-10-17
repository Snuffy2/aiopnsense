Authentication
==============

Connection model
----------------

``aiopnsense`` uses asynchronous client access to an OPNsense endpoint and should be
initialized with the base URL, an ``aiohttp.ClientSession``, and API credentials
appropriate for your deployment. In OPNsense, the generated API key is used
as the ``username`` and the generated secret as the ``password``.

TLS verification
----------------

Only disable TLS verification if you understand the risks in doing so.

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

Request failure logs
--------------------

For each client, HTTP request failures are logged at their original warning or
error level once per ongoing outage for that method and path. Repeated failures
are logged at debug level, and a successful request logs an informational
recovery. Endpoint availability probes and data requests track recovery
separately, so a successful probe does not imply that a subsequent data request
succeeded. This changes logging only; ``throw_errors`` still controls whether
request failures are suppressed or raised.
