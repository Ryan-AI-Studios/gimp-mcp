"""Offline tests for state-manifest orientation (track 0006).

stdlib structural validation is SoT; optional Draft202012Validator when
jsonschema is importable (transitive via mcp — do not require as prod dep).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

import gimp_mcp_state as state

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "gimp-mcp-plugin.py"
SERVER = ROOT / "gimp_mcp_server.py"
STATE_MOD = ROOT / "gimp_mcp_state.py"
SCHEMA_PATH = ROOT / "schemas" / "state-manifest.v1.json"


def _method_body(source: str, method_name: str) -> str:
    m = re.search(
        rf"(    def {re.escape(method_name)}\(\s*self.*?)(?=\n    def |\nclass |\Z)",
        source,
        re.DOTALL,
    )
    assert m is not None, f"method {method_name} not found"
    return m.group(1)


def _function_body(source: str, func_name: str) -> str:
    m = re.search(
        rf"(^def {re.escape(func_name)}\(.*?)(?=^def |\Z)",
        source,
        re.DOTALL | re.MULTILINE,
    )
    assert m is not None, f"function {func_name} not found"
    return m.group(1)


def _item_handle(
    item_id: int,
    *,
    image_id: int = 1,
    session_epoch: int = 1,
    generation: int = 1,
) -> dict[str, Any]:
    return state.provisional_item_handle(
        item_id,
        image_id=image_id,
        session_epoch=session_epoch,
        generation=generation,
    )


def _image_handle(
    image_id: int,
    *,
    session_epoch: int = 1,
    generation: int = 1,
) -> dict[str, Any]:
    return state.provisional_image_handle(
        image_id,
        session_epoch=session_epoch,
        generation=generation,
    )


def _layer(
    item_id: int,
    name: str,
    *,
    kind: str = "raster",
    children: list[dict[str, Any]] | None = None,
    parent_handle: dict[str, Any] | None = None,
    image_id: int = 1,
    session_epoch: int = 1,
) -> dict[str, Any]:
    return {
        "handle": _item_handle(item_id, image_id=image_id, session_epoch=session_epoch),
        "name": name,
        "kind": kind,
        "parent_handle": parent_handle,
        "visible": True,
        "opacity": 100.0,
        "blend_mode": "NORMAL",
        "offset": {"x": 0, "y": 0},
        "size": {"width": 100, "height": 100},
        "has_alpha": True,
        "mask": {"present": False},
        "filters": [],
        "children": children if children is not None else [],
    }


def golden_empty() -> dict[str, Any]:
    """Minimal valid manifest with zero open images."""
    return {
        "schema_version": state.SCHEMA_VERSION,
        "captured_at": "2026-08-02T12:00:00Z",
        "session": {
            "session_id": "11111111-1111-1111-1111-111111111111",
            "epoch": 1,
            "transport": "stdio-proxy",
            "authenticated": True,
            "port": 9877,
        },
        "gimp": {
            "version": "3.2.4",
            "api_version": "3.0",
            "os": "Windows",
            "executable": "gimp-console-3.2.exe",
        },
        "images": [],
        "context": {
            "foreground_rgba": [0.0, 0.0, 0.0, 1.0],
            "background_rgba": [1.0, 1.0, 1.0, 1.0],
        },
        "capabilities": state.default_capabilities(),
    }


def golden_nested_group() -> dict[str, Any]:
    """3-level nested group tree for recursive validation."""
    epoch = 1
    img_id = 42
    leaf = _layer(30, "Leaf", kind="raster", image_id=img_id, session_epoch=epoch)
    mid_handle = _item_handle(20, image_id=img_id, session_epoch=epoch)
    leaf["parent_handle"] = mid_handle
    mid = _layer(
        20,
        "MidGroup",
        kind="group",
        children=[leaf],
        image_id=img_id,
        session_epoch=epoch,
    )
    root_handle = _item_handle(10, image_id=img_id, session_epoch=epoch)
    mid["parent_handle"] = root_handle
    root = _layer(
        10,
        "RootGroup",
        kind="group",
        children=[mid],
        image_id=img_id,
        session_epoch=epoch,
    )
    text = _layer(40, "Title", kind="text", image_id=img_id, session_epoch=epoch)

    doc = golden_empty()
    doc["images"] = [
        {
            "handle": _image_handle(img_id, session_epoch=epoch),
            "name": "nested.xcf",
            "source_path": None,
            "width": 800,
            "height": 600,
            "base_type": "RGB",
            "precision": "u8-gamma",
            "dirty": False,
            "selected": True,
            "alpha_present": True,
            "color_profile": {"name": "sRGB", "embedded": True},
            "metadata": {
                "exif_orientation_original": None,
                "pixel_orientation_normalized": False,
            },
            "selection": {"empty": True},
            "active_layer_handles": [_item_handle(30, image_id=img_id, session_epoch=epoch)],
            "layers": [root, text],
            "channels": [],
            "paths": [],
        }
    ]
    return doc


def golden_multi_image() -> dict[str, Any]:
    doc = golden_empty()
    doc["images"] = [
        {
            "handle": _image_handle(1),
            "name": "a.png",
            "source_path": "C:/tmp/a.png",
            "width": 10,
            "height": 10,
            "base_type": "RGB",
            "precision": "u8",
            "dirty": False,
            "selected": False,
            "alpha_present": False,
            "color_profile": None,
            "metadata": {
                "exif_orientation_original": 1,
                "pixel_orientation_normalized": False,
            },
            "selection": {"empty": True},
            "active_layer_handles": [],
            "layers": [_layer(100, "Background", image_id=1)],
            "channels": [],
            "paths": [],
        },
        {
            "handle": _image_handle(2),
            "name": "b.png",
            "source_path": None,
            "width": 20,
            "height": 30,
            "base_type": "GRAY",
            "precision": "u8-gamma",
            "dirty": True,
            "selected": True,
            "alpha_present": True,
            "color_profile": None,
            "metadata": {
                "exif_orientation_original": None,
                "pixel_orientation_normalized": False,
            },
            "selection": {
                "empty": False,
                "bounds": {"x": 1, "y": 2, "width": 3, "height": 4},
            },
            "active_layer_handles": [_item_handle(200, image_id=2)],
            "layers": [_layer(200, "Layer 1", image_id=2)],
            "channels": [
                {
                    "handle": _item_handle(300, image_id=2),
                    "name": "Alpha",
                    "visible": True,
                }
            ],
            "paths": [],
        },
    ]
    return doc


# ---------------------------------------------------------------------------
# Golden / structural validation
# ---------------------------------------------------------------------------


def test_golden_empty_valid() -> None:
    assert state.validate_manifest(golden_empty()) == []


def test_golden_multi_image_valid() -> None:
    assert state.validate_manifest(golden_multi_image()) == []


def test_golden_nested_group_valid() -> None:
    errors = state.validate_manifest(golden_nested_group())
    assert errors == [], errors


def test_deep_malformed_child_missing_handle() -> None:
    doc = golden_nested_group()
    # Remove handle from deepest child: images[0].layers[0].children[0].children[0]
    deep = doc["images"][0]["layers"][0]["children"][0]["children"][0]
    del deep["handle"]
    errors = state.validate_manifest(doc)
    assert errors, "expected validation errors for missing deep handle"
    joined = "\n".join(errors)
    assert "images[0].layers[0].children[0].children[0]" in joined
    assert "handle" in joined


def test_missing_top_level_required() -> None:
    doc = golden_empty()
    del doc["capabilities"]
    errors = state.validate_manifest(doc)
    assert any("capabilities" in e for e in errors)


# ---------------------------------------------------------------------------
# Handles / normalize / classify
# ---------------------------------------------------------------------------


def test_handle_shape_defaults() -> None:
    ih = state.provisional_image_handle(7, session_epoch=3)
    assert ih == {"image_id": 7, "generation": 1, "session_epoch": 3}
    th = state.provisional_item_handle(9, image_id=7, session_epoch=3)
    assert th == {
        "item_id": 9,
        "generation": 1,
        "image_id": 7,
        "session_epoch": 3,
    }
    assert state.SCHEMA_VERSION == "1.0.0"
    assert state.MAX_LAYER_DEPTH == 32


def test_normalize_base_type() -> None:
    assert state.normalize_base_type("Grayscale") == "GRAY"
    assert state.normalize_base_type("GRAY") == "GRAY"
    assert state.normalize_base_type("RGB") == "RGB"
    assert state.normalize_base_type("RGBA") == "RGB"
    assert state.normalize_base_type("Indexed") == "INDEXED"
    assert state.normalize_base_type("INDEXED") == "INDEXED"


def test_normalize_opacity_clamp() -> None:
    assert state.normalize_opacity(-5) == 0.0
    assert state.normalize_opacity(150) == 100.0
    assert state.normalize_opacity("50") == 50.0
    assert state.normalize_opacity(None) == 100.0


def test_classify_layer_kind() -> None:
    assert state.classify_layer_kind("GimpGroupLayer") == "group"
    assert state.classify_layer_kind("GimpTextLayer") == "text"
    assert state.classify_layer_kind("GimpLinkLayer") == "link"
    assert state.classify_layer_kind("GimpVectorLayer") == "vector"
    assert state.classify_layer_kind("GimpLayer") == "raster"
    # Pixel-type strings from _get_layer_type_string must NOT become special kinds
    assert state.classify_layer_kind("RGB") == "raster"
    assert state.classify_layer_kind("RGBA") == "raster"
    assert state.classify_layer_kind("unknown") == "raster"


def test_parse_layer_offsets() -> None:
    """parse_layer_offsets handles GIMP offset objects, tuples, and failures."""

    class _Off:
        def __init__(self, x: int, y: int) -> None:
            self.offset_x = x
            self.offset_y = y

    assert state.parse_layer_offsets(_Off(12, -4)) == (12, -4)
    assert state.parse_layer_offsets(_Off(0, 0)) == (0, 0)
    assert state.parse_layer_offsets((3, 7)) == (3, 7)
    assert state.parse_layer_offsets([10, 20]) == (10, 20)
    assert state.parse_layer_offsets([_Off(5, 6)]) == (5, 6)
    assert state.parse_layer_offsets(None) == (0, 0)
    assert state.parse_layer_offsets("bad") == (0, 0)
    assert state.parse_layer_offsets([]) == (0, 0)

    class _Partial:
        offset_x = 9  # no offset_y

    assert state.parse_layer_offsets(_Partial()) == (9, 0)


def test_image_requires_orientation_fields() -> None:
    """Promoted imageManifest fields are required by validate_manifest."""
    doc = golden_multi_image()
    img = doc["images"][0]
    for key in (
        "selection",
        "alpha_present",
        "color_profile",
        "metadata",
        "active_layer_handles",
        "channels",
        "paths",
        "source_path",
    ):
        missing = dict(img)
        del missing[key]
        bad = golden_empty()
        bad["images"] = [missing]
        errors = state.validate_manifest(bad)
        assert any(key in e for e in errors), f"expected missing {key}: {errors}"

    # metadata sub-keys
    meta_bad = dict(img)
    meta_bad["metadata"] = {"exif_orientation_original": 1}
    bad2 = golden_empty()
    bad2["images"] = [meta_bad]
    errors2 = state.validate_manifest(bad2)
    assert any("pixel_orientation_normalized" in e for e in errors2)

    # parent_handle required on layer nodes
    layer_bad = golden_nested_group()
    del layer_bad["images"][0]["layers"][0]["parent_handle"]
    errors3 = state.validate_manifest(layer_bad)
    assert any("parent_handle" in e for e in errors3)


def test_source_path_type_and_required() -> None:
    """source_path must be present and string|null."""
    doc = golden_multi_image()
    img = dict(doc["images"][0])
    img["source_path"] = 123
    bad = golden_empty()
    bad["images"] = [img]
    errors = state.validate_manifest(bad)
    assert any("source_path" in e for e in errors)

    img2 = dict(doc["images"][0])
    img2["source_path"] = None
    ok = golden_empty()
    ok["images"] = [img2]
    assert state.validate_manifest(ok) == []


def test_selection_bounds_require_integers() -> None:
    """Non-null selection.bounds must carry integer x/y/width/height."""
    doc = golden_multi_image()
    img = dict(doc["images"][1])  # has non-empty selection with bounds
    img["selection"] = {"empty": False, "bounds": {"x": 1, "y": 2}}  # missing w/h
    bad = golden_empty()
    bad["images"] = [img]
    errors = state.validate_manifest(bad)
    joined = "\n".join(errors)
    assert "selection.bounds" in joined
    assert "width" in joined or "height" in joined

    img2 = dict(doc["images"][1])
    img2["selection"] = {
        "empty": False,
        "bounds": {"x": 1, "y": 2, "width": "3", "height": 4},
    }
    bad2 = golden_empty()
    bad2["images"] = [img2]
    errors2 = state.validate_manifest(bad2)
    assert any("width" in e for e in errors2)

    img3 = dict(doc["images"][0])
    img3["selection"] = {"empty": True, "bounds": None}
    ok = golden_empty()
    ok["images"] = [img3]
    assert state.validate_manifest(ok) == []


def test_captured_at_iso_ish() -> None:
    """captured_at must be non-empty and lightly ISO-8601 shaped."""
    doc = golden_empty()
    doc["captured_at"] = ""
    errors = state.validate_manifest(doc)
    assert any("captured_at" in e for e in errors)

    doc2 = golden_empty()
    doc2["captured_at"] = "not-a-timestamp"
    errors2 = state.validate_manifest(doc2)
    assert any("captured_at" in e for e in errors2)

    doc3 = golden_empty()
    doc3["captured_at"] = "2026-08-02T12:00:00Z"
    assert state.validate_manifest(doc3) == []


# ---------------------------------------------------------------------------
# Capabilities honesty
# ---------------------------------------------------------------------------


def test_capabilities_honesty() -> None:
    caps = state.default_capabilities()
    assert caps["visible_composite_snapshot"] is True
    assert caps["atomic_xcf_save"] is True
    assert caps["atomic_export"] is True
    assert caps["alpha_preserving_export"] is True
    assert caps["state_manifest_orientation"] is True
    assert caps["stable_handle_registry"] is True
    assert caps["coordinate_exif_normalized"] is True
    assert caps["source_immutable_policy"] is True
    assert caps["checkpoints"] is True
    assert caps["isolated_layer_snapshot"] is False
    assert caps["alpha_snapshot"] is False  # live renderer unfinished (0014 keeps false)
    assert caps["pixel_verification"] is True  # 0014 host PNG compare/verify
    assert caps["batch_interpreter"] is False
    assert caps["mcp_image_visible_to_model"] is True
    assert caps["filesystem_image_attachment"] is True
    for key in (
        "visible_composite_snapshot",
        "isolated_layer_snapshot",
        "alpha_snapshot",
        "atomic_xcf_save",
        "atomic_export",
    ):
        assert key in caps


# ---------------------------------------------------------------------------
# finalize_manifest
# ---------------------------------------------------------------------------


def test_finalize_manifest_injects_and_validates() -> None:
    raw = {
        "session": {
            "session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "epoch": 1,
            "started_at": "2026-08-02T11:00:00Z",
        },
        "gimp": {
            "version": "3.2.4",
            "api_version": "3.0",
            "os": "Windows",
            "executable": "gimp.exe",
        },
        "images": [],
        "context": {},
    }
    doc = state.finalize_manifest(
        raw,
        authenticated=True,
        host="127.0.0.1",
        port=9877,
        transport="stdio-proxy",
        captured_at="2026-08-02T12:00:00Z",
    )
    assert doc["schema_version"] == "1.0.0"
    assert doc["session"]["transport"] == "stdio-proxy"
    assert doc["session"]["authenticated"] is True
    assert doc["session"]["port"] == 9877
    assert doc["session"]["host"] == "127.0.0.1"
    assert doc["capabilities"]["state_manifest_orientation"] is True
    assert state.validate_manifest(doc) == []
    # raw must not be mutated
    assert "capabilities" not in raw
    assert "schema_version" not in raw


def test_finalize_normalizes_grayscale() -> None:
    raw = {
        "session": {"session_id": "s", "epoch": 1},
        "gimp": {
            "version": "3",
            "api_version": "3.0",
            "os": "Linux",
            "executable": "gimp",
        },
        "images": [
            {
                "handle": _image_handle(1),
                "name": "g",
                "source_path": None,
                "width": 1,
                "height": 1,
                "base_type": "Grayscale",
                "precision": "u8",
                "dirty": False,
                "selected": False,
                "alpha_present": False,
                "color_profile": None,
                "metadata": {
                    "exif_orientation_original": None,
                    "pixel_orientation_normalized": False,
                },
                "selection": {"empty": True},
                "active_layer_handles": [],
                "layers": [],
                "channels": [],
                "paths": [],
            }
        ],
        "context": {},
    }
    doc = state.finalize_manifest(raw, authenticated=False, port=1)
    assert doc["images"][0]["base_type"] == "GRAY"


def test_finalize_preserves_warnings() -> None:
    raw = {
        "session": {"session_id": "s", "epoch": 1},
        "gimp": {
            "version": "3",
            "api_version": "3.0",
            "os": "Linux",
            "executable": "gimp",
        },
        "images": [],
        "context": {},
        "warnings": [{"image_index": 1, "error": "boom"}],
    }
    doc = state.finalize_manifest(raw, authenticated=True, port=9)
    assert doc["warnings"] == [{"image_index": 1, "error": "boom"}]


# ---------------------------------------------------------------------------
# Optional jsonschema (transitive via mcp)
# ---------------------------------------------------------------------------


def test_jsonschema_golden_when_available() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    for name, doc in (
        ("empty", golden_empty()),
        ("nested", golden_nested_group()),
        ("multi", golden_multi_image()),
    ):
        errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.path))
        assert not errors, f"{name}: " + "; ".join(e.message for e in errors)


def test_schema_file_exists_and_id() -> None:
    assert SCHEMA_PATH.is_file()
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$id"] == "urn:gimp-agent:state-manifest:1"
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    # capabilities must NOT lock additionalProperties:false
    caps = schema["properties"]["capabilities"]
    assert caps.get("additionalProperties") is not False


# ---------------------------------------------------------------------------
# Source greps (wiring) — after Phase 2/3 these should pass fully
# ---------------------------------------------------------------------------


def test_state_module_exports() -> None:
    text = STATE_MOD.read_text(encoding="utf-8")
    assert 'SCHEMA_VERSION = "1.0.0"' in text
    assert "MAX_LAYER_DEPTH = 32" in text
    assert "def validate_manifest" in text
    assert "def finalize_manifest" in text
    assert "def default_capabilities" in text
    assert "def classify_layer_kind" in text
    assert "def parse_layer_offsets" in text


def test_orient_workspace_wiring_when_present() -> None:
    """orient_workspace registered on plugin + server; read-only greps hold."""
    plugin = PLUGIN.read_text(encoding="utf-8")
    server = SERVER.read_text(encoding="utf-8")
    assert "orient_workspace" in plugin and "orient_workspace" in server
    assert 'j["type"] == "orient_workspace"' in plugin or '"orient_workspace"' in plugin
    assert "def orient_workspace" in server
    assert "finalize_manifest" in server
    assert 'transport="stdio-proxy"' in server or "transport='stdio-proxy'" in server

    body = _method_body(plugin, "_orient_workspace")
    # Ban mutation call sites (docstrings may mention the names)
    assert "displays_flush(" not in body
    assert "Gimp.displays_flush" not in body
    assert "undo_group_start" not in body
    assert "undo_group_end" not in body
    assert "self.session_id" in body
    # Partial dump failures surface as warnings (not silent drop)
    assert "warnings" in body

    # Tree walk uses _layer_children (not flat _iter_layers_recursive) in orient helpers
    layer_node = _method_body(plugin, "_orient_layer_node")
    assert "self._layer_children" in layer_node
    assert "_iter_layers_recursive" not in layer_node
    assert "self._get_layer_type_string" not in layer_node
    # Offset parsing handles GIMP 3 object attrs (not list-only)
    assert "offset_x" in layer_node
    assert "offset_y" in layer_node
    classify = _method_body(plugin, "_orient_classify_kind")
    assert "self._get_layer_type_string" not in classify
    assert "isinstance" in classify
    # No substring kind heuristics
    assert '"group" in lower' not in classify
    assert "'group' in lower" not in classify

    init_body = _method_body(plugin, "__init__")
    assert "self.session_id" in init_body
    assert "self.session_epoch" in init_body
    assert "self.session_started_at" in init_body

    # get_image_metadata three-edit hygiene (server params + dispatcher + plugin)
    server_meta = _function_body(server, "get_image_metadata")
    assert "image_index" in server_meta
    assert 'send_command("get_image_metadata"' in server_meta
    assert (
        '"image_index": image_index' in server_meta or "'image_index': image_index" in server_meta
    )
    assert '_get_current_image_metadata(j.get("params"' in plugin
    meta_body = _method_body(plugin, "_get_current_image_metadata")
    assert "_get_image" in meta_body
    assert "image_index" in meta_body
    assert "images[0]" not in meta_body


def test_recursive_walk_guards_in_source() -> None:
    """summary_only count and _iter_layers_recursive must guard depth + visited ids."""
    plugin = PLUGIN.read_text(encoding="utf-8")
    orient_entry = _method_body(plugin, "_orient_image_entry")
    # summary_only branch uses visited set + max depth (not bare unbounded stack)
    assert "summary_only" in orient_entry
    assert "visited_ids" in orient_entry or "visited" in orient_entry
    assert "_ORIENT_MAX_LAYER_DEPTH" in orient_entry
    # Ensure count path is not the old unguarded while-stack.extend pattern alone
    summary_idx = orient_entry.find("if summary_only:")
    assert summary_idx >= 0
    summary_block = orient_entry[summary_idx : summary_idx + 1200]
    assert "visited" in summary_block
    assert "depth" in summary_block
    assert "_ORIENT_MAX_LAYER_DEPTH" in summary_block

    walker = _method_body(plugin, "_iter_layers_recursive")
    assert "visited" in walker
    assert "max_depth" in walker
    assert "depth" in walker
    # Default max_depth=32 in signature
    assert "max_depth=32" in walker or "max_depth = 32" in walker
