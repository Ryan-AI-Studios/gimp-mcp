"""Pure unit tests for gimp_mcp_snapshot helpers (no GIMP required)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import gimp_mcp_snapshot as snap

# ---------------------------------------------------------------------------
# normalize_region
# ---------------------------------------------------------------------------


def test_normalize_region_none_and_empty() -> None:
    assert snap.normalize_region(None) is None
    assert snap.normalize_region({}) is None


def test_normalize_region_origin_keys() -> None:
    out = snap.normalize_region({"origin_x": 10, "origin_y": 20, "width": 100, "height": 80})
    assert out == {
        "origin_x": 10,
        "origin_y": 20,
        "width": 100,
        "height": 80,
    }


def test_normalize_region_x_y_aliases() -> None:
    out = snap.normalize_region({"x": 5, "y": 7, "width": 50, "height": 40})
    assert out is not None
    assert out["origin_x"] == 5
    assert out["origin_y"] == 7
    assert out["width"] == 50
    assert out["height"] == 40


def test_normalize_region_origin_x_preferred_over_x() -> None:
    out = snap.normalize_region(
        {"origin_x": 1, "x": 99, "origin_y": 2, "y": 88, "width": 10, "height": 10}
    )
    assert out is not None
    assert out["origin_x"] == 1
    assert out["origin_y"] == 2


def test_normalize_region_preserves_max_dims() -> None:
    out = snap.normalize_region(
        {
            "origin_x": 0,
            "origin_y": 0,
            "width": 100,
            "height": 100,
            "max_width": 50,
            "max_height": 40,
        }
    )
    assert out is not None
    assert out["max_width"] == 50
    assert out["max_height"] == 40


def test_normalize_region_rejects_negatives() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        snap.normalize_region({"x": -1, "y": 0, "width": 10, "height": 10})
    with pytest.raises(ValueError, match="non-negative"):
        snap.normalize_region({"origin_x": 0, "origin_y": -2, "width": 10, "height": 10})
    with pytest.raises(ValueError, match="non-negative"):
        snap.normalize_region({"origin_x": 0, "origin_y": 0, "width": -1, "height": 10})


# ---------------------------------------------------------------------------
# compute_fit_scale
# ---------------------------------------------------------------------------


def test_compute_fit_scale_width_limited() -> None:
    # 200x100 into 50x50 → width limited → 50x25
    assert snap.compute_fit_scale(200, 100, 50, 50) == (50, 25)


def test_compute_fit_scale_height_limited() -> None:
    # 100x200 into 50x50 → height limited → 25x50
    assert snap.compute_fit_scale(100, 200, 50, 50) == (25, 50)


def test_compute_fit_scale_square() -> None:
    assert snap.compute_fit_scale(1000, 1000, 512, 512) == (512, 512)


def test_compute_fit_scale_rejects_non_positive() -> None:
    with pytest.raises(ValueError):
        snap.compute_fit_scale(0, 100, 50, 50)
    with pytest.raises(ValueError):
        snap.compute_fit_scale(100, 100, 0, 50)


# ---------------------------------------------------------------------------
# build_mapping_metadata — full canvas vs region-relative scales
# ---------------------------------------------------------------------------


def test_build_mapping_full_canvas_scales() -> None:
    m = snap.build_mapping_metadata(
        image_index=0,
        source_width=4000,
        source_height=3000,
        rendered_width=512,
        rendered_height=384,
    )
    assert m["mode"] == "visible_composite"
    assert m["image_index"] == 0
    assert m["source_width"] == 4000
    assert m["source_height"] == 3000
    assert m["rendered_width"] == 512
    assert m["rendered_height"] == 384
    assert m["region"] is None
    assert m["composite_method"] == snap.COMPOSITE_METHOD_MERGE
    assert m["scale_x"] == pytest.approx(512 / 4000)
    assert m["scale_y"] == pytest.approx(384 / 3000)
    # Additive coordinate declaration (track 0008)
    assert m["coordinate_space"] == "image-pixels"
    assert m["origin"] == "top-left"
    assert m["x_axis"] == "right"
    assert m["y_axis"] == "down"
    assert m["preview_padding_x"] == 0
    assert m["preview_padding_y"] == 0
    assert m["view_rotation_ignored"] is True
    assert m["pixel_orientation_normalized"] is False
    assert m["exif_orientation_original"] is None


def test_build_mapping_exif_kwargs() -> None:
    m = snap.build_mapping_metadata(
        image_index=0,
        source_width=100,
        source_height=100,
        rendered_width=50,
        rendered_height=50,
        pixel_orientation_normalized=True,
        exif_orientation_original=6,
    )
    assert m["pixel_orientation_normalized"] is True
    assert m["exif_orientation_original"] == 6


def test_build_mapping_region_relative_scales() -> None:
    """CRITICAL: scale = rendered / region dims, NOT full canvas."""
    region = {"origin_x": 100, "origin_y": 200, "width": 800, "height": 600}
    m = snap.build_mapping_metadata(
        image_index=1,
        source_width=4000,
        source_height=3000,
        rendered_width=400,
        rendered_height=300,
        region=region,
        composite_method=snap.COMPOSITE_METHOD_MERGE,
    )
    assert m["region"] == {
        "origin_x": 100,
        "origin_y": 200,
        "width": 800,
        "height": 600,
    }
    # Region-relative (correct)
    assert m["scale_x"] == pytest.approx(400 / 800)
    assert m["scale_y"] == pytest.approx(300 / 600)
    # Must NOT be full-canvas scales
    assert m["scale_x"] != pytest.approx(400 / 4000)
    assert m["scale_y"] != pytest.approx(300 / 3000)


def test_build_mapping_region_x_y_aliases() -> None:
    m = snap.build_mapping_metadata(
        image_index=0,
        source_width=1000,
        source_height=1000,
        rendered_width=100,
        rendered_height=50,
        region={"x": 10, "y": 20, "width": 200, "height": 100},
    )
    assert m["region"] == {"origin_x": 10, "origin_y": 20, "width": 200, "height": 100}
    assert m["scale_x"] == pytest.approx(100 / 200)
    assert m["scale_y"] == pytest.approx(50 / 100)


def test_build_mapping_flatten_method() -> None:
    m = snap.build_mapping_metadata(
        image_index=0,
        source_width=100,
        source_height=100,
        rendered_width=50,
        rendered_height=50,
        composite_method=snap.COMPOSITE_METHOD_FLATTEN,
    )
    assert m["composite_method"] == "flatten"


# ---------------------------------------------------------------------------
# select_image_index
# ---------------------------------------------------------------------------


def test_select_image_index_ok() -> None:
    imgs = ["a", "b", "c"]
    assert snap.select_image_index(imgs, 0) == "a"
    assert snap.select_image_index(imgs, 2) == "c"


def test_select_image_index_rejects_negative() -> None:
    with pytest.raises(IndexError, match="negative"):
        snap.select_image_index(["a"], -1)


def test_select_image_index_rejects_oob() -> None:
    with pytest.raises(IndexError, match="out of range"):
        snap.select_image_index(["a"], 1)
    with pytest.raises(IndexError, match="out of range"):
        snap.select_image_index([], 0)


# ---------------------------------------------------------------------------
# Temp path policy
# ---------------------------------------------------------------------------


def test_temp_path_under_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(snap.ENV_WORKSPACE, str(tmp_path))
    d = snap.ensure_snapshot_temp_dir()
    assert d == tmp_path / snap.SNAPSHOT_TMP_SUBDIR
    assert d.is_dir()
    p = snap.snapshot_temp_path()
    assert p.parent == d
    assert p.name.startswith("snapshot-")
    assert p.suffix == ".png"
    # mkstemp creates the file; clean up
    p.unlink(missing_ok=True)


def test_temp_path_pid_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(snap.ENV_WORKSPACE, raising=False)
    monkeypatch.setattr(
        snap.tempfile,
        "gettempdir",
        lambda: str(tmp_path),
    )
    d = snap.ensure_snapshot_temp_dir()
    assert d == tmp_path / f"gimp-mcp-{os.getpid()}"
    assert d.is_dir()
    p = snap.snapshot_temp_path()
    assert p.parent == d
    p.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# PNG validation (Codex P2-2)
# ---------------------------------------------------------------------------


def test_validate_png_bytes_accepts_signature() -> None:
    assert snap.validate_png_bytes(b"\x89PNG\r\n\x1a\n") is True
    assert snap.validate_png_bytes(b"\x89PNG\r\n\x1a\n" + b"IHDR-rest") is True


def test_validate_png_bytes_rejects_empty_and_non_png() -> None:
    assert snap.validate_png_bytes(b"") is False
    assert snap.validate_png_bytes(b"\x00" * 8) is False
    assert snap.validate_png_bytes(b"PNG") is False
    assert snap.validate_png_bytes(b"\x89PNG\r\n\x1a") is False  # truncated
    assert snap.validate_png_bytes(b"not a png file at all") is False


def test_validate_png_file_empty_mkstemp_style(tmp_path: Path) -> None:
    """snapshot_temp_path pre-creates empty files — must not validate as PNG."""
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    assert snap.validate_png_file(empty) is False
    assert snap.validate_png_file(tmp_path / "missing.png") is False


def test_validate_png_file_with_signature(tmp_path: Path) -> None:
    p = tmp_path / "ok.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    assert snap.validate_png_file(p) is True
    assert snap.validate_png_file(str(p)) is True


# ---------------------------------------------------------------------------
# Server ToolResult pass-through copies additive 0008 keys (H5)
# ---------------------------------------------------------------------------


def test_server_pass_through_copies_coordinate_space() -> None:
    """Pass-through branch must emit coordinate_space (not only rebuild path)."""
    import base64

    # Minimal valid PNG signature as payload for ToolResult builder
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    plugin_results = {
        "image_data": base64.b64encode(png).decode("ascii"),
        "mode": "visible_composite",
        "scale_x": 0.5,
        "scale_y": 0.5,
        "source_width": 200,
        "source_height": 100,
        "rendered_width": 100,
        "rendered_height": 50,
        "width": 100,
        "height": 50,
        "image_index": 0,
        "region": None,
        "composite_method": "merge_visible_layers",
        # Additive keys present (as plugin flattens them)
        "coordinate_space": "image-pixels",
        "origin": "top-left",
        "x_axis": "right",
        "y_axis": "down",
        "preview_padding_x": 0,
        "preview_padding_y": 0,
        "view_rotation_ignored": True,
        "pixel_orientation_normalized": True,
        "exif_orientation_original": 6,
    }
    # Import after env is fine — server module is pure enough for unit
    from gimp_mcp_server import _snapshot_tool_result

    tr = _snapshot_tool_result(plugin_results, image_index=0, write_filesystem=False)
    # ToolResult may expose structured_content or structuredContent
    sc = getattr(tr, "structured_content", None) or getattr(tr, "structuredContent", None)
    assert sc is not None
    assert sc["coordinate_space"] == "image-pixels"
    assert sc["origin"] == "top-left"
    assert sc["x_axis"] == "right"
    assert sc["y_axis"] == "down"
    assert sc["preview_padding_x"] == 0
    assert sc["preview_padding_y"] == 0
    assert sc["view_rotation_ignored"] is True
    assert sc["pixel_orientation_normalized"] is True
    assert sc["exif_orientation_original"] == 6
    assert sc["scale_x"] == pytest.approx(0.5)


def test_server_pass_through_defaults_missing_additive_keys() -> None:
    """When plugin omits additive keys, pass-through still defaults them."""
    import base64

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    plugin_results = {
        "image_data": base64.b64encode(png).decode("ascii"),
        "mode": "visible_composite",
        "scale_x": 1.0,
        "scale_y": 1.0,
        "source_width": 10,
        "source_height": 10,
        "rendered_width": 10,
        "rendered_height": 10,
        "width": 10,
        "height": 10,
        "image_index": 0,
    }
    from gimp_mcp_server import _snapshot_tool_result

    tr = _snapshot_tool_result(plugin_results, image_index=0, write_filesystem=False)
    sc = getattr(tr, "structured_content", None) or getattr(tr, "structuredContent", None)
    assert sc is not None
    assert sc["coordinate_space"] == "image-pixels"
    assert sc["view_rotation_ignored"] is True
    assert sc["preview_padding_x"] == 0
    assert sc["pixel_orientation_normalized"] is False
    assert sc["exif_orientation_original"] is None


def test_wiring_plugin_bitmap_flattens_additive_keys() -> None:
    """Plugin get_image_bitmap results must flatten coordinate_space for pass-through."""
    text = (Path(__file__).resolve().parents[1] / "gimp-mcp-plugin.py").read_text(encoding="utf-8")
    body_start = text.find("def _get_current_image_bitmap")
    assert body_start != -1
    rest = text[body_start:]
    end = rest.find("\n    def ")
    body = rest[:end] if end != -1 else rest[:40000]
    assert "coordinate_space" in body
    assert "pixel_orientation_normalized" in body
    assert "exif_orientation_original" in body
    assert "build_mapping_metadata" in body
