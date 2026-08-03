#!/usr/bin/env python3

"""
GIMP MCP Plugin - Model Context Protocol integration for GIMP
Provides bitmap extraction and metadata access functionality
"""

import gi

gi.require_version("Gimp", "3.0")

from gi.repository import Gimp
from gi.repository import GLib
from gi.repository import GObject

import io
import sys
import json
import socket
import traceback
import threading
import base64
import os
import platform
import signal
import uuid
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Security + snapshot helpers (stdlib modules — deploy beside this file)
# ---------------------------------------------------------------------------
_plugin_dir = os.path.dirname(os.path.abspath(__file__))
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)
try:
    import gimp_mcp_security as _sec
except ImportError as _sec_imp_err:  # pragma: no cover - fail closed at runtime
    raise ImportError(
        "gimp_mcp_security.py must sit next to gimp-mcp-plugin.py "
        f"(looked in {_plugin_dir}): {_sec_imp_err}"
    ) from _sec_imp_err
try:
    import gimp_mcp_snapshot as _snap
except ImportError as _snap_imp_err:  # pragma: no cover - fail closed at runtime
    raise ImportError(
        "gimp_mcp_snapshot.py must sit next to gimp-mcp-plugin.py "
        f"(looked in {_plugin_dir}): {_snap_imp_err}"
    ) from _snap_imp_err
try:
    import gimp_mcp_export as _exp
except ImportError as _exp_imp_err:  # pragma: no cover - fail closed at runtime
    raise ImportError(
        "gimp_mcp_export.py must sit next to gimp-mcp-plugin.py "
        f"(looked in {_plugin_dir}): {_exp_imp_err}"
    ) from _exp_imp_err
try:
    import gimp_mcp_handles as _handles
except ImportError as _handles_imp_err:  # pragma: no cover - fail closed at runtime
    raise ImportError(
        "gimp_mcp_handles.py must sit next to gimp-mcp-plugin.py "
        f"(looked in {_plugin_dir}): {_handles_imp_err}"
    ) from _handles_imp_err
try:
    import gimp_mcp_coords as _coords
except ImportError as _coords_imp_err:  # pragma: no cover - fail closed at runtime
    raise ImportError(
        "gimp_mcp_coords.py must sit next to gimp-mcp-plugin.py "
        f"(looked in {_plugin_dir}): {_coords_imp_err}"
    ) from _coords_imp_err
try:
    import gimp_mcp_policy as _policy
except ImportError as _policy_imp_err:  # pragma: no cover - fail closed at runtime
    raise ImportError(
        "gimp_mcp_policy.py must sit next to gimp-mcp-plugin.py "
        f"(looked in {_plugin_dir}): {_policy_imp_err}"
    ) from _policy_imp_err

# Constants for configuration and thresholds
LARGE_SCALING_THRESHOLD = 4.0  # Warn if scaling ratio exceeds this value
MAX_REGION_SIZE = 8192  # Maximum region dimension in pixels
DEFAULT_TIMEOUT_SECONDS = 30  # Default timeout for operations
MAX_SELECT_LAYERS = _handles.MAX_SELECT_LAYERS


def N_(message):
    return message


def _(message):
    return GLib.dgettext(None, message)


def exec_and_get_results(command, context):
    buffer = io.StringIO()
    original_stdout = sys.stdout
    sys.stdout = buffer
    exec(command, context)
    sys.stdout = original_stdout
    output = buffer.getvalue()
    return output


class MCPPlugin(Gimp.PlugIn):
    def __init__(self, host=None, port=None):
        super().__init__()
        # Env plumbing (net-new): defaults are loopback-only AF_INET literals.
        raw_host = host if host is not None else os.environ.get(_sec.ENV_HOST, _sec.DEFAULT_HOST)
        try:
            self.host = _sec.assert_bind_host(raw_host)
        except _sec.SecurityError as e:
            print(f"[MCP] SECURITY bind rejected ({e}); using 127.0.0.1")
            self.host = _sec.DEFAULT_HOST
        if port is not None:
            self.port = int(port)
        else:
            self.port = _sec.get_port()

        # Rotate file-backed tokens on every plugin start (stale-token mitigation).
        token, token_path, generated = _sec.resolve_expected_token(
            generate_if_missing=True,
            rotate_file_token=True,
        )
        self.expected_token = token
        self.token_path = token_path
        self.workspace_root = _sec.workspace_root()
        self.audit_path = _sec.audit_log_path()
        self._last_peer = None

        # Session identity for stable handles / state-manifest (tracks 0006/0007).
        # session_epoch is process-bound (stable for this process lifetime, not
        # always 1). Derived from session_id so each plugin restart gets a new
        # epoch >= 1 and FOREIGN_SESSION fires for pre-restart handles.
        self.session_id = str(uuid.uuid4())
        self.session_epoch = (uuid.UUID(self.session_id).int % 2_000_000_000) + 1
        self.session_started_at = (
            datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
        # Per-image structural generation counters (stable handle registry, 0007).
        self._image_generations: dict = {}
        # Last generation when closed/pruned — ID-recycle defense (never reseed at 1
        # if this GIMP id was seen before in this process).
        self._retired_generations: dict = {}
        # Per-image EXIF/pixel orientation normalized flags (track 0008).
        # True after successful normalize_image_orientation in this session.
        self._orientation_normalized: dict[int, bool] = {}
        # Per-image protected Source_Immutable item ids (track 0009).
        # item_id → denied for mutators unless allow_source_mutation=true.
        self._protected_item_ids: dict[int, set[int]] = {}
        # Working copies created by ensure_source_immutable (skip on re-ensure).
        self._working_item_ids: dict[int, set[int]] = {}

        print(f"[MCP] Secure defaults: bind={self.host}:{self.port} AF_INET")
        if not _sec.is_loopback_host(self.host):
            print(
                f"[MCP] WARNING: non-loopback bind host {self.host!r} "
                f"( {_sec.ENV_ALLOW_NON_LOOPBACK}=1 ) — not recommended for agent use"
            )
        if token_path:
            print(
                f"[MCP] Session token file: {token_path}"
                + (" (rotated/generated)" if generated else "")
            )
        elif os.environ.get(_sec.ENV_TOKEN):
            print("[MCP] Session token: from GIMP_MCP_TOKEN env")
        if self.workspace_root:
            print(f"[MCP] Workspace root: {self.workspace_root}")
        else:
            print(
                f"[MCP] WARNING: {_sec.ENV_WORKSPACE} unset — filesystem tools fail closed (PATH_DENIED)"
            )
        if _sec.exec_allowed():
            print("[MCP] WARNING: GIMP_MCP_ALLOW_EXEC=1 — Class A exec ENABLED (mode: elevated)")
            _sec.write_audit_event(
                {
                    "event": "exec_mode_enabled",
                    "mode": "elevated",
                    "host": self.host,
                    "port": self.port,
                },
                self.audit_path,
            )
        if _sec.debug_enabled():
            print("[MCP] DEBUG diagnostics on (policy flags unchanged)")

        self.running = False
        self.socket = None
        self.server_thread = None
        self.context = {}
        # Bootstrap only — not agent-reachable Class A exec
        exec("from gi.repository import Gimp", self.context)
        self.auto_disconnect_client = True

    def do_set_i18n(self, procname):
        # Plugin has no translations; tell GIMP so it stops logging
        # "catalog directory does not exist" for every registered procedure.
        return False

    def do_query_procedures(self):
        """Register the plugin procedures."""
        return ["plug-in-mcp-server", "plug-in-mcp-check", "plug-in-mcp-restart"]

    def do_create_procedure(self, name):
        """Define the procedure properties."""
        if name == "plug-in-mcp-check":
            procedure = Gimp.Procedure.new(
                self, name, Gimp.PDBProcType.PLUGIN, self._run_check, None
            )
            procedure.set_menu_label(_("Check MCP Server"))
            procedure.set_documentation(
                _("Check whether the MCP server is running"),
                _("Prints MCP server status to the GIMP console"),
                name,
            )
            procedure.set_attribution("Viesar Lab", "Viesar Lab", "2026")
            procedure.add_enum_argument(
                "run-mode",
                _("Run mode"),
                _("The run mode"),
                Gimp.RunMode,
                Gimp.RunMode.INTERACTIVE,
                GObject.ParamFlags.READWRITE,
            )
            procedure.add_menu_path("<Image>/Tools/MCP")
            return procedure

        if name == "plug-in-mcp-restart":
            procedure = Gimp.Procedure.new(
                self, name, Gimp.PDBProcType.PLUGIN, self._run_restart, None
            )
            procedure.set_menu_label(_("Restart MCP Server"))
            procedure.set_documentation(
                _("Restart the MCP server socket"),
                _("Drops and re-binds the MCP server socket on port 9877"),
                name,
            )
            procedure.set_attribution("Viesar Lab", "Viesar Lab", "2026")
            procedure.add_enum_argument(
                "run-mode",
                _("Run mode"),
                _("The run mode"),
                Gimp.RunMode,
                Gimp.RunMode.INTERACTIVE,
                GObject.ParamFlags.READWRITE,
            )
            procedure.add_menu_path("<Image>/Tools/MCP")
            return procedure

        # Default: plug-in-mcp-server
        procedure = Gimp.Procedure.new(self, name, Gimp.PDBProcType.PLUGIN, self.run, None)
        procedure.set_menu_label(_("Start MCP Server"))
        procedure.set_documentation(
            _("Starts an MCP server to control GIMP externally"),
            _("Starts an MCP server to control GIMP externally"),
            name,
        )
        procedure.set_attribution("Viesar Lab", "Viesar Lab", "2026")
        procedure.add_enum_argument(
            "run-mode",
            _("Run mode"),
            _("The run mode"),
            Gimp.RunMode,
            Gimp.RunMode.INTERACTIVE,
            GObject.ParamFlags.READWRITE,
        )
        procedure.add_menu_path("<Image>/Tools/MCP")
        return procedure

    def _run_check(self, procedure, config, run_data):
        """Menu action: print server status."""
        status = "RUNNING" if self.running else "STOPPED"
        print(f"[MCP] Server status: {status} on port {self.port}")
        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())

    def _run_restart(self, procedure, config, run_data):
        """Menu action: restart the server socket."""
        result = self._restart_server()
        print(f"[MCP] Restart result: {result}")
        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())

    def shutdown_server(self, signum=None, frame=None):
        """Gracefully shutdown the server."""
        print(f"Shutdown signal received (signal: {signum}), closing MCP server...")
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass
        if hasattr(self, "_glib_loop") and self._glib_loop:
            self._glib_loop.quit()

    def _start_server_thread(self):
        """Core server loop — runs in a background thread."""
        self.running = True
        try:
            # Re-assert bind host (covers restart / late env changes)
            try:
                self.host = _sec.assert_bind_host(self.host)
            except _sec.SecurityError as e:
                print(f"[MCP] SECURITY: {e}; forcing 127.0.0.1")
                self.host = _sec.DEFAULT_HOST
            print("Creating socket (AF_INET)...")
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.settimeout(1.0)
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)
            print(f"GimpMCP server started on {self.host}:{self.port}")
            _sec.write_audit_event(
                {
                    "event": "server_start",
                    "host": self.host,
                    "port": self.port,
                    "exec_allowed": _sec.exec_allowed(),
                },
                self.audit_path,
            )

            while self.running:
                try:
                    client, address = self.socket.accept()
                    print(f"Connected to client: {address}")
                except TimeoutError:
                    continue
                except OSError:
                    break
                client_thread = threading.Thread(target=self._handle_client, args=(client, address))
                client_thread.daemon = True
                client_thread.start()

            print("MCP server shutting down...")
            if self.socket:
                try:
                    self.socket.close()
                except Exception:
                    pass
                self.socket = None
            print("MCP server stopped")
        except Exception as e:
            print(f"Error in MCP server thread: {e!s}")
            self.running = False

    def run(self, procedure, config, run_data):
        """Menu handler: start the server."""
        if self.running:
            print("MCP Server is already running")
            return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())

        signal.signal(signal.SIGTERM, self.shutdown_server)
        signal.signal(signal.SIGINT, self.shutdown_server)

        # Server socket runs in a background thread
        server_thread = threading.Thread(target=self._start_server_thread, daemon=True)
        server_thread.start()

        # GLib main loop runs in the main thread — required for GIMP API calls
        # (all Gimp.* calls go over the wire protocol which needs GLib to dispatch)
        self._glib_loop = GLib.MainLoop()
        self._glib_loop.run()

        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())

    def _handle_client(self, client, address=None):
        """Handle connected client"""
        # print("Client handler started")
        buffer = b""
        peer = address

        # Receive data in chunks to handle larger payloads
        while True:
            data = client.recv(4096)
            # print(f"Received data: {data}")
            if not data:
                break
            buffer += data

            # Check if we have a complete message
            # For simplicity, assume messages end with newline or are complete JSON
            try:
                if isinstance(buffer, (bytes, bytearray)):
                    request = buffer.decode("utf-8")
                else:
                    request = str(buffer)

                # Try to parse as JSON to see if complete
                if request.strip():
                    json.loads(request)  # This will raise if incomplete
                    break
            except (json.JSONDecodeError, UnicodeDecodeError):
                # Continue receiving if JSON is incomplete
                continue

        if not buffer:
            print("Client disconnected")
            return

        if isinstance(buffer, (bytes, bytearray)):
            request = buffer.decode("utf-8")
        else:
            request = str(buffer)

        # print(f"Parsed request: {request}")
        response = self.execute_command(request, peer=peer)
        print(f"response type: {type(response)}")

        if isinstance(response, dict):
            response = _sec.strip_traceback_unless_debug(response)
            # Completion audit for typed tools (auth/path/exec already audited).
            try:
                status = response.get("status")
                code = response.get("code")
                self._audit(
                    event="command_complete",
                    success=(status == "success"),
                    status=status,
                    code=code,
                )
            except Exception:
                pass
            response_str = json.dumps(response)
        else:
            response_str = str(response)

        # Send response in chunks for large data
        response_bytes = response_str.encode("utf-8")
        bytes_sent = 0
        while bytes_sent < len(response_bytes):
            chunk = response_bytes[bytes_sent : bytes_sent + 8192]
            client.sendall(chunk)
            bytes_sent += len(chunk)

        if self.auto_disconnect_client:
            client.close()
        return

    def _audit(self, **fields):
        """Append audit JSONL (no tokens / file contents)."""
        event = {"peer": str(self._last_peer) if self._last_peer else None}
        event.update(fields)
        _sec.write_audit_event(event, self.audit_path)

    def _jail_path(self, path):
        """Resolve path under workspace root. Returns (Path|None, error_dict|None)."""
        try:
            safe = _sec.resolve_under_root(path, self.workspace_root)
            self._audit(
                event="path_decision",
                decision="allow",
                path_kind="workspace_relative",
            )
            return safe, None
        except _sec.SecurityError as e:
            self._audit(
                event="path_decision",
                decision="deny",
                code=e.code,
                message=e.message,
            )
            return None, e.as_error()

    def execute_command(self, request, peer=None):
        """Execute commands in GIMP's main thread.

        Auth is a **single precheck** after JSON parse and before any type/cmds routing.
        """
        self._last_peer = peer
        try:
            # Bare string command: deprecated in secure mode (no auth possible).
            if isinstance(request, str) and request.strip() == "disable_auto_disconnect":
                self._audit(
                    event="auth",
                    auth_ok=False,
                    type="disable_auto_disconnect_string",
                    success=False,
                )
                return _sec.make_error(
                    _sec.CODE_AUTH_FAILED,
                    "Bare string 'disable_auto_disconnect' is disabled; "
                    'send authenticated JSON: {"type":"disable_auto_disconnect","auth":"..."}',
                )

            j = json.loads(request)

            # ── AUTH PRECHECK (before any type / cmds / side effects) ─────────
            provided_auth = j.get("auth")
            auth_ok = _sec.verify_token(provided_auth, self.expected_token)
            cmd_type = j.get("type")
            if not auth_ok:
                self._audit(
                    event="auth",
                    auth_ok=False,
                    type=cmd_type or ("cmds" if "cmds" in j else "unknown"),
                    success=False,
                )
                return _sec.make_error(
                    _sec.CODE_AUTH_FAILED,
                    "Authentication failed (missing or invalid auth token)",
                )
            self._audit(
                event="auth",
                auth_ok=True,
                success=True,
                type=cmd_type or ("cmds" if "cmds" in j else "unknown"),
            )

            # Authenticated JSON equivalent of deprecated string command
            if cmd_type == "disable_auto_disconnect":
                self.auto_disconnect_client = False
                self._audit(event="command", type=cmd_type, success=True)
                return {"status": "success", "results": "OK"}

            # ── Class A exec gate (plugin-internal cmds / eval) ───────────────
            if "cmds" in j:
                if not _sec.exec_allowed():
                    self._audit(
                        event="exec",
                        type="cmds",
                        success=False,
                        code=_sec.CODE_EXEC_DISABLED,
                    )
                    return _sec.make_error(
                        _sec.CODE_EXEC_DISABLED,
                        "Plugin-internal arbitrary Python exec is disabled. "
                        "Set GIMP_MCP_ALLOW_EXEC=1 only for advanced use.",
                    )
                self._audit(event="exec", type="cmds", mode="elevated", success=True)
                a = ["python-fu-exec", j["cmds"]]
                outputs = ["OK"]
                if len(a) > 1 and a[1]:
                    print(f"Executing commands (elevated): {a[1]}")
                    outputs = [exec_and_get_results(c, self.context) for c in a[1]]
                return {"status": "success", "results": outputs}

            if "type" in j and j["type"] == "get_image_bitmap":
                params = j.get("params", {})
                return self._get_current_image_bitmap(params)
            elif "type" in j and j["type"] == "get_image_metadata":
                return self._get_current_image_metadata(j.get("params", {}))
            elif "type" in j and j["type"] == "orient_workspace":
                return self._orient_workspace(j.get("params", {}))
            elif "type" in j and j["type"] == "select_image":
                return self._select_image(j.get("params", {}))
            elif "type" in j and j["type"] == "select_layers":
                return self._select_layers(j.get("params", {}))
            elif "type" in j and j["type"] == "normalize_image_orientation":
                return self._normalize_image_orientation(j.get("params", {}))
            elif "type" in j and j["type"] == "get_gimp_info":
                return self._get_gimp_info()
            elif "type" in j and j["type"] == "get_context_state":
                return self._get_context_state()
            elif "type" in j and j["type"] == "check_server":
                return {"status": "success", "results": {"running": True, "port": self.port}}
            elif "type" in j and j["type"] == "restart_server":
                return self._restart_server()
            elif "type" in j and j["type"] == "new_canvas":
                params = j.get("params", {})
                return self._new_canvas(params)
            # ── Category 1: File Operations ──────────────────────────────────
            elif "type" in j and j["type"] == "open_image":
                return self._open_image(j.get("params", {}))
            elif "type" in j and j["type"] == "save_xcf":
                return self._save_xcf(j.get("params", {}))
            elif "type" in j and j["type"] == "export_image":
                return self._export_image(j.get("params", {}))
            elif "type" in j and j["type"] == "batch_export":
                return self._batch_export(j.get("params", {}))
            elif "type" in j and j["type"] == "verify_alpha_channel":
                return self._verify_alpha_channel(j.get("params", {}))
            # ── Category 2: Image Adjustments ────────────────────────────────
            elif "type" in j and j["type"] == "auto_levels":
                return self._auto_levels(j.get("params", {}))
            elif "type" in j and j["type"] == "adjust_curves":
                return self._adjust_curves(j.get("params", {}))
            elif "type" in j and j["type"] == "adjust_brightness_contrast":
                return self._adjust_brightness_contrast(j.get("params", {}))
            elif "type" in j and j["type"] == "adjust_hue_saturation":
                return self._adjust_hue_saturation(j.get("params", {}))
            elif "type" in j and j["type"] == "adjust_color_balance":
                return self._adjust_color_balance(j.get("params", {}))
            elif "type" in j and j["type"] == "sharpen":
                return self._sharpen(j.get("params", {}))
            elif "type" in j and j["type"] == "blur":
                return self._blur(j.get("params", {}))
            elif "type" in j and j["type"] == "denoise":
                return self._denoise(j.get("params", {}))
            elif "type" in j and j["type"] == "desaturate":
                return self._desaturate(j.get("params", {}))
            elif "type" in j and j["type"] == "invert_colors":
                return self._invert_colors(j.get("params", {}))
            # ── Category 3: Resize & Transform ───────────────────────────────
            elif "type" in j and j["type"] == "scale_image":
                return self._scale_image(j.get("params", {}))
            elif "type" in j and j["type"] == "scale_to_fit":
                return self._scale_to_fit(j.get("params", {}))
            elif "type" in j and j["type"] == "crop_to_selection":
                return self._crop_to_selection(j.get("params", {}))
            elif "type" in j and j["type"] == "crop_to_rect":
                return self._crop_to_rect(j.get("params", {}))
            elif "type" in j and j["type"] == "rotate_image":
                return self._rotate_image(j.get("params", {}))
            elif "type" in j and j["type"] == "flip_image":
                return self._flip_image(j.get("params", {}))
            elif "type" in j and j["type"] == "resize_canvas":
                return self._resize_canvas(j.get("params", {}))
            # ── Category 4: Selections ────────────────────────────────────────
            elif "type" in j and j["type"] == "select_rectangle":
                return self._select_rectangle(j.get("params", {}))
            elif "type" in j and j["type"] == "select_ellipse":
                return self._select_ellipse(j.get("params", {}))
            elif "type" in j and j["type"] == "select_by_color":
                return self._select_by_color(j.get("params", {}))
            elif "type" in j and j["type"] == "select_all":
                return self._select_all(j.get("params", {}))
            elif "type" in j and j["type"] == "select_none":
                return self._select_none(j.get("params", {}))
            elif "type" in j and j["type"] == "invert_selection":
                return self._invert_selection(j.get("params", {}))
            elif "type" in j and j["type"] == "modify_selection":
                return self._modify_selection(j.get("params", {}))
            # ── Category 5: Layer Operations ──────────────────────────────────
            elif "type" in j and j["type"] == "create_layer":
                return self._create_layer(j.get("params", {}))
            elif "type" in j and j["type"] == "duplicate_layer":
                return self._duplicate_layer(j.get("params", {}))
            elif "type" in j and j["type"] == "delete_layer":
                return self._delete_layer(j.get("params", {}))
            elif "type" in j and j["type"] == "rename_layer":
                return self._rename_layer(j.get("params", {}))
            elif "type" in j and j["type"] == "set_layer_properties":
                return self._set_layer_properties(j.get("params", {}))
            elif "type" in j and j["type"] == "reorder_layer":
                return self._reorder_layer(j.get("params", {}))
            elif "type" in j and j["type"] == "flatten_image":
                return self._flatten_image(j.get("params", {}))
            elif "type" in j and j["type"] == "merge_visible_layers":
                return self._merge_visible_layers(j.get("params", {}))
            elif "type" in j and j["type"] == "list_layers":
                return self._list_layers(j.get("params", {}))
            elif "type" in j and j["type"] == "ensure_source_immutable":
                return self._ensure_source_immutable(j.get("params", {}))
            elif "type" in j and j["type"] == "checkpoint_create":
                return self._checkpoint_create(j.get("params", {}))
            elif "type" in j and j["type"] == "checkpoint_restore":
                return self._checkpoint_restore(j.get("params", {}))
            # ── Category 6: Color & Paint ─────────────────────────────────────
            elif "type" in j and j["type"] == "fill_layer":
                return self._fill_layer(j.get("params", {}))
            elif "type" in j and j["type"] == "fill_selection":
                return self._fill_selection(j.get("params", {}))
            elif "type" in j and j["type"] == "set_colors":
                return self._set_colors(j.get("params", {}))
            elif "type" in j and j["type"] == "draw_line":
                return self._draw_line(j.get("params", {}))
            elif "type" in j and j["type"] == "draw_rectangle":
                return self._draw_rectangle(j.get("params", {}))
            elif "type" in j and j["type"] == "draw_ellipse":
                return self._draw_ellipse(j.get("params", {}))
            elif "type" in j and j["type"] == "fill_rectangle":
                return self._fill_rectangle(j.get("params", {}))
            elif "type" in j and j["type"] == "fill_ellipse":
                return self._fill_ellipse(j.get("params", {}))
            elif "type" in j and j["type"] == "gradient_fill":
                return self._gradient_fill(j.get("params", {}))
            # ── Category 7: Text ──────────────────────────────────────────────
            elif "type" in j and j["type"] == "add_text":
                return self._add_text(j.get("params", {}))
            elif "type" in j and j["type"] == "edit_text":
                return self._edit_text(j.get("params", {}))
            elif "type" in j and j["type"] == "list_fonts":
                return self._list_fonts(j.get("params", {}))
            # ── Category 8: Filters & Effects ────────────────────────────────
            elif "type" in j and j["type"] == "apply_drop_shadow":
                return self._apply_drop_shadow(j.get("params", {}))
            elif "type" in j and j["type"] == "apply_gaussian_blur":
                return self._apply_gaussian_blur(j.get("params", {}))
            elif "type" in j and j["type"] == "apply_pixelate":
                return self._apply_pixelate(j.get("params", {}))
            elif "type" in j and j["type"] == "apply_emboss":
                return self._apply_emboss(j.get("params", {}))
            elif "type" in j and j["type"] == "apply_vignette":
                return self._apply_vignette(j.get("params", {}))
            elif "type" in j and j["type"] == "apply_noise":
                return self._apply_noise(j.get("params", {}))
            # ── Category 9: Export Pipelines ──────────────────────────────────
            elif "type" in j and j["type"] == "export_icon_sizes":
                return self._export_icon_sizes(j.get("params", {}))
            elif "type" in j and j["type"] == "export_web_optimized":
                return self._export_web_optimized(j.get("params", {}))
            elif "type" in j and j["type"] == "batch_resize":
                return self._batch_resize(j.get("params", {}))
            elif "type" in j and j["type"] == "export_sprite_sheet":
                return self._export_sprite_sheet(j.get("params", {}))
            elif "type" in j and j["type"] == "export_social_media_kit":
                return self._export_social_media_kit(j.get("params", {}))
            # ── Category 10: Utility ──────────────────────────────────────────
            elif "type" in j and j["type"] == "list_images":
                return self._list_images(j.get("params", {}))
            elif "type" in j and j["type"] == "set_active_image":
                return self._set_active_image(j.get("params", {}))
            elif "type" in j and j["type"] == "undo":
                return self._undo(j.get("params", {}))
            elif "type" in j and j["type"] == "redo":
                return self._redo(j.get("params", {}))
            elif "type" in j and j["type"] == "convert_color_mode":
                return self._convert_color_mode(j.get("params", {}))
            elif "type" in j and j["type"] == "close_image":
                return self._close_image(j.get("params", {}))
            elif "type" in j and j["type"] == "get_selection_bounds":
                return self._get_selection_bounds(j.get("params", {}))
            elif "type" in j and j["type"] == "get_pixel_color":
                return self._get_pixel_color(j.get("params", {}))
            elif "type" in j and j["type"] == "get_histogram":
                return self._get_histogram(j.get("params", {}))
            elif "type" in j and j["type"] == "warp_region":
                return self._warp_region(j.get("params", {}))
            else:
                # Legacy params.args exec path (python-fu-eval / python-fu-exec)
                p = j.get("params") or {}
                a = p.get("args") if isinstance(p, dict) else None
                if not a:
                    return _sec.make_error(
                        "UNKNOWN_COMMAND",
                        "Unknown command (no type / cmds / params.args)",
                    )
                if not _sec.exec_allowed():
                    self._audit(
                        event="exec",
                        type=str(a[0]) if a else "params.args",
                        success=False,
                        code=_sec.CODE_EXEC_DISABLED,
                    )
                    return _sec.make_error(
                        _sec.CODE_EXEC_DISABLED,
                        "Plugin-internal arbitrary Python exec/eval is disabled. "
                        "Set GIMP_MCP_ALLOW_EXEC=1 only for advanced use.",
                    )
                self._audit(
                    event="exec",
                    type=str(a[0]),
                    mode="elevated",
                    success=True,
                )
                if a[0] == "python-fu-eval":
                    print(f"evaluating exprs (elevated): {a[1]}")
                    vals = [str(eval(e)) for e in a[1]]
                    return {"status": "success", "results": vals}
                print(f"Executing commands (elevated): {a[1]}")
                outputs = [exec_and_get_results(c, self.context) for c in a[1]]
                return {"status": "success", "results": outputs}

        except Exception as e:
            error_msg = f"Error executing command: {e!s}"
            print(error_msg)
            if _sec.debug_enabled():
                print(traceback.format_exc())
            return _sec.redact_error(e, code=_sec.CODE_INTERNAL, message=str(e))

    def _get_current_image_bitmap(self, params=None):
        """Export the visible composite of a GIMP image as base64 PNG + mapping metadata.

        Never mutates the user's original image. Works only on a duplicate:
        Selection.none → merge_visible_layers(CLIP_TO_IMAGE) [or flatten fallback]
        → optional region crop → optional scale → PNG export.
        """
        dup = None
        temp_path = None
        try:
            if params is None:
                params = {}

            print(f"Getting current image bitmap with params: {params}")

            max_width = params.get("max_width")
            max_height = params.get("max_height")

            # Normalize region (accepts x/y or origin_x/origin_y)
            raw_region = params.get("region")
            try:
                region = _snap.normalize_region(raw_region) if raw_region else None
            except (TypeError, ValueError) as e:
                return {"status": "error", "error": f"Invalid region: {e!s}"}

            origin_x = origin_y = region_width = region_height = None
            region_max_w = region_max_h = None
            region_requested = False
            if region:
                origin_x = region.get("origin_x")
                origin_y = region.get("origin_y")
                region_width = region.get("width")
                region_height = region.get("height")
                region_max_w = region.get("max_width")
                region_max_h = region.get("max_height")
                region_requested = any(
                    v is not None for v in (origin_x, origin_y, region_width, region_height)
                )

            # Select image: handle preferred, else image_index (default 0).
            # Mapping metadata must report the resolved image's open-list index
            # (not a stale default 0 when only handle was supplied).
            try:
                original_image, image_id = self._resolve_image_from_params(params)
                images_open = list(Gimp.get_images() or [])
                image_index = next(
                    (i for i, im in enumerate(images_open) if int(im.get_id()) == int(image_id)),
                    int(params.get("image_index", 0)),
                )
            except _handles.HandleError as e:
                return self._handle_error_response(e)
            except RuntimeError as e:
                return {"status": "error", "error": str(e)}

            source_width = original_image.get_width()
            source_height = original_image.get_height()

            if region_requested:
                if (
                    origin_x is None
                    or origin_y is None
                    or region_width is None
                    or region_height is None
                ):
                    return {
                        "status": "error",
                        "error": "For region selection, all parameters are required: "
                        "origin_x/x, origin_y/y, width, height",
                    }
                if (
                    origin_x + region_width > source_width
                    or origin_y + region_height > source_height
                ):
                    return {
                        "status": "error",
                        "error": (
                            f"Region bounds invalid. Image size: "
                            f"{source_width}x{source_height}, "
                            f"requested region: ({origin_x},{origin_y}) "
                            f"{region_width}x{region_height}"
                        ),
                    }

            # ---- Primary path: duplicate only; never touch original ----
            dup = original_image.duplicate()
            try:
                dup.undo_disable()
            except (AttributeError, RuntimeError) as e:
                print(f"Warning: undo_disable on snapshot dup failed: {e}")

            # Clear inherited selection so merge is not clipped — fail closed.
            # GIMP Selection.none returns gboolean; treat explicit False as failure.
            # (Exceptions and False must not proceed; GI None is treated as ok.)
            self._selection_none_or_fail(dup, "Selection.none on snapshot dup failed")

            # Capture merge/flatten return layer — do not guess layers[0]
            # (merge_visible_layers keeps invisible layers; layers[0] may be hidden).
            composite_method = _snap.COMPOSITE_METHOD_MERGE
            merged = None
            try:
                merged = dup.merge_visible_layers(Gimp.MergeType.CLIP_TO_IMAGE)
            except (AttributeError, RuntimeError) as merge_err:
                print(f"merge_visible_layers failed, trying flatten: {merge_err}")
                self._selection_none_or_fail(dup, "Selection.none before flatten failed")
                try:
                    merged = dup.flatten()
                    composite_method = _snap.COMPOSITE_METHOD_FLATTEN
                except (AttributeError, RuntimeError) as flatten_err:
                    raise RuntimeError(
                        f"Composite failed: merge_visible_layers: {merge_err}; "
                        f"flatten: {flatten_err}"
                    ) from flatten_err
            else:
                if merged is None:
                    print("merge_visible_layers returned None, trying flatten")
                    self._selection_none_or_fail(dup, "Selection.none before flatten failed")
                    try:
                        merged = dup.flatten()
                        composite_method = _snap.COMPOSITE_METHOD_FLATTEN
                    except (AttributeError, RuntimeError) as flatten_err:
                        raise RuntimeError(
                            "Composite failed: merge_visible_layers returned None; "
                            f"flatten: {flatten_err}"
                        ) from flatten_err

            if merged is None:
                return {
                    "status": "error",
                    "error": "Composite merge/flatten returned no layer",
                }

            # Crop region on the composite duplicate only
            if region_requested:
                print(
                    f"Cropping composite region "
                    f"({origin_x},{origin_y}) {region_width}x{region_height}"
                )
                dup.crop(region_width, region_height, origin_x, origin_y)

            # Scale if max dimensions provided (region max_* preferred when set)
            current_width = dup.get_width()
            current_height = dup.get_height()
            target_width = current_width
            target_height = current_height

            if region_max_w is not None and region_max_h is not None:
                max_w, max_h = int(region_max_w), int(region_max_h)
            elif max_width is not None and max_height is not None:
                max_w, max_h = int(max_width), int(max_height)
            else:
                max_w = max_h = None

            if max_w is not None and max_h is not None:
                target_width, target_height = _snap.compute_fit_scale(
                    current_width, current_height, max_w, max_h
                )
                if target_width != current_width or target_height != current_height:
                    scaling_ratio = (target_width * target_height) / (
                        current_width * current_height
                    )
                    if scaling_ratio > LARGE_SCALING_THRESHOLD:
                        print(
                            f"Warning: Large scaling operation detected "
                            f"(ratio: {scaling_ratio:.2f}). This may take time."
                        )
                    print(
                        f"Scaling composite from {current_width}x{current_height} "
                        f"to {target_width}x{target_height}"
                    )
                    dup.scale(target_width, target_height)

            # Export PNG via existing PDB paths (no Pillow).
            # Prefer the merge/flatten return layer as drawable. Crop/scale may
            # invalidate the proxy — re-resolve to a single safe layer only.
            temp_path = str(_snap.snapshot_temp_path())
            drawable = None
            if merged is not None:
                try:
                    _ = merged.get_width()
                    drawable = merged
                except (AttributeError, RuntimeError):
                    drawable = None

            if drawable is None:
                try:
                    layers = list(dup.get_layers() or [])
                except (AttributeError, RuntimeError, TypeError):
                    layers = []
                visible = []
                for layer in layers:
                    try:
                        if layer.get_visible():
                            visible.append(layer)
                    except (AttributeError, RuntimeError):
                        continue
                if len(visible) == 1:
                    drawable = visible[0]
                elif len(layers) == 1:
                    drawable = layers[0]
                else:
                    return {
                        "status": "error",
                        "error": "No drawable for export after composite",
                    }

            if not drawable:
                return {
                    "status": "error",
                    "error": "No drawable for export after composite",
                }

            # Export PNG (no Pillow). Fail closed: never return success for empty
            # or non-PNG bytes (mkstemp pre-creates an empty file).
            export_errors: list[str] = []
            try:
                from gi.repository import Gio

                file_obj = Gio.File.new_for_path(temp_path)
                export_proc = Gimp.get_pdb().lookup_procedure("file-png-export")
                if not export_proc:
                    export_errors.append("PNG export procedure not found")
                else:
                    export_config = export_proc.create_config()
                    export_config.set_property("image", dup)
                    export_config.set_property("file", file_obj)
                    drawable_set = False
                    try:
                        export_config.set_property("drawable", drawable)
                        drawable_set = True
                    except Exception:
                        try:
                            export_config.set_property("drawables", [drawable])
                            drawable_set = True
                        except Exception as prop_err:
                            # Do not run file-png-export without a drawable —
                            # image-level fallbacks handle that path.
                            export_errors.append(
                                f"file-png-export drawable/drawables property failed: {prop_err}"
                            )

                    if drawable_set:
                        result = export_proc.run(export_config)
                        print(f"Export result: {result}")
                        if not _snap.validate_png_file(temp_path):
                            export_errors.append(
                                f"file-png-export produced empty/invalid PNG (result={result})"
                            )
            except Exception as export_error:
                print(f"Export error: {export_error}")
                export_errors.append(f"file-png-export error: {export_error}")

            # Image-level fallbacks when primary path did not yield a valid PNG
            if not _snap.validate_png_file(temp_path):
                try:
                    from gi.repository import Gio

                    file_obj = Gio.File.new_for_path(temp_path)
                    Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, dup, file_obj)
                    print("Fallback export (Gimp.file_save) attempted")
                    if not _snap.validate_png_file(temp_path):
                        export_errors.append("Gimp.file_save produced empty/invalid PNG")
                except Exception as fallback_error:
                    print(f"Fallback export error: {fallback_error}")
                    export_errors.append(f"Gimp.file_save: {fallback_error}")

            if not _snap.validate_png_file(temp_path):
                try:
                    from gi.repository import Gio

                    file_obj = Gio.File.new_for_path(temp_path)
                    pdb = Gimp.get_pdb()
                    save_proc = pdb.lookup_procedure("gimp-file-save")
                    if save_proc:
                        save_config = save_proc.create_config()
                        save_config.set_property("image", dup)
                        save_config.set_property("file", file_obj)
                        save_result = save_proc.run(save_config)
                        print(f"PDB save result: {save_result}")
                        if not _snap.validate_png_file(temp_path):
                            export_errors.append(
                                f"gimp-file-save produced empty/invalid PNG (result={save_result})"
                            )
                    else:
                        export_errors.append("gimp-file-save procedure not found")
                except Exception as pdb_error:
                    export_errors.append(f"gimp-file-save: {pdb_error}")

            # Fail closed: never base64-encode empty mkstemp / garbage bytes
            if not _snap.validate_png_file(temp_path):
                detail = "; ".join(export_errors) if export_errors else "unknown"
                return {
                    "status": "error",
                    "error": (f"PNG export failed or produced empty/invalid file: {detail}"),
                }

            with open(temp_path, "rb") as f:
                image_bytes = f.read()
            if not _snap.validate_png_bytes(image_bytes):
                return {
                    "status": "error",
                    "error": "PNG export validation failed: empty or non-PNG data",
                }
            encoded_image = base64.b64encode(image_bytes).decode("utf-8")

            rendered_width = dup.get_width()
            rendered_height = dup.get_height()

            region_for_mapping = None
            if region_requested:
                region_for_mapping = {
                    "origin_x": int(origin_x),
                    "origin_y": int(origin_y),
                    "width": int(region_width),
                    "height": int(region_height),
                }

            # Snapshot-time EXIF + session honesty (track 0008) for mapping.
            try:
                image_id_for_map = int(original_image.get_id())
            except Exception:
                image_id_for_map = None
            exif_raw = self._orient_exif_orientation(original_image)
            pixel_norm = self._pixel_orientation_normalized(image_id_for_map, exif_raw)
            exif_orig = _coords.orientation_for_manifest(exif_raw)

            mapping = _snap.build_mapping_metadata(
                image_index=image_index,
                source_width=source_width,
                source_height=source_height,
                rendered_width=rendered_width,
                rendered_height=rendered_height,
                region=region_for_mapping,
                composite_method=composite_method,
                pixel_orientation_normalized=pixel_norm,
                exif_orientation_original=exif_orig,
            )

            return {
                "status": "success",
                "results": {
                    "image_data": encoded_image,
                    "format": "png",
                    "width": rendered_width,
                    "height": rendered_height,
                    "original_width": source_width,
                    "original_height": source_height,
                    "encoding": "base64",
                    "image_index": image_index,
                    "mode": mapping["mode"],
                    "scale_x": mapping["scale_x"],
                    "scale_y": mapping["scale_y"],
                    "region": mapping["region"],
                    "composite_method": mapping["composite_method"],
                    "source_width": source_width,
                    "source_height": source_height,
                    "rendered_width": rendered_width,
                    "rendered_height": rendered_height,
                    # Flatten additive mapping keys so server pass-through sees them
                    "coordinate_space": mapping["coordinate_space"],
                    "origin": mapping["origin"],
                    "x_axis": mapping["x_axis"],
                    "y_axis": mapping["y_axis"],
                    "preview_padding_x": mapping["preview_padding_x"],
                    "preview_padding_y": mapping["preview_padding_y"],
                    "view_rotation_ignored": mapping["view_rotation_ignored"],
                    "pixel_orientation_normalized": mapping["pixel_orientation_normalized"],
                    "exif_orientation_original": mapping["exif_orientation_original"],
                    "processing_applied": {
                        "region_extracted": region_requested,
                        "scaled": (
                            target_width != current_width or target_height != current_height
                        ),
                        "region_coords": {
                            "x": origin_x,
                            "y": origin_y,
                            "w": region_width,
                            "h": region_height,
                        }
                        if region_requested
                        else None,
                    },
                },
            }

        except (RuntimeError, AttributeError, OSError, ValueError) as e:
            return {
                "status": "error",
                "error": f"Processing error: {e!s}",
                "traceback": traceback.format_exc(),
            }
        finally:
            if dup is not None:
                try:
                    dup.delete()
                except (AttributeError, RuntimeError) as e:
                    print(f"Warning: Failed to delete snapshot duplicate: {e}")
            if temp_path is not None and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError as e:
                    print(f"Warning: Failed to unlink snapshot temp file: {e}")

    def _get_current_image_metadata(self, params=None):
        """Get comprehensive metadata about the current image without bitmap data.

        params may include image_index (default 0) for multi-document workspaces.
        """
        try:
            print("Getting current image metadata...")
            image = self._get_image(int((params or {}).get("image_index", 0)))

            # Basic image properties
            width = image.get_width()
            height = image.get_height()

            # Get image type and base type
            base_type = image.get_base_type()
            base_type_str = self._base_type_to_string(base_type)

            # Get precision and color profile info
            precision = image.get_precision()
            precision_str = self._precision_to_string(precision)

            # Get layers information
            layers = image.get_layers()
            layers_info = []
            for i, layer in enumerate(layers):
                try:
                    layer_info = {
                        "name": layer.get_name(),
                        "visible": layer.get_visible(),
                        "opacity": layer.get_opacity(),
                        "width": layer.get_width(),
                        "height": layer.get_height(),
                        "has_alpha": layer.has_alpha(),
                        "is_group": hasattr(layer, "get_children") and callable(layer.get_children),
                        "layer_type": self._get_layer_type_string(layer),
                    }
                    # Try to get layer mode if available
                    try:
                        layer_info["blend_mode"] = str(layer.get_mode())
                    except Exception:
                        layer_info["blend_mode"] = "unknown"

                    layers_info.append(layer_info)
                except Exception as layer_error:
                    print(f"Error getting layer {i} info: {layer_error}")
                    layers_info.append({"name": f"Layer {i}", "error": str(layer_error)})

            # Get channels information
            channels = image.get_channels()
            channels_info = []
            for i, channel in enumerate(channels):
                try:
                    channel_info = {
                        "name": channel.get_name(),
                        "visible": channel.get_visible(),
                        "opacity": channel.get_opacity(),
                        "color": str(channel.get_color())
                        if hasattr(channel, "get_color")
                        else "unknown",
                    }
                    channels_info.append(channel_info)
                except Exception as channel_error:
                    print(f"Error getting channel {i} info: {channel_error}")
                    channels_info.append({"name": f"Channel {i}", "error": str(channel_error)})

            # Get paths/vectors information
            paths = []
            try:
                image_paths = image.get_paths()
                for i, path in enumerate(image_paths):
                    try:
                        path_info = {
                            "name": path.get_name(),
                            "visible": path.get_visible(),
                            "num_strokes": len(path.get_strokes())
                            if hasattr(path, "get_strokes")
                            else 0,
                        }
                        paths.append(path_info)
                    except Exception as path_error:
                        print(f"Error getting path {i} info: {path_error}")
                        paths.append({"name": f"Path {i}", "error": str(path_error)})
            except Exception as paths_error:
                print(f"Error getting paths: {paths_error}")

            # Get file information if available
            file_info = {}
            try:
                image_file = image.get_file()
                if image_file:
                    file_info = {
                        "path": image_file.get_path() if hasattr(image_file, "get_path") else None,
                        "uri": image_file.get_uri() if hasattr(image_file, "get_uri") else None,
                        "basename": image_file.get_basename()
                        if hasattr(image_file, "get_basename")
                        else None,
                    }
            except Exception as file_error:
                print(f"Error getting file info: {file_error}")
                file_info = {"error": str(file_error)}

            # Get resolution information
            resolution_x = resolution_y = None
            try:
                resolution_x, resolution_y = image.get_resolution()
            except Exception as res_error:
                print(f"Error getting resolution: {res_error}")

            # Check if image has unsaved changes
            is_dirty = False
            try:
                is_dirty = image.is_dirty()
            except Exception as dirty_error:
                print(f"Error getting dirty status: {dirty_error}")

            metadata = {
                "basic": {
                    "width": width,
                    "height": height,
                    "base_type": base_type_str,
                    "precision": precision_str,
                    "resolution_x": resolution_x,
                    "resolution_y": resolution_y,
                    "is_dirty": is_dirty,
                },
                "structure": {
                    "num_layers": len(layers),
                    "num_channels": len(channels),
                    "num_paths": len(paths),
                    "layers": layers_info,
                    "channels": channels_info,
                    "paths": paths,
                },
                "file": file_info,
            }

            return {"status": "success", "results": metadata}

        except Exception as e:
            print(f"Error getting image metadata: {e!s}")
            return _sec.redact_error(e, message=f"Error getting image metadata: {e!s}")

    # -------------------------------------------------------------------------
    # State-manifest orientation (track 0006) — read-only raw dump
    # Host (gimp_mcp_state.finalize_manifest) injects capabilities + transport.
    # -------------------------------------------------------------------------

    _ORIENT_MAX_LAYER_DEPTH = 32

    def _orient_classify_kind(self, layer):
        """Classify layer kind via isinstance / gtype (not pixel-type strings)."""
        try:
            for cls_name, kind in (
                ("GroupLayer", "group"),
                ("TextLayer", "text"),
                ("LinkLayer", "link"),
                ("VectorLayer", "vector"),
            ):
                cls = getattr(Gimp, cls_name, None)
                if cls is not None and isinstance(layer, cls):
                    return kind
        except Exception:
            pass
        type_name = None
        try:
            gtype = getattr(layer, "__gtype__", None)
            if gtype is not None:
                type_name = getattr(gtype, "name", None) or str(gtype)
        except Exception:
            type_name = None
        if type_name:
            # Inline map (keep plugin free of gimp_mcp_state import).
            name = str(type_name).rsplit(".", 1)[-1]
            mapping = {
                "GimpGroupLayer": "group",
                "GimpTextLayer": "text",
                "GimpLinkLayer": "link",
                "GimpVectorLayer": "vector",
            }
            if name in mapping:
                return mapping[name]
            # Exact short forms only (no substring heuristics like "group" in name).
            lower = name.lower()
            exact = {
                "gimpgrouplayer": "group",
                "grouplayer": "group",
                "group": "group",
                "gimptextlayer": "text",
                "textlayer": "text",
                "text": "text",
                "gimplinklayer": "link",
                "linklayer": "link",
                "link": "link",
                "gimpvectorlayer": "vector",
                "vectorlayer": "vector",
                "vector": "vector",
            }
            if lower in exact:
                return exact[lower]
        # is_group() fallback for group layers when types are missing
        try:
            if hasattr(layer, "is_group") and callable(layer.is_group) and layer.is_group():
                return "group"
        except Exception:
            pass
        return "raster"

    def _orient_item_handle(self, item_id, image_id):
        """Emit item handle using live registry generation (seed if missing)."""
        return self._emit_item_handle_ids(int(item_id), int(image_id))

    def _orient_image_handle(self, image_id):
        """Emit image handle using live registry generation (seed if missing)."""
        return self._emit_image_handle_id(int(image_id))

    def _orient_source_path(self, image):
        try:
            image_file = image.get_file()
            if image_file is None:
                return None
            if hasattr(image_file, "get_path"):
                path = image_file.get_path()
                return path if path else None
        except Exception:
            pass
        return None

    def _orient_selection(self, image):
        """Read-only Selection.bounds → {empty, bounds?}."""
        try:
            _ok, non_empty, x1, y1, x2, y2 = Gimp.Selection.bounds(image)
            if not non_empty:
                return {"empty": True}
            return {
                "empty": False,
                "bounds": {
                    "x": int(x1),
                    "y": int(y1),
                    "width": int(x2 - x1),
                    "height": int(y2 - y1),
                },
            }
        except Exception as e:
            print(f"[MCP] orient selection bounds failed: {e}")
            return {"empty": True}

    def _orient_color_profile(self, image):
        """Best-effort color profile; null on failure (never fail whole orient)."""
        try:
            profile = None
            if hasattr(image, "get_color_profile"):
                profile = image.get_color_profile()
            if profile is None:
                return None
            out = {"embedded": True}
            try:
                if hasattr(profile, "get_name"):
                    name = profile.get_name()
                    if name:
                        out["name"] = str(name)
            except Exception:
                pass
            try:
                if hasattr(profile, "get_description"):
                    desc = profile.get_description()
                    if desc:
                        out["description"] = str(desc)
            except Exception:
                pass
            if "name" not in out:
                out["name"] = "unknown"
            return out
        except Exception as e:
            print(f"[MCP] orient color_profile failed: {e}")
            return None

    def _orient_exif_orientation(self, image):
        """Best-effort Exif orientation tag as int, or null if absent.

        Valid tags are 1..8. Present-but-invalid ints (e.g. 0, 9) are returned
        as-is so honesty can treat them as non-identity; callers that emit
        state-manifest fields must pass through
        ``_coords.orientation_for_manifest`` (invalid → null).
        """
        try:
            if not hasattr(image, "get_metadata"):
                return None
            meta = image.get_metadata()
            if meta is None:
                return None
            for tag in ("Exif.Image.Orientation", "Exif.Photo.Orientation"):
                raw = None
                try:
                    if hasattr(meta, "get_tag_long"):
                        try:
                            raw = meta.get_tag_long(tag)
                        except Exception:
                            raw = None
                    if raw is None and hasattr(meta, "get_tag_string"):
                        try:
                            s = meta.get_tag_string(tag)
                            if s is not None and str(s).strip():
                                raw = int(str(s).strip().split()[0])
                        except Exception:
                            raw = None
                except Exception:
                    raw = None
                if raw is None:
                    continue
                try:
                    return int(raw)
                except (TypeError, ValueError):
                    continue
            return None
        except Exception as e:
            print(f"[MCP] orient EXIF orientation failed: {e}")
            return None

    def _orient_layer_node(self, layer, parent_handle, image_id, depth, visited):
        """Recursive layer tree via _layer_children only (not flat iterator)."""
        try:
            lid = int(layer.get_id())
        except Exception:
            return None
        if depth > self._ORIENT_MAX_LAYER_DEPTH or lid in visited:
            return None
        visited.add(lid)
        handle = self._orient_item_handle(lid, image_id)
        kind = self._orient_classify_kind(layer)

        try:
            name = layer.get_name() or f"layer-{lid}"
        except Exception:
            name = f"layer-{lid}"
        try:
            visible = bool(layer.get_visible())
        except Exception:
            visible = True
        try:
            opacity = float(layer.get_opacity())
        except Exception:
            opacity = 100.0
        # Clamp 0..100 without host helper
        if opacity < 0.0:
            opacity = 0.0
        elif opacity > 100.0:
            opacity = 100.0
        try:
            blend_mode = str(layer.get_mode())
        except Exception:
            blend_mode = "unknown"
        # GIMP 3.x get_offsets() returns an object with offset_x/offset_y
        # (see drop-shadow path). Fall back to 2-tuple / wrapped object.
        ox, oy = 0, 0
        try:
            offsets = layer.get_offsets()
            if offsets is not None:
                if hasattr(offsets, "offset_x") or hasattr(offsets, "offset_y"):
                    ox = int(getattr(offsets, "offset_x", 0) or 0)
                    oy = int(getattr(offsets, "offset_y", 0) or 0)
                elif isinstance(offsets, (list, tuple)) and len(offsets) >= 2:
                    ox, oy = int(offsets[0]), int(offsets[1])
                elif (
                    isinstance(offsets, (list, tuple))
                    and len(offsets) == 1
                    and hasattr(offsets[0], "offset_x")
                ):
                    ox = int(offsets[0].offset_x)
                    oy = int(offsets[0].offset_y)
        except (TypeError, ValueError, AttributeError, RuntimeError):
            ox, oy = 0, 0
        try:
            lw = int(layer.get_width())
            lh = int(layer.get_height())
        except Exception:
            lw, lh = 0, 0
        try:
            has_alpha = bool(layer.has_alpha())
        except Exception:
            has_alpha = False

        # Additive 0009: tattoo (write-only identity) + protected flag
        tattoo_val = None
        try:
            if hasattr(layer, "get_tattoo"):
                tattoo_val = int(layer.get_tattoo())
        except Exception:
            tattoo_val = None
        protected_flag = False
        try:
            protected_flag = lid in (self._protected_item_ids.get(int(image_id)) or set())
        except Exception:
            protected_flag = False
        if not protected_flag:
            try:
                protected_flag = self._item_under_source_immutable_policy(layer)
            except Exception:
                protected_flag = False

        mask_info = {"present": False}
        try:
            mask = layer.get_mask() if hasattr(layer, "get_mask") else None
            if mask is not None:
                mask_info = {"present": True}
                try:
                    if hasattr(layer, "get_apply_mask"):
                        mask_info["apply"] = bool(layer.get_apply_mask())
                except Exception:
                    pass
                try:
                    if hasattr(layer, "get_show_mask"):
                        mask_info["show"] = bool(layer.get_show_mask())
                except Exception:
                    pass
        except Exception:
            pass

        children = []
        for child in self._layer_children(layer):
            node = self._orient_layer_node(child, handle, image_id, depth + 1, visited)
            if node is not None:
                children.append(node)

        node = {
            "handle": handle,
            "name": str(name),
            "kind": kind,
            "parent_handle": parent_handle,
            "visible": visible,
            "opacity": opacity,
            "blend_mode": blend_mode,
            "offset": {"x": ox, "y": oy},
            "size": {"width": lw, "height": lh},
            "has_alpha": has_alpha,
            "mask": mask_info,
            "filters": [],
            "children": children,
            "protected": bool(protected_flag),
        }
        if tattoo_val is not None:
            node["tattoo"] = tattoo_val
        return node

    def _orient_item_summaries(self, items, image_id):
        summaries = []
        for item in items or []:
            try:
                iid = int(item.get_id())
                name = str(item.get_name() or f"item-{iid}")
                try:
                    visible = bool(item.get_visible())
                except Exception:
                    visible = True
                summaries.append(
                    {
                        "handle": self._orient_item_handle(iid, image_id),
                        "name": name,
                        "visible": visible,
                    }
                )
            except Exception as ex:
                print(f"[MCP] orient item summary failed: {ex}")
        return summaries

    def _orient_image_entry(self, image, front_id, summary_only):
        image_id = int(image.get_id())
        try:
            name = image.get_name() or f"image-{image_id}"
        except Exception:
            name = f"image-{image_id}"
        try:
            width = int(image.get_width())
            height = int(image.get_height())
        except Exception:
            width, height = 1, 1
        if width < 1:
            width = 1
        if height < 1:
            height = 1
        try:
            base_type = self._base_type_to_string(image.get_base_type())
        except Exception:
            base_type = "RGB"
        try:
            precision = self._precision_to_string(image.get_precision())
        except Exception:
            precision = "unknown"
        try:
            dirty = bool(image.is_dirty())
        except Exception:
            dirty = False

        selected = front_id is not None and image_id == front_id
        entry = {
            "handle": self._orient_image_handle(image_id),
            "name": str(name),
            "source_path": self._orient_source_path(image),
            "width": width,
            "height": height,
            "base_type": base_type,
            "precision": str(precision),
            "dirty": dirty,
            "selected": bool(selected),
            "alpha_present": bool(self._preflight_has_alpha(image)),
            "color_profile": self._orient_color_profile(image),
            "metadata": self._orient_metadata_block(image, image_id),
            "selection": self._orient_selection(image),
            "active_layer_handles": [],
            "layers": [],
            "channels": [],
            "paths": [],
        }

        # Active / selected layers
        try:
            selected_layers = list(image.get_selected_layers() or [])
        except Exception:
            selected_layers = []
        for sl in selected_layers:
            try:
                entry["active_layer_handles"].append(
                    self._orient_item_handle(int(sl.get_id()), image_id)
                )
            except Exception:
                pass

        if summary_only:
            try:
                # Cheap count via guarded walk (no full node tree); same depth/visited
                # limits as _orient_layer_node so cyclic/deep graphs cannot hang.
                roots = list(image.get_layers() or [])
                count = 0
                visited_ids = set()
                stack = [(root, 0) for root in roots]  # (layer, depth)
                while stack:
                    layer, depth = stack.pop()
                    try:
                        lid = int(layer.get_id())
                    except Exception:
                        continue
                    if lid in visited_ids:
                        continue
                    if depth > self._ORIENT_MAX_LAYER_DEPTH:
                        continue
                    visited_ids.add(lid)
                    count += 1
                    if depth < self._ORIENT_MAX_LAYER_DEPTH:
                        for child in self._layer_children(layer):
                            stack.append((child, depth + 1))
                entry["layer_count"] = count
            except Exception:
                entry["layer_count"] = 0
            entry["layers"] = []
            return entry

        # Full recursive layer tree
        visited = set()
        try:
            roots = list(image.get_layers() or [])
        except Exception:
            roots = []
        for root in roots:
            node = self._orient_layer_node(root, None, image_id, 0, visited)
            if node is not None:
                entry["layers"].append(node)

        # Channels / paths as itemSummary
        try:
            entry["channels"] = self._orient_item_summaries(
                list(image.get_channels() or []), image_id
            )
        except Exception as e:
            print(f"[MCP] orient channels failed: {e}")
            entry["channels"] = []
        try:
            paths = list(image.get_paths() or []) if hasattr(image, "get_paths") else []
            entry["paths"] = self._orient_item_summaries(paths, image_id)
        except Exception as e:
            print(f"[MCP] orient paths failed: {e}")
            entry["paths"] = []

        return entry

    def _orient_gimp_env(self):
        """Best-effort gimp block for the raw dump (host may fill gaps)."""
        version = "unknown"
        try:
            if hasattr(Gimp, "version") and callable(Gimp.version):
                version = str(Gimp.version())
            elif hasattr(Gimp, "VERSION"):
                version = str(Gimp.VERSION)
            else:
                parts = []
                for attr in ("MAJOR_VERSION", "MINOR_VERSION", "MICRO_VERSION"):
                    if hasattr(Gimp, attr):
                        parts.append(str(getattr(Gimp, attr)))
                if parts:
                    version = ".".join(parts)
        except Exception:
            pass
        executable = "unknown"
        try:
            executable = sys.executable or "unknown"
        except Exception:
            pass
        config_directory = None
        try:
            if hasattr(Gimp, "directory") and callable(Gimp.directory):
                config_directory = str(Gimp.directory())
        except Exception:
            pass
        out = {
            "version": version,
            "api_version": "3.0",
            "os": platform.system() or "unknown",
            "executable": executable,
            "plugin_version": "0.1.0",
        }
        if config_directory:
            out["config_directory"] = config_directory
        return out

    def _orient_context(self, displays_open):
        """Paint context fg/bg as 0.0–1.0 floats (design scale)."""
        ctx = {"displays_open": bool(displays_open)}
        try:
            fg = Gimp.context_get_foreground()
            if hasattr(fg, "get_rgba"):
                rgba = fg.get_rgba()
                if rgba is not None:
                    ctx["foreground_rgba"] = [float(c) for c in list(rgba)[:4]]
        except Exception as e:
            print(f"[MCP] orient foreground failed: {e}")
        try:
            bg = Gimp.context_get_background()
            if hasattr(bg, "get_rgba"):
                rgba = bg.get_rgba()
                if rgba is not None:
                    ctx["background_rgba"] = [float(c) for c in list(rgba)[:4]]
        except Exception as e:
            print(f"[MCP] orient background failed: {e}")
        try:
            brush = Gimp.context_get_brush()
            if brush is not None and hasattr(brush, "get_name"):
                ctx["brush_name"] = str(brush.get_name())
        except Exception:
            pass
        try:
            ctx["opacity"] = float(Gimp.context_get_opacity())
        except Exception:
            pass
        try:
            ctx["paint_mode"] = str(Gimp.context_get_paint_mode())
        except Exception:
            pass
        return ctx

    def _orient_workspace(self, params):
        """Read-only raw workspace dump for host finalize_manifest (track 0006).

        Zero mutation: no undo groups, no Selection mutators, no export/dup/flatten,
        no displays_flush. Prunes generation map to open images only (no reseed).
        """
        try:
            params = params or {}
            image_index = params.get("image_index", None)
            summary_only = bool(params.get("summary_only", False))

            # Front display image id for selected (H2)
            front_id = None
            displays_open = False
            try:
                displays = Gimp.get_displays() or []
                displays_open = len(displays) > 0
                if displays:
                    try:
                        front_img = displays[0].get_image()
                        if front_img is not None:
                            front_id = int(front_img.get_id())
                    except Exception as e:
                        print(f"[MCP] orient front display failed: {e}")
                        front_id = None
            except Exception as e:
                print(f"[MCP] orient get_displays failed: {e}")
                displays = []
                displays_open = False
                front_id = None

            try:
                all_images = list(Gimp.get_images() or [])
            except Exception:
                all_images = []

            # F1: drop closed-image generation keys; never reseed them here
            self._sync_image_generations(all_images)
            # Durable Source_Immutable hydrate for orient protected flags
            for _img in all_images:
                try:
                    self._hydrate_protected_from_group(_img)
                except Exception:
                    pass

            explicit_index = image_index is not None
            if explicit_index:
                idx = int(image_index)
                if idx < 0 or idx >= len(all_images):
                    return {
                        "status": "error",
                        "error": (
                            f"image_index {idx} out of range (only {len(all_images)} images open)"
                        ),
                    }
                images_to_dump = [all_images[idx]]
                dump_indices = [idx]
            else:
                images_to_dump = all_images
                dump_indices = list(range(len(all_images)))

            image_entries = []
            warnings = []
            for dump_i, image in enumerate(images_to_dump):
                img_id = None
                try:
                    img_id = int(image.get_id())
                except Exception:
                    img_id = None
                try:
                    image_entries.append(self._orient_image_entry(image, front_id, summary_only))
                except Exception as img_err:
                    print(f"[MCP] orient image entry failed: {img_err}")
                    traceback.print_exc()
                    warn = {
                        "image_index": dump_indices[dump_i],
                        "error": str(img_err),
                    }
                    if img_id is not None:
                        warn["image_id"] = img_id
                    warnings.append(warn)

            # Fail closed: explicit image_index must succeed
            if explicit_index and warnings:
                return {
                    "status": "error",
                    "error": (
                        f"Failed to orient image_index {dump_indices[0]}: "
                        f"{warnings[0].get('error', 'unknown error')}"
                    ),
                    "warnings": warnings,
                }

            # Fail closed: open images existed but none dumped successfully
            if not image_entries and images_to_dump:
                return {
                    "status": "error",
                    "error": "Failed to orient all requested images",
                    "warnings": warnings,
                }

            raw = {
                "session": {
                    "session_id": self.session_id,
                    "epoch": int(self.session_epoch),
                    "started_at": self.session_started_at,
                },
                "gimp": self._orient_gimp_env(),
                "images": image_entries,
                "context": self._orient_context(displays_open),
            }
            if warnings:
                raw["warnings"] = warnings
            return {"status": "success", "results": raw}
        except Exception as e:
            print(f"Error in orient_workspace: {e!s}")
            return _sec.redact_error(e, message=f"Error in orient_workspace: {e!s}")

    def _base_type_to_string(self, base_type):
        """Convert GIMP base type enum to string."""
        try:
            base_type_map = {
                Gimp.ImageBaseType.RGB: "RGB",
                Gimp.ImageBaseType.GRAY: "Grayscale",
                Gimp.ImageBaseType.INDEXED: "Indexed",
            }
            return base_type_map.get(base_type, f"Unknown ({base_type})")
        except Exception:
            return str(base_type)

    def _precision_to_string(self, precision):
        """Convert GIMP precision enum to readable string."""
        try:
            precision_map = {
                100: "u8",  # Gimp.Precision.U8_LINEAR
                150: "u8-gamma",  # Gimp.Precision.U8_GAMMA
                200: "u16",  # Gimp.Precision.U16_LINEAR
                250: "u16-gamma",  # Gimp.Precision.U16_GAMMA
                300: "u32",  # Gimp.Precision.U32_LINEAR
                350: "u32-gamma",  # Gimp.Precision.U32_GAMMA
                500: "half",  # Gimp.Precision.HALF_LINEAR
                550: "half-gamma",  # Gimp.Precision.HALF_GAMMA
                600: "float",  # Gimp.Precision.FLOAT_LINEAR
                650: "float-gamma",  # Gimp.Precision.FLOAT_GAMMA
                700: "double",  # Gimp.Precision.DOUBLE_LINEAR
                750: "double-gamma",  # Gimp.Precision.DOUBLE_GAMMA
            }
            return precision_map.get(int(precision), f"precision-{precision}")
        except Exception:
            return str(precision)

    def _get_layer_type_string(self, layer):
        """Get layer type string with compatibility for different GIMP versions."""
        try:
            # Try different methods to get layer type
            if hasattr(layer, "get_type"):
                return str(layer.get_type())
            elif hasattr(layer, "get_image_type"):
                return str(layer.get_image_type())
            elif hasattr(layer, "type"):
                return str(layer.type)
            else:
                # Fallback - determine from layer properties
                if layer.has_alpha():
                    return "RGBA"
                else:
                    return "RGB"
        except Exception as e:
            print(f"Warning: Could not determine layer type: {e}")
            return "unknown"

    def _get_gimp_info(self):
        """Get comprehensive information about GIMP installation and environment."""
        try:
            print("Getting GIMP environment information...")

            gimp_info = {}

            # Basic GIMP version and build information
            try:
                version_info = {}

                # Try different methods to get version info
                try:
                    # Try the version() method if it exists
                    if hasattr(Gimp, "version"):
                        version_info["version_method"] = str(Gimp.version())
                except Exception as v_error:
                    version_info["version_method_error"] = str(v_error)

                # Try to get version from constants if they exist
                for attr in ["MAJOR_VERSION", "MINOR_VERSION", "MICRO_VERSION"]:
                    try:
                        if hasattr(Gimp, attr):
                            version_info[attr.lower()] = getattr(Gimp, attr)
                    except Exception as attr_error:
                        version_info[f"{attr.lower()}_error"] = str(attr_error)

                # Get available version-related attributes
                version_attrs = [attr for attr in dir(Gimp) if "version" in attr.lower()]
                if version_attrs:
                    version_info["available_version_attributes"] = version_attrs

                # Try to get version string from any available source
                version_string = "Unknown"
                try:
                    # Check if there's a version string constant
                    if hasattr(Gimp, "VERSION"):
                        version_string = str(Gimp.VERSION)
                    elif hasattr(Gimp, "version_string"):
                        version_string = str(Gimp.version_string())
                    elif hasattr(Gimp, "get_version"):
                        version_string = str(Gimp.get_version())
                except Exception:
                    pass

                version_info["detected_version"] = version_string
                version_info["gimp_module_type"] = str(type(Gimp))

                gimp_info["version"] = version_info

            except Exception as version_error:
                print(f"Error getting version info: {version_error}")
                gimp_info["version"] = {"error": str(version_error)}

            # Installation and directory information
            try:
                directories = {}

                # Safely try each directory method
                directory_methods = [
                    ("user_directory", "directory"),
                    ("system_data_directory", "data_directory"),
                    ("locale_directory", "locale_directory"),
                    ("plugin_directory", "plug_in_directory"),
                    ("sysconf_directory", "sysconf_directory"),
                ]

                for dir_name, method_name in directory_methods:
                    try:
                        if hasattr(Gimp, method_name):
                            method = getattr(Gimp, method_name)
                            if callable(method):
                                directories[dir_name] = str(method())
                            else:
                                directories[dir_name] = str(method)
                        else:
                            directories[f"{dir_name}_not_available"] = True
                    except Exception as method_error:
                        directories[f"{dir_name}_error"] = str(method_error)

                # List available directory-related methods
                dir_attrs = [attr for attr in dir(Gimp) if "dir" in attr.lower()]
                directories["available_directory_methods"] = dir_attrs

                gimp_info["directories"] = directories

            except Exception as dir_error:
                print(f"Error getting directory info: {dir_error}")
                gimp_info["directories"] = {"error": str(dir_error)}

            # Current session information
            try:
                images = Gimp.get_images()
                gimp_info["session"] = {
                    "num_open_images": len(images),
                    "has_open_images": len(images) > 0,
                    "open_image_files": [],
                }

                # Get file information for open images
                for i, image in enumerate(images):
                    try:
                        image_file = image.get_file()
                        file_info = {
                            "index": i,
                            "width": image.get_width(),
                            "height": image.get_height(),
                            "base_type": self._base_type_to_string(image.get_base_type()),
                            "is_dirty": image.is_dirty() if hasattr(image, "is_dirty") else None,
                        }

                        if image_file:
                            file_info.update(
                                {
                                    "path": image_file.get_path()
                                    if hasattr(image_file, "get_path")
                                    else None,
                                    "basename": image_file.get_basename()
                                    if hasattr(image_file, "get_basename")
                                    else None,
                                }
                            )
                        else:
                            file_info["path"] = "Untitled"

                        gimp_info["session"]["open_image_files"].append(file_info)
                    except Exception as image_error:
                        print(f"Error getting image {i} info: {image_error}")
                        gimp_info["session"]["open_image_files"].append(
                            {"index": i, "error": str(image_error)}
                        )

            except Exception as session_error:
                print(f"Error getting session info: {session_error}")
                gimp_info["session"] = {"error": str(session_error)}

            # PDB (Procedure Database) information
            try:
                pdb = Gimp.get_pdb()
                pdb_info = {"available": pdb is not None, "type": str(type(pdb)) if pdb else None}

                # Try to get some example procedures
                if pdb:
                    sample_procedures = []
                    try:
                        # Test some common procedures
                        test_procs = [
                            "file-png-export",
                            "gimp-file-save",
                            "gimp-image-new",
                            "python-fu-console",
                        ]
                        for proc_name in test_procs:
                            try:
                                proc = pdb.lookup_procedure(proc_name)
                                sample_procedures.append(
                                    {
                                        "name": proc_name,
                                        "available": proc is not None,
                                        "type": str(type(proc)) if proc else None,
                                    }
                                )
                            except Exception:
                                sample_procedures.append(
                                    {
                                        "name": proc_name,
                                        "available": False,
                                        "error": "lookup_failed",
                                    }
                                )
                    except Exception as proc_error:
                        print(f"Error testing procedures: {proc_error}")

                    pdb_info["sample_procedures"] = sample_procedures

                gimp_info["pdb"] = pdb_info

            except Exception as pdb_error:
                print(f"Error getting PDB info: {pdb_error}")
                gimp_info["pdb"] = {"error": str(pdb_error)}

            # Context and environment information
            try:
                context_info = {}

                # Try to get current context information
                try:
                    fg_color = Gimp.context_get_foreground()
                    context_info["foreground_color"] = str(fg_color) if fg_color else None
                except Exception:
                    context_info["foreground_color"] = "unavailable"

                try:
                    bg_color = Gimp.context_get_background()
                    context_info["background_color"] = str(bg_color) if bg_color else None
                except Exception:
                    context_info["background_color"] = "unavailable"

                try:
                    brush_size = Gimp.context_get_brush_size()
                    context_info["brush_size"] = brush_size if brush_size else None
                except Exception:
                    context_info["brush_size"] = "unavailable"

                gimp_info["context"] = context_info

            except Exception as context_error:
                print(f"Error getting context info: {context_error}")
                gimp_info["context"] = {"error": str(context_error)}

            # Capabilities and features
            try:
                capabilities = {
                    "has_python_console": True,  # We're running Python
                    "mcp_server_running": True,  # We're responding to MCP requests
                    "supports_image_export": True,  # We have the bitmap export function
                    "supports_metadata_export": True,  # We have the metadata function
                    "supports_gimp_info": True,  # We have the gimp info function
                    "api_version": "3.0+",
                    "python_version": sys.version,
                    "available_modules": [],
                    "gimp_module_attributes": len(dir(Gimp)),
                    "gimp_methods": [
                        attr for attr in dir(Gimp) if callable(getattr(Gimp, attr, None))
                    ][:20],  # First 20 methods
                }

                # Test for available Python modules
                test_modules = [
                    "gi.repository.Gimp",
                    "gi.repository.Gegl",
                    "gi.repository.Gio",
                    "json",
                    "base64",
                    "tempfile",
                ]
                for module_name in test_modules:
                    try:
                        if module_name == "gi.repository.Gimp":
                            # Already imported
                            capabilities["available_modules"].append(
                                {"name": module_name, "available": True}
                            )
                        elif module_name == "gi.repository.Gegl":
                            from gi.repository import Gegl  # noqa: F401

                            capabilities["available_modules"].append(
                                {"name": module_name, "available": True}
                            )
                        elif module_name == "gi.repository.Gio":
                            from gi.repository import Gio  # noqa: F401

                            capabilities["available_modules"].append(
                                {"name": module_name, "available": True}
                            )
                        else:
                            __import__(module_name)
                            capabilities["available_modules"].append(
                                {"name": module_name, "available": True}
                            )
                    except ImportError:
                        capabilities["available_modules"].append(
                            {"name": module_name, "available": False}
                        )
                    except Exception as mod_error:
                        capabilities["available_modules"].append(
                            {"name": module_name, "available": False, "error": str(mod_error)}
                        )

                gimp_info["capabilities"] = capabilities

            except Exception as cap_error:
                print(f"Error getting capabilities: {cap_error}")
                gimp_info["capabilities"] = {"error": str(cap_error)}

            # System and platform information
            try:
                system_info = {
                    "platform": platform.platform(),
                    "system": platform.system(),
                    "machine": platform.machine(),
                    "python_version": platform.python_version(),
                    "environment_vars": {
                        "HOME": os.environ.get("HOME"),
                        "USER": os.environ.get("USER"),
                        "GIMP_PLUG_IN_DIR": os.environ.get("GIMP_PLUG_IN_DIR"),
                        "GIMP_DATA_DIR": os.environ.get("GIMP_DATA_DIR"),
                    },
                }

                gimp_info["system"] = system_info

            except Exception as sys_error:
                print(f"Error getting system info: {sys_error}")
                gimp_info["system"] = {"error": str(sys_error)}

            return {"status": "success", "results": gimp_info}

        except Exception as e:
            print(f"Error getting GIMP info: {e!s}")
            return _sec.redact_error(e, message=f"Error getting GIMP info: {e!s}")

    def _get_context_state(self):
        """Get current GIMP context state (colors, brush, tool settings)."""
        try:
            print("Getting GIMP context state...")

            context_state = {}

            # Get foreground and background colors
            try:
                fg_color = Gimp.context_get_foreground()
                bg_color = Gimp.context_get_background()

                # Convert colors to RGB values
                context_state["foreground_color"] = {
                    "color_object": str(fg_color),
                    "description": "Current foreground color",
                }
                context_state["background_color"] = {
                    "color_object": str(bg_color),
                    "description": "Current background color",
                }

                # Try to get RGB values if possible
                try:
                    if hasattr(fg_color, "get_rgba"):
                        rgba = fg_color.get_rgba()
                        context_state["foreground_color"]["rgba"] = list(rgba) if rgba else None
                except Exception as color_error:
                    context_state["foreground_color"]["rgba_error"] = str(color_error)

                try:
                    if hasattr(bg_color, "get_rgba"):
                        rgba = bg_color.get_rgba()
                        context_state["background_color"]["rgba"] = list(rgba) if rgba else None
                except Exception as color_error:
                    context_state["background_color"]["rgba_error"] = str(color_error)

            except Exception as color_err:
                context_state["colors_error"] = str(color_err)

            # Get brush information
            try:
                brush = Gimp.context_get_brush()
                if brush:
                    context_state["brush"] = {
                        "name": brush.get_name() if hasattr(brush, "get_name") else str(brush),
                        "description": "Current brush",
                    }
            except Exception as brush_err:
                context_state["brush_error"] = str(brush_err)

            # Get opacity
            try:
                opacity = Gimp.context_get_opacity()
                context_state["opacity"] = {
                    "value": opacity,  # Already in percentage (0-100)
                    "description": "Current opacity percentage (0-100)",
                }
            except Exception as opacity_err:
                context_state["opacity_error"] = str(opacity_err)

            # Get paint mode
            try:
                paint_mode = Gimp.context_get_paint_mode()
                context_state["paint_mode"] = {
                    "value": str(paint_mode),
                    "description": "Current paint/blend mode",
                }
            except Exception as mode_err:
                context_state["paint_mode_error"] = str(mode_err)

            # Get feather setting (if available)
            try:
                feather = Gimp.context_get_feather()
                feather_radius = Gimp.context_get_feather_radius()
                context_state["feather"] = {
                    "enabled": feather,
                    "radius": feather_radius,
                    "description": "Selection feathering state",
                }
            except Exception:
                context_state["feather_note"] = "Feather settings not available in context"

            # Get antialias setting
            try:
                antialias = Gimp.context_get_antialias()
                context_state["antialias"] = {
                    "enabled": antialias,
                    "description": "Antialiasing state for selections",
                }
            except Exception:
                context_state["antialias_note"] = "Antialias setting not available"

            return {"status": "success", "results": context_state}

        except Exception as e:
            print(f"Error getting context state: {e!s}")
            return _sec.redact_error(e, message=f"Error getting context state: {e!s}")

    def _restart_server(self):
        """Gracefully restart the MCP socket server in-place."""
        try:
            print("Restarting MCP server socket...")
            # Close existing socket to force reconnect on next client call
            if self.socket:
                try:
                    self.socket.close()
                except Exception:
                    pass
                self.socket = None

            # Re-bind a fresh socket (AF_INET + validated host)
            import time

            try:
                self.host = _sec.assert_bind_host(self.host)
            except _sec.SecurityError as e:
                print(f"[MCP] SECURITY: {e}; forcing 127.0.0.1")
                self.host = _sec.DEFAULT_HOST

            time.sleep(0.3)
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.settimeout(1.0)
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)
            print(f"MCP server restarted on {self.host}:{self.port}")
            _sec.write_audit_event(
                {
                    "event": "server_restart",
                    "host": self.host,
                    "port": self.port,
                },
                self.audit_path,
            )
            return {
                "status": "success",
                "results": {"restarted": True, "host": self.host, "port": self.port},
            }
        except Exception as e:
            return _sec.redact_error(e, message=f"Restart failed: {e!s}")

    def _selection_none_or_fail(self, image, context_msg):
        """Clear selection on *image*; fail closed on exception or explicit False.

        GIMP documents ``Selection.none`` as returning gboolean (TRUE on success).
        Some GI bindings may return None for void-like wrappers — treat None as
        success; only explicit False is a failure so merge is not selection-clipped.
        """
        try:
            ok = Gimp.Selection.none(image)
        except (AttributeError, RuntimeError) as e:
            raise RuntimeError(
                f"{context_msg} (cannot safely composite without clearing selection): {e}"
            ) from e
        if ok is False:
            raise RuntimeError(
                f"{context_msg} (Selection.none returned False; "
                "cannot safely composite with inherited selection)"
            )

    def _new_canvas(self, params):
        """Create a new blank canvas and open it in a GIMP display window."""
        try:
            width = int(params.get("width", 1024))
            height = int(params.get("height", 1024))
            name = str(params.get("name", "Untitled"))
            color_mode = str(params.get("color_mode", "RGB")).upper()
            fill = str(params.get("fill", "white"))
            resolution = int(params.get("resolution", 72))

            mode_map = {
                "RGB": Gimp.ImageBaseType.RGB,
                "RGBA": Gimp.ImageBaseType.RGB,
                "GRAY": Gimp.ImageBaseType.GRAY,
                "GRAYA": Gimp.ImageBaseType.GRAY,
            }
            layer_type_map = {
                "RGB": Gimp.ImageType.RGB_IMAGE,
                "RGBA": Gimp.ImageType.RGBA_IMAGE,
                "GRAY": Gimp.ImageType.GRAY_IMAGE,
                "GRAYA": Gimp.ImageType.GRAYA_IMAGE,
            }
            base_type = mode_map.get(color_mode, Gimp.ImageBaseType.RGB)
            layer_type = layer_type_map.get(color_mode, Gimp.ImageType.RGB_IMAGE)

            from gi.repository import Gegl

            image = Gimp.Image.new(width, height, base_type)
            image.set_resolution(resolution, resolution)

            layer = Gimp.Layer.new(
                image, name, width, height, layer_type, 100, Gimp.LayerMode.NORMAL
            )
            image.insert_layer(layer, None, 0)

            if fill.lower() == "transparent":
                layer.add_alpha()
                Gimp.Drawable.edit_fill(layer, Gimp.FillType.TRANSPARENT)
            else:
                bg_color = Gegl.Color.new(fill)
                Gimp.context_set_background(bg_color)
                Gimp.Drawable.edit_fill(layer, Gimp.FillType.BACKGROUND)

            Gimp.Display.new(image)
            Gimp.displays_flush()

            image_id = int(image.get_id())
            gen = self._seed_image_generation(image_id, 1)
            print(f"New canvas created: {width}x{height} {color_mode} fill={fill}")
            return {
                "status": "success",
                "results": {
                    "image_id": image_id,
                    "width": width,
                    "height": height,
                    "color_mode": color_mode,
                    "fill": fill,
                    "resolution": resolution,
                    "display_opened": True,
                    "generation": gen,
                    "handle": self._emit_image_handle(image),
                },
            }
        except Exception as e:
            return {
                "status": "error",
                "error": f"new_canvas failed: {e!s}",
                "traceback": traceback.format_exc(),
            }

    # =========================================================================
    # SHARED HELPERS
    # =========================================================================

    # ── Stable handle registry (track 0007) ──────────────────────────────────

    def _seed_floor(self, image_id):
        """Minimum generation for first-seen image_id (respects retired tombstone)."""
        return _handles.next_seed_generation(self._retired_generations.get(int(image_id)))

    def _image_generation(self, image_id):
        """Return live structural generation for image_id; seed via retired floor if new."""
        iid = int(image_id)
        if iid not in self._image_generations:
            self._image_generations[iid] = self._seed_floor(iid)
        return self._image_generations[iid]

    def _seed_image_generation(self, image_id, gen=None):
        """Register image_id at generation gen (default/max with retired floor)."""
        iid = int(image_id)
        floor = self._seed_floor(iid)
        if gen is None:
            gen = floor
        else:
            gen = max(int(gen), floor)
        self._image_generations[iid] = int(gen)
        return int(gen)

    def _bump_image_generation(self, image_id):
        """Increment structural generation after successful live mutation; return new."""
        iid = int(image_id)
        cur = self._image_generations.get(iid)
        if cur is None:
            cur = self._seed_floor(iid)
        new = int(cur) + 1
        self._image_generations[iid] = new
        return new

    def _drop_image_generation(self, image_id):
        """Drop registry entry when image is closed; keep retired floor for recycle."""
        iid = int(image_id)
        if iid in self._image_generations:
            prev = int(self._image_generations.pop(iid))
            floor = int(self._retired_generations.get(iid, 0) or 0)
            self._retired_generations[iid] = max(floor, prev)
        # Session orientation flag must not survive close / id recycle (M1).
        self._orientation_normalized.pop(iid, None)
        # Protected Source_Immutable set must not survive close / id recycle (0009 M1).
        self._protected_item_ids.pop(iid, None)
        self._working_item_ids.pop(iid, None)

    def _sync_image_generations(self, open_images=None):
        """Prune generation map to currently open images. Does not reseed closed ids.

        Call at orient start (and anytime the open set is known). Keys for
        closed/invalid image ids are tombstoned into ``_retired_generations`` then
        dropped; open ids keep their counters. Also prunes orientation flags (M1).
        """
        if open_images is None:
            try:
                open_images = list(Gimp.get_images() or [])
            except Exception:
                open_images = []
        open_ids = set()
        for img in open_images:
            try:
                open_ids.add(int(img.get_id()))
            except Exception:
                continue
        dropped = _handles.prune_image_generations(
            self._image_generations, open_ids, retired=self._retired_generations
        )
        # Drop orientation flags for pruned / non-open ids (same open set).
        for key in list(self._orientation_normalized.keys()):
            try:
                iid = int(key)
            except (TypeError, ValueError):
                self._orientation_normalized.pop(key, None)
                continue
            if iid not in open_ids:
                self._orientation_normalized.pop(key, None)
        # Drop protected/working item sets for pruned / non-open ids (0009 M1).
        for key in list(self._protected_item_ids.keys()):
            try:
                iid = int(key)
            except (TypeError, ValueError):
                self._protected_item_ids.pop(key, None)
                continue
            if iid not in open_ids:
                self._protected_item_ids.pop(key, None)
        for key in list(self._working_item_ids.keys()):
            try:
                iid = int(key)
            except (TypeError, ValueError):
                self._working_item_ids.pop(key, None)
                continue
            if iid not in open_ids:
                self._working_item_ids.pop(key, None)
        return dropped

    def _pixel_orientation_normalized(self, image_id, tag=None):
        """Honesty (M2): session flag OR tag identity (null/1)."""
        session_flag = False
        if image_id is not None:
            try:
                session_flag = bool(self._orientation_normalized.get(int(image_id)))
            except (TypeError, ValueError):
                session_flag = False
        return session_flag or _coords.orientation_is_identity(tag)

    def _normalized_basis(self, image_id, tag=None):
        """Optional basis label for orient honesty."""
        if image_id is not None:
            try:
                if bool(self._orientation_normalized.get(int(image_id))):
                    return "session_flag"
            except (TypeError, ValueError):
                pass
        if _coords.orientation_is_identity(tag):
            return "tag_identity"
        return None

    def _orient_metadata_block(self, image, image_id):
        """Build image.metadata dict with EXIF + honest normalized flag."""
        tag = self._orient_exif_orientation(image)
        normalized = self._pixel_orientation_normalized(image_id, tag)
        block = {
            # Schema: 1..8 or null (invalid present tags → null)
            "exif_orientation_original": _coords.orientation_for_manifest(tag),
            "pixel_orientation_normalized": bool(normalized),
        }
        basis = self._normalized_basis(image_id, tag)
        if basis is not None and normalized:
            block["normalized_basis"] = basis
        return block

    def _emit_image_handle_id(self, image_id):
        gen = self._image_generation(image_id)
        return _handles.image_handle(
            int(image_id),
            session_epoch=int(self.session_epoch),
            generation=int(gen),
        )

    def _emit_image_handle(self, image):
        return self._emit_image_handle_id(int(image.get_id()))

    def _emit_item_handle_ids(self, item_id, image_id):
        gen = self._image_generation(image_id)
        return _handles.item_handle(
            int(item_id),
            image_id=int(image_id),
            session_epoch=int(self.session_epoch),
            generation=int(gen),
        )

    def _emit_item_handle(self, item, image_id):
        return self._emit_item_handle_ids(int(item.get_id()), int(image_id))

    def _handle_error_response(self, exc):
        """Map HandleError / SecurityError to structured make_error dict."""
        if isinstance(exc, _handles.HandleError):
            return _sec.make_error(exc.code, exc.message)
        if isinstance(exc, _sec.SecurityError):
            return _sec.make_error(exc.code, exc.message)
        raise exc

    def _validate_request_handle(self, handle, *, kind="image", expected_image_id=None):
        """Validate handle against live GIMP ids + registry generation.

        Returns validated dict or raises HandleError.
        """
        if kind == "image":
            image_id = None
            if isinstance(handle, dict) and "image_id" in handle:
                try:
                    image_id = int(handle["image_id"])
                except (TypeError, ValueError):
                    image_id = None
            id_valid = False
            if image_id is not None:
                try:
                    id_valid = bool(Gimp.Image.id_is_valid(image_id))
                except Exception:
                    id_valid = False
            if image_id is None:
                live_gen = 0
            elif id_valid:
                live_gen = self._image_generation(image_id)
            else:
                # Do not seed closed images; still respect retired floor for STALE checks
                live_gen = self._image_generations.get(image_id)
                if live_gen is None:
                    live_gen = self._seed_floor(image_id)
            return _handles.require_image_handle(
                handle,
                live_epoch=int(self.session_epoch),
                live_generation=int(live_gen),
                id_valid=id_valid,
            )

        # item
        item_id = None
        claimed_image_id = None
        if isinstance(handle, dict):
            try:
                if "item_id" in handle:
                    item_id = int(handle["item_id"])
            except (TypeError, ValueError):
                item_id = None
            try:
                if "image_id" in handle:
                    claimed_image_id = int(handle["image_id"])
            except (TypeError, ValueError):
                claimed_image_id = None

        id_valid = False
        item = None
        if item_id is not None:
            try:
                if hasattr(Gimp.Item, "id_is_valid"):
                    id_valid = bool(Gimp.Item.id_is_valid(item_id))
                else:
                    id_valid = True  # fall through to get_by_id
                if id_valid:
                    item = Gimp.Item.get_by_id(item_id)
                    if item is None:
                        id_valid = False
            except Exception:
                id_valid = False
                item = None

        belongs = None
        live_image_id = claimed_image_id
        if expected_image_id is not None:
            live_image_id = int(expected_image_id)
        if id_valid and item is not None and live_image_id is not None:
            try:
                img = item.get_image()
                belongs = img is not None and int(img.get_id()) == int(live_image_id)
            except Exception:
                belongs = False

        live_gen = 0
        if live_image_id is not None:
            if id_valid:
                live_gen = self._image_generation(live_image_id)
            else:
                live_gen = self._image_generations.get(live_image_id)
                if live_gen is None:
                    live_gen = self._seed_floor(live_image_id)

        return _handles.require_item_handle(
            handle,
            live_epoch=int(self.session_epoch),
            live_generation=int(live_gen),
            id_valid=id_valid,
            expected_image_id=int(expected_image_id) if expected_image_id is not None else None,
            item_belongs_to_image=belongs,
        )

    def _get_image(self, image_index):
        """Return the image at image_index from Gimp.get_images(), raise if none open."""
        images = Gimp.get_images()
        if not images:
            raise RuntimeError("No images are currently open in GIMP")
        if image_index < 0:
            raise RuntimeError(f"image_index {image_index} is negative")
        if image_index >= len(images):
            raise RuntimeError(
                f"image_index {image_index} out of range (only {len(images)} images open)"
            )
        return images[image_index]

    def _resolve_image_from_params(self, params):
        """Resolve image from handle (preferred) or image_index (default 0).

        Returns ``(image, image_id)``. Handle path uses the stable-handle registry
        (STALE_HANDLE / FOREIGN_SESSION / …). Used by 0010 handle-first tools.
        """
        params = params or {}
        handle = params.get("handle")
        if handle is not None:
            validated = self._validate_request_handle(handle, kind="image")
            image_id = int(validated["image_id"])
            image = self._get_image_by_id(image_id)
            return image, image_id
        image_index = int(params.get("image_index", 0))
        image = self._get_image(image_index)
        return image, int(image.get_id())

    def _get_image_by_id(self, image_id):
        """Resolve image by GIMP id; raise RuntimeError with HANDLE_NOT_FOUND semantics.

        Does not change legacy ``_get_image(index)``. Drops registry entry when id
        is invalid (no reseed of closed images).
        """
        iid = int(image_id)
        try:
            valid = bool(Gimp.Image.id_is_valid(iid))
        except Exception:
            valid = False
        if not valid:
            self._drop_image_generation(iid)
            raise RuntimeError(f"HANDLE_NOT_FOUND: image_id {iid} is not valid")
        try:
            image = Gimp.Image.get_by_id(iid)
        except Exception as e:
            self._drop_image_generation(iid)
            raise RuntimeError(f"HANDLE_NOT_FOUND: image_id {iid}: {e}") from e
        if image is None:
            self._drop_image_generation(iid)
            raise RuntimeError(f"HANDLE_NOT_FOUND: image_id {iid} not found")
        return image

    def _resolve_layer(self, image, layer_name, layer_index, layer_id=None, item_id=None):
        """Resolve a layer by id (preferred), root name, index, or active layer.

        Name match is **root-only** (no recursive DFS). Nested layers require
        ``layer_id`` / ``item_id``.
        """
        rid = layer_id if layer_id is not None else item_id
        if rid is not None:
            rid = int(rid)
            item = None
            try:
                if hasattr(Gimp, "Layer") and hasattr(Gimp.Layer, "get_by_id"):
                    item = Gimp.Layer.get_by_id(rid)
                if item is None and hasattr(Gimp.Item, "get_by_id"):
                    item = Gimp.Item.get_by_id(rid)
            except Exception as e:
                raise RuntimeError(f"HANDLE_NOT_FOUND: layer/item id {rid}: {e}") from e
            if item is None:
                raise RuntimeError(f"HANDLE_NOT_FOUND: layer/item id {rid} not found")
            try:
                img = item.get_image()
                if img is None or int(img.get_id()) != int(image.get_id()):
                    raise RuntimeError(
                        f"HANDLE_NOT_FOUND: item {rid} does not belong to image {image.get_id()}"
                    )
            except RuntimeError:
                raise
            except Exception as e:
                raise RuntimeError(
                    f"HANDLE_NOT_FOUND: cannot verify membership for item {rid}: {e}"
                ) from e
            return item
        if layer_name is not None:
            layers = image.get_layers()
            for layer in layers:
                if layer.get_name() == layer_name:
                    return layer
            raise RuntimeError(f"Layer '{layer_name}' not found")
        if layer_index is not None:
            layers = image.get_layers()
            if layer_index >= len(layers):
                raise RuntimeError(f"layer_index {layer_index} out of range")
            return layers[layer_index]
        layer = (image.get_selected_layers() or image.get_layers() or [None])[0]
        if layer is None:
            layers = image.get_layers()
            if not layers:
                raise RuntimeError("No layers in image")
            return layers[0]
        return layer

    def _find_source_immutable_group(self, image):
        """Return parasite-marked Source_Immutable group or None (no create)."""
        group_name = _policy.SOURCE_IMMUTABLE_GROUP_NAME
        for layer in image.get_layers() or []:
            try:
                if layer.get_name() != group_name:
                    continue
            except Exception:
                continue
            if not self._item_is_group(layer):
                continue
            if self._item_has_policy_parasite(layer):
                return layer
        return None

    def _hydrate_protected_from_group(self, image, image_id=None):
        """Populate session protected set from marked-group descendants.

        Durable across plugin restart / checkpoint_restore (Codex final P1).
        Returns the set of hydrated item_ids (may be empty).
        """
        try:
            iid = int(image_id if image_id is not None else image.get_id())
        except Exception:
            return set()
        group = self._find_source_immutable_group(image)
        if group is None:
            return set()
        found: set[int] = set()

        def walk(layer, depth, visited):
            if depth > 32:
                return
            try:
                lid = int(layer.get_id())
            except Exception:
                return
            if lid in visited:
                return
            visited.add(lid)
            if not self._item_is_group(layer):
                found.add(lid)
            for child in self._layer_children(layer):
                walk(child, depth + 1, visited)

        visited: set = set()
        for child in self._layer_children(group):
            walk(child, 0, visited)
        if found:
            self._protected_item_ids.setdefault(iid, set()).update(found)
        return found

    def _item_under_source_immutable_policy(self, item):
        """True if item is a descendant of a parasite-marked Source_Immutable group."""
        try:
            img = item.get_image()
        except Exception:
            return False
        if img is None:
            return False
        group = self._find_source_immutable_group(img)
        if group is None:
            return False
        return self._layer_under_policy_group(item, group)

    def _assert_mutable(self, item, *, allow_source_mutation=False):
        """Raise POLICY_DENIED if item is Source_Immutable protected.

        Checks:
        1. Session ``_protected_item_ids`` (fast path after ensure)
        2. Durable ancestry under parasite-marked Source_Immutable group
           (survives restart / checkpoint_restore — Codex final P1)

        Stale pre-protect layer handles still hit this assert by **item_id**.
        """
        if allow_source_mutation:
            return
        try:
            item_id = int(item.get_id())
        except Exception as e:
            raise RuntimeError(f"cannot resolve item id for mutability check: {e}") from e
        try:
            img = item.get_image()
            image_id = int(img.get_id()) if img is not None else None
        except Exception:
            image_id = None
            img = None
        if image_id is None:
            return
        protected = self._protected_item_ids.get(int(image_id)) or set()
        if item_id in protected:
            raise _sec.SecurityError(
                _sec.CODE_POLICY_DENIED,
                f"item_id {item_id} is Source_Immutable protected; "
                "mutate the working copy or pass allow_source_mutation=true",
            )
        # Durable path when session set empty (restore/restart)
        if self._item_under_source_immutable_policy(item):
            # Hydrate session set so subsequent checks are O(1)
            if img is not None:
                self._hydrate_protected_from_group(img, image_id)
            raise _sec.SecurityError(
                _sec.CODE_POLICY_DENIED,
                f"item_id {item_id} is under Source_Immutable (durable policy); "
                "mutate the working copy or pass allow_source_mutation=true",
            )

    def _resolve_mutable_layer(
        self,
        image,
        layer_name,
        layer_index,
        layer_id=None,
        item_id=None,
        *,
        allow_source_mutation=False,
    ):
        """Resolve a layer then assert it is mutable under Source_Immutable policy."""
        layer = self._resolve_layer(
            image, layer_name, layer_index, layer_id=layer_id, item_id=item_id
        )
        self._assert_mutable(layer, allow_source_mutation=allow_source_mutation)
        return layer

    def _allow_source_mutation_from_params(self, params):
        """Coerce allow_source_mutation without bool(\"false\")==True (0009 Codex P1)."""
        return _exp.coerce_bool((params or {}).get("allow_source_mutation", False), default=False)

    def _require_confirm_destructive(self, params, action):
        """Require confirm_destructive=true for live flatten/merge paths.

        Uses :func:`gimp_mcp_export.coerce_bool` so stringly ``\"false\"`` / ``\"0\"``
        cannot satisfy the gate (authenticated TCP JSON safety).
        """
        if not _exp.coerce_bool((params or {}).get("confirm_destructive", False), default=False):
            raise _sec.SecurityError(
                _sec.CODE_CONFIRM_REQUIRED,
                f"{action} requires confirm_destructive=true (destroys the live layer stack)",
            )

    def _layer_id_from_params(self, params):
        """Extract optional layer_id or item_id from params."""
        lid = params.get("layer_id", None)
        if lid is None:
            lid = params.get("item_id", None)
        if lid is None:
            return None
        return int(lid)

    def _image_has_display(self, image_id):
        """True if any existing display shows this image (never creates displays)."""
        try:
            for display in Gimp.get_displays() or []:
                try:
                    img = display.get_image()
                    if img is not None and int(img.get_id()) == int(image_id):
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        return False

    def _select_image(self, params):
        """Validate image handle and report display presence (no Display.new).

        ``selected: true`` means the handle is bound for agent targeting — not
        that a new window was created. ``display`` is true only if an existing
        display already shows the image. GIMP has no reliable public focus API
        without Display.new; we never invent a window here.
        """
        try:
            handle = params.get("handle")
            validated = self._validate_request_handle(handle, kind="image")
            image_id = int(validated["image_id"])
            image = self._get_image_by_id(image_id)
            gen = self._image_generation(image_id)
            has_display = self._image_has_display(image_id)
            return {
                "status": "success",
                "results": {
                    "handle": self._emit_image_handle(image),
                    "image_id": image_id,
                    "generation": gen,
                    "selected": True,
                    "display": bool(has_display),
                },
            }
        except _handles.HandleError as e:
            return self._handle_error_response(e)
        except RuntimeError as e:
            msg = str(e)
            if msg.startswith("HANDLE_NOT_FOUND"):
                return _sec.make_error(_sec.CODE_HANDLE_NOT_FOUND, msg)
            return {"status": "error", "error": msg, "traceback": traceback.format_exc()}
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _gimp_bool_or_fail(self, ok, action):
        """Fail-closed on explicit gboolean False; None (GI void-like) is success.

        Matches ``_selection_none_or_fail`` / GIMP 3.x gboolean docs: only
        ``False`` is a hard failure. Raises ``RuntimeError`` on failure.
        """
        if ok is False:
            raise RuntimeError(f"{action} returned False")

    def _apply_orientation_ops(self, image, ops):
        """Apply ordered EXIF bake ops via direct image.rotate/flip (never policy_rotate).

        Checks gboolean returns (explicit False → RuntimeError) so mid-sequence
        failure does not continue to tag rewrite + gen bump.
        """
        rot_map = {
            "rot90": Gimp.RotationType.DEGREES90,
            "rot180": Gimp.RotationType.DEGREES180,
            "rot270": Gimp.RotationType.DEGREES270,
        }
        for op in ops:
            if op == "flip_h":
                ok = image.flip(Gimp.OrientationType.HORIZONTAL)
                self._gimp_bool_or_fail(ok, "image.flip(HORIZONTAL)")
            elif op == "flip_v":
                ok = image.flip(Gimp.OrientationType.VERTICAL)
                self._gimp_bool_or_fail(ok, "image.flip(VERTICAL)")
            elif op in rot_map:
                ok = image.rotate(rot_map[op])
                self._gimp_bool_or_fail(ok, f"image.rotate({op})")
            else:
                raise RuntimeError(f"unknown orientation op: {op!r}")

    def _set_orientation_tags_to_1(self, image):
        """Ensure metadata exists; set both Orientation tags to 1; return set_metadata ok.

        Returns (ok: bool, error_message: str|None). Does not bump generation.
        Explicit ``False`` from set_metadata fails; ``None`` treated as success
        (GI void-like), consistent with Selection.none policy.
        """
        meta = None
        try:
            if hasattr(image, "get_metadata"):
                meta = image.get_metadata()
        except Exception:
            meta = None
        if meta is None:
            try:
                if hasattr(Gimp, "Metadata") and hasattr(Gimp.Metadata, "new"):
                    meta = Gimp.Metadata.new()
                else:
                    return False, "Gimp.Metadata.new() unavailable"
            except Exception as e:
                return False, f"Metadata.new failed: {e}"
        try:
            for tag in ("Exif.Image.Orientation", "Exif.Photo.Orientation"):
                if hasattr(meta, "set_tag_long"):
                    meta.set_tag_long(tag, 1)
                elif hasattr(meta, "set_tag_string"):
                    meta.set_tag_string(tag, "1")
                else:
                    return False, "metadata has no set_tag_long/set_tag_string"
        except Exception as e:
            return False, f"set orientation tag failed: {e}"
        try:
            if not hasattr(image, "set_metadata"):
                return False, "image.set_metadata unavailable"
            ok = image.set_metadata(meta)
            if ok is False:
                return False, "image.set_metadata returned false"
            return True, None
        except Exception as e:
            return False, f"image.set_metadata failed: {e}"

    def _normalize_image_orientation(self, params):
        """Normalize EXIF orientation tags (and optionally bake pixels).

        Modes (H1):
        - assume_pixels_upright (default): set both tags to 1; no pixel ops.
          Safe after normal GIMP open where policy_rotate may already have
          uprighted pixels while leaving the tag as 6/8.
        - trust_tag: apply ordered ORIENTATION_OPS for tags 2–8, then set tags
          to 1. Opt-in only when pixels still match tag encoding.

        Never calls Image.policy_rotate or reuses _rotate_image/_flip_image.
        Atomic undo group around pixel ops + metadata; generation bumps only
        on full success.
        """
        try:
            mode = str(params.get("mode") or _coords.MODE_ASSUME_PIXELS_UPRIGHT).strip()
            if mode not in _coords.NORMALIZE_MODES:
                return {
                    "status": "error",
                    "error": (
                        f"mode must be one of {sorted(_coords.NORMALIZE_MODES)}, got {mode!r}"
                    ),
                }

            handle = params.get("handle")
            image = None
            image_id = None
            if handle is not None:
                validated = self._validate_request_handle(handle, kind="image")
                image_id = int(validated["image_id"])
                image = self._get_image_by_id(image_id)
            else:
                image_index = int(params.get("image_index", 0))
                image = self._get_image(image_index)
                image_id = int(image.get_id())

            original_orientation = self._orient_exif_orientation(image)
            plan = _coords.plan_normalize_ops(mode, original_orientation)
            ops: list = list(plan["ops"])
            applied = bool(plan["applied"])

            # Atomic group: pixel ops (if any) + metadata write (H4, BS5).
            # If pixel ops started, any failure (mid-op exception OR metadata
            # false) must image.undo() after undo_group_end so the canvas is
            # not left partially transformed with a stale EXIF tag.
            ops_started = False
            meta_ok = False
            meta_err = None
            try:
                ug_ok = image.undo_group_start()
                self._gimp_bool_or_fail(ug_ok, "image.undo_group_start")
                body_err: BaseException | None = None
                try:
                    if applied and ops:
                        ops_started = True
                        self._apply_orientation_ops(image, ops)
                    meta_ok, meta_err = self._set_orientation_tags_to_1(image)
                except BaseException as e:
                    body_err = e
                finally:
                    # Always end the group; explicit False is a hard failure
                    # (do not swallow — fail closed before success/gen bump).
                    ug_end = image.undo_group_end()
                    self._gimp_bool_or_fail(ug_end, "image.undo_group_end")

                if body_err is not None:
                    raise body_err

                if not meta_ok:
                    if ops_started:
                        try:
                            image.undo()
                        except Exception as undo_err:
                            print(f"[MCP] normalize undo after metadata fail: {undo_err}")
                    # Do not set session flag / gen bump
                    return _sec.make_error(
                        _sec.CODE_METADATA_WRITE_FAILED,
                        meta_err or "failed to write orientation metadata",
                    )

                # Full success only:
                self._orientation_normalized[int(image_id)] = True
                gen = self._bump_image_generation(int(image_id))
                try:
                    Gimp.displays_flush()
                except Exception:
                    pass
                return {
                    "status": "success",
                    "results": {
                        "original_orientation": _coords.orientation_for_manifest(
                            original_orientation
                        ),
                        "mode_applied": mode,
                        "applied": bool(applied),
                        "pixel_orientation_normalized": True,
                        "generation": gen,
                        "handle": self._emit_image_handle(image),
                        "image_id": int(image_id),
                        "ops_applied": list(ops) if applied else [],
                    },
                }
            except _handles.HandleError as e:
                return self._handle_error_response(e)
            except RuntimeError as e:
                if ops_started:
                    try:
                        image.undo()
                    except Exception as undo_err:
                        print(f"[MCP] normalize undo after pixel-op error: {undo_err}")
                msg = str(e)
                if msg.startswith("HANDLE_NOT_FOUND"):
                    return _sec.make_error(_sec.CODE_HANDLE_NOT_FOUND, msg)
                return {"status": "error", "error": msg, "traceback": traceback.format_exc()}
            except Exception as e:
                if ops_started:
                    try:
                        image.undo()
                    except Exception as undo_err:
                        print(f"[MCP] normalize undo after pixel-op error: {undo_err}")
                return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}
        except _handles.HandleError as e:
            return self._handle_error_response(e)
        except RuntimeError as e:
            msg = str(e)
            if msg.startswith("HANDLE_NOT_FOUND"):
                return _sec.make_error(_sec.CODE_HANDLE_NOT_FOUND, msg)
            return {"status": "error", "error": msg, "traceback": traceback.format_exc()}
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _select_layers(self, params):
        """Select layers by stable item handles (same image, max MAX_SELECT_LAYERS)."""
        try:
            raw_handles = params.get("handles")
            if not isinstance(raw_handles, list):
                return _sec.make_error(
                    _sec.CODE_INVALID_HANDLE, "handles must be a list of item handles"
                )
            if len(raw_handles) == 0:
                return _sec.make_error(_sec.CODE_INVALID_HANDLE, "handles list must not be empty")
            if len(raw_handles) > MAX_SELECT_LAYERS:
                return _sec.make_error(
                    _sec.CODE_INVALID_HANDLE,
                    f"handles list length {len(raw_handles)} exceeds "
                    f"MAX_SELECT_LAYERS={MAX_SELECT_LAYERS}",
                )

            # Shape + mixed image_ids first (pure)
            image_ids = []
            for i, h in enumerate(raw_handles):
                if not isinstance(h, dict):
                    return _sec.make_error(
                        _sec.CODE_INVALID_HANDLE, f"handles[{i}] must be an object"
                    )
                if "image_id" not in h:
                    return _sec.make_error(
                        _sec.CODE_INVALID_HANDLE, f"handles[{i}] missing image_id"
                    )
                try:
                    image_ids.append(int(h["image_id"]))
                except (TypeError, ValueError):
                    return _sec.make_error(
                        _sec.CODE_INVALID_HANDLE, f"handles[{i}].image_id must be an integer"
                    )
            if len(set(image_ids)) > 1:
                return _sec.make_error(
                    _sec.CODE_INVALID_HANDLE, "handles must all share the same image_id"
                )
            image_id = image_ids[0]
            # Do not seed closed images during validation; respect retired floor
            live_gen = self._image_generations.get(image_id)
            if live_gen is None:
                live_gen = self._seed_floor(image_id)

            # §7.7 precedence: pure shape/epoch/generation BEFORE GIMP kind checks.
            # Pass optimistic id_valid=True so FOREIGN_SESSION/STALE fire first;
            # layer-ness and membership are enforced after pure validation.
            try:
                validated = _handles.require_item_handles(
                    raw_handles,
                    live_epoch=int(self.session_epoch),
                    live_generation=int(live_gen),
                    id_valid_flags=[True] * len(raw_handles),
                    item_belongs_flags=[None] * len(raw_handles),
                    max_count=MAX_SELECT_LAYERS,
                )
            except _handles.HandleError as e:
                return self._handle_error_response(e)

            layers = []
            for h in validated:
                item_id = int(h["item_id"])
                item = None
                try:
                    id_ok = True
                    if hasattr(Gimp.Item, "id_is_valid"):
                        id_ok = bool(Gimp.Item.id_is_valid(item_id))
                    if not id_ok:
                        return _sec.make_error(
                            _sec.CODE_HANDLE_NOT_FOUND,
                            f"item_id {item_id} is not valid (closed or never existed)",
                        )
                    # Spec: each id must be a layer — do not fall through to bare Item
                    if hasattr(Gimp, "Layer") and hasattr(Gimp.Layer, "get_by_id"):
                        item = Gimp.Layer.get_by_id(item_id)
                    if item is None and hasattr(Gimp.Item, "id_is_layer"):
                        if bool(Gimp.Item.id_is_layer(item_id)):
                            item = Gimp.Item.get_by_id(item_id)
                        else:
                            if hasattr(Gimp.Item, "get_by_id"):
                                probe = Gimp.Item.get_by_id(item_id)
                                if probe is not None:
                                    return _sec.make_error(
                                        _sec.CODE_INVALID_HANDLE,
                                        f"item_id {item_id} is not a layer",
                                    )
                    if item is None and hasattr(Gimp.Item, "get_by_id"):
                        candidate = Gimp.Item.get_by_id(item_id)
                        if candidate is not None:
                            is_layer = False
                            if hasattr(Gimp, "Layer") and isinstance(candidate, Gimp.Layer):
                                is_layer = True
                            elif hasattr(candidate, "is_layer") and callable(candidate.is_layer):
                                try:
                                    is_layer = bool(candidate.is_layer())
                                except Exception:
                                    is_layer = False
                            if is_layer:
                                item = candidate
                            else:
                                return _sec.make_error(
                                    _sec.CODE_INVALID_HANDLE,
                                    f"item_id {item_id} is not a layer",
                                )
                    if item is None:
                        return _sec.make_error(
                            _sec.CODE_HANDLE_NOT_FOUND,
                            f"item_id {item_id} is not a valid layer",
                        )
                    try:
                        img = item.get_image()
                        belongs = img is not None and int(img.get_id()) == int(image_id)
                    except Exception:
                        belongs = False
                    if not belongs:
                        return _sec.make_error(
                            _sec.CODE_HANDLE_NOT_FOUND,
                            f"item_id {item_id} does not belong to image_id {image_id}",
                        )
                except _sec.SecurityError:
                    raise
                except Exception:
                    return _sec.make_error(
                        _sec.CODE_HANDLE_NOT_FOUND,
                        f"item_id {item_id} could not be resolved as a layer",
                    )
                layers.append(item)

            # Resolve image (live only) then seed/emit generation for open image
            image = self._get_image_by_id(image_id)
            selected_layers = list(layers)
            try:
                image.set_selected_layers(selected_layers)
            except Exception as e:
                msg = str(e).lower()
                # Only float/floating/anchor → SELECTION_CONFLICT (not generic PDB failures)
                if "float" in msg or "floating" in msg or "anchor" in msg:
                    return _sec.make_error(
                        _sec.CODE_SELECTION_CONFLICT,
                        "Cannot set selected layers while a floating selection exists; "
                        "anchor or remove floating selection first",
                    )
                return _sec.make_error(_sec.CODE_INTERNAL, str(e))

            Gimp.displays_flush()
            selected_handles = [
                self._emit_item_handle_ids(int(v["item_id"]), image_id) for v in validated
            ]
            return {
                "status": "success",
                "results": {
                    "selected_handles": selected_handles,
                    "image_id": image_id,
                    "generation": self._image_generation(image_id),
                },
            }
        except _handles.HandleError as e:
            return self._handle_error_response(e)
        except RuntimeError as e:
            msg = str(e)
            if msg.startswith("HANDLE_NOT_FOUND"):
                return _sec.make_error(_sec.CODE_HANDLE_NOT_FOUND, msg)
            return {"status": "error", "error": msg, "traceback": traceback.format_exc()}
        except Exception as e:
            msg = str(e).lower()
            if "float" in msg or "floating" in msg or "anchor" in msg:
                return _sec.make_error(
                    _sec.CODE_SELECTION_CONFLICT,
                    "Cannot set selected layers while a floating selection exists; "
                    "anchor or remove floating selection first",
                )
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _channel_ops_from_string(self, op):
        """Map operation string to Gimp.ChannelOps enum value."""
        return {
            "replace": Gimp.ChannelOps.REPLACE,
            "add": Gimp.ChannelOps.ADD,
            "subtract": Gimp.ChannelOps.SUBTRACT,
            "intersect": Gimp.ChannelOps.INTERSECT,
        }.get(op.lower(), Gimp.ChannelOps.REPLACE)

    def _interp_from_string(self, interp):
        """Map interpolation string to Gimp.InterpolationType."""
        return {
            "cubic": Gimp.InterpolationType.CUBIC,
            "linear": Gimp.InterpolationType.LINEAR,
            "none": Gimp.InterpolationType.NONE,
        }.get(interp.lower(), Gimp.InterpolationType.CUBIC)

    def _layer_children(self, layer):
        """Return child layers if *layer* is a group, else an empty list.

        GIMP 3 group layers expose children via ``get_children()`` and/or
        ``get_layers()``; prefer ``is_group()`` when available.
        """
        try:
            if hasattr(layer, "is_group") and callable(layer.is_group):
                try:
                    if not layer.is_group():
                        return []
                except (AttributeError, RuntimeError, TypeError):
                    pass
            for attr in ("get_children", "get_layers"):
                getter = getattr(layer, attr, None)
                if not callable(getter):
                    continue
                try:
                    kids = getter()
                    if kids is not None:
                        return list(kids)
                except (AttributeError, RuntimeError, TypeError):
                    continue
        except (AttributeError, RuntimeError, TypeError):
            pass
        return []

    def _iter_layers_recursive(self, layers, *, visible_only=False, max_depth=32):
        """Depth-first walk of layers including nested group children.

        Yields every node (leaf and group). When *visible_only* is True, skips
        invisible layers and does not descend into invisible groups (they do
        not contribute to the visible composite).

        Guards: visited layer ids (cycle-safe) and *max_depth* (default 32,
        matching orientation tree walk). Document order among siblings is
        preserved as much as practical.
        """
        stack = [(layer, 0) for layer in (layers or [])]
        visited = set()
        while stack:
            layer, depth = stack.pop(0)
            try:
                lid = int(layer.get_id())
            except Exception:
                lid = None
            if lid is not None:
                if lid in visited:
                    continue
                visited.add(lid)
            if depth > max_depth:
                continue
            if visible_only:
                try:
                    if not bool(layer.get_visible()):
                        continue
                except (AttributeError, RuntimeError, TypeError):
                    pass
            yield layer
            if depth < max_depth:
                children = self._layer_children(layer)
                if children:
                    # Preserve document order among siblings (depth-first).
                    stack[0:0] = [(c, depth + 1) for c in children]

    def _preflight_has_alpha(self, image):
        """Read-only: True if any visible layer/drawable reports has_alpha().

        Walks nested layer groups recursively (spec §2.3: any visible drawable).
        """
        try:
            layers = list(image.get_layers() or [])
        except (AttributeError, RuntimeError, TypeError):
            layers = []
        for layer in self._iter_layers_recursive(layers, visible_only=True):
            try:
                if layer.has_alpha():
                    return True
            except (AttributeError, RuntimeError, TypeError):
                continue
        # Also check selected drawables (may include items outside top-level walk)
        try:
            selected = list(image.get_selected_layers() or [])
        except (AttributeError, RuntimeError, TypeError):
            selected = []
        # Selected layers: only visible ones (invisible selection must not force alpha).
        for layer in self._iter_layers_recursive(selected, visible_only=True):
            try:
                if layer.has_alpha():
                    return True
            except (AttributeError, RuntimeError, TypeError):
                continue
        return False

    def _set_export_property_critical(self, cfg, name, value, property_errors, required=True):
        """Set a config property; on failure append to property_errors (no bare pass)."""
        try:
            cfg.set_property(name, value)
            return True
        except Exception as e:
            msg = f"set_property({name!r}) failed: {e}"
            print(f"[MCP] export critical property: {msg}")
            if required:
                property_errors.append(msg)
            return False

    def _set_png_rgba8_format(self, cfg, property_errors):
        """Force PNG pixel format to RGBA8 via Phase-0 candidate props/values."""
        last_err = None
        for prop in _exp.PNG_PIXEL_FORMAT_PROP_CANDIDATES:
            for val in _exp.PNG_RGBA8_VALUE_CANDIDATES:
                try:
                    cfg.set_property(prop, val)
                    print(f"[MCP] PNG pixel format set: {prop}={val!r}")
                    return True
                except Exception as e:
                    last_err = e
                    continue
        msg = (
            "Failed to set PNG pixel format to RGBA8 "
            f"(tried props={list(_exp.PNG_PIXEL_FORMAT_PROP_CANDIDATES)}, "
            f"values={list(_exp.PNG_RGBA8_VALUE_CANDIDATES)}): {last_err}"
        )
        print(f"[MCP] {msg}")
        property_errors.append(msg)
        return False

    def _export_to_path(
        self,
        image,
        file_path,
        fmt,
        quality,
        flatten=False,
        preserve_alpha=None,
        verify=True,
    ):
        """Export image to file_path; return rich result dict (success or error).

        Never mutates the caller's original image — prep runs on a duplicate.
        Internal opaque callers should pass flatten=True (auto preserve_alpha=False).

        Defense-in-depth: re-check path jail even if callers already jailed.
        """
        from gi.repository import Gio

        safe, err = self._jail_path(file_path)
        if err is not None:
            raise _sec.SecurityError(_sec.CODE_PATH_DENIED, err.get("error", "PATH_DENIED"))
        file_path = str(safe)

        policy = _exp.resolve_export_policy(fmt, preserve_alpha, flatten, verify=verify)
        if policy.error:
            return _exp.build_export_error(
                code=policy.code or _exp.CODE_POLICY_CONFLICT,
                error=policy.error,
                file_path=file_path,
                preserve_alpha=policy.preserve_alpha,
                format=policy.format,
                export_method=policy.export_method,
            )

        preflight_has_alpha = bool(self._preflight_has_alpha(image))
        property_errors = []
        pdb_procedure = None
        export_method = policy.export_method
        dup = None
        png_color_type = None

        try:
            # Always prep on a duplicate so the user image is never mutated.
            dup = image.duplicate()
            try:
                dup.undo_disable()
            except (AttributeError, RuntimeError) as e:
                print(f"[MCP] export undo_disable on dup failed: {e}")

            drawable = None
            if policy.preserve_alpha:
                self._selection_none_or_fail(
                    dup, "Selection.none before alpha-preserving export merge failed"
                )
                try:
                    merged = dup.merge_visible_layers(Gimp.MergeType.CLIP_TO_IMAGE)
                except (AttributeError, RuntimeError) as merge_err:
                    return _exp.build_export_error(
                        code=_exp.CODE_EXPORT_FAILED,
                        error=f"merge_visible_layers failed: {merge_err}",
                        file_path=file_path,
                        preserve_alpha=True,
                        preflight_has_alpha=preflight_has_alpha,
                        format=policy.format,
                        export_method=export_method,
                    )
                if merged is None:
                    return _exp.build_export_error(
                        code=_exp.CODE_EXPORT_FAILED,
                        error="merge_visible_layers returned no layer",
                        file_path=file_path,
                        preserve_alpha=True,
                        preflight_has_alpha=preflight_has_alpha,
                        format=policy.format,
                        export_method=export_method,
                    )
                drawable = merged
                export_method = _exp.EXPORT_METHOD_MERGE
            elif policy.flatten:
                self._selection_none_or_fail(dup, "Selection.none before flatten export failed")
                try:
                    flattened = dup.flatten()
                except (AttributeError, RuntimeError) as flatten_err:
                    return _exp.build_export_error(
                        code=_exp.CODE_EXPORT_FAILED,
                        error=f"flatten failed: {flatten_err}",
                        file_path=file_path,
                        preserve_alpha=False,
                        preflight_has_alpha=preflight_has_alpha,
                        format=policy.format,
                        export_method=_exp.EXPORT_METHOD_FLATTEN,
                    )
                drawable = flattened
                export_method = _exp.EXPORT_METHOD_FLATTEN
            else:
                # Direct path — still on dup; pick a drawable for export config.
                try:
                    layers = list(dup.get_layers() or [])
                except (AttributeError, RuntimeError, TypeError):
                    layers = []
                try:
                    selected = list(dup.get_selected_layers() or [])
                except (AttributeError, RuntimeError, TypeError):
                    selected = []
                drawable = (selected or layers or [None])[0]
                export_method = _exp.EXPORT_METHOD_DIRECT

            gio_file = Gio.File.new_for_path(file_path)
            pdb = Gimp.get_pdb()
            proc_name = _exp.pdb_procedure_for_format(policy.format)
            if proc_name is None:
                # Never silently substitute PNG for an unsupported format.
                return _exp.build_export_error(
                    code=_exp.CODE_UNSUPPORTED_FORMAT,
                    error=(
                        f"Unsupported export format {policy.format!r} "
                        f"(UNSUPPORTED_FORMAT). Supported formats: "
                        f"{_exp.SUPPORTED_EXPORT_FORMATS_DISPLAY}."
                    ),
                    file_path=file_path,
                    preserve_alpha=policy.preserve_alpha,
                    preflight_has_alpha=preflight_has_alpha,
                    format=policy.format,
                    export_method=export_method,
                )
            proc = pdb.lookup_procedure(proc_name) if proc_name else None
            used_degraded = False

            if proc is not None:
                pdb_procedure = proc_name
                cfg = proc.create_config()
                # Alpha-critical props: image, file, drawable/drawables, pixel format
                if not self._set_export_property_critical(
                    cfg, "image", dup, property_errors, required=True
                ):
                    return _exp.build_export_error(
                        code=_exp.CODE_EXPORT_FAILED,
                        error="Failed to set export image property",
                        file_path=file_path,
                        property_errors=property_errors,
                        preserve_alpha=policy.preserve_alpha,
                        preflight_has_alpha=preflight_has_alpha,
                        format=policy.format,
                        export_method=export_method,
                        pdb_procedure=pdb_procedure,
                    )
                if not self._set_export_property_critical(
                    cfg, "file", gio_file, property_errors, required=True
                ):
                    return _exp.build_export_error(
                        code=_exp.CODE_EXPORT_FAILED,
                        error="Failed to set export file property",
                        file_path=file_path,
                        property_errors=property_errors,
                        preserve_alpha=policy.preserve_alpha,
                        preflight_has_alpha=preflight_has_alpha,
                        format=policy.format,
                        export_method=export_method,
                        pdb_procedure=pdb_procedure,
                    )

                drawable_set = False
                if drawable is not None:
                    try:
                        cfg.set_property("drawable", drawable)
                        drawable_set = True
                    except Exception as drawable_err:
                        print(f"[MCP] export drawable property failed: {drawable_err}")
                        try:
                            cfg.set_property("drawables", [drawable])
                            drawable_set = True
                        except Exception as prop_err:
                            property_errors.append(
                                f"drawable/drawables property failed: {prop_err}"
                            )
                if not drawable_set:
                    if drawable is None:
                        property_errors.append("No drawable available for export config")
                    else:
                        property_errors.append("Could not set drawable/drawables on export config")
                    # Fail-closed when preserve_alpha (align with snapshot: do not
                    # run file-*-export without a drawable for alpha-critical path).
                    if policy.preserve_alpha:
                        return _exp.build_export_error(
                            code=_exp.CODE_EXPORT_FAILED,
                            error=(
                                "Alpha-preserving export requires a drawable on the "
                                "export config; drawable/drawables property was not set"
                            ),
                            file_path=file_path,
                            property_errors=property_errors,
                            preserve_alpha=True,
                            preflight_has_alpha=preflight_has_alpha,
                            format=policy.format,
                            export_method=export_method,
                            pdb_procedure=pdb_procedure,
                        )

                # PNG RGBA8 when preserving alpha and preflight saw alpha (DoD-12 fail-closed)
                if policy.format == "png" and policy.preserve_alpha and preflight_has_alpha:
                    if not self._set_png_rgba8_format(cfg, property_errors):
                        return _exp.build_export_error(
                            code=_exp.CODE_EXPORT_FAILED,
                            error=(
                                "Failed to set PNG RGBA8 pixel format (alpha-critical); "
                                "refusing to run export without guaranteed alpha pixel format"
                            ),
                            file_path=file_path,
                            property_errors=property_errors,
                            preserve_alpha=True,
                            preflight_has_alpha=preflight_has_alpha,
                            format=policy.format,
                            export_method=export_method,
                            pdb_procedure=pdb_procedure,
                        )

                # Best-effort quality knobs (not alpha-critical)
                if policy.format == "jpeg":
                    try:
                        cfg.set_property("quality", float(quality) / 100.0)
                    except Exception:
                        try:
                            cfg.set_property("quality", float(quality))
                        except Exception as qe:
                            print(f"[MCP] jpeg quality set skipped: {qe}")
                if policy.format == "webp":
                    try:
                        cfg.set_property("quality", float(quality))
                    except Exception as qe:
                        print(f"[MCP] webp quality set skipped: {qe}")

                try:
                    proc.run(cfg)
                except Exception as run_err:
                    property_errors.append(f"{proc_name} run failed: {run_err}")
                    print(f"[MCP] {proc_name} failed: {run_err}; trying degraded path")
                    proc = None

            if proc is None or not os.path.isfile(file_path) or os.path.getsize(file_path) == 0:
                # Degraded last resort — log; still verify when preserve_alpha.
                used_degraded = True
                print(f"[MCP] DEGRADED export path for {file_path!r} (procedure={proc_name!r})")
                try:
                    Gimp.file_overwrite(Gimp.RunMode.NONINTERACTIVE, dup, gio_file)
                    pdb_procedure = pdb_procedure or "Gimp.file_overwrite"
                except Exception as ow_err:
                    try:
                        Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, dup, gio_file)
                        pdb_procedure = "Gimp.file_save"
                    except Exception as save_err:
                        return _exp.build_export_error(
                            code=_exp.CODE_EXPORT_FAILED,
                            error=(
                                f"Export failed via {proc_name} and degraded "
                                f"file_overwrite/file_save: {ow_err}; {save_err}"
                            ),
                            file_path=file_path,
                            left_on_disk=os.path.isfile(file_path),
                            property_errors=property_errors,
                            preserve_alpha=policy.preserve_alpha,
                            preflight_has_alpha=preflight_has_alpha,
                            format=policy.format,
                            export_method=export_method,
                            pdb_procedure=pdb_procedure,
                        )

            if not os.path.isfile(file_path):
                return _exp.build_export_error(
                    code=_exp.CODE_EXPORT_FAILED,
                    error="Export produced no output file",
                    file_path=file_path,
                    left_on_disk=False,
                    property_errors=property_errors,
                    preserve_alpha=policy.preserve_alpha,
                    preflight_has_alpha=preflight_has_alpha,
                    format=policy.format,
                    export_method=export_method,
                    pdb_procedure=pdb_procedure,
                )

            file_size = os.path.getsize(file_path)

            # PNG IHDR verify (fail-closed when preserve_alpha + preflight alpha)
            if policy.format == "png" and os.path.isfile(file_path):
                try:
                    ihdr = _exp.png_ihdr_info(file_path)
                    png_color_type = int(ihdr["color_type"])
                except (ValueError, OSError) as ihdr_err:
                    if policy.preserve_alpha and preflight_has_alpha and policy.verify:
                        return _exp.build_export_error(
                            code=_exp.CODE_ALPHA_LOST,
                            error=f"PNG IHDR unreadable after export: {ihdr_err}",
                            file_path=file_path,
                            left_on_disk=True,
                            preflight_has_alpha=preflight_has_alpha,
                            preserve_alpha=True,
                            property_errors=property_errors,
                            export_method=export_method,
                            pdb_procedure=pdb_procedure,
                            format=policy.format,
                        )
                    png_color_type = None

            if (
                policy.verify
                and policy.preserve_alpha
                and preflight_has_alpha
                and policy.format == "png"
            ):
                has_alpha_file = False
                try:
                    has_alpha_file = _exp.file_has_alpha_channel(file_path)
                except (ValueError, OSError):
                    has_alpha_file = False
                if not has_alpha_file:
                    return _exp.build_export_error(
                        code=_exp.CODE_ALPHA_LOST,
                        error=(
                            "preserve_alpha=True and preflight had alpha, but "
                            f"PNG color type is {png_color_type} "
                            f"(expected 4 or 6). File left on disk for debugging."
                        ),
                        file_path=file_path,
                        left_on_disk=True,
                        png_color_type=png_color_type,
                        preflight_has_alpha=True,
                        preserve_alpha=True,
                        property_errors=property_errors,
                        export_method=export_method,
                        pdb_procedure=pdb_procedure,
                        format=policy.format,
                    )

            if policy.preserve_alpha and not preflight_has_alpha:
                alpha_verified = "not_applicable"
            elif not policy.preserve_alpha:
                alpha_verified = "not_applicable"
            elif policy.format == "png" and policy.verify and preflight_has_alpha:
                alpha_verified = True
            else:
                alpha_verified = "not_applicable"

            result = _exp.build_export_success(
                file_path=file_path,
                format=policy.format,
                file_size_bytes=file_size,
                preserve_alpha=policy.preserve_alpha,
                preflight_has_alpha=preflight_has_alpha,
                alpha_verified=alpha_verified,
                export_method=export_method,
                pdb_procedure=pdb_procedure,
                png_color_type=png_color_type,
                property_errors=property_errors if property_errors else None,
                extra={"degraded_path": used_degraded} if used_degraded else None,
            )
            return result
        finally:
            if dup is not None:
                try:
                    dup.delete()
                except Exception:
                    pass

    def _apply_gegl_filter(self, image, drawable, op_name, props):
        """Apply a GEGL operation to a drawable via gimp-drawable-filter-new."""
        pdb = Gimp.get_pdb()
        # Try the GEGL filter approach via PDB
        filter_proc = pdb.lookup_procedure("gimp-drawable-filter-new")
        if filter_proc:
            cfg = filter_proc.create_config()
            cfg.set_property("drawable", drawable)
            cfg.set_property("operation-name", op_name)
            cfg.set_property("name", op_name)
            result = filter_proc.run(cfg)
            # Get the filter object
            try:
                filtr = result.index(0)
                for k, v in props.items():
                    try:
                        filtr.set_property(k, v)
                    except Exception:
                        pass
                # Apply filter (merge)
                apply_proc = pdb.lookup_procedure("gimp-drawable-merge-filter")
                if apply_proc:
                    acfg = apply_proc.create_config()
                    acfg.set_property("drawable", drawable)
                    acfg.set_property("filter", filtr)
                    apply_proc.run(acfg)
            except Exception:
                pass
        else:
            # Fallback: execute via exec context — gated (not agent cmds, but still exec).
            if not _sec.exec_allowed():
                print(
                    f"[MCP] GEGL filter fallback exec skipped for {op_name} "
                    "(GIMP_MCP_ALLOW_EXEC off; use PDB path or enable advanced mode)"
                )
                return
            self._audit(
                event="exec",
                type="gegl_filter_fallback",
                mode="elevated",
                op_name=op_name,
                success=True,
            )
            props_code = ", ".join(f'"{k}", {v!r}' for k, v in props.items())
            cmds = [
                "from gi.repository import Gimp, Gegl",
                "_img = Gimp.get_images()[0]",
                "_d = (_img.get_selected_layers() or _img.get_layers() or [None])[0]",
                f"_d.apply_drawable_filter_new('{op_name}', '', [{props_code}])",
                "Gimp.displays_flush()",
            ]
            for cmd in cmds:
                exec(cmd, self.context)

    # =========================================================================
    # CATEGORY 1 — File Operations
    # =========================================================================

    def _open_image(self, params):
        """Open an image file, create a display, return metadata."""
        try:
            from gi.repository import Gio

            file_path = params.get("file_path", "")
            safe, err = self._jail_path(file_path)
            if err is not None:
                return err
            file_path = str(safe)
            gio_file = Gio.File.new_for_path(file_path)
            image = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, gio_file)
            if image is None:
                return {"status": "error", "error": f"Could not open file: {file_path}"}
            display = Gimp.Display.new(image)
            Gimp.displays_flush()
            base_type = image.get_base_type()
            mode_map = {
                Gimp.ImageBaseType.RGB: "RGB",
                Gimp.ImageBaseType.GRAY: "Grayscale",
                Gimp.ImageBaseType.INDEXED: "Indexed",
            }
            image_id = int(image.get_id())
            gen = self._seed_image_generation(image_id, 1)
            # If XCF already had Source_Immutable group, hydrate session deny set
            self._hydrate_protected_from_group(image, image_id)
            return {
                "status": "success",
                "results": {
                    "image_id": image_id,
                    "width": image.get_width(),
                    "height": image.get_height(),
                    "color_mode": mode_map.get(base_type, str(base_type)),
                    "num_layers": len(image.get_layers()),
                    "display_opened": display is not None,
                    "generation": gen,
                    "handle": self._emit_image_handle(image),
                },
            }
        except Exception as e:
            return _sec.redact_error(e)

    def _save_xcf(self, params):
        """Save image as XCF."""
        try:
            from gi.repository import Gio

            file_path = params.get("file_path", "")
            safe, err = self._jail_path(file_path)
            if err is not None:
                return err
            file_path = str(safe)
            try:
                image, _iid = self._resolve_image_from_params(params)
            except _handles.HandleError as e:
                return self._handle_error_response(e)
            gio_file = Gio.File.new_for_path(file_path)
            pdb = Gimp.get_pdb()
            proc = pdb.lookup_procedure("gimp-xcf-save")
            if proc:
                cfg = proc.create_config()
                cfg.set_property("image", image)
                cfg.set_property("file", gio_file)
                proc.run(cfg)
            else:
                Gimp.file_overwrite(Gimp.RunMode.NONINTERACTIVE, image, gio_file)
            return {"status": "success", "results": {"status": "success", "file_path": file_path}}
        except Exception as e:
            return _sec.redact_error(e)

    def _export_image(self, params):
        """Export image to raster format (alpha-preserving defaults for PNG/WEBP/TIFF)."""
        try:
            file_path = params.get("file_path", "")
            safe, err = self._jail_path(file_path)
            if err is not None:
                return err
            file_path = str(safe)
            # MCP schema uses format; raw TCP/demos may send file_type
            fmt = params.get("format", params.get("file_type", "png"))
            quality = int(params.get("quality", 90))
            flatten = _exp.coerce_bool(params.get("flatten", False), default=False)
            preserve_alpha = _exp.coerce_optional_bool(params.get("preserve_alpha", None))
            verify = _exp.coerce_bool(params.get("verify", True), default=True)
            try:
                image, _iid = self._resolve_image_from_params(params)
            except _handles.HandleError as e:
                return self._handle_error_response(e)
            result = self._export_to_path(
                image,
                file_path,
                fmt,
                quality,
                flatten=flatten,
                preserve_alpha=preserve_alpha,
                verify=verify,
            )
            Gimp.displays_flush()
            if result.get("status") == "error":
                # Preserve structured export errors (ALPHA_LOST, POLICY_CONFLICT, …)
                return result
            return {
                "status": "success",
                "results": result,
            }
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return _sec.redact_error(e)

    def _batch_export(self, params):
        """Export all (or one) open images to output_dir."""
        try:
            output_dir = params.get("output_dir", "")
            safe_dir, err = self._jail_path(output_dir)
            if err is not None:
                return err
            output_dir = str(safe_dir)
            fmt = params.get("format", params.get("file_type", "png"))
            quality = int(params.get("quality", 90))
            name_pattern = params.get("name_pattern", "{name}")
            image_index = params.get("image_index", None)
            flatten = _exp.coerce_bool(params.get("flatten", False), default=False)
            preserve_alpha = _exp.coerce_optional_bool(params.get("preserve_alpha", None))
            verify = _exp.coerce_bool(params.get("verify", True), default=True)

            images = Gimp.get_images()
            if not images:
                return {"status": "error", "error": "No images open"}

            targets = [(i, img) for i, img in enumerate(images)]
            if image_index is not None:
                targets = [(image_index, images[int(image_index)])]

            os.makedirs(output_dir, exist_ok=True)
            exported = []
            errors = []

            for idx, image in targets:
                try:
                    gio_file = image.get_file()
                    raw_name = (
                        gio_file.get_basename().rsplit(".", 1)[0] if gio_file else f"image_{idx}"
                    )
                    filename = name_pattern.format(name=raw_name, index=idx) + f".{fmt}"
                    out_path = os.path.join(output_dir, filename)
                    result = self._export_to_path(
                        image,
                        out_path,
                        fmt,
                        quality,
                        flatten=flatten,
                        preserve_alpha=preserve_alpha,
                        verify=verify,
                    )
                    if result.get("status") == "error":
                        # Forward full structured export fields (ALPHA_LOST contract).
                        err_item = {
                            "index": idx,
                            "error": result.get("error"),
                            "code": result.get("code"),
                            "file_path": result.get("file_path", out_path),
                        }
                        for key in (
                            "left_on_disk",
                            "png_color_type",
                            "preflight_has_alpha",
                            "property_errors",
                            "format",
                            "preserve_alpha",
                            "export_method",
                            "pdb_procedure",
                        ):
                            if key in result:
                                err_item[key] = result[key]
                        errors.append(err_item)
                    else:
                        exported.append(
                            {
                                "file_path": out_path,
                                "name": raw_name,
                                "width": image.get_width(),
                                "height": image.get_height(),
                                "file_size_bytes": result.get("file_size_bytes"),
                                "preserve_alpha": result.get("preserve_alpha"),
                                "alpha_verified": result.get("alpha_verified"),
                            }
                        )
                except Exception as ex:
                    errors.append({"index": idx, "error": str(ex)})

            return {
                "status": "success",
                "results": {"exported": exported, "count": len(exported), "errors": errors},
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _verify_alpha_channel(self, params):
        """Read-only preflight: image-level alpha + format capability matrix."""
        try:
            try:
                image, _iid = self._resolve_image_from_params(params)
            except _handles.HandleError as e:
                return self._handle_error_response(e)
            layers_with_alpha = []
            try:
                layers = list(image.get_layers() or [])
            except (AttributeError, RuntimeError, TypeError):
                layers = []
            # Recursive walk so nested group members appear in layers_with_alpha
            for layer in self._iter_layers_recursive(layers, visible_only=False):
                try:
                    if layer.has_alpha():
                        try:
                            name = layer.get_name()
                        except (AttributeError, RuntimeError):
                            name = str(layer)
                        layers_with_alpha.append(name)
                except (AttributeError, RuntimeError, TypeError):
                    continue

            has_alpha = bool(layers_with_alpha) or self._preflight_has_alpha(image)

            base_type = "unknown"
            try:
                bt = image.get_base_type()
                mode_map = {
                    Gimp.ImageBaseType.RGB: "RGB",
                    Gimp.ImageBaseType.GRAY: "Grayscale",
                    Gimp.ImageBaseType.INDEXED: "Indexed",
                }
                base_type = mode_map.get(bt, str(bt))
            except (AttributeError, RuntimeError, TypeError):
                pass

            return {
                "status": "success",
                "results": {
                    "has_alpha": has_alpha,
                    "image_base_type": base_type,
                    "layers_with_alpha": layers_with_alpha,
                    "can_preserve_alpha_for_format": _exp.format_capability_matrix(),
                },
            }
        except Exception as e:
            return _sec.redact_error(e)

    # =========================================================================
    # CATEGORY 2 — Image Adjustments
    # =========================================================================

    def _auto_levels(self, params):
        """Auto-stretch levels on a drawable."""
        try:
            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            image.undo_group_start()
            try:
                pdb = Gimp.get_pdb()
                proc = pdb.lookup_procedure("gimp-levels-stretch")
                if proc:
                    cfg = proc.create_config()
                    cfg.set_property("drawable", drawable)
                    proc.run(cfg)
                else:
                    proc2 = pdb.lookup_procedure("gimp-drawable-levels")
                    if proc2:
                        cfg2 = proc2.create_config()
                        cfg2.set_property("drawable", drawable)
                        cfg2.set_property("channel", Gimp.HistogramChannel.VALUE)
                        cfg2.set_property("low-input", 0.0)
                        cfg2.set_property("high-input", 1.0)
                        cfg2.set_property("clamp-input", True)
                        cfg2.set_property("gamma", 1.0)
                        cfg2.set_property("low-output", 0.0)
                        cfg2.set_property("high-output", 1.0)
                        proc2.run(cfg2)
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _adjust_curves(self, params):
        """Adjust tonal curves."""
        try:
            PRESETS = {
                "s_curve": [0, 0, 64, 50, 192, 210, 255, 255],
                "lighten": [0, 0, 128, 180, 255, 255],
                "darken": [0, 0, 128, 75, 255, 255],
                "contrast": [0, 0, 64, 40, 192, 215, 255, 255],
            }
            CHANNEL_MAP = {
                "value": Gimp.HistogramChannel.VALUE,
                "red": Gimp.HistogramChannel.RED,
                "green": Gimp.HistogramChannel.GREEN,
                "blue": Gimp.HistogramChannel.BLUE,
                "alpha": Gimp.HistogramChannel.ALPHA,
            }
            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            preset = params.get("preset", "s_curve")
            custom_pts = params.get("points", None)
            channel_str = params.get("channel", "value")

            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            channel = CHANNEL_MAP.get(channel_str.lower(), Gimp.HistogramChannel.VALUE)

            if custom_pts is not None:
                # Flatten [[in,out],...] -> [in,out,in,out,...]
                if custom_pts and isinstance(custom_pts[0], (list, tuple)):
                    flat = []
                    for pt in custom_pts:
                        flat.extend(pt)
                    control_pts = flat
                else:
                    control_pts = list(custom_pts)
            else:
                control_pts = PRESETS.get(preset, PRESETS["s_curve"])

            image.undo_group_start()
            try:
                pts_normalized = [p / 255.0 for p in control_pts]
                # set_property can't auto-convert Python list to GimpDoubleArray.
                # Try calling curves_spline as a direct method on the drawable
                # (GI exposes gimp-drawable-curves-spline as drawable.curves_spline).
                try:
                    drawable.curves_spline(channel, pts_normalized)
                except Exception:
                    # Fallback: use array.array typed buffer which GI may accept
                    import array as _arr

                    typed = _arr.array("d", pts_normalized)
                    pdb = Gimp.get_pdb()
                    proc = pdb.lookup_procedure("gimp-drawable-curves-spline")
                    if proc:
                        cfg = proc.create_config()
                        cfg.set_property("drawable", drawable)
                        cfg.set_property("channel", channel)
                        cfg.set_property("points", typed)
                        proc.run(cfg)
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _adjust_brightness_contrast(self, params):
        """Adjust brightness and contrast."""
        try:
            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            brightness = float(params.get("brightness", 0))
            contrast = float(params.get("contrast", 0))
            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            image.undo_group_start()
            try:
                pdb = Gimp.get_pdb()
                proc = pdb.lookup_procedure("gimp-drawable-brightness-contrast")
                if proc:
                    cfg = proc.create_config()
                    cfg.set_property("drawable", drawable)
                    cfg.set_property("brightness", brightness / 127.0)
                    cfg.set_property("contrast", contrast / 127.0)
                    proc.run(cfg)
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _adjust_hue_saturation(self, params):
        """Adjust hue, saturation, lightness."""
        try:
            HUE_RANGE_MAP = {
                "all": Gimp.HueRange.ALL,
                "red": Gimp.HueRange.RED,
                "yellow": Gimp.HueRange.YELLOW,
                "green": Gimp.HueRange.GREEN,
                "cyan": Gimp.HueRange.CYAN,
                "blue": Gimp.HueRange.BLUE,
                "magenta": Gimp.HueRange.MAGENTA,
            }
            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            hue = float(params.get("hue", 0))
            saturation = float(params.get("saturation", 0))
            lightness = float(params.get("lightness", 0))
            color_range = params.get("color_range", "all")
            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            hue_range = HUE_RANGE_MAP.get(color_range.lower(), Gimp.HueRange.ALL)
            image.undo_group_start()
            try:
                pdb = Gimp.get_pdb()
                proc = pdb.lookup_procedure("gimp-drawable-hue-saturation")
                if proc:
                    cfg = proc.create_config()
                    cfg.set_property("drawable", drawable)
                    cfg.set_property("hue-range", hue_range)
                    cfg.set_property("hue-offset", hue)
                    cfg.set_property("lightness", lightness)
                    cfg.set_property("saturation", saturation)
                    cfg.set_property("overlap", 0.0)
                    proc.run(cfg)
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _adjust_color_balance(self, params):
        """Adjust color balance for shadows/midtones/highlights."""
        try:
            # GIMP 3.2 uses integer constants for color-range (0=shadows,1=midtones,2=highlights)
            RANGE_MAP = {
                "shadows": 0,
                "midtones": 1,
                "highlights": 2,
            }
            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            cyan_red = float(params.get("cyan_red", 0))
            magenta_green = float(params.get("magenta_green", 0))
            yellow_blue = float(params.get("yellow_blue", 0))
            range_str = params.get("range", "midtones")
            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            color_range = RANGE_MAP.get(range_str.lower(), 1)
            image.undo_group_start()
            try:
                pdb = Gimp.get_pdb()
                proc = pdb.lookup_procedure("gimp-drawable-color-balance")
                if proc:
                    cfg = proc.create_config()
                    cfg.set_property("drawable", drawable)
                    cfg.set_property("transfer-mode", color_range)
                    cfg.set_property("cyan-red", cyan_red)
                    cfg.set_property("magenta-green", magenta_green)
                    cfg.set_property("yellow-blue", yellow_blue)
                    cfg.set_property("preserve-lum", True)
                    proc.run(cfg)
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _sharpen(self, params):
        """Sharpen using unsharp mask."""
        try:
            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            amount = float(params.get("amount", 50.0))
            radius = float(params.get("radius", 3.0))
            threshold = int(params.get("threshold", 0))
            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            image.undo_group_start()
            try:
                pdb = Gimp.get_pdb()
                proc = pdb.lookup_procedure("plug-in-unsharp-mask")
                if proc:
                    cfg = proc.create_config()
                    cfg.set_property("image", image)
                    cfg.set_property("drawable", drawable)
                    cfg.set_property("radius", radius)
                    cfg.set_property("amount", amount / 100.0)
                    cfg.set_property("threshold", threshold)
                    proc.run(cfg)
                else:
                    self._apply_gegl_filter(
                        image,
                        drawable,
                        "gegl:unsharp-mask",
                        {
                            "std-dev": radius,
                            "scale": amount / 100.0,
                            "threshold": threshold / 255.0,
                        },
                    )
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _blur(self, params):
        """Gaussian blur."""
        try:
            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            radius_x = float(params.get("radius_x", 5.0))
            radius_y = float(params.get("radius_y", 5.0))
            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            image.undo_group_start()
            try:
                pdb = Gimp.get_pdb()
                proc = pdb.lookup_procedure("plug-in-gauss")
                if proc:
                    cfg = proc.create_config()
                    cfg.set_property("image", image)
                    cfg.set_property("drawable", drawable)
                    cfg.set_property("horizontal", int(radius_x * 2 + 1))
                    cfg.set_property("vertical", int(radius_y * 2 + 1))
                    cfg.set_property("method", 0)
                    proc.run(cfg)
                else:
                    self._apply_gegl_filter(
                        image,
                        drawable,
                        "gegl:gaussian-blur",
                        {
                            "std-dev-x": radius_x,
                            "std-dev-y": radius_y,
                        },
                    )
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _denoise(self, params):
        """Noise reduction."""
        try:
            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            strength = int(params.get("strength", 50))
            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            image.undo_group_start()
            try:
                self._apply_gegl_filter(
                    image,
                    drawable,
                    "gegl:noise-reduction",
                    {
                        "iterations": max(1, strength // 20),
                    },
                )
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _desaturate(self, params):
        """Desaturate a layer."""
        try:
            # GIMP 3.2: LUMINOSITY was renamed to LUMINANCE
            MODE_MAP = {
                "luminosity": Gimp.DesaturateMode.LUMINANCE,
                "luminance": Gimp.DesaturateMode.LUMINANCE,
                "luma": Gimp.DesaturateMode.LUMA,
                "average": Gimp.DesaturateMode.AVERAGE,
                "lightness": Gimp.DesaturateMode.LIGHTNESS,
            }
            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            mode_str = params.get("mode", "luminosity")
            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            mode = MODE_MAP.get(mode_str.lower(), Gimp.DesaturateMode.LUMINANCE)
            image.undo_group_start()
            try:
                pdb = Gimp.get_pdb()
                proc = pdb.lookup_procedure("gimp-drawable-desaturate")
                if proc:
                    cfg = proc.create_config()
                    cfg.set_property("drawable", drawable)
                    cfg.set_property("desaturate-mode", mode)
                    proc.run(cfg)
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _invert_colors(self, params):
        """Invert all colors in a layer."""
        try:
            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            image.undo_group_start()
            try:
                pdb = Gimp.get_pdb()
                proc = pdb.lookup_procedure("gimp-drawable-invert")
                if proc:
                    cfg = proc.create_config()
                    cfg.set_property("drawable", drawable)
                    cfg.set_property("linear", False)
                    proc.run(cfg)
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    # =========================================================================
    # CATEGORY 3 — Resize & Transform
    # =========================================================================

    def _scale_image(self, params):
        """Scale image to exact dimensions."""
        try:
            image_index = int(params.get("image_index", 0))
            width = int(params.get("width"))
            height = int(params.get("height"))
            interpolation = params.get("interpolation", "cubic")
            image = self._get_image(image_index)
            self._interp_from_string(interpolation)
            image.undo_group_start()
            try:
                image.scale(width, height)
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            return {
                "status": "success",
                "results": {"status": "success", "width": width, "height": height},
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _scale_to_fit(self, params):
        """Scale image to fit within a bounding box preserving aspect ratio."""
        try:
            image_index = int(params.get("image_index", 0))
            max_width = int(params.get("max_width"))
            max_height = int(params.get("max_height"))
            interpolation = params.get("interpolation", "cubic")
            image = self._get_image(image_index)
            self._interp_from_string(interpolation)
            src_w = image.get_width()
            src_h = image.get_height()
            aspect = src_w / src_h
            max_aspect = max_width / max_height
            if aspect > max_aspect:
                new_w = max_width
                new_h = max(1, int(max_width / aspect))
            else:
                new_h = max_height
                new_w = max(1, int(max_height * aspect))
            image.undo_group_start()
            try:
                image.scale(new_w, new_h)
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            return {
                "status": "success",
                "results": {"status": "success", "width": new_w, "height": new_h},
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _crop_to_selection(self, params):
        """Crop image to selection bounds."""
        try:
            image_index = int(params.get("image_index", 0))
            autocrop = bool(params.get("autocrop", False))
            image = self._get_image(image_index)
            image.undo_group_start()
            try:
                if autocrop:
                    pdb = Gimp.get_pdb()
                    proc = pdb.lookup_procedure("gimp-image-autocrop")
                    if proc:
                        cfg = proc.create_config()
                        cfg.set_property("image", image)
                        proc.run(cfg)
                else:
                    _ok, non_empty, x1, y1, x2, y2 = Gimp.Selection.bounds(image)
                    if non_empty:
                        image.crop(x2 - x1, y2 - y1, x1, y1)
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _crop_to_rect(self, params):
        """Crop image to explicit rectangle."""
        try:
            image_index = int(params.get("image_index", 0))
            x = int(params.get("x", 0))
            y = int(params.get("y", 0))
            width = int(params.get("width"))
            height = int(params.get("height"))
            image = self._get_image(image_index)
            image.undo_group_start()
            try:
                image.crop(width, height, x, y)
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            return {
                "status": "success",
                "results": {"status": "success", "x": x, "y": y, "width": width, "height": height},
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _rotate_image(self, params):
        """Rotate image by angle.

        Free-angle branch flattens the live stack and requires
        ``confirm_destructive=true`` (track 0009 H1). 90/180/270 lossless does not.
        """
        try:
            image_index = int(params.get("image_index", 0))
            angle = float(params.get("angle", 90))
            image = self._get_image(image_index)
            structural_flatten = False
            rot_map = {
                90.0: Gimp.RotationType.DEGREES90,
                180.0: Gimp.RotationType.DEGREES180,
                270.0: Gimp.RotationType.DEGREES270,
                -90.0: Gimp.RotationType.DEGREES270,
            }
            needs_free_angle_flatten = angle not in rot_map
            if needs_free_angle_flatten:
                self._require_confirm_destructive(params, "rotate_image free-angle flatten")
            image.undo_group_start()
            try:
                if angle in rot_map:
                    image.rotate(rot_map[angle])
                else:
                    import math

                    rad = math.radians(angle)
                    for layer in image.get_layers():
                        pdb = Gimp.get_pdb()
                        proc = pdb.lookup_procedure("gimp-item-transform-rotate-default")
                        if proc:
                            cfg = proc.create_config()
                            cfg.set_property("item", layer)
                            cfg.set_property("angle", rad)
                            cfg.set_property("auto-center", True)
                            cfg.set_property("center-x", 0)
                            cfg.set_property("center-y", 0)
                            proc.run(cfg)
                    image.flatten()
                    structural_flatten = True
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            results = {"status": "success", "angle": angle}
            if structural_flatten:
                gen = self._bump_image_generation(int(image.get_id()))
                results["generation"] = gen
                results["handle"] = self._emit_image_handle(image)
            return {"status": "success", "results": results}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _flip_image(self, params):
        """Flip image horizontally or vertically."""
        try:
            image_index = int(params.get("image_index", 0))
            direction = params.get("direction", "horizontal").lower()
            image = self._get_image(image_index)
            orient = (
                Gimp.OrientationType.HORIZONTAL
                if direction == "horizontal"
                else Gimp.OrientationType.VERTICAL
            )
            image.undo_group_start()
            try:
                image.flip(orient)
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success", "direction": direction}}
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _resize_canvas(self, params):
        """Resize canvas without scaling content.

        Non-transparent fill path flattens the live stack and requires
        ``confirm_destructive=true`` (track 0009 H1). Transparent fill does not.
        """
        try:
            from gi.repository import Gegl

            image_index = int(params.get("image_index", 0))
            new_w = int(params.get("width"))
            new_h = int(params.get("height"))
            anchor = params.get("anchor", "center").lower()
            fill = params.get("fill", "transparent")
            image = self._get_image(image_index)
            src_w = image.get_width()
            src_h = image.get_height()
            # Compute offset based on anchor
            dx = (new_w - src_w) // 2
            dy = (new_h - src_h) // 2
            anchor_offsets = {
                "center": (dx, dy),
                "top-left": (0, 0),
                "top": (dx, 0),
                "top-right": (new_w - src_w, 0),
                "left": (0, dy),
                "right": (new_w - src_w, dy),
                "bottom-left": (0, new_h - src_h),
                "bottom": (dx, new_h - src_h),
                "bottom-right": (new_w - src_w, new_h - src_h),
            }
            off_x, off_y = anchor_offsets.get(anchor, (dx, dy))
            structural_flatten = False
            will_flatten = str(fill).lower() != "transparent"
            if will_flatten:
                self._require_confirm_destructive(
                    params, "resize_canvas non-transparent fill flatten"
                )
            image.undo_group_start()
            try:
                image.resize(new_w, new_h, off_x, off_y)
                if will_flatten:
                    Gimp.context_push()
                    try:
                        bg = Gegl.Color.new(fill)
                        Gimp.context_set_background(bg)
                        image.flatten()
                        structural_flatten = True
                    finally:
                        Gimp.context_pop()
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            results = {
                "status": "success",
                "width": new_w,
                "height": new_h,
                "offset_x": off_x,
                "offset_y": off_y,
            }
            if structural_flatten:
                gen = self._bump_image_generation(int(image.get_id()))
                results["generation"] = gen
                results["handle"] = self._emit_image_handle(image)
            return {
                "status": "success",
                "results": results,
            }
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    # =========================================================================
    # CATEGORY 4 — Selections
    # =========================================================================

    def _select_rectangle(self, params):
        """Create a rectangular selection."""
        try:
            x = int(params.get("x", 0))
            y = int(params.get("y", 0))
            width = int(params.get("width"))
            height = int(params.get("height"))
            operation = params.get("operation", "replace")
            feather = float(params.get("feather", 0))
            try:
                image, _iid = self._resolve_image_from_params(params)
            except _handles.HandleError as e:
                return self._handle_error_response(e)
            op = self._channel_ops_from_string(operation)
            image.select_rectangle(op, x, y, width, height)
            if feather > 0:
                Gimp.Selection.feather(image, feather)
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _select_ellipse(self, params):
        """Create an elliptical selection."""
        try:
            x = int(params.get("x", 0))
            y = int(params.get("y", 0))
            width = int(params.get("width"))
            height = int(params.get("height"))
            operation = params.get("operation", "replace")
            feather = float(params.get("feather", 0))
            try:
                image, _iid = self._resolve_image_from_params(params)
            except _handles.HandleError as e:
                return self._handle_error_response(e)
            op = self._channel_ops_from_string(operation)
            image.select_ellipse(op, x, y, width, height)
            if feather > 0:
                Gimp.Selection.feather(image, feather)
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _select_by_color(self, params):
        """Select by color similarity."""
        try:
            from gi.repository import Gegl

            layer_name = params.get("layer_name", None)
            layer_id = params.get("layer_id", None)
            color_str = params.get("color", "white")
            threshold = int(params.get("threshold", 15))
            operation = params.get("operation", "replace")
            try:
                image, _iid = self._resolve_image_from_params(params)
            except _handles.HandleError as e:
                return self._handle_error_response(e)
            # Prefer layer_id when provided (create_selection layer_handle path).
            # Explicit layer_id must not silently fall back to the active layer.
            if layer_id is not None and layer_name is None:
                drawable = self._resolve_layer(image, None, None, layer_id=int(layer_id))
            else:
                drawable = self._resolve_layer(image, layer_name, None)
            op = self._channel_ops_from_string(operation)
            color = Gegl.Color.new(color_str)
            pdb = Gimp.get_pdb()
            proc = pdb.lookup_procedure("gimp-image-select-color")
            if proc is None:
                raise RuntimeError("PDB procedure 'gimp-image-select-color' not found")
            Gimp.context_push()
            try:
                Gimp.context_set_antialias(True)
                Gimp.context_set_feather(False)
                Gimp.context_set_sample_threshold_int(threshold)
                Gimp.context_set_sample_merged(False)
                Gimp.context_set_sample_transparent(False)
                cfg = proc.create_config()
                cfg.set_property("image", image)
                cfg.set_property("drawable", drawable)
                cfg.set_property("color", color)
                cfg.set_property("operation", op)
                proc.run(cfg)
            finally:
                Gimp.context_pop()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _select_all(self, params):
        """Select entire canvas."""
        try:
            try:
                image, _iid = self._resolve_image_from_params(params)
            except _handles.HandleError as e:
                return self._handle_error_response(e)
            Gimp.Selection.all(image)
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _select_none(self, params):
        """Remove all selections."""
        try:
            try:
                image, _iid = self._resolve_image_from_params(params)
            except _handles.HandleError as e:
                return self._handle_error_response(e)
            Gimp.Selection.none(image)
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _invert_selection(self, params):
        """Invert selection."""
        try:
            image = self._get_image(int(params.get("image_index", 0)))
            Gimp.Selection.invert(image)
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _modify_selection(self, params):
        """Grow/shrink/feather/border/sharpen selection."""
        try:
            image_index = int(params.get("image_index", 0))
            operation = params.get("operation", "grow").lower()
            amount = float(params.get("amount", 0))
            image = self._get_image(image_index)
            OP_MAP = {
                "grow": Gimp.Selection.grow,
                "shrink": Gimp.Selection.shrink,
                "feather": Gimp.Selection.feather,
                "border": Gimp.Selection.border,
                "sharpen": Gimp.Selection.sharpen,
            }
            fn = OP_MAP.get(operation)
            if fn is None:
                return {"status": "error", "error": f"Unknown selection operation: {operation}"}
            if operation == "sharpen":
                fn(image)
            else:
                fn(image, amount)
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    # =========================================================================
    # CATEGORY 5 — Layer Operations
    # =========================================================================

    def _blend_mode_from_string(self, mode_str):
        """Map blend mode name string to Gimp.LayerMode."""
        MODE_MAP = {
            "NORMAL": Gimp.LayerMode.NORMAL,
            "MULTIPLY": Gimp.LayerMode.MULTIPLY,
            "SCREEN": Gimp.LayerMode.SCREEN,
            "OVERLAY": Gimp.LayerMode.OVERLAY,
            "DARKEN": Gimp.LayerMode.DARKEN_ONLY,
            "LIGHTEN": Gimp.LayerMode.LIGHTEN_ONLY,
            "DODGE": Gimp.LayerMode.DODGE,
            "BURN": Gimp.LayerMode.BURN,
            "HARD_LIGHT": Gimp.LayerMode.HARDLIGHT,
            "SOFT_LIGHT": Gimp.LayerMode.SOFTLIGHT,
            "DIFFERENCE": Gimp.LayerMode.DIFFERENCE,
            "HUE": Gimp.LayerMode.HSV_HUE,
            "SATURATION": Gimp.LayerMode.HSV_SATURATION,
            "COLOR": Gimp.LayerMode.HSL_COLOR,
            "LUMINOSITY": Gimp.LayerMode.HSV_VALUE,
            "DISSOLVE": Gimp.LayerMode.DISSOLVE,
        }
        return MODE_MAP.get(mode_str.upper(), Gimp.LayerMode.NORMAL)

    def _create_layer(self, params):
        """Create and insert a new layer."""
        try:
            from gi.repository import Gegl

            image_index = int(params.get("image_index", 0))
            name = params.get("name", "New Layer")
            opacity = float(params.get("opacity", 100))
            blend_mode = params.get("blend_mode", "NORMAL")
            position = int(params.get("position", -1))
            fill = params.get("fill", "transparent")
            image = self._get_image(image_index)
            width = int(params.get("width") or image.get_width())
            height = int(params.get("height") or image.get_height())
            mode = self._blend_mode_from_string(blend_mode)
            # Determine layer type
            base_type = image.get_base_type()
            layer_type = (
                Gimp.ImageType.RGBA_IMAGE
                if base_type == Gimp.ImageBaseType.RGB
                else Gimp.ImageType.GRAYA_IMAGE
            )
            image.undo_group_start()
            try:
                layer = Gimp.Layer.new(image, name, width, height, layer_type, opacity, mode)
                image.insert_layer(layer, None, position)
                Gimp.context_push()
                try:
                    if fill.lower() == "transparent":
                        layer.add_alpha()
                        Gimp.Drawable.edit_fill(layer, Gimp.FillType.TRANSPARENT)
                    else:
                        bg = Gegl.Color.new(fill)
                        Gimp.context_set_background(bg)
                        Gimp.Drawable.edit_fill(layer, Gimp.FillType.BACKGROUND)
                finally:
                    Gimp.context_pop()
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            image_id = int(image.get_id())
            gen = self._bump_image_generation(image_id)
            return {
                "status": "success",
                "results": {
                    "layer_name": layer.get_name(),
                    "layer_id": layer.get_id(),
                    "width": width,
                    "height": height,
                    "position": position,
                    "generation": gen,
                    "handle": self._emit_item_handle(layer, image_id),
                },
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _duplicate_layer(self, params):
        """Duplicate a layer."""
        try:
            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            image = self._get_image(image_index)
            layer = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                layer_id=self._layer_id_from_params(params),
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            layers = image.get_layers()
            position = layers.index(layer) if layer in layers else 0
            image.undo_group_start()
            try:
                new_layer = layer.copy()
                image.insert_layer(new_layer, None, position)
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            image_id = int(image.get_id())
            gen = self._bump_image_generation(image_id)
            return {
                "status": "success",
                "results": {
                    "layer_name": new_layer.get_name(),
                    "layer_id": new_layer.get_id(),
                    "generation": gen,
                    "handle": self._emit_item_handle(new_layer, image_id),
                },
            }
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _delete_layer(self, params):
        """Delete a layer."""
        try:
            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            layer_index = params.get("layer_index", None)
            if layer_index is not None:
                layer_index = int(layer_index)
            image = self._get_image(image_index)
            layer = self._resolve_mutable_layer(
                image,
                layer_name,
                layer_index,
                layer_id=self._layer_id_from_params(params),
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            image.undo_group_start()
            try:
                image.remove_layer(layer)
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            image_id = int(image.get_id())
            gen = self._bump_image_generation(image_id)
            return {
                "status": "success",
                "results": {
                    "status": "success",
                    "generation": gen,
                    "handle": self._emit_image_handle(image),
                },
            }
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _rename_layer(self, params):
        """Rename a layer (non-structural — no generation bump)."""
        try:
            image_index = int(params.get("image_index", 0))
            old_name = params.get("old_name", None)
            layer_index = params.get("layer_index", None)
            new_name = params.get("new_name", "")
            if layer_index is not None:
                layer_index = int(layer_index)
            image = self._get_image(image_index)
            layer = self._resolve_mutable_layer(
                image,
                old_name,
                layer_index,
                layer_id=self._layer_id_from_params(params),
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            prev_name = layer.get_name()
            layer.set_name(new_name)
            Gimp.displays_flush()
            return {"status": "success", "results": {"old_name": prev_name, "new_name": new_name}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _set_layer_properties(self, params):
        """Set layer opacity, blend mode, and/or visibility."""
        try:
            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            layer_index = params.get("layer_index", None)
            opacity = params.get("opacity", None)
            blend_mode = params.get("blend_mode", None)
            visible = params.get("visible", None)
            if layer_index is not None:
                layer_index = int(layer_index)
            image = self._get_image(image_index)
            layer = self._resolve_mutable_layer(
                image,
                layer_name,
                layer_index,
                layer_id=self._layer_id_from_params(params),
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            image.undo_group_start()
            try:
                if opacity is not None:
                    layer.set_opacity(float(opacity))
                if blend_mode is not None:
                    layer.set_mode(self._blend_mode_from_string(blend_mode))
                if visible is not None:
                    layer.set_visible(bool(visible))
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _reorder_layer(self, params):
        """Move a layer to a new stack position."""
        try:
            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            layer_index = params.get("layer_index", None)
            new_position = int(params.get("new_position", 0))
            if layer_index is not None:
                layer_index = int(layer_index)
            image = self._get_image(image_index)
            layer = self._resolve_mutable_layer(
                image,
                layer_name,
                layer_index,
                layer_id=self._layer_id_from_params(params),
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            image.undo_group_start()
            try:
                image.reorder_item(layer, None, new_position)
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            image_id = int(image.get_id())
            gen = self._bump_image_generation(image_id)
            return {
                "status": "success",
                "results": {
                    "status": "success",
                    "generation": gen,
                    "handle": self._emit_item_handle(layer, image_id),
                },
            }
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _flatten_image(self, params):
        """Flatten all layers (live document — requires confirm_destructive)."""
        try:
            self._require_confirm_destructive(params, "flatten_image")
            image = self._get_image(int(params.get("image_index", 0)))
            image.undo_group_start()
            try:
                image.flatten()
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            image_id = int(image.get_id())
            gen = self._bump_image_generation(image_id)
            return {
                "status": "success",
                "results": {
                    "status": "success",
                    "generation": gen,
                    "handle": self._emit_image_handle(image),
                },
            }
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _merge_visible_layers(self, params):
        """Merge visible layers (live agent tool — bumps generation; confirm_destructive)."""
        try:
            self._require_confirm_destructive(params, "merge_visible_layers")
            image = self._get_image(int(params.get("image_index", 0)))
            image.undo_group_start()
            try:
                merged = image.merge_visible_layers(Gimp.MergeType.CLIP_TO_IMAGE)
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            image_id = int(image.get_id())
            gen = self._bump_image_generation(image_id)
            return {
                "status": "success",
                "results": {
                    "layer_name": merged.get_name(),
                    "layer_id": merged.get_id(),
                    "generation": gen,
                    "handle": self._emit_item_handle(merged, image_id),
                },
            }
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _list_layers(self, params):
        """List all layers with properties."""
        try:
            image = self._get_image(int(params.get("image_index", 0)))
            layers = image.get_layers()
            layer_list = []
            for i, layer in enumerate(layers):
                try:
                    layer_list.append(
                        {
                            "index": i,
                            "name": layer.get_name(),
                            "id": layer.get_id(),
                            "visible": layer.get_visible(),
                            "opacity": layer.get_opacity(),
                            "blend_mode": str(layer.get_mode()),
                            "width": layer.get_width(),
                            "height": layer.get_height(),
                            "has_alpha": layer.has_alpha(),
                            "offsets": list(layer.get_offsets()),
                        }
                    )
                except Exception as ex:
                    layer_list.append({"index": i, "error": str(ex)})
            return {
                "status": "success",
                "results": {"layers": layer_list, "count": len(layer_list)},
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    # =========================================================================
    # CATEGORY 5b — Source_Immutable policy + checkpoints (track 0009)
    # =========================================================================

    def _item_is_group(self, item):
        try:
            if hasattr(item, "is_group") and callable(item.is_group):
                return bool(item.is_group())
        except Exception:
            pass
        try:
            tname = type(item).__name__
            if "GroupLayer" in tname:
                return True
        except Exception:
            pass
        return False

    def _item_has_policy_parasite(self, item):
        """True if item carries the Source_Immutable parasite marker."""
        name = _policy.PARASITE_SOURCE_IMMUTABLE
        try:
            if hasattr(item, "get_parasite"):
                p = item.get_parasite(name)
                if p is not None:
                    return True
            if hasattr(item, "find_parasite"):
                p = item.find_parasite(name)
                if p is not None:
                    return True
        except Exception:
            pass
        return False

    def _attach_policy_parasite(self, item):
        """Attach gimp-mcp:source-immutable parasite; fail closed if API missing."""
        name = _policy.PARASITE_SOURCE_IMMUTABLE
        if not hasattr(Gimp, "Parasite") or not hasattr(Gimp.Parasite, "new"):
            raise RuntimeError("Gimp.Parasite.new unavailable — cannot mark Source_Immutable group")
        if not hasattr(item, "attach_parasite"):
            raise RuntimeError(
                "item.attach_parasite unavailable — cannot mark Source_Immutable group"
            )
        # flags: 0 = temporary / not persistent to XCF is wrong; use PERSISTENT if available
        flags = 0
        try:
            if hasattr(Gimp, "PARASITE_PERSISTENT"):
                flags = int(Gimp.PARASITE_PERSISTENT)
            elif hasattr(Gimp.Parasite, "PERSISTENT"):
                flags = int(Gimp.Parasite.PERSISTENT)
        except Exception:
            flags = 1  # common PERSISTENT value historically
        data = b"1"
        parasite = Gimp.Parasite.new(name, flags, data)
        item.attach_parasite(parasite)

    def _create_source_immutable_group(self, image):
        """Find or create parasite-marked Source_Immutable group.

        Name collision without parasite → POLICY_DENIED.
        """
        group_name = _policy.SOURCE_IMMUTABLE_GROUP_NAME
        # Search root layers for existing group by name
        for layer in image.get_layers() or []:
            try:
                if layer.get_name() != group_name:
                    continue
            except Exception:
                continue
            if not self._item_is_group(layer):
                raise _sec.SecurityError(
                    _sec.CODE_POLICY_DENIED,
                    f"Layer named {group_name!r} exists but is not a group",
                )
            if not self._item_has_policy_parasite(layer):
                raise _sec.SecurityError(
                    _sec.CODE_POLICY_DENIED,
                    f"Name collision: {group_name!r} exists without "
                    f"parasite {_policy.PARASITE_SOURCE_IMMUTABLE!r}",
                )
            return layer

        # Create new group at bottom of root stack
        if not hasattr(Gimp, "GroupLayer") or not hasattr(Gimp.GroupLayer, "new"):
            raise RuntimeError("Gimp.GroupLayer.new unavailable")
        group = Gimp.GroupLayer.new(image, group_name)
        # position -1 often means append; use end of root stack
        n = len(image.get_layers() or [])
        image.insert_layer(group, None, n)
        self._attach_policy_parasite(group)
        # Prefer hidden + content-locked group
        try:
            ok = group.set_visible(False)
            self._gimp_bool_or_fail(ok, "Source_Immutable group set_visible(False)")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Source_Immutable group set_visible failed: {e}") from e
        try:
            if hasattr(group, "set_lock_content"):
                ok = group.set_lock_content(True)
                self._gimp_bool_or_fail(ok, "Source_Immutable group set_lock_content(True)")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Source_Immutable group set_lock_content failed: {e}") from e
        return group

    def _group_has_children(self, group):
        """True if group layer has at least one child (prior ensure applied)."""
        try:
            kids = self._layer_children(group) if group is not None else []
            return bool(kids)
        except Exception:
            return False

    def _layer_under_policy_group(self, layer, policy_group):
        """True if layer is already a descendant of the marked policy group."""
        try:
            group_id = int(policy_group.get_id())
        except Exception:
            return False
        try:
            parent = layer.get_parent() if hasattr(layer, "get_parent") else None
        except Exception:
            parent = None
        visited = set()
        while parent is not None:
            try:
                pid = int(parent.get_id())
            except Exception:
                break
            if pid in visited:
                break
            visited.add(pid)
            if pid == group_id:
                return True
            try:
                parent = parent.get_parent() if hasattr(parent, "get_parent") else None
            except Exception:
                break
        return False

    def _unique_working_name(self, image, base_name):
        """Return a non-colliding working-layer name: base + ' (working)' [+ n]."""
        candidate = f"{base_name} (working)"
        existing = set()
        try:
            for lyr in image.get_layers() or []:
                try:
                    existing.add(str(lyr.get_name()))
                except Exception:
                    continue
                # also scan one level of children
                try:
                    if self._item_is_group(lyr) and hasattr(lyr, "get_children"):
                        for ch in lyr.get_children() or []:
                            try:
                                existing.add(str(ch.get_name()))
                            except Exception:
                                pass
                except Exception:
                    pass
        except Exception:
            pass
        if candidate not in existing:
            return candidate
        n = 2
        while f"{candidate} {n}" in existing:
            n += 1
        return f"{candidate} {n}"

    def _lock_source_layer(self, layer):
        """Hide + lock content/position/visibility on original; check gboolean each."""
        ok = layer.set_visible(False)
        self._gimp_bool_or_fail(ok, "set_visible(False) on protected layer")
        if not hasattr(layer, "set_lock_content"):
            raise RuntimeError("set_lock_content unavailable")
        ok = layer.set_lock_content(True)
        self._gimp_bool_or_fail(ok, "set_lock_content(True) on protected layer")
        if not hasattr(layer, "set_lock_position"):
            raise RuntimeError("set_lock_position unavailable")
        ok = layer.set_lock_position(True)
        self._gimp_bool_or_fail(ok, "set_lock_position(True) on protected layer")
        if not hasattr(layer, "set_lock_visibility"):
            raise RuntimeError("set_lock_visibility unavailable")
        ok = layer.set_lock_visibility(True)
        self._gimp_bool_or_fail(ok, "set_lock_visibility(True) on protected layer")

    def _ensure_source_immutable(self, params):
        """Protect root source layers: copy working → reparent original → lock/hide.

        Locked order per layer (§7.1):
          1. working = layer.copy()
          2. insert_layer(working, None, original_index)
          3. reorder_item(original, parent=group, position=-1)
          4. set_visible(False); set_lock_content/position/visibility(True)
        Single generation bump after ALL layers; emit handles after.
        """
        try:
            try:
                image, image_id = self._resolve_image_from_params(params)
            except _handles.HandleError as e:
                return self._handle_error_response(e)
            # Optional explicit layer ids (root non-group only when omitted)
            raw_ids = params.get("layer_ids") or params.get("item_ids") or None
            explicit_ids = None
            if raw_ids is not None:
                if not isinstance(raw_ids, (list, tuple)):
                    return _sec.make_error(
                        _sec.CODE_INVALID_HANDLE
                        if hasattr(_sec, "CODE_INVALID_HANDLE")
                        else _sec.CODE_INTERNAL,
                        "layer_ids must be a list of integers",
                    )
                explicit_ids = [int(x) for x in raw_ids]

            protected_out = []
            working_out = []
            skipped = []
            noop = False

            image.undo_group_start()
            try:
                group = self._create_source_immutable_group(image)
                # Hydrate session set from any prior XCF/session group members
                self._hydrate_protected_from_group(image, image_id)

                # Snapshot root candidates (root stack changes as we process)
                roots = list(image.get_layers() or [])
                targets = []
                working_set = self._working_item_ids.get(image_id) or set()
                protected_set = self._protected_item_ids.get(image_id) or set()
                for layer in roots:
                    try:
                        lid = int(layer.get_id())
                    except Exception:
                        continue
                    if self._item_is_group(layer):
                        # skip groups (including the policy group itself)
                        continue
                    if explicit_ids is not None and lid not in explicit_ids:
                        continue
                    if self._layer_under_policy_group(layer, group):
                        skipped.append({"item_id": lid, "reason": "already_under_policy_group"})
                        continue
                    if lid in protected_set:
                        skipped.append({"item_id": lid, "reason": "already_protected"})
                        continue
                    # Session working copies from a prior ensure (idempotent)
                    if lid in working_set:
                        skipped.append({"item_id": lid, "reason": "working_copy"})
                        continue
                    # Name-based defense only after policy group already has
                    # children (prior ensure). Avoids first-run skip of a
                    # source intentionally/accidentally named "... (working)".
                    try:
                        lname = str(layer.get_name() or "")
                    except Exception:
                        lname = ""
                    if _policy.is_working_layer_name(lname) and (
                        protected_set or self._group_has_children(group)
                    ):
                        skipped.append(
                            {"item_id": lid, "reason": "working_copy_name", "name": lname}
                        )
                        continue
                    targets.append(layer)

                # True no-op: nothing to protect → no gen bump (idempotent)
                if not targets:
                    noop = True
                else:
                    for layer in targets:
                        try:
                            orig_id = int(layer.get_id())
                            orig_name = str(layer.get_name() or f"layer-{orig_id}")
                        except Exception as e:
                            raise RuntimeError(f"cannot read layer for protect: {e}") from e

                        # original_index among current root layers
                        roots_now = list(image.get_layers() or [])
                        try:
                            original_index = roots_now.index(layer)
                        except ValueError:
                            # not at root anymore
                            skipped.append({"item_id": orig_id, "reason": "not_root"})
                            continue

                        # 1. copy working
                        working = layer.copy()
                        # 2. insert working into original slot (working takes the slot)
                        image.insert_layer(working, None, original_index)
                        try:
                            wname = self._unique_working_name(image, orig_name)
                            working.set_name(wname)
                        except Exception:
                            pass
                        # 3. reparent original into policy group
                        if not hasattr(image, "reorder_item"):
                            raise RuntimeError("image.reorder_item unavailable")
                        image.reorder_item(layer, group, -1)
                        # 4. hide + lock original
                        self._lock_source_layer(layer)

                        # register protected + working for session idempotency
                        self._protected_item_ids.setdefault(image_id, set()).add(orig_id)

                        protected_out.append(
                            {
                                "item_id": orig_id,
                                "name": orig_name,
                                "handle": self._emit_item_handle(layer, image_id),
                            }
                        )
                        try:
                            wid = int(working.get_id())
                            self._working_item_ids.setdefault(image_id, set()).add(wid)
                            working_out.append(
                                {
                                    "item_id": wid,
                                    "name": working.get_name(),
                                    "handle": self._emit_item_handle(working, image_id),
                                }
                            )
                        except Exception:
                            working_out.append({"item_id": None, "name": None})
            finally:
                image.undo_group_end()

            if noop:
                gen = self._image_generation(image_id)
                return {
                    "status": "success",
                    "results": {
                        "status": "success",
                        "image_id": image_id,
                        "generation": gen,
                        "handle": self._emit_image_handle(image),
                        "protected": [],
                        "working": [],
                        "skipped": skipped,
                        "group_name": _policy.SOURCE_IMMUTABLE_GROUP_NAME,
                        "protected_item_ids": sorted(
                            self._protected_item_ids.get(image_id) or set()
                        ),
                        "noop": True,
                    },
                }

            # Single generation bump after ALL layers (AI2 BS3) — only if work done
            gen = self._bump_image_generation(image_id)
            Gimp.displays_flush()

            # Re-emit handles after gen bump so generation is current
            for entry in protected_out:
                try:
                    entry["handle"] = self._emit_item_handle_ids(int(entry["item_id"]), image_id)
                except Exception:
                    pass
            for entry in working_out:
                if entry.get("item_id") is not None:
                    try:
                        entry["handle"] = self._emit_item_handle_ids(
                            int(entry["item_id"]), image_id
                        )
                    except Exception:
                        pass

            return {
                "status": "success",
                "results": {
                    "status": "success",
                    "image_id": image_id,
                    "generation": gen,
                    "handle": self._emit_image_handle(image),
                    "protected": protected_out,
                    "working": working_out,
                    "skipped": skipped,
                    "group_name": _policy.SOURCE_IMMUTABLE_GROUP_NAME,
                    "protected_item_ids": sorted(self._protected_item_ids.get(image_id) or set()),
                    "noop": False,
                },
            }
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return _sec.redact_error(e)

    def _collect_layers_for_sidecar(self, image, image_id):
        """Build flat layer inventory for checkpoint sidecar (tattoos write-only)."""
        out = []
        protected = self._protected_item_ids.get(int(image_id)) or set()

        def walk(layer, parent_item_id, depth, visited):
            if depth > 32:
                return
            try:
                lid = int(layer.get_id())
            except Exception:
                return
            if lid in visited:
                return
            visited.add(lid)
            kind = self._orient_classify_kind(layer)
            try:
                name = str(layer.get_name() or f"layer-{lid}")
            except Exception:
                name = f"layer-{lid}"
            entry = {
                "item_id": lid,
                "name": name,
                "kind": kind,
                "parent_item_id": parent_item_id,
                "protected": lid in protected,
            }
            try:
                if hasattr(layer, "get_tattoo"):
                    entry["tattoo"] = int(layer.get_tattoo())
            except Exception:
                pass
            out.append(entry)
            for child in self._layer_children(layer):
                walk(child, lid, depth + 1, visited)

        visited: set = set()
        for root in image.get_layers() or []:
            walk(root, None, 0, visited)
        return out

    def _pdb_status_is_success(self, result):
        """Best-effort: True if *result* looks like PDB SUCCESS (or unknown)."""
        if result is None:
            return True  # no status surface — rely on file checks
        try:
            status = result.index(0)
        except Exception:
            try:
                status = result[0]
            except Exception:
                return True
        try:
            success = Gimp.PDBStatusType.SUCCESS
            if status == success:
                return True
            # Some builds return int enum values
            if int(status) == int(success):
                return True
            # Explicit non-success statuses fail closed
            for name in ("EXECUTION_ERROR", "CALLING_ERROR", "PASS_THROUGH", "CANCEL"):
                if hasattr(Gimp.PDBStatusType, name) and status == getattr(
                    Gimp.PDBStatusType, name
                ):
                    return False
            return False
        except Exception:
            return True

    def _save_xcf_to_path(self, image, file_path):
        """Save image as XCF to an already-jailed absolute path.

        Writes to a ``.partial`` sibling first, verifies non-empty bytes, then
        ``os.replace`` into the final path so a failed overwrite cannot pair a
        stale XCF with a fresh sidecar (Codex P1). Full atomic product semantics
        remain **0013**.
        """
        from gi.repository import Gio

        xcf_path = os.fspath(file_path)
        tmp_path = xcf_path + ".partial"
        try:
            if os.path.isfile(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            gio_file = Gio.File.new_for_path(tmp_path)
            pdb = Gimp.get_pdb()
            proc = pdb.lookup_procedure("gimp-xcf-save")
            if proc:
                cfg = proc.create_config()
                cfg.set_property("image", image)
                cfg.set_property("file", gio_file)
                result = proc.run(cfg)
                if not self._pdb_status_is_success(result):
                    raise RuntimeError(f"gimp-xcf-save failed (status={result!r})")
            else:
                Gimp.file_overwrite(Gimp.RunMode.NONINTERACTIVE, image, gio_file)
            if not os.path.isfile(tmp_path) or os.path.getsize(tmp_path) <= 0:
                raise RuntimeError(f"XCF save produced empty/missing file: {tmp_path}")
            os.replace(tmp_path, xcf_path)
            if not os.path.isfile(xcf_path) or os.path.getsize(xcf_path) <= 0:
                raise RuntimeError(f"XCF replace did not yield file: {xcf_path}")
            return xcf_path
        finally:
            if os.path.isfile(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def _checkpoint_create(self, params):
        """Create a workspace-jailed XCF checkpoint + JSON sidecar.

        Sidecar is written **only after** XCF save succeeds. ``xcf_sha256`` is
        integrity of as-written bytes (not reproducibility).
        """
        try:
            if not self.workspace_root:
                return _sec.make_error(
                    _sec.CODE_PATH_DENIED,
                    "checkpoint_create requires GIMP_WORKSPACE_ROOT",
                )
            raw_label = params.get("label", "")
            try:
                label = _policy.sanitize_checkpoint_label(str(raw_label))
            except ValueError as e:
                return _sec.make_error(_sec.CODE_POLICY_DENIED, str(e))

            overwrite = _exp.coerce_bool(params.get("overwrite", False), default=False)
            include_orient = _exp.coerce_bool(
                params.get("include_orient_snapshot", False), default=False
            )
            try:
                image, image_id = self._resolve_image_from_params(params)
            except _handles.HandleError as e:
                return self._handle_error_response(e)
            gen = self._image_generation(image_id)

            # Prefer absolute under workspace_root then jail
            intended_dir = Path(str(self.workspace_root)) / _policy.CHECKPOINT_DIR_NAME / label
            intended_xcf = intended_dir / _policy.CHECKPOINT_XCF_NAME
            intended_json = intended_dir / _policy.CHECKPOINT_JSON_NAME

            safe_dir, err = self._jail_path(str(intended_dir))
            if err is not None:
                return err
            safe_xcf, err = self._jail_path(str(intended_xcf))
            if err is not None:
                return err
            safe_json, err = self._jail_path(str(intended_json))
            if err is not None:
                return err

            if (safe_dir.exists() or safe_xcf.exists()) and not overwrite:
                return _sec.make_error(
                    _sec.CODE_CHECKPOINT_EXISTS,
                    f"checkpoint label {label!r} already exists (pass overwrite=true)",
                )

            safe_dir.mkdir(parents=True, exist_ok=True)

            # Save XCF first — sidecar only on success (AI2 BS5)
            self._save_xcf_to_path(image, safe_xcf)
            if not safe_xcf.is_file():
                return _sec.make_error(
                    _sec.CODE_INTERNAL,
                    f"XCF save did not produce file: {safe_xcf}",
                )
            digest = _policy.sha256_file(safe_xcf)

            try:
                img_name = None
                src = self._orient_source_path(image)
                if src:
                    img_name = os.path.basename(src)
            except Exception:
                img_name = None

            layers = self._collect_layers_for_sidecar(image, image_id)
            image_meta = {
                "image_id": image_id,
                "generation": int(gen),
                "width": int(image.get_width()),
                "height": int(image.get_height()),
            }
            if img_name:
                image_meta["name"] = img_name

            sidecar = _policy.build_sidecar(
                label=label,
                session_epoch=int(self.session_epoch),
                image=image_meta,
                xcf_path=str(safe_xcf),
                xcf_sha256=digest,
                layers=layers,
            )
            if include_orient:
                # M6 default False. True is honesty-only: no full orient payload
                # embedded (agent must orient_workspace after restore / reopen).
                sidecar["orient_note"] = (
                    "include_orient_snapshot=true: note-only; full orient dump not "
                    "embedded in sidecar (call orient_workspace after restore)"
                )

            try:
                validated = _policy.validate_sidecar(sidecar)
            except ValueError as e:
                return _sec.make_error(
                    _sec.CODE_INTERNAL,
                    f"checkpoint sidecar validation failed after XCF save: {e}",
                )

            safe_json.write_text(
                json.dumps(validated, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            return {
                "status": "success",
                "results": {
                    "status": "success",
                    "label": label,
                    "xcf_path": str(safe_xcf),
                    "json_path": str(safe_json),
                    "xcf_sha256": digest,
                    "generation": int(gen),
                    "handle": self._emit_image_handle(image),
                    "image_id": image_id,
                    "layers_count": len(layers),
                },
            }
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return _sec.redact_error(e)

    def _checkpoint_restore(self, params):
        """Open a checkpoint XCF alongside (default); optional close_prior.

        Returns a **new** image handle. Prior handles for the closed/replaced
        image become invalid. Agent **must** re-run ``orient_workspace``.
        Sidecar tattoos are **not** used for rebind in 0009.
        """
        try:
            if not self.workspace_root:
                return _sec.make_error(
                    _sec.CODE_PATH_DENIED,
                    "checkpoint_restore requires GIMP_WORKSPACE_ROOT",
                )
            raw_label = params.get("label", "")
            try:
                label = _policy.sanitize_checkpoint_label(str(raw_label))
            except ValueError as e:
                return _sec.make_error(_sec.CODE_POLICY_DENIED, str(e))

            close_prior = _exp.coerce_bool(params.get("close_prior", False), default=False)
            prior_index = params.get("image_index", None)
            prior_handle = params.get("handle", None)
            verify_hash = _exp.coerce_bool(params.get("verify_hash", True), default=True)

            intended_dir = Path(str(self.workspace_root)) / _policy.CHECKPOINT_DIR_NAME / label
            intended_xcf = intended_dir / _policy.CHECKPOINT_XCF_NAME
            intended_json = intended_dir / _policy.CHECKPOINT_JSON_NAME

            safe_xcf, err = self._jail_path(str(intended_xcf))
            if err is not None:
                return err
            safe_json, err = self._jail_path(str(intended_json))
            if err is not None:
                return err

            if not safe_xcf.is_file():
                return _sec.make_error(
                    _sec.CODE_CHECKPOINT_NOT_FOUND,
                    f"checkpoint XCF not found for label {label!r}: {safe_xcf}",
                )

            hash_status = "skipped"
            if verify_hash and safe_json.is_file():
                try:
                    data = json.loads(safe_json.read_text(encoding="utf-8"))
                    expected = data.get("xcf_sha256")
                    actual = _policy.sha256_file(safe_xcf)
                    if isinstance(expected, str) and expected.lower() != actual.lower():
                        return _sec.make_error(
                            _sec.CODE_CHECKPOINT_CORRUPTED,
                            f"checkpoint XCF hash mismatch for {label!r} "
                            f"(integrity soft-check; not reproducibility)",
                        )
                    hash_status = "matched"
                except _sec.SecurityError:
                    raise
                except Exception as e:
                    # Soft: missing/unreadable sidecar does not block open
                    hash_status = f"soft_skip:{e}"

            from gi.repository import Gio

            xcf_path = os.fspath(safe_xcf)
            gio_file = Gio.File.new_for_path(xcf_path)
            image = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, gio_file)
            if image is None:
                return {
                    "status": "error",
                    "error": f"Could not open checkpoint XCF: {safe_xcf}",
                }
            display = Gimp.Display.new(image)
            Gimp.displays_flush()
            new_id = int(image.get_id())
            gen = self._seed_image_generation(new_id, 1)
            # Rebuild durable Source_Immutable session set from parasite group
            hydrated = self._hydrate_protected_from_group(image, new_id)

            closed_prior = None
            if close_prior:
                try:
                    prior = None
                    if prior_handle is not None:
                        # Explicit prior handle must not silently no-op on STALE/FOREIGN
                        try:
                            prior, _pid = self._resolve_image_from_params({"handle": prior_handle})
                        except _handles.HandleError as e:
                            return self._handle_error_response(e)
                    elif prior_index is not None:
                        prior = self._get_image(int(prior_index))
                    if prior is not None and int(prior.get_id()) != new_id:
                        closed_prior = int(prior.get_id())
                        for display_obj in Gimp.get_displays() or []:
                            try:
                                if (
                                    display_obj.get_image() is not None
                                    and int(display_obj.get_image().get_id()) == closed_prior
                                ):
                                    Gimp.Display.delete(display_obj)
                            except Exception:
                                pass
                        prior.delete()
                        self._drop_image_generation(closed_prior)
                except Exception as e:
                    # Open succeeded; report close failure without discarding new image
                    return {
                        "status": "success",
                        "results": {
                            "status": "success",
                            "label": label,
                            "image_id": new_id,
                            "generation": gen,
                            "handle": self._emit_image_handle(image),
                            "display_opened": display is not None,
                            "hash_status": hash_status,
                            "close_prior_error": str(e),
                            "protected_hydrated": sorted(hydrated),
                            "note": (
                                "Restored as NEW image. Prior handles for any closed "
                                "image are invalid. Call orient_workspace; tattoos are "
                                "not rebound in 0009. Source_Immutable re-hydrated."
                            ),
                        },
                    }

            return {
                "status": "success",
                "results": {
                    "status": "success",
                    "label": label,
                    "image_id": new_id,
                    "generation": gen,
                    "handle": self._emit_image_handle(image),
                    "display_opened": display is not None,
                    "hash_status": hash_status,
                    "closed_prior_image_id": closed_prior,
                    "protected_hydrated": sorted(hydrated),
                    "note": (
                        "Restored as NEW image handle. Prior handles for the previous "
                        "document are invalid if closed. Agent must call "
                        "orient_workspace. Sidecar tattoos are write-only (no rebind). "
                        "Source_Immutable descendants re-hydrated into session deny set."
                    ),
                },
            }
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return _sec.redact_error(e)

    # =========================================================================
    # CATEGORY 6 — Color & Paint
    # =========================================================================

    def _fill_layer(self, params):
        """Fill entire layer with color."""
        try:
            from gi.repository import Gegl

            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            color_str = params.get("color", "white")
            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            image.undo_group_start()
            Gimp.context_push()
            try:
                Gimp.Selection.all(image)
                fg = Gegl.Color.new(color_str)
                Gimp.context_set_foreground(fg)
                Gimp.Drawable.edit_fill(drawable, Gimp.FillType.FOREGROUND)
                Gimp.Selection.none(image)
            finally:
                Gimp.context_pop()
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _fill_selection(self, params):
        """Fill current selection with color or transparency."""
        try:
            from gi.repository import Gegl

            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            fill_type = (params.get("fill_type") or "foreground").lower()
            color_str = params.get("color", "white")
            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            image.undo_group_start()
            Gimp.context_push()
            try:
                if fill_type == "transparent":
                    # Ensure layer has alpha channel before deleting pixels
                    if not drawable.has_alpha():
                        drawable.add_alpha()
                    Gimp.Drawable.edit_clear(drawable)
                elif fill_type == "background":
                    Gimp.Drawable.edit_fill(drawable, Gimp.FillType.BACKGROUND)
                elif fill_type == "pattern":
                    Gimp.Drawable.edit_fill(drawable, Gimp.FillType.PATTERN)
                else:
                    # foreground (default) or explicit color
                    fg = Gegl.Color.new(color_str)
                    Gimp.context_set_foreground(fg)
                    Gimp.Drawable.edit_fill(drawable, Gimp.FillType.FOREGROUND)
            finally:
                Gimp.context_pop()
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _set_colors(self, params):
        """Set foreground and/or background color."""
        try:
            from gi.repository import Gegl

            fg_str = params.get("foreground", None)
            bg_str = params.get("background", None)
            if fg_str is not None:
                Gimp.context_set_foreground(Gegl.Color.new(fg_str))
            if bg_str is not None:
                Gimp.context_set_background(Gegl.Color.new(bg_str))
            return {"status": "success", "results": {"foreground": fg_str, "background": bg_str}}
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _draw_line(self, params):
        """Draw a straight line."""
        try:
            from gi.repository import Gegl

            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            x1 = float(params.get("x1", 0))
            y1 = float(params.get("y1", 0))
            x2 = float(params.get("x2", 0))
            y2 = float(params.get("y2", 0))
            color_str = params.get("color", None)
            line_width = float(params.get("width", 2.0))
            tool = params.get("tool", "pencil").lower()
            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            image.undo_group_start()
            Gimp.context_push()
            try:
                if color_str:
                    Gimp.context_set_foreground(Gegl.Color.new(color_str))
                Gimp.context_set_brush_size(line_width)
                Gimp.context_set_opacity(100.0)
                coords = [x1, y1, x2, y2]
                if tool == "paintbrush":
                    Gimp.paintbrush_default(drawable, coords)
                else:
                    Gimp.pencil(drawable, coords)
            finally:
                Gimp.context_pop()
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _draw_rectangle(self, params):
        """Draw a rectangle outline."""
        try:
            from gi.repository import Gegl

            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            x = int(params.get("x", 0))
            y = int(params.get("y", 0))
            width = int(params.get("width"))
            height = int(params.get("height"))
            color_str = params.get("color", None)
            line_width = float(params.get("line_width", 2.0))
            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            image.undo_group_start()
            Gimp.context_push()
            try:
                if color_str:
                    Gimp.context_set_foreground(Gegl.Color.new(color_str))
                Gimp.context_set_stroke_method(Gimp.StrokeMethod.LINE)
                Gimp.context_set_line_width(line_width)
                Gimp.context_set_opacity(100.0)
                image.select_rectangle(Gimp.ChannelOps.REPLACE, x, y, width, height)
                pdb = Gimp.get_pdb()
                proc = pdb.lookup_procedure("gimp-drawable-edit-stroke-selection")
                if proc:
                    cfg = proc.create_config()
                    cfg.set_property("drawable", drawable)
                    proc.run(cfg)
                Gimp.Selection.none(image)
            finally:
                Gimp.context_pop()
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _draw_ellipse(self, params):
        """Draw an ellipse outline."""
        try:
            from gi.repository import Gegl

            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            x = int(params.get("x", 0))
            y = int(params.get("y", 0))
            width = int(params.get("width"))
            height = int(params.get("height"))
            color_str = params.get("color", None)
            line_width = float(params.get("line_width", 2.0))
            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            image.undo_group_start()
            Gimp.context_push()
            try:
                if color_str:
                    Gimp.context_set_foreground(Gegl.Color.new(color_str))
                Gimp.context_set_stroke_method(Gimp.StrokeMethod.LINE)
                Gimp.context_set_line_width(line_width)
                Gimp.context_set_opacity(100.0)
                image.select_ellipse(Gimp.ChannelOps.REPLACE, x, y, width, height)
                pdb = Gimp.get_pdb()
                proc = pdb.lookup_procedure("gimp-drawable-edit-stroke-selection")
                if proc:
                    cfg = proc.create_config()
                    cfg.set_property("drawable", drawable)
                    proc.run(cfg)
                Gimp.Selection.none(image)
            finally:
                Gimp.context_pop()
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _fill_rectangle(self, params):
        """Fill a rectangular region with color."""
        try:
            from gi.repository import Gegl

            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            x = int(params.get("x", 0))
            y = int(params.get("y", 0))
            width = int(params.get("width"))
            height = int(params.get("height"))
            color_str = params.get("color", "white")
            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            image.undo_group_start()
            Gimp.context_push()
            try:
                image.select_rectangle(Gimp.ChannelOps.REPLACE, x, y, width, height)
                Gimp.context_set_foreground(Gegl.Color.new(color_str))
                Gimp.Drawable.edit_fill(drawable, Gimp.FillType.FOREGROUND)
                Gimp.Selection.none(image)
            finally:
                Gimp.context_pop()
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _fill_ellipse(self, params):
        """Fill an elliptical region with color."""
        try:
            from gi.repository import Gegl

            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            x = int(params.get("x", 0))
            y = int(params.get("y", 0))
            width = int(params.get("width"))
            height = int(params.get("height"))
            color_str = params.get("color", "white")
            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            image.undo_group_start()
            Gimp.context_push()
            try:
                image.select_ellipse(Gimp.ChannelOps.REPLACE, x, y, width, height)
                Gimp.context_set_foreground(Gegl.Color.new(color_str))
                Gimp.Drawable.edit_fill(drawable, Gimp.FillType.FOREGROUND)
                Gimp.Selection.none(image)
            finally:
                Gimp.context_pop()
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _gradient_fill(self, params):
        """Fill with a gradient using GEGL (gimp-blend was removed in GIMP 3)."""
        try:
            from gi.repository import Gegl

            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            color1 = params.get("color1", "black")
            color2 = params.get("color2", "white")
            gradient_type = params.get("gradient_type", "linear").lower()
            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            w = image.get_width()
            h = image.get_height()
            x1 = float(params.get("x1") or params.get("start_x") or 0)
            y1 = float(params.get("y1") or params.get("start_y") or 0)
            x2 = float(params.get("x2") or params.get("end_x") or w)
            y2 = float(params.get("y2") or params.get("end_y") or h)

            image.undo_group_start()
            Gimp.context_push()
            try:
                Gimp.context_set_foreground(Gegl.Color.new(color1))
                Gimp.context_set_background(Gegl.Color.new(color2))

                Gegl.init(None)
                shadow_buf = drawable.get_shadow_buffer()
                graph = Gegl.Node()

                op_name = (
                    "gegl:radial-gradient" if gradient_type == "radial" else "gegl:linear-gradient"
                )
                grad_node = graph.create_child(op_name)
                try:
                    grad_node.set_property("start-color", Gegl.Color.new(color1))
                    grad_node.set_property("end-color", Gegl.Color.new(color2))
                except Exception:
                    pass
                if w > 0 and h > 0:
                    try:
                        grad_node.set_property("x0", x1 / w)
                        grad_node.set_property("y0", y1 / h)
                        grad_node.set_property("x1", x2 / w)
                        grad_node.set_property("y1", y2 / h)
                    except Exception:
                        pass

                out_node = graph.create_child("gegl:write-buffer")
                out_node.set_property("buffer", shadow_buf)
                grad_node.link(out_node)
                out_node.process()

                shadow_buf.flush()
                drawable.merge_shadow(True)
                drawable.update(0, 0, w, h)
            finally:
                Gimp.context_pop()
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    # =========================================================================
    # CATEGORY 7 — Text
    # =========================================================================

    def _resolve_font(self, name):
        """Resolve a font name to a Gimp.Font."""
        if not (hasattr(Gimp, "Font") and hasattr(Gimp.Font, "get_by_name")):
            return None

        font_obj = Gimp.Font.get_by_name(name)
        if font_obj is not None:
            return font_obj

        # GIMP 3.2 dropped GIMP 2.x aliases; e.g. "Sans" no longer resolves,
        # the 3.2 equivalent is "Sans-serif".
        for alias in (
            name + "-serif",
            name + " Regular",
            name.replace(" ", "-"),
            "Sans-serif",
            "Serif",
            "Monospace",
        ):
            font_obj = Gimp.Font.get_by_name(alias)
            if font_obj is not None:
                return font_obj

        raw = Gimp.fonts_get_list("")
        flist = list(raw[1]) if isinstance(raw, tuple) and len(raw) > 1 else list(raw)
        return flist[0] if flist else None

    def _create_text_layer_native(self, image, text, font_obj, size, x, y, color_str):
        """Create a text layer via Gimp.TextLayer.new."""
        from gi.repository import Gegl

        if not hasattr(Gimp, "TextLayer"):
            return None

        try:
            tl = Gimp.TextLayer.new(image, text, font_obj, float(size), Gimp.Unit.pixel())
        except Exception:
            return None
        if tl is None:
            return None

        try:
            image.insert_layer(tl, None, 0)
        except Exception:
            return None

        # Layer is now in the image; swallow offset/color failures so the
        # caller does not fall through to the PDB fallback and insert a
        # second layer.
        try:
            tl.set_offsets(x, y)
        except Exception:
            pass
        try:
            pdb = Gimp.get_pdb()
            cproc = pdb.lookup_procedure("gimp-text-layer-set-color")
            if cproc:
                ccfg = cproc.create_config()
                ccfg.set_property("layer", tl)
                ccfg.set_property("color", Gegl.Color.new(color_str))
                cproc.run(ccfg)
        except Exception:
            pass
        return tl

    def _create_text_layer_pdb(self, image, text, font_obj, size, x, y):
        """Create a text layer via the gimp-text-font PDB procedure."""
        proc = Gimp.get_pdb().lookup_procedure("gimp-text-font")
        if proc is None:
            return
        cfg = proc.create_config()
        cfg.set_property("image", image)
        cfg.set_property("drawable", None)
        cfg.set_property("x", float(x))
        cfg.set_property("y", float(y))
        cfg.set_property("text", text)
        cfg.set_property("border", 0)
        cfg.set_property("antialias", True)
        cfg.set_property("size", float(size))
        cfg.set_property("font", font_obj)
        proc.run(cfg)

    def _find_new_layer(self, image, before_ids):
        """Return the first layer whose id is not in before_ids."""
        for lyr in image.get_layers():
            if lyr.get_id() not in before_ids:
                return lyr
        return None

    def _add_text(self, params):
        """Add a text layer."""
        try:
            from gi.repository import Gegl

            image_index = int(params.get("image_index", 0))
            text_str = params.get("text", "")
            x = int(params.get("x", 0))
            y = int(params.get("y", 0))
            font = params.get("font", "Sans")
            size = int(params.get("size", 24))
            color_str = params.get("color", "black")

            image = self._get_image(image_index)
            before_ids = {lyr.get_id() for lyr in image.get_layers()}
            text_layer = None

            image.undo_group_start()
            Gimp.context_push()
            try:
                Gimp.context_set_foreground(Gegl.Color.new(color_str))
                font_obj = self._resolve_font(font)
                if font_obj is not None:
                    text_layer = self._create_text_layer_native(
                        image, text_str, font_obj, size, x, y, color_str
                    )
                    if text_layer is None:
                        self._create_text_layer_pdb(image, text_str, font_obj, size, x, y)
            finally:
                Gimp.context_pop()
                image.undo_group_end()
            Gimp.displays_flush()

            if text_layer is None:
                text_layer = self._find_new_layer(image, before_ids)
            if text_layer is None:
                # Issue #15: return an explicit error instead of a placeholder
                # success so clients never chain ops on a fake handle.
                return {
                    "status": "error",
                    "error": "add_text: no text layer was created (no PDB procedure succeeded)",
                }

            image_id = int(image.get_id())
            gen = self._bump_image_generation(image_id)
            return {
                "status": "success",
                "results": {
                    "layer_name": text_layer.get_name(),
                    "layer_id": text_layer.get_id(),
                    "text_width": text_layer.get_width(),
                    "text_height": text_layer.get_height(),
                    "position": [x, y],
                    "generation": gen,
                    "handle": self._emit_item_handle(text_layer, image_id),
                },
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _edit_text(self, params):
        """Edit an existing text layer."""
        try:
            from gi.repository import Gegl

            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", "")
            new_text = params.get("text", None)
            new_font = params.get("font", None)
            new_size = params.get("size", None)
            new_color = params.get("color", None)
            image = self._get_image(image_index)
            layer = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                layer_id=self._layer_id_from_params(params),
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            pdb = Gimp.get_pdb()
            image.undo_group_start()
            try:
                if new_text is not None:
                    proc = pdb.lookup_procedure("gimp-text-layer-set-text")
                    if proc:
                        cfg = proc.create_config()
                        cfg.set_property("layer", layer)
                        cfg.set_property("text", new_text)
                        proc.run(cfg)
                if new_font is not None:
                    proc = pdb.lookup_procedure("gimp-text-layer-set-font")
                    if proc:
                        cfg = proc.create_config()
                        cfg.set_property("layer", layer)
                        cfg.set_property("font", new_font)
                        proc.run(cfg)
                if new_size is not None:
                    proc = pdb.lookup_procedure("gimp-text-layer-set-font-size")
                    if proc:
                        cfg = proc.create_config()
                        cfg.set_property("layer", layer)
                        cfg.set_property("font-size", float(new_size))
                        cfg.set_property("unit", Gimp.Unit.PIXEL)
                        proc.run(cfg)
                if new_color is not None:
                    proc = pdb.lookup_procedure("gimp-text-layer-set-color")
                    if proc:
                        cfg = proc.create_config()
                        cfg.set_property("layer", layer)
                        cfg.set_property("color", Gegl.Color.new(new_color))
                        proc.run(cfg)
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _warp_region(self, params):
        """Warp / liquify a region of pixels to deform facial features.

        Uses plug-in-iwarp (interactive warp) to push pixels in a direction.
        Useful for subtle expressions: turning a neutral mouth into a smile by
        pushing corners upward, etc.

        params:
          image_index  — which image
          layer_name   — optional layer
          vectors      — list of warp vectors: [{x, y, dx, dy, radius, amount}]
                         x/y: pixel coords of warp center
                         dx/dy: push direction in pixels (positive y = down)
                         radius: influence radius in pixels (default 40)
                         amount: deform strength 0-1 (default 0.3)
        """
        try:
            from gi.repository import Gegl

            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            vectors = params.get("vectors", [])
            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            pdb = Gimp.get_pdb()

            image.undo_group_start()
            try:
                for v in vectors:
                    x = float(v.get("x", 0))
                    y = float(v.get("y", 0))
                    dx = float(v.get("dx", 0))
                    dy = float(v.get("dy", 0))
                    radius = float(v.get("radius", 40))
                    amount = float(v.get("amount", 0.3))

                    # Try GEGL warp operation first (GIMP 3 native approach)
                    try:
                        Gegl.init(None)
                        buf = drawable.get_buffer()
                        shadow_buf = drawable.get_shadow_buffer()
                        graph = Gegl.Node()

                        src = graph.create_child("gegl:buffer-source")
                        src.set_property("buffer", buf)

                        warp = graph.create_child("gegl:warp")
                        warp.set_property("behavior", 0)  # 0 = move
                        warp.set_property("strength", amount)
                        warp.set_property("size", radius)
                        warp.set_property("hardness", 0.5)
                        # stamp one warp stroke at (x,y) → (x+dx, y+dy)
                        # GEGL warp builds strokes via the "stroke" property
                        stroke = [(x, y), (x + dx, y + dy)]
                        warp.set_property("stroke", stroke)

                        out = graph.create_child("gegl:write-buffer")
                        out.set_property("buffer", shadow_buf)

                        src.link(warp)
                        warp.link(out)
                        out.process()

                        shadow_buf.flush()
                        drawable.merge_shadow(True)
                        drawable.update(
                            max(0, int(x - radius - abs(dx))),
                            max(0, int(y - radius - abs(dy))),
                            int(radius * 2 + abs(dx) * 2 + 4),
                            int(radius * 2 + abs(dy) * 2 + 4),
                        )
                    except Exception:
                        # Fallback: plug-in-iwarp if GEGL warp fails
                        proc = pdb.lookup_procedure("plug-in-iwarp")
                        if proc:
                            cfg = proc.create_config()
                            try:
                                cfg.set_property("run-mode", Gimp.RunMode.NONINTERACTIVE)
                                cfg.set_property("image", image)
                                cfg.set_property("drawable", drawable)
                                cfg.set_property("cursor-x", int(x))
                                cfg.set_property("cursor-y", int(y))
                                cfg.set_property("pressure", amount)
                                cfg.set_property("move-max-dist", int(radius))
                                cfg.set_property("deform-type", 0)  # 0 = MOVE
                                cfg.set_property("x", int(x + dx))
                                cfg.set_property("y", int(y + dy))
                                proc.run(cfg)
                            except Exception:
                                pass
            finally:
                image.undo_group_end()

            Gimp.displays_flush()
            return {"status": "success", "results": {"warped_vectors": len(vectors)}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _list_fonts(self, params):
        """List available fonts."""
        try:
            filt = params.get("filter", None) or ""
            limit = int(params.get("limit", 100))
            raw = Gimp.fonts_get_list(filt)
            # GIMP 3.2 returns (n, [Gimp.Font, ...]) or just [Gimp.Font, ...]
            if isinstance(raw, tuple):
                font_objs = list(raw[1]) if len(raw) > 1 else []
            else:
                font_objs = list(raw)
            names = []
            for f in font_objs[:limit]:
                if hasattr(f, "get_name"):
                    names.append(f.get_name())
                elif hasattr(f, "name"):
                    names.append(f.name)
                else:
                    names.append(str(f))
            return {"status": "success", "results": {"fonts": names, "count": len(names)}}
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    # =========================================================================
    # CATEGORY 8 — Filters & Effects
    # =========================================================================

    def _apply_drop_shadow(self, params):
        """Apply drop shadow via manual layer compositing (GIMP 3.2 compatible).

        gegl:drop-shadow and plug-in-drop-shadow are not reliably available in
        GIMP 3.2, so we build the shadow manually:
          1. Duplicate source layer → shadow_layer
          2. Fill it with shadow color (alpha-locked so shape is preserved)
          3. Set opacity and offset
          4. Gaussian-blur with plug-in-gauss
        """
        try:
            from gi.repository import Gegl

            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            offset_x = int(params.get("offset_x", 5))
            offset_y = int(params.get("offset_y", 5))
            blur_radius = float(params.get("blur_radius", 10))
            color_str = params.get("color", "black")
            opacity = float(params.get("opacity", 60))
            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                layer_id=self._layer_id_from_params(params),
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            image.undo_group_start()
            try:
                pdb = Gimp.get_pdb()

                # 1. Duplicate source layer to use as shadow base
                shadow_layer = drawable.copy()
                src_pos = image.get_item_position(drawable)
                image.insert_layer(shadow_layer, None, src_pos + 1)

                # 2. Fill shadow layer with shadow color, preserving alpha shape
                Gimp.context_set_foreground(Gegl.Color.new(color_str))
                shadow_layer.set_lock_alpha(True)
                Gimp.Drawable.edit_fill(shadow_layer, Gimp.FillType.FOREGROUND)
                shadow_layer.set_lock_alpha(False)

                # 3. Set opacity and offset
                shadow_layer.set_opacity(opacity)
                offs = shadow_layer.get_offsets()
                shadow_layer.set_offsets(offs.offset_x + offset_x, offs.offset_y + offset_y)

                # 4. Blur with plug-in-gauss (always available in GIMP 3.x)
                if blur_radius > 0:
                    size = max(3, int(blur_radius * 2) | 1)  # must be odd, ≥ 3
                    blur_proc = pdb.lookup_procedure("plug-in-gauss")
                    if blur_proc:
                        cfg = blur_proc.create_config()
                        cfg.set_property("image", image)
                        cfg.set_property("drawable", shadow_layer)
                        cfg.set_property("horizontal", size)
                        cfg.set_property("vertical", size)
                        cfg.set_property("method", 0)
                        blur_proc.run(cfg)

                shadow_layer.set_name("Drop Shadow")
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            image_id = int(image.get_id())
            gen = self._bump_image_generation(image_id)
            return {
                "status": "success",
                "results": {
                    "status": "success",
                    "generation": gen,
                    "handle": self._emit_item_handle(shadow_layer, image_id),
                    "layer_id": shadow_layer.get_id(),
                },
            }
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _apply_gaussian_blur(self, params):
        """Apply Gaussian blur."""
        try:
            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            radius = float(params.get("radius", 5.0))
            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            image.undo_group_start()
            try:
                self._apply_gegl_filter(
                    image,
                    drawable,
                    "gegl:gaussian-blur",
                    {
                        "std-dev-x": radius,
                        "std-dev-y": radius,
                    },
                )
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _apply_pixelate(self, params):
        """Apply pixelate effect."""
        try:
            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            block_size = int(params.get("block_size", 10))
            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            image.undo_group_start()
            try:
                self._apply_gegl_filter(
                    image,
                    drawable,
                    "gegl:pixelize",
                    {
                        "size-x": block_size,
                        "size-y": block_size,
                    },
                )
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _apply_emboss(self, params):
        """Apply emboss effect."""
        try:
            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            azimuth = float(params.get("azimuth", 315))
            elevation = float(params.get("elevation", 45))
            depth = float(params.get("depth", 2))
            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            image.undo_group_start()
            try:
                self._apply_gegl_filter(
                    image,
                    drawable,
                    "gegl:emboss",
                    {
                        "azimuth": azimuth,
                        "elevation": elevation,
                        "depth": depth,
                        "emboss": True,
                    },
                )
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _apply_vignette(self, params):
        """Apply vignette effect."""
        try:
            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            softness = float(params.get("softness", 3.0))
            shape = float(params.get("shape", 1.0))
            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            image.undo_group_start()
            try:
                self._apply_gegl_filter(
                    image,
                    drawable,
                    "gegl:vignette",
                    {
                        "softness": softness,
                        "shape": shape,
                        "radius": 1.0,
                    },
                )
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _apply_noise(self, params):
        """Add noise to a layer."""
        try:
            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            amount = float(params.get("amount", 0.2))
            image = self._get_image(image_index)
            drawable = self._resolve_mutable_layer(
                image,
                layer_name,
                None,
                allow_source_mutation=self._allow_source_mutation_from_params(params),
            )
            image.undo_group_start()
            try:
                self._apply_gegl_filter(
                    image,
                    drawable,
                    "gegl:noise-hsv",
                    {
                        "value": amount,
                        "saturation": 0.0,
                        "hue": 0.0,
                    },
                )
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success"}}
        except _sec.SecurityError as e:
            return e.as_error()
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    # =========================================================================
    # CATEGORY 9 — Export Pipelines
    # =========================================================================

    def _export_icon_sizes(self, params):
        """Export icon size sets for Android or iOS."""
        try:
            output_dir = params.get("output_dir", "")
            safe_dir, err = self._jail_path(output_dir)
            if err is not None:
                return err
            output_dir = str(safe_dir)
            platform_str = params.get("platform", "android").lower()
            src_index = int(params.get("source_image_index", 0))
            fmt = params.get("format", "png")

            ANDROID_SIZES = [
                (48, "mdpi"),
                (72, "hdpi"),
                (96, "xhdpi"),
                (144, "xxhdpi"),
                (192, "xxxhdpi"),
                (512, "playstore"),
            ]
            IOS_SIZES = [
                (20, 1),
                (20, 2),
                (20, 3),
                (29, 1),
                (29, 2),
                (29, 3),
                (40, 2),
                (40, 3),
                (60, 2),
                (60, 3),
                (76, 1),
                (76, 2),
                (84, 2),  # 83.5x2 rounded
                (1024, 1),
            ]

            source_image = self._get_image(src_index)
            os.makedirs(output_dir, exist_ok=True)
            exported = []
            sizes = ANDROID_SIZES if platform_str == "android" else IOS_SIZES

            for entry in sizes:
                if platform_str == "android":
                    px, density = entry
                    out_name = f"icon_{density}_{px}.{fmt}"
                else:
                    px, scale = entry
                    actual_px = int(px * scale)
                    out_name = f"icon_{px}@{scale}x.{fmt}"
                    px = actual_px

                out_path = os.path.join(output_dir, out_name)
                dup = source_image.duplicate()
                try:
                    src_w = dup.get_width()
                    src_h = dup.get_height()
                    aspect = src_w / src_h
                    if aspect >= 1.0:
                        new_w = px
                        new_h = max(1, int(px / aspect))
                    else:
                        new_h = px
                        new_w = max(1, int(px * aspect))
                    dup.scale(new_w, new_h)
                    # Opaque bake intentional (icons): flatten=True → preserve_alpha False
                    exp_r = self._export_to_path(dup, out_path, fmt, 95, flatten=True)
                    if exp_r.get("status") == "error":
                        raise RuntimeError(exp_r.get("error", "icon export failed"))
                    exported.append({"size": px, "file_path": out_path})
                finally:
                    dup.delete()

            return {
                "status": "success",
                "results": {"exported": exported, "count": len(exported), "platform": platform_str},
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _export_web_optimized(self, params):
        """Export as both JPEG and PNG, return comparison."""
        try:
            output_dir = params.get("output_dir", "")
            safe_dir, err = self._jail_path(output_dir)
            if err is not None:
                return err
            output_dir = str(safe_dir)
            jpeg_quality = int(params.get("jpeg_quality", 85))
            image_index = int(params.get("image_index", 0))
            max_width = params.get("max_width", None)
            max_height = params.get("max_height", None)
            image = self._get_image(image_index)
            os.makedirs(output_dir, exist_ok=True)

            dup = image.duplicate()
            try:
                if max_width or max_height:
                    src_w = dup.get_width()
                    src_h = dup.get_height()
                    mw = int(max_width or src_w)
                    mh = int(max_height or src_h)
                    aspect = src_w / src_h
                    if src_w / mw > src_h / mh:
                        new_w, new_h = mw, max(1, int(mw / aspect))
                    else:
                        new_h, new_w = mh, max(1, int(mh * aspect))
                    dup.scale(new_w, new_h)

                gio_file = dup.get_file()
                raw_name = gio_file.get_basename().rsplit(".", 1)[0] if gio_file else "image"
                jpeg_path = os.path.join(output_dir, f"{raw_name}.jpg")
                png_path = os.path.join(output_dir, f"{raw_name}.png")
                # Opaque bake for size compare (flatten=True → preserve_alpha False)
                jpeg_r = self._export_to_path(dup, jpeg_path, "jpeg", jpeg_quality, flatten=True)
                png_r = self._export_to_path(dup, png_path, "png", 95, flatten=True)
                if jpeg_r.get("status") == "error":
                    raise RuntimeError(jpeg_r.get("error", "jpeg export failed"))
                if png_r.get("status") == "error":
                    raise RuntimeError(png_r.get("error", "png export failed"))
                jpeg_size = int(jpeg_r.get("file_size_bytes") or 0)
                png_size = int(png_r.get("file_size_bytes") or 0)
            finally:
                dup.delete()

            recommendation = "jpeg" if jpeg_size < png_size else "png"
            return {
                "status": "success",
                "results": {
                    "jpeg_path": jpeg_path,
                    "jpeg_size": jpeg_size,
                    "png_path": png_path,
                    "png_size": png_size,
                    "recommendation": recommendation,
                },
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _batch_resize(self, params):
        """Resize all open images."""
        try:
            width = params.get("width", None)
            height = params.get("height", None)
            scale_factor = params.get("scale_factor", None)
            maintain_aspect = bool(params.get("maintain_aspect", True))
            images = Gimp.get_images()
            results = []
            for img in images:
                src_w = img.get_width()
                src_h = img.get_height()
                if scale_factor is not None:
                    new_w = max(1, int(src_w * float(scale_factor)))
                    new_h = max(1, int(src_h * float(scale_factor)))
                else:
                    tw = int(width or src_w)
                    th = int(height or src_h)
                    if maintain_aspect:
                        if width and not height:
                            new_w = tw
                            new_h = max(1, int(src_h * tw / src_w))
                        elif height and not width:
                            new_h = th
                            new_w = max(1, int(src_w * th / src_h))
                        else:
                            aspect = src_w / src_h
                            if tw / th > aspect:
                                new_h = th
                                new_w = max(1, int(th * aspect))
                            else:
                                new_w = tw
                                new_h = max(1, int(tw / aspect))
                    else:
                        new_w, new_h = tw, th
                img.scale(new_w, new_h)
                results.append(
                    {
                        "image_id": img.get_id(),
                        "old_width": src_w,
                        "old_height": src_h,
                        "new_width": new_w,
                        "new_height": new_h,
                    }
                )
            Gimp.displays_flush()
            return {"status": "success", "results": {"results": results, "count": len(results)}}
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _export_sprite_sheet(self, params):
        """Combine frames into a sprite sheet."""
        try:
            from gi.repository import Gegl

            output_path = params.get("output_path", "")
            safe_out, err = self._jail_path(output_path)
            if err is not None:
                return err
            output_path = str(safe_out)
            columns = params.get("columns", None)
            padding = int(params.get("padding", 0))
            source = params.get("source", "layers").lower()
            image_index = int(params.get("image_index", 0))
            import math

            if source == "images":
                frames = Gimp.get_images()
            else:
                src_image = self._get_image(image_index)
                frames = src_image.get_layers()

            if not frames:
                return {"status": "error", "error": "No frames found"}

            # Use first frame dimensions as the cell size
            frame_w = frames[0].get_width()
            frame_h = frames[0].get_height()
            n = len(frames)
            cols = int(columns) if columns else max(1, math.ceil(math.sqrt(n)))
            rows = math.ceil(n / cols)

            sheet_w = cols * frame_w + (cols - 1) * padding
            sheet_h = rows * frame_h + (rows - 1) * padding

            sheet = Gimp.Image.new(sheet_w, sheet_h, Gimp.ImageBaseType.RGBA)
            bg_layer = Gimp.Layer.new(
                sheet,
                "Background",
                sheet_w,
                sheet_h,
                Gimp.ImageType.RGBA_IMAGE,
                100,
                Gimp.LayerMode.NORMAL,
            )
            sheet.insert_layer(bg_layer, None, 0)
            Gimp.context_set_background(Gegl.Color.new("transparent"))
            Gimp.Drawable.edit_fill(bg_layer, Gimp.FillType.TRANSPARENT)

            for i, frame in enumerate(frames):
                col = i % cols
                row = i // cols
                dest_x = col * (frame_w + padding)
                dest_y = row * (frame_h + padding)
                if source == "images":
                    src_layers = frame.get_layers()
                    if not src_layers:
                        continue
                    src_drawable = src_layers[0]
                    frame.select_rectangle(
                        Gimp.ChannelOps.REPLACE, 0, 0, frame.get_width(), frame.get_height()
                    )
                else:
                    src_drawable = frame
                    frame.get_image().select_rectangle(
                        Gimp.ChannelOps.REPLACE, 0, 0, frame_w, frame_h
                    )
                Gimp.edit_copy([src_drawable])
                pasted = Gimp.edit_paste(bg_layer, True)[0]
                pasted.set_offsets(dest_x, dest_y)
                Gimp.floating_sel_anchor(pasted)

            # Sprite sheet is pre-composited; flatten bake is intentional
            exp_r = self._export_to_path(sheet, output_path, "png", 95, flatten=True)
            if exp_r.get("status") == "error":
                sheet.delete()
                return {
                    "status": "error",
                    "error": exp_r.get("error", "sprite sheet export failed"),
                    "code": exp_r.get("code"),
                }
            sheet.delete()
            return {
                "status": "success",
                "results": {
                    "file_path": output_path,
                    "columns": cols,
                    "rows": rows,
                    "frame_width": frame_w,
                    "frame_height": frame_h,
                    "count": n,
                },
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _export_social_media_kit(self, params):
        """Export for multiple social media platforms."""
        try:
            output_dir = params.get("output_dir", "")
            safe_dir, err = self._jail_path(output_dir)
            if err is not None:
                return err
            output_dir = str(safe_dir)
            platforms = params.get("platforms", None)
            image_index = int(params.get("image_index", 0))

            PLATFORM_SIZES = {
                "instagram_square": (1080, 1080),
                "instagram_story": (1080, 1920),
                "twitter_header": (1500, 500),
                "facebook_cover": (820, 312),
                "youtube_thumbnail": (1280, 720),
            }

            target_platforms = platforms if platforms else list(PLATFORM_SIZES.keys())
            source_image = self._get_image(image_index)
            os.makedirs(output_dir, exist_ok=True)
            exported = []

            for platform_name in target_platforms:
                if platform_name not in PLATFORM_SIZES:
                    continue
                target_w, target_h = PLATFORM_SIZES[platform_name]
                dup = source_image.duplicate()
                try:
                    src_w = dup.get_width()
                    src_h = dup.get_height()
                    # Scale to cover target (crop to exact size)
                    scale = max(target_w / src_w, target_h / src_h)
                    scaled_w = max(1, int(src_w * scale))
                    scaled_h = max(1, int(src_h * scale))
                    dup.scale(scaled_w, scaled_h)
                    # Crop to exact target
                    crop_x = (scaled_w - target_w) // 2
                    crop_y = (scaled_h - target_h) // 2
                    dup.crop(target_w, target_h, crop_x, crop_y)
                    out_path = os.path.join(output_dir, f"{platform_name}.png")
                    # Social media kit: opaque bake (flatten=True)
                    exp_r = self._export_to_path(dup, out_path, "png", 95, flatten=True)
                    if exp_r.get("status") == "error":
                        raise RuntimeError(exp_r.get("error", f"export failed for {platform_name}"))
                    exported.append(
                        {
                            "platform": platform_name,
                            "file_path": out_path,
                            "width": target_w,
                            "height": target_h,
                        }
                    )
                finally:
                    dup.delete()

            return {"status": "success", "results": {"exported": exported, "count": len(exported)}}
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    # =========================================================================
    # CATEGORY 10 — Utility
    # =========================================================================

    def _list_images(self, params):
        """List all open images."""
        try:
            images = Gimp.get_images()
            image_list = []
            base_type_map = {
                Gimp.ImageBaseType.RGB: "RGB",
                Gimp.ImageBaseType.GRAY: "Grayscale",
                Gimp.ImageBaseType.INDEXED: "Indexed",
            }
            for i, img in enumerate(images):
                try:
                    gio_file = img.get_file()
                    file_path = gio_file.get_path() if gio_file else "Untitled"
                    image_list.append(
                        {
                            "index": i,
                            "image_id": img.get_id(),
                            "name": gio_file.get_basename() if gio_file else f"Untitled_{i}",
                            "width": img.get_width(),
                            "height": img.get_height(),
                            "color_mode": base_type_map.get(img.get_base_type(), "Unknown"),
                            "num_layers": len(img.get_layers()),
                            "file_path": file_path,
                            "is_dirty": img.is_dirty() if hasattr(img, "is_dirty") else None,
                        }
                    )
                except Exception as ex:
                    image_list.append({"index": i, "error": str(ex)})
            return {
                "status": "success",
                "results": {"images": image_list, "count": len(image_list)},
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _set_active_image(self, params):
        """Raise a specific image to the front."""
        try:
            image_index = int(params.get("image_index", 0))
            image = self._get_image(image_index)
            displays = Gimp.get_displays()
            for display in displays:
                try:
                    if display.get_image().get_id() == image.get_id():
                        Gimp.set_default_context()
                        display.present()
                        break
                except Exception:
                    pass
            Gimp.displays_flush()
            return {
                "status": "success",
                "results": {"status": "success", "image_id": image.get_id()},
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _undo(self, params):
        """Undo N steps."""
        try:
            image_index = int(params.get("image_index", 0))
            steps = int(params.get("steps", 1))
            image = self._get_image(image_index)
            done = 0
            for _ in range(steps):
                if image.undo():
                    done += 1
                else:
                    break
            Gimp.displays_flush()
            return {"status": "success", "results": {"steps_undone": done}}
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _redo(self, params):
        """Redo N steps."""
        try:
            image_index = int(params.get("image_index", 0))
            steps = int(params.get("steps", 1))
            image = self._get_image(image_index)
            done = 0
            for _ in range(steps):
                if image.redo():
                    done += 1
                else:
                    break
            Gimp.displays_flush()
            return {"status": "success", "results": {"steps_redone": done}}
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _convert_color_mode(self, params):
        """Convert image color mode."""
        try:
            image_index = int(params.get("image_index", 0))
            mode = params.get("mode", "RGB").upper()
            num_colors = int(params.get("num_colors", 256))
            image = self._get_image(image_index)
            image.undo_group_start()
            try:
                if mode in ("RGB", "RGBA"):
                    image.convert_rgb()
                    if mode == "RGBA":
                        # Add alpha channel to all layers
                        for layer in image.get_layers():
                            if not layer.has_alpha():
                                layer.add_alpha()
                elif mode in ("GRAY", "GRAYA"):
                    image.convert_grayscale()
                    if mode == "GRAYA":
                        for layer in image.get_layers():
                            if not layer.has_alpha():
                                layer.add_alpha()
                elif mode == "INDEXED":
                    image.convert_indexed(
                        Gimp.ConvertDitherType.NO_DITHER,
                        Gimp.ConvertPaletteType.GENERATE,
                        num_colors,
                        False,
                        False,
                        "",
                    )
                else:
                    return {"status": "error", "error": f"Unknown mode: {mode}"}
            finally:
                image.undo_group_end()
            Gimp.displays_flush()
            return {"status": "success", "results": {"status": "success", "mode": mode}}
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _close_image(self, params):
        """Close an image, optionally saving first (path-jailed when saving)."""
        try:
            from gi.repository import Gio

            save_first = bool(params.get("save_first", False))
            try:
                image, _iid = self._resolve_image_from_params(params)
            except _handles.HandleError as e:
                return self._handle_error_response(e)
            if save_first:
                img_file = image.get_file()
                if img_file and img_file.get_path():
                    xcf_path = img_file.get_path().rsplit(".", 1)[0] + ".xcf"
                else:
                    # Untitled images must save under workspace (fail-closed).
                    root = self.workspace_root
                    if root is None:
                        return _sec.make_error(
                            _sec.CODE_PATH_DENIED,
                            "close_image save_first requires GIMP_WORKSPACE_ROOT "
                            "for untitled images",
                        )
                    xcf_path = os.path.join(str(root), f"gimp_backup_{image.get_id()}.xcf")
                safe_path, err = self._jail_path(xcf_path)
                if err is not None:
                    return err
                gio_file = Gio.File.new_for_path(os.fspath(safe_path))
                pdb = Gimp.get_pdb()
                proc = pdb.lookup_procedure("gimp-xcf-save")
                if proc:
                    cfg = proc.create_config()
                    cfg.set_property("image", image)
                    cfg.set_property("file", gio_file)
                    proc.run(cfg)
            # Delete all displays for this image
            closed_id = int(image.get_id())
            for display in Gimp.get_displays():
                try:
                    if display.get_image().get_id() == closed_id:
                        Gimp.Display.delete(display)
                except Exception:
                    pass
            image.delete()
            self._drop_image_generation(closed_id)
            return {"status": "success", "results": {"status": "success"}}
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _get_selection_bounds(self, params):
        """Get selection bounding rectangle."""
        try:
            image = self._get_image(int(params.get("image_index", 0)))
            _ok, non_empty, x1, y1, x2, y2 = Gimp.Selection.bounds(image)
            return {
                "status": "success",
                "results": {
                    "has_selection": bool(non_empty),
                    "x": x1,
                    "y": y1,
                    "width": x2 - x1,
                    "height": y2 - y1,
                },
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _get_pixel_color(self, params):
        """Get color of a single pixel."""
        try:
            image_index = int(params.get("image_index", 0))
            layer_name = params.get("layer_name", None)
            x = int(params.get("x", 0))
            y = int(params.get("y", 0))
            image = self._get_image(image_index)
            drawable = self._resolve_layer(image, layer_name, None)
            pixel = drawable.get_pixel(x, y)
            # GIMP 3.2: get_pixel returns a Gegl.Color object
            if hasattr(pixel, "get_rgba"):
                rf, gf, bf, af = pixel.get_rgba()
                r, g, b, a = int(rf * 255), int(gf * 255), int(bf * 255), int(af * 255)
            else:
                # Fallback: old tuple format (num_channels, bytes_array)
                channels = list(pixel[1]) if hasattr(pixel[1], "__iter__") else []
                r = channels[0] if len(channels) > 0 else 0
                g = channels[1] if len(channels) > 1 else 0
                b = channels[2] if len(channels) > 2 else 0
                a = channels[3] if len(channels) > 3 else 255
            color_hex = f"#{r:02x}{g:02x}{b:02x}"
            return {
                "status": "success",
                "results": {
                    "color_hex": color_hex,
                    "color_rgb": [r, g, b],
                    "alpha": a,
                },
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}

    def _get_histogram(self, params):
        """Get histogram statistics for a layer channel."""
        try:
            CHANNEL_MAP = {
                "value": Gimp.HistogramChannel.VALUE,
                "red": Gimp.HistogramChannel.RED,
                "green": Gimp.HistogramChannel.GREEN,
                "blue": Gimp.HistogramChannel.BLUE,
                "alpha": Gimp.HistogramChannel.ALPHA,
            }
            image_index = int(params.get("image_index", 0))
            channel_str = params.get("channel", "value")
            image = self._get_image(image_index)
            drawable = (image.get_selected_layers() or image.get_layers() or [None])[0]
            channel = CHANNEL_MAP.get(channel_str.lower(), Gimp.HistogramChannel.VALUE)
            pdb = Gimp.get_pdb()
            proc = pdb.lookup_procedure("gimp-drawable-histogram")
            if proc:
                cfg = proc.create_config()
                cfg.set_property("drawable", drawable)
                cfg.set_property("channel", channel)
                cfg.set_property("start-range", 0.0)
                cfg.set_property("end-range", 1.0)
                result = proc.run(cfg)

                # Return values: mean, std-dev, median, pixels, count, percentile
                def _safe(idx):
                    try:
                        return result.index(idx)
                    except Exception:
                        return 0

                return {
                    "status": "success",
                    "results": {
                        "mean": _safe(0),
                        "std_dev": _safe(1),
                        "median": _safe(2),
                        "pixels": _safe(3),
                        "count": _safe(4),
                    },
                }
            else:
                return {"status": "error", "error": "gimp-drawable-histogram not available"}
        except Exception as e:
            return {"status": "error", "error": str(e), "traceback": traceback.format_exc()}


Gimp.main(MCPPlugin.__gtype__, sys.argv)
