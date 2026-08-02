"""Offline regression tests for visible-composite snapshot (Issue 17 / track 0004).

Source-grep style (like 0003 security tests) — no live GIMP required.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

import gimp_mcp_snapshot as snap

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "gimp-mcp-plugin.py"
SERVER = ROOT / "gimp_mcp_server.py"


def _method_body(source: str, method_name: str) -> str:
    """Extract a class method body via indentation heuristic."""
    m = re.search(
        rf"(    def {re.escape(method_name)}\(self.*?)(?=\n    def |\nclass |\Z)",
        source,
        re.DOTALL,
    )
    assert m is not None, f"method {method_name} not found"
    return m.group(1)


# ---------------------------------------------------------------------------
# Plugin: _get_current_image_bitmap must use composite path
# ---------------------------------------------------------------------------


def test_bitmap_method_uses_duplicate_and_merge() -> None:
    text = PLUGIN.read_text(encoding="utf-8")
    body = _method_body(text, "_get_current_image_bitmap")
    assert "duplicate()" in body
    assert "merge_visible_layers" in body
    assert "CLIP_TO_IMAGE" in body or "MergeType" in body


def test_bitmap_method_captures_merge_flatten_return() -> None:
    """Export must use merge/flatten return layer, not bare call + layers[0] guess."""
    text = PLUGIN.read_text(encoding="utf-8")
    body = _method_body(text, "_get_current_image_bitmap")
    # Assign return of merge_visible_layers (not a bare discarded call only)
    assert re.search(r"=\s*.*merge_visible_layers\s*\(", body), (
        "merge_visible_layers return must be assigned for export drawable"
    )
    # Flatten fallback must also capture return
    assert re.search(r"=\s*.*\.flatten\s*\(", body), (
        "flatten() return must be assigned for export drawable"
    )
    # Must fail closed rather than only guessing selected/layers[0]
    assert "No drawable for export" in body or "returned no layer" in body


def test_bitmap_method_no_single_layer_edit_copy() -> None:
    text = PLUGIN.read_text(encoding="utf-8")
    body = _method_body(text, "_get_current_image_bitmap")
    # Banned Issue 17 path: copy from layers[0] only
    assert "edit_copy" not in body
    assert "orig_layers[0]" not in body
    assert "edit_paste" not in body


def test_bitmap_method_no_select_on_original() -> None:
    text = PLUGIN.read_text(encoding="utf-8")
    body = _method_body(text, "_get_current_image_bitmap")
    # Must not call select_rectangle on the user's image
    assert "select_rectangle" not in body
    # Selection.none only on dup is OK; original_image.select / Selection.none(original)
    # should not appear for region extraction
    assert "original_image.select" not in body
    assert "Selection.none(original" not in body


def test_bitmap_method_uses_get_image_not_bare_images0() -> None:
    text = PLUGIN.read_text(encoding="utf-8")
    body = _method_body(text, "_get_current_image_bitmap")
    assert "_get_image" in body
    # Ban bare first-image selection in this method
    assert "images[0]" not in body
    assert "Gimp.get_images()" not in body


def test_bitmap_method_uses_snapshot_temp_helpers() -> None:
    text = PLUGIN.read_text(encoding="utf-8")
    body = _method_body(text, "_get_current_image_bitmap")
    assert "snapshot_temp_path" in body or "_snap.snapshot_temp_path" in body
    # Must not use bare system-temp mkstemp without policy helper
    assert "tempfile.mkstemp" not in body


def test_bitmap_method_cleanup_deletes_dup() -> None:
    text = PLUGIN.read_text(encoding="utf-8")
    body = _method_body(text, "_get_current_image_bitmap")
    assert "finally" in body
    assert "dup.delete()" in body or "delete()" in body


def test_bitmap_method_selection_none_fail_closed() -> None:
    """Codex P2-1: Selection.none failure must not only warn-and-continue.

    Inherited selection can silently clip merge/flatten; clear failures re-raise.
    Also treat explicit boolean False return as failure (GIMP gboolean contract).
    """
    text = PLUGIN.read_text(encoding="utf-8")
    body = _method_body(text, "_get_current_image_bitmap")
    full = text
    # Helper must exist and check explicit False + exceptions
    assert "_selection_none_or_fail" in full
    assert "ok is False" in full or "ok is False" in body
    assert "Selection.none returned False" in full
    # Bitmap path uses helper (not bare warn-and-continue)
    assert "_selection_none_or_fail" in body
    assert "Selection.none on snapshot dup failed" in body
    assert "Selection.none before flatten failed" in body
    # Must not swallow selection clear with bare pass (flatten retries)
    assert not re.search(
        r"Gimp\.Selection\.none\(dup\)\s*\n"
        r"\s*except \(AttributeError, RuntimeError\).*?:\s*\n"
        r"\s*pass\b",
        body,
    ), "Selection.none must not be caught with bare pass"


def test_bitmap_method_export_validates_png() -> None:
    """Codex P2-2: export must not succeed on empty/invalid PNG (mkstemp residue)."""
    text = PLUGIN.read_text(encoding="utf-8")
    body = _method_body(text, "_get_current_image_bitmap")
    assert "validate_png_file" in body or "_snap.validate_png_file" in body
    assert "validate_png_bytes" in body or "_snap.validate_png_bytes" in body
    # Fail closed string for invalid export
    assert "empty/invalid" in body or "empty or non-PNG" in body
    # Drawable property both-fail must not silently run primary with pass
    assert "drawable_set" in body or "drawables property" in body
    # Ban pre-fix silent pass on both drawable property failures
    assert not re.search(
        r'set_property\("drawables".*?\n\s*except Exception:\s*\n\s*pass\b',
        body,
        re.DOTALL,
    ), "drawable/drawables both-fail must not bare-pass into export run"


def test_plugin_imports_snapshot_module() -> None:
    text = PLUGIN.read_text(encoding="utf-8")
    assert "import gimp_mcp_snapshot" in text
    # Fail-closed if missing (like security)
    assert "gimp_mcp_snapshot.py must sit next to" in text


def test_get_image_rejects_negative_index() -> None:
    text = PLUGIN.read_text(encoding="utf-8")
    body = _method_body(text, "_get_image")
    assert "image_index < 0" in body or "index < 0" in body
    assert "negative" in body.lower()


# ---------------------------------------------------------------------------
# Server: image_index + ToolResult path
# ---------------------------------------------------------------------------


def test_server_get_image_bitmap_has_image_index() -> None:
    text = SERVER.read_text(encoding="utf-8")
    # Function signature must include image_index
    m = re.search(
        r"def get_image_bitmap\((.*?)\)\s*(?:->|:)",
        text,
        re.DOTALL,
    )
    assert m is not None
    sig = m.group(1)
    assert "image_index" in sig


def test_server_snapshot_tools_return_tool_result() -> None:
    text = SERVER.read_text(encoding="utf-8")
    assert "from fastmcp.tools.tool import ToolResult" in text
    assert "structured_content" in text
    assert "_snapshot_tool_result" in text
    # Both tools should return ToolResult (annotation or explicit return)
    assert "def get_image_bitmap(" in text
    assert "def get_state_snapshot(" in text
    assert "-> ToolResult" in text
    # convert_result passthrough for ToolResult
    assert "convert_result" in text
    assert "to_mcp_result" in text


def test_server_docstrings_mention_visible_composite() -> None:
    text = SERVER.read_text(encoding="utf-8")
    # Extract get_image_bitmap and get_state_snapshot docstrings roughly
    for name in ("get_image_bitmap", "get_state_snapshot"):
        m = re.search(
            rf'def {name}\(.*?\n    """(.*?)"""',
            text,
            re.DOTALL,
        )
        assert m is not None, f"{name} docstring missing"
        doc = m.group(1).lower()
        assert "composite" in doc, f"{name} docstring must mention composite"


# ---------------------------------------------------------------------------
# Server helper unit test (no GIMP)
# ---------------------------------------------------------------------------


def test_snapshot_tool_result_builds_mapping() -> None:
    import gimp_mcp_server as server

    # Minimal PNG-ish payload (not a real PNG; ToolResult only base64-decodes)
    fake_png = b"\x89PNG\r\n\x1a\nfake"
    results = {
        "image_data": base64.b64encode(fake_png).decode("ascii"),
        "format": "png",
        "width": 256,
        "height": 192,
        "original_width": 1000,
        "original_height": 750,
        "encoding": "base64",
        "image_index": 0,
        "mode": "visible_composite",
        "scale_x": 256 / 1000,
        "scale_y": 192 / 750,
        "region": None,
        "composite_method": snap.COMPOSITE_METHOD_MERGE,
        "source_width": 1000,
        "source_height": 750,
        "rendered_width": 256,
        "rendered_height": 192,
    }
    tr = server._snapshot_tool_result(results, image_index=0)
    assert isinstance(tr, server.ToolResult)
    mcp_result = tr.to_mcp_result()
    assert isinstance(mcp_result, tuple)
    content, structured = mcp_result
    assert isinstance(structured, dict)
    assert structured["mode"] == "visible_composite"
    assert structured["source_width"] == 1000
    assert structured["rendered_width"] == 256
    assert structured["scale_x"] == pytest.approx(256 / 1000)
    assert structured["composite_method"] == snap.COMPOSITE_METHOD_MERGE
    assert isinstance(content, list)
    assert len(content) == 1
    assert content[0].type == "image"


def test_snapshot_tool_result_region_relative_scales() -> None:
    import gimp_mcp_server as server

    fake_png = b"\x89PNG\r\n\x1a\nreg"
    results = {
        "image_data": base64.b64encode(fake_png).decode("ascii"),
        "width": 100,
        "height": 50,
        "original_width": 4000,
        "original_height": 3000,
        "image_index": 1,
        "mode": "visible_composite",
        "scale_x": 100 / 200,
        "scale_y": 50 / 100,
        "region": {"origin_x": 10, "origin_y": 20, "width": 200, "height": 100},
        "composite_method": snap.COMPOSITE_METHOD_MERGE,
        "source_width": 4000,
        "source_height": 3000,
        "rendered_width": 100,
        "rendered_height": 50,
    }
    tr = server._snapshot_tool_result(results, image_index=1)
    mcp_result = tr.to_mcp_result()
    assert isinstance(mcp_result, tuple)
    _, structured = mcp_result
    assert isinstance(structured, dict)
    region = structured["region"]
    assert isinstance(region, dict)
    assert region["width"] == 200
    assert structured["scale_x"] == pytest.approx(0.5)
    assert structured["scale_y"] == pytest.approx(0.5)
    # Not full-canvas scale
    assert structured["scale_x"] != pytest.approx(100 / 4000)


def test_convert_result_passthrough_for_tool_result() -> None:
    """Patched FuncMetadata.convert_result must return (content, structured) for ToolResult."""
    from mcp.server.fastmcp.utilities.func_metadata import FuncMetadata

    import gimp_mcp_server as server

    fake_png = b"\x89PNG\r\n\x1a\ncvt"
    results = {
        "image_data": base64.b64encode(fake_png).decode("ascii"),
        "format": "png",
        "width": 64,
        "height": 48,
        "original_width": 320,
        "original_height": 240,
        "encoding": "base64",
        "image_index": 0,
        "mode": "visible_composite",
        "scale_x": 64 / 320,
        "scale_y": 48 / 240,
        "region": None,
        "composite_method": snap.COMPOSITE_METHOD_MERGE,
        "source_width": 320,
        "source_height": 240,
        "rendered_width": 64,
        "rendered_height": 48,
    }
    tr = server._snapshot_tool_result(results, image_index=0)
    assert isinstance(tr, server.ToolResult)

    # Invoke the monkey-patched class method (self unused on ToolResult branch)
    out = FuncMetadata.convert_result(None, tr)  # type: ignore[arg-type]
    assert isinstance(out, tuple)
    assert len(out) == 2
    content_list, structured_dict = out
    assert isinstance(content_list, list)
    assert len(content_list) == 1
    assert content_list[0].type == "image"
    assert isinstance(structured_dict, dict)
    assert structured_dict["mode"] == "visible_composite"
    assert structured_dict["source_width"] == 320
    assert structured_dict["rendered_width"] == 64
    assert structured_dict["composite_method"] == snap.COMPOSITE_METHOD_MERGE
    assert "scale_x" in structured_dict
    assert "image_index" in structured_dict
