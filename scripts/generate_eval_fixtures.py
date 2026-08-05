#!/usr/bin/env python3
"""Generate synthetic 16x16 eval PNG fixtures under tests/fixtures/eval/.

Deterministic stdlib PNGs (no Pillow / GIMP):

    uv run python scripts/generate_eval_fixtures.py

Binaries in git remain CI SoT; re-run after intentional pixel layout changes
and update hashes in tests/fixtures/README.md.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests._png_builder import build_minimal_png  # noqa: E402

EVAL_DIR = ROOT / "tests" / "fixtures" / "eval"
WIDTH = 16
HEIGHT = 16


def _rgb_row(r: int, g: int, b: int, width: int = WIDTH) -> bytes:
    return b"\x00" + bytes((r, g, b)) * width


def _rgba_pixel(r: int, g: int, b: int, a: int) -> bytes:
    return bytes((r, g, b, a))


def main() -> int:
    EVAL_DIR.mkdir(parents=True, exist_ok=True)

    # rgb_16x16_opaque.png — solid white RGB
    opaque = build_minimal_png(width=WIDTH, height=HEIGHT, color_type=2)

    # rgba_16x16_alpha.png — checker of full and half alpha on white/red
    alpha_rows: list[bytes] = []
    for y in range(HEIGHT):
        row = b"\x00"
        for x in range(WIDTH):
            if (x + y) % 2 == 0:
                row += _rgba_pixel(255, 255, 255, 255)
            else:
                row += _rgba_pixel(255, 0, 0, 128)
        alpha_rows.append(row)
    alpha = build_minimal_png(width=WIDTH, height=HEIGHT, color_type=6, pixels=b"".join(alpha_rows))

    # rgb_16x16_delta_corner.png — white except corner pixel red
    delta_rows: list[bytes] = []
    for y in range(HEIGHT):
        row = b"\x00"
        for x in range(WIDTH):
            if x == 0 and y == 0:
                row += bytes((255, 0, 0))
            else:
                row += bytes((255, 255, 255))
        delta_rows.append(row)
    delta = build_minimal_png(width=WIDTH, height=HEIGHT, color_type=2, pixels=b"".join(delta_rows))

    files = {
        "rgb_16x16_opaque.png": opaque,
        "rgba_16x16_alpha.png": alpha,
        "rgb_16x16_delta_corner.png": delta,
    }
    for name, data in files.items():
        path = EVAL_DIR / name
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        print(f"{name}  {len(data)} bytes  sha256={digest}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
