"""Authenticated TCP probe against the GIMP MCP plug-in."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from typing import Any

import gimp_mcp_security as sec
from gimp_agent import exit_codes as ec


@dataclass
class ProbeReport:
    ok: bool
    code: str | None
    message: str
    exit_code: int
    data: dict[str, Any]


def _resolve_host() -> str:
    try:
        return sec.get_host()
    except sec.SecurityError:
        return sec.DEFAULT_HOST


def load_probe_token() -> str | None:
    """Load session token: ENV_TOKEN first, then file (with short retry)."""
    env_tok = sec.load_token_with_retry(max_attempts=3, base_delay_s=0.05, max_delay_s=0.2)
    if env_tok:
        return env_tok
    return sec.read_token_file()


def _recv_json(sock: socket.socket) -> dict[str, Any]:
    """Read until a complete JSON object is available (newline-framed stream)."""
    response_data = b""
    while True:
        chunk = sock.recv(8192)
        if not chunk:
            break
        response_data += chunk
        try:
            parsed = json.loads(response_data.decode("utf-8"))
            if isinstance(parsed, dict):
                return parsed
            return {
                "status": "error",
                "error": "non-object JSON response",
                "code": sec.CODE_INTERNAL,
            }
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    if not response_data:
        raise ConnectionError("empty response from plugin")
    try:
        parsed = json.loads(response_data.decode("utf-8"))
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ConnectionError(f"invalid JSON response: {exc}") from exc
    return {"status": "error", "error": "non-object JSON response", "code": sec.CODE_INTERNAL}


def send_get_gimp_info(
    *,
    host: str,
    port: int,
    token: str,
    timeout: float,
) -> dict[str, Any]:
    """Minimal local client: connect, send authenticated get_gimp_info, parse JSON.

    Does **not** import ``gimp_mcp_server``. Framing matches GimpConnection:
    ``json.dumps(...) + b"\\n"`` then recv until ``json.loads`` succeeds.
    """
    payload = {
        "type": "get_gimp_info",
        "params": {},
        "auth": token,
    }
    raw = json.dumps(payload).encode("utf-8") + b"\n"
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(raw)
        return _recv_json(sock)


def _extract_version(result: dict[str, Any]) -> str | None:
    results = result.get("results")
    if not isinstance(results, dict):
        return None
    version = results.get("version")
    if isinstance(version, dict):
        for key in ("detected_version", "version_method", "VERSION"):
            val = version.get(key)
            if val is not None and str(val) and str(val) != "Unknown":
                return str(val)
        major = version.get("major_version")
        minor = version.get("minor_version")
        micro = version.get("micro_version")
        if major is not None and minor is not None:
            if micro is not None:
                return f"{major}.{minor}.{micro}"
            return f"{major}.{minor}"
    if isinstance(version, str) and version:
        return version
    return None


def run_probe(*, timeout: float = 2.0) -> ProbeReport:
    """Probe plugin with authenticated get_gimp_info."""
    host = _resolve_host()
    port = sec.get_port()
    data: dict[str, Any] = {
        "host": host,
        "port": port,
        "timeout": timeout,
    }

    token = load_probe_token()
    if not token:
        return ProbeReport(
            ok=False,
            code=sec.CODE_AUTH_FAILED,
            message=(
                f"No session token — set {sec.ENV_TOKEN} or start the GIMP MCP plugin "
                f"so it writes {sec.default_token_path()}"
            ),
            exit_code=ec.exit_code_for(sec.CODE_AUTH_FAILED),
            data=data,
        )

    data["token_source"] = "available"

    try:
        result = send_get_gimp_info(host=host, port=port, token=token, timeout=timeout)
    except TimeoutError:
        return ProbeReport(
            ok=False,
            code=sec.CODE_TIMEOUT,
            message=f"timeout after {timeout}s connecting/reading {host}:{port}",
            exit_code=ec.exit_code_for(sec.CODE_TIMEOUT),
            data=data,
        )
    except OSError as exc:
        return ProbeReport(
            ok=False,
            code=sec.CODE_CONNECTION_FAILED,
            message=f"connection failed to {host}:{port}: {exc}",
            exit_code=ec.exit_code_for(sec.CODE_CONNECTION_FAILED),
            data=data,
        )

    data["raw_status"] = result.get("status")
    code = result.get("code")
    if isinstance(code, str) and code == sec.CODE_AUTH_FAILED:
        return ProbeReport(
            ok=False,
            code=sec.CODE_AUTH_FAILED,
            message=str(result.get("error") or "authentication failed"),
            exit_code=ec.exit_code_for(sec.CODE_AUTH_FAILED),
            data=data,
        )
    if result.get("status") == "error":
        err_code = code if isinstance(code, str) else sec.CODE_CONNECTION_FAILED
        return ProbeReport(
            ok=False,
            code=err_code,
            message=str(result.get("error") or "probe error"),
            exit_code=ec.exit_code_for(err_code),
            data=data,
        )

    # Plugin real success is {"status":"success","results":...} from _get_gimp_info.
    # Do not treat {} or unexpected status as success.
    if result.get("status") != "success":
        raw = result.get("status")
        return ProbeReport(
            ok=False,
            code=sec.CODE_INTERNAL,
            message=(f"unexpected probe response status {raw!r} (expected status='success')"),
            exit_code=ec.exit_code_for(sec.CODE_INTERNAL),
            data=data,
        )

    version = _extract_version(result)
    data["gimp_version"] = version
    data["probe"] = "ok"
    return ProbeReport(
        ok=True,
        code=None,
        message="probe ok" + (f" (gimp {version})" if version else ""),
        exit_code=ec.EXIT_SUCCESS,
        data=data,
    )
