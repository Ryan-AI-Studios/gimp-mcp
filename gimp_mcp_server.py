#!/usr/bin/env python3
# GIMP MCP Server Script — improved fork
# Adds: new_canvas, check_server, restart_server, no bitmap size restrictions
# Security (0003): loopback AF_INET, session auth, path jail, call_api gated
# Structured errors (0011): ToolError envelope + request_id audit correlation

import base64
import contextvars
import functools
import json
import logging
import os
import socket
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn, TypeVar

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools.tool import ToolResult
from fastmcp.utilities.types import Image
from mcp.types import Annotations, TextContent, ToolAnnotations

import gimp_mcp_coords as coords
import gimp_mcp_filters as filters
import gimp_mcp_security as sec
import gimp_mcp_snapshot as snap
import gimp_mcp_surface as surface
import gimp_mcp_verify as verify
from gimp_mcp_state import default_capabilities, finalize_manifest

F = TypeVar("F", bound=Callable[..., Any])

# Per-tool-invocation request_id (send_command reads this for TCP _request_id)
_current_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "gimp_mcp_request_id", default=None
)

# Per-tool-invocation image_id harvested from handle/layer_handle kwargs (0017 P2-2).
# tool_fail / raise_from_plugin_result read this when image_id is not passed explicitly.
_current_tool_image_id: contextvars.ContextVar[int | str | None] = contextvars.ContextVar(
    "gimp_mcp_tool_image_id", default=None
)

# Host open-TX hint cache (0017 AI2 BS3): image_id str → top transaction_id.
# Updated only from successful undo_group_begin/end/rollback tool results.
# Plugin remains SoT for any error returned via TCP.
_HOST_OPEN_TX: dict[str, str] = {}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("GimpMCPServer")

# Env plumbing — never default to bare "localhost" (IPv6 dual-stack risk).
try:
    GIMP_HOST = sec.get_host()
except sec.SecurityError as _bind_err:
    logger.warning("%s; using 127.0.0.1", _bind_err)
    GIMP_HOST = sec.DEFAULT_HOST
GIMP_PORT = sec.get_port()

# Lazy session token (plugin may write the file after MCP server process starts).
# File-backed tokens rotate on plugin start — cache must be cleared on restart /
# AUTH_FAILED so a long-lived MCP process reloads the new file token.
_session_token: str | None = None
_token_load_attempted = False


def _clear_session_token() -> None:
    """Invalidate cached session token (plugin restart / AUTH_FAILED recovery)."""
    global _session_token, _token_load_attempted
    _session_token = None
    _token_load_attempted = False


def _ensure_session_token(*, force_reload: bool = False) -> str | None:
    """Prefer GIMP_MCP_TOKEN env; else retry-read token file on first use."""
    global _session_token, _token_load_attempted
    if force_reload:
        _clear_session_token()
    if _session_token:
        return _session_token
    env_tok = os.environ.get(sec.ENV_TOKEN)
    if env_tok and str(env_tok).strip():
        _session_token = str(env_tok).strip()
        return _session_token
    # Lazy retry/backoff — start order: GIMP plugin first → token file → MCP tools
    tok = sec.load_token_with_retry()
    _token_load_attempted = True
    if tok:
        _session_token = tok
        logger.info("Loaded session token from %s", sec.default_token_path())
    else:
        logger.warning(
            "No session token yet (set %s or start GIMP plugin to write %s)",
            sec.ENV_TOKEN,
            sec.default_token_path(),
        )
    return _session_token


def get_current_request_id() -> str | None:
    """Return the request_id minted by ``with_structured_error`` for this call."""
    return _current_request_id.get()


def _jail_path_or_raise(path: str, label: str = "path") -> str:
    """Defense-in-depth path check before sending to the plug-in.

    Re-raises ``sec.SecurityError`` with its ``.code`` intact (0011 H5) so
    callers / ``raise_from_exception`` map PATH_DENIED correctly.
    """
    try:
        return str(sec.resolve_under_root(path))
    except sec.SecurityError:
        raise


def _snapshot_tool_result(
    plugin_results: dict[str, Any],
    *,
    image_index: int = 0,
    write_filesystem: bool | None = None,
) -> ToolResult:
    """Build MCP ToolResult for a visible-composite snapshot.

    Content order (locked):
    1. **TextContent** — compact JSON mirror of the structured mapping
       (includes ``filesystem_path`` when the jailed write succeeds) so clients
       that ignore ``structuredContent`` still surface the path.
    2. **ImageContent** — PNG vision payload (same as pre-0021).

    Dual-delivery (track 0021): when filesystem write is enabled (param or
    ``GIMP_MCP_SNAPSHOT_WRITE``, default on), PNG bytes are also written under
    ``{workspace}/.gimp-mcp-tmp/snapshots/`` and ``filesystem_*`` fields are
    merged into the mapping. Write failure is **non-fatal** — ImageContent is
    still returned with ``filesystem_write: false`` (+ optional
    ``filesystem_error``).
    """
    base64_data = plugin_results["image_data"]
    as_bytes = base64.b64decode(base64_data)

    rendered_w = int(plugin_results.get("width") or plugin_results.get("rendered_width") or 0)
    rendered_h = int(plugin_results.get("height") or plugin_results.get("rendered_height") or 0)
    source_w = int(
        plugin_results.get("original_width") or plugin_results.get("source_width") or rendered_w
    )
    source_h = int(
        plugin_results.get("original_height") or plugin_results.get("source_height") or rendered_h
    )
    idx = int(plugin_results.get("image_index", image_index))
    region = plugin_results.get("region")
    composite_method = plugin_results.get("composite_method", snap.COMPOSITE_METHOD_MERGE)

    # Prefer plugin-supplied mapping fields; rebuild if incomplete.
    # Additive 0008 keys must be copied/defaulted on the pass-through path (H5).
    def _additive_from_plugin(src: dict[str, Any]) -> dict[str, Any]:
        return {
            "coordinate_space": src.get("coordinate_space", "image-pixels"),
            "origin": src.get("origin", "top-left"),
            "x_axis": src.get("x_axis", "right"),
            "y_axis": src.get("y_axis", "down"),
            "preview_padding_x": int(src.get("preview_padding_x", 0) or 0),
            "preview_padding_y": int(src.get("preview_padding_y", 0) or 0),
            "view_rotation_ignored": bool(src.get("view_rotation_ignored", True)),
            "pixel_orientation_normalized": bool(src.get("pixel_orientation_normalized", False)),
            "exif_orientation_original": src.get("exif_orientation_original"),
        }

    if all(
        k in plugin_results
        for k in (
            "mode",
            "scale_x",
            "scale_y",
            "source_width",
            "source_height",
            "rendered_width",
            "rendered_height",
        )
    ):
        mapping: dict[str, Any] = {
            "mode": plugin_results["mode"],
            "image_index": idx,
            "source_width": int(plugin_results["source_width"]),
            "source_height": int(plugin_results["source_height"]),
            "rendered_width": int(plugin_results["rendered_width"]),
            "rendered_height": int(plugin_results["rendered_height"]),
            "scale_x": float(plugin_results["scale_x"]),
            "scale_y": float(plugin_results["scale_y"]),
            "region": region,
            "composite_method": composite_method,
        }
        mapping.update(_additive_from_plugin(plugin_results))
    else:
        mapping = snap.build_mapping_metadata(
            image_index=idx,
            source_width=source_w,
            source_height=source_h,
            rendered_width=rendered_w,
            rendered_height=rendered_h,
            region=region,
            composite_method=str(composite_method),
            pixel_orientation_normalized=bool(
                plugin_results.get("pixel_orientation_normalized", False)
            ),
            exif_orientation_original=plugin_results.get("exif_orientation_original"),
        )
        # Prefer any plugin-supplied additive overrides when present
        for k, v in _additive_from_plugin(plugin_results).items():
            if k in plugin_results:
                mapping[k] = v

    # Dual-delivery filesystem write (non-fatal).
    if snap.snapshot_write_enabled(param=write_filesystem):
        write_result = snap.write_snapshot_png(as_bytes, param=True)
    else:
        write_result = {
            "ok": False,
            "filesystem_write": False,
            "filesystem_path": None,
        }
    snap.merge_filesystem_fields(mapping, write_result)

    img = Image(data=as_bytes, format="png")
    image_content = img.to_image_content()
    image_content.annotations = Annotations(audience=["user", "assistant"])
    text_content = TextContent(
        type="text",
        text=json.dumps(mapping, separators=(",", ":")),
    )
    # Order: TextContent first (mapping + filesystem_path), then ImageContent.
    return ToolResult(content=[text_content, image_content], structured_content=mapping)


class GimpConnection:
    def __init__(self, host: str | None = None, port: int | None = None):
        try:
            self.host = sec.assert_bind_host(host if host is not None else GIMP_HOST)
        except sec.SecurityError:
            self.host = sec.DEFAULT_HOST
        self.port = int(port if port is not None else GIMP_PORT)
        self.sock: socket.socket | None = None

    def connect(self):
        if self.sock:
            return
        try:
            # AF_INET + literal 127.0.0.1 — no hostname resolution on default path
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(10)
            self.sock.connect((self.host, self.port))
            logger.info(f"Connected to GIMP at {self.host}:{self.port}")
        except Exception as e:
            self.sock = None
            logger.error(f"Failed to connect: {e}")
            raise ConnectionError(
                f"Could not connect to GIMP at {self.host}:{self.port}. "
                "Ensure the MCP Server plugin is running (Tools > Start MCP Server)."
            )

    def disconnect(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def send_command(
        self,
        command_type: str,
        params: dict[str, Any] | None = None,
        *,
        request_id: str | None = None,
    ):
        """Send authenticated JSON; reload token once on AUTH_FAILED (plugin rotated).

        Copies ``params`` and injects ``_request_id`` from the explicit arg or the
        current contextvar (minted by ``with_structured_error``). Never mutates
        the caller's dict (0011 M1).
        """
        p = dict(params) if params else {}
        rid = request_id if request_id is not None else get_current_request_id()
        if rid:
            p["_request_id"] = rid
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                return self._send_command_once(command_type, p, force_reload_token=(attempt > 0))
            except Exception as e:
                last_error = e
                raise
        if last_error:
            raise last_error
        raise RuntimeError("send_command failed without error")

    def _send_command_once(
        self,
        command_type: str,
        params: dict[str, Any] | None,
        *,
        force_reload_token: bool,
    ) -> Any:
        if not self.sock:
            self.connect()
        sock = self.sock
        if sock is None:
            raise ConnectionError(
                f"Could not connect to GIMP at {self.host}:{self.port}. "
                "Ensure the MCP Server plugin is running (Tools > Start MCP Server)."
            )
        token = _ensure_session_token(force_reload=force_reload_token)
        if not token:
            raise ConnectionError(
                "No session token available — refusing to send unauthenticated TCP "
                f"JSON. Set {sec.ENV_TOKEN} or start the GIMP MCP plugin first so it "
                f"writes {sec.default_token_path()}."
            )
        # params already copied + _request_id injected by send_command
        wire_params = dict(params) if params else {}
        command: dict[str, Any] = {
            "type": command_type,
            "params": wire_params,
            "auth": token,
        }
        try:
            sock.sendall(json.dumps(command).encode("utf-8") + b"\n")
            response_data = b""
            while True:
                chunk = sock.recv(8192)
                if not chunk:
                    break
                response_data += chunk
                try:
                    json.loads(response_data.decode("utf-8"))
                    break
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
            result = json.loads(response_data.decode("utf-8"))
            # Plugin may have rotated file token after GIMP restart
            if (
                isinstance(result, dict)
                and result.get("code") == sec.CODE_AUTH_FAILED
                and not force_reload_token
                and not os.environ.get(sec.ENV_TOKEN)
            ):
                logger.info("AUTH_FAILED — reloading session token and retrying once")
                _clear_session_token()
                self.disconnect()
                return self._send_command_once(command_type, params, force_reload_token=True)
            return result
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.error("Communication error: %s", e)
            # M8: map transport failures to CONNECTION_FAILED via raise path
            raise ConnectionError(f"Error communicating with GIMP: {e}") from e
        except Exception as e:
            logger.error("Communication error: %s", e)
            raise ConnectionError(f"Error communicating with GIMP: {e}") from e
        finally:
            self.disconnect()


# Global connection
_gimp_connection: GimpConnection | None = None


def get_gimp_connection() -> GimpConnection:
    global _gimp_connection
    if _gimp_connection is None:
        _gimp_connection = GimpConnection()
        _gimp_connection.connect()
    return _gimp_connection


def reset_gimp_connection() -> None:
    global _gimp_connection
    if _gimp_connection:
        _gimp_connection.disconnect()
    _gimp_connection = None
    # Plugin may regenerate file token on restart; drop cached secret.
    _clear_session_token()


# ---------------------------------------------------------------------------
# Structured error helpers (track 0011)
# ---------------------------------------------------------------------------

# Extra top-level keys on plugin TCP error dicts that become envelope.details
_PLUGIN_DETAIL_KEYS = (
    "left_on_disk",
    "final_intact",
    "png_color_type",
    "property_errors",
    "preflight_has_alpha",
    "preserve_alpha",
    "file_path",
    "format",
    "export_method",
    "pdb_procedure",
    "alpha_verified",
    "collision",
    "backup_path",
)


def _host_audit(event: str, **fields: Any) -> None:
    """Append one host-side audit line to audit-server.jsonl (never tokens)."""
    payload: dict[str, Any] = {"event": event, "side": "server"}
    payload.update(fields)
    sec.write_audit_event(payload, sec.audit_server_path())


def _host_tx_hint_set(image_id: int | str, transaction_id: str) -> None:
    """Record top open agent TX for image (best-effort host pre-TCP honesty)."""
    _HOST_OPEN_TX[str(image_id)] = str(transaction_id)


def _host_tx_hint_clear(image_id: int | str) -> None:
    _HOST_OPEN_TX.pop(str(image_id), None)


def _host_tx_hint_get(image_id: int | str) -> str | None:
    return _HOST_OPEN_TX.get(str(image_id))


def _host_tx_hint_update_from_results(
    results: dict[str, Any],
    *,
    image_id: int | str | None = None,
    op: str,
) -> None:
    """Update host open-TX hint from successful begin/end/rollback results."""
    iid = image_id
    if iid is None:
        handle = results.get("image_handle") or results.get("handle")
        if isinstance(handle, dict) and handle.get("image_id") is not None:
            iid = handle["image_id"]
    if iid is None:
        return
    if op == "begin":
        tid = results.get("transaction_id")
        if tid:
            _host_tx_hint_set(iid, str(tid))
        return
    if op in ("end", "rollback"):
        # After closing top, depth_remaining > 0 means a new top may exist —
        # status would be needed; clear when depth_remaining is 0, else keep
        # if results provide remaining top id, else clear (next begin resets).
        depth_rem = results.get("depth_remaining")
        if depth_rem is not None and int(depth_rem) <= 0:
            _host_tx_hint_clear(iid)
        elif op == "rollback" or results.get("status") in ("committed", "rolled_back"):
            # Prefer explicit remaining top if provided; otherwise clear
            remaining = results.get("top_transaction_id")
            if remaining:
                _host_tx_hint_set(iid, str(remaining))
            else:
                _host_tx_hint_clear(iid)


def _image_id_from_handle(handle: dict | None) -> int | str | None:
    if isinstance(handle, dict) and handle.get("image_id") is not None:
        return handle["image_id"]
    return None


def _image_id_from_tool_kwargs(kwargs: dict[str, Any]) -> int | str | None:
    """Harvest image_id from common tool handle kwargs (handle / layer_handle / …)."""
    for key in (
        "handle",
        "layer_handle",
        "image_handle",
        "source_handle",
        "destination_handle",
        "item_handle",
        "drawable_handle",
        "mask_handle",
    ):
        val = kwargs.get(key)
        if isinstance(val, dict) and val.get("image_id") is not None:
            return val["image_id"]
    return None


def get_current_tool_image_id() -> int | str | None:
    """Return image_id harvested by ``with_structured_error`` for this tool call."""
    return _current_tool_image_id.get()


def tool_fail(
    code: str,
    message: str,
    *,
    request_id: str | None = None,
    affected_handles: list[Any] | None = None,
    details: dict[str, Any] | None = None,
    cause: BaseException | None = None,
    image_id: int | str | None = None,
    **kw: Any,
) -> NoReturn:
    """Build envelope, raise single-line ToolError (MCP isError path)."""
    # Host open-TX hint for pre-TCP / missing-plugin-field honesty (0017).
    # Prefer explicit image_id; else contextvar from with_structured_error.
    if image_id is None:
        image_id = _current_tool_image_id.get()
    if "rollback_available" not in kw and image_id is not None:
        hint_tid = _host_tx_hint_get(image_id)
        if hint_tid:
            kw = {**kw, "rollback_available": True, "transaction_id": hint_tid}
    rid = request_id or get_current_request_id() or sec.new_request_id()
    envelope = sec.build_error_envelope(
        code,
        message,
        request_id=rid,
        affected_handles=affected_handles,
        details=details,
        **{
            k: v
            for k, v in kw.items()
            if k
            in (
                "retryable",
                "approval_required",
                "state_may_have_changed",
                "transaction_id",
                "rollback_available",
            )
        },
    )
    text = sec.format_tool_error_text(envelope)
    if cause is not None:
        raise ToolError(text) from cause
    raise ToolError(text) from None


def raise_from_plugin_result(
    result: dict[str, Any],
    tool_name: str,
    *,
    request_id: str | None = None,
    affected_handles: list[Any] | None = None,
    image_id: int | str | None = None,
) -> NoReturn:
    """Map plugin TCP error dict → ToolError with full envelope (incl. details).

    Plugin is SoT for TCP errors: top-level ``rollback_available`` /
    ``transaction_id`` are forwarded as tool_fail kwargs. When omitted, defaults
    to ``rollback_available=False`` — host open-TX hint is **not** applied here
    (hint is for pre-TCP host-side tool_fail only).
    """
    code = str(result.get("code") or sec.CODE_INTERNAL)
    raw_msg = result.get("error", "Unknown error")
    message = str(raw_msg) if raw_msg is not None else "Unknown error"
    if not message.startswith(tool_name):
        message = f"{tool_name} failed: {message}"

    details: dict[str, Any] = {}
    nested = result.get("details")
    if isinstance(nested, dict):
        details.update(nested)
    for key in _PLUGIN_DETAIL_KEYS:
        if key in result:
            details[key] = result[key]
    # Also accept request_id from plugin TCP if host lost context
    rid = request_id or result.get("request_id") or get_current_request_id()
    handles = affected_handles
    if handles is None and result.get("affected_handles") is not None:
        ah = result.get("affected_handles")
        handles = list(ah) if isinstance(ah, list) else None

    # 0017 H1: top-level rollback fields → tool_fail kwargs (not details-only).
    # Plugin is SoT for TCP errors: do NOT merge host open-TX hint here (stale
    # after plugin-side reap/close). Host hint is for pre-TCP tool_fail only.
    extra: dict[str, Any] = {}
    if "rollback_available" in result:
        extra["rollback_available"] = bool(result.get("rollback_available"))
    else:
        # Explicit false when plugin omitted fields → no open agent TX on SoT
        extra["rollback_available"] = False
    if "transaction_id" in result and result.get("transaction_id") is not None:
        extra["transaction_id"] = result.get("transaction_id")

    tool_fail(
        code,
        message,
        request_id=rid if isinstance(rid, str) else None,
        affected_handles=handles,
        details=details or None,
        # No image_id: prevents tool_fail host-hint fill on TCP path
        **extra,
    )


def raise_from_exception(
    exc: BaseException,
    *,
    request_id: str | None = None,
    tool_name: str | None = None,
    affected_handles: list[Any] | None = None,
    image_id: int | str | None = None,
) -> NoReturn:
    """Map host exceptions → structured ToolError.

    - ToolError: re-raise unchanged
    - SecurityError / GimpMcpError: use their code
    - ConnectionError / TimeoutError / OSError: CONNECTION_FAILED
    - else: INTERNAL_ERROR (pass affected_handles when known)

    ``image_id`` enables host open-TX hint merge for pre-TCP honesty (0017).
    """
    rid = request_id or get_current_request_id() or sec.new_request_id()
    if image_id is None:
        image_id = _current_tool_image_id.get()

    if isinstance(exc, ToolError):
        raise exc

    if isinstance(exc, sec.GimpMcpError):
        gimp_kw: dict[str, Any] = {
            "retryable": exc.retryable,
            "approval_required": exc.approval_required,
            "state_may_have_changed": exc.state_may_have_changed,
        }
        # Only set rollback kwargs when true/non-null so host TX hint can still merge.
        if exc.rollback_available:
            gimp_kw["rollback_available"] = True
        if exc.transaction_id is not None:
            gimp_kw["transaction_id"] = exc.transaction_id
        tool_fail(
            exc.code,
            exc.message,
            request_id=rid,
            affected_handles=affected_handles or exc.affected_handles or None,
            details=exc.details,
            cause=exc,
            image_id=image_id,
            **gimp_kw,
        )

    if isinstance(exc, sec.SecurityError):
        tool_fail(
            exc.code,
            exc.message,
            request_id=rid,
            affected_handles=affected_handles,
            cause=exc,
            image_id=image_id,
        )

    if isinstance(exc, (ConnectionError, TimeoutError)):
        tool_fail(
            sec.CODE_CONNECTION_FAILED,
            str(exc) or "Connection to GIMP failed",
            request_id=rid,
            affected_handles=affected_handles,
            cause=exc,
            image_id=image_id,
        )

    if isinstance(exc, OSError):
        # Broken pipe / refused / reset → transport
        tool_fail(
            sec.CODE_CONNECTION_FAILED,
            str(exc) or "OS transport error talking to GIMP",
            request_id=rid,
            affected_handles=affected_handles,
            cause=exc,
            image_id=image_id,
        )

    prefix = f"{tool_name} failed: " if tool_name else ""
    msg = f"{prefix}{exc}" if str(exc) else f"{prefix}Internal error"
    if sec.debug_enabled():
        logger.exception("tool error (%s)", tool_name or "?")
    tool_fail(
        sec.CODE_INTERNAL,
        msg,
        request_id=rid,
        affected_handles=affected_handles,
        cause=exc,
        image_id=image_id,
    )


def _harvest_affected_handles(kwargs: dict[str, Any]) -> list[Any] | None:
    """Collect known handle kwargs for INTERNAL_ERROR affected_handles (M5).

    - ``handle`` if present and dict-like → include once
    - ``layer_handle`` if present and dict-like → include
    - ``handles`` if present and list → extend
    Returns None when nothing harvested (omit empty list on wire).
    """
    out: list[Any] = []
    handle = kwargs.get("handle")
    if isinstance(handle, dict):
        out.append(handle)
    layer_handle = kwargs.get("layer_handle")
    if isinstance(layer_handle, dict):
        out.append(layer_handle)
    handles = kwargs.get("handles")
    if isinstance(handles, list):
        out.extend(item for item in handles if item is not None)
    return out or None


def with_structured_error(tool_name: str | None = None) -> Callable[[F], F]:
    """Decorator: mint request_id, host audit start/end, map exceptions → ToolError.

    Success path does **not** inject request_id into return dicts (v1 M4).
    On non-ToolError exceptions, harvests ``handle`` / ``handles`` kwargs into
    affected_handles for INTERNAL_ERROR (and other mapped codes) when known.
    Sets ``_current_tool_image_id`` from handle/layer_handle so tool_fail and
    raise_from_plugin_result can apply host open-TX hint without per-tool wiring.
    """

    def decorator(fn: F) -> F:
        name = tool_name or fn.__name__

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            rid = sec.new_request_id()
            token = _current_request_id.set(rid)
            iid = _image_id_from_tool_kwargs(kwargs)
            iid_token = _current_tool_image_id.set(iid)
            _host_audit("mcp_tool_start", tool=name, request_id=rid)
            try:
                out = fn(*args, **kwargs)
                _host_audit("mcp_tool_end", tool=name, request_id=rid, success=True)
                return out
            except ToolError as te:
                parsed = sec.parse_tool_error_text(str(te))
                code = None
                if parsed and isinstance(parsed.get("error"), dict):
                    code = parsed["error"].get("code")
                _host_audit(
                    "mcp_tool_end",
                    tool=name,
                    request_id=rid,
                    success=False,
                    code=code,
                )
                raise
            except Exception as e:
                if sec.debug_enabled():
                    traceback.print_exc()
                handles = _harvest_affected_handles(kwargs)
                try:
                    raise_from_exception(
                        e,
                        request_id=rid,
                        tool_name=name,
                        affected_handles=handles,
                        image_id=iid,
                    )
                except ToolError as te:
                    parsed = sec.parse_tool_error_text(str(te))
                    code = None
                    if parsed and isinstance(parsed.get("error"), dict):
                        code = parsed["error"].get("code")
                    _host_audit(
                        "mcp_tool_end",
                        tool=name,
                        request_id=rid,
                        success=False,
                        code=code,
                    )
                    raise
            finally:
                _current_tool_image_id.reset(iid_token)
                _current_request_id.reset(token)

        return wrapper  # type: ignore[return-value]

    return decorator


def _raise_plugin_error(result: dict[str, Any], tool_name: str) -> NoReturn:
    """Back-compat alias → raise_from_plugin_result (ToolError, not bare Exception)."""
    raise_from_plugin_result(result, tool_name)


# MCP server


def _ann(
    *,
    read_only: bool = False,
    destructive: bool = False,
    idempotent: bool | None = None,
) -> ToolAnnotations:
    """MCP ToolAnnotations helper (omit destructiveHint when read-only)."""
    if read_only:
        return ToolAnnotations(
            readOnlyHint=True,
            openWorldHint=False,
            idempotentHint=idempotent,
        )
    return ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=destructive,
        openWorldHint=False,
        idempotentHint=idempotent,
    )


def create_mcp_server(*, advanced_mode: bool | None = None) -> FastMCP:
    """Build a FastMCP instance with HL or advanced include_tags (track 0010)."""
    mode = surface.surface_mode(advanced_mode=advanced_mode)
    # First 512 chars must be self-contained (Codex/Grok clients truncate).
    instructions = (
        "GIMP MCP — default HL 28 tools (GIMP_MCP_ADVANCED_TOOLS=1 for ~90 advanced). "
        "GIMP must be open; Tools → MCP → Start MCP Server. "
        "Set GIMP_WORKSPACE_ROOT jail for file tools. "
        "image_delivery.client_model_visibility=unknown — prefer filesystem_path "
        "fallback when ImageContent is omitted/unrendered. "
        "Prefer HL tools; no Class-A exec. "
        "Snapshots dual-deliver ImageContent + TextContent JSON mapping "
        "(structuredContent.filesystem_path under .gimp-mcp-tmp/snapshots/)."
    )
    return FastMCP(
        "GimpMCP",
        instructions=instructions,
        include_tags=surface.include_tags_for_mode(mode),
    )


mcp = create_mcp_server()


def _probe_connection() -> dict[str, Any]:
    """Shared TCP probe of the GIMP plug-in (used by session_probe / check_server)."""
    try:
        test_conn = GimpConnection(GIMP_HOST, GIMP_PORT)
        test_conn.connect()
        result = test_conn.send_command("get_gimp_info")
        version = result.get("results", {}).get("version", {}).get("version_method", "unknown")
        return {
            "connected": True,
            "host": GIMP_HOST,
            "port": GIMP_PORT,
            "gimp_version": version,
        }
    except Exception as e:
        return {
            "connected": False,
            "host": GIMP_HOST,
            "port": GIMP_PORT,
            "error": str(e),
        }


def _surface_probe_fields(
    *,
    nonce: str | None = None,
    min_plugin_version: str | None = None,
    gimp_version: str | None = None,
) -> dict[str, Any]:
    """Host-side surface/capability fields always returned by session_probe."""
    mode = surface.surface_mode()
    out: dict[str, Any] = {
        "tool_surface": mode,
        "advanced_tools_enabled": surface.advanced_tools_enabled(),
        "hl_tool_names": surface.get_hl_catalog_names(),
        "capabilities": default_capabilities(),
        "nonce": nonce,
        "version_ok": surface.soft_version_ok(gimp_version, min_plugin_version),
        "min_plugin_version": min_plugin_version,
        # Honest image-delivery report (0021) — never claims client/model vision.
        "image_delivery": {
            "emits_mcp_image_content": True,
            "filesystem_snapshot_write": True,
            "client_model_visibility": "unknown",
            "fallback": (
                "If ImageContent is omitted/unrendered, open "
                "structuredContent.filesystem_path (or TextContent mapping JSON) "
                "via host tools"
            ),
            "snapshot_write_env": "GIMP_MCP_SNAPSHOT_WRITE",
        },
    }
    return out


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(read_only=True, idempotent=True))
@with_structured_error()
def session_probe(
    ctx: Context,
    nonce: str | None = None,
    min_plugin_version: str | None = None,
) -> dict:
    """Probe GIMP MCP connectivity plus host-side surface/capability state.

    Preferred default-surface health check (supersedes ``check_server``).
    Always reports tool surface mode and HL catalog even when disconnected.

    Parameters:
    - nonce: Optional echo token for client correlation
    - min_plugin_version: Soft minimum (dotted ints); ``version_ok`` may be None
      if unparseable

    Returns: connected, host, port, gimp_version (when up), tool_surface,
    advanced_tools_enabled, hl_tool_names, capabilities, nonce, version_ok,
    and error when disconnected.
    """
    probe = _probe_connection()
    gimp_version = probe.get("gimp_version") if probe.get("connected") else None
    if isinstance(gimp_version, str):
        ver: str | None = gimp_version
    else:
        ver = None
    out = {
        **probe,
        **_surface_probe_fields(
            nonce=nonce,
            min_plugin_version=min_plugin_version,
            gimp_version=ver,
        ),
    }
    return out


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def check_server(ctx: Context) -> dict:
    """Check whether the GIMP MCP plugin socket is reachable (advanced surface).

    Prefer ``session_probe`` on the default high-level surface — it also reports
    tool_surface, capabilities, and HL catalog when disconnected.

    Returns a status dict:
    - connected: bool
    - host / port: where it tried
    - gimp_version: if connected successfully
    - error: description if not connected

    If not connected, open GIMP and run Tools > Start MCP Server.
    """
    return _probe_connection()


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(destructive=True))
@with_structured_error()
def restart_server(ctx: Context) -> dict:
    """Drop and re-establish the connection to the GIMP MCP plugin.

    Prefer ``session_probe`` first. Use ``restart_server`` only when disconnected
    or reconnect is needed after GIMP/plugin restart — it is session-disruptive
    (clears connection + cached auth token).

    Use when:
    - GIMP was restarted after the MCP client was already running
    - The socket connection dropped mid-session
    - session_probe shows not connected but GIMP is open

    Returns the new connection status (same format as check_server / probe base).
    """
    reset_gimp_connection()
    time.sleep(0.5)
    return _probe_connection()


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(destructive=False, idempotent=False))
@with_structured_error()
def new_canvas(
    ctx: Context,
    width: int,
    height: int,
    name: str = "Untitled",
    color_mode: str = "RGB",
    fill: str = "white",
    resolution: int = 72,
) -> dict:
    """Create a new blank canvas in GIMP and open it in a display window.

    Parameters:
    - width: Canvas width in pixels
    - height: Canvas height in pixels
    - name: Layer/image name (default: "Untitled")
    - color_mode: "RGB" (default), "RGBA", "GRAY", "GRAYA"
    - fill: Fill color for the background layer. Any CSS color name or
            hex string: "white" (default), "black", "transparent",
            "#FF5733", "rgb(100,200,50)", etc.
    - resolution: DPI resolution (default: 72)

    Returns:
    - image_id: internal GIMP image ID
    - width / height: confirmed dimensions
    - color_mode: confirmed mode
    - display_opened: whether a GIMP window was opened

    Examples:
    - new_canvas(1024, 1024) — white 1024x1024 RGB canvas
    - new_canvas(1920, 1080, name="Background", fill="black")
    - new_canvas(512, 512, color_mode="RGBA", fill="transparent")
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "new_canvas",
            {
                "width": width,
                "height": height,
                "name": name,
                "color_mode": color_mode,
                "fill": fill,
                "resolution": resolution,
            },
        )
        if result.get("status") != "success":
            raise_from_plugin_result(result, "tool")
        return result["results"]
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


def _render_visible_composite_impl(
    *,
    handle: dict | None = None,
    image_index: int | None = None,
    max_width: int | None = None,
    max_height: int | None = None,
    region: dict | None = None,
    write_filesystem: bool | None = None,
) -> ToolResult:
    """Shared visible-composite snapshot (plugin get_image_bitmap + ToolResult)."""
    if handle is None and image_index is None:
        image_index = 0
    params: dict[str, Any] = {}
    if handle is not None:
        params["handle"] = handle
    if image_index is not None:
        params["image_index"] = int(image_index)
    if max_width is not None:
        params["max_width"] = max_width
    if max_height is not None:
        params["max_height"] = max_height
    if region is not None:
        try:
            norm = snap.normalize_region(region)
        except (TypeError, ValueError) as e:
            raise Exception(f"Invalid region: {e}") from e
        if norm is not None:
            params["region"] = norm

    conn = get_gimp_connection()
    result = conn.send_command("get_image_bitmap", params)
    if result["status"] == "success":
        # Prefer plugin-reported index (honest after handle resolve); fallback local
        results = result["results"]
        if "image_index" in results and results["image_index"] is not None:
            idx = int(results["image_index"])
        else:
            idx = int(image_index) if image_index is not None else 0
        return _snapshot_tool_result(results, image_index=idx, write_filesystem=write_filesystem)
    raise_from_plugin_result(result, "gimp")


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(read_only=True, idempotent=True))
@with_structured_error()
def render_visible_composite(
    ctx: Context,
    handle: dict | None = None,
    image_index: int | None = None,
    max_width: int | None = None,
    max_height: int | None = None,
    region: dict | None = None,
    write_filesystem: bool | None = None,
) -> ToolResult:
    """Render the visible composite of an open GIMP image as PNG + mapping metadata.

    Design-name primary for the default surface (prefer over advanced
    ``get_image_bitmap``). Returns the **visible composite** (all visible layers,
    opacity, blend modes, masks — GIMP's canvas projection), not a single top
    layer. Never mutates the user's original image.

    Dual-delivery: TextContent (JSON mapping) + ImageContent PNG, plus optional
    jailed filesystem write (``write_filesystem`` / ``GIMP_MCP_SNAPSHOT_WRITE``;
    default on). Prefer ``structuredContent.filesystem_path`` when ImageContent
    is not model-visible.

    Parameters:
    - handle: Preferred image handle from orient_workspace / mutators
    - image_index: Legacy open-image index when handle is omitted (default 0)
    - max_width, max_height: Target dimensions for scaling (aspect-ratio preserved)
    - region: Optional region dict (origin_x/origin_y or x/y, width, height)
    - write_filesystem: Override snapshot disk write (None → env default on)

    Returns ToolResult with TextContent mapping + PNG ImageContent and
    structuredContent (including filesystem_* when write succeeds).
    """
    try:
        print("Requesting visible composite from GIMP...")
        return _render_visible_composite_impl(
            handle=handle,
            image_index=image_index,
            max_width=max_width,
            max_height=max_height,
            region=region,
            write_filesystem=write_filesystem,
        )
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def get_image_bitmap(
    ctx: Context,
    image_index: int = 0,
    max_width: int | None = None,
    max_height: int | None = None,
    region: dict | None = None,
    handle: dict | None = None,
    write_filesystem: bool | None = None,
) -> ToolResult:
    """Get the visible composite of an open GIMP image as PNG + mapping metadata.

    Advanced alias — prefer ``render_visible_composite`` on the default surface.
    Same implementation (visible composite, never mutates the original image).

    Parameters:
    - image_index: Which open image to capture (default 0)
    - max_width, max_height: Target dimensions for scaling (aspect-ratio preserved)
    - region: Dictionary with origin_x/origin_y (or x/y), width, height
    - handle: Optional image handle (preferred when known)
    - write_filesystem: Override snapshot disk write (None → env default on)

    Returns ToolResult with TextContent mapping + PNG ImageContent and
    structuredContent (including filesystem_* when write succeeds).
    """
    try:
        print("Requesting current image bitmap from GIMP...")
        return _render_visible_composite_impl(
            handle=handle,
            image_index=image_index,
            max_width=max_width,
            max_height=max_height,
            region=region,
            write_filesystem=write_filesystem,
        )
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def get_image_metadata(ctx: Context, image_index: int = 0) -> dict:
    """Get metadata about an open image in GIMP without the bitmap data.

    Prefer ``orient_workspace`` for agent orientation (schema-versioned SoT).

    Parameters:
    - image_index: Target image index (default 0)

    Returns detailed information including dimensions, color mode, layers,
    channels, paths, and file info. Faster than get_image_bitmap().
    """
    try:
        print("Requesting current image metadata from GIMP...")

        conn = get_gimp_connection()
        result = conn.send_command("get_image_metadata", {"image_index": image_index})
        if result["status"] == "success":
            return result["results"]
        else:
            raise_from_plugin_result(result, "gimp")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(read_only=True, idempotent=True))
@with_structured_error()
def orient_workspace(
    ctx: Context,
    image_index: int | None = None,
    summary_only: bool = False,
) -> dict:
    """Full workspace state manifest (schema v1.0.0) — orientation source of truth.

    Prefer this before edits and re-run after structural mutations (create/delete/
    reorder/merge/rasterize layers, open/close images). Read-only; does not
    change selection, dirty state, or displays.

    Handles are session-stable under generation rules: each image has a structural
    generation counter; after create/delete/duplicate/reorder/flatten/merge/text/
    drop-shadow (etc.) the generation advances and prior handles become STALE_HANDLE.
    Re-orient or use the generation/handle returned by the last structural mutator.
    For large workspaces, pass image_index and/or summary_only=True.

    Parameters:
    - image_index: If set, only that open image is included; default is all images
    - summary_only: If True, omit full recursive layer trees (lightweight summary)

    Returns:
    - Schema-versioned state manifest with session, gimp, images (recursive layer
      trees + kinds), selection, color profile, EXIF orientation fields, paint
      context, and honest capability flags. Transport is agent-facing stdio-proxy.
    """
    try:
        params: dict[str, Any] = {"summary_only": bool(summary_only)}
        if image_index is not None:
            params["image_index"] = int(image_index)
        conn = get_gimp_connection()
        result = conn.send_command("orient_workspace", params)
        if result.get("status") != "success":
            raise_from_plugin_result(result, "tool")
        raw = result["results"]
        if not isinstance(raw, dict):
            raise Exception("orient_workspace returned non-object results")
        return finalize_manifest(
            raw,
            authenticated=True,
            host=GIMP_HOST,
            port=GIMP_PORT,
            transport="stdio-proxy",
        )
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(destructive=False, idempotent=True))
@with_structured_error()
def select_image(ctx: Context, handle: dict) -> dict:
    """Validate/bind an image handle for agent targeting (handles only).

    Resolves ``handle`` against the live session registry. Does **not** create a
    new GIMP display window (never ``Display.new``). ``selected: true`` means the
    handle is bound for subsequent agent ops — not that a window was focused or
    created. ``display: true`` only if an existing display already shows the image
    (``display: false`` is still success when the handle is valid).

    Parameters:
    - handle: Image handle object from orient_workspace or a structural mutator
      (``{image_id, generation, session_epoch, fingerprint?}``). Not a bare int
      or name.

    Returns: ``{handle, image_id, generation, selected: true, display: bool}``

    Errors (code embedded in exception text until 0011 envelope):
    - STALE_HANDLE: re-orient_workspace or use generation from last mutator
    - FOREIGN_SESSION: plugin process restarted — restart MCP flow and orient
    - HANDLE_NOT_FOUND / INVALID_HANDLE: closed image or malformed handle
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command("select_image", {"handle": handle})
        if result.get("status") == "success":
            return result["results"]
        _raise_plugin_error(result, "select_image")
        raise AssertionError("unreachable")  # pragma: no cover
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(destructive=True))
@with_structured_error()
def normalize_image_orientation(
    ctx: Context,
    handle: dict | None = None,
    image_index: int | None = None,
    mode: str = "assume_pixels_upright",
) -> dict:
    """Normalize EXIF orientation so tag matches upright pixels (track 0008).

    Default mode ``assume_pixels_upright`` only sets both EXIF Orientation tags
    to 1 — **no** pixel rotate/flip. Safe after normal GIMP open, where
    ``Image.policy_rotate`` may already have uprighted pixels while leaving the
    tag as 6/8. Never calls ``policy_rotate``.

    Opt-in ``mode="trust_tag"`` applies ordered pixel ops for tags 2-8 then sets
    tags to 1. Use only when you know pixels still match the tag encoding
    (e.g. load path that bypassed orientation policy).

    Parameters:
    - handle: Preferred image handle from orient_workspace / mutators
    - image_index: Legacy open-image index when handle is omitted
    - mode: ``assume_pixels_upright`` (default) or ``trust_tag``

    Returns: ``{original_orientation, mode_applied, applied, pixel_orientation_normalized,
    generation, handle, image_id, ops_applied}``

    Errors: STALE_HANDLE, FOREIGN_SESSION, INVALID_HANDLE, HANDLE_NOT_FOUND,
    METADATA_WRITE_FAILED
    """
    try:
        if handle is None and image_index is None:
            image_index = 0
        params: dict[str, Any] = {"mode": mode}
        if handle is not None:
            params["handle"] = handle
        if image_index is not None:
            params["image_index"] = int(image_index)
        conn = get_gimp_connection()
        result = conn.send_command("normalize_image_orientation", params)
        if result.get("status") == "success":
            return result["results"]
        _raise_plugin_error(result, "normalize_image_orientation")
        raise AssertionError("unreachable")  # pragma: no cover
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(read_only=True, idempotent=True))
@with_structured_error()
def map_preview_to_image(
    ctx: Context,
    preview_x: float,
    preview_y: float,
    scale_x: float,
    scale_y: float,
    region_origin_x: float = 0,
    region_origin_y: float = 0,
    preview_padding_x: float = 0,
    preview_padding_y: float = 0,
    declaration: dict | None = None,
) -> dict:
    """Map preview/snapshot pixel coords → full-canvas image-pixel coords (host-only).

    Pure math from snapshot mapping fields — no GIMP call. Formula::

        image_x = region_origin_x + (preview_x - preview_padding_x) / scale_x
        image_y = region_origin_y + (preview_y - preview_padding_y) / scale_y

    Rounding: Python half-even (``int(round(x))``).

    Optional ``declaration`` is validated against the locked coordinate-space
    contract when provided.
    """
    try:
        if declaration is not None:
            coords.validate_declaration(declaration)
        ix, iy = coords.preview_to_image_xy(
            preview_x,
            preview_y,
            scale_x=scale_x,
            scale_y=scale_y,
            region_origin_x=region_origin_x,
            region_origin_y=region_origin_y,
            preview_padding_x=preview_padding_x,
            preview_padding_y=preview_padding_y,
        )
        return {
            "image_x": ix,
            "image_y": iy,
            "coordinate_space": coords.COORDINATE_SPACE,
            "view_rotation_ignored": coords.VIEW_ROTATION_IGNORED,
        }
    except ValueError as e:
        raise Exception(f"map_preview_to_image failed: {e}") from e
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def map_image_to_preview(
    ctx: Context,
    image_x: float,
    image_y: float,
    scale_x: float,
    scale_y: float,
    region_origin_x: float = 0,
    region_origin_y: float = 0,
    preview_padding_x: float = 0,
    preview_padding_y: float = 0,
    declaration: dict | None = None,
) -> dict:
    """Inverse of map_preview_to_image (image-pixel → preview). Host-only pure math.

    Advanced map inverse — default surface exposes ``map_preview_to_image`` only.
    """
    try:
        if declaration is not None:
            coords.validate_declaration(declaration)
        px, py = coords.image_to_preview_xy(
            image_x,
            image_y,
            scale_x=scale_x,
            scale_y=scale_y,
            region_origin_x=region_origin_x,
            region_origin_y=region_origin_y,
            preview_padding_x=preview_padding_x,
            preview_padding_y=preview_padding_y,
        )
        return {
            "preview_x": px,
            "preview_y": py,
            "coordinate_space": coords.COORDINATE_SPACE,
            "view_rotation_ignored": coords.VIEW_ROTATION_IGNORED,
        }
    except ValueError as e:
        raise Exception(f"map_image_to_preview failed: {e}") from e
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def map_layer_local_to_image(
    ctx: Context,
    local_x: float,
    local_y: float,
    offset_x: float,
    offset_y: float,
    declaration: dict | None = None,
) -> dict:
    """Map layer-local coords → image canvas: image = local + offset. Host-only.

    Prefer absolute canvas offsets from GIMP ``layer.get_offsets()``.
    """
    try:
        if declaration is not None:
            coords.validate_declaration(declaration)
        ix, iy = coords.layer_local_to_image_xy(local_x, local_y, offset_x, offset_y)
        return {
            "image_x": ix,
            "image_y": iy,
            "coordinate_space": coords.COORDINATE_SPACE,
        }
    except ValueError as e:
        raise Exception(f"map_layer_local_to_image failed: {e}") from e
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def map_image_to_layer_local(
    ctx: Context,
    image_x: float,
    image_y: float,
    offset_x: float,
    offset_y: float,
    declaration: dict | None = None,
) -> dict:
    """Inverse: local = image - offset. Host-only pure math."""
    try:
        if declaration is not None:
            coords.validate_declaration(declaration)
        lx, ly = coords.image_to_layer_local_xy(image_x, image_y, offset_x, offset_y)
        return {
            "local_x": lx,
            "local_y": ly,
            "coordinate_space": coords.COORDINATE_SPACE,
        }
    except ValueError as e:
        raise Exception(f"map_image_to_layer_local failed: {e}") from e
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(destructive=False, idempotent=True))
@with_structured_error()
def select_layers(ctx: Context, handles: list) -> dict:
    """Select layers by stable item handles (handles only; max 64).

    All handles must share the same image_id, session_epoch, and current
    generation. Nested layers require item_id handles (name-only resolve remains
    root-only on low-level tools).

    Parameters:
    - handles: List of 1..64 item handle objects from orient_workspace / mutators

    Returns: ``{selected_handles, image_id, generation}`` (generation unchanged)

    Errors (code embedded in exception text until 0011 envelope):
    - STALE_HANDLE: re-orient or use last mutator generation
    - FOREIGN_SESSION: restart plugin + orient
    - SELECTION_CONFLICT: floating selection present — anchor or remove it first
    - INVALID_HANDLE: empty list, >64, mixed images, bad shape
    - HANDLE_NOT_FOUND: invalid/closed item or wrong image membership
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command("select_layers", {"handles": handles})
        if result.get("status") == "success":
            return result["results"]
        _raise_plugin_error(result, "select_layers")
        raise AssertionError("unreachable")  # pragma: no cover
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def get_gimp_info(ctx: Context) -> dict:
    """Get comprehensive information about the GIMP installation and environment.

    Prefer ``orient_workspace`` for agent orientation (schema-versioned SoT).
    Prefer ``session_probe`` for connectivity + surface/capability summary.

    Returns detailed information about GIMP that AI assistants need to understand
    the current environment, including:
    - GIMP version and build information
    - Installation paths and directories
    - Available plugins and procedures
    - System configuration
    - Runtime environment details

    This information helps AI assistants provide better support and troubleshooting
    by understanding the specific GIMP setup they're working with.

    Returns:
    - Dictionary containing comprehensive GIMP environment information
    - Raises exception if GIMP connection fails
    """
    try:
        print("Requesting GIMP environment information...")

        conn = get_gimp_connection()
        result = conn.send_command("get_gimp_info")
        if result["status"] == "success":
            return result["results"]
        else:
            raise_from_plugin_result(result, "gimp")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def get_state_snapshot(
    ctx: Context,
    image_index: int = 0,
    max_size: int = 512,
    region: dict | None = None,
    label: str = "",
) -> ToolResult:
    """Return a live visual snapshot of the visible composite (dual-delivery).

    Prefer ``orient_workspace`` for structural SoT and ``render_visible_composite``
    for full-fidelity composite + mapping on the default surface.

    AI agents call this for visual feedback after edits. Dual-delivery matches
    ``render_visible_composite``: TextContent JSON mapping + ImageContent PNG,
    plus optional jailed filesystem write under ``.gimp-mcp-tmp/snapshots/``
    (``GIMP_MCP_SNAPSHOT_WRITE``, default on). Client model vision is unknown —
    prefer ``filesystem_path`` when ImageContent is omitted/unrendered.

    Captures the **visible composite** (all visible layers / opacity / blend), not a
    single layer. Never mutates the user's original image.

    Parameters:
    - image_index: Which open image to snapshot (default: 0 = most recent)
    - max_size: Maximum width/height of the returned preview in pixels (default: 512)
    - region: Optional dict {x, y, width, height} or {origin_x, origin_y, width, height}
              to zoom into a specific area
              e.g. {"x": 200, "y": 300, "width": 100, "height": 80} for mouth area
    - label: Optional annotation label (logged but not drawn — for agent bookkeeping)

    Returns:
    - ToolResult with TextContent (JSON mapping) + PNG ImageContent, plus
      structuredContent mapping (mode, image_index, source/rendered sizes,
      scale_x/scale_y, region, composite_method, optional filesystem_path)

    Typical agent workflow:
        1. open_image / new_canvas
        2. <edit operations>
        3. get_state_snapshot()          ← see result, decide next step
        4. <more edits>
        5. get_state_snapshot(region={"x":200,"y":300,"width":100,"height":80})
        6. export_image when satisfied
    """
    try:
        if label:
            print(f"[snapshot] {label}")
        conn = get_gimp_connection()
        params: dict[str, Any] = {"image_index": image_index}
        if max_size:
            params["max_width"] = max_size
            params["max_height"] = max_size
        if region:
            try:
                norm = snap.normalize_region(region)
            except (TypeError, ValueError) as e:
                raise Exception(f"Invalid region: {e}") from e
            if norm is None:
                norm = {}
            # Defaults for partial shorthand from agents
            if "origin_x" not in norm:
                norm["origin_x"] = 0
            if "origin_y" not in norm:
                norm["origin_y"] = 0
            if "width" not in norm:
                norm["width"] = int(max_size)
            if "height" not in norm:
                norm["height"] = int(max_size)
            params["region"] = norm
        result = conn.send_command("get_image_bitmap", params)
        if result["status"] == "success":
            return _snapshot_tool_result(result["results"], image_index=image_index)
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def get_context_state(ctx: Context) -> dict:
    """Get the current GIMP context state (colors, brush, settings).

    Prefer ``orient_workspace`` for agent orientation (schema-versioned SoT).

    IMPORTANT: Context state can be changed by the user in GIMP UI at any time.
    Check context state before operations that depend on specific settings.

    Returns information about:
    - Foreground and background colors (RGB/RGBA values)
    - Current brush and its properties
    - Opacity setting (0-100%)
    - Paint/blend mode
    - Feather state and radius
    - Antialiasing state

    Use cases:
    - Verify colors before drawing operations
    - Check if feathering is enabled (avoid unwanted blurry edges)
    - Ensure correct opacity and blend mode
    - Detect if user changed settings in GIMP UI

    Returns:
    - Dictionary containing current context state
    - Raises exception if unable to get context state
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command("get_context_state", params={})
        if result["status"] == "success":
            return result["results"]
        else:
            raise_from_plugin_result(result, "gimp")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def call_api(
    ctx: Context,
    api_path: str,
    args: list | None = None,
    kwargs: dict | None = None,
) -> str:
    """Call GIMP 3.2 API methods through PyGObject console.

    GIMP MCP Protocol:
    - Use api_path="exec" to execute Python code in GIMP
    - args[0] should be "pyGObject-console" for executing commands
    - args[1] should be array of Python code strings to execute
    - Commands execute in persistent context - imports and variables persist
    - Always call Gimp.displays_flush() after drawing operations

    For image operations, use get_image_bitmap()
    which return proper MCP Image objects that Claude can process directly.

    GUIDANCE PROMPTS:
    - For common operations and best practices, invoke the 'gimp_best_practices' prompt
    - For complex multi-element drawings with layers, invoke the 'gimp_iterative_workflow' prompt

    Optional Initialization Pattern:
    ["images = Gimp.get_images()", "image1 = images[0]",
     "layers = image1.get_layers()", "layer1 = layers[0]", "drawable1 = layer1"]

    Common Operations:
    - Draw line: ["Gimp.pencil(drawable1, [0, 0, 200, 200])", "Gimp.displays_flush()"]
    - Set color: ["from gi.repository import Gegl", "red_color = Gegl.Color.new('red')",
                  "Gimp.context_set_foreground(red_color)"]
    - Draw ellipse: ["Gimp.Image.select_ellipse(image1, Gimp.ChannelOps.REPLACE, 100, 100, 30, 20)",
                     "Gimp.Drawable.edit_fill(drawable1, Gimp.FillType.FOREGROUND)",
                     "Gimp.Selection.none(image1)", "Gimp.displays_flush()"]
    - Paint curve: ["Gimp.paintbrush_default(drawable1, [50.0, 50.0, 150.0, 200.0, 250.0, 50.0, 350.0, 200.0])",
                    "Gimp.displays_flush()"]
    - Draw bezier curve: ["path = Gimp.Path.new(image1, 'my_bezier_path')",
                          "image1.insert_path(path, None, 0)",
                          "stroke_id = path.bezier_stroke_new_moveto(100, 100)",
                          "path.bezier_stroke_cubicto(stroke_id, 150, 50, 250, 150, 300, 100)",
                          "Gimp.Drawable.edit_stroke_item(drawable1, path)",
                          "Gimp.Selection.none(image1)", "Gimp.displays_flush()"]
    - Get open filenames: ["print([x.get_file().get_path() for x in Gimp.get_images()])"]
    - Copy layer between images: ["image1 = Gimp.get_images()[0]", "image2 = Gimp.get_images()[1]",
                                  "width = image1.get_width()", "height = image1.get_height()",
                                  "image1.select_rectangle(Gimp.ChannelOps.REPLACE, 0, 0, width, height)",
                                  "image1_layers = image1.get_selected_layers()", "drawable = image1_layers[0]",
                                  "Gimp.edit_copy([drawable])", "image2_layers = image2.get_layers()",
                                  "target_drawable = image2_layers[0]", "floating_sel = Gimp.edit_paste(target_drawable, True)[0]",
                                  "Gimp.floating_sel_anchor(floating_sel)", "Gimp.displays_flush()"]
    - New image: ["image1 = Gimp.Image.new(350, 800, Gimp.ImageBaseType.RGB)",
                  "layer1 = Gimp.Layer.new(image1, 'Background', 350, 800, Gimp.ImageType.RGB_IMAGE, 100, Gimp.LayerMode.NORMAL)",
                  "image1.insert_layer(layer1, None, 0)", "drawable1 = layer1",
                  "white_color = Gegl.Color.new('white')", "Gimp.context_set_background(white_color)",
                  "Gimp.Drawable.edit_fill(drawable1, Gimp.FillType.BACKGROUND)", "Gimp.Display.new(image1)"]

    Important Tips:
    - When filling layers with color, ensure layer has alpha channel using Gimp.Layer.add_alpha()
    - Use Gimp.Drawable.fill() for reliable full-layer fills
    - Specify colors precisely with rgb(R, G, B) or rgba(R, G, B, A) to avoid transparency issues
    - After drawing operations, always call Gimp.displays_flush()
    - After selection operations for drawing, unselect with Gimp.Selection.none(image1)

    GIMP 3.2 API Changes:
    - Use Gimp.get_images() instead of deprecated Gimp.list_images()
    - Use image.get_layers() instead of Gimp.get_active_layer()
    - gimpfu module not available in GIMP 3.2
    - Colors created with Gegl.Color.new('color_name')
    - Full API documentation: https://developer.gimp.org/api/3.0/libgimp/

    Parameters:
    - api_path: Use "exec" for Python execution
    - args: ["pyGObject-console", ["python_code_array"]] or ["pyGObject-eval", ["expression"]]
    - kwargs: Dictionary of keyword arguments (rarely used)

    Returns:
    - JSON string of the result or error message

    Security: Class B (PDB-mediated) exec — disabled unless GIMP_MCP_ALLOW_EXEC=1.
    """
    if not sec.exec_allowed():
        tool_fail(
            sec.CODE_EXEC_DISABLED,
            "Class B call_api is disabled. Set GIMP_MCP_ALLOW_EXEC=1 only for "
            "advanced local use (Class B — cannot disable GIMP built-in "
            "python-fu-* PDB procedures globally).",
        )
    if args is None:
        args = []
    if kwargs is None:
        kwargs = {}
    conn = get_gimp_connection()
    result = conn.send_command("call_api", {"api_path": api_path, "args": args, "kwargs": kwargs})
    if result.get("status") == "success":
        return json.dumps(result["results"])
    raise_from_plugin_result(
        result if isinstance(result, dict) else {"error": str(result)},
        "call_api",
    )


@mcp.prompt(
    description="GIMP MCP best practices for common operations - filling shapes, bezier paths, and variable persistence",
    tags={surface.HL_TAG},
)
def gimp_best_practices() -> str:
    """Returns guidance on best practices for GIMP operations via MCP.

    This prompt provides critical DO/DON'T patterns that help AI assistants
    and users avoid common mistakes when working with GIMP through MCP.
    """
    docs_path = Path(__file__).parent / "docs" / "best_practices.md"
    return docs_path.read_text()


@mcp.prompt(
    description="Iterative workflow guidance for building complex images with proper validation and layer management",
    tags={surface.HL_TAG},
)
def gimp_iterative_workflow() -> str:
    """Returns comprehensive guidance on iterative workflow with GIMP MCP.

    This prompt teaches AI assistants how to:
    - Plan layer structures before drawing
    - Work incrementally with continuous validation
    - Self-critique using render_visible_composite()
    - Fix problems properly instead of painting over them
    - Leverage GIMP's professional features for clean, organized work
    """
    docs_path = Path(__file__).parent / "docs" / "iterative_workflow.md"
    return docs_path.read_text()


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY 1 — File Operations
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(destructive=False, idempotent=False))
@with_structured_error()
def open_image(ctx: Context, file_path: str) -> dict:
    """Open an image file in GIMP and create a display window.

    Parameters:
    - file_path: Absolute path to the image file to open (PNG, JPEG, TIFF, etc.)

    Returns:
    - image_id: internal GIMP image ID
    - width / height: image dimensions in pixels
    - color_mode: RGB / Grayscale / Indexed
    - num_layers: number of layers in the image
    - display_opened: whether a GIMP display window was created
    """
    try:
        file_path = _jail_path_or_raise(file_path, "file_path")
        conn = get_gimp_connection()
        result = conn.send_command("open_image", {"file_path": file_path})
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(destructive=True))
@with_structured_error()
def save_xcf(
    ctx: Context,
    file_path: str,
    handle: dict | None = None,
    image_index: int | None = None,
    collision: str = "fail",
    verify_reopen: bool = True,
) -> dict:
    """Save the current image as a GIMP XCF file (preserves all layers and metadata).

    **Atomic** (track 0013): writes a same-directory temp with real ``.xcf`` suffix,
    optional structural reopen on the temp, then ``os.replace`` into the final path.
    Verify failure never clobbers an existing final file.

    Collision policy (``collision``):
    - ``fail`` (default): existing target → ``OUTPUT_COLLISION`` (CLI exit 11)
    - ``version``: next free ``stem-N.xcf`` (cap 10000 → INTERNAL)
    - ``replace``: namespaced ``.gimp-mcp.bak`` backup then atomic write

    Parameters:
    - file_path: Absolute path for the output .xcf file (workspace-jailed)
    - handle: Preferred image handle from orient_workspace / mutators
    - image_index: Legacy open-image index when handle is omitted (default 0)
    - collision: fail | version | replace (default fail)
    - verify_reopen: Structural reopen of temp XCF before replace (default True;
      XCF-only — export has no reopen)

    Returns (flat success results): file_path, bytes, sha256, collision,
    collision_resolved, backup_path, atomic=true, reopen_verified.
    """
    try:
        if handle is None and image_index is None:
            image_index = 0
        file_path = _jail_path_or_raise(file_path, "file_path")
        # Validate collision on host before TCP (invalid → POLICY_DENIED)
        try:
            import gimp_mcp_atomic as _atomic

            collision_mode = _atomic.parse_collision(collision, default="fail")
        except ValueError as e:
            tool_fail(sec.CODE_POLICY_DENIED, str(e))
        conn = get_gimp_connection()
        params: dict[str, Any] = {
            "file_path": file_path,
            "collision": collision_mode,
            "verify_reopen": bool(verify_reopen),
        }
        if handle is not None:
            params["handle"] = handle
        if image_index is not None:
            params["image_index"] = int(image_index)
        result = conn.send_command("save_xcf", params)
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(destructive=True))
@with_structured_error()
def export_image(
    ctx: Context,
    file_path: str,
    format: str = "png",
    quality: int = 90,
    flatten: bool = False,
    preserve_alpha: bool | None = None,
    verify: bool = True,
    collision: str = "fail",
    handle: dict | None = None,
    image_index: int | None = None,
) -> dict:
    """Export the current image to a raster file (PNG, JPEG, WEBP, TIFF).

    **Atomic** (track 0013): writes a same-directory temp with the real format
    suffix, runs PNG IHDR / alpha verify on the **temp**, then backup (replace
    mode) + ``os.replace``. ALPHA_LOST discards temp → ``left_on_disk=false``
    and ``final_intact=true`` (existing final is not clobbered).

    **No ``verify_reopen``** — reopen is XCF-only on ``save_xcf``. Export uses
    ``verify`` / PNG IHDR (0005). Full AE/SSIM → track 0014.

    Collision policy (``collision``):
    - ``fail`` (default): existing target → ``OUTPUT_COLLISION`` (CLI exit 11)
    - ``version``: next free ``stem-N.ext``
    - ``replace``: namespaced backup then atomic write

    **Breaking change (Issue 16):** ``flatten`` default is **False**. Transparent
    PNG/WEBP/TIFF exports use merge-on-duplicate (alpha preserved) and fail closed
    with code ``ALPHA_LOST`` if the artifact loses alpha after a source that had it.

    Parameters:
    - file_path: Absolute path for the output file (workspace-jailed)
    - format: Output format — "png" (default), "jpeg", "webp", "tiff"
    - quality: JPEG/WEBP quality 1-100 (default 90; ignored for PNG/TIFF)
    - flatten: Flatten layers on a **duplicate** before export (default **False**).
      Flatten **strips alpha**. For opaque bake set flatten=True (auto preserve_alpha=False).
    - preserve_alpha: None (auto), True, or False. Auto=True for png/webp/tiff when
      flatten is False; False for jpeg. JPEG+preserve_alpha=True → ALPHA_UNSUPPORTED_FORMAT.
      flatten=True + preserve_alpha=True → POLICY_CONFLICT error.
    - verify: Fail-closed PNG IHDR alpha check when preserve_alpha and preflight had alpha
      (default True)
    - collision: fail | version | replace (default fail)
    - handle: Preferred image handle from orient_workspace / mutators
    - image_index: Index of the image to export when handle omitted (default 0)

    Notes:
    - Never mutates the open document for export prep (works on a duplicate).
    - ``flatten_image`` is an explicit mutator of the open document — different tool.
    - Opaque multi-layer bake: pass flatten=True (or preserve_alpha=False).

    Returns (success): file_path, format, file_size_bytes, preserve_alpha,
    preflight_has_alpha, alpha_verified (true|false|"not_applicable"), export_method,
    pdb_procedure, optional png_color_type, plus atomic manifest fields
    (bytes, sha256, collision, collision_resolved, backup_path, atomic=true).

    Structured errors raise ToolError (MCP isError) with single-line envelope JSON:
    ALPHA_LOST (details.left_on_disk / final_intact), OUTPUT_COLLISION, EXPORT_FAILED,
    ALPHA_UNSUPPORTED_FORMAT, POLICY_CONFLICT. Parse with ``parse_tool_error_text``;
    never returned as a successful tool result (0011 H1).
    """
    try:
        if handle is None and image_index is None:
            image_index = 0
        file_path = _jail_path_or_raise(file_path, "file_path")
        try:
            import gimp_mcp_atomic as _atomic

            collision_mode = _atomic.parse_collision(collision, default="fail")
        except ValueError as e:
            tool_fail(sec.CODE_POLICY_DENIED, str(e))
        conn = get_gimp_connection()
        payload: dict[str, Any] = {
            "file_path": file_path,
            "format": format,
            "quality": quality,
            "flatten": flatten,
            "preserve_alpha": preserve_alpha,
            "verify": verify,
            "collision": collision_mode,
        }
        if handle is not None:
            payload["handle"] = handle
        if image_index is not None:
            payload["image_index"] = int(image_index)
        result = conn.send_command("export_image", payload)
        if result.get("status") == "success":
            return result["results"]
        # H1 (0011): always raise ToolError — never return error dict as success.
        # ALPHA_LOST details (left_on_disk, png_color_type, property_errors) go in envelope.details.
        raise_from_plugin_result(
            result if isinstance(result, dict) else {"error": str(result)},
            "export_image",
        )
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def batch_export(
    ctx: Context,
    output_dir: str,
    format: str = "png",
    quality: int = 90,
    name_pattern: str = "{name}",
    image_index: int | None = None,
    flatten: bool = False,
    preserve_alpha: bool | None = None,
    verify: bool = True,
) -> dict:
    """Export all open images (or a specific one) to a directory.

    Same alpha/flatten policy as ``export_image`` (defaults preserve alpha for PNG).

    Parameters:
    - output_dir: Directory to write exported files into
    - format: "png", "jpeg", "webp", "tiff" (default "png")
    - quality: JPEG/WEBP quality (default 90)
    - name_pattern: Filename template — use {name} for image name, {index} for position
    - image_index: If set, export only that image; omit to export all open images
    - flatten: Flatten on duplicate before export (default False; strips alpha if True)
    - preserve_alpha: None (auto), True, or False — same rules as export_image
    - verify: PNG IHDR alpha verify when applicable (default True)

    Returns:
    - exported: list of {file_path, name, width, height, ...}
    - count: number of files written
    - errors: list of any export errors
    """
    try:
        output_dir = _jail_path_or_raise(output_dir, "output_dir")
        params: dict = {
            "output_dir": output_dir,
            "format": format,
            "quality": quality,
            "name_pattern": name_pattern,
            "flatten": flatten,
            "preserve_alpha": preserve_alpha,
            "verify": verify,
        }
        if image_index is not None:
            params["image_index"] = image_index
        conn = get_gimp_connection()
        result = conn.send_command("batch_export", params)
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(read_only=True, idempotent=True))
@with_structured_error()
def verify_alpha_channel(
    ctx: Context,
    handle: dict | None = None,
    image_index: int | None = None,
) -> dict:
    """Read-only preflight: does the image have alpha, and which formats can keep it?

    Cheaper than full metadata when an agent only needs image-level alpha status
    and format prediction before export.

    Parameters:
    - handle: Preferred image handle from orient_workspace / mutators
    - image_index: Index of the open image when handle omitted (default 0)

    Returns:
    - has_alpha: True if any layer reports an alpha channel
    - image_base_type: e.g. RGB, Grayscale, Indexed
    - layers_with_alpha: list of layer names that have alpha
    - can_preserve_alpha_for_format: {png: true, jpeg: false, webp: true, tiff: true}
    """
    try:
        if handle is None and image_index is None:
            image_index = 0
        conn = get_gimp_connection()
        params: dict[str, Any] = {}
        if handle is not None:
            params["handle"] = handle
        if image_index is not None:
            params["image_index"] = int(image_index)
        result = conn.send_command("verify_alpha_channel", params)
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(read_only=True, idempotent=True))
@with_structured_error()
def compare_images(
    ctx: Context,
    path_a: str,
    path_b: str,
    thresholds: dict | None = None,
    write_diff_path: str | None = None,
    raise_on_fail: bool = False,
    ignore_alpha: bool = False,
    compute_ssim: bool | str = "auto",
    change_threshold: int = 1,
) -> dict:
    """Compare two workspace-jailed PNG paths with objective pixel metrics (host-only).

    Does **not** call the GIMP plug-in. Loads 8-bit non-interlaced PNG (color types
    0/2/4/6) with full defilter (0-4 incl. Paeth).

    **ok vs pass:** returned dict always has ``ok: true`` when the operation
    succeeds (files loaded, metrics computed). ``pass`` is the threshold gate.
    Path jail / unsupported PNG / pixel budget errors **raise** structured errors
    (never ``ok: false``).

    **SSIM honesty:** when computed, ``ssim`` is **global** luminance SSIM
    (single window, C1/C2 Wang constants). It is **not** ImageMagick windowed
    ``-metric SSIM``. ``compute_ssim="auto"`` disables when ``w*h > 1_000_000``.

    Parameters:
    - path_a / path_b: Workspace-jailed PNG paths (before/after or artifact pair)
    - thresholds: optional gates — ``require_mutation``, ``min_changed_pixels``,
      ``max_mae``, ``max_max_ae``, ``min_ssim``, ``max_changed_fraction``,
      ``require_same_size`` (default true)
    - write_diff_path: optional grayscale heatmap PNG (max |ΔRGB| per pixel)
    - raise_on_fail: when true and ``pass`` is false → raise ``VERIFY_FAILED``
    - ignore_alpha: allow RGB vs RGBA compare on RGB only (default false = fail)
    - compute_ssim: true | false | \"auto\" (default auto)
    - change_threshold: per-channel abs delta to count a pixel changed (default 1)

    Returns metrics: mae, max_ae, changed_pixels, changed_fraction, alpha
    transparent counts, ssim/ssim_computed, failures[], pass, ok.
    """
    try:
        ja = _jail_path_or_raise(path_a, "path_a")
        jb = _jail_path_or_raise(path_b, "path_b")
        jdiff = _jail_path_or_raise(write_diff_path, "write_diff_path") if write_diff_path else None
        return verify.compare_images(
            ja,
            jb,
            thresholds=thresholds,
            write_diff_path=jdiff,
            raise_on_fail=bool(raise_on_fail),
            ignore_alpha=bool(ignore_alpha),
            compute_ssim=compute_ssim,
            change_threshold=int(change_threshold),
        )
    except (ToolError, sec.SecurityError, sec.GimpMcpError):
        raise
    except Exception as exc:
        if sec.debug_enabled():
            traceback.print_exc()
        raise_from_exception(exc, tool_name="compare_images")


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(read_only=True, idempotent=True))
@with_structured_error()
def verify_artifact(
    ctx: Context,
    path: str,
    expected: dict,
    raise_on_fail: bool = False,
) -> dict:
    """Validate one workspace-jailed artifact against a typed expectation (host-only).

    Does **not** call the GIMP plug-in. Format detection is **signature-based**
    (not extension). v1 supports ``format: \"png\"`` only; other formats raise
    ``UNSUPPORTED``.

    **ok vs pass:** ``ok: true`` means the check ran; ``pass`` is the expectation
    gate. Path/budget/unsupported raise structured errors.

    Expected keys (v1):
    - ``min_width`` / ``max_width`` / ``width`` / ``height`` / ``min_height`` / ``max_height``
    - ``format``: \"png\" (jpeg/webp/tiff → UNSUPPORTED)
    - ``require_alpha``: true | false | null
    - ``sha256``: hex digest
    - ``min_bytes`` / ``max_bytes``

    Parameters:
    - path: Workspace-jailed artifact path
    - expected: expectation object (see keys above)
    - raise_on_fail: when true and ``pass`` is false → raise ``VERIFY_FAILED``
    """
    try:
        jpath = _jail_path_or_raise(path, "path")
        return verify.verify_artifact(
            jpath,
            expected if expected is not None else {},
            raise_on_fail=bool(raise_on_fail),
        )
    except (ToolError, sec.SecurityError, sec.GimpMcpError):
        raise
    except Exception as exc:
        if sec.debug_enabled():
            traceback.print_exc()
        raise_from_exception(exc, tool_name="verify_artifact")


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(read_only=True, idempotent=True))
@with_structured_error()
def list_recipes(ctx: Context) -> dict:
    """List shipped versioned recipes (id, version, title, routing flags).

    Host-only: loads package-data JSON under ``gimp_agent/recipes/``. Does not
    call GIMP. Returns exactly the fields agents need to choose a recipe — no
    steps/parameters in the list payload.

    Returns::

        {"recipes": [{"id", "version", "title", "batch_safe",
                      "requires_open_session", "requires_gimp"}, ...]}
    """
    try:
        import gimp_mcp_recipes as recipes

        return {"recipes": recipes.list_recipes()}
    except (ToolError, sec.SecurityError, sec.GimpMcpError):
        raise
    except Exception as exc:
        if sec.debug_enabled():
            traceback.print_exc()
        raise_from_exception(exc, tool_name="list_recipes")


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(destructive=True))
@with_structured_error()
def apply_recipe(
    ctx: Context,
    recipe_id: str,
    version: str | None = None,
    params: dict | None = None,
    input_path: str | None = None,
    output_path: str | None = None,
    handle: dict | None = None,
) -> dict:
    """Run a versioned allowlisted recipe; return a mutation log.

    Recipe step ops call the plug-in TCP bridge / host verify modules **directly**
    — they are **not** filtered by ``GIMP_MCP_ADVANCED_TOOLS`` (e.g. ``scale_image``
    inside ``web-export`` works with advanced tools unset).

    Parameters:
    - recipe_id: Recipe id (e.g. ``transparent-png``, ``compare-artifacts``)
    - version: Optional semver; default = latest
    - params: Optional parameter object (recipe-specific keys)
    - input_path: Workspace-jailed input (mutually exclusive with handle)
    - output_path: Workspace-jailed output path
    - handle: Open image handle for ``requires_open_session`` recipes

    Returns mutation log: ``ok``, ``recipe_id``, ``version``, ``backend``
    (``session``|``host``|``headless``; default auto tries session then
    headless for batch_safe recipes), ``steps``, ``artifacts``, ``created_paths``.

    Errors: unknown id → UNSUPPORTED; bad params / both handle+input → structured
    policy error; verification fail → VERIFY_FAILED.
    """
    try:
        import gimp_mcp_recipes as recipes

        # Jail paths on the host before the runner (runner also jails).
        j_input = _jail_path_or_raise(input_path, "input_path") if input_path else None
        j_output = _jail_path_or_raise(output_path, "output_path") if output_path else None

        def _session_send(command_type: str, payload: dict) -> dict:
            conn = get_gimp_connection()
            return conn.send_command(command_type, payload)

        return recipes.run_recipe(
            recipe_id,
            version=version,
            params=params,
            input_path=j_input,
            output_path=j_output,
            handle=handle,
            session_send=_session_send,
        )
    except (ToolError, sec.SecurityError, sec.GimpMcpError):
        raise
    except Exception as exc:
        if sec.debug_enabled():
            traceback.print_exc()
        raise_from_exception(exc, tool_name="apply_recipe")


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY 2 — Image Adjustments
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def auto_levels(ctx: Context, image_index: int = 0, layer_name: str | None = None) -> dict:
    """Automatically stretch the tonal range of an image (auto levels / auto stretch contrast).

    Parameters:
    - image_index: Index of the target image (default 0)
    - layer_name: Name of the layer to adjust; defaults to active layer

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "auto_levels", {"image_index": image_index, "layer_name": layer_name}
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def adjust_curves(
    ctx: Context,
    preset: str = "s_curve",
    points: list | None = None,
    channel: str = "value",
    image_index: int = 0,
    layer_name: str | None = None,
) -> dict:
    """Adjust tonal curves for a layer.

    Parameters:
    - preset: Built-in curve shape — "s_curve" (default), "lighten", "darken", "contrast"
    - points: Custom control points as [[input, output], ...] override (overrides preset)
    - channel: "value" (all), "red", "green", "blue", "alpha"
    - image_index: Target image index (default 0)
    - layer_name: Layer to adjust; defaults to active layer

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "adjust_curves",
            {
                "preset": preset,
                "points": points,
                "channel": channel,
                "image_index": image_index,
                "layer_name": layer_name,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def adjust_brightness_contrast(
    ctx: Context,
    brightness: int = 0,
    contrast: int = 0,
    image_index: int = 0,
    layer_name: str | None = None,
) -> dict:
    """Adjust brightness and contrast of a layer.

    Parameters:
    - brightness: -127 to +127 (default 0)
    - contrast: -127 to +127 (default 0)
    - image_index: Target image index (default 0)
    - layer_name: Layer to adjust; defaults to active layer

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "adjust_brightness_contrast",
            {
                "brightness": brightness,
                "contrast": contrast,
                "image_index": image_index,
                "layer_name": layer_name,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def adjust_hue_saturation(
    ctx: Context,
    hue: float = 0,
    saturation: float = 0,
    lightness: float = 0,
    color_range: str = "all",
    image_index: int = 0,
    layer_name: str | None = None,
) -> dict:
    """Adjust hue, saturation, and lightness of a layer.

    Parameters:
    - hue: Hue rotation -180 to +180 (default 0)
    - saturation: Saturation shift -100 to +100 (default 0)
    - lightness: Lightness shift -100 to +100 (default 0)
    - color_range: "all", "red", "yellow", "green", "cyan", "blue", "magenta" (default "all")
    - image_index: Target image index (default 0)
    - layer_name: Layer to adjust; defaults to active layer

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "adjust_hue_saturation",
            {
                "hue": hue,
                "saturation": saturation,
                "lightness": lightness,
                "color_range": color_range,
                "image_index": image_index,
                "layer_name": layer_name,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def adjust_color_balance(
    ctx: Context,
    cyan_red: float = 0,
    magenta_green: float = 0,
    yellow_blue: float = 0,
    range: str = "midtones",
    image_index: int = 0,
    layer_name: str | None = None,
) -> dict:
    """Adjust color balance (shadows / midtones / highlights) of a layer.

    Parameters:
    - cyan_red: -100 to +100 (negative = cyan, positive = red; default 0)
    - magenta_green: -100 to +100 (default 0)
    - yellow_blue: -100 to +100 (default 0)
    - range: "shadows", "midtones" (default), "highlights"
    - image_index: Target image index (default 0)
    - layer_name: Layer to adjust; defaults to active layer

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "adjust_color_balance",
            {
                "cyan_red": cyan_red,
                "magenta_green": magenta_green,
                "yellow_blue": yellow_blue,
                "range": range,
                "image_index": image_index,
                "layer_name": layer_name,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def sharpen(
    ctx: Context,
    amount: float = 50.0,
    radius: float = 3.0,
    threshold: int = 0,
    image_index: int = 0,
    layer_name: str | None = None,
) -> dict:
    """Sharpen a layer using unsharp mask.

    Parameters:
    - amount: Sharpening strength 0-500 (default 50.0)
    - radius: Blur radius for the mask in pixels (default 3.0)
    - threshold: Minimum difference before sharpening is applied (default 0)
    - image_index: Target image index (default 0)
    - layer_name: Layer to sharpen; defaults to active layer

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "sharpen",
            {
                "amount": amount,
                "radius": radius,
                "threshold": threshold,
                "image_index": image_index,
                "layer_name": layer_name,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def blur(
    ctx: Context,
    radius_x: float = 5.0,
    radius_y: float = 5.0,
    image_index: int = 0,
    layer_name: str | None = None,
) -> dict:
    """Apply Gaussian blur to a layer.

    Parameters:
    - radius_x: Horizontal blur radius in pixels (default 5.0)
    - radius_y: Vertical blur radius in pixels (default 5.0)
    - image_index: Target image index (default 0)
    - layer_name: Layer to blur; defaults to active layer

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "blur",
            {
                "radius_x": radius_x,
                "radius_y": radius_y,
                "image_index": image_index,
                "layer_name": layer_name,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def denoise(
    ctx: Context, strength: int = 50, image_index: int = 0, layer_name: str | None = None
) -> dict:
    """Reduce noise in a layer using GEGL noise-reduction.

    Parameters:
    - strength: Noise reduction strength 0-100 (default 50)
    - image_index: Target image index (default 0)
    - layer_name: Layer to denoise; defaults to active layer

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "denoise",
            {
                "strength": strength,
                "image_index": image_index,
                "layer_name": layer_name,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def desaturate(
    ctx: Context, mode: str = "luminosity", image_index: int = 0, layer_name: str | None = None
) -> dict:
    """Convert a layer to grayscale (desaturate).

    Parameters:
    - mode: Desaturation algorithm — "luminosity" (default), "luma", "average", "lightness"
    - image_index: Target image index (default 0)
    - layer_name: Layer to desaturate; defaults to active layer

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "desaturate",
            {
                "mode": mode,
                "image_index": image_index,
                "layer_name": layer_name,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def invert_colors(ctx: Context, image_index: int = 0, layer_name: str | None = None) -> dict:
    """Invert all colors in a layer (create a negative).

    Parameters:
    - image_index: Target image index (default 0)
    - layer_name: Layer to invert; defaults to active layer

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "invert_colors",
            {
                "image_index": image_index,
                "layer_name": layer_name,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY 3 — Resize & Transform
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def scale_image(
    ctx: Context, width: int, height: int, interpolation: str = "cubic", image_index: int = 0
) -> dict:
    """Scale an image to exact pixel dimensions.

    Parameters:
    - width: Target width in pixels
    - height: Target height in pixels
    - interpolation: "cubic" (default), "linear", "none"
    - image_index: Target image index (default 0)

    Returns: {status, width, height}
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "scale_image",
            {
                "width": width,
                "height": height,
                "interpolation": interpolation,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def scale_to_fit(
    ctx: Context,
    max_width: int,
    max_height: int,
    interpolation: str = "cubic",
    image_index: int = 0,
) -> dict:
    """Scale an image to fit within a bounding box, preserving aspect ratio.

    Parameters:
    - max_width: Maximum allowed width in pixels
    - max_height: Maximum allowed height in pixels
    - interpolation: "cubic" (default), "linear", "none"
    - image_index: Target image index (default 0)

    Returns: {status, width, height} — final dimensions after scaling
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "scale_to_fit",
            {
                "max_width": max_width,
                "max_height": max_height,
                "interpolation": interpolation,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def crop_to_selection(ctx: Context, autocrop: bool = False, image_index: int = 0) -> dict:
    """Crop the image canvas to the current selection bounds.

    Parameters:
    - autocrop: If True, auto-detect crop bounds instead of using selection (default False)
    - image_index: Target image index (default 0)

    Returns: {status, x, y, width, height} — crop region applied
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "crop_to_selection",
            {
                "autocrop": autocrop,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def crop_to_rect(
    ctx: Context, x: int, y: int, width: int, height: int, image_index: int = 0
) -> dict:
    """Crop the image canvas to an explicit rectangle.

    Parameters:
    - x, y: Top-left corner of the crop rectangle
    - width, height: Dimensions of the crop rectangle
    - image_index: Target image index (default 0)

    Returns: {status, x, y, width, height}
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "crop_to_rect",
            {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def rotate_image(
    ctx: Context,
    angle: float,
    image_index: int = 0,
    confirm_destructive: bool = False,
) -> dict:
    """Rotate the entire image.

    Parameters:
    - angle: Rotation in degrees — 90, 180, 270 use lossless GIMP rotation;
             other values rotate all layers with interpolation and flatten
    - image_index: Target image index (default 0)
    - confirm_destructive: required True for free-angle branch (live flatten).
      90/180/270 lossless paths do not require it. Missing → CONFIRM_REQUIRED.

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "rotate_image",
            {
                "angle": angle,
                "image_index": image_index,
                "confirm_destructive": confirm_destructive,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def flip_image(ctx: Context, direction: str = "horizontal", image_index: int = 0) -> dict:
    """Flip the entire image horizontally or vertically.

    Parameters:
    - direction: "horizontal" (default) or "vertical"
    - image_index: Target image index (default 0)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "flip_image",
            {
                "direction": direction,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def resize_canvas(
    ctx: Context,
    width: int,
    height: int,
    anchor: str = "center",
    fill: str = "transparent",
    image_index: int = 0,
    confirm_destructive: bool = False,
) -> dict:
    """Resize the image canvas without scaling the content.

    Parameters:
    - width, height: New canvas dimensions in pixels
    - anchor: Position of existing content — "center" (default), "top-left", "top",
              "top-right", "left", "right", "bottom-left", "bottom", "bottom-right"
    - fill: Color for new canvas areas — CSS color or "transparent"
    - image_index: Target image index (default 0)
    - confirm_destructive: required True when fill != transparent (live flatten).
      Transparent fill does not require it. Missing → CONFIRM_REQUIRED.

    Returns: {status, width, height, offset_x, offset_y}
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "resize_canvas",
            {
                "width": width,
                "height": height,
                "anchor": anchor,
                "fill": fill,
                "image_index": image_index,
                "confirm_destructive": confirm_destructive,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY 4 — Selections
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(destructive=False, idempotent=False))
@with_structured_error()
def create_selection(
    ctx: Context,
    type: str,
    handle: dict | None = None,
    image_index: int | None = None,
    operation: str = "replace",
    feather: float = 0.0,
    x: int | None = None,
    y: int | None = None,
    width: int | None = None,
    height: int | None = None,
    color: str | None = None,
    threshold: int = 15,
    layer_handle: dict | None = None,
) -> dict:
    """Unified selection tool (rectangle, ellipse, by_color, all, none).

    Prefer this over advanced ``select_rectangle`` / ``select_ellipse`` /
    ``select_by_color`` / ``select_all`` / ``select_none``. Does **not** wrap
    invert/modify (use advanced tools for those).

    Parameters:
    - type: ``rectangle`` | ``ellipse`` | ``by_color`` | ``all`` | ``none``
    - handle: Preferred image handle; image_index used when handle omitted
    - image_index: Legacy open-image index (default 0 when both omitted)
    - operation: replace/add/subtract/intersect (default replace)
    - feather: pixels for rectangle/ellipse only (default 0)
    - x, y, width, height: required for rectangle/ellipse
    - color: required for by_color (CSS/hex string)
    - threshold: by_color similarity (default 15)
    - layer_handle: optional item handle for by_color sample layer. When
      provided, plugin resolves by ``item_id`` and **fails closed** on invalid
      id/membership (no silent active-layer fallback). When omitted, samples
      the **active layer**.

    Host validates params before any TCP call.
    """
    raw: dict[str, Any] = {
        "type": type,
        "operation": operation,
        "feather": feather,
        "threshold": threshold,
    }
    if handle is not None:
        raw["handle"] = handle
    if image_index is not None:
        raw["image_index"] = image_index
    if layer_handle is not None:
        raw["layer_handle"] = layer_handle
    if x is not None:
        raw["x"] = x
    if y is not None:
        raw["y"] = y
    if width is not None:
        raw["width"] = width
    if height is not None:
        raw["height"] = height
    if color is not None:
        raw["color"] = color

    try:
        norm = surface.validate_create_selection_params(raw)
    except ValueError as e:
        raise Exception(f"create_selection validation failed: {e}") from e

    sel_type = norm["type"]
    cmd_map = {
        "rectangle": "select_rectangle",
        "ellipse": "select_ellipse",
        "by_color": "select_by_color",
        "all": "select_all",
        "none": "select_none",
    }
    cmd = cmd_map[sel_type]
    params: dict[str, Any] = {"operation": norm["operation"]}
    if "handle" in norm:
        params["handle"] = norm["handle"]
    if "image_index" in norm:
        params["image_index"] = int(norm["image_index"])
    elif "handle" not in params:
        params["image_index"] = 0

    if sel_type in ("rectangle", "ellipse"):
        params.update(
            {
                "x": norm["x"],
                "y": norm["y"],
                "width": norm["width"],
                "height": norm["height"],
                "feather": norm["feather"],
            }
        )
    elif sel_type == "by_color":
        params["color"] = norm["color"]
        params["threshold"] = norm["threshold"]
        # Prefer layer id from handle when present; plugin may use layer_name.
        lh = norm.get("layer_handle")
        if isinstance(lh, dict) and "item_id" in lh:
            params["layer_id"] = int(lh["item_id"])

    try:
        conn = get_gimp_connection()
        result = conn.send_command(cmd, params)
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def select_rectangle(
    ctx: Context,
    x: int,
    y: int,
    width: int,
    height: int,
    operation: str = "replace",
    feather: float = 0,
    image_index: int = 0,
) -> dict:
    """Create a rectangular selection.

    Parameters:
    - x, y: Top-left corner of the selection
    - width, height: Dimensions of the selection
    - operation: "replace" (default), "add", "subtract", "intersect"
    - feather: Feather radius in pixels (default 0 = no feather)
    - image_index: Target image index (default 0)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "select_rectangle",
            {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "operation": operation,
                "feather": feather,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def select_ellipse(
    ctx: Context,
    x: int,
    y: int,
    width: int,
    height: int,
    operation: str = "replace",
    feather: float = 0,
    image_index: int = 0,
) -> dict:
    """Create an elliptical selection.

    Parameters:
    - x, y: Top-left corner of the bounding box
    - width, height: Bounding box dimensions
    - operation: "replace" (default), "add", "subtract", "intersect"
    - feather: Feather radius in pixels (default 0)
    - image_index: Target image index (default 0)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "select_ellipse",
            {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "operation": operation,
                "feather": feather,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def select_by_color(
    ctx: Context,
    color: str,
    threshold: int = 15,
    operation: str = "replace",
    image_index: int = 0,
    layer_name: str | None = None,
) -> dict:
    """Select regions by color similarity.

    Parameters:
    - color: Target color as CSS name, hex (#rrggbb), or rgb() string
    - threshold: Color similarity tolerance 0-255 (default 15)
    - operation: "replace" (default), "add", "subtract", "intersect"
    - image_index: Target image index (default 0)
    - layer_name: Layer to sample from; defaults to active layer

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "select_by_color",
            {
                "color": color,
                "threshold": threshold,
                "operation": operation,
                "image_index": image_index,
                "layer_name": layer_name,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def select_all(ctx: Context, image_index: int = 0) -> dict:
    """Select the entire image canvas.

    Parameters:
    - image_index: Target image index (default 0)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command("select_all", {"image_index": image_index})
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def select_none(ctx: Context, image_index: int = 0) -> dict:
    """Remove / deselect all selections.

    Parameters:
    - image_index: Target image index (default 0)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command("select_none", {"image_index": image_index})
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def invert_selection(ctx: Context, image_index: int = 0) -> dict:
    """Invert the current selection (select what is not selected).

    Parameters:
    - image_index: Target image index (default 0)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command("invert_selection", {"image_index": image_index})
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def modify_selection(ctx: Context, operation: str, amount: float, image_index: int = 0) -> dict:
    """Grow, shrink, feather, border, or sharpen the current selection.

    Parameters:
    - operation: "grow", "shrink", "feather", "border", "sharpen"
    - amount: Pixel radius for grow/shrink/feather/border; ignored for sharpen
    - image_index: Target image index (default 0)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "modify_selection",
            {
                "operation": operation,
                "amount": amount,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY 5 — Layer Operations
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def create_layer(
    ctx: Context,
    name: str = "New Layer",
    width: int | None = None,
    height: int | None = None,
    fill: str = "transparent",
    opacity: float = 100,
    blend_mode: str = "NORMAL",
    position: int = -1,
    image_index: int = 0,
) -> dict:
    """Create and insert a new layer into an image.

    Parameters:
    - name: Layer name (default "New Layer")
    - width, height: Layer dimensions; defaults to image dimensions
    - fill: Initial fill — "transparent" (default), "white", "black", or any CSS color
    - opacity: Layer opacity 0-100 (default 100)
    - blend_mode: GIMP layer mode name — "NORMAL" (default), "MULTIPLY", "SCREEN", etc.
    - position: Stack position — -1 = top (default), 0 = bottom
    - image_index: Target image index (default 0)

    Returns: {layer_name, layer_id, width, height, position}
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "create_layer",
            {
                "name": name,
                "width": width,
                "height": height,
                "fill": fill,
                "opacity": opacity,
                "blend_mode": blend_mode,
                "position": position,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def duplicate_layer(ctx: Context, layer_name: str | None = None, image_index: int = 0) -> dict:
    """Duplicate a layer and insert the copy above it.

    Parameters:
    - layer_name: Name of the layer to duplicate; defaults to active layer
    - image_index: Target image index (default 0)

    Returns: {layer_name, layer_id}
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "duplicate_layer",
            {
                "layer_name": layer_name,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def delete_layer(
    ctx: Context,
    layer_name: str | None = None,
    layer_index: int | None = None,
    image_index: int = 0,
) -> dict:
    """Delete a layer from an image.

    Parameters:
    - layer_name: Name of the layer to delete
    - layer_index: Position index of the layer (alternative to layer_name)
    - image_index: Target image index (default 0)

    Provide either layer_name or layer_index. Defaults to active layer if neither given.

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "delete_layer",
            {
                "layer_name": layer_name,
                "layer_index": layer_index,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def rename_layer(
    ctx: Context,
    new_name: str,
    old_name: str | None = None,
    layer_index: int | None = None,
    image_index: int = 0,
) -> dict:
    """Rename a layer.

    Parameters:
    - new_name: New name for the layer
    - old_name: Current name of the layer to rename
    - layer_index: Position index alternative to old_name
    - image_index: Target image index (default 0)

    Returns: {old_name, new_name}
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "rename_layer",
            {
                "old_name": old_name,
                "layer_index": layer_index,
                "new_name": new_name,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def set_layer_properties(
    ctx: Context,
    layer_name: str | None = None,
    layer_index: int | None = None,
    opacity: float | None = None,
    blend_mode: str | None = None,
    visible: bool | None = None,
    image_index: int = 0,
) -> dict:
    """Set properties on an existing layer.

    Parameters:
    - layer_name / layer_index: Identify the layer (defaults to active layer)
    - opacity: New opacity 0-100 (omit to leave unchanged)
    - blend_mode: New GIMP layer mode name (omit to leave unchanged)
    - visible: True/False visibility (omit to leave unchanged)
    - image_index: Target image index (default 0)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "set_layer_properties",
            {
                "layer_name": layer_name,
                "layer_index": layer_index,
                "opacity": opacity,
                "blend_mode": blend_mode,
                "visible": visible,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def reorder_layer(
    ctx: Context,
    new_position: int,
    layer_name: str | None = None,
    layer_index: int | None = None,
    image_index: int = 0,
) -> dict:
    """Move a layer to a new stack position.

    Parameters:
    - new_position: Target stack index (0 = bottom)
    - layer_name / layer_index: Identify the layer (defaults to active layer)
    - image_index: Target image index (default 0)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "reorder_layer",
            {
                "layer_name": layer_name,
                "layer_index": layer_index,
                "new_position": new_position,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def flatten_image(
    ctx: Context,
    image_index: int = 0,
    confirm_destructive: bool = False,
) -> dict:
    """Flatten all layers into a single background layer (destroys live stack).

    Parameters:
    - image_index: Target image index (default 0)
    - confirm_destructive: must be True (default False → CONFIRM_REQUIRED)

    Prefer ``ensure_source_immutable`` + ``checkpoint_create`` before this.
    Returns status dict with generation/handle.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "flatten_image",
            {
                "image_index": image_index,
                "confirm_destructive": confirm_destructive,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def merge_visible_layers(
    ctx: Context,
    image_index: int = 0,
    confirm_destructive: bool = False,
) -> dict:
    """Merge all visible layers into a single layer (live document).

    Parameters:
    - image_index: Target image index (default 0)
    - confirm_destructive: must be True (default False → CONFIRM_REQUIRED)

    Returns: {layer_name, layer_id, generation, handle}
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "merge_visible_layers",
            {
                "image_index": image_index,
                "confirm_destructive": confirm_destructive,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def list_layers(ctx: Context, image_index: int = 0) -> dict:
    """List layers in an image (flat root list — group children not expanded).

    Prefer ``orient_workspace`` for nested groups, layer kinds, handles, and
    full workspace orientation (schema-versioned SoT).

    Parameters:
    - image_index: Target image index (default 0)

    Returns: {layers: [{name, id, visible, opacity, blend_mode, width, height, has_alpha}], count}
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command("list_layers", {"image_index": image_index})
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(destructive=True))
@with_structured_error()
def ensure_source_immutable(
    ctx: Context,
    handle: dict | None = None,
    image_index: int | None = None,
    layer_ids: list[int] | None = None,
) -> dict:
    """Protect root source layers under a parasite-marked Source_Immutable group.

    For each target root non-group layer (all roots when ``layer_ids`` omitted):
    1. copy working layer into the original stack slot
    2. reparent the original into ``Source_Immutable``
    3. hide + lock content/position/visibility on the original

    Mutating a protected item_id returns ``POLICY_DENIED``. The plugin accepts a
    raw-TCP recovery flag ``allow_source_mutation=true`` (not exposed on MCP
    mutator tools — intentional fail-closed surface). Idempotent: working copies
    and layers already under the marked group are skipped; no generation bump
    when nothing changes. Single generation bump only after actual protect work.

    Agent intake order: orient_workspace → ensure_source_immutable →
    checkpoint_create before destructive ops → confirm_destructive for flatten.

    Parameters:
    - handle: Preferred image handle from orient_workspace / mutators
    - image_index: Legacy open-image index when handle is omitted (default 0)
    - layer_ids: optional explicit root layer ids to protect

    Returns: protected/working layer lists, generation, image handle.
    """
    try:
        if handle is None and image_index is None:
            image_index = 0
        conn = get_gimp_connection()
        params: dict[str, Any] = {}
        if handle is not None:
            params["handle"] = handle
        if image_index is not None:
            params["image_index"] = int(image_index)
        if layer_ids is not None:
            params["layer_ids"] = layer_ids
        result = conn.send_command("ensure_source_immutable", params)
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(destructive=True))
@with_structured_error()
def checkpoint_create(
    ctx: Context,
    label: str,
    handle: dict | None = None,
    image_index: int | None = None,
    overwrite: bool = False,
    include_orient_snapshot: bool = False,
) -> dict:
    """Save a workspace-jailed XCF checkpoint with integrity sidecar JSON.

    Paths: ``{GIMP_WORKSPACE_ROOT}/.gimp-mcp-checkpoints/{label}/project.xcf``
    and ``checkpoint.json``. Label rules: ``[A-Za-z0-9._-]+``, max 64; rejects
    ``..``, Windows reserved names (CON/PRN/…), trailing dots/spaces.

    Sidecar is written **only after** XCF save succeeds. ``xcf_sha256`` is
    integrity of as-written bytes — **not** XCF reproducibility (0013).

    Parameters:
    - label: checkpoint label
    - handle: Preferred image handle from orient_workspace / mutators
    - image_index: Legacy open-image index when handle is omitted (default 0)
    - overwrite: replace existing label (default False → CHECKPOINT_EXISTS)
    - include_orient_snapshot: default False. When True, records an honesty
      note only (full orient dump is not embedded — call orient_workspace
      after restore). Reserved for a richer dump later.

    Returns: paths, xcf_sha256, generation, handle.
    """
    try:
        if handle is None and image_index is None:
            image_index = 0
        conn = get_gimp_connection()
        params: dict[str, Any] = {
            "label": label,
            "overwrite": overwrite,
            "include_orient_snapshot": include_orient_snapshot,
        }
        if handle is not None:
            params["handle"] = handle
        if image_index is not None:
            params["image_index"] = int(image_index)
        result = conn.send_command("checkpoint_create", params)
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(destructive=True))
@with_structured_error()
def checkpoint_restore(
    ctx: Context,
    label: str,
    close_prior: bool = False,
    handle: dict | None = None,
    image_index: int | None = None,
    verify_hash: bool = True,
) -> dict:
    """Open a checkpoint XCF as a **new** image (alongside by default).

    Returns a new image handle/generation. Prior handles are invalid if the
    prior image is closed. Agent **must** call ``orient_workspace`` after restore.
    Sidecar tattoos are write-only — no tattoo rebind in 0009.

    Parameters:
    - label: checkpoint label
    - close_prior: if True, close the prior image after open success (default False)
    - handle: Preferred prior image handle when close_prior is True
    - image_index: prior image index when close_prior is True (legacy)
    - verify_hash: soft integrity compare vs sidecar (mismatch → CHECKPOINT_CORRUPTED)

    Returns: new handle, generation, hash_status, note.
    """
    try:
        conn = get_gimp_connection()
        params: dict[str, Any] = {
            "label": label,
            "close_prior": close_prior,
            "verify_hash": verify_hash,
        }
        if handle is not None:
            params["handle"] = handle
        if image_index is not None:
            params["image_index"] = image_index
        result = conn.send_command("checkpoint_restore", params)
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY 6 — Color & Paint
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def fill_layer(
    ctx: Context, color: str, layer_name: str | None = None, image_index: int = 0
) -> dict:
    """Fill an entire layer with a solid color.

    Parameters:
    - color: Fill color as CSS name, hex, or rgb() string
    - layer_name: Layer to fill; defaults to active layer
    - image_index: Target image index (default 0)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "fill_layer",
            {
                "color": color,
                "layer_name": layer_name,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def fill_selection(
    ctx: Context,
    color: str | None = None,
    fill_type: str | None = None,
    image_index: int = 0,
    layer_name: str | None = None,
) -> dict:
    """Fill the current selection with a color or fill type.

    Parameters:
    - color: Fill color as CSS name, hex, or rgb() string (used when fill_type is omitted)
    - fill_type: Fill type override: "foreground", "background", or "transparent"
    - image_index: Target image index (default 0)
    - layer_name: Target layer; defaults to active layer

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "fill_selection",
            {
                "color": color,
                "fill_type": fill_type,
                "image_index": image_index,
                "layer_name": layer_name,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def set_colors(ctx: Context, foreground: str | None = None, background: str | None = None) -> dict:
    """Set the GIMP foreground and/or background color.

    Parameters:
    - foreground: New foreground color (CSS name, hex, rgb()); omit to leave unchanged
    - background: New background color; omit to leave unchanged

    Returns: {foreground, background} confirmation dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "set_colors",
            {
                "foreground": foreground,
                "background": background,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def draw_line(
    ctx: Context,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    color: str | None = None,
    width: float = 2.0,
    tool: str = "pencil",
    layer_name: str | None = None,
    image_index: int = 0,
) -> dict:
    """Draw a straight line on a layer.

    Parameters:
    - x1, y1: Start point
    - x2, y2: End point
    - color: Stroke color (CSS / hex / rgb); uses current foreground if omitted
    - width: Stroke width in pixels (default 2.0)
    - tool: "pencil" (default, hard edge) or "paintbrush" (soft edge)
    - layer_name: Target layer; defaults to active layer
    - image_index: Target image index (default 0)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "draw_line",
            {
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "color": color,
                "width": width,
                "tool": tool,
                "layer_name": layer_name,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def draw_rectangle(
    ctx: Context,
    x: int,
    y: int,
    width: int,
    height: int,
    color: str | None = None,
    line_width: float = 2.0,
    layer_name: str | None = None,
    image_index: int = 0,
) -> dict:
    """Draw a rectangle outline (stroke only) on a layer.

    Parameters:
    - x, y: Top-left corner
    - width, height: Rectangle dimensions
    - color: Stroke color; uses current foreground if omitted
    - line_width: Stroke width in pixels (default 2.0)
    - layer_name: Target layer; defaults to active layer
    - image_index: Target image index (default 0)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "draw_rectangle",
            {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "color": color,
                "line_width": line_width,
                "layer_name": layer_name,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def draw_ellipse(
    ctx: Context,
    x: int,
    y: int,
    width: int,
    height: int,
    color: str | None = None,
    line_width: float = 2.0,
    layer_name: str | None = None,
    image_index: int = 0,
) -> dict:
    """Draw an ellipse outline (stroke only) on a layer.

    Parameters:
    - x, y: Top-left corner of the bounding box
    - width, height: Bounding box dimensions
    - color: Stroke color; uses current foreground if omitted
    - line_width: Stroke width in pixels (default 2.0)
    - layer_name: Target layer; defaults to active layer
    - image_index: Target image index (default 0)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "draw_ellipse",
            {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "color": color,
                "line_width": line_width,
                "layer_name": layer_name,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def fill_rectangle(
    ctx: Context,
    x: int,
    y: int,
    width: int,
    height: int,
    color: str,
    layer_name: str | None = None,
    image_index: int = 0,
) -> dict:
    """Fill a rectangular region with a solid color.

    Parameters:
    - x, y: Top-left corner
    - width, height: Rectangle dimensions
    - color: Fill color (CSS name, hex, or rgb() string)
    - layer_name: Target layer; defaults to active layer
    - image_index: Target image index (default 0)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "fill_rectangle",
            {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "color": color,
                "layer_name": layer_name,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def fill_ellipse(
    ctx: Context,
    x: int,
    y: int,
    width: int,
    height: int,
    color: str,
    layer_name: str | None = None,
    image_index: int = 0,
) -> dict:
    """Fill an elliptical region with a solid color.

    Parameters:
    - x, y: Top-left corner of the bounding box
    - width, height: Bounding box dimensions
    - color: Fill color (CSS name, hex, or rgb() string)
    - layer_name: Target layer; defaults to active layer
    - image_index: Target image index (default 0)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "fill_ellipse",
            {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "color": color,
                "layer_name": layer_name,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def gradient_fill(
    ctx: Context,
    color1: str = "black",
    color2: str = "white",
    x1: float = 0,
    y1: float = 0,
    x2: float | None = None,
    y2: float | None = None,
    gradient_type: str = "linear",
    layer_name: str | None = None,
    image_index: int = 0,
) -> dict:
    """Fill a layer or selection with a gradient.

    Parameters:
    - color1: Start color (default "black")
    - color2: End color (default "white")
    - x1, y1: Gradient start point (default top-left 0,0)
    - x2, y2: Gradient end point (defaults to bottom-right of image)
    - gradient_type: "linear" (default) or "radial"
    - layer_name: Target layer; defaults to active layer
    - image_index: Target image index (default 0)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "gradient_fill",
            {
                "color1": color1,
                "color2": color2,
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "gradient_type": gradient_type,
                "layer_name": layer_name,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY 7 — Text
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def add_text(
    ctx: Context,
    text: str,
    x: int = 0,
    y: int = 0,
    font: str = "Sans",
    size: int = 24,
    color: str = "black",
    image_index: int = 0,
) -> dict:
    """Add a text layer to an image.

    Parameters:
    - text: The text string to render
    - x, y: Position of the text layer's top-left corner (default 0, 0)
    - font: Font family name — "Sans" (default), "Serif", etc.
    - size: Font size in pixels (default 24)
    - color: Text color (CSS name, hex, or rgb() string; default "black")
    - image_index: Target image index (default 0)

    Returns: {layer_name, layer_id, text_width, text_height, position}
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "add_text",
            {
                "text": text,
                "x": x,
                "y": y,
                "font": font,
                "size": size,
                "color": color,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def edit_text(
    ctx: Context,
    layer_name: str,
    text: str | None = None,
    font: str | None = None,
    size: float | None = None,
    color: str | None = None,
    image_index: int = 0,
) -> dict:
    """Edit an existing text layer's content or formatting.

    Parameters:
    - layer_name: Name of the text layer to edit
    - text: New text content (omit to leave unchanged)
    - font: New font family (omit to leave unchanged)
    - size: New font size in pixels (omit to leave unchanged)
    - color: New text color (omit to leave unchanged)
    - image_index: Target image index (default 0)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "edit_text",
            {
                "layer_name": layer_name,
                "text": text,
                "font": font,
                "size": size,
                "color": color,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def list_fonts(ctx: Context, filter: str | None = None) -> dict:
    """List available fonts installed in GIMP.

    Parameters:
    - filter: Optional string to filter font names (case-insensitive substring match)

    Returns: {fonts: [font_name, ...], count}
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command("list_fonts", {"filter": filter})
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY 8 — Filters & Effects
# ─────────────────────────────────────────────────────────────────────────────


def _require_hl_layer_handle(layer_handle: Any) -> dict[str, Any]:
    """HL NDE mutators require a real item handle (no name/active-layer fallback)."""
    if not isinstance(layer_handle, dict):
        raise sec.GimpMcpError(
            sec.CODE_INVALID_HANDLE,
            "layer_handle is required for this high-level tool "
            "(pass an item handle from orient_workspace; layer_name/layer_id/"
            "image_index alone are not accepted on HL NDE tools)",
        )
    return layer_handle


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(destructive=False, idempotent=False))
@with_structured_error()
def apply_nde_filter(
    ctx: Context,
    operation: str,
    layer_handle: dict,
    name: str | None = None,
    config: dict | None = None,
    opacity: float = 1.0,
    blend_mode: str = "REPLACE",
    visible: bool = True,
) -> dict:
    """Append an allowlisted GEGL/GIMP op as a **non-destructive** DrawableFilter.

    Prefer this over advanced merge-bake filters (``apply_gaussian_blur``, etc.).
    Config is synced via ``DrawableFilter.update()`` **before** ``append_filter``;
    tools always flush displays before return so composite/snapshots see the stack.

    **v1 allowlist (13):** ``gegl:gaussian-blur``, ``unsharp-mask``, ``noise-reduction``,
    ``pixelize``, ``emboss``, ``vignette``, ``brightness-contrast``, ``hue-chroma``,
    ``color-balance``, ``exposure``, ``shadows-highlights``; ``gimp:levels`` /
    ``gimp:curves`` (runtime-probed). **Not** drop-shadow (use advanced manual tool).

    Soft config: unknown keys are ignored (reported in ``ignored_props``), not rejected.

    Parameters:
    - operation: allowlisted op name (required)
    - layer_handle: **required** item handle from orient_workspace (HL handle-first)
    - name: display name (default = operation string)
    - config: prop → value object (soft; e.g. ``{"std-dev-x": 5.0, "std-dev-y": 5.0}``)
    - opacity: filter opacity 0.0-1.0 (default 1.0)
    - blend_mode: default **REPLACE** (tutorial convention)
    - visible: default true

    Returns: filter summary, layer_handle, updated, applied_props, ignored_props, optional notes.
    No generation bump (re-orient to refresh filters[]). Verify with render_visible_composite
    + compare_images after edits.
    """
    try:
        lh = _require_hl_layer_handle(layer_handle)
        op_v = filters.validate_operation(operation)
        if not op_v.get("ok"):
            raise sec.GimpMcpError(
                sec.CODE_UNSUPPORTED,
                str(op_v.get("message", "unsupported operation")),
                details=op_v.get("details") if isinstance(op_v.get("details"), dict) else None,
            )
        bm_v = filters.validate_blend_mode(blend_mode)
        if not bm_v.get("ok"):
            raise sec.GimpMcpError(
                sec.CODE_UNSUPPORTED,
                str(bm_v.get("message", "unsupported blend_mode")),
                details=bm_v.get("details") if isinstance(bm_v.get("details"), dict) else None,
            )
        # Soft config: never reject unknown keys; type must be object when provided
        if config is not None and not isinstance(config, dict):
            raise sec.GimpMcpError(
                sec.CODE_UNSUPPORTED,
                "config must be an object when provided",
            )

        params: dict[str, Any] = {
            "operation": op_v["operation"],
            "opacity": float(opacity),
            "blend_mode": bm_v["blend_mode"],
            "visible": bool(visible),
            "layer_handle": lh,
        }
        if name is not None:
            params["name"] = name
        if config is not None:
            params["config"] = config

        conn = get_gimp_connection()
        result = conn.send_command("apply_nde_filter", params)
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except (ToolError, sec.SecurityError, sec.GimpMcpError):
        raise
    except Exception as exc:
        if sec.debug_enabled():
            traceback.print_exc()
        raise_from_exception(exc, tool_name="apply_nde_filter")


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(destructive=False, idempotent=False))
@with_structured_error()
def edit_filter_config(
    ctx: Context,
    filter_id: int,
    layer_handle: dict,
    config: dict | None = None,
    opacity: float | None = None,
    blend_mode: str | None = None,
    visible: bool | None = None,
) -> dict:
    """Edit config / opacity / blend / visibility of an existing NDE filter.

    Always calls ``DrawableFilter.update()`` + flush after config/blend/opacity
    changes so the next composite/snapshot is live. Visibility uses ``set_visible``
    + flush (not covered by update API alone).

    Target by ``filter_id`` (from orient/list) **and** layer membership.
    After delete/merge/undo/XCF reopen, filter ids are invalid → ``HANDLE_NOT_FOUND``.

    Parameters:
    - filter_id: session filter id from orient ``filters[]`` or list_drawable_filters
    - layer_handle: **required** item handle (HL handle-first)
    - config / opacity / blend_mode / visible: partial updates (omit to leave unchanged)

    Returns: filter summary, applied_props, ignored_props, updated=true.
    """
    try:
        lh = _require_hl_layer_handle(layer_handle)
        if blend_mode is not None:
            bm_v = filters.validate_blend_mode(blend_mode)
            if not bm_v.get("ok"):
                raise sec.GimpMcpError(
                    sec.CODE_UNSUPPORTED,
                    str(bm_v.get("message", "unsupported blend_mode")),
                    details=bm_v.get("details") if isinstance(bm_v.get("details"), dict) else None,
                )
            blend_mode = bm_v["blend_mode"]
        if config is not None and not isinstance(config, dict):
            raise sec.GimpMcpError(
                sec.CODE_UNSUPPORTED,
                "config must be an object when provided",
            )

        params: dict[str, Any] = {
            "filter_id": int(filter_id),
            "layer_handle": lh,
        }
        if config is not None:
            params["config"] = config
        if opacity is not None:
            params["opacity"] = float(opacity)
        if blend_mode is not None:
            params["blend_mode"] = blend_mode
        if visible is not None:
            params["visible"] = bool(visible)

        conn = get_gimp_connection()
        result = conn.send_command("edit_filter_config", params)
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except (ToolError, sec.SecurityError, sec.GimpMcpError):
        raise
    except Exception as exc:
        if sec.debug_enabled():
            traceback.print_exc()
        raise_from_exception(exc, tool_name="edit_filter_config")


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(destructive=True))
@with_structured_error()
def remove_nde_filter(
    ctx: Context,
    filter_id: int,
    layer_handle: dict,
) -> dict:
    """Delete an NDE DrawableFilter node by filter_id (non-destructive stack edit).

    Source_Immutable protected layers → ``POLICY_DENIED``.
    Unknown / wrong-layer filter_id → ``HANDLE_NOT_FOUND``.
    No generation bump; re-orient to refresh filters[].

    Parameters:
    - filter_id: session filter id
    - layer_handle: **required** item handle (HL handle-first)

    Returns: removed_filter_id, layer_handle, updated=true.
    """
    try:
        lh = _require_hl_layer_handle(layer_handle)
        params: dict[str, Any] = {
            "filter_id": int(filter_id),
            "layer_handle": lh,
        }

        conn = get_gimp_connection()
        result = conn.send_command("remove_nde_filter", params)
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except (ToolError, sec.SecurityError, sec.GimpMcpError):
        raise
    except Exception as exc:
        if sec.debug_enabled():
            traceback.print_exc()
        raise_from_exception(exc, tool_name="remove_nde_filter")


@mcp.tool(tags={surface.ADVANCED_TAG}, annotations=_ann(read_only=True, idempotent=True))
@with_structured_error()
def list_drawable_filters(
    ctx: Context,
    layer_handle: dict | None = None,
    include_config: bool = True,
    layer_id: int | None = None,
    layer_name: str | None = None,
    handle: dict | None = None,
    image_index: int | None = None,
) -> dict:
    """List NDE filters on a drawable (topmost → bottommost). Advanced, not HL.

    Prefer ``orient_workspace`` for full tree inventory; this tool is a cheaper
    single-layer probe. Filter ids are session-live until delete/merge/undo/reopen.

    Parameters:
    - layer_handle: preferred item handle
    - include_config: include best-effort prop dump (default true)
    - layer_id / layer_name / handle / image_index: legacy targeting

    Returns: filters[], count, layer_handle.
    """
    try:
        params: dict[str, Any] = {"include_config": bool(include_config)}
        if layer_handle is not None:
            params["layer_handle"] = layer_handle
        if layer_id is not None:
            params["layer_id"] = int(layer_id)
        if layer_name is not None:
            params["layer_name"] = layer_name
        if handle is not None:
            params["handle"] = handle
        if image_index is not None:
            params["image_index"] = int(image_index)

        conn = get_gimp_connection()
        result = conn.send_command("list_drawable_filters", params)
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG}, annotations=_ann(destructive=True))
@with_structured_error()
def merge_nde_filters(
    ctx: Context,
    confirm_destructive: bool = False,
    layer_handle: dict | None = None,
    filter_id: int | None = None,
    layer_id: int | None = None,
    layer_name: str | None = None,
    handle: dict | None = None,
    image_index: int | None = None,
) -> dict:
    """Destructively bake one or all NDE filters into layer pixels (advanced).

    Requires ``confirm_destructive=true``. When ``filter_id`` is omitted, merges
    **all** filters on the layer. Merged filter ids become invalid — re-orient
    before further filter edits.

    Prefer keeping filters non-destructive via apply/edit/remove unless bake is required.

    Parameters:
    - confirm_destructive: must be true
    - layer_handle: preferred item handle
    - filter_id: optional; omit to merge all
    - layer_id / layer_name / handle / image_index: legacy targeting

    Returns: merged_count, merged_filter_ids, note about invalidation.
    """
    try:
        params: dict[str, Any] = {"confirm_destructive": bool(confirm_destructive)}
        if layer_handle is not None:
            params["layer_handle"] = layer_handle
        if filter_id is not None:
            params["filter_id"] = int(filter_id)
        if layer_id is not None:
            params["layer_id"] = int(layer_id)
        if layer_name is not None:
            params["layer_name"] = layer_name
        if handle is not None:
            params["handle"] = handle
        if image_index is not None:
            params["image_index"] = int(image_index)

        conn = get_gimp_connection()
        result = conn.send_command("merge_nde_filters", params)
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def apply_drop_shadow(
    ctx: Context,
    offset_x: int = 5,
    offset_y: int = 5,
    blur_radius: float = 10,
    color: str = "black",
    opacity: float = 60,
    layer_name: str | None = None,
    image_index: int = 0,
) -> dict:
    """Apply a drop shadow effect to a layer.

    Parameters:
    - offset_x, offset_y: Shadow offset in pixels (default 5, 5)
    - blur_radius: Shadow softness radius (default 10)
    - color: Shadow color (default "black")
    - opacity: Shadow opacity 0-100 (default 60)
    - layer_name: Target layer; defaults to active layer
    - image_index: Target image index (default 0)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "apply_drop_shadow",
            {
                "offset_x": offset_x,
                "offset_y": offset_y,
                "blur_radius": blur_radius,
                "color": color,
                "opacity": opacity,
                "layer_name": layer_name,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def apply_gaussian_blur(
    ctx: Context, radius: float = 5.0, layer_name: str | None = None, image_index: int = 0
) -> dict:
    """Apply Gaussian blur as a destructive merge-bake filter operation.

    Prefer HL ``apply_nde_filter`` with ``operation="gegl:gaussian-blur"`` for a
    re-editable non-destructive stack.

    Parameters:
    - radius: Blur radius in pixels (default 5.0)
    - layer_name: Target layer; defaults to active layer
    - image_index: Target image index (default 0)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "apply_gaussian_blur",
            {
                "radius": radius,
                "layer_name": layer_name,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def apply_pixelate(
    ctx: Context, block_size: int = 10, layer_name: str | None = None, image_index: int = 0
) -> dict:
    """Pixelate a layer using a mosaic/block effect.

    Parameters:
    - block_size: Size of each mosaic block in pixels (default 10)
    - layer_name: Target layer; defaults to active layer
    - image_index: Target image index (default 0)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "apply_pixelate",
            {
                "block_size": block_size,
                "layer_name": layer_name,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def apply_emboss(
    ctx: Context,
    azimuth: float = 315,
    elevation: float = 45,
    depth: float = 2,
    layer_name: str | None = None,
    image_index: int = 0,
) -> dict:
    """Apply an emboss (bas-relief) effect to a layer.

    Parameters:
    - azimuth: Light direction in degrees 0-360 (default 315 = top-left)
    - elevation: Light elevation angle 0-90 (default 45)
    - depth: Effect depth/intensity (default 2)
    - layer_name: Target layer; defaults to active layer
    - image_index: Target image index (default 0)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "apply_emboss",
            {
                "azimuth": azimuth,
                "elevation": elevation,
                "depth": depth,
                "layer_name": layer_name,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def apply_vignette(
    ctx: Context,
    softness: float = 3.0,
    shape: float = 1.0,
    layer_name: str | None = None,
    image_index: int = 0,
) -> dict:
    """Apply a vignette darkening effect around the edges of a layer.

    Parameters:
    - softness: Edge softness / fade width (default 3.0)
    - shape: Shape factor — 1.0 = elliptical (default), values >1 = more rectangular
    - layer_name: Target layer; defaults to active layer
    - image_index: Target image index (default 0)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "apply_vignette",
            {
                "softness": softness,
                "shape": shape,
                "layer_name": layer_name,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def apply_noise(
    ctx: Context, amount: float = 0.2, layer_name: str | None = None, image_index: int = 0
) -> dict:
    """Add noise/grain to a layer.

    Parameters:
    - amount: Noise intensity 0.0-1.0 (default 0.2)
    - layer_name: Target layer; defaults to active layer
    - image_index: Target image index (default 0)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "apply_noise",
            {
                "amount": amount,
                "layer_name": layer_name,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY 9 — Export Pipelines
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def export_icon_sizes(
    ctx: Context,
    output_dir: str,
    platform: str = "android",
    source_image_index: int = 0,
    format: str = "png",
) -> dict:
    """Export an image as a complete icon set for Android or iOS.

    Android sizes: 48 (mdpi), 72 (hdpi), 96 (xhdpi), 144 (xxhdpi),
                   192 (xxxhdpi), 512 (Play Store)
    iOS sizes: 20x1/2/3, 29x1/2/3, 40x2/3, 60x2/3, 76x1/2, 83.5x2, 1024x1

    Parameters:
    - output_dir: Directory to write icon files into
    - platform: "android" (default) or "ios"
    - source_image_index: Image to use as source (default 0)
    - format: Output format — "png" (default)

    Returns: {exported: [{size, file_path}], count, platform}
    """
    try:
        output_dir = _jail_path_or_raise(output_dir, "output_dir")
        conn = get_gimp_connection()
        result = conn.send_command(
            "export_icon_sizes",
            {
                "output_dir": output_dir,
                "platform": platform,
                "source_image_index": source_image_index,
                "format": format,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def export_web_optimized(
    ctx: Context,
    output_dir: str,
    jpeg_quality: int = 85,
    png_compression: int = 9,
    max_width: int | None = None,
    max_height: int | None = None,
    image_index: int = 0,
) -> dict:
    """Export an image as both JPEG and PNG, choosing the smaller format.

    Parameters:
    - output_dir: Directory to write output files
    - jpeg_quality: JPEG quality 1-100 (default 85)
    - png_compression: PNG compression level 0-9 (default 9)
    - max_width / max_height: Optional scaling before export
    - image_index: Source image index (default 0)

    Returns: {jpeg_path, jpeg_size, png_path, png_size, recommendation}
    """
    try:
        output_dir = _jail_path_or_raise(output_dir, "output_dir")
        conn = get_gimp_connection()
        result = conn.send_command(
            "export_web_optimized",
            {
                "output_dir": output_dir,
                "jpeg_quality": jpeg_quality,
                "png_compression": png_compression,
                "max_width": max_width,
                "max_height": max_height,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def warp_region(
    ctx: Context,
    vectors: list,
    image_index: int = 0,
    layer_name: str | None = None,
) -> dict:
    """Warp / liquify a region of the image by pushing pixels in a direction.

    Uses GEGL warp (GIMP 3 native) with plug-in-iwarp fallback. Ideal for
    subtle facial expression edits — e.g. turning a neutral mouth into a smile
    by pushing the mouth corners upward.

    Parameters:
    - vectors: List of warp stroke dicts, each with:
        - x, y      : center of the warp influence (pixels)
        - dx, dy    : push direction — negative dy = push upward
        - radius    : influence radius in pixels (default: 40)
        - amount    : deform strength 0-1 (default: 0.3)
    - image_index: Which open image to edit (default: 0)
    - layer_name: Target layer; omit to use the active/top layer

    Examples — make a character smile:
        warp_region(vectors=[
            {"x": 215, "y": 355, "dx":  5, "dy": -8, "radius": 18, "amount": 0.45},
            {"x": 295, "y": 355, "dx": -5, "dy": -8, "radius": 18, "amount": 0.45},
            {"x": 255, "y": 370, "dx":  0, "dy": -4, "radius": 22, "amount": 0.30},
        ])

    Returns: {"warped_vectors": N}
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "warp_region",
            {
                "image_index": image_index,
                "layer_name": layer_name,
                "vectors": vectors,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def batch_resize(
    ctx: Context,
    width: int | None = None,
    height: int | None = None,
    scale_factor: float | None = None,
    maintain_aspect: bool = True,
) -> dict:
    """Resize all open images to a common target size.

    Parameters:
    - width / height: Target dimensions in pixels (provide one or both)
    - scale_factor: Proportional scale (e.g. 0.5 = 50%); overrides width/height if set
    - maintain_aspect: Preserve aspect ratio when only one dimension is given (default True)

    Returns: {results: [{image_id, old_width, old_height, new_width, new_height}], count}
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "batch_resize",
            {
                "width": width,
                "height": height,
                "scale_factor": scale_factor,
                "maintain_aspect": maintain_aspect,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def export_sprite_sheet(
    ctx: Context,
    output_path: str,
    columns: int | None = None,
    padding: int = 0,
    source: str = "layers",
    image_index: int = 0,
) -> dict:
    """Combine multiple frames into a sprite sheet PNG.

    Parameters:
    - output_path: Absolute path for the output PNG file
    - columns: Number of columns in the grid (defaults to square root of frame count)
    - padding: Pixel gap between frames (default 0)
    - source: "layers" (each layer is a frame; default) or "images" (each open image)
    - image_index: Source image when source="layers" (default 0)

    Returns: {file_path, columns, rows, frame_width, frame_height, count}
    """
    try:
        output_path = _jail_path_or_raise(output_path, "output_path")
        conn = get_gimp_connection()
        result = conn.send_command(
            "export_sprite_sheet",
            {
                "output_path": output_path,
                "columns": columns,
                "padding": padding,
                "source": source,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def export_social_media_kit(
    ctx: Context, output_dir: str, platforms: list | None = None, image_index: int = 0
) -> dict:
    """Export an image resized for multiple social media platforms.

    Platform sizes (all in pixels):
    - instagram_square: 1080x1080
    - instagram_story: 1080x1920
    - twitter_header: 1500x500
    - facebook_cover: 820x312
    - youtube_thumbnail: 1280x720

    Parameters:
    - output_dir: Directory to write output files
    - platforms: List of platform names to export (omit for all five)
    - image_index: Source image index (default 0)

    Returns: {exported: [{platform, file_path, width, height}], count}
    """
    try:
        output_dir = _jail_path_or_raise(output_dir, "output_dir")
        conn = get_gimp_connection()
        result = conn.send_command(
            "export_social_media_kit",
            {
                "output_dir": output_dir,
                "platforms": platforms,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


# ─────────────────────────────────────────────────────────────────────────────
# CATEGORY 10 — Utility
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def list_images(ctx: Context) -> dict:
    """List all images currently open in GIMP.

    Prefer ``orient_workspace`` for agent orientation (schema-versioned SoT).

    Returns:
    - images: list of {index, image_id, name, width, height, color_mode,
                       num_layers, file_path, is_dirty}
    - count: total number of open images
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command("list_images", {})
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def set_active_image(ctx: Context, image_index: int) -> dict:
    """Raise a specific image to the front / make it active in GIMP.

    Parameters:
    - image_index: Index of the image to activate (from list_images)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command("set_active_image", {"image_index": image_index})
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def undo(ctx: Context, steps: int = 1, image_index: int = 0) -> dict:
    """Undo one or more operations on an image.

    Parameters:
    - steps: Number of undo steps (default 1)
    - image_index: Target image index (default 0)

    Returns: {steps_undone}
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command("undo", {"steps": steps, "image_index": image_index})
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def redo(ctx: Context, steps: int = 1, image_index: int = 0) -> dict:
    """Redo one or more previously undone operations on an image.

    Parameters:
    - steps: Number of redo steps (default 1)
    - image_index: Target image index (default 0)

    Returns: {steps_redone}
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command("redo", {"steps": steps, "image_index": image_index})
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(destructive=False, idempotent=False))
@with_structured_error()
def undo_group_begin(
    ctx: Context,
    handle: dict,
    label: str | None = None,
) -> dict:
    """Start an agent undo-group transaction on an open image.

    Wall-clock timeout is **300s from begin** (not sliding / not last-activity);
    override via env ``GIMP_MCP_UNDO_TX_TIMEOUT_S`` (clamped 5-3600). Use for
    **short multi-step** edit sequences only. Long or risky work →
    ``checkpoint_create`` (0009) or segment into multiple transactions.

    Nested agent transactions are allowed up to depth 8. Local mutator undo
    groups nest inside the agent TX and must stay balanced.

    Parameters:
    - handle: image handle from orient_workspace / mutators (required)
    - label: optional label (default ``agent``; max 128 chars)

    Returns: {transaction_id, label, image_handle, depth, timeout_s, opened_at}
    """
    try:
        conn = get_gimp_connection()
        params: dict[str, Any] = {"handle": handle}
        if label is not None:
            params["label"] = label
        result = conn.send_command("undo_group_begin", params)
        if result["status"] == "success":
            out = result["results"]
            _host_tx_hint_update_from_results(
                out if isinstance(out, dict) else {},
                image_id=_image_id_from_handle(handle),
                op="begin",
            )
            return out
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception as exc:
        if sec.debug_enabled():
            traceback.print_exc()
        raise_from_exception(
            exc,
            tool_name="undo_group_begin",
            image_id=_image_id_from_handle(handle),
        )


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(destructive=False, idempotent=False))
@with_structured_error()
def undo_group_end(
    ctx: Context,
    handle: dict,
    transaction_id: str | None = None,
) -> dict:
    """Commit/close the top open agent undo-group transaction.

    Optional ``transaction_id`` must match the stack top (no out-of-order end).
    Empty stack or id mismatch → ``TX_MISMATCH``.

    Parameters:
    - handle: image handle (required)
    - transaction_id: optional; must be the top open TX if provided

    Returns: {transaction_id, status: "committed", depth_remaining}
    """
    try:
        conn = get_gimp_connection()
        params: dict[str, Any] = {"handle": handle}
        if transaction_id is not None:
            params["transaction_id"] = transaction_id
        result = conn.send_command("undo_group_end", params)
        if result["status"] == "success":
            out = result["results"]
            _host_tx_hint_update_from_results(
                out if isinstance(out, dict) else {},
                image_id=_image_id_from_handle(handle),
                op="end",
            )
            return out
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception as exc:
        if sec.debug_enabled():
            traceback.print_exc()
        raise_from_exception(
            exc,
            tool_name="undo_group_end",
            image_id=_image_id_from_handle(handle),
        )


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(destructive=True, idempotent=False))
@with_structured_error()
def undo_group_rollback(
    ctx: Context,
    handle: dict,
    transaction_id: str | None = None,
) -> dict:
    """Abort the top open agent undo-group transaction (end + one image.undo).

    Closes the outer agent group then undoes that user-visible unit, restoring
    pre-TX canvas state. Bumps image generation. Agents **MUST** call
    ``orient_workspace`` after rollback — handles may be stale after structural undo.

    Optional ``transaction_id`` must match the stack top. Nested agent TX: only
    the top agent group is rolled back (outer agent TX stays open).

    Parameters:
    - handle: image handle (required)
    - transaction_id: optional; must be the top open TX if provided

    Returns: {transaction_id, status: "rolled_back", generation}
    """
    try:
        conn = get_gimp_connection()
        params: dict[str, Any] = {"handle": handle}
        if transaction_id is not None:
            params["transaction_id"] = transaction_id
        result = conn.send_command("undo_group_rollback", params)
        if result["status"] == "success":
            out = result["results"]
            _host_tx_hint_update_from_results(
                out if isinstance(out, dict) else {},
                image_id=_image_id_from_handle(handle),
                op="rollback",
            )
            return out
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception as exc:
        if sec.debug_enabled():
            traceback.print_exc()
        raise_from_exception(
            exc,
            tool_name="undo_group_rollback",
            image_id=_image_id_from_handle(handle),
        )


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def undo_group_status(
    ctx: Context,
    handle: dict,
) -> dict:
    """Report open agent undo TXs and recent closed summaries for an image.

    Advanced tool (not HL). Open stack is deepest-last (index 0 = outermost).
    Recent ring buffer cap is 10.

    Parameters:
    - handle: image handle (required)

    Returns: {image_handle, open, recent, timeout_s}
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command("undo_group_status", {"handle": handle})
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def undo_group_force_close(
    ctx: Context,
    handle: dict,
    transaction_id: str | None = None,
) -> dict:
    """Force-end open agent undo groups without undoing canvas changes.

    No ``transaction_id`` → end **all** open agent TXs on the image.
    Mid-stack id → force-close that TX **and all above it** (deepest-first);
    lower stack entries remain open. Work may remain as undoable unit(s) —
    use advanced ``undo`` or a checkpoint for recovery.

    Parameters:
    - handle: image handle (required)
    - transaction_id: optional open TX id (or omit for all)

    Returns: {force_closed_count, force_closed_ids, note}
    """
    try:
        conn = get_gimp_connection()
        params: dict[str, Any] = {"handle": handle}
        if transaction_id is not None:
            params["transaction_id"] = transaction_id
        result = conn.send_command("undo_group_force_close", params)
        if result["status"] == "success":
            out = result["results"]
            # Refresh host hint: clear if stack empty after force_close
            iid = _image_id_from_handle(handle)
            if iid is not None:
                remaining_top = None
                if isinstance(out, dict):
                    remaining_top = out.get("top_transaction_id")
                if remaining_top:
                    _host_tx_hint_set(iid, str(remaining_top))
                else:
                    _host_tx_hint_clear(iid)
            return out
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def convert_color_mode(
    ctx: Context, mode: str, num_colors: int = 256, image_index: int = 0
) -> dict:
    """Convert an image to a different color mode.

    Parameters:
    - mode: "RGB", "GRAY", or "INDEXED"
    - num_colors: Number of colors for INDEXED mode (default 256)
    - image_index: Target image index (default 0)

    Returns status dict.
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "convert_color_mode",
            {
                "mode": mode,
                "num_colors": num_colors,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.HL_TAG}, annotations=_ann(destructive=True))
@with_structured_error()
def close_image(
    ctx: Context,
    handle: dict | None = None,
    image_index: int | None = None,
    save_first: bool = False,
) -> dict:
    """Close an image, optionally saving as XCF first.

    Parameters:
    - handle: Preferred image handle from orient_workspace / mutators
    - image_index: Index of the image to close when handle omitted (default 0)
    - save_first: If True, save as XCF before closing (default False)

    Returns status dict.
    """
    try:
        if handle is None and image_index is None:
            image_index = 0
        conn = get_gimp_connection()
        params: dict[str, Any] = {"save_first": save_first}
        if handle is not None:
            params["handle"] = handle
        if image_index is not None:
            params["image_index"] = int(image_index)
        result = conn.send_command("close_image", params)
        if result["status"] == "success":
            # Plugin force-ended open agent TXs before delete — clear host hint.
            iid = _image_id_from_handle(handle)
            if iid is not None:
                _host_tx_hint_clear(iid)
            else:
                # Legacy image_index path: no image_id on host — drop all hints
                # (single-client MCP; avoids stale pre-TCP rollback_available).
                _HOST_OPEN_TX.clear()
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def get_selection_bounds(ctx: Context, image_index: int = 0) -> dict:
    """Get the bounding rectangle of the current selection.

    Parameters:
    - image_index: Target image index (default 0)

    Returns: {has_selection, x, y, width, height}
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command("get_selection_bounds", {"image_index": image_index})
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def get_pixel_color(
    ctx: Context, x: int, y: int, image_index: int = 0, layer_name: str | None = None
) -> dict:
    """Get the color of a single pixel.

    Parameters:
    - x, y: Pixel coordinates
    - image_index: Target image index (default 0)
    - layer_name: Layer to sample from; defaults to active layer

    Returns: {color_hex, color_rgb: [r, g, b], alpha}
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "get_pixel_color",
            {
                "x": x,
                "y": y,
                "image_index": image_index,
                "layer_name": layer_name,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


@mcp.tool(tags={surface.ADVANCED_TAG})
@with_structured_error()
def get_histogram(ctx: Context, channel: str = "value", image_index: int = 0) -> dict:
    """Get histogram statistics for a channel of the active layer.

    Parameters:
    - channel: "value" (all; default), "red", "green", "blue", "alpha"
    - image_index: Target image index (default 0)

    Returns: {mean, median, std_dev, min, max, pixels, count}
    """
    try:
        conn = get_gimp_connection()
        result = conn.send_command(
            "get_histogram",
            {
                "channel": channel,
                "image_index": image_index,
            },
        )
        if result["status"] == "success":
            return result["results"]
        raise_from_plugin_result(result, "tool")
    except ToolError:
        raise
    except Exception:
        if sec.debug_enabled():
            traceback.print_exc()
        raise


def main():
    mcp.run()


if __name__ == "__main__":
    main()
