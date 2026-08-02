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


# ---------------------------------------------------------------------------
# Capabilities honesty
# ---------------------------------------------------------------------------


def test_capabilities_honesty() -> None:
    caps = state.default_capabilities()
    assert caps["visible_composite_snapshot"] is True
    assert caps["atomic_xcf_save"] is False
    assert caps["atomic_export"] is False
    assert caps["alpha_preserving_export"] is True
    assert caps["state_manifest_orientation"] is True
    assert caps["stable_handle_registry"] is False
    assert caps["coordinate_exif_normalized"] is False
    assert caps["isolated_layer_snapshot"] is False
    assert caps["alpha_snapshot"] is False
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
                "width": 1,
                "height": 1,
                "base_type": "Grayscale",
                "precision": "u8",
                "dirty": False,
                "selected": False,
                "layers": [],
            }
        ],
        "context": {},
    }
    doc = state.finalize_manifest(raw, authenticated=False, port=1)
    assert doc["images"][0]["base_type"] == "GRAY"


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

    # Tree walk uses _layer_children (not flat _iter_layers_recursive) in orient helpers
    layer_node = _method_body(plugin, "_orient_layer_node")
    assert "self._layer_children" in layer_node
    assert "_iter_layers_recursive" not in layer_node
    assert "self._get_layer_type_string" not in layer_node
    classify = _method_body(plugin, "_orient_classify_kind")
    assert "self._get_layer_type_string" not in classify
    assert "isinstance" in classify

    init_body = _method_body(plugin, "__init__")
    assert "self.session_id" in init_body
    assert "self.session_epoch" in init_body
    assert "self.session_started_at" in init_body

    # get_image_metadata three-edit hygiene
    assert "image_index" in _function_body(server, "get_image_metadata")
    meta_body = _method_body(plugin, "_get_current_image_metadata")
    assert "_get_image" in meta_body
    assert "images[0]" not in meta_body
