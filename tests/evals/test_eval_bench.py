"""Optional @slow micro-bench: compare_images on 16x16 fixtures (L6)."""

from __future__ import annotations

import time

import pytest

from gimp_mcp_verify import compare_images
from tests.fixture_paths import fixture_path

EVAL_OPAQUE = "eval/rgb_16x16_opaque.png"
EVAL_DELTA = "eval/rgb_16x16_delta_corner.png"


@pytest.mark.slow
def test_compare_images_microbench_16x16() -> None:
    """N iterations of compare_images on 16x16; finishes without error / <5s for 1000."""
    a = fixture_path(EVAL_OPAQUE)
    b = fixture_path(EVAL_DELTA)
    assert a.is_file() and b.is_file()
    n = 1000
    t0 = time.perf_counter()
    for _ in range(n):
        result = compare_images(str(a), str(b))
        # Non-status: metrics prove pixel difference
        mae = result.get("mae")
        changed = result.get("changed_pixels")
        assert (mae is not None and mae > 0) or (changed is not None and changed > 0)
    elapsed = time.perf_counter() - t0
    assert elapsed < 5.0, f"1000 compare_images iters took {elapsed:.2f}s (bound 5s)"
