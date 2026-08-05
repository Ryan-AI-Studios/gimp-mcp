"""Unit tests for eval catalog loader (no pytest subprocess)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.evals.loader import (
    CATEGORY_WEIGHTS,
    CatalogError,
    load_cases,
    offline_structure_cases,
)


def test_category_weights_sum_100() -> None:
    assert sum(CATEGORY_WEIGHTS.values()) == 100


def test_load_shipped_catalog() -> None:
    cases = load_cases()
    assert len(cases) >= 30
    ids = {c.id for c in cases}
    for required in (
        "E-SEC-PATH-TRAVERSAL",
        "E-SEC-EXEC",
        "E-SEC-BIND",
        "E-HANDLE-STALE",
        "E-PIXEL-DELTA",
        "E-PIXEL-IDENTICAL",
        "E-ALPHA-PNG",
        "E-VERIFY-BUDGET",
        "E-SNAPSHOT-BUDGET",
        "E-COORDS-NORMALIZE",
        "E-COMPOSITE-STRUCTURE",
        "E-EXPORT-ALPHA-STRUCTURE",
        "E-RECIPE-CATALOG",
        "E-OFFLINE-GOLDEN",
        "E-BATCH-SMALL",
    ):
        assert required in ids, f"missing core case {required}"
    # All 30 CGPT design refs present (via design_ref substrings or full)
    design_blob = " ".join(c.design_ref for c in cases)
    for n in range(1, 31):
        # CGPT-01 style or CGPT-1 or multi like CGPT-05-06 / CGPT-13-14
        token = f"CGPT-{n:02d}" if n >= 10 else f"CGPT-0{n}"
        alt = f"CGPT-{n}"
        # multi-range: 5-6, 13-14
        covered = (
            token in design_blob
            or alt in design_blob
            or (n in (5, 6) and "CGPT-05-06" in design_blob)
            or (n in (13, 14) and "CGPT-13-14" in design_blob)
        )
        assert covered, f"CGPT row {n} not covered in design_ref set: {design_blob}"


def test_reject_unknown_mode(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "E-BAD",
                        "design_ref": "x",
                        "category": "security",
                        "weight_category": 15,
                        "mode": "host_magic",
                        "gate": "informational",
                        "test_nodeids": [],
                        "notes": "",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CatalogError, match="unknown or missing mode"):
        load_cases(path)


def test_release_offline_requires_nodeids(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "E-EMPTY",
                        "design_ref": "x",
                        "category": "security",
                        "weight_category": 15,
                        "mode": "offline",
                        "gate": "release",
                        "test_nodeids": [],
                        "notes": "",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CatalogError, match="non-empty test_nodeids"):
        load_cases(path)


def test_release_structure_requires_nodeids(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "E-EMPTY-S",
                        "design_ref": "x",
                        "category": "layer_preservation",
                        "weight_category": 15,
                        "mode": "structure",
                        "gate": "release",
                        "test_nodeids": [],
                        "notes": "",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CatalogError, match="non-empty test_nodeids"):
        load_cases(path)


def test_weight_category_must_match(tmp_path: Path) -> None:
    path = tmp_path / "cases.json"
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "E-W",
                        "design_ref": "x",
                        "category": "security",
                        "weight_category": 99,
                        "mode": "live_residual",
                        "gate": "informational",
                        "test_nodeids": [],
                        "notes": "",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CatalogError, match="weight_category"):
        load_cases(path)


def test_offline_structure_filter() -> None:
    cases = load_cases()
    os_cases = offline_structure_cases(cases)
    assert all(c.mode in ("offline", "structure") for c in os_cases)
    assert len(os_cases) < len(cases)
