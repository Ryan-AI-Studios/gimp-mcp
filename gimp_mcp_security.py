"""Stdlib-only security policy for GIMP MCP bridge.

Deployable next to ``gimp-mcp-plugin.py`` under the GIMP plug-ins directory
(no third-party imports; no package-relative imports that require a venv).

Used by:
- the GIMP plug-in (TCP bind, auth, Class A exec gate, path jail, audit)
- the MCP server (connect host, token load, Class B call_api gate, path checks)
- offline unit tests

``GIMP_MCP_DEBUG`` expands diagnostics only — it never bypasses bind, auth,
exec, or path policy.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Env names (single source of truth for docs/tests)
# ---------------------------------------------------------------------------

ENV_HOST = "GIMP_MCP_HOST"
ENV_PORT = "GIMP_MCP_PORT"
ENV_TOKEN = "GIMP_MCP_TOKEN"
ENV_TOKEN_FILE = "GIMP_MCP_TOKEN_FILE"
ENV_WORKSPACE = "GIMP_WORKSPACE_ROOT"
ENV_ALLOW_EXEC = "GIMP_MCP_ALLOW_EXEC"
ENV_ALLOW_NON_LOOPBACK = "GIMP_MCP_ALLOW_NON_LOOPBACK"
ENV_DEBUG = "GIMP_MCP_DEBUG"
ENV_AUDIT_LOG = "GIMP_MCP_AUDIT_LOG"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9877

# Structured error codes
CODE_AUTH_FAILED = "AUTH_FAILED"
CODE_EXEC_DISABLED = "EXEC_DISABLED"
CODE_PATH_DENIED = "PATH_DENIED"
CODE_BIND_DENIED = "BIND_DENIED"
CODE_INTERNAL = "INTERNAL_ERROR"
# Handle registry (track 0007)
CODE_STALE_HANDLE = "STALE_HANDLE"
CODE_FOREIGN_SESSION = "FOREIGN_SESSION"
CODE_INVALID_HANDLE = "INVALID_HANDLE"
CODE_HANDLE_NOT_FOUND = "HANDLE_NOT_FOUND"
CODE_SELECTION_CONFLICT = "SELECTION_CONFLICT"
# Coordinate / EXIF normalize (track 0008)
CODE_METADATA_WRITE_FAILED = "METADATA_WRITE_FAILED"
# Layer policy + checkpoints (track 0009)
CODE_POLICY_DENIED = "POLICY_DENIED"
CODE_CONFIRM_REQUIRED = "CONFIRM_REQUIRED"
CODE_CHECKPOINT_EXISTS = "CHECKPOINT_EXISTS"
CODE_CHECKPOINT_NOT_FOUND = "CHECKPOINT_NOT_FOUND"
CODE_CHECKPOINT_CORRUPTED = "CHECKPOINT_CORRUPTED"
# Export alpha (track 0005) — also used in envelope matrix
CODE_ALPHA_LOST = "ALPHA_LOST"
# Structured errors envelope (track 0011)
CODE_PARTIAL_MUTATION = "PARTIAL_MUTATION"
CODE_CONNECTION_FAILED = "CONNECTION_FAILED"
CODE_TIMEOUT = "TIMEOUT"  # reserved
CODE_UNSUPPORTED = "UNSUPPORTED"  # reserved
# Atomic save/export (track 0013)
CODE_OUTPUT_COLLISION = "OUTPUT_COLLISION"
CODE_VERIFY_FAILED = "VERIFY_FAILED"
# Undo group transactions (track 0017)
CODE_TX_MISMATCH = "TX_MISMATCH"
CODE_TX_NOT_FOUND = "TX_NOT_FOUND"
CODE_TX_DEPTH = "TX_DEPTH"


class SecurityError(Exception):
    """Policy violation with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)

    def as_error(self) -> dict[str, Any]:
        return make_error(self.code, self.message)


# ---------------------------------------------------------------------------
# Error envelope v1 (track 0011)
# ---------------------------------------------------------------------------


class ErrorSpec(dict):
    """Defaults for a code: retryable, approval_required, state_may_have_changed."""


def _spec(
    *,
    retryable: bool = False,
    approval_required: bool = False,
    state_may_have_changed: bool = False,
) -> ErrorSpec:
    return ErrorSpec(
        retryable=retryable,
        approval_required=approval_required,
        state_may_have_changed=state_may_have_changed,
    )


CODE_DEFAULTS: dict[str, ErrorSpec] = {
    CODE_AUTH_FAILED: _spec(),
    CODE_EXEC_DISABLED: _spec(),
    CODE_PATH_DENIED: _spec(),
    CODE_BIND_DENIED: _spec(),
    CODE_INTERNAL: _spec(state_may_have_changed=True),
    CODE_STALE_HANDLE: _spec(retryable=True),
    CODE_FOREIGN_SESSION: _spec(),
    CODE_INVALID_HANDLE: _spec(),
    CODE_HANDLE_NOT_FOUND: _spec(),
    CODE_SELECTION_CONFLICT: _spec(retryable=True),
    CODE_METADATA_WRITE_FAILED: _spec(state_may_have_changed=True),
    CODE_POLICY_DENIED: _spec(),
    CODE_CONFIRM_REQUIRED: _spec(approval_required=True),
    CODE_CHECKPOINT_EXISTS: _spec(),
    CODE_CHECKPOINT_NOT_FOUND: _spec(),
    CODE_CHECKPOINT_CORRUPTED: _spec(state_may_have_changed=True),
    CODE_ALPHA_LOST: _spec(state_may_have_changed=True),
    CODE_PARTIAL_MUTATION: _spec(state_may_have_changed=True),
    CODE_CONNECTION_FAILED: _spec(retryable=True),
    CODE_TIMEOUT: _spec(retryable=True, state_may_have_changed=True),
    CODE_UNSUPPORTED: _spec(),
    # 0013: static CODE_DEFAULTS — both non-retryable; verify-on-temp leaves final intact
    CODE_OUTPUT_COLLISION: _spec(retryable=False),
    CODE_VERIFY_FAILED: _spec(retryable=False, state_may_have_changed=False),
    # 0017: agent undo TX stack errors — non-retryable; partial rollback overrides via kwargs
    CODE_TX_MISMATCH: _spec(retryable=False, state_may_have_changed=False),
    CODE_TX_NOT_FOUND: _spec(retryable=False, state_may_have_changed=False),
    CODE_TX_DEPTH: _spec(retryable=False, state_may_have_changed=False),
}


def new_request_id() -> str:
    """Mint a product request id: ``req_`` + uuid4.hex (32 hex chars)."""
    import uuid

    return "req_" + uuid.uuid4().hex


def _defaults_for(code: str) -> ErrorSpec:
    return CODE_DEFAULTS.get(code) or _spec(state_may_have_changed=True)


class GimpMcpError(Exception):
    """Product exception carrying envelope fields (host-side raises)."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        affected_handles: list[Any] | None = None,
        retryable: bool | None = None,
        approval_required: bool | None = None,
        state_may_have_changed: bool | None = None,
        rollback_available: bool = False,
        transaction_id: str | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.details = details
        self.affected_handles = list(affected_handles) if affected_handles else []
        defaults = _defaults_for(code)
        self.retryable = defaults["retryable"] if retryable is None else bool(retryable)
        self.approval_required = (
            defaults["approval_required"] if approval_required is None else bool(approval_required)
        )
        self.state_may_have_changed = (
            defaults["state_may_have_changed"]
            if state_may_have_changed is None
            else bool(state_may_have_changed)
        )
        self.rollback_available = bool(rollback_available)
        self.transaction_id = transaction_id
        super().__init__(message)

    def envelope(self, request_id: str | None = None) -> dict[str, Any]:
        return build_error_envelope(
            self.code,
            self.message,
            request_id=request_id or new_request_id(),
            details=self.details,
            affected_handles=self.affected_handles,
            transaction_id=self.transaction_id,
            retryable=self.retryable,
            approval_required=self.approval_required,
            state_may_have_changed=self.state_may_have_changed,
            rollback_available=self.rollback_available,
        )


def build_error_envelope(
    code: str,
    message: str,
    *,
    request_id: str,
    details: dict[str, Any] | None = None,
    affected_handles: list[Any] | None = None,
    transaction_id: str | None = None,
    retryable: bool | None = None,
    approval_required: bool | None = None,
    state_may_have_changed: bool | None = None,
    rollback_available: bool = False,
    **_overrides: Any,
) -> dict[str, Any]:
    """Full product envelope: ``{ok: false, error: {...}}`` (v1).

    ``rollback_available`` is honest post-0017: true when an open agent undo TX
    exists for the affected image (plugin SoT; host open-TX hint for pre-TCP).
    Default remains false when not set.
    """
    defaults = _defaults_for(code)
    err: dict[str, Any] = {
        "code": code,
        "message": str(message),
        "retryable": defaults["retryable"] if retryable is None else bool(retryable),
        "approval_required": (
            defaults["approval_required"] if approval_required is None else bool(approval_required)
        ),
        "request_id": request_id,
        "transaction_id": transaction_id,
        "state_may_have_changed": (
            defaults["state_may_have_changed"]
            if state_may_have_changed is None
            else bool(state_may_have_changed)
        ),
        "rollback_available": False if rollback_available is False else bool(rollback_available),
        "affected_handles": list(affected_handles) if affected_handles else [],
        "details": details,
    }
    return {"ok": False, "error": err}


def format_tool_error_text(envelope: dict[str, Any]) -> str:
    """Single-line ToolError wire text (exactly one line).

    Format::

        {CODE}: {message} (request_id={req_…}) | {"ok":false,"error":{...}}

    Newlines in message are sanitized to spaces. JSON uses compact separators.
    """
    err = envelope.get("error") if isinstance(envelope, dict) else None
    if not isinstance(err, dict):
        err = {}
    code = str(err.get("code") or CODE_INTERNAL)
    message = str(err.get("message") or "").replace("\n", " ").replace("\r", " ")
    rid = str(err.get("request_id") or "")
    # Ensure message in JSON body is also sanitized for single-line wire
    wire_env = dict(envelope)
    if isinstance(wire_env.get("error"), dict):
        wire_err = dict(wire_env["error"])
        wire_err["message"] = message
        wire_env["error"] = wire_err
    compact = json.dumps(wire_env, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"{code}: {message} (request_id={rid}) | {compact}"


def parse_tool_error_text(text: str) -> dict[str, Any] | None:
    """Parse single-line ToolError text → envelope dict, or None if malformed.

    The wire format is ``{CODE}: {message} (request_id=…) | {json}``. Both the
    human message and the JSON body may contain ``" | "`` (the message is
    duplicated in the JSON ``error.message`` field). A single ``rfind(" | ")``
    therefore selects the wrong split when the message contains that sequence.

    Strategy: try every ``" | "`` candidate from the right until the right-hand
    side parses as a valid envelope (``ok is False`` + ``error`` object).
    """
    if not text or not isinstance(text, str):
        return None
    marker = " | "
    starts: list[int] = []
    pos = 0
    while True:
        idx = text.find(marker, pos)
        if idx < 0:
            break
        starts.append(idx)
        pos = idx + 1
    if not starts:
        return None
    for idx in reversed(starts):
        json_part = text[idx + len(marker) :].strip()
        try:
            data = json.loads(json_part)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("ok") is not False:
            continue
        err = data.get("error")
        if not isinstance(err, dict):
            continue
        return data
    return None


# ---------------------------------------------------------------------------
# Env truthiness
# ---------------------------------------------------------------------------


def _env_truthy(name: str, default: str = "") -> bool:
    raw = os.environ.get(name, default)
    if raw is None:
        return False
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def exec_allowed() -> bool:
    """Class A (plugin cmds/eval) and Class B (MCP call_api) when True."""
    return _env_truthy(ENV_ALLOW_EXEC)


def debug_enabled() -> bool:
    """Tracebacks + verbose diagnostics only — never a policy bypass."""
    return _env_truthy(ENV_DEBUG)


def non_loopback_allowed() -> bool:
    return _env_truthy(ENV_ALLOW_NON_LOOPBACK)


# ---------------------------------------------------------------------------
# Loopback / bind
# ---------------------------------------------------------------------------


def is_loopback_host(host: str | None) -> bool:
    """Return True if host is a loopback literal (no DNS resolution)."""
    if host is None:
        return False
    h = str(host).strip().lower()
    return h in ("127.0.0.1", "::1", "0:0:0:0:0:0:0:1")


def assert_bind_host(host: str | None, *, warn: bool = True) -> str:
    """Validate bind/connect host.

    Default posture: only ``127.0.0.1`` (and ``::1`` if explicitly chosen).
    Non-loopback requires ``GIMP_MCP_ALLOW_NON_LOOPBACK=1`` **and** emits a loud
    warning when ``warn`` is True (stderr + optional audit caller).

    Bare ``localhost`` is always rejected (IPv6 dual-stack risk).
    """
    if host is None or str(host).strip() == "":
        return DEFAULT_HOST
    h = str(host).strip()
    if h.lower() == "localhost":
        # Prefer literal; do not silently rebind to dual-stack name.
        # ALLOW_NON_LOOPBACK does not enable "localhost" (IPv6 dual-stack risk remains).
        raise SecurityError(
            CODE_BIND_DENIED,
            "Host 'localhost' is not allowed; always use the literal 127.0.0.1 (AF_INET). "
            f"{ENV_ALLOW_NON_LOOPBACK}=1 is only for non-loopback IP addresses, not hostname aliases",
        )
    if is_loopback_host(h):
        return h
    if non_loopback_allowed():
        if warn:
            msg = (
                f"[MCP] WARNING: non-loopback host '{h}' allowed via "
                f"{ENV_ALLOW_NON_LOOPBACK}=1 — bind/connect is NOT loopback-restricted"
            )
            print(msg, file=sys.stderr)
        return h
    raise SecurityError(
        CODE_BIND_DENIED,
        f"Non-loopback bind/connect host '{h}' requires {ENV_ALLOW_NON_LOOPBACK}=1",
    )


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------


def verify_token(provided: str | None, expected: str | None) -> bool:
    """Constant-time token compare. None/empty never authenticates."""
    if provided is None or expected is None:
        return False
    a = str(provided)
    b = str(expected)
    if a == "" or b == "":
        return False
    try:
        return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
    except (TypeError, ValueError):
        return False


def generate_token() -> str:
    """Cryptographically strong session secret (url-safe, 32+ bytes entropy)."""
    return secrets.token_urlsafe(32)


def default_token_path() -> Path:
    """Platform default for the session token file."""
    override = os.environ.get(ENV_TOKEN_FILE)
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "gimp-mcp" / "session.token"
        return Path.home() / "AppData" / "Local" / "gimp-mcp" / "session.token"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "gimp-mcp" / "session.token"
    return Path.home() / ".config" / "gimp-mcp" / "session.token"


def _windows_restrict_acl(path: Path) -> None:
    """Best-effort: grant current user full control, remove inherited ACLs."""
    try:
        import subprocess

        user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
        if not user:
            return
        # /inheritance:r removes inherited ACEs; grant current user only.
        subprocess.run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"{user}:(R,W)",
            ],
            check=False,
            capture_output=True,
            timeout=15,
        )
    except Exception:
        # Residual: same-user readability; documented in SECURITY.md
        pass


def write_token_file(path: Path | str, token: str) -> Path:
    """Atomic write of token with restrictive permissions."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = token.encode("utf-8")
    # Write to temp in same dir then replace (atomic on POSIX/Windows for same volume).
    fd, tmp_name = tempfile.mkstemp(prefix=".token-", dir=str(dest.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if sys.platform != "win32":
            os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, dest)
        if sys.platform != "win32":
            try:
                os.chmod(dest, 0o600)
            except OSError:
                pass
        else:
            _windows_restrict_acl(dest)
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise
    return dest


def read_token_file(path: Path | str | None = None) -> str | None:
    """Read token file; return None if missing/empty/unreadable."""
    p = Path(path) if path is not None else default_token_path()
    try:
        text = p.read_text(encoding="utf-8").strip()
        return text if text else None
    except OSError:
        return None


def resolve_expected_token(
    *,
    generate_if_missing: bool = False,
    write_if_generated: bool = True,
    rotate_file_token: bool = False,
) -> tuple[str, Path | None, bool]:
    """Resolve session token.

    Returns ``(token, token_file_path_or_None, generated)``.

    Preference: ``GIMP_MCP_TOKEN`` env → token file → optional generate.

    When ``rotate_file_token=True`` (plugin startup), ignore any existing file
    token and mint a new one so a stale/compromised file token does not survive
    restarts. Env tokens are never rotated here.
    """
    env_tok = os.environ.get(ENV_TOKEN)
    if env_tok and str(env_tok).strip():
        return str(env_tok).strip(), None, False

    path = default_token_path()
    if not rotate_file_token:
        existing = read_token_file(path)
        if existing:
            return existing, path, False
        if not generate_if_missing:
            return "", path, False
    # rotate_file_token=True (plugin start) always mints a new file token;
    # generate_if_missing=True mints when no file exists.

    token = generate_token()
    written: Path | None = None
    if write_if_generated:
        written = write_token_file(path, token)
    return token, written or path, True


def load_token_with_retry(
    *,
    max_attempts: int = 10,
    base_delay_s: float = 0.25,
    max_delay_s: float = 2.0,
) -> str | None:
    """Lazy token load for MCP server (plugin may start later).

    Prefers env; else retries reading the default token file with backoff.
    """
    env_tok = os.environ.get(ENV_TOKEN)
    if env_tok and str(env_tok).strip():
        return str(env_tok).strip()

    path = default_token_path()
    delay = base_delay_s
    for attempt in range(max_attempts):
        tok = read_token_file(path)
        if tok:
            return tok
        if attempt + 1 >= max_attempts:
            break
        time.sleep(delay)
        delay = min(max_delay_s, delay * 1.5)
    return None


# ---------------------------------------------------------------------------
# Workspace / path jail
# ---------------------------------------------------------------------------


def workspace_root() -> Path | None:
    """Return configured workspace root or None if unset."""
    raw = os.environ.get(ENV_WORKSPACE)
    if raw is None or str(raw).strip() == "":
        return None
    return Path(str(raw).strip())


def _normalize_resolved(path: Path) -> Path:
    """Resolve and normalize Windows drive-letter casing for comparisons."""
    resolved = path.resolve()
    s = str(resolved)
    if sys.platform == "win32" and len(s) >= 2 and s[1] == ":":
        # Uppercase drive letter so C:\ and c:\ compare equal
        s = s[0].upper() + s[1:]
        return Path(s)
    return resolved


def resolve_under_root(path: str | Path, root: str | Path | None = None) -> Path:
    """Canonicalize ``path`` and ensure it stays under workspace root.

    Fail-closed: missing root → PATH_DENIED.
    Rejects ``..`` escapes, other drives, and UNC paths that leave root.

    Raises:
        SecurityError: with code PATH_DENIED
    """
    if root is None:
        root = workspace_root()
    if root is None or str(root).strip() == "":
        raise SecurityError(
            CODE_PATH_DENIED,
            f"Filesystem ops require {ENV_WORKSPACE} (workspace root unset)",
        )

    root_resolved = _normalize_resolved(Path(root))
    # Reject empty path
    if path is None or str(path).strip() == "":
        raise SecurityError(CODE_PATH_DENIED, "Empty path denied")

    candidate = Path(path)
    # Absolute or relative: resolve against root for relative paths
    if not candidate.is_absolute():
        candidate = root_resolved / candidate
    try:
        target = _normalize_resolved(candidate)
    except (OSError, RuntimeError) as e:
        raise SecurityError(CODE_PATH_DENIED, f"Path resolve failed: {e}") from e

    # UNC outside a UNC root: reject if target is UNC and root is not under same share
    target_s = str(target)
    root_s = str(root_resolved)
    if target_s.startswith("\\\\") and not root_s.startswith("\\\\"):
        raise SecurityError(CODE_PATH_DENIED, "UNC path outside workspace root denied")

    try:
        # Python 3.9+ Path.is_relative_to
        if hasattr(target, "is_relative_to"):
            if not target.is_relative_to(root_resolved):
                raise SecurityError(
                    CODE_PATH_DENIED,
                    f"Path escapes workspace root: {target}",
                )
        else:
            common = os.path.commonpath([root_s, target_s])
            if _normalize_resolved(Path(common)) != root_resolved:
                raise SecurityError(
                    CODE_PATH_DENIED,
                    f"Path escapes workspace root: {target}",
                )
    except ValueError as e:
        # different drives on Windows → commonpath raises ValueError
        raise SecurityError(
            CODE_PATH_DENIED,
            f"Path escapes workspace root: {target}",
        ) from e

    return target


def check_path_under_root(
    path: str | Path,
    root: str | Path | None = None,
) -> tuple[Path | None, dict[str, Any] | None]:
    """Non-raising path check: ``(resolved, None)`` or ``(None, error_dict)``."""
    try:
        return resolve_under_root(path, root), None
    except SecurityError as e:
        return None, e.as_error()


# ---------------------------------------------------------------------------
# Errors / redaction
# ---------------------------------------------------------------------------


def make_error(
    code: str,
    message: str,
    *,
    request_id: str | None = None,
    retryable: bool | None = None,
    approval_required: bool | None = None,
    state_may_have_changed: bool | None = None,
    rollback_available: bool | None = None,
    affected_handles: list[Any] | None = None,
    details: dict[str, Any] | None = None,
    transaction_id: str | None = None,
) -> dict[str, Any]:
    """TCP/plugin structured error dict (status/error/code + optional additive fields).

    Base shape is unchanged when no optional kwargs are passed (back-compat).
    Additive fields support request_id audit correlation and envelope matrix flags.
    """
    body: dict[str, Any] = {"status": "error", "error": message, "code": code}
    if request_id is not None:
        body["request_id"] = request_id
    if retryable is not None:
        body["retryable"] = bool(retryable)
    if approval_required is not None:
        body["approval_required"] = bool(approval_required)
    if state_may_have_changed is not None:
        body["state_may_have_changed"] = bool(state_may_have_changed)
    if rollback_available is not None:
        body["rollback_available"] = bool(rollback_available)
    if affected_handles is not None:
        body["affected_handles"] = list(affected_handles)
    if details is not None:
        body["details"] = details
    if transaction_id is not None:
        body["transaction_id"] = transaction_id
    return body


def redact_error(
    exc: BaseException,
    *,
    code: str = CODE_INTERNAL,
    message: str | None = None,
) -> dict[str, Any]:
    """Build error dict; include traceback only when ``GIMP_MCP_DEBUG`` is set.

    DEBUG does not change policy flags — only diagnostic detail.
    """
    msg = message if message is not None else str(exc)
    body = make_error(code, msg)
    if debug_enabled():
        import traceback

        body["traceback"] = traceback.format_exc()
    return body


def strip_traceback_unless_debug(response: Mapping[str, Any]) -> dict[str, Any]:
    """Copy response, drop traceback unless debug, ensure error ``code`` present.

    Legacy handlers that only set ``status``/``error`` get ``code=INTERNAL_ERROR``
    so the wire envelope always carries a structured code (DoD-6).

    Also sanitizes ``error`` strings that embedded ``traceback.format_exc()``
    into the message body (common pre-0003 pattern).
    """
    out = dict(response)
    if not debug_enabled():
        out.pop("traceback", None)
        err = out.get("error")
        if isinstance(err, str) and "Traceback (most recent call last)" in err:
            # Keep the first line / message before the traceback dump.
            head = err.split("Traceback (most recent call last)", 1)[0].rstrip("\n: ")
            out["error"] = head if head else "Internal error"
    if out.get("status") == "error" and "code" not in out:
        out["code"] = CODE_INTERNAL
    return out


# ---------------------------------------------------------------------------
# Audit log (split server / plugin files — track 0011)
# ---------------------------------------------------------------------------
#
# Default: ``…/gimp-mcp/audit-server.jsonl`` and ``…/gimp-mcp/audit-plugin.jsonl``.
#
# ``GIMP_MCP_AUDIT_LOG`` override rules:
# - If the value ends with ``.jsonl`` (file path), use that path's **directory**
#   and write sibling names ``audit-server.jsonl`` / ``audit-plugin.jsonl``.
# - Otherwise treat the value as a **directory** and place those filenames under it.
#
# Split files avoid Windows WinError 32 sharing violations when host and plugin
# append concurrently. Join events by ``request_id``.
# Never log tokens, auth secrets, or file bytes.


def _default_audit_dir() -> Path:
    """Platform default directory for audit JSONL files (no filename)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "gimp-mcp"
        return Path.home() / "AppData" / "Local" / "gimp-mcp"
    xdg = os.environ.get("XDG_STATE_HOME") or os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "gimp-mcp"
    return Path.home() / ".local" / "state" / "gimp-mcp"


def audit_dir() -> Path:
    """Resolve audit directory from ``GIMP_MCP_AUDIT_LOG`` or platform default."""
    override = os.environ.get(ENV_AUDIT_LOG)
    if override and str(override).strip():
        p = Path(str(override).strip())
        # File path ending in .jsonl → sibling files in same directory
        if p.suffix.lower() == ".jsonl" or str(p).lower().endswith(".jsonl"):
            return p.parent if str(p.parent) not in ("", ".") else Path(".")
        return p
    return _default_audit_dir()


def audit_server_path() -> Path:
    """Host MCP tool audit log (mcp_tool_start / mcp_tool_end)."""
    return audit_dir() / "audit-server.jsonl"


def audit_plugin_path() -> Path:
    """Plugin TCP command audit log (command / command_complete / auth / …)."""
    return audit_dir() / "audit-plugin.jsonl"


def default_audit_log_path() -> Path:
    """Deprecated alias: plugin audit path (pre-0011 single-file default)."""
    return audit_plugin_path()


def audit_log_path() -> Path:
    """Back-compat alias for ``audit_plugin_path()`` (pre-0011 callers)."""
    return audit_plugin_path()


def write_audit_event(
    event: Mapping[str, Any],
    path: Path | str | None = None,
) -> None:
    """Append one JSONL audit record. Never log tokens or file contents.

    Audit log is diagnostics only — not a secret and not tamper-evident.
    Prefer ``audit_server_path()`` / ``audit_plugin_path()`` over the default.
    """
    dest = Path(path) if path is not None else audit_plugin_path()
    # Strip any accidental sensitive keys
    safe = {k: v for k, v in event.items() if k.lower() not in ("auth", "token", "secret")}
    if "timestamp" not in safe:
        safe["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(safe, ensure_ascii=False, default=str) + "\n"
        with dest.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        # Audit failure must not break the bridge
        pass


# ---------------------------------------------------------------------------
# Config helpers for plugin / server env plumbing
# ---------------------------------------------------------------------------


def get_host() -> str:
    """Return configured host defaulting to 127.0.0.1 (validated)."""
    raw = os.environ.get(ENV_HOST, DEFAULT_HOST)
    return assert_bind_host(raw if raw else DEFAULT_HOST)


def get_port() -> int:
    raw = os.environ.get(ENV_PORT, str(DEFAULT_PORT))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PORT
