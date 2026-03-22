# aiopnsense

`aiopnsense` is an async Python client library for OPNsense.

**Requires OPNsense Firmware 26.1.1+**

It is extracted from the [`hass-opnsense`](https://github.com/travisghansen/hass-opnsense) integration so it can be versioned and used as an external dependency by Home Assistant.

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
