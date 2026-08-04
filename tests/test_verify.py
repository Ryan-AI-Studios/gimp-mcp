"""Offline tests for track 0014 pixel verification (stdlib PNG, no GIMP/Pillow)."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

import gimp_mcp_security as sec
import gimp_mcp_snapshot as snap
import gimp_mcp_verify as verify
from gimp_agent import exit_codes as ec
from tests.test_export_alpha import build_minimal_png


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _build_png_with_filter(
    *,
    width: int,
    height: int,
    color_type: int,
    filtered_rows: bytes,
) -> bytes:
    """Build PNG from pre-filtered scanlines (each row: filter_byte + samples)."""
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    row_bytes = width * channels
    expected = height * (1 + row_bytes)
    if len(filtered_rows) != expected:
        raise ValueError(f"filtered_rows length {len(filtered_rows)} != {expected}")
    compressed = zlib.compress(filtered_rows, 9)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return (
        snap.PNG_SIGNATURE
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", compressed)
        + _png_chunk(b"IEND", b"")
    )


def _encode_filter_row(filter_type: int, recon: bytes, prev: bytes, bpp: int) -> bytes:
    """Encode one scanline from reconstructed samples using PNG filter ``filter_type``."""
    row_bytes = len(recon)
    out = bytearray(1 + row_bytes)
    out[0] = filter_type
    if filter_type == 0:
        out[1:] = recon
    elif filter_type == 1:  # Sub
        for i in range(row_bytes):
            left = recon[i - bpp] if i >= bpp else 0
            out[1 + i] = (recon[i] - left) & 0xFF
    elif filter_type == 2:  # Up
        for i in range(row_bytes):
            out[1 + i] = (recon[i] - prev[i]) & 0xFF
    elif filter_type == 3:  # Average
        for i in range(row_bytes):
            left = recon[i - bpp] if i >= bpp else 0
            up = prev[i]
            out[1 + i] = (recon[i] - ((left + up) // 2)) & 0xFF
    elif filter_type == 4:  # Paeth
        for i in range(row_bytes):
            left = recon[i - bpp] if i >= bpp else 0
            up = prev[i]
            up_left = prev[i - bpp] if i >= bpp else 0
            pred = verify.paeth_predictor(left, up, up_left)
            out[1 + i] = (recon[i] - pred) & 0xFF
    else:
        raise ValueError(filter_type)
    return bytes(out)


def _build_filtered_rgb_png(
    pixels_rgb: bytes,
    *,
    width: int,
    height: int,
    filter_types: list[int],
) -> bytes:
    """Build RGB PNG applying given filter type per row (length == height)."""
    assert len(filter_types) == height
    bpp = 3
    row_bytes = width * bpp
    assert len(pixels_rgb) == height * row_bytes
    prev = bytes(row_bytes)
    filtered = bytearray()
    for y, ft in enumerate(filter_types):
        recon = pixels_rgb[y * row_bytes : (y + 1) * row_bytes]
        filtered.extend(_encode_filter_row(ft, recon, prev, bpp))
        prev = recon
    return _build_png_with_filter(
        width=width, height=height, color_type=2, filtered_rows=bytes(filtered)
    )


# ---------------------------------------------------------------------------
# Paeth + defilter
# ---------------------------------------------------------------------------


def test_paeth_predictor_corners() -> None:
    # a closest: p=a+b-c → distances favor left
    assert verify.paeth_predictor(100, 50, 50) == 100
    # b closest
    assert verify.paeth_predictor(10, 100, 10) == 100
    # c closest: a=50,b=50,c=10 → p=90; pa=40,pb=40,pc=80 → tie pa wins (a)
    # true c win: a=0,b=0,c=50 → p=-50; pa=50,pb=50,pc=100 → still a on ties
    # force c: a=80,b=80,c=100 → p=60; pa=20,pb=20,pc=40 → a
    # PNG prefers a on ties; use a case where pc is strictly smallest:
    # a=10,b=50,c=30 → p=30; pa=20,pb=20,pc=0 → c
    assert verify.paeth_predictor(10, 50, 30) == 30


def test_defilter_paeth_roundtrip() -> None:
    # 2x2 RGB with non-trivial values
    width, height = 2, 2
    pixels = bytes(
        [
            10,
            20,
            30,
            40,
            50,
            60,
            70,
            80,
            90,
            100,
            110,
            120,
        ]
    )
    data = _build_filtered_rgb_png(pixels, width=width, height=height, filter_types=[4, 4])
    img = verify.load_png(data)
    assert img.mode == "RGB"
    assert img.width == 2 and img.height == 2
    assert img.pixels == pixels


def test_defilter_types_1_2_3() -> None:
    width, height = 3, 3
    # Gradient pattern
    pixels = bytes([(r * 3 + c) % 256 for r in range(height) for c in range(width * 3)])
    for ft in (1, 2, 3):
        data = _build_filtered_rgb_png(
            pixels, width=width, height=height, filter_types=[ft] * height
        )
        img = verify.load_png(data)
        assert img.pixels == pixels, f"filter type {ft} failed"


def test_filter0_build_minimal_png_loads() -> None:
    data = build_minimal_png(width=2, height=2, color_type=2)
    img = verify.load_png(data)
    assert img.width == 2 and img.height == 2
    assert img.mode == "RGB"
    assert img.pixels == b"\xff\xff\xff" * 4


# ---------------------------------------------------------------------------
# Metrics / compare
# ---------------------------------------------------------------------------


def test_identical_mae_zero(tmp_path: Path) -> None:
    data = build_minimal_png(width=4, height=4, color_type=2)
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    a.write_bytes(data)
    b.write_bytes(data)
    r = verify.compare_images(a, b)
    assert r["ok"] is True
    assert r["pass"] is True
    assert r["mae"] == 0.0
    assert r["max_ae"] == 0
    assert r["changed_pixels"] == 0
    assert r["changed_fraction"] == 0.0
    assert r["ssim_computed"] is True
    assert r["ssim"] == pytest.approx(1.0)


def test_one_pixel_change(tmp_path: Path) -> None:
    # 2x1 RGB: white vs one pixel black
    white = build_minimal_png(
        width=2,
        height=1,
        color_type=2,
        pixels=b"\x00" + b"\xff\xff\xff" * 2,
    )
    # second pixel black
    changed = build_minimal_png(
        width=2,
        height=1,
        color_type=2,
        pixels=b"\x00" + b"\xff\xff\xff" + b"\x00\x00\x00",
    )
    a = tmp_path / "w.png"
    b = tmp_path / "c.png"
    a.write_bytes(white)
    b.write_bytes(changed)
    r = verify.compare_images(a, b)
    assert r["ok"] is True
    assert r["changed_pixels"] >= 1
    assert r["max_ae"] == 255
    assert r["mae"] > 0


def test_require_mutation_fails_identical(tmp_path: Path) -> None:
    data = build_minimal_png(width=2, height=2, color_type=2)
    p = tmp_path / "x.png"
    p.write_bytes(data)
    r = verify.compare_images(p, p, thresholds={"require_mutation": True})
    assert r["ok"] is True
    assert r["pass"] is False
    assert any("require_mutation" in f for f in r["failures"])


def test_self_compare_pass_require_mutation_fails(tmp_path: Path) -> None:
    data = build_minimal_png(width=3, height=3, color_type=6)
    p = tmp_path / "self.png"
    p.write_bytes(data)
    r = verify.compare_images(p, p)
    assert r["pass"] is True
    assert r["mae"] == 0.0
    r2 = verify.compare_images(p, p, thresholds={"require_mutation": True})
    assert r2["pass"] is False


def test_size_mismatch(tmp_path: Path) -> None:
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    a.write_bytes(build_minimal_png(width=2, height=2, color_type=2))
    b.write_bytes(build_minimal_png(width=3, height=2, color_type=2))
    r = verify.compare_images(a, b)
    assert r["ok"] is True
    assert r["pass"] is False
    assert r["size_mismatch"] is True
    assert any("size mismatch" in f for f in r["failures"])


def test_rgb_vs_rgba_mismatch_and_ignore_alpha(tmp_path: Path) -> None:
    rgb = tmp_path / "rgb.png"
    rgba = tmp_path / "rgba.png"
    rgb.write_bytes(build_minimal_png(width=2, height=2, color_type=2))
    rgba.write_bytes(build_minimal_png(width=2, height=2, color_type=6))
    r = verify.compare_images(rgb, rgba, raise_on_fail=False)
    assert r["ok"] is True
    assert r["pass"] is False
    assert any("mode mismatch" in f for f in r["failures"])

    r2 = verify.compare_images(rgb, rgba, ignore_alpha=True)
    assert r2["ok"] is True
    assert r2["pass"] is True
    assert r2["channels"] == 3
    assert r2["mae"] == 0.0


def test_raise_on_fail_verify_failed(tmp_path: Path) -> None:
    data = build_minimal_png(width=1, height=1, color_type=2)
    p = tmp_path / "x.png"
    p.write_bytes(data)
    with pytest.raises(sec.GimpMcpError) as ei:
        verify.compare_images(p, p, thresholds={"require_mutation": True}, raise_on_fail=True)
    assert ei.value.code == sec.CODE_VERIFY_FAILED
    assert ec.exit_code_for(ei.value.code) == 8


def test_write_diff_png(tmp_path: Path) -> None:
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    diff = tmp_path / "diff.png"
    a.write_bytes(
        build_minimal_png(
            width=2,
            height=1,
            color_type=2,
            pixels=b"\x00" + b"\x00\x00\x00" + b"\xff\x00\x00",
        )
    )
    b.write_bytes(
        build_minimal_png(
            width=2,
            height=1,
            color_type=2,
            pixels=b"\x00" + b"\x00\x00\x00" + b"\x00\x00\x00",
        )
    )
    r = verify.compare_images(a, b, write_diff_path=diff)
    assert r["diff_path"] == str(diff)
    assert diff.is_file()
    img = verify.load_png(diff)
    assert img.mode == "L"
    assert img.pixels[0] == 0
    assert img.pixels[1] == 255  # max |ΔRGB| = 255


def test_gray_vs_rgb_promotion(tmp_path: Path) -> None:
    g = tmp_path / "g.png"
    rgb = tmp_path / "rgb.png"
    # gray white
    g.write_bytes(build_minimal_png(width=1, height=1, color_type=0, pixels=b"\x00\xff"))
    rgb.write_bytes(build_minimal_png(width=1, height=1, color_type=2, pixels=b"\x00\xff\xff\xff"))
    r = verify.compare_images(g, rgb)
    assert r["pass"] is True
    assert r["channels"] == 3


# ---------------------------------------------------------------------------
# Budgets / unsupported
# ---------------------------------------------------------------------------


def test_pixel_budget_policy_denied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(verify.ENV_MAX_DECODED_PIXELS, "1")
    p = tmp_path / "big.png"
    p.write_bytes(build_minimal_png(width=2, height=2, color_type=2))
    with pytest.raises(sec.GimpMcpError) as ei:
        verify.load_png(p)
    assert ei.value.code == sec.CODE_POLICY_DENIED
    assert ec.exit_code_for(sec.CODE_POLICY_DENIED) == 6


def test_file_size_budget_policy_denied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-read file-size guard → POLICY_DENIED (exit 6)."""
    monkeypatch.setenv(verify.ENV_MAX_VERIFY_FILE_BYTES, "10")
    p = tmp_path / "large.png"
    p.write_bytes(build_minimal_png(width=4, height=4, color_type=2))
    assert p.stat().st_size > 10
    with pytest.raises(sec.GimpMcpError) as ei:
        verify.load_png(p)
    assert ei.value.code == sec.CODE_POLICY_DENIED
    assert ec.exit_code_for(sec.CODE_POLICY_DENIED) == 6


def test_untrusted_budget_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(verify.ENV_MAX_DECODED_PIXELS, raising=False)
    monkeypatch.setenv(verify.ENV_UNTRUSTED_IMAGES, "1")
    assert verify.max_decoded_pixels() == verify.DEFAULT_UNTRUSTED_MAX_PIXELS
    monkeypatch.delenv(verify.ENV_UNTRUSTED_IMAGES, raising=False)
    assert verify.max_decoded_pixels() == verify.DEFAULT_TRUSTED_MAX_PIXELS


def test_16bit_unsupported() -> None:
    data = build_minimal_png(width=1, height=1, color_type=6, bit_depth=16)
    with pytest.raises(sec.GimpMcpError) as ei:
        verify.load_png(data)
    assert ei.value.code == sec.CODE_UNSUPPORTED


def test_interlaced_unsupported() -> None:
    # Manually craft interlaced IHDR
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 1)  # interlace=1
    row = b"\x00\xff\xff\xff"
    compressed = zlib.compress(row, 9)
    data = (
        snap.PNG_SIGNATURE
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", compressed)
        + _png_chunk(b"IEND", b"")
    )
    with pytest.raises(sec.GimpMcpError) as ei:
        verify.load_png(data)
    assert ei.value.code == sec.CODE_UNSUPPORTED


# ---------------------------------------------------------------------------
# refine_should_stop
# ---------------------------------------------------------------------------


def test_refine_max_loops() -> None:
    history = [
        {"pass": False, "mae": 10.0},
        {"pass": False, "mae": 8.0},
        {"pass": False, "mae": 6.0},
    ]
    d = verify.refine_should_stop(history, max_loops=3)
    assert d.stop is True
    assert d.reason == "max_loops"
    assert d.loops == 3


def test_refine_passed() -> None:
    d = verify.refine_should_stop([{"pass": True, "mae": 0.0}], max_loops=3)
    assert d.stop is True
    assert d.reason == "passed"


def test_refine_regression() -> None:
    history = [
        {"pass": False, "mae": 5.0},
        {"pass": False, "mae": 7.0},
    ]
    d = verify.refine_should_stop(history, max_loops=3)
    assert d.stop is True
    assert d.reason == "regression"


def test_refine_no_improvement() -> None:
    history = [
        {"pass": False, "mae": 5.0},
        {"pass": False, "mae": 5.0},
    ]
    d = verify.refine_should_stop(history, max_loops=3, min_improvement=0.1)
    assert d.stop is True
    assert d.reason == "no_improvement"


def test_refine_continue() -> None:
    history = [
        {"pass": False, "mae": 10.0},
        {"pass": False, "mae": 5.0},
    ]
    d = verify.refine_should_stop(history, max_loops=3, min_improvement=0.0)
    assert d.stop is False
    assert d.reason == "continue"


# ---------------------------------------------------------------------------
# verify_artifact
# ---------------------------------------------------------------------------


def test_verify_artifact_png_pass(tmp_path: Path) -> None:
    p = tmp_path / "out.png"
    data = build_minimal_png(width=8, height=4, color_type=6)
    p.write_bytes(data)
    r = verify.verify_artifact(
        p,
        {
            "format": "png",
            "width": 8,
            "height": 4,
            "require_alpha": True,
            "min_bytes": 1,
        },
    )
    assert r["ok"] is True
    assert r["pass"] is True
    assert r["has_alpha"] is True
    assert r["sha256"]


def test_verify_artifact_bad_signature(tmp_path: Path) -> None:
    p = tmp_path / "not.png"
    p.write_bytes(b"not a png file content here")
    r = verify.verify_artifact(p, {"format": "png"})
    assert r["ok"] is True
    assert r["pass"] is False


def test_verify_artifact_jpeg_unsupported(tmp_path: Path) -> None:
    p = tmp_path / "x.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
    with pytest.raises(sec.GimpMcpError) as ei:
        verify.verify_artifact(p, {"format": "jpeg"})
    assert ei.value.code == sec.CODE_UNSUPPORTED


def test_verify_artifact_sha256(tmp_path: Path) -> None:
    import hashlib

    p = tmp_path / "a.png"
    data = build_minimal_png(width=1, height=1, color_type=2)
    p.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    r = verify.verify_artifact(p, {"format": "png", "sha256": digest})
    assert r["pass"] is True
    r2 = verify.verify_artifact(p, {"format": "png", "sha256": "0" * 64})
    assert r2["pass"] is False


# ---------------------------------------------------------------------------
# Packaging / capabilities / ship set
# ---------------------------------------------------------------------------


def test_not_in_expected_plugin_files() -> None:
    from gimp_agent import paths as pathmod

    assert "gimp_mcp_verify.py" not in pathmod.EXPECTED_PLUGIN_FILES
    assert len(pathmod.EXPECTED_PLUGIN_FILES) == 9
    assert "gimp_mcp_filters.py" in pathmod.EXPECTED_PLUGIN_FILES


def test_capability_flags() -> None:
    from gimp_mcp_state import default_capabilities

    caps = default_capabilities()
    assert caps["pixel_verification"] is True
    assert caps["alpha_snapshot"] is False


def test_pyproject_registers_verify() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "gimp_mcp_verify" in text


def test_alpha_stats_rgba(tmp_path: Path) -> None:
    # One opaque white + one fully transparent
    # color_type 6: filter + RGBA per pixel
    pixels = b"\x00" + b"\xff\xff\xff\xff" + b"\xff\xff\xff\x00"
    data = build_minimal_png(width=2, height=1, color_type=6, pixels=pixels)
    p = tmp_path / "a.png"
    p.write_bytes(data)
    img = verify.load_png(p)
    assert verify._alpha_transparent_count(img) == 1


def test_ssim_auto_off_large(monkeypatch: pytest.MonkeyPatch) -> None:
    """auto disables SSIM when n_pixels exceeds SSIM_AUTO_MAX_PIXELS (monkeypatched)."""
    monkeypatch.setattr(verify, "SSIM_AUTO_MAX_PIXELS", 1)
    # 2x1 RGB -> 2 pixels > auto max of 1
    buf = b"\x00\x00\x00" + b"\xff\xff\xff"
    m = verify.metrics_buffers(buf, buf, width=2, height=1, channels=3, compute_ssim="auto")
    assert m["ssim_computed"] is False
    assert m["ssim"] is None
    # Below threshold still computes
    m2 = verify.metrics_buffers(
        buf[:3], buf[:3], width=1, height=1, channels=3, compute_ssim="auto"
    )
    assert m2["ssim_computed"] is True
    assert m2["ssim"] is not None
