# aiopnsense

`aiopnsense` is an async Python client library for OPNsense.

It is extracted from the `hass-opnsense` custom integration so it can be
versioned and consumed as an external dependency by Home Assistant Core.

## Development

Install the package in editable mode with test dependencies and run:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install --group dev -e .
pytest
prek run --all-files
```

