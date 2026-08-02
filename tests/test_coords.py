"""Offline unit tests for gimp_mcp_coords (track 0008). No GIMP required."""

from __future__ import annotations

from pathlib import Path

import pytest

import gimp_mcp_coords as coords
import gimp_mcp_security as sec

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Declaration constants + validate
# ---------------------------------------------------------------------------


def test_declaration_constants() -> None:
    assert coords.COORDINATE_SPACE == "image-pixels"
    assert coords.ORIGIN == "top-left"
    assert coords.X_AXIS == "right"
    assert coords.Y_AXIS == "down"
    assert coords.VIEW_ROTATION_IGNORED is True
    assert coords.view_rotation_ignored is True


def test_validate_declaration_ok() -> None:
    decl = {
        "coordinate_space": "image-pixels",
        "origin": "top-left",
        "x_axis": "right",
        "y_axis": "down",
    }
    out = coords.validate_declaration(decl)
    assert out["view_rotation_ignored"] is True
    assert out["coordinate_space"] == "image-pixels"


def test_validate_declaration_preserves_extra_keys() -> None:
    decl = {
        "coordinate_space": "image-pixels",
        "origin": "top-left",
        "x_axis": "right",
        "y_axis": "down",
        "preview_padding_x": 0,
        "agent_note": "ok",
    }
    out = coords.validate_declaration(decl)
    assert out["agent_note"] == "ok"
    assert out["preview_padding_x"] == 0


def test_validate_declaration_rejects_bad() -> None:
    with pytest.raises(ValueError, match="must be a dict"):
        coords.validate_declaration("nope")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="missing required"):
        coords.validate_declaration({"coordinate_space": "image-pixels"})
    with pytest.raises(ValueError, match="must be 'image-pixels'"):
        coords.validate_declaration(
            {
                "coordinate_space": "layer-local",
                "origin": "top-left",
                "x_axis": "right",
                "y_axis": "down",
            }
        )
    with pytest.raises(ValueError, match="view_rotation_ignored"):
        coords.validate_declaration(
            {
                "coordinate_space": "image-pixels",
                "origin": "top-left",
                "x_axis": "right",
                "y_axis": "down",
                "view_rotation_ignored": False,
            }
        )
    with pytest.raises(ValueError, match="preview_padding_x"):
        coords.validate_declaration(
            {
                "coordinate_space": "image-pixels",
                "origin": "top-left",
                "x_axis": "right",
                "y_axis": "down",
                "preview_padding_x": -1,
            }
        )


# ---------------------------------------------------------------------------
# Preview ↔ image
# ---------------------------------------------------------------------------


def test_preview_to_image_full_canvas() -> None:
    # scale 0.5: preview 10,20 → image 20,40
    ix, iy = coords.preview_to_image_xy(10, 20, scale_x=0.5, scale_y=0.5)
    assert (ix, iy) == (20, 40)


def test_preview_to_image_region_and_padding() -> None:
    # region origin 100,200; scale 0.5; padding 2,4
    # image = 100 + (10-2)/0.5 = 100 + 16 = 116
    # image_y = 200 + (20-4)/0.5 = 200 + 32 = 232
    ix, iy = coords.preview_to_image_xy(
        10,
        20,
        scale_x=0.5,
        scale_y=0.5,
        region_origin_x=100,
        region_origin_y=200,
        preview_padding_x=2,
        preview_padding_y=4,
    )
    assert (ix, iy) == (116, 232)


def test_image_to_preview_inverse() -> None:
    px, py = coords.image_to_preview_xy(
        116,
        232,
        scale_x=0.5,
        scale_y=0.5,
        region_origin_x=100,
        region_origin_y=200,
        preview_padding_x=2,
        preview_padding_y=4,
    )
    assert (px, py) == (10, 20)


def test_preview_scale_zero_raises() -> None:
    with pytest.raises(ValueError, match="scale_x"):
        coords.preview_to_image_xy(0, 0, scale_x=0, scale_y=1)
    with pytest.raises(ValueError, match="scale_y"):
        coords.preview_to_image_xy(0, 0, scale_x=1, scale_y=0)


def test_half_even_rounding() -> None:
    """Python banker's rounding: 0.5 → 0, 1.5 → 2, 2.5 → 2."""
    # scale_x=1 so image_x = preview_x (no padding)
    assert coords.preview_to_image_xy(0.5, 0, scale_x=1, scale_y=1)[0] == 0
    assert coords.preview_to_image_xy(1.5, 0, scale_x=1, scale_y=1)[0] == 2
    assert coords.preview_to_image_xy(2.5, 0, scale_x=1, scale_y=1)[0] == 2


@pytest.mark.parametrize("scale", [1 / 3, 0.5, 0.75])
def test_preview_image_round_trip_within_one_px(scale: float) -> None:
    """Inverse round-trip error ≤ 1 px for common downscales (O2)."""
    origins = [(0, 0), (100, 50), (7, 13)]
    pads = [(0, 0), (1, 2)]
    points = [(0, 0), (10, 20), (100, 75), (33, 66), (1, 1)]
    for ox, oy in origins:
        for padx, pady in pads:
            for px, py in points:
                ix, iy = coords.preview_to_image_xy(
                    px,
                    py,
                    scale_x=scale,
                    scale_y=scale,
                    region_origin_x=ox,
                    region_origin_y=oy,
                    preview_padding_x=padx,
                    preview_padding_y=pady,
                )
                rx, ry = coords.image_to_preview_xy(
                    ix,
                    iy,
                    scale_x=scale,
                    scale_y=scale,
                    region_origin_x=ox,
                    region_origin_y=oy,
                    preview_padding_x=padx,
                    preview_padding_y=pady,
                )
                assert abs(rx - px) <= 1, (scale, px, py, rx, ry)
                assert abs(ry - py) <= 1, (scale, px, py, rx, ry)


# ---------------------------------------------------------------------------
# Layer local ↔ image
# ---------------------------------------------------------------------------


def test_layer_offset_round_trip() -> None:
    ix, iy = coords.layer_local_to_image_xy(10, 20, offset_x=50, offset_y=-5)
    assert (ix, iy) == (60, 15)
    lx, ly = coords.image_to_layer_local_xy(60, 15, offset_x=50, offset_y=-5)
    assert (lx, ly) == (10, 20)


def test_layer_offset_zero() -> None:
    assert coords.layer_local_to_image_xy(3, 4, 0, 0) == (3, 4)
    assert coords.image_to_layer_local_xy(3, 4, 0, 0) == (3, 4)


# ---------------------------------------------------------------------------
# Clamp
# ---------------------------------------------------------------------------


def test_clamp_to_image() -> None:
    assert coords.clamp_to_image(-1, -1, 100, 50) == (0, 0)
    assert coords.clamp_to_image(50, 25, 100, 50) == (50, 25)
    assert coords.clamp_to_image(100, 50, 100, 50) == (99, 49)
    assert coords.clamp_to_image(999, 999, 100, 50) == (99, 49)
    assert coords.clamp_to_image(5, 5, 0, 10) == (0, 0)


# ---------------------------------------------------------------------------
# ORIENTATION_OPS ordered lists
# ---------------------------------------------------------------------------


def test_orientation_ops_table_complete() -> None:
    assert set(coords.ORIENTATION_OPS.keys()) == set(range(1, 9))
    assert coords.ORIENTATION_OPS[1] == []
    assert coords.ORIENTATION_OPS[2] == ["flip_h"]
    assert coords.ORIENTATION_OPS[3] == ["rot180"]
    assert coords.ORIENTATION_OPS[4] == ["flip_v"]
    assert coords.ORIENTATION_OPS[6] == ["rot90"]
    assert coords.ORIENTATION_OPS[8] == ["rot270"]


def test_orientation_ops_tag5_order() -> None:
    """Tag 5 (transpose-equivalent): flip_h first, then rot90."""
    ops = coords.ORIENTATION_OPS[5]
    assert ops == ["flip_h", "rot90"]
    assert ops[0] == "flip_h"


def test_orientation_ops_tag7_order() -> None:
    """Tag 7 (transverse-equivalent): flip_h first, then rot270."""
    ops = coords.ORIENTATION_OPS[7]
    assert ops == ["flip_h", "rot270"]
    assert ops[0] == "flip_h"


def test_orientation_is_identity() -> None:
    assert coords.orientation_is_identity(None) is True
    assert coords.orientation_is_identity(1) is True
    assert coords.orientation_is_identity(6) is False
    assert coords.orientation_is_identity(8) is False
    assert coords.orientation_is_identity(0) is False


def test_orient_honesty_formula() -> None:
    """M2: session_flag OR tag identity."""
    tag = 6
    session_flag = False
    honesty = bool(session_flag) or coords.orientation_is_identity(tag)
    assert honesty is False
    session_flag = True
    honesty = bool(session_flag) or coords.orientation_is_identity(tag)
    assert honesty is True
    # identity tag → true even without flag
    assert (False or coords.orientation_is_identity(1)) is True
    assert (False or coords.orientation_is_identity(None)) is True


# ---------------------------------------------------------------------------
# Security code + wiring
# ---------------------------------------------------------------------------


def test_metadata_write_failed_code_defined() -> None:
    assert hasattr(sec, "CODE_METADATA_WRITE_FAILED")
    assert sec.CODE_METADATA_WRITE_FAILED == "METADATA_WRITE_FAILED"


def test_wiring_pyproject_lists_coords() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "gimp_mcp_coords" in text


def test_wiring_server_has_map_and_normalize_tools() -> None:
    text = (ROOT / "gimp_mcp_server.py").read_text(encoding="utf-8")
    assert "def map_preview_to_image(" in text
    assert "def map_image_to_preview(" in text
    assert "def map_layer_local_to_image(" in text
    assert "def map_image_to_layer_local(" in text
    assert "def normalize_image_orientation(" in text


def test_wiring_plugin_normalize_dispatcher() -> None:
    text = (ROOT / "gimp-mcp-plugin.py").read_text(encoding="utf-8")
    assert "normalize_image_orientation" in text
    assert "def _normalize_image_orientation" in text
    assert "_orientation_normalized" in text
    # Must not call policy_rotate / legacy rotate/flip helpers for normalize
    body_start = text.find("def _normalize_image_orientation")
    assert body_start != -1
    rest = text[body_start:]
    end = rest.find("\n    def ")
    body = rest[:end] if end != -1 else rest[:6000]
    # Docstring may mention policy_rotate as a warning; no executable call
    assert ".policy_rotate(" not in body
    assert "self._rotate_image(" not in body
    assert "self._flip_image(" not in body
    assert "_bump_image_generation" in body
    assert "_apply_orientation_ops" in body
    # Fail-closed: pixel ops tracked so mid-op exceptions also undo
    assert "ops_started" in body
    assert "image.undo()" in body
    assert "CODE_METADATA_WRITE_FAILED" in body
