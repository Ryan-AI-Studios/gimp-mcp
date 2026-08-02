"""Offline tests for alpha-preserving export (Issue 16 / track 0005).

stdlib only — no Pillow, no live GIMP required.
"""

from __future__ import annotations

import re
import struct
import zlib
from pathlib import Path

import pytest

import gimp_mcp_export as exp
import gimp_mcp_snapshot as snap

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "gimp-mcp-plugin.py"
SERVER = ROOT / "gimp_mcp_server.py"
EXPORT_MOD = ROOT / "gimp_mcp_export.py"


def _method_body(source: str, method_name: str) -> str:
    """Extract a class method body via indentation heuristic.

    Supports multi-line signatures: ``def foo(\\n        self, ...``.
    """
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


# ---------------------------------------------------------------------------
# Synthetic PNG builders (stdlib)
# ---------------------------------------------------------------------------


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def build_minimal_png(
    *,
    width: int = 1,
    height: int = 1,
    bit_depth: int = 8,
    color_type: int = 2,
    pixels: bytes | None = None,
) -> bytes:
    """Build a minimal valid PNG with given IHDR color_type / bit_depth.

    color_type 2 = RGB, 6 = RGBA. For 16-bit, samples are 2 bytes each.
    """
    if color_type == 2:
        channels = 3
    elif color_type == 6:
        channels = 4
    elif color_type == 0:
        channels = 1
    elif color_type == 4:
        channels = 2
    else:
        raise ValueError(f"unsupported synthetic color_type {color_type}")

    sample_bytes = bit_depth // 8
    if bit_depth not in (8, 16):
        raise ValueError("synthetic builder supports bit_depth 8 or 16 only")
    row_bytes = width * channels * sample_bytes
    if pixels is None:
        # Opaque white (or white+full alpha for type 6)
        if bit_depth == 8:
            if color_type == 6:
                pixel = b"\xff\xff\xff\xff"
            elif color_type == 2:
                pixel = b"\xff\xff\xff"
            elif color_type == 4:
                pixel = b"\xff\xff"
            else:
                pixel = b"\xff"
        else:  # 16-bit
            if color_type == 6:
                pixel = b"\xff\xff" * 4
            elif color_type == 2:
                pixel = b"\xff\xff" * 3
            elif color_type == 4:
                pixel = b"\xff\xff" * 2
            else:
                pixel = b"\xff\xff"
        raw = b"".join(b"\x00" + pixel * width for _ in range(height))
    else:
        raw = pixels
        if len(raw) != height * (1 + row_bytes):
            raise ValueError("pixels length mismatch for filter-prefixed rows")

    compressed = zlib.compress(raw, 9)
    ihdr = struct.pack(
        ">IIBBBBB",
        width,
        height,
        bit_depth,
        color_type,
        0,  # compression
        0,  # filter
        0,  # interlace
    )
    return (
        snap.PNG_SIGNATURE
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", compressed)
        + _png_chunk(b"IEND", b"")
    )


# ---------------------------------------------------------------------------
# normalize_format / capability
# ---------------------------------------------------------------------------


def test_normalize_format() -> None:
    assert exp.normalize_format("PNG") == "png"
    assert exp.normalize_format(" jpg ") == "jpeg"
    assert exp.normalize_format("JPEG") == "jpeg"
    assert exp.normalize_format("webp") == "webp"


def test_can_preserve_alpha_for_format() -> None:
    assert exp.can_preserve_alpha_for_format("png") is True
    assert exp.can_preserve_alpha_for_format("webp") is True
    assert exp.can_preserve_alpha_for_format("tiff") is True
    assert exp.can_preserve_alpha_for_format("jpeg") is False
    assert exp.can_preserve_alpha_for_format("jpg") is False


def test_format_capability_matrix() -> None:
    m = exp.format_capability_matrix()
    assert m["png"] is True
    assert m["jpeg"] is False
    assert m["webp"] is True
    assert m["tiff"] is True


# ---------------------------------------------------------------------------
# Policy matrix
# ---------------------------------------------------------------------------


def test_policy_png_auto_preserves_alpha() -> None:
    p = exp.resolve_export_policy("png", None, False, verify=True)
    assert p.error is None
    assert p.preserve_alpha is True
    assert p.flatten is False
    assert p.export_method == exp.EXPORT_METHOD_MERGE
    assert p.verify is True
    assert p.format == "png"


def test_policy_webp_tiff_auto() -> None:
    for fmt in ("webp", "tiff"):
        p = exp.resolve_export_policy(fmt, None, False)
        assert p.preserve_alpha is True
        assert p.export_method == exp.EXPORT_METHOD_MERGE


def test_policy_jpeg_auto_no_alpha() -> None:
    p = exp.resolve_export_policy("jpeg", None, False)
    assert p.error is None
    assert p.preserve_alpha is False
    assert p.format == "jpeg"


def test_policy_jpg_normalizes() -> None:
    p = exp.resolve_export_policy("jpg", None, False)
    assert p.format == "jpeg"


def test_policy_jpeg_preserve_alpha_true_errors() -> None:
    p = exp.resolve_export_policy("jpeg", True, False)
    assert p.error is not None
    assert p.code == exp.CODE_ALPHA_UNSUPPORTED_FORMAT


def test_policy_flatten_true_preserve_none_opaque() -> None:
    """Internal callers pass flatten=True → auto preserve_alpha=False (H4)."""
    p = exp.resolve_export_policy("png", None, True)
    assert p.error is None
    assert p.preserve_alpha is False
    assert p.flatten is True
    assert p.export_method == exp.EXPORT_METHOD_FLATTEN
    assert p.verify is False


def test_policy_flatten_true_preserve_true_conflict() -> None:
    p = exp.resolve_export_policy("png", True, True)
    assert p.error is not None
    assert p.code == exp.CODE_POLICY_CONFLICT


def test_policy_explicit_preserve_alpha_never_flatten() -> None:
    p = exp.resolve_export_policy("png", True, False)
    assert p.preserve_alpha is True
    assert p.flatten is False
    assert p.export_method == exp.EXPORT_METHOD_MERGE


def test_policy_explicit_preserve_false() -> None:
    p = exp.resolve_export_policy("png", False, False)
    assert p.preserve_alpha is False
    assert p.export_method == exp.EXPORT_METHOD_DIRECT


def test_policy_full_matrix_no_unexpected_errors() -> None:
    formats = ("png", "jpeg", "jpg", "webp", "tiff")
    preserves: list[bool | None] = [None, True, False]
    flattens = (True, False)
    for fmt in formats:
        for pa in preserves:
            for flat in flattens:
                p = exp.resolve_export_policy(fmt, pa, flat)
                # Only two error classes allowed
                if p.error is not None:
                    assert p.code in (
                        exp.CODE_ALPHA_UNSUPPORTED_FORMAT,
                        exp.CODE_POLICY_CONFLICT,
                    )
                else:
                    assert p.code is None
                    assert p.export_method in (
                        exp.EXPORT_METHOD_MERGE,
                        exp.EXPORT_METHOD_FLATTEN,
                        exp.EXPORT_METHOD_DIRECT,
                    )
                    # Never flatten when preserve_alpha is True
                    if p.preserve_alpha:
                        assert p.flatten is False
                        assert p.export_method == exp.EXPORT_METHOD_MERGE


# ---------------------------------------------------------------------------
# IHDR / file_has_alpha_channel
# ---------------------------------------------------------------------------


def test_png_ihdr_rgb8() -> None:
    data = build_minimal_png(color_type=2, bit_depth=8)
    info = exp.png_ihdr_info(data)
    assert info["width"] == 1
    assert info["height"] == 1
    assert info["bit_depth"] == 8
    assert info["color_type"] == 2
    assert exp.file_has_alpha_channel(data) is False


def test_png_ihdr_rgba8() -> None:
    data = build_minimal_png(color_type=6, bit_depth=8)
    info = exp.png_ihdr_info(data)
    assert info["color_type"] == 6
    assert info["bit_depth"] == 8
    assert exp.file_has_alpha_channel(data) is True


def test_png_ihdr_rgba16() -> None:
    """16-bit RGBA still color_type 6 (L3)."""
    data = build_minimal_png(color_type=6, bit_depth=16)
    info = exp.png_ihdr_info(data)
    assert info["color_type"] == 6
    assert info["bit_depth"] == 16
    assert exp.file_has_alpha_channel(data) is True


def test_png_ihdr_from_path(tmp_path: Path) -> None:
    data = build_minimal_png(color_type=6, bit_depth=8)
    p = tmp_path / "a.png"
    p.write_bytes(data)
    assert exp.file_has_alpha_channel(p) is True
    info = exp.png_ihdr_info(p)
    assert info["color_type"] == 6


def test_png_ihdr_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        exp.png_ihdr_info(b"not a png")


def test_png_signature_reuse() -> None:
    data = build_minimal_png(color_type=2)
    assert snap.validate_png_bytes(data)


# ---------------------------------------------------------------------------
# Result builders / ALPHA_LOST / not_applicable
# ---------------------------------------------------------------------------


def test_build_export_success_fields() -> None:
    r = exp.build_export_success(
        file_path="/tmp/out.png",
        format="png",
        file_size_bytes=100,
        preserve_alpha=True,
        preflight_has_alpha=True,
        alpha_verified=True,
        export_method=exp.EXPORT_METHOD_MERGE,
        pdb_procedure="file-png-export",
        png_color_type=6,
    )
    assert r["status"] == "success"
    assert r["alpha_verified"] is True
    assert r["png_color_type"] == 6
    assert r["pdb_procedure"] == "file-png-export"
    assert r["export_method"] == exp.EXPORT_METHOD_MERGE


def test_build_export_error_alpha_lost() -> None:
    r = exp.build_export_error(
        code=exp.CODE_ALPHA_LOST,
        error="preserve_alpha=True and preflight had alpha, but PNG color type is 2 (RGB)",
        file_path="/tmp/out.png",
        left_on_disk=True,
        png_color_type=2,
        preflight_has_alpha=True,
        preserve_alpha=True,
        property_errors=[],
    )
    assert r["status"] == "error"
    assert r["code"] == exp.CODE_ALPHA_LOST
    assert r["left_on_disk"] is True
    assert r["png_color_type"] == 2


def test_alpha_verified_not_applicable_opaque_source() -> None:
    data = build_minimal_png(color_type=2)
    v = exp.alpha_verified_value(
        preserve_alpha=True,
        preflight_has_alpha=False,
        verify=True,
        path_or_bytes=data,
        format="png",
    )
    assert v == "not_applicable"


def test_alpha_verified_true_when_rgba() -> None:
    data = build_minimal_png(color_type=6)
    v = exp.alpha_verified_value(
        preserve_alpha=True,
        preflight_has_alpha=True,
        verify=True,
        path_or_bytes=data,
        format="png",
    )
    assert v is True


def test_alpha_verified_false_when_rgb_after_alpha_source() -> None:
    data = build_minimal_png(color_type=2)
    v = exp.alpha_verified_value(
        preserve_alpha=True,
        preflight_has_alpha=True,
        verify=True,
        path_or_bytes=data,
        format="png",
    )
    assert v is False


def test_alpha_lost_logic_with_ihdr() -> None:
    """Simulate post-export check: preflight had alpha, file is RGB → ALPHA_LOST."""
    rgb = build_minimal_png(color_type=2)
    assert exp.file_has_alpha_channel(rgb) is False
    preflight_has_alpha = True
    preserve_alpha = True
    verify = True
    lost = verify and preserve_alpha and preflight_has_alpha and not exp.file_has_alpha_channel(rgb)
    assert lost is True
    err = exp.build_export_error(
        code=exp.CODE_ALPHA_LOST,
        error="alpha lost",
        left_on_disk=True,
        png_color_type=exp.png_ihdr_info(rgb)["color_type"],
        preflight_has_alpha=True,
    )
    assert err["code"] == exp.CODE_ALPHA_LOST
    assert err["left_on_disk"] is True


def test_pdb_procedure_map() -> None:
    assert exp.pdb_procedure_for_format("png") == "file-png-export"
    assert exp.pdb_procedure_for_format("jpeg") == "file-jpeg-export"
    assert exp.pdb_procedure_for_format("jpg") == "file-jpeg-export"
    assert exp.pdb_procedure_for_format("webp") == "file-webp-export"
    assert exp.pdb_procedure_for_format("tiff") == "file-tiff-export"
    # No -save names
    for v in exp.PDB_EXPORT.values():
        assert v.endswith("-export")
        assert "-save" not in v


def test_png_pixel_format_candidates_include_documented() -> None:
    assert "format" in exp.PNG_PIXEL_FORMAT_PROP_CANDIDATES
    assert "rgba8" in exp.PNG_RGBA8_VALUE_CANDIDATES


# ---------------------------------------------------------------------------
# Source greps — plugin export path
# ---------------------------------------------------------------------------


def test_plugin_imports_export_module() -> None:
    text = PLUGIN.read_text(encoding="utf-8")
    assert "gimp_mcp_export" in text
    assert "import gimp_mcp_export" in text or "as _exp" in text


def test_export_path_no_file_save_literals() -> None:
    """DoD-6: no file-*-save in export path / helpers."""
    text = PLUGIN.read_text(encoding="utf-8")
    body = _method_body(text, "_export_to_path")
    # Ban GIMP 2 -save procedure names in the export helper
    assert "file-png-save" not in body
    assert "file-jpeg-save" not in body
    assert "file-webp-save" not in body
    assert "file-tiff-save" not in body
    assert re.search(r"file-\w+-save", body) is None, (
        "export path must not contain file-*-save PDB names"
    )
    # Must use -export names
    assert "file-png-export" in body or "PDB_EXPORT" in body or "_exp." in body


def test_export_path_uses_merge_when_preserve_alpha() -> None:
    text = PLUGIN.read_text(encoding="utf-8")
    body = _method_body(text, "_export_to_path")
    assert "merge_visible_layers" in body
    assert "CLIP_TO_IMAGE" in body or "MergeType" in body
    assert "resolve_export_policy" in body or "_exp.resolve_export_policy" in body


def test_export_path_prep_on_duplicate_only() -> None:
    text = PLUGIN.read_text(encoding="utf-8")
    body = _method_body(text, "_export_to_path")
    assert "duplicate()" in body
    # flatten should only appear on dup path, not as bare unconditional
    assert "delete" in body.lower() or ".delete()" in body


def test_export_image_default_flatten_false() -> None:
    text = PLUGIN.read_text(encoding="utf-8")
    body = _method_body(text, "_export_image")
    # default flatten False
    assert 'params.get("flatten", False)' in body or "flatten, False" in body
    # Accept preserve_alpha / verify
    assert "preserve_alpha" in body
    assert "verify" in body
    # file_type alias for raw TCP
    assert "file_type" in body or "format" in body


def test_batch_export_reads_params_not_hardcode_flatten() -> None:
    text = PLUGIN.read_text(encoding="utf-8")
    body = _method_body(text, "_batch_export")
    assert "preserve_alpha" in body
    assert "verify" in body
    # Must not hardcode flatten True as sole export arg like old: _export_to_path(..., True)
    # Allow flatten from params
    assert 'params.get("flatten"' in body
    # Ban the old hardcode pattern of literal True as last positional for flatten-only
    # (old code: self._export_to_path(image, out_path, fmt, quality, True))
    assert not re.search(
        r"_export_to_path\([^)]+,\s*True\s*\)",
        body,
    ), "batch_export must not hardcode flatten=True"


def test_verify_alpha_channel_handler_exists() -> None:
    text = PLUGIN.read_text(encoding="utf-8")
    assert "verify_alpha_channel" in text
    assert "_verify_alpha_channel" in text
    body = _method_body(text, "_verify_alpha_channel")
    assert "has_alpha" in body
    assert "can_preserve_alpha" in body or "format_capability_matrix" in body


def test_server_export_defaults() -> None:
    text = SERVER.read_text(encoding="utf-8")
    body = _function_body(text, "export_image")
    assert "flatten: bool = False" in body
    assert "preserve_alpha" in body
    assert "verify" in body
    # Only format in schema (not dual file_type tool param)
    assert "file_type" not in body


def test_server_batch_export_params() -> None:
    text = SERVER.read_text(encoding="utf-8")
    body = _function_body(text, "batch_export")
    assert "flatten" in body
    assert "preserve_alpha" in body
    assert "verify" in body


def test_server_verify_alpha_tool() -> None:
    text = SERVER.read_text(encoding="utf-8")
    assert "def verify_alpha_channel" in text


def test_export_module_no_pillow_no_gi() -> None:
    text = EXPORT_MOD.read_text(encoding="utf-8")
    assert "from PIL" not in text
    assert "import PIL" not in text
    assert re.search(r"(?m)^import gi\b", text) is None
    assert re.search(r"(?m)^from gi\b", text) is None
    assert "gi.repository" not in text


def test_internal_caller_policy_documented() -> None:
    """Internal callers pass flatten=True → preserve_alpha auto False (no false ALPHA_LOST)."""
    p = exp.resolve_export_policy("png", None, True)
    assert p.preserve_alpha is False
    # Even if file ends up RGB, verify is off so no ALPHA_LOST
    assert p.verify is False
    data = build_minimal_png(color_type=2)
    v = exp.alpha_verified_value(
        preserve_alpha=p.preserve_alpha,
        preflight_has_alpha=True,
        verify=p.verify,
        path_or_bytes=data,
        format="png",
    )
    assert v == "not_applicable"


def test_pyproject_wires_export_module() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "gimp_mcp_export" in text
