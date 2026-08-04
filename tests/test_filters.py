"""Offline tests for track 0016 NDE filter tools (pure module + surface wiring)."""

from __future__ import annotations

from pathlib import Path

import pytest

import gimp_mcp_filters as filters
import gimp_mcp_security as sec
from gimp_agent import paths as pathmod
from gimp_mcp_surface import HL_TOOL_NAMES, get_hl_catalog_names, is_hl_tool

# ---------------------------------------------------------------------------
# Allowlist / validate_operation
# ---------------------------------------------------------------------------


def test_allowlisted_ops_accepted() -> None:
    for op in (
        "gegl:gaussian-blur",
        "gegl:unsharp-mask",
        "gegl:brightness-contrast",
        "gimp:levels",
        "gimp:curves",
    ):
        result = filters.validate_operation(op)
        assert result["ok"] is True
        assert result["operation"] == op


def test_unknown_op_rejected() -> None:
    result = filters.validate_operation("gegl:dropshadow")
    assert result["ok"] is False
    assert result["code"] == filters.CODE_UNSUPPORTED
    assert "allowlist" in result["message"].lower() or "not in" in result["message"].lower()


def test_dropshadow_not_in_allowlist() -> None:
    assert "gegl:dropshadow" not in filters.ALLOWED_OPS
    assert "gegl:drop-shadow" not in filters.ALLOWED_OPS
    assert "gegl:dropshadow" not in filters.ALLOWED_OPS


def test_allowlist_has_13_ops() -> None:
    assert len(filters.ALLOWED_OPS) == 13
    assert "gegl:gaussian-blur" in filters.ALLOWED_OPS
    assert "gimp:levels" in filters.ALLOWED_OPS
    assert "gimp:curves" in filters.ALLOWED_OPS


def test_empty_op_rejected() -> None:
    assert filters.validate_operation("")["ok"] is False
    assert filters.validate_operation("   ")["ok"] is False


# ---------------------------------------------------------------------------
# Soft config keys (H5)
# ---------------------------------------------------------------------------


def test_soft_config_does_not_fail_on_bogus_key() -> None:
    result = filters.validate_config_keys(
        {"std-dev-x": 5.0, "bogus": 1},
        "gegl:gaussian-blur",
    )
    assert result["ok"] is True
    assert "bogus" in result["unknown_keys"]
    # Still ok — soft policy never rejects
    assert result["operation"] == "gegl:gaussian-blur"


def test_soft_config_none_ok() -> None:
    result = filters.validate_config_keys(None, "gegl:pixelize")
    assert result["ok"] is True


def test_soft_config_unknown_op_still_fails_op() -> None:
    result = filters.validate_config_keys({"x": 1}, "gegl:not-a-real-op")
    assert result["ok"] is False
    assert result["code"] == filters.CODE_UNSUPPORTED


# ---------------------------------------------------------------------------
# Runtime probe flags / expand class
# ---------------------------------------------------------------------------


def test_requires_runtime_probe_gimp_ops() -> None:
    assert filters.requires_runtime_probe("gimp:levels") is True
    assert filters.requires_runtime_probe("gimp:curves") is True
    assert filters.requires_runtime_probe("gegl:gaussian-blur") is False
    assert filters.requires_runtime_probe("gegl:not-real") is False


def test_check_runtime_probe_unavailable_unsupported() -> None:
    """DoD-5: gimp:* with available list missing op → UNSUPPORTED (pure)."""
    decision = filters.check_runtime_probe("gimp:levels", ["gegl:gaussian-blur", "gimp:other"])
    assert decision["ok"] is False
    assert decision["code"] == filters.CODE_UNSUPPORTED
    assert decision["details"]["reason"] == "operation_get_available"
    assert decision["details"]["operation"] == "gimp:levels"
    assert "not available" in decision["message"].lower()


def test_check_runtime_probe_available_ok() -> None:
    decision = filters.check_runtime_probe("gimp:curves", ["gimp:levels", "gimp:curves"])
    assert decision["ok"] is True
    assert decision["probed"] is True
    assert decision["operation"] == "gimp:curves"


def test_check_runtime_probe_api_missing_allows_try() -> None:
    """None available list → probe API missing; allow try new() (ok True)."""
    decision = filters.check_runtime_probe("gimp:levels", None)
    assert decision["ok"] is True
    assert decision["probed"] is False


def test_check_runtime_probe_non_probe_ops_skip() -> None:
    # Non-probe ops always ok even with empty available list
    decision = filters.check_runtime_probe("gegl:gaussian-blur", [])
    assert decision["ok"] is True
    assert decision["probed"] is False


def test_is_expand_class_op() -> None:
    assert filters.is_expand_class_op("gegl:vignette") is True
    assert filters.is_expand_class_op("gegl:gaussian-blur") is False
    note = filters.expand_class_note("gegl:vignette")
    assert note is not None
    assert "bounds" in note.lower() or "clip" in note.lower()
    assert filters.expand_class_note("gegl:gaussian-blur") is None


# ---------------------------------------------------------------------------
# Blend modes
# ---------------------------------------------------------------------------


def test_blend_modes_include_replace() -> None:
    assert "REPLACE" in filters.ALLOWED_BLEND_MODES
    assert "NORMAL" in filters.ALLOWED_BLEND_MODES
    result = filters.validate_blend_mode(None)
    assert result["ok"] is True
    assert result["blend_mode"] == "REPLACE"
    assert filters.validate_blend_mode("multiply")["blend_mode"] == "MULTIPLY"
    bad = filters.validate_blend_mode("NOT_A_MODE")
    assert bad["ok"] is False


# ---------------------------------------------------------------------------
# Summary normalize
# ---------------------------------------------------------------------------


def test_normalize_filter_summary() -> None:
    raw = {
        "filter_id": 42,
        "name": "Gaussian Blur",
        "operation_name": "gegl:gaussian-blur",
        "visible": True,
        "opacity": 1.0,
        "blend_mode": "REPLACE",
        "config": {"std-dev-x": 5.0, "std-dev-y": 5.0},
    }
    full = filters.normalize_filter_summary(raw, include_config=True)
    assert full["filter_id"] == 42
    assert full["operation_name"] == "gegl:gaussian-blur"
    assert full["config"]["std-dev-x"] == 5.0
    assert full["blend_mode"] == "REPLACE"

    no_cfg = filters.normalize_filter_summary(raw, include_config=False)
    assert "config" not in no_cfg
    assert no_cfg["filter_id"] == 42


def test_normalize_filter_summary_defaults() -> None:
    out = filters.normalize_filter_summary({})
    assert out["filter_id"] is None
    assert out["visible"] is True
    assert out["opacity"] == 1.0
    assert out["blend_mode"] == "REPLACE"


# ---------------------------------------------------------------------------
# Pure coerce helper
# ---------------------------------------------------------------------------


def test_coerce_config_value_types() -> None:
    assert filters.coerce_config_value(5, "DOUBLE") == 5.0
    assert filters.coerce_config_value(5.7, "INT") == 5
    assert filters.coerce_config_value(1, "BOOLEAN") is True
    assert filters.coerce_config_value(0, "BOOLEAN") is False
    assert filters.coerce_config_value("true", "BOOLEAN") is True
    assert filters.coerce_config_value(3.14, "STRING") == "3.14"
    assert filters.coerce_config_value(9, "TYPE_DOUBLE") == 9.0
    assert filters.coerce_config_value("keep", None) == "keep"
    assert filters.coerce_config_value([1, 2], "UNKNOWN_OBJ") == [1, 2]


def test_set_config_props_applied_and_ignored() -> None:
    """Honesty contract: successful keys in applied_props; failures in ignored_props."""

    class _Pspec:
        def __init__(self, type_name: str) -> None:
            self.type_name = type_name

    class FakeCfg:
        def __init__(self) -> None:
            self.store: dict[str, object] = {}
            self._types = {
                "std-dev-x": _Pspec("DOUBLE"),
                "std-dev-y": _Pspec("DOUBLE"),
                # bad-key exists but set_property will raise
                "bad-key": _Pspec("DOUBLE"),
            }

        def find_property(self, key: str):
            return self._types.get(key)

        def set_property(self, key: str, value: object) -> None:
            if key == "bad-key":
                raise RuntimeError("property write refused")
            self.store[key] = value

    cfg = FakeCfg()

    def pspec_type_name(pspec: object) -> str | None:
        return getattr(pspec, "type_name", None)

    applied, ignored = filters.set_config_props(
        {
            "std-dev-x": 5,
            "std-dev-y": 3.5,
            "bad-key": 1.0,
            "missing-prop": 9,
        },
        set_property=cfg.set_property,
        find_property=cfg.find_property,
        pspec_type_name=pspec_type_name,
    )
    assert "std-dev-x" in applied
    assert "std-dev-y" in applied
    assert cfg.store["std-dev-x"] == 5.0
    assert cfg.store["std-dev-y"] == 3.5
    ignored_keys = {e["key"] for e in ignored}
    assert "bad-key" in ignored_keys
    # missing-prop: no pspec → type_name None → pass-through set succeeds
    # (plugin may still set unknown keys if GI accepts them)
    bad_entry = next(e for e in ignored if e["key"] == "bad-key")
    assert "refused" in bad_entry["error"].lower() or "error" in bad_entry


def test_set_config_props_non_object_config() -> None:
    # Non-empty non-dict (empty string is falsy and treated as no config)
    applied, ignored = filters.set_config_props(
        ["not", "a", "dict"],  # type: ignore[arg-type]
        set_property=lambda _k, _v: None,
    )
    assert applied == []
    assert ignored == [{"key": "*", "error": "config must be an object"}]


# ---------------------------------------------------------------------------
# Host pre-validate → UNSUPPORTED (R1-F1)
# ---------------------------------------------------------------------------


def _host_raise_unsupported(result: dict) -> None:
    """Mirror host apply_nde_filter / edit_filter_config validation raise path."""
    if not result.get("ok"):
        raise sec.GimpMcpError(
            sec.CODE_UNSUPPORTED,
            str(result.get("message", "unsupported")),
            details=result.get("details") if isinstance(result.get("details"), dict) else None,
        )


def test_host_prevalidate_unknown_op_raises_unsupported() -> None:
    op_v = filters.validate_operation("gegl:dropshadow")
    assert op_v["ok"] is False
    with pytest.raises(sec.GimpMcpError) as ei:
        _host_raise_unsupported(op_v)
    assert ei.value.code == sec.CODE_UNSUPPORTED
    assert ei.value.code != sec.CODE_INTERNAL


def test_host_prevalidate_bad_blend_raises_unsupported() -> None:
    bm_v = filters.validate_blend_mode("NOT_A_MODE")
    assert bm_v["ok"] is False
    with pytest.raises(sec.GimpMcpError) as ei:
        _host_raise_unsupported(bm_v)
    assert ei.value.code == sec.CODE_UNSUPPORTED


def test_server_nde_tools_use_gimp_mcp_error_unsupported() -> None:
    """Structure: host NDE tools raise GimpMcpError(CODE_UNSUPPORTED), not bare Exception."""
    text = Path("gimp_mcp_server.py").read_text(encoding="utf-8")
    for name in ("def apply_nde_filter", "def edit_filter_config"):
        start = text.find(name)
        assert start != -1, name
        next_def = text.find("\n@mcp.tool", start + 10)
        body = text[start : next_def if next_def != -1 else start + 5000]
        assert "GimpMcpError" in body, f"{name} must raise GimpMcpError"
        assert "CODE_UNSUPPORTED" in body, f"{name} must use CODE_UNSUPPORTED"
        assert "raise Exception(" not in body, f"{name} must not raise bare Exception"
        assert "sec.GimpMcpError" in body or "GimpMcpError" in body
        assert "(ToolError, sec.SecurityError, sec.GimpMcpError)" in body


# ---------------------------------------------------------------------------
# Packaging / EXPECTED ship set
# ---------------------------------------------------------------------------


def test_filters_in_expected_plugin_files_len_9() -> None:
    expected = {
        "gimp-mcp-plugin.py",
        "gimp_mcp_security.py",
        "gimp_mcp_snapshot.py",
        "gimp_mcp_export.py",
        "gimp_mcp_handles.py",
        "gimp_mcp_coords.py",
        "gimp_mcp_policy.py",
        "gimp_mcp_atomic.py",
        "gimp_mcp_filters.py",
    }
    assert set(pathmod.EXPECTED_PLUGIN_FILES) == expected
    assert len(pathmod.EXPECTED_PLUGIN_FILES) == 9
    assert "gimp_mcp_filters.py" in pathmod.EXPECTED_PLUGIN_FILES
    # Host-only modules stay out of ship set
    assert "gimp_mcp_state.py" not in pathmod.EXPECTED_PLUGIN_FILES
    assert "gimp_mcp_surface.py" not in pathmod.EXPECTED_PLUGIN_FILES
    assert "gimp_mcp_recipes.py" not in pathmod.EXPECTED_PLUGIN_FILES
    assert "gimp_mcp_verify.py" not in pathmod.EXPECTED_PLUGIN_FILES


def test_filters_module_stdlib_only() -> None:
    text = Path("gimp_mcp_filters.py").read_text(encoding="utf-8")
    assert "from PIL" not in text
    assert "import gi" not in text
    assert "gi.repository" not in text
    assert "import gimp_mcp_security" not in text
    assert "from gimp_mcp_security" not in text


def test_pyproject_registers_filters() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "gimp_mcp_filters" in text
    # packaging triad locations
    assert text.count("gimp_mcp_filters") >= 3


# ---------------------------------------------------------------------------
# HL catalog 25 + advanced not HL
# ---------------------------------------------------------------------------


def test_hl_catalog_exact_25_includes_nde() -> None:
    names = get_hl_catalog_names()
    assert len(names) == 25
    assert set(names) == HL_TOOL_NAMES
    assert "apply_nde_filter" in HL_TOOL_NAMES
    assert "edit_filter_config" in HL_TOOL_NAMES
    assert "remove_nde_filter" in HL_TOOL_NAMES
    assert is_hl_tool("apply_nde_filter")
    assert is_hl_tool("edit_filter_config")
    assert is_hl_tool("remove_nde_filter")
    # Advanced tools must NOT be HL
    assert not is_hl_tool("list_drawable_filters")
    assert not is_hl_tool("merge_nde_filters")
    assert "list_drawable_filters" not in HL_TOOL_NAMES
    assert "merge_nde_filters" not in HL_TOOL_NAMES


def test_capability_nde_filters_true() -> None:
    from gimp_mcp_state import default_capabilities

    caps = default_capabilities()
    assert caps["nde_filters"] is True


# ---------------------------------------------------------------------------
# Structure greps (plugin source contracts)
# ---------------------------------------------------------------------------


def test_plugin_apply_update_before_append() -> None:
    text = Path("gimp-mcp-plugin.py").read_text(encoding="utf-8")
    # Locate apply_nde_filter method body region
    start = text.find("def _apply_nde_filter")
    assert start != -1, "plugin must define _apply_nde_filter"
    # Look ahead to next def at same class indent-ish region (next method)
    next_def = text.find("\n    def _", start + 10)
    body = text[start : next_def if next_def != -1 else start + 8000]
    update_pos = body.find(".update(")
    # also accept _sync_filter which calls update
    sync_pos = body.find("_sync_filter")
    append_pos = body.find("append_filter")
    assert append_pos != -1, "apply must call append_filter"
    # Either direct update or _sync_filter must appear before append
    sync_or_update = min(p for p in (update_pos, sync_pos) if p != -1)
    assert sync_or_update < append_pos, "update/_sync_filter must precede append_filter"
    # Must not bake-merge by default
    assert "merge_filter" not in body or body.find("merge_filter") > append_pos
    # Prefer: no merge in apply path at all for default NDE
    # (merge_filters may appear in comments; hard check: no drawable.merge)
    assert "merge_filters" not in body
    assert "merge_new_filter" not in body


def test_plugin_has_sync_filter_and_helpers() -> None:
    text = Path("gimp-mcp-plugin.py").read_text(encoding="utf-8")
    assert "def _sync_filter" in text
    assert "def _emit_filter_summaries" in text
    assert "def _resolve_filter_on_layer" in text
    assert "def _set_filter_config_props" in text
    assert '"REPLACE"' in text or "'REPLACE'" in text
    assert "gimp_mcp_filters" in text
    assert "def _edit_filter_config" in text
    assert "def _remove_nde_filter" in text
    assert "def _list_drawable_filters" in text
    assert "def _merge_nde_filters" in text
    # Prefer NDE note on legacy path
    assert "apply_nde_filter" in text
    # No gen bump on filter ops: apply_nde should not call _bump_image_generation
    start = text.find("def _apply_nde_filter")
    next_def = text.find("\n    def _", start + 10)
    body = text[start:next_def]
    assert "_bump_image_generation" not in body


def test_plugin_merge_result_keys_documented() -> None:
    text = Path("gimp-mcp-plugin.py").read_text(encoding="utf-8")
    start = text.find("def _merge_nde_filters")
    assert start != -1
    next_def = text.find("\n    def _", start + 10)
    body = text[start : next_def if next_def != -1 else start + 6000]
    assert "merged_count" in body
    assert "merged_filter_ids" in body
    assert "confirm_destructive" in body or "_require_confirm_destructive" in body


def test_plugin_gimp_probe_unsupported_path() -> None:
    """Structure: gimp:* ops probe via pure check_runtime_probe before new."""
    text = Path("gimp-mcp-plugin.py").read_text(encoding="utf-8")
    start = text.find("def _apply_nde_filter")
    next_def = text.find("\n    def _", start + 10)
    body = text[start:next_def]
    assert "operation_get_available" in body or "check_runtime_probe" in body
    assert "requires_runtime_probe" in body
    assert "check_runtime_probe" in body
    assert "CODE_UNSUPPORTED" in body


def test_plugin_edit_validates_before_mutate() -> None:
    """R1-F2: blend/opacity/config validation precedes set_visible / undo mutations."""
    text = Path("gimp-mcp-plugin.py").read_text(encoding="utf-8")
    start = text.find("def _edit_filter_config")
    assert start != -1
    next_def = text.find("\n    def _", start + 10)
    body = text[start : next_def if next_def != -1 else start + 8000]
    validate_blend_pos = body.find("validate_blend_mode")
    undo_pos = body.find("undo_group_start")
    set_visible_pos = body.find("set_visible")
    assert validate_blend_pos != -1
    assert undo_pos != -1
    assert validate_blend_pos < undo_pos, "blend validate must precede undo_group_start"
    if set_visible_pos != -1:
        assert validate_blend_pos < set_visible_pos, "blend validate must precede set_visible"


def test_orient_uses_emit_filter_summaries() -> None:
    text = Path("gimp-mcp-plugin.py").read_text(encoding="utf-8")
    # Must not hard-code empty filters list as sole path
    assert "_emit_filter_summaries" in text
    # orient layer node should call emit
    start = text.find("def _orient_layer_node")
    next_def = text.find("\n    def _", start + 10)
    body = text[start:next_def]
    assert "_emit_filter_summaries" in body
    # empty stub should be gone as sole assignment
    assert '"filters": []' not in body or "_emit_filter_summaries" in body
    # R1-F5: include_config=not summary_only
    assert "include_config=not summary_only" in body
    assert "summary_only" in body


def test_surface_and_state_wiring() -> None:
    surface_text = Path("gimp_mcp_surface.py").read_text(encoding="utf-8")
    assert "apply_nde_filter" in surface_text
    assert "edit_filter_config" in surface_text
    assert "remove_nde_filter" in surface_text
    # comments say 25
    assert "25" in surface_text

    state_text = Path("gimp_mcp_state.py").read_text(encoding="utf-8")
    assert "nde_filters" in state_text

    server_text = Path("gimp_mcp_server.py").read_text(encoding="utf-8")
    assert "def apply_nde_filter" in server_text
    assert "def edit_filter_config" in server_text
    assert "def remove_nde_filter" in server_text
    assert "def list_drawable_filters" in server_text
    assert "def merge_nde_filters" in server_text
    assert "gimp_mcp_filters" in server_text
