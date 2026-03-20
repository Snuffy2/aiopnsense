# aiopnsense

`aiopnsense` is an async Python client library for OPNsense.

It is extracted from the `hass-opnsense` custom integration so it can be
versioned and consumed as an external dependency by Home Assistant Core.

## Development

Install the package in editable mode with test dependencies and run:

```bash
python -m pip install -e .[dev]
pytest
ruff check .
```

