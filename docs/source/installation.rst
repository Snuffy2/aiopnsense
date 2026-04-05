Installation
============

Install from PyPI:

.. code-block:: bash

   pip install aiopnsense

For development, install the project with its dependency groups from the repository root.

.. code-block:: bash

   python -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   pip install --group dev -e .

To work on the documentation only, install the docs extra instead:

.. code-block:: bash

   pip install -e .[docs]

Python Version
--------------

The package metadata currently declares Python ``>=3.14``. Confirm that your local
runtime and CI environment match the package requirement before publishing docs.
