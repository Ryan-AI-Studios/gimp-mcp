"""Offline unit tests for layer policy + checkpoints (track 0009). No GIMP required."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import gimp_mcp_policy as policy
import gimp_mcp_security as sec
import gimp_mcp_state as state

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# CODE_* exports
# ---------------------------------------------------------------------------


def test_policy_error_codes_defined() -> None:
    assert sec.CODE_POLICY_DENIED == "POLICY_DENIED"
    assert sec.CODE_CONFIRM_REQUIRED == "CONFIRM_REQUIRED"
    assert sec.CODE_CHECKPOINT_EXISTS == "CHECKPOINT_EXISTS"
    assert sec.CODE_CHECKPOINT_NOT_FOUND == "CHECKPOINT_NOT_FOUND"
    assert sec.CODE_CHECKPOINT_CORRUPTED == "CHECKPOINT_CORRUPTED"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_source_immutable_constants() -> None:
    assert policy.SOURCE_IMMUTABLE_GROUP_NAME == "Source_Immutable"
    assert policy.PARASITE_SOURCE_IMMUTABLE == "gimp-mcp:source-immutable"
    assert policy.COORDINATE_SPACE == "image-pixels"
    assert policy.VIEW_ROTATION_IGNORED is True
    assert policy.SIDECAR_SCHEMA_VERSION == "1.0.0"


def test_windows_reserved_names_cover_devices() -> None:
    for name in ("CON", "PRN", "AUX", "NUL", "COM1", "COM9", "LPT1", "LPT9"):
        assert name in policy.WINDOWS_RESERVED_NAMES
    assert "COM0" not in policy.WINDOWS_RESERVED_NAMES
    assert "LPT0" not in policy.WINDOWS_RESERVED_NAMES


def test_is_working_layer_name() -> None:
    assert policy.is_working_layer_name("Background (working)") is True
    assert policy.is_working_layer_name("Background (working) 2") is True
    assert policy.is_working_layer_name("foo (working) 99") is True
    assert policy.is_working_layer_name("Background") is False
    assert policy.is_working_layer_name("working") is False
    assert policy.is_working_layer_name("x (working) extra") is False
    assert policy.is_working_layer_name("") is False


# ---------------------------------------------------------------------------
# sanitize_checkpoint_label
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label",
    [
        "before-edit",
        "a",
        "A_B-c.1",
        "x" * 64,
        "pre_flatten_v2",
    ],
)
def test_sanitize_label_good(label: str) -> None:
    assert policy.sanitize_checkpoint_label(label) == label


@pytest.mark.parametrize(
    "label,match",
    [
        ("", "empty"),
        ("   ", "empty"),
        (".", r"'\.'"),
        ("..", r"\.\."),
        ("foo..bar", r"\.\."),
        ("has space", r"\[A-Za-z0-9"),
        ("slash/bad", r"\[A-Za-z0-9"),
        ("back\\slash", r"\[A-Za-z0-9"),
        ("trail.", r"end with"),
        ("trail ", r"end with"),
        ("x" * 65, "max length"),
        ("CON", "reserved"),
        ("con", "reserved"),
        ("Prn", "reserved"),
        ("AUX", "reserved"),
        ("nul", "reserved"),
        ("COM1", "reserved"),
        ("com9", "reserved"),
        ("LPT1", "reserved"),
        ("lpt9", "reserved"),
        ("CON.backup", "reserved"),  # basename CON
    ],
)
def test_sanitize_label_bad(label: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        policy.sanitize_checkpoint_label(label)


def test_sanitize_label_type_error() -> None:
    with pytest.raises(ValueError, match="string"):
        policy.sanitize_checkpoint_label(123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def test_checkpoint_paths(tmp_path: Path) -> None:
    d = policy.checkpoint_dir(tmp_path, "snap1")
    assert d == tmp_path / ".gimp-mcp-checkpoints" / "snap1"
    assert policy.checkpoint_xcf_path(tmp_path, "snap1") == d / "project.xcf"
    assert policy.checkpoint_json_path(tmp_path, "snap1") == d / "checkpoint.json"


def test_checkpoint_paths_reject_bad_label(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        policy.checkpoint_dir(tmp_path, "CON")


# ---------------------------------------------------------------------------
# sha256_file
# ---------------------------------------------------------------------------


def test_sha256_file_fixed_bytes(tmp_path: Path) -> None:
    p = tmp_path / "blob.bin"
    p.write_bytes(b"gimp-mcp-checkpoint-fixture\n")
    digest = policy.sha256_file(p)
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    # Known SHA-256 of the fixture bytes
    import hashlib

    expected = hashlib.sha256(b"gimp-mcp-checkpoint-fixture\n").hexdigest()
    assert digest == expected
    assert policy.sha256_file(p) == digest  # stable


# ---------------------------------------------------------------------------
# Sidecar build / validate
# ---------------------------------------------------------------------------


def _sample_image() -> dict:
    return {
        "image_id": 7,
        "generation": 2,
        "width": 800,
        "height": 600,
        "name": "demo.xcf",
    }


def _sample_layers() -> list[dict]:
    return [
        {
            "item_id": 11,
            "tattoo": 1001,
            "name": "Background",
            "kind": "raster",
            "parent_item_id": None,
            "protected": True,
        },
        {
            "item_id": 12,
            "name": "Background (working)",
            "kind": "raster",
            "protected": False,
        },
    ]


def test_build_and_validate_sidecar_ok(tmp_path: Path) -> None:
    xcf = tmp_path / "project.xcf"
    xcf.write_bytes(b"fake-xcf")
    digest = policy.sha256_file(xcf)
    doc = policy.build_sidecar(
        label="before-edit",
        session_epoch=42,
        image=_sample_image(),
        xcf_path=str(xcf),
        xcf_sha256=digest,
        layers=_sample_layers(),
        created_at="2026-08-02T12:00:00Z",
    )
    assert doc["schema_version"] == "1.0.0"
    assert doc["coordinate_space"] == "image-pixels"
    assert doc["view_rotation_ignored"] is True
    out = policy.validate_sidecar(doc)
    assert out["label"] == "before-edit"
    assert out["xcf_sha256"] == digest
    assert out["layers"][0]["tattoo"] == 1001


def test_validate_sidecar_rejects_bad() -> None:
    good = policy.build_sidecar(
        label="ok",
        session_epoch=1,
        image=_sample_image(),
        xcf_path="/ws/.gimp-mcp-checkpoints/ok/project.xcf",
        xcf_sha256="a" * 64,
        layers=[],
        created_at="2026-08-02T12:00:00Z",
    )
    with pytest.raises(ValueError, match="dict"):
        policy.validate_sidecar("nope")  # type: ignore[arg-type]
    bad_sv = dict(good)
    bad_sv["schema_version"] = "0.9.0"
    with pytest.raises(ValueError, match="schema_version"):
        policy.validate_sidecar(bad_sv)
    bad_at = dict(good)
    bad_at["created_at"] = "not-a-date"
    with pytest.raises(ValueError, match="created_at"):
        policy.validate_sidecar(bad_at)
    bad_hash = dict(good)
    bad_hash["xcf_sha256"] = "zz"
    with pytest.raises(ValueError, match="xcf_sha256"):
        policy.validate_sidecar(bad_hash)
    bad_cs = dict(good)
    bad_cs["coordinate_space"] = "layer-local"
    with pytest.raises(ValueError, match="coordinate_space"):
        policy.validate_sidecar(bad_cs)
    bad_vri = dict(good)
    bad_vri["view_rotation_ignored"] = False
    with pytest.raises(ValueError, match="view_rotation_ignored"):
        policy.validate_sidecar(bad_vri)
    bad_label = dict(good)
    bad_label["label"] = "CON"
    with pytest.raises(ValueError, match="reserved"):
        policy.validate_sidecar(bad_label)


def test_validate_sidecar_roundtrip_json(tmp_path: Path) -> None:
    doc = policy.build_sidecar(
        label="rt",
        session_epoch=3,
        image=_sample_image(),
        xcf_path="rt/project.xcf",
        xcf_sha256="b" * 64,
        layers=_sample_layers(),
        created_at="2026-08-02T15:30:00Z",
    )
    p = tmp_path / "checkpoint.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    loaded = json.loads(p.read_text(encoding="utf-8"))
    policy.validate_sidecar(loaded)


# ---------------------------------------------------------------------------
# Capabilities (Phase 5 flags)
# ---------------------------------------------------------------------------


def test_capabilities_policy_flags() -> None:
    caps = state.default_capabilities()
    assert caps.get("source_immutable_policy") is True
    assert caps.get("checkpoints") is True
    assert caps["atomic_xcf_save"] is True
    assert caps["atomic_export"] is True


# ---------------------------------------------------------------------------
# Wiring greps (plugin / server / pyproject / README)
# ---------------------------------------------------------------------------


def test_wiring_pyproject_lists_policy() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "gimp_mcp_policy" in text


def test_wiring_readme_install_files() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "gimp_mcp_policy.py" in text
    assert "gimp_mcp_atomic.py" in text
    assert "gimp_mcp_filters.py" in text
    assert "gimp_mcp_tx.py" in text
    # EXPECTED_PLUGIN_FILES = plugin + 9 shared modules (10 total after 0017)
    assert "10 files" in text or "**10** files" in text


def test_wiring_plugin_imports_policy() -> None:
    text = (ROOT / "gimp-mcp-plugin.py").read_text(encoding="utf-8")
    assert "gimp_mcp_policy" in text
    assert "ensure_source_immutable" in text
    assert "checkpoint_create" in text
    assert "checkpoint_restore" in text
    assert "_resolve_mutable_layer" in text
    assert "_protected_item_ids" in text
    assert "_working_item_ids" in text
    assert "is_working_layer_name" in text
    assert "confirm_destructive" in text
    assert "CODE_CONFIRM_REQUIRED" in text or "CONFIRM_REQUIRED" in text
    # Codex P1: policy gates use coerce_bool (not bare bool on those params)
    assert "_exp.coerce_bool" in text
    assert "def _require_confirm_destructive" in text
    assert "def _allow_source_mutation_from_params" in text
    # Checkpoint XCF: atomic temp (suffix-preserving) + os.replace (0013)
    assert "make_temp_path" in text
    assert "os.replace" in text
    # Durable protection across restore/restart (Codex final P1)
    assert "_hydrate_protected_from_group" in text
    assert "_item_under_source_immutable_policy" in text
    assert "_find_source_immutable_group" in text


def test_wiring_server_tools() -> None:
    text = (ROOT / "gimp_mcp_server.py").read_text(encoding="utf-8")
    assert "def ensure_source_immutable(" in text
    assert "def checkpoint_create(" in text
    assert "def checkpoint_restore(" in text
    assert "confirm_destructive" in text
    # Live flatten sites forward the flag
    for name in ("flatten_image", "merge_visible_layers", "rotate_image", "resize_canvas"):
        assert f"def {name}(" in text
    assert "confirm_destructive: bool" in text or "confirm_destructive:bool" in text


def test_wiring_plugin_guarded_mutators_use_mutable_resolve() -> None:
    text = (ROOT / "gimp-mcp-plugin.py").read_text(encoding="utf-8")

    def method_body(method_def: str) -> str:
        start = text.find(method_def)
        assert start != -1, method_def
        rest = text[start + len(method_def) :]
        end = rest.find("\n    def ")
        return rest[:end] if end != -1 else rest[:4000]

    mutators = [
        "def _duplicate_layer",
        "def _delete_layer",
        "def _rename_layer",
        "def _set_layer_properties",
        "def _reorder_layer",
        "def _fill_layer",
        "def _fill_selection",
        "def _draw_line",
        "def _draw_rectangle",
        "def _draw_ellipse",
        "def _fill_rectangle",
        "def _fill_ellipse",
        "def _gradient_fill",
        "def _auto_levels",
        "def _adjust_curves",
        "def _adjust_brightness_contrast",
        "def _adjust_hue_saturation",
        "def _adjust_color_balance",
        "def _sharpen",
        "def _blur",
        "def _denoise",
        "def _desaturate",
        "def _invert_colors",
        "def _apply_drop_shadow",
        "def _apply_gaussian_blur",
        "def _apply_pixelate",
        "def _apply_emboss",
        "def _apply_vignette",
        "def _apply_noise",
        "def _edit_text",
        "def _warp_region",
    ]
    for m in mutators:
        body = method_body(m)
        assert "_resolve_mutable_layer" in body, f"{m} must use _resolve_mutable_layer"

    # Read-only paths keep plain resolve
    for m in ("def _get_pixel_color", "def _select_by_color", "def _list_layers"):
        body = method_body(m)
        assert "_resolve_mutable_layer" not in body, f"{m} must not use mutable resolve"


def test_wiring_confirm_destructive_sites() -> None:
    text = (ROOT / "gimp-mcp-plugin.py").read_text(encoding="utf-8")

    def method_body(method_def: str) -> str:
        start = text.find(method_def)
        assert start != -1, method_def
        rest = text[start + len(method_def) :]
        end = rest.find("\n    def ")
        return rest[:end] if end != -1 else rest[:4000]

    # Always-gated live stack destroyers
    for m in ("def _flatten_image", "def _merge_visible_layers"):
        body = method_body(m)
        assert "_require_confirm_destructive" in body, f"{m} must require confirm_destructive"

    # Free-angle only: confirm must be under needs_free_angle_flatten branch
    rotate = method_body("def _rotate_image")
    assert "needs_free_angle_flatten" in rotate
    assert "_require_confirm_destructive" in rotate
    # Confirm call appears after the free-angle flag, not as unconditional first action
    flag_pos = rotate.find("needs_free_angle_flatten")
    conf_pos = rotate.find("_require_confirm_destructive")
    assert flag_pos != -1 and conf_pos != -1 and flag_pos < conf_pos
    assert "if needs_free_angle_flatten:" in rotate

    # Non-transparent fill only: confirm under will_flatten
    resize = method_body("def _resize_canvas")
    assert "will_flatten" in resize
    assert "_require_confirm_destructive" in resize
    assert 'str(fill).lower() != "transparent"' in resize or '!= "transparent"' in resize
    flag_pos = resize.find("will_flatten")
    conf_pos = resize.find("_require_confirm_destructive")
    assert flag_pos != -1 and conf_pos != -1 and flag_pos < conf_pos
    assert "if will_flatten:" in resize


def test_coerce_bool_rejects_stringly_false() -> None:
    """Policy gates must use export.coerce_bool semantics (Codex P1)."""
    import gimp_mcp_export as exp

    assert exp.coerce_bool("false", default=False) is False
    assert exp.coerce_bool("FALSE", default=False) is False
    assert exp.coerce_bool("0", default=False) is False
    assert exp.coerce_bool("true", default=False) is True
    assert exp.coerce_bool(True, default=False) is True
    assert exp.coerce_bool(False, default=False) is False
    # bare bool() would wrongly treat these as True:
    assert bool("false") is True
    assert exp.coerce_bool("false", default=False) is not bool("false")
    # Fail-closed on non-scalar JSON (lists/dicts must not satisfy safety gates)
    assert exp.coerce_bool([0], default=False) is False
    assert exp.coerce_bool({}, default=False) is False
    assert exp.coerce_bool([1], default=False) is False
    assert exp.coerce_bool({"ok": True}, default=True) is True  # default when unknown type
