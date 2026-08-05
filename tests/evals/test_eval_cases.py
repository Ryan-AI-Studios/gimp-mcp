"""Thin offline/structure wrappers for eval catalog cases.

Non-status asserts only (H3): codes, procedure names, recipe ids, path jail ok.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import gimp_mcp_recipes as recipes
import gimp_mcp_security as sec
from gimp_agent import batch as batch_mod


def _valid_job_n_steps(n: int, **overrides: Any) -> dict[str, Any]:
    """Synthetic batch job with n GIMP-safe open_image steps (+ final export)."""
    if n < 1:
        raise ValueError("n must be >= 1")
    steps: list[dict[str, Any]] = [
        {"op": "open_image", "with": {"file_path": f"C:/ws/in_{i}.png"}} for i in range(n - 1)
    ]
    steps.append(
        {
            "op": "export_image",
            "with": {
                "file_path": "C:/ws/out.png",
                "format": "png",
                "preserve_alpha": True,
                "collision": "fail",
                "verify": True,
            },
        }
    )
    if n == 1:
        steps = [
            {
                "op": "export_image",
                "with": {
                    "file_path": "C:/ws/out.png",
                    "format": "png",
                    "preserve_alpha": True,
                    "collision": "fail",
                    "verify": True,
                },
            }
        ]
    job: dict[str, Any] = {
        "v": 1,
        "recipe_id": "web-export",
        "steps": steps,
    }
    job.update(overrides)
    return job


def test_batch_small_validate_job_five_steps() -> None:
    """E-BATCH-SMALL: validate_job accepts synthetic job with ≤5 GIMP steps."""
    job = _valid_job_n_steps(5)
    assert len(job["steps"]) == 5
    out = batch_mod.validate_job(job)
    assert out["v"] == 1
    assert len(out["steps"]) == 5
    # Non-status: every step has an op (structure of validated job)
    ops = [s.get("op") for s in out["steps"]]
    assert all(isinstance(op, str) and op for op in ops)


def test_batch_procedure_name_and_interpreter() -> None:
    """E-BATCH-SMALL: product procedure name + --batch-interpreter flag (not host run)."""
    assert batch_mod.PROCEDURE_NAME == "plug-in-gimp-mcp-batch"
    argv = batch_mod.build_console_argv(
        Path("C:/Program Files/GIMP 3/bin/gimp-console-3.2.exe"),
        batch_mod.build_run_job_payload("C:/ws/.gimp-mcp-tmp/job.json"),
    )
    assert "--batch-interpreter" in argv
    idx = argv.index("--batch-interpreter")
    assert argv[idx + 1] == batch_mod.PROCEDURE_NAME


def test_recipe_catalog_transparent_png_and_web_export() -> None:
    """E-RECIPE-CATALOG: package recipes include transparent-png and web-export."""
    reg = recipes.load_package_recipes()
    ids = set(reg.ids())
    assert "transparent-png" in ids
    assert "web-export" in ids
    # Non-status: recipes have non-empty steps
    for rid in ("transparent-png", "web-export"):
        recipe = reg.get(rid)
        assert recipe["id"] == rid
        assert len(recipe["steps"]) >= 1


def test_filename_spaces_under_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CGPT-15: paths with spaces ok under workspace; escape denied."""
    monkeypatch.setenv(sec.ENV_WORKSPACE, str(tmp_path))
    spaced = tmp_path / "my file.png"
    spaced.write_bytes(b"x")
    resolved = sec.resolve_under_root(str(spaced))
    assert resolved.name == "my file.png"
    with pytest.raises(sec.SecurityError) as ei:
        sec.resolve_under_root(str(tmp_path / ".." / "escape.png"))
    assert ei.value.code == sec.CODE_PATH_DENIED


def test_unicode_filename_under_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CGPT-16: unicode filenames under workspace jail."""
    monkeypatch.setenv(sec.ENV_WORKSPACE, str(tmp_path))
    uni = tmp_path / "写真_test.png"
    uni.write_bytes(b"x")
    resolved = sec.resolve_under_root(str(uni))
    assert "写真" in resolved.name
