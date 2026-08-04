"""Host-only offline golden-path E2E (track 0022).

No gi / GIMP process. Not marked @slow or @integration.
"""

from __future__ import annotations

from pathlib import Path

import gimp_mcp_recipes as recipes
import gimp_mcp_verify as verify
from tests.fixture_paths import copy_fixture_to_workspace

OPAQUE = "rgb_2x2_opaque.png"
ALPHA = "rgba_2x2_alpha.png"
DELTA = "rgb_2x2_delta.png"


def test_offline_golden_path_verify_compare_recipes(tmp_workspace: Path) -> None:
    opaque = copy_fixture_to_workspace(OPAQUE, tmp_workspace)
    alpha = copy_fixture_to_workspace(ALPHA, tmp_workspace)
    delta = copy_fixture_to_workspace(DELTA, tmp_workspace)
    opaque_copy = tmp_workspace / "opaque_copy.png"
    opaque_copy.write_bytes(opaque.read_bytes())

    # 3. verify_artifact baselines (metric-style expected)
    base = verify.verify_artifact(
        opaque,
        {"format": "png", "width": 2, "height": 2},
    )
    assert base["ok"] is True
    assert base["pass"] is True
    assert base["width"] == 2
    assert base["height"] == 2
    assert base["has_alpha"] is False
    assert base["detected_format"] == "png"

    alpha_v = verify.verify_artifact(
        alpha,
        {"format": "png", "width": 2, "height": 2, "require_alpha": True},
    )
    assert alpha_v["ok"] is True
    assert alpha_v["pass"] is True
    assert alpha_v["has_alpha"] is True
    assert alpha_v["width"] == 2
    assert alpha_v["height"] == 2

    # 4. identical copies → pass with MAE≈0 / changed_pixels==0
    same = verify.compare_images(
        opaque,
        opaque_copy,
        thresholds={"max_mae": 0, "require_same_size": True},
    )
    assert same["ok"] is True
    assert same["pass"] is True
    assert float(same["mae"]) == 0.0
    assert int(same["changed_pixels"]) == 0
    assert same["failures"] == []

    # 5. opaque vs delta → non-zero metrics; strict max_mae=0 fails gate
    diff = verify.compare_images(
        opaque,
        delta,
        thresholds={"max_mae": 0, "require_same_size": True},
    )
    assert diff["ok"] is True
    assert float(diff["mae"]) > 0.0
    assert int(diff["changed_pixels"]) > 0
    assert diff["pass"] is False
    assert any("mae" in str(f).lower() for f in diff["failures"])

    # 6. recipe catalog includes concrete shipped ids
    ids = {r["id"] for r in recipes.list_recipes()}
    assert "transparent-png" in ids
    assert "web-export" in ids
