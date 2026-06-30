Live Test Scripts
=================

The repository includes live diagnostic scripts for maintainers who need to
inspect aiopnsense and raw OPNsense API responses against a real firewall.
They read credentials from ``scripts/aiopnsense.env`` by default. Copy
``scripts/aiopnsense.env.example`` to that path and fill in your local
OPNsense API URL, key, and secret before running them.

Do not commit ``scripts/aiopnsense.env`` or other files containing live API
credentials.

aiopnsense endpoint dump
------------------------

``scripts/aiopnsense_dump.py`` calls supported ``aiopnsense`` client endpoints
and prints the raw data returned by the library. Use ``--list`` to show the
available endpoint names, or omit ``--endpoint`` for an interactive menu. The
interface traffic stream endpoint runs for the configured duration and defaults
to 30 seconds.

.. sphinx_argparse_cli::
   :module: aiopnsense_dump
   :func: build_parser
   :prog: scripts/aiopnsense_dump.py

Raw OPNsense API call
---------------------

``scripts/opnsense_api_call.py`` calls an arbitrary OPNsense API endpoint and
prints response metadata plus the JSON or text body returned by OPNsense.
Specify the endpoint, HTTP method, and optional POST payload on the command
line.

.. sphinx_argparse_cli::
   :module: opnsense_api_call
   :func: build_parser
   :prog: scripts/opnsense_api_call.py
