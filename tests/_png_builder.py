"""Shared stdlib PNG builders for offline tests (no Pillow, no GIMP).

Extracted from test_export_alpha for reuse by verify / recipes / CLI / fixtures.
"""

from __future__ import annotations

import struct
import zlib

import gimp_mcp_snapshot as snap


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
