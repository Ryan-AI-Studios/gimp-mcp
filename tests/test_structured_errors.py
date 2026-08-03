"""Offline tests for structured error envelope v1 + request_id (track 0011)."""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import gimp_mcp_security as sec

# ---------------------------------------------------------------------------
# CODE_DEFAULTS matrix
# ---------------------------------------------------------------------------

REQUIRED_CODES = (
    sec.CODE_PARTIAL_MUTATION,
    sec.CODE_CONNECTION_FAILED,
    sec.CODE_STALE_HANDLE,
    sec.CODE_FOREIGN_SESSION,
    sec.CODE_POLICY_DENIED,
    sec.CODE_CONFIRM_REQUIRED,
    sec.CODE_PATH_DENIED,
    sec.CODE_AUTH_FAILED,
    sec.CODE_EXEC_DISABLED,
    sec.CODE_ALPHA_LOST,
    sec.CODE_INTERNAL,
)


def test_code_defaults_matrix_completeness() -> None:
    for code in REQUIRED_CODES:
        assert code in sec.CODE_DEFAULTS, f"missing CODE_DEFAULTS entry for {code}"
        spec = sec.CODE_DEFAULTS[code]
        assert "retryable" in spec
        assert "approval_required" in spec
        assert "state_may_have_changed" in spec
        assert isinstance(spec["retryable"], bool)
        assert isinstance(spec["approval_required"], bool)
        assert isinstance(spec["state_may_have_changed"], bool)


def test_partial_mutation_and_connection_defaults() -> None:
    pm = sec.CODE_DEFAULTS[sec.CODE_PARTIAL_MUTATION]
    assert pm["retryable"] is False
    assert pm["state_may_have_changed"] is True
    cf = sec.CODE_DEFAULTS[sec.CODE_CONNECTION_FAILED]
    assert cf["retryable"] is True
    assert cf["state_may_have_changed"] is False
    conf = sec.CODE_DEFAULTS[sec.CODE_CONFIRM_REQUIRED]
    assert conf["approval_required"] is True
    alpha = sec.CODE_DEFAULTS[sec.CODE_ALPHA_LOST]
    assert alpha["state_may_have_changed"] is True


# ---------------------------------------------------------------------------
# request_id
# ---------------------------------------------------------------------------


def test_new_request_id_format() -> None:
    rid = sec.new_request_id()
    assert rid.startswith("req_")
    hex_part = rid[4:]
    assert re.fullmatch(r"[0-9a-f]{32}", hex_part), rid


def test_new_request_id_uniqueness_smoke() -> None:
    ids = {sec.new_request_id() for _ in range(50)}
    assert len(ids) == 50


# ---------------------------------------------------------------------------
# build / format / parse
# ---------------------------------------------------------------------------


def test_build_error_envelope_shape() -> None:
    rid = "req_" + "a" * 32
    env = sec.build_error_envelope(
        sec.CODE_STALE_HANDLE,
        "handle generation mismatch",
        request_id=rid,
        affected_handles=[{"image_id": 1}],
    )
    assert env["ok"] is False
    err = env["error"]
    assert err["code"] == sec.CODE_STALE_HANDLE
    assert err["message"] == "handle generation mismatch"
    assert err["request_id"] == rid
    assert err["retryable"] is True
    assert err["approval_required"] is False
    assert err["state_may_have_changed"] is False
    assert err["rollback_available"] is False
    assert err["transaction_id"] is None
    assert err["affected_handles"] == [{"image_id": 1}]
    assert err["details"] is None


def test_format_parse_round_trip() -> None:
    rid = sec.new_request_id()
    env = sec.build_error_envelope(
        sec.CODE_POLICY_DENIED,
        "Source_Immutable protected",
        request_id=rid,
    )
    text = sec.format_tool_error_text(env)
    assert "\n" not in text
    assert text.startswith(f"{sec.CODE_POLICY_DENIED}: ")
    assert f"(request_id={rid})" in text
    assert " | " in text
    parsed = sec.parse_tool_error_text(text)
    assert parsed is not None
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == sec.CODE_POLICY_DENIED
    assert parsed["error"]["request_id"] == rid
    assert parsed["error"]["message"] == "Source_Immutable protected"


def test_format_sanitizes_newlines_in_message() -> None:
    rid = sec.new_request_id()
    env = sec.build_error_envelope(
        sec.CODE_INTERNAL,
        "line1\nline2\rline3",
        request_id=rid,
    )
    text = sec.format_tool_error_text(env)
    assert "\n" not in text
    assert "\r" not in text
    parsed = sec.parse_tool_error_text(text)
    assert parsed is not None
    assert "line1" in parsed["error"]["message"]
    assert "line2" in parsed["error"]["message"]


def test_format_message_with_spaces() -> None:
    rid = sec.new_request_id()
    env = sec.build_error_envelope(
        sec.CODE_PATH_DENIED,
        "Path escapes workspace root: C:\\foo\\bar",
        request_id=rid,
    )
    text = sec.format_tool_error_text(env)
    parsed = sec.parse_tool_error_text(text)
    assert parsed is not None
    assert "workspace root" in parsed["error"]["message"]


def test_parse_tool_error_text_malformed() -> None:
    assert sec.parse_tool_error_text("") is None
    assert sec.parse_tool_error_text("just a string") is None
    assert sec.parse_tool_error_text("CODE: msg | not-json") is None
    assert sec.parse_tool_error_text('CODE: msg | {"ok": true}') is None
    assert sec.parse_tool_error_text('CODE: msg | {"ok": false}') is None  # no error key


def test_parse_preserves_compact_json() -> None:
    rid = sec.new_request_id()
    env = sec.build_error_envelope(
        sec.CODE_CONNECTION_FAILED,
        "socket closed",
        request_id=rid,
        details={"host": "127.0.0.1"},
    )
    text = sec.format_tool_error_text(env)
    # compact separators — no space after colon/comma in the JSON half
    json_part = text.split(" | ", 1)[1]
    assert ": " not in json_part or True  # message side has spaces; JSON half:
    assert '": ' not in json_part
    assert ", " not in json_part


# ---------------------------------------------------------------------------
# GimpMcpError
# ---------------------------------------------------------------------------


def test_gimp_mcp_error_envelope() -> None:
    rid = sec.new_request_id()
    exc = sec.GimpMcpError(
        sec.CODE_CONFIRM_REQUIRED,
        "need confirm_destructive",
        details={"op": "flatten"},
    )
    env = exc.envelope(request_id=rid)
    assert env["error"]["approval_required"] is True
    assert env["error"]["details"] == {"op": "flatten"}


# ---------------------------------------------------------------------------
# make_error additive + strip_traceback
# ---------------------------------------------------------------------------


def test_make_error_base_shape_unchanged() -> None:
    e = sec.make_error(sec.CODE_AUTH_FAILED, "nope")
    assert e == {"status": "error", "error": "nope", "code": sec.CODE_AUTH_FAILED}


def test_make_error_additive_fields() -> None:
    rid = sec.new_request_id()
    e = sec.make_error(
        sec.CODE_STALE_HANDLE,
        "stale",
        request_id=rid,
        retryable=True,
        approval_required=False,
        state_may_have_changed=False,
        rollback_available=False,
        affected_handles=[{"image_id": 2}],
        details={"reason": "gen"},
        transaction_id=None,
    )
    assert e["status"] == "error"
    assert e["code"] == sec.CODE_STALE_HANDLE
    assert e["request_id"] == rid
    assert e["retryable"] is True
    assert e["affected_handles"] == [{"image_id": 2}]
    assert e["details"] == {"reason": "gen"}


def test_strip_traceback_preserves_additive_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sec.ENV_DEBUG, raising=False)
    body = {
        "status": "error",
        "error": "x",
        "code": sec.CODE_INTERNAL,
        "traceback": "stack",
        "request_id": "req_abc",
        "retryable": False,
        "state_may_have_changed": True,
        "affected_handles": [],
        "details": {"k": 1},
    }
    cleaned = sec.strip_traceback_unless_debug(body)
    assert "traceback" not in cleaned
    assert cleaned["request_id"] == "req_abc"
    assert cleaned["retryable"] is False
    assert cleaned["state_may_have_changed"] is True
    assert cleaned["details"] == {"k": 1}


# ---------------------------------------------------------------------------
# Split audit paths
# ---------------------------------------------------------------------------


def test_audit_split_paths_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sec.ENV_AUDIT_LOG, raising=False)
    server = sec.audit_server_path()
    plugin = sec.audit_plugin_path()
    assert server.name == "audit-server.jsonl"
    assert plugin.name == "audit-plugin.jsonl"
    assert server.parent == plugin.parent


def test_audit_split_paths_env_jsonl_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    target = tmp_path / "custom" / "audit.jsonl"
    monkeypatch.setenv(sec.ENV_AUDIT_LOG, str(target))
    assert sec.audit_server_path() == target.parent / "audit-server.jsonl"
    assert sec.audit_plugin_path() == target.parent / "audit-plugin.jsonl"


def test_audit_split_paths_env_directory(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv(sec.ENV_AUDIT_LOG, str(tmp_path / "logs"))
    assert sec.audit_server_path() == tmp_path / "logs" / "audit-server.jsonl"
    assert sec.audit_plugin_path() == tmp_path / "logs" / "audit-plugin.jsonl"


def test_audit_log_path_aliases_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sec.ENV_AUDIT_LOG, raising=False)
    assert sec.audit_log_path() == sec.audit_plugin_path()


# ---------------------------------------------------------------------------
# capability
# ---------------------------------------------------------------------------


def test_structured_errors_capability() -> None:
    from gimp_mcp_state import default_capabilities

    caps = default_capabilities()
    assert caps.get("structured_errors") is True


# ---------------------------------------------------------------------------
# Server helpers (import server module carefully)
# ---------------------------------------------------------------------------


def test_export_image_alpha_lost_raises_tool_error() -> None:
    """H1: export_image must raise ToolError on ALPHA_LOST — never return error dict."""
    from fastmcp.exceptions import ToolError

    import gimp_mcp_server as srv

    plugin_err: dict[str, Any] = {
        "status": "error",
        "code": sec.CODE_ALPHA_LOST,
        "error": "Alpha channel lost in export",
        "left_on_disk": True,
        "png_color_type": 2,
        "property_errors": ["ihdr"],
        "file_path": "C:/ws/out.png",
    }
    mock_conn = MagicMock()
    mock_conn.send_command.return_value = plugin_err

    export_fn = srv.export_image.fn  # real FastMCP FunctionTool wrapper
    with (
        patch.object(srv, "get_gimp_connection", return_value=mock_conn),
        patch.object(srv, "_jail_path_or_raise", return_value="C:/ws/out.png"),
        patch.object(srv.sec, "write_audit_event"),
    ):
        with pytest.raises(ToolError) as ei:
            export_fn(
                ctx=MagicMock(),
                file_path="out.png",
                format="png",
            )

    text = str(ei.value)
    parsed = sec.parse_tool_error_text(text)
    assert parsed is not None
    assert parsed["error"]["code"] == sec.CODE_ALPHA_LOST
    details = parsed["error"].get("details") or {}
    assert details.get("left_on_disk") is True
    assert details.get("png_color_type") == 2
    assert details.get("property_errors") == ["ihdr"]
    # Must not be a successful return of the plugin dict
    assert not isinstance(ei.value, dict)


def test_jail_path_reraise_security_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """H5: _jail_path_or_raise re-raises SecurityError (PATH_DENIED), not RuntimeError."""
    import gimp_mcp_server as srv

    monkeypatch.delenv(sec.ENV_WORKSPACE, raising=False)
    with pytest.raises(sec.SecurityError) as ei:
        srv._jail_path_or_raise("foo.png", "file_path")
    assert ei.value.code == sec.CODE_PATH_DENIED


def test_connection_failed_mapping() -> None:
    """CONNECTION_FAILED from ConnectionError via raise_from_exception helper."""
    from fastmcp.exceptions import ToolError

    import gimp_mcp_server as srv

    with patch.object(srv.sec, "write_audit_event"):
        with pytest.raises(ToolError) as ei:
            srv.raise_from_exception(
                ConnectionError("refused"),
                request_id="req_" + "b" * 32,
                tool_name="session_probe",
            )
    parsed = sec.parse_tool_error_text(str(ei.value))
    assert parsed is not None
    assert parsed["error"]["code"] == sec.CODE_CONNECTION_FAILED
    assert parsed["error"]["retryable"] is True
    assert parsed["error"]["state_may_have_changed"] is False


def test_raise_from_plugin_result_uses_code() -> None:
    from fastmcp.exceptions import ToolError

    import gimp_mcp_server as srv

    result = {
        "status": "error",
        "code": sec.CODE_STALE_HANDLE,
        "error": "generation mismatch",
    }
    with patch.object(srv.sec, "write_audit_event"):
        with pytest.raises(ToolError) as ei:
            srv.raise_from_plugin_result(
                result,
                "select_image",
                request_id="req_" + "c" * 32,
            )
    parsed = sec.parse_tool_error_text(str(ei.value))
    assert parsed is not None
    assert parsed["error"]["code"] == sec.CODE_STALE_HANDLE


def test_send_command_injects_request_id_copy() -> None:
    """params copy + _request_id injection; original dict not mutated."""
    import gimp_mcp_server as srv

    original: dict[str, Any] = {"handle": {"image_id": 1}}
    captured: dict[str, Any] = {}

    def fake_once(self, command_type, params, *, force_reload_token):
        captured["params"] = params
        return {"status": "success", "results": {}}

    rid = "req_" + "d" * 32
    conn = srv.GimpConnection.__new__(srv.GimpConnection)
    conn.host = "127.0.0.1"
    conn.port = 9877
    conn.sock = MagicMock()

    with (
        patch.object(srv.GimpConnection, "_send_command_once", fake_once),
        patch.object(srv, "get_current_request_id", return_value=rid),
    ):
        conn.send_command("select_image", original)

    assert "_request_id" not in original
    assert captured["params"]["_request_id"] == rid
    assert captured["params"]["handle"] == original["handle"]


# ---------------------------------------------------------------------------
# call_api — no false-green (BS3 / DoD-2)
# ---------------------------------------------------------------------------


def test_call_api_exec_disabled_raises_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """EXEC_DISABLED must raise ToolError (isError), never return success JSON."""
    from fastmcp.exceptions import ToolError

    import gimp_mcp_server as srv

    monkeypatch.delenv(sec.ENV_ALLOW_EXEC, raising=False)
    call_fn = srv.call_api.fn
    with patch.object(srv.sec, "write_audit_event"):
        with pytest.raises(ToolError) as ei:
            call_fn(ctx=MagicMock(), api_path="exec", args=[], kwargs={})

    parsed = sec.parse_tool_error_text(str(ei.value))
    assert parsed is not None
    assert parsed["error"]["code"] == sec.CODE_EXEC_DISABLED
    assert parsed["ok"] is False


def test_call_api_plugin_error_raises_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plugin status=error must raise ToolError with plugin code — not 'Error: …' string."""
    from fastmcp.exceptions import ToolError

    import gimp_mcp_server as srv

    monkeypatch.setenv(sec.ENV_ALLOW_EXEC, "1")
    mock_conn = MagicMock()
    mock_conn.send_command.return_value = {
        "status": "error",
        "code": sec.CODE_POLICY_DENIED,
        "error": "exec blocked by policy",
    }
    call_fn = srv.call_api.fn
    with (
        patch.object(srv, "get_gimp_connection", return_value=mock_conn),
        patch.object(srv.sec, "write_audit_event"),
    ):
        with pytest.raises(ToolError) as ei:
            call_fn(
                ctx=MagicMock(),
                api_path="exec",
                args=["pyGObject-console", ["1+1"]],
                kwargs={},
            )

    text = str(ei.value)
    parsed = sec.parse_tool_error_text(text)
    assert parsed is not None
    assert parsed["error"]["code"] == sec.CODE_POLICY_DENIED
    # Must not be the old false-green "Error: …" success-string path
    assert not text.startswith("Error: ")


def test_call_api_connection_error_raises_tool_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ConnectionError on call_api → CONNECTION_FAILED via decorator."""
    from fastmcp.exceptions import ToolError

    import gimp_mcp_server as srv

    monkeypatch.setenv(sec.ENV_ALLOW_EXEC, "1")
    call_fn = srv.call_api.fn
    with (
        patch.object(srv, "get_gimp_connection", side_effect=ConnectionError("refused")),
        patch.object(srv.sec, "write_audit_event"),
    ):
        with pytest.raises(ToolError) as ei:
            call_fn(ctx=MagicMock(), api_path="exec", args=[], kwargs={})

    parsed = sec.parse_tool_error_text(str(ei.value))
    assert parsed is not None
    assert parsed["error"]["code"] == sec.CODE_CONNECTION_FAILED
    assert parsed["error"]["retryable"] is True


# ---------------------------------------------------------------------------
# M5 — affected_handles on INTERNAL when handle known
# ---------------------------------------------------------------------------


def test_harvest_affected_handles_from_kwargs() -> None:
    import gimp_mcp_server as srv

    h = {"image_id": 7, "generation": 1}
    assert srv._harvest_affected_handles({"handle": h}) == [h]
    assert srv._harvest_affected_handles({"handles": [h, {"image_id": 8}]}) == [
        h,
        {"image_id": 8},
    ]
    both = srv._harvest_affected_handles({"handle": h, "handles": [{"image_id": 9}]})
    assert both == [h, {"image_id": 9}]
    assert srv._harvest_affected_handles({}) is None
    assert srv._harvest_affected_handles({"handle": "not-a-dict"}) is None
    assert srv._harvest_affected_handles({"handles": "nope"}) is None


def test_with_structured_error_harvests_handle_on_internal() -> None:
    """Decorator maps RuntimeError → INTERNAL with affected_handles from handle=."""
    from fastmcp.exceptions import ToolError

    import gimp_mcp_server as srv

    handle = {"image_id": 42, "generation": 3, "session_id": "s1"}

    @srv.with_structured_error("fake_handle_tool")
    def fake_handle_tool(*, handle: dict | None = None) -> str:
        raise RuntimeError("simulated crash")

    with patch.object(srv.sec, "write_audit_event"):
        with pytest.raises(ToolError) as ei:
            fake_handle_tool(handle=handle)

    parsed = sec.parse_tool_error_text(str(ei.value))
    assert parsed is not None
    assert parsed["error"]["code"] == sec.CODE_INTERNAL
    assert handle in (parsed["error"].get("affected_handles") or [])


def test_select_image_internal_includes_affected_handle() -> None:
    """select_image RuntimeError from connection → INTERNAL + handle in envelope."""
    from fastmcp.exceptions import ToolError

    import gimp_mcp_server as srv

    handle = {"image_id": 99, "generation": 0}
    select_fn = srv.select_image.fn
    with (
        patch.object(srv, "get_gimp_connection", side_effect=RuntimeError("boom")),
        patch.object(srv.sec, "write_audit_event"),
    ):
        with pytest.raises(ToolError) as ei:
            select_fn(ctx=MagicMock(), handle=handle)

    parsed = sec.parse_tool_error_text(str(ei.value))
    assert parsed is not None
    assert parsed["error"]["code"] == sec.CODE_INTERNAL
    assert handle in (parsed["error"].get("affected_handles") or [])
