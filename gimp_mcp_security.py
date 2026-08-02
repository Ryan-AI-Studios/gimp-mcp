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


class SecurityError(Exception):
    """Policy violation with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)

    def as_error(self) -> dict[str, Any]:
        return make_error(self.code, self.message)


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


def make_error(code: str, message: str) -> dict[str, Any]:
    """Structured error envelope shared by plugin and server."""
    return {"status": "error", "error": message, "code": code}


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
# Audit log
# ---------------------------------------------------------------------------


def default_audit_log_path() -> Path:
    override = os.environ.get(ENV_AUDIT_LOG)
    if override and str(override).strip():
        return Path(str(override).strip())
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "gimp-mcp" / "audit.jsonl"
        return Path.home() / "AppData" / "Local" / "gimp-mcp" / "audit.jsonl"
    xdg = os.environ.get("XDG_STATE_HOME") or os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "gimp-mcp" / "audit.jsonl"
    return Path.home() / ".local" / "state" / "gimp-mcp" / "audit.jsonl"


def audit_log_path() -> Path:
    return default_audit_log_path()


def write_audit_event(
    event: Mapping[str, Any],
    path: Path | str | None = None,
) -> None:
    """Append one JSONL audit record. Never log tokens or file contents.

    Audit log is diagnostics only — not a secret and not tamper-evident.
    """
    dest = Path(path) if path is not None else audit_log_path()
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
