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
