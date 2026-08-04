#!/usr/bin/env python3
"""Generate the minimum committed PNG fixture set under tests/fixtures/.

Deterministic stdlib PNGs (no Pillow / GIMP). Re-run after changing pixel layout:

    uv run python scripts/generate_test_fixtures.py

0025 owns corpus growth; this script ships the 0022 min-set only.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

# Allow running as ``uv run python scripts/generate_test_fixtures.py`` from repo root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests._png_builder import build_minimal_png  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def _rgb_row(r: int, g: int, b: int, width: int = 2) -> bytes:
    return b"\x00" + bytes((r, g, b)) * width


def _rgba_pixel(r: int, g: int, b: int, a: int) -> bytes:
    return bytes((r, g, b, a))


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)

    # rgb_2x2_opaque.png — color_type 2, solid white
    opaque = build_minimal_png(width=2, height=2, color_type=2)

    # rgba_2x2_alpha.png — color_type 6, non-full alpha on some pixels
    # row0: white opaque + red half-alpha; row1: green transparent + blue mostly-opaque
    alpha_raw = (
        b"\x00"
        + _rgba_pixel(255, 255, 255, 255)
        + _rgba_pixel(255, 0, 0, 128)
        + b"\x00"
        + _rgba_pixel(0, 255, 0, 0)
        + _rgba_pixel(0, 0, 255, 192)
    )
    alpha = build_minimal_png(width=2, height=2, color_type=6, pixels=alpha_raw)

    # rgb_2x2_delta.png — color_type 2, solid red (differs from opaque white)
    delta_raw = _rgb_row(255, 0, 0) + _rgb_row(255, 0, 0)
    delta = build_minimal_png(width=2, height=2, color_type=2, pixels=delta_raw)

    files = {
        "rgb_2x2_opaque.png": opaque,
        "rgba_2x2_alpha.png": alpha,
        "rgb_2x2_delta.png": delta,
    }
    for name, data in files.items():
        path = FIXTURES / name
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        print(f"{name}  {len(data)} bytes  sha256={digest}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
