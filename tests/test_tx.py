"""Offline tests for agent undo-group transactions (track 0017)."""

from __future__ import annotations

import re
from pathlib import Path

import gimp_mcp_tx as tx
from gimp_agent import paths as pathmod

# ---------------------------------------------------------------------------
# mint / label / timeout
# ---------------------------------------------------------------------------


def test_mint_transaction_id_prefix_and_uniqueness() -> None:
    ids = {tx.mint_transaction_id() for _ in range(40)}
    assert len(ids) == 40
    for tid in ids:
        assert tid.startswith("txn_")
        assert re.fullmatch(r"txn_[0-9a-f]{32}", tid), tid


def test_validate_label_default_and_strip() -> None:
    assert tx.validate_label(None) == "agent"
    assert tx.validate_label("") == "agent"
    assert tx.validate_label("   ") == "agent"
    assert tx.validate_label("  edit-pass  ") == "edit-pass"
    assert tx.validate_label("a" * 128) == "a" * 128


def test_validate_label_rejects_over_128() -> None:
    try:
        tx.validate_label("x" * 129)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "128" in str(exc)


def test_parse_timeout_s_default_and_clamp() -> None:
    assert tx.parse_timeout_s(None) == tx.DEFAULT_TIMEOUT_S
    assert tx.parse_timeout_s("") == tx.DEFAULT_TIMEOUT_S
    assert tx.parse_timeout_s("bogus") == tx.DEFAULT_TIMEOUT_S
    assert tx.parse_timeout_s(1) == 5.0
    assert tx.parse_timeout_s(4.9) == 5.0
    assert tx.parse_timeout_s(3601) == 3600.0
    assert tx.parse_timeout_s(120) == 120.0
    assert tx.parse_timeout_s(300) == 300.0


# ---------------------------------------------------------------------------
# TxStack nest / depth / mismatch
# ---------------------------------------------------------------------------


def _rec(
    tid: str,
    *,
    depth: int,
    mono: float = 0.0,
    image_id: int = 1,
    label: str = "agent",
) -> tx.TxRecord:
    return tx.TxRecord(
        transaction_id=tid,
        label=label,
        image_id=image_id,
        opened_mono=mono,
        depth=depth,
        opened_at=mono,
    )


def test_stack_nest_push_pop_top() -> None:
    stack = tx.TxStack()
    assert stack.depth == 0
    assert stack.top() is None
    assert stack.pop() is None

    a = _rec("txn_a", depth=1, mono=10.0)
    b = _rec("txn_b", depth=2, mono=11.0)
    stack.push(a)
    stack.push(b)
    assert stack.depth == 2
    assert stack.top() is b
    open_list = stack.open_list()
    assert open_list[0] is a  # outermost
    assert open_list[-1] is b  # deepest / top
    assert stack.pop() is b
    assert stack.top() is a
    assert stack.depth == 1


def test_stack_depth_9_would_exceed() -> None:
    stack = tx.TxStack()
    for i in range(tx.MAX_DEPTH):
        # Before push, depth < MAX so a begin is still allowed
        assert stack.would_exceed_depth() is False
        stack.push(_rec(f"txn_{i}", depth=i + 1, mono=float(i)))
    assert stack.depth == tx.MAX_DEPTH
    assert stack.would_exceed_depth() is True
    # depth 9 would exceed MAX_DEPTH=8 → TX_DEPTH semantics
    assert stack.depth + 1 > tx.MAX_DEPTH


def test_empty_end_mismatch() -> None:
    stack = tx.TxStack()
    code, rec = stack.end_top()
    assert code == tx.CODE_TX_MISMATCH
    assert rec is None
    code2, rec2 = stack.end_top("txn_anything")
    assert code2 == tx.CODE_TX_MISMATCH
    assert rec2 is None


def test_end_top_id_mismatch() -> None:
    stack = tx.TxStack()
    stack.push(_rec("txn_top", depth=1))
    code, rec = stack.end_top("txn_other")
    assert code == tx.CODE_TX_MISMATCH
    assert rec is None
    code_ok, rec_ok = stack.end_top("txn_top")
    assert code_ok == "ok"
    assert rec_ok is not None
    assert rec_ok.transaction_id == "txn_top"


# ---------------------------------------------------------------------------
# wall-clock reap
# ---------------------------------------------------------------------------


def test_reap_expired_wall_clock_200s_noop_310s_close() -> None:
    stack = tx.TxStack()
    # opened at mono=0
    stack.push(_rec("txn_outer", depth=1, mono=0.0))
    stack.push(_rec("txn_inner", depth=2, mono=5.0))
    timeout = 300.0

    # +200s: neither expired (outer age 200 < 300)
    closed = stack.reap_expired(now_mono=200.0, timeout_s=timeout)
    assert closed == []
    assert stack.depth == 2

    # +310s: outer age 310 >= 300 → force-close outer and above (inner first)
    closed2 = stack.reap_expired(now_mono=310.0, timeout_s=timeout)
    assert len(closed2) == 2
    assert closed2[0].transaction_id == "txn_inner"  # deepest first
    assert closed2[1].transaction_id == "txn_outer"
    assert all(r.status == "force_closed" for r in closed2)
    assert stack.depth == 0


def test_reap_only_expired_from_outer_index() -> None:
    stack = tx.TxStack()
    stack.push(_rec("txn_a", depth=1, mono=0.0))  # age 400 → expired
    stack.push(_rec("txn_b", depth=2, mono=350.0))  # age 50 → not expired alone
    closed = stack.reap_expired(now_mono=400.0, timeout_s=300.0)
    # outermost expired → close from top through outer
    assert [r.transaction_id for r in closed] == ["txn_b", "txn_a"]
    assert stack.depth == 0


# ---------------------------------------------------------------------------
# force_close mid-stack
# ---------------------------------------------------------------------------


def test_force_close_mid_stack_closes_id_and_above() -> None:
    stack = tx.TxStack()
    stack.push(_rec("txn_bottom", depth=1))
    stack.push(_rec("txn_mid", depth=2))
    stack.push(_rec("txn_top", depth=3))

    closed = stack.force_close_from("txn_mid")
    assert closed is not None
    assert [r.transaction_id for r in closed] == ["txn_top", "txn_mid"]
    assert all(r.status == "force_closed" for r in closed)
    assert stack.depth == 1
    remaining = stack.top()
    assert remaining is not None
    assert remaining.transaction_id == "txn_bottom"


def test_force_close_unknown_id() -> None:
    stack = tx.TxStack()
    stack.push(_rec("txn_a", depth=1))
    assert stack.force_close_from("txn_missing") is None
    assert stack.depth == 1


def test_force_close_all() -> None:
    stack = tx.TxStack()
    stack.push(_rec("txn_a", depth=1))
    stack.push(_rec("txn_b", depth=2))
    closed = stack.force_close_all()
    assert [r.transaction_id for r in closed] == ["txn_b", "txn_a"]
    assert stack.depth == 0


# ---------------------------------------------------------------------------
# RecentClosed ring
# ---------------------------------------------------------------------------


def test_recent_closed_cap_10() -> None:
    recent = tx.RecentClosed(maxlen=10)
    for i in range(12):
        rec = _rec(f"txn_{i:02d}", depth=1)
        rec.status = "committed"
        rec.closed_at = float(i)
        recent.push(rec)
    listed = recent.list()
    assert len(listed) == 10
    assert listed[0].transaction_id == "txn_02"
    assert listed[-1].transaction_id == "txn_11"


# ---------------------------------------------------------------------------
# Packaging triad + EXPECTED ship #10
# ---------------------------------------------------------------------------


def test_expected_plugin_files_has_tx_len_10() -> None:
    assert "gimp_mcp_tx.py" in pathmod.EXPECTED_PLUGIN_FILES
    assert len(pathmod.EXPECTED_PLUGIN_FILES) == 10
    assert pathmod.EXPECTED_PLUGIN_FILES.count("gimp_mcp_tx.py") == 1


def test_pyproject_registers_tx_triad() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "gimp_mcp_tx" in text
    # packaging triad: py-modules + isort known-first-party + basedpyright include
    assert text.count("gimp_mcp_tx") >= 3


def test_tx_module_stdlib_only() -> None:
    text = Path("gimp_mcp_tx.py").read_text(encoding="utf-8")
    assert "from PIL" not in text
    assert "import gi" not in text
    assert "gi.repository" not in text
    assert "import gimp_mcp_security" not in text
    assert "from gimp_mcp_security" not in text


def test_security_tx_codes_and_envelope_honesty() -> None:
    import gimp_mcp_security as sec

    assert sec.CODE_TX_MISMATCH == "TX_MISMATCH"
    assert sec.CODE_TX_NOT_FOUND == "TX_NOT_FOUND"
    assert sec.CODE_TX_DEPTH == "TX_DEPTH"
    for code in (sec.CODE_TX_MISMATCH, sec.CODE_TX_NOT_FOUND, sec.CODE_TX_DEPTH):
        assert code in sec.CODE_DEFAULTS
        spec = sec.CODE_DEFAULTS[code]
        assert spec["retryable"] is False
        assert spec["state_may_have_changed"] is False

    rid = sec.new_request_id()
    env_true = sec.build_error_envelope(
        sec.CODE_INTERNAL,
        "mid-tx fail",
        request_id=rid,
        rollback_available=True,
        transaction_id="txn_abc",
    )
    assert env_true["error"]["rollback_available"] is True
    assert env_true["error"]["transaction_id"] == "txn_abc"

    env_default = sec.build_error_envelope(
        sec.CODE_INTERNAL,
        "no tx",
        request_id=rid,
    )
    assert env_default["error"]["rollback_available"] is False


def test_exit_codes_tx_map_to_6() -> None:
    import gimp_mcp_security as sec
    from gimp_agent import exit_codes as ec

    assert ec.exit_code_for(sec.CODE_TX_MISMATCH) == ec.EXIT_POLICY
    assert ec.exit_code_for(sec.CODE_TX_NOT_FOUND) == ec.EXIT_POLICY
    assert ec.exit_code_for(sec.CODE_TX_DEPTH) == ec.EXIT_POLICY
    assert ec.exit_code_for(sec.CODE_TX_MISMATCH) == 6


def test_hl_catalog_28_tx_tools() -> None:
    from gimp_mcp_surface import HL_TOOL_NAMES, get_hl_catalog_names, is_hl_tool

    names = get_hl_catalog_names()
    assert len(names) == 28
    assert set(names) == HL_TOOL_NAMES
    assert "undo_group_begin" in HL_TOOL_NAMES
    assert "undo_group_end" in HL_TOOL_NAMES
    assert "undo_group_rollback" in HL_TOOL_NAMES
    assert is_hl_tool("undo_group_begin")
    assert not is_hl_tool("undo_group_status")
    assert not is_hl_tool("undo_group_force_close")


def test_capability_undo_group_transactions() -> None:
    from gimp_mcp_state import default_capabilities

    caps = default_capabilities()
    assert caps["undo_group_transactions"] is True


def test_raise_from_plugin_result_forwards_rollback_fields() -> None:
    from unittest.mock import patch

    import pytest
    from fastmcp.exceptions import ToolError

    import gimp_mcp_security as sec
    import gimp_mcp_server as srv

    result = {
        "status": "error",
        "code": sec.CODE_POLICY_DENIED,
        "error": "Source_Immutable protected",
        "rollback_available": True,
        "transaction_id": "txn_deadbeef",
    }
    with patch.object(srv.sec, "write_audit_event"):
        with pytest.raises(ToolError) as ei:
            srv.raise_from_plugin_result(
                result,
                "apply_nde_filter",
                request_id="req_" + "e" * 32,
            )
    parsed = sec.parse_tool_error_text(str(ei.value))
    assert parsed is not None
    assert parsed["error"]["code"] == sec.CODE_POLICY_DENIED
    assert parsed["error"]["rollback_available"] is True
    assert parsed["error"]["transaction_id"] == "txn_deadbeef"


# ---------------------------------------------------------------------------
# Pure helpers: image_id_from_params + enrich_error_with_open_tx (P1 / P2-1)
# ---------------------------------------------------------------------------


def test_image_id_from_params_handle_and_layer_handle() -> None:
    assert tx.image_id_from_params(None) is None
    assert tx.image_id_from_params({}) is None
    assert tx.image_id_from_params({"handle": {"image_id": 7}}) == 7
    # layer_handle (HL NDE) without handle
    assert tx.image_id_from_params({"layer_handle": {"image_id": 42, "item_id": 9}}) == 42
    # handle preferred when both present
    assert (
        tx.image_id_from_params(
            {
                "handle": {"image_id": 1},
                "layer_handle": {"image_id": 2},
            }
        )
        == 1
    )
    assert tx.image_id_from_params({"source_handle": {"image_id": 99}}) == 99
    assert tx.image_id_from_params({"image_id": 3}) == 3
    assert tx.image_id_from_params({"layer_handle": {"item_id": 1}}) is None
    assert tx.image_id_from_params({"handle": "not-a-dict"}) is None


def test_enrich_error_with_open_tx_stamps_when_absent() -> None:
    err = {"status": "error", "code": "POLICY_DENIED", "error": "Source_Immutable"}
    out = tx.enrich_error_with_open_tx(err, open_transaction_id="txn_abc123")
    assert out["rollback_available"] is True
    assert out["transaction_id"] == "txn_abc123"
    # mutates in place for send-path efficiency
    assert out is err


def test_enrich_error_with_open_tx_no_open_or_success_or_explicit() -> None:
    bare = {"status": "error", "error": "x"}
    assert tx.enrich_error_with_open_tx(bare, open_transaction_id=None) is bare
    assert "rollback_available" not in bare

    ok = {"status": "success", "results": {}}
    assert tx.enrich_error_with_open_tx(ok, open_transaction_id="txn_x") is ok
    assert "rollback_available" not in ok

    # Explicit False must not be overridden (trust producer)
    explicit = {
        "status": "error",
        "error": "no tx",
        "rollback_available": False,
    }
    out = tx.enrich_error_with_open_tx(explicit, open_transaction_id="txn_x")
    assert out["rollback_available"] is False
    assert "transaction_id" not in out or out.get("transaction_id") != "txn_x"


def test_plugin_send_path_calls_enrich_error_with_tx() -> None:
    """Structure: client send path stamps TX fields before json.dumps."""
    body = Path("gimp-mcp-plugin.py").read_text(encoding="utf-8")
    assert "def _enrich_error_with_tx" in body
    assert "self._enrich_error_with_tx(" in body
    assert "strip_traceback_unless_debug" in body
    # enrich sits near send serialization
    idx_strip = body.find("strip_traceback_unless_debug")
    idx_enrich = body.find("_enrich_error_with_tx", idx_strip)
    idx_dumps = body.find("json.dumps(response)", idx_strip)
    assert idx_strip > 0
    assert idx_enrich > idx_strip
    assert idx_dumps > idx_enrich


def test_plugin_reap_uses_image_id_from_params() -> None:
    """Structure: dispatch reap resolves layer_handle via pure helper."""
    body = Path("gimp-mcp-plugin.py").read_text(encoding="utf-8")
    assert "def _maybe_reap_on_dispatch" in body
    # Must use shared pure resolver (not handle-only)
    reap_start = body.find("def _maybe_reap_on_dispatch")
    reap_end = body.find("\n    def ", reap_start + 1)
    reap_src = body[reap_start:reap_end]
    assert "image_id_from_params" in reap_src
    assert "layer_handle" in body  # documented / used in pure keys


def test_partial_rollback_undo_fail_pops_stack() -> None:
    """Structure: after undo_group_end ok + image.undo fail (False or raise), stack popped."""
    body = Path("gimp-mcp-plugin.py").read_text(encoding="utf-8")
    rb_start = body.find("def _tx_rollback")
    rb_end = body.find("\n    def _tx_status", rb_start)
    assert rb_start > 0 and rb_end > rb_start
    rb_src = body[rb_start:rb_end]
    assert "image.undo()" in rb_src or "image.undo(" in rb_src
    # gboolean False and exceptions both fail (Codex P1)
    assert "undo_ok is False" in rb_src or "is False" in rb_src
    # On undo failure path: pop + force_closed + recent + state_may_have_changed
    assert "force_closed" in rb_src
    assert "stack.pop()" in rb_src
    assert "state_may_have_changed=True" in rb_src
    assert "group closed" in rb_src or "stack cleared" in rb_src


def test_sync_image_generations_prunes_tx_stacks() -> None:
    body = Path("gimp-mcp-plugin.py").read_text(encoding="utf-8")
    sync_start = body.find("def _sync_image_generations")
    sync_end = body.find("\n    def _pixel_orientation_normalized", sync_start)
    sync_src = body[sync_start:sync_end]
    assert "_agent_tx_stack" in sync_src
    assert "_agent_tx_recent" in sync_src


def test_raise_from_plugin_result_no_host_hint_when_fields_absent() -> None:
    """TCP path: plugin is SoT — omit fields → false (do not use stale host hint).

    Host open-TX hint is for pre-TCP tool_fail only (Codex P2).
    """
    from unittest.mock import patch

    import pytest
    from fastmcp.exceptions import ToolError

    import gimp_mcp_security as sec
    import gimp_mcp_server as srv

    # Stale host hint must NOT override plugin omission on TCP errors
    srv._HOST_OPEN_TX.clear()
    srv._host_tx_hint_set(55, "txn_host_hint_55")

    result = {
        "status": "error",
        "code": sec.CODE_POLICY_DENIED,
        "error": "Source_Immutable protected",
        # no rollback_available / transaction_id from plugin → no open TX on SoT
    }
    try:
        with patch.object(srv.sec, "write_audit_event"):
            with pytest.raises(ToolError) as ei:
                srv.raise_from_plugin_result(
                    result,
                    "apply_nde_filter",
                    request_id="req_" + "f" * 32,
                    image_id=55,
                )
        parsed = sec.parse_tool_error_text(str(ei.value))
        assert parsed is not None
        assert parsed["error"]["rollback_available"] is False
    finally:
        srv._HOST_OPEN_TX.clear()


def test_raise_from_plugin_result_trusts_explicit_false() -> None:
    """Plugin explicit rollback_available=False must not be overridden by host hint."""
    from unittest.mock import patch

    import pytest
    from fastmcp.exceptions import ToolError

    import gimp_mcp_security as sec
    import gimp_mcp_server as srv

    srv._HOST_OPEN_TX.clear()
    srv._host_tx_hint_set(55, "txn_host_hint_55")
    result = {
        "status": "error",
        "code": sec.CODE_INTERNAL,
        "error": "plugin says no rollback",
        "rollback_available": False,
    }
    try:
        with patch.object(srv.sec, "write_audit_event"):
            with pytest.raises(ToolError) as ei:
                srv.raise_from_plugin_result(
                    result,
                    "tool",
                    request_id="req_" + "a" * 32,
                    image_id=55,
                )
        parsed = sec.parse_tool_error_text(str(ei.value))
        assert parsed is not None
        assert parsed["error"]["rollback_available"] is False
    finally:
        srv._HOST_OPEN_TX.clear()


def test_tool_fail_reads_contextvar_image_id() -> None:
    """with_structured_error contextvar enables host hint without explicit image_id."""
    from unittest.mock import patch

    import pytest
    from fastmcp.exceptions import ToolError

    import gimp_mcp_security as sec
    import gimp_mcp_server as srv

    srv._HOST_OPEN_TX.clear()
    srv._host_tx_hint_set(77, "txn_ctx_77")
    token = srv._current_tool_image_id.set(77)
    try:
        with patch.object(srv.sec, "write_audit_event"):
            with pytest.raises(ToolError) as ei:
                srv.tool_fail(
                    sec.CODE_INTERNAL,
                    "mid-tx mutator fail",
                    request_id="req_" + "b" * 32,
                )
        parsed = sec.parse_tool_error_text(str(ei.value))
        assert parsed is not None
        assert parsed["error"]["rollback_available"] is True
        assert parsed["error"]["transaction_id"] == "txn_ctx_77"
    finally:
        srv._current_tool_image_id.reset(token)
        srv._HOST_OPEN_TX.clear()


def test_image_id_from_tool_kwargs() -> None:
    import gimp_mcp_server as srv

    assert srv._image_id_from_tool_kwargs({}) is None
    assert srv._image_id_from_tool_kwargs({"handle": {"image_id": 3}}) == 3
    assert srv._image_id_from_tool_kwargs({"layer_handle": {"image_id": 9, "item_id": 1}}) == 9
