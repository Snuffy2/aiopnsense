Development
===========

Set up a local development environment from the repository root:

.. code-block:: bash

   python -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   pip install --group dev -e .

Run the standard local checks inside ``.venv``:

.. code-block:: bash

   pytest
   prek run --all-files
