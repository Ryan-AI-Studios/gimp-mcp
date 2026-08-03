"""JSON envelope helpers and JSON-mode resolution for gimp-agent."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

ENV_JSON = "GIMP_AGENT_JSON"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def json_mode_enabled(*, flag: bool | None = None) -> bool:
    """Resolve JSON output mode: ``--json`` flag wins; else ``GIMP_AGENT_JSON``.

    Truthy env values: ``1``, ``true``, ``yes``, ``on`` (case-insensitive).
    """
    if flag is True:
        return True
    if flag is False:
        return False
    raw = os.environ.get(ENV_JSON, "")
    return str(raw).strip().lower() in _TRUTHY


def make_envelope(
    *,
    ok: bool,
    exit_code: int,
    code: str | None = None,
    message: str = "",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the CLI stdout envelope contract."""
    return {
        "ok": bool(ok),
        "exit_code": int(exit_code),
        "code": code,
        "message": message,
        "data": data if data is not None else {},
    }


def emit(
    envelope: dict[str, Any],
    *,
    as_json: bool,
    human_lines: list[str] | None = None,
) -> None:
    """Write envelope to stdout (JSON) or human text; always set exit via caller."""
    if as_json:
        sys.stdout.write(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n")
        return
    lines = human_lines if human_lines is not None else _default_human(envelope)
    text = "\n".join(lines)
    if text and not text.endswith("\n"):
        text += "\n"
    # Always write human mode to stdout so agent pipes capture the summary
    # (including failures). Process exit code remains the failure signal.
    sys.stdout.write(text if text else "")


def _default_human(envelope: dict[str, Any]) -> list[str]:
    ok = envelope.get("ok")
    code = envelope.get("code")
    msg = envelope.get("message") or ""
    exit_n = envelope.get("exit_code")
    status = "ok" if ok else "fail"
    head = f"[{status}] exit={exit_n}"
    if code:
        head += f" code={code}"
    lines = [head]
    if msg:
        lines.append(msg)
    data = envelope.get("data")
    if isinstance(data, dict) and data:
        # Compact one-liner hints for common keys
        for key in ("agent_version", "gimp_version", "gimp_console_path", "plugin_dir"):
            if key in data and data[key] is not None:
                lines.append(f"  {key}: {data[key]}")
        checks = data.get("checks")
        if isinstance(checks, list):
            for item in checks:
                if not isinstance(item, dict):
                    continue
                name = item.get("name", "?")
                severity = item.get("severity", "")
                status_s = item.get("status", "")
                detail = item.get("message") or item.get("detail") or ""
                line = f"  - {name}: {status_s}"
                if severity:
                    line += f" ({severity})"
                if detail:
                    line += f" — {detail}"
                lines.append(line)
    return lines
