"""Tests for the raw OPNsense API caller."""

from __future__ import annotations

import importlib.util
import runpy
from pathlib import Path
import sys
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock
import aiohttp

import pytest


def load_api_call_module() -> ModuleType:
    """Load the script module through importlib for direct unit testing."""
    module_path = Path(__file__).parents[1] / "scripts" / "opnsense_api_call.py"
    sys.path.insert(0, str(module_path.parent))
    spec = importlib.util.spec_from_file_location("opnsense_api_call", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    """Async response stub with typed return behavior."""

    status: int
    reason: str
    headers: dict[str, str]
    _json: Any
    _text: str
    _json_error: Exception | None

    def __init__(
        self,
        status: int,
        reason: str,
        headers: dict[str, str] | None = None,
        json_payload: Any = None,
        text: str = "",
        json_error: Exception | None = None,
    ) -> None:
        """Create a minimal aiohttp-like response stub."""
        self.status = status
        self.reason = reason
        self.headers = headers or {}
        self._json = json_payload
        self._text = text
        self._json_error = json_error

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def json(self, *_args: Any, **_kwargs: Any) -> Any:
        """Parse JSON payload for response body."""
        if self._json_error is not None:
            raise self._json_error
        return self._json

    async def text(self) -> str:
        """Return text fallback body."""
        return self._text


class FakeSession:
    """Session stub that records GET and POST call parameters."""

    get_calls: list[tuple[str, dict[str, Any]]]
    post_calls: list[tuple[str, dict[str, Any]]]
    get_response: FakeResponse
    post_response: FakeResponse
    enter_count: int
    exit_count: int

    def __init__(
        self,
        get_response: FakeResponse | None = None,
        post_response: FakeResponse | None = None,
    ) -> None:
        """Initialize call history and response stubs."""
        self.get_calls = []
        self.post_calls = []
        self.get_response = get_response or FakeResponse(200, "OK", json_payload={"default": True})
        self.post_response = post_response or FakeResponse(
            200, "OK", json_payload={"default": True}
        )
        self.enter_count = 0
        self.exit_count = 0

    async def __aenter__(self) -> "FakeSession":
        self.enter_count += 1
        return self

    async def __aexit__(self, *_args: Any) -> None:
        self.exit_count += 1
        return None

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        """Record GET call and return response context."""
        self.get_calls.append((url, kwargs))
        return self.get_response

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        """Record POST call and return response context."""
        self.post_calls.append((url, kwargs))
        return self.post_response


def test_normalize_endpoint_requires_leading_slash() -> None:
    """Endpoint values are normalized to include a leading slash."""
    module = load_api_call_module()

    assert module.normalize_endpoint("/status") == "/status"
    assert module.normalize_endpoint("status") == "/status"


def test_normalize_endpoint_rejects_blank_value() -> None:
    """Blank endpoints are rejected with a clear ``ValueError``."""
    module = load_api_call_module()

    with pytest.raises(ValueError, match="endpoint cannot be blank"):
        module.normalize_endpoint("   ")


def test_load_payload_from_json_string() -> None:
    """Inline payload strings parse only JSON objects."""
    module = load_api_call_module()

    assert module.load_payload('{"ok": true}', None) == {"ok": True}


def test_load_payload_from_file(tmp_path: Path) -> None:
    """Payload file input supports JSON objects."""
    module = load_api_call_module()
    payload_file = tmp_path / "payload.json"
    payload_file.write_text('{"from": "file"}', encoding="utf-8")

    assert module.load_payload(None, payload_file) == {"from": "file"}


def test_load_payload_rejects_both_sources() -> None:
    """Rejecting both inline and file payload sources avoids ambiguous payload input."""
    module = load_api_call_module()
    with pytest.raises(ValueError, match="Specify only one"):
        module.load_payload('{"ok": true}', Path("payload.json"))


def test_load_payload_rejects_non_object_json() -> None:
    """Non-object payload values are rejected as invalid body."""
    module = load_api_call_module()

    with pytest.raises(ValueError, match="must be a JSON object"):
        module.load_payload("[1, 2, 3]", None)


def test_load_payload_invalid_json() -> None:
    """Malformed JSON input raises a clear error."""
    module = load_api_call_module()

    with pytest.raises(ValueError, match="Invalid JSON"):
        module.load_payload("{bad json}", None)


@pytest.mark.asyncio
async def test_call_api_get_returns_response_metadata() -> None:
    """GET calls return response metadata and parsed JSON body."""
    module = load_api_call_module()
    config = module.LiveConfig(
        url="https://firewall.example.test/",
        api_key="key",
        api_secret="secret",
        verify_ssl=True,
    )
    response = FakeResponse(
        status=200,
        reason="OK",
        headers={"content-type": "application/json"},
        json_payload={"status": "alive"},
        text='{"status":"alive"}',
    )
    session = FakeSession(get_response=response)

    result = await module.call_api(session, config, "api/v1/status", "get", None)

    assert result["method"] == "GET"
    assert result["endpoint"] == "/api/v1/status"
    assert result["url"] == "https://firewall.example.test/api/v1/status"
    assert result["status"] == 200
    assert result["reason"] == "OK"
    assert result["headers"] == {"content-type": "application/json"}
    assert result["json"] == {"status": "alive"}
    assert result["text"] is None
    assert len(session.get_calls) == 1
    _, kwargs = session.get_calls[0]
    assert isinstance(kwargs["auth"], aiohttp.BasicAuth)
    assert kwargs["auth"].login == "key"
    assert kwargs["auth"].password == "secret"
    assert kwargs["timeout"] == aiohttp.ClientTimeout(total=60)
    assert kwargs["ssl"] is True
    assert "json" not in kwargs


@pytest.mark.asyncio
async def test_call_api_post_sends_payload() -> None:
    """POST calls include requested payload JSON body."""
    module = load_api_call_module()
    config = module.LiveConfig(
        url="https://firewall.example.test/",
        api_key="key",
        api_secret="secret",
        verify_ssl=True,
    )
    response = FakeResponse(
        status=201,
        reason="Created",
        headers={"content-type": "application/json"},
        json_payload={"result": "created"},
    )
    session = FakeSession(post_response=response)

    await module.call_api(
        session,
        config,
        "/api/v1/configure",
        "post",
        {"enabled": True},
    )

    assert len(session.post_calls) == 1
    _, kwargs = session.post_calls[0]
    assert kwargs["json"] == {"enabled": True}
    assert isinstance(kwargs["auth"], aiohttp.BasicAuth)
    assert kwargs["auth"].login == "key"
    assert kwargs["auth"].password == "secret"
    assert kwargs["timeout"] == aiohttp.ClientTimeout(total=60)
    assert kwargs["ssl"] is True


@pytest.mark.asyncio
async def test_call_api_falls_back_to_text_for_non_json_response() -> None:
    """Non-JSON response bodies fall back to plain text."""
    module = load_api_call_module()
    config = module.LiveConfig(
        url="https://firewall.example.test/",
        api_key="key",
        api_secret="secret",
        verify_ssl=False,
    )
    response = FakeResponse(
        status=200,
        reason="OK",
        headers={"content-type": "text/plain"},
        json_payload={},
        text="plain text response",
        json_error=ValueError("not json"),
    )
    session = FakeSession(get_response=response)

    result = await module.call_api(session, config, "/api/v1/text", "get", None)

    assert result["json"] is None
    assert result["text"] == "plain text response"


@pytest.mark.asyncio
async def test_call_api_preserves_non_2xx_json_body() -> None:
    """Non-2xx response status/reason/body are still returned."""
    module = load_api_call_module()
    config = module.LiveConfig(
        url="https://firewall.example.test/",
        api_key="key",
        api_secret="secret",
        verify_ssl=True,
    )
    response = FakeResponse(
        status=404,
        reason="Not Found",
        headers={"content-type": "application/json"},
        json_payload={"error": "missing"},
        text='{"error": "missing"}',
    )
    session = FakeSession(get_response=response)

    result = await module.call_api(session, config, "/api/v1/missing", "get", None)

    assert result["status"] == 404
    assert result["reason"] == "Not Found"
    assert result["json"] == {"error": "missing"}
    assert result["text"] is None


@pytest.mark.asyncio
async def test_call_api_preserves_non_2xx_text_body() -> None:
    """Non-2xx non-JSON responses preserve status and fallback text."""
    module = load_api_call_module()
    config = module.LiveConfig(
        url="https://firewall.example.test/",
        api_key="key",
        api_secret="secret",
        verify_ssl=True,
    )
    response = FakeResponse(
        status=404,
        reason="Not Found",
        headers={"content-type": "text/plain"},
        text="resource not found",
        json_error=ValueError("not json"),
    )
    session = FakeSession(get_response=response)

    result = await module.call_api(session, config, "/api/v1/missing", "get", None)

    assert result["status"] == 404
    assert result["reason"] == "Not Found"
    assert result["json"] is None
    assert result["text"] == "resource not found"


def test_main_converts_live_config_error_to_system_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() maps LiveConfigError from async_main to SystemExit."""
    module = load_api_call_module()

    async def raise_error() -> None:
        raise module.LiveConfigError("bad config")

    monkeypatch.setattr(module, "async_main", raise_error)

    with pytest.raises(SystemExit) as excinfo:
        module.main()

    assert "bad config" in str(excinfo.value)


@pytest.mark.asyncio
async def test_async_main_rejects_payload_with_get_method(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """GET requests cannot be combined with payload options."""
    module = load_api_call_module()
    output_file = tmp_path / "ignored.json"

    with pytest.raises(SystemExit):
        await module.async_main(
            [
                "--endpoint",
                "/api/v1/status",
                "--method",
                "get",
                "--payload",
                '{"x":1}',
                "--output",
                str(output_file),
            ]
        )

    assert (
        "--payload and --payload-file are only valid with --method post" in capsys.readouterr().err
    )


@pytest.mark.asyncio
async def test_async_main_rejects_invalid_json_with_get_payload(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """GET + invalid inline payload uses method-specific error, not JSON parsing error."""
    module = load_api_call_module()

    with pytest.raises(SystemExit):
        await module.async_main(
            [
                "--endpoint",
                "/api/v1/status",
                "--method",
                "get",
                "--payload",
                "{bad_json}",
            ]
        )

    assert (
        "--payload and --payload-file are only valid with --method post" in capsys.readouterr().err
    )


@pytest.mark.asyncio
async def test_async_main_rejects_non_object_payload_with_get(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """GET + non-object payload uses method-specific error, not payload-type error."""
    module = load_api_call_module()

    with pytest.raises(SystemExit):
        await module.async_main(
            [
                "--endpoint",
                "/api/v1/status",
                "--method",
                "get",
                "--payload",
                "[1, 2, 3]",
            ]
        )

    assert (
        "--payload and --payload-file are only valid with --method post" in capsys.readouterr().err
    )


@pytest.mark.asyncio
async def test_async_main_rejects_payload_file_with_get_method(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """GET + --payload-file is rejected with required parser error text."""
    module = load_api_call_module()
    payload_file = tmp_path / "payload.json"
    payload_file.write_text('{"ok": true}', encoding="utf-8")

    with pytest.raises(SystemExit):
        await module.async_main(
            [
                "--endpoint",
                "/api/v1/status",
                "--method",
                "get",
                "--payload-file",
                str(payload_file),
            ]
        )

    assert (
        "--payload and --payload-file are only valid with --method post" in capsys.readouterr().err
    )


@pytest.mark.asyncio
async def test_async_main_rejects_blank_endpoint() -> None:
    """Blank endpoint values fail normalization and exit through parser.error."""
    module = load_api_call_module()

    with pytest.raises(SystemExit):
        await module.async_main(["--endpoint", "   ", "--method", "post"])


@pytest.mark.asyncio
async def test_async_main_rejects_invalid_payload_json() -> None:
    """Invalid inline JSON payload errors are surfaced by parser.error."""
    module = load_api_call_module()

    with pytest.raises(SystemExit):
        await module.async_main(
            [
                "--endpoint",
                "/api/v1/test",
                "--method",
                "post",
                "--payload",
                "{bad_json}",
            ]
        )


@pytest.mark.asyncio
async def test_async_main_rejects_non_object_payload_json() -> None:
    """Non-object inline JSON payload errors are surfaced by parser.error."""
    module = load_api_call_module()

    with pytest.raises(SystemExit):
        await module.async_main(
            [
                "--endpoint",
                "/api/v1/test",
                "--method",
                "post",
                "--payload",
                "[1, 2, 3]",
            ]
        )


@pytest.mark.asyncio
async def test_async_main_rejects_both_payload_sources_for_post(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """POST requests with both payload sources use load_payload conflict message."""
    module = load_api_call_module()
    payload_file = tmp_path / "payload.json"
    payload_file.write_text('{"from": "file"}', encoding="utf-8")

    with pytest.raises(SystemExit):
        await module.async_main(
            [
                "--endpoint",
                "/api/v1/status",
                "--method",
                "post",
                "--payload",
                "{}",
                "--payload-file",
                str(payload_file),
            ]
        )

    assert "Specify only one of --payload or --payload-file." in capsys.readouterr().err


@pytest.mark.asyncio
async def test_async_main_rejects_missing_payload_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Missing payload files become parser errors instead of filesystem tracebacks."""
    module = load_api_call_module()
    missing_file = tmp_path / "missing.json"

    with pytest.raises(SystemExit):
        await module.async_main(
            [
                "--endpoint",
                "/api/v1/status",
                "--method",
                "post",
                "--payload-file",
                str(missing_file),
            ]
        )

    assert "Unable to read --payload-file" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_async_main_writes_output_and_closes_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Happy path runs the call and guarantees session lifecycle completion."""
    module = load_api_call_module()
    config = module.LiveConfig(
        url="https://firewall.example.test/",
        api_key="key",
        api_secret="secret",
        verify_ssl=True,
    )
    session = FakeSession()
    output_path = tmp_path / "result.json"
    call_args: list[tuple[Any, ...]] = []
    write_calls: list[tuple[dict[str, Any], Path | None]] = []

    def fake_load_live_config(_env_file: Any) -> Any:
        return config

    async def fake_call_api(
        call_session: Any,
        call_config: Any,
        endpoint: str,
        method: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        call_args.append((call_session, call_config, endpoint, method, payload))
        return {"ok": True}

    monkeypatch.setattr(module, "load_live_config", fake_load_live_config)
    monkeypatch.setattr(module.aiohttp, "ClientSession", lambda: session)
    monkeypatch.setattr(module, "call_api", fake_call_api)

    def fake_write_output(payload: dict[str, Any], output: Path | None) -> None:
        write_calls.append((payload, output))
        output_path.write_text(str(payload), encoding="utf-8")

    monkeypatch.setattr(module, "write_output", fake_write_output)

    exit_code = await module.async_main(
        [
            "--endpoint",
            "api/v1/status",
            "--method",
            "post",
            "--payload",
            "{}",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    assert session.enter_count == 1
    assert session.exit_count == 1
    assert call_args == [
        (session, config, "/api/v1/status", "post", {}),
    ]
    assert write_calls == [({"ok": True}, output_path)]
    assert output_path.exists()


def test_entrypoint_exits_with_help_status_zero() -> None:
    """Executing the script as __main__ with --help exits with status 0."""
    script_path = Path(__file__).parents[1] / "scripts" / "opnsense_api_call.py"
    monkeypatch_argv = ["opnsense_api_call.py", "--help"]
    original_argv = sys.argv
    sys.argv = monkeypatch_argv
    try:
        with pytest.raises(SystemExit) as excinfo:
            runpy.run_path(str(script_path), run_name="__main__")
    finally:
        sys.argv = original_argv

    assert excinfo.value.code == 0


def test_reexec_with_repo_venv_uses_local_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bootstrap re-exec uses the repo venv when launched outside it."""
    module = load_api_call_module()
    calls: list[tuple[str, list[str]]] = []
    repo_venv = Path(module.__file__).resolve().parents[1] / ".venv"
    expected_python = repo_venv / "bin" / "python"

    monkeypatch.delenv("AIOPNSENSE_LIVE_SCRIPT_BOOTSTRAPPED", raising=False)
    monkeypatch.setattr(module.sys, "prefix", "/usr/local")
    monkeypatch.setattr(module.sys, "argv", ["opnsense_api_call.py", "--help"])
    monkeypatch.setattr(module.os, "execv", lambda path, args: calls.append((path, args)))

    module._reexec_with_repo_venv()

    assert module.os.environ["AIOPNSENSE_LIVE_SCRIPT_BOOTSTRAPPED"] == "1"
    assert calls == [
        (
            str(expected_python),
            [str(expected_python), module.__file__, "--help"],
        )
    ]


def test_reexec_with_repo_venv_skips_when_already_in_venv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bootstrap does nothing when the repo venv is already active."""
    module = load_api_call_module()
    repo_venv = Path(module.__file__).resolve().parents[1] / ".venv"
    execv = MagicMock()

    monkeypatch.delenv("AIOPNSENSE_LIVE_SCRIPT_BOOTSTRAPPED", raising=False)
    monkeypatch.setattr(module.sys, "prefix", str(repo_venv))
    monkeypatch.setattr(module.os, "execv", execv)

    module._reexec_with_repo_venv()

    execv.assert_not_called()


def test_main_converts_client_error_to_system_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() converts aiohttp client transport exceptions into SystemExit."""
    module = load_api_call_module()

    async def raise_connector_error() -> None:
        raise module.aiohttp.ClientConnectorError(
            module.aiohttp.client_reqrep.ConnectionKey(
                host="localhost",
                port=443,
                is_ssl=False,
                ssl=None,
                proxy=None,
                proxy_auth=None,
                proxy_headers_hash=None,
            ),
            OSError("boom"),
        )

    monkeypatch.setattr(module, "async_main", raise_connector_error)
    with pytest.raises(SystemExit) as excinfo:
        module.main()

    assert "ClientConnectorError" in str(excinfo.value)


def test_main_converts_timeout_error_to_system_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() converts timeout exceptions into concise SystemExit messages."""
    module = load_api_call_module()

    async def raise_timeout_error() -> None:
        raise TimeoutError("timeout while waiting for response")

    monkeypatch.setattr(module, "async_main", raise_timeout_error)
    with pytest.raises(SystemExit) as excinfo:
        module.main()

    assert "TimeoutError" in str(excinfo.value)
