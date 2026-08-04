"""Offline asserts for the committed min fixture corpus (track 0022)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import gimp_mcp_verify as verify
from tests.fixture_paths import (
    FIXTURES_DIR,
    copy_fixture_to_workspace,
    fixture_path,
)

OPAQUE = "rgb_2x2_opaque.png"
ALPHA = "rgba_2x2_alpha.png"
DELTA = "rgb_2x2_delta.png"

# Must match tests/fixtures/README.md sha256 table (machine-enforced SoT).
FIXTURE_SHA256 = {
    OPAQUE: "7a85b76bbe808dab07fd927f64b9c8dbfa00743889eb376162c9ab0bf616b4d5",
    ALPHA: "a9d1b76d3d9d086248bc2d4f413f1e2829636a8a0a75b802e7025664a6248264",
    DELTA: "68c41bb798155f8ad4c0280b6540e49f18457b263986fa6edbf58dc0821f3cb1",
}


def test_fixture_files_exist() -> None:
    assert FIXTURES_DIR.is_dir()
    for name in (OPAQUE, ALPHA, DELTA):
        p = fixture_path(name)
        assert p.is_file(), f"missing fixture {name}"
        assert p.stat().st_size > 0


def test_fixture_sha256_matches_readme() -> None:
    for name, expected in FIXTURE_SHA256.items():
        got = hashlib.sha256(fixture_path(name).read_bytes()).hexdigest()
        assert got == expected, f"{name}: sha256 mismatch (regenerate + update README)"


def test_fixture_path_rejects_traversal() -> None:
    with pytest.raises(ValueError):
        fixture_path("../secret.png")
    with pytest.raises(ValueError):
        fixture_path("..\\secret.png")
    with pytest.raises(ValueError):
        fixture_path("/etc/passwd")
    with pytest.raises(ValueError):
        fixture_path(r"C:\Windows\x.png")
    with pytest.raises(ValueError):
        fixture_path("")


def test_fixture_dims_2x2() -> None:
    for name in (OPAQUE, ALPHA, DELTA):
        img = verify.load_png(fixture_path(name))
        assert img.width == 2
        assert img.height == 2


def test_alpha_fixture_has_alpha() -> None:
    img = verify.load_png(fixture_path(ALPHA))
    assert img.mode in ("RGBA", "LA") or "A" in img.mode
    report = verify.verify_artifact(
        fixture_path(ALPHA),
        {"format": "png", "width": 2, "height": 2, "require_alpha": True},
    )
    assert report["pass"] is True
    assert report["has_alpha"] is True
    assert report["width"] == 2
    assert report["height"] == 2


def test_opaque_fixture_no_alpha_required_false() -> None:
    report = verify.verify_artifact(
        fixture_path(OPAQUE),
        {"format": "png", "width": 2, "height": 2, "require_alpha": False},
    )
    assert report["pass"] is True
    assert report["has_alpha"] is False


def test_delta_vs_opaque_nonzero_metrics() -> None:
    m = verify.compare_images(fixture_path(OPAQUE), fixture_path(DELTA))
    assert m["ok"] is True
    assert m["size_mismatch"] is False
    assert m["mae"] is not None and float(m["mae"]) > 0.0
    assert m["changed_pixels"] is not None and int(m["changed_pixels"]) > 0


def test_copy_fixture_to_workspace_leaves_source_unchanged(tmp_path: Path) -> None:
    src = fixture_path(OPAQUE)
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    dest = copy_fixture_to_workspace(OPAQUE, tmp_path)
    assert dest.is_file()
    assert dest.parent == tmp_path
    assert dest.name == OPAQUE
    after = hashlib.sha256(src.read_bytes()).hexdigest()
    assert before == after
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == before
