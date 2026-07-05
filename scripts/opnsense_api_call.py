#!/usr/bin/env python3
"""Execute raw OPNsense API requests using local live credentials."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
from pathlib import Path
from typing import Any, Protocol

import aiohttp  # noqa: E402

_common = importlib.import_module("_opnsense_live_common")
DEFAULT_ENV_FILE = _common.DEFAULT_ENV_FILE
DOCUMENTED_DEFAULT_ENV_FILE = _common.DOCUMENTED_DEFAULT_ENV_FILE
LiveConfig = _common.LiveConfig
LiveConfigError = _common.LiveConfigError
load_live_config = _common.load_live_config
reexec_with_repo_venv = _common.reexec_with_repo_venv
resolve_env_file_argument = _common.resolve_env_file_argument
write_output = _common.write_output

_NO_PAYLOAD = object()


class LiveConfigProtocol(Protocol):
    """Protocol for live OPNsense connection configuration."""

    url: str
    api_key: str
    api_secret: str
    verify_ssl: bool


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser for the raw API caller.

    Returns:
        A configured ``ArgumentParser`` for the script CLI.
    """
    parser = argparse.ArgumentParser(description="Call a raw OPNsense API endpoint.")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DOCUMENTED_DEFAULT_ENV_FILE,
        help="Path to the env file with live credentials.",
    )
    parser.add_argument(
        "--endpoint",
        required=True,
        help="Target endpoint path, with or without a leading slash.",
    )
    parser.add_argument(
        "--method",
        required=True,
        choices=("get", "post"),
        help="HTTP method.",
    )
    parser.add_argument(
        "--payload",
        help="Inline JSON object for POST requests.",
    )
    parser.add_argument(
        "--payload-file",
        type=Path,
        help="Path to a JSON object payload file for POST requests.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path to save output JSON.",
    )
    return parser


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the raw API caller.

    Args:
        args: Optional argument override for testability.

    Returns:
        Parsed arguments with runtime defaults resolved.
    """
    parser = build_parser()
    parsed_args = parser.parse_args(args=args)
    parsed_args.env_file = resolve_env_file_argument(parsed_args.env_file)
    return parsed_args


def normalize_endpoint(endpoint: str) -> str:
    """Return endpoint with leading slash and no surrounding whitespace.

    Args:
        endpoint: Raw endpoint value from CLI.

    Returns:
        Normalized endpoint path.

    Raises:
        ValueError: If endpoint is blank after stripping.
    """
    normalized = endpoint.strip()
    if not normalized:
        raise ValueError("endpoint cannot be blank")
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized


def _load_json_object(payload_text: str, source: str) -> dict[str, Any]:
    """Load and validate a JSON object from a raw string.

    Args:
        payload_text: Raw JSON text to parse.
        source: Human-readable payload source used in validation messages.

    Returns:
        Parsed JSON object.

    Raises:
        ValueError: If the payload is invalid JSON or does not decode to an object.
    """
    try:
        value = json.loads(payload_text)
    except json.JSONDecodeError as err:
        raise ValueError(f"Invalid JSON in {source}: {err}") from err
    if not isinstance(value, dict):
        raise ValueError(f"{source} must be a JSON object")
    return value


def load_payload(
    payload: str | None,
    payload_file: Path | None,
) -> dict[str, Any] | object:
    """Load POST payload from inline JSON or payload file.

    Args:
        payload: Inline JSON body string.
        payload_file: Path to inline JSON file.

    Returns:
        Parsed JSON object payload, or ``_NO_PAYLOAD`` when absent.

    Raises:
        ValueError: If both inputs are set, JSON is invalid, or payload is not object.
    """
    if payload is not None and payload_file is not None:
        raise ValueError("Specify only one of --payload or --payload-file.")
    if payload is not None:
        return _load_json_object(payload, "--payload")
    if payload_file is not None:
        try:
            payload_text = payload_file.read_text(encoding="utf-8")
        except OSError as err:
            raise ValueError(f"Unable to read --payload-file: {err}") from err
        return _load_json_object(payload_text, "--payload-file")
    return _NO_PAYLOAD


async def call_api(
    session: aiohttp.ClientSession,
    config: LiveConfigProtocol,
    endpoint: str,
    method: str,
    payload: dict[str, Any] | object,
) -> dict[str, Any]:
    """Call the configured OPNsense endpoint and return response metadata.

    Args:
        session: Active aiohttp client session.
        config: Live config with URL and credentials.
        endpoint: Raw endpoint path.
        method: HTTP method name (``get`` or ``post``).
        payload: Optional POST payload dictionary or omitted-payload sentinel.

    Returns:
        Parsed response payload with request metadata.
    """
    normalized_endpoint = normalize_endpoint(endpoint)
    url = f"{config.url.rstrip('/')}{normalized_endpoint}"
    request_kwargs: dict[str, Any] = {
        "auth": aiohttp.BasicAuth(config.api_key, config.api_secret),
        "timeout": aiohttp.ClientTimeout(total=60),
        "ssl": config.verify_ssl,
    }
    request_method = method.lower()
    if request_method == "post":
        if payload is not _NO_PAYLOAD:
            request_kwargs["json"] = payload
        request_ctx = session.post(url, **request_kwargs)
    else:
        request_ctx = session.get(url, **request_kwargs)

    async with request_ctx as response:
        try:
            payload_json = await response.json(content_type=None)
            payload_text = None
        except ValueError:
            payload_json = None
            payload_text = await response.text()
        return {
            "method": method.upper(),
            "endpoint": normalized_endpoint,
            "url": url,
            "status": response.status,
            "reason": response.reason,
            "headers": dict(response.headers),
            "json": payload_json,
            "text": payload_text,
        }


async def async_main(argv: list[str] | None = None) -> int:
    """Run the CLI flow and return shell status code.

    Args:
        argv: Optional argument list for testability.

    Returns:
        Process exit status code.
    """
    parser = build_parser()
    args = parse_args(argv)
    try:
        if args.method == "get" and (args.payload is not None or args.payload_file is not None):
            parser.error("--payload and --payload-file are only valid with --method post")
        endpoint = normalize_endpoint(args.endpoint)
        payload = load_payload(args.payload, args.payload_file)
    except ValueError as err:
        parser.error(str(err))

    config = load_live_config(args.env_file)
    async with aiohttp.ClientSession() as session:
        result = await call_api(session, config, endpoint, args.method, payload)
        write_output(result, args.output)
    return 0


def main() -> int:
    """Run CLI entrypoint and map config errors into CLI exits.

    Returns:
        Process exit status code.

    Raises:
        SystemExit: Raised when configuration, HTTP, timeout, or OS errors occur.
    """
    try:
        return asyncio.run(async_main())
    except LiveConfigError as err:
        raise SystemExit(str(err)) from None
    except (aiohttp.ClientError, TimeoutError, OSError) as err:
        raise SystemExit(f"{type(err).__name__}: {err}") from None


if __name__ == "__main__":
    reexec_with_repo_venv(Path(__file__))
    raise SystemExit(main())
