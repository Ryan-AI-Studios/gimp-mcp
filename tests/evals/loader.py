"""Load and validate the evaluation case catalog (cases.json)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

Mode = Literal["offline", "structure", "live_residual", "oos_agent"]
Gate = Literal["release", "informational"]

VALID_MODES: Final[frozenset[str]] = frozenset(
    {"offline", "structure", "live_residual", "oos_agent"}
)
VALID_GATES: Final[frozenset[str]] = frozenset({"release", "informational"})

# Category weights must sum to 100 (product rubric).
CATEGORY_WEIGHTS: Final[dict[str, int]] = {
    "pixel_metadata": 25,
    "layer_preservation": 15,
    "orientation_coords": 10,
    "recoverability": 10,
    "security": 15,
    "determinism": 8,
    "visual_quality": 7,
    "speed": 4,
    "portability": 3,
    "tool_token_efficiency": 3,
}

CASES_PATH: Final[Path] = Path(__file__).resolve().parent / "cases.json"


@dataclass(frozen=True, slots=True)
class Case:
    """One evaluation catalog row."""

    id: str
    design_ref: str
    category: str
    weight_category: int
    mode: Mode
    gate: Gate
    test_nodeids: tuple[str, ...]
    notes: str


class CatalogError(ValueError):
    """Raised when cases.json fails schema / invariant checks."""


def _as_mode(raw: object, case_id: str) -> Mode:
    if not isinstance(raw, str) or raw not in VALID_MODES:
        raise CatalogError(f"{case_id}: unknown or missing mode {raw!r}")
    return raw  # type: ignore[return-value]


def _as_gate(raw: object, case_id: str) -> Gate:
    if not isinstance(raw, str) or raw not in VALID_GATES:
        raise CatalogError(f"{case_id}: unknown or missing gate {raw!r}")
    return raw  # type: ignore[return-value]


def _parse_case(raw: dict[str, Any]) -> Case:
    case_id = raw.get("id")
    if not isinstance(case_id, str) or not case_id.strip():
        raise CatalogError(f"case missing string id: {raw!r}")

    design_ref = raw.get("design_ref")
    if not isinstance(design_ref, str):
        raise CatalogError(f"{case_id}: design_ref must be a string")

    category = raw.get("category")
    if not isinstance(category, str) or category not in CATEGORY_WEIGHTS:
        raise CatalogError(f"{case_id}: unknown category {category!r}")

    weight = raw.get("weight_category")
    if not isinstance(weight, int) or isinstance(weight, bool):
        raise CatalogError(f"{case_id}: weight_category must be int")
    expected = CATEGORY_WEIGHTS[category]
    if weight != expected:
        raise CatalogError(
            f"{case_id}: weight_category {weight} != category weight {expected} for {category}"
        )

    mode = _as_mode(raw.get("mode"), case_id)
    gate = _as_gate(raw.get("gate"), case_id)

    nodeids_raw = raw.get("test_nodeids")
    if not isinstance(nodeids_raw, list) or not all(isinstance(x, str) for x in nodeids_raw):
        raise CatalogError(f"{case_id}: test_nodeids must be a list of strings")
    nodeids = tuple(nodeids_raw)

    notes = raw.get("notes", "")
    if not isinstance(notes, str):
        raise CatalogError(f"{case_id}: notes must be a string")

    if gate == "release" and mode in ("offline", "structure") and not nodeids:
        raise CatalogError(
            f"{case_id}: gate=release with mode={mode} requires non-empty test_nodeids"
        )

    return Case(
        id=case_id,
        design_ref=design_ref,
        category=category,
        weight_category=weight,
        mode=mode,
        gate=gate,
        test_nodeids=nodeids,
        notes=notes,
    )


def load_cases(path: Path | None = None) -> list[Case]:
    """Load and validate cases.json; return list of Case."""
    cases_path = path if path is not None else CASES_PATH
    try:
        data = json.loads(cases_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CatalogError(f"cannot read catalog: {cases_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogError(f"invalid JSON in {cases_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise CatalogError("cases.json root must be an object")
    raw_cases = data.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise CatalogError("cases.json must contain a non-empty 'cases' array")

    cases: list[Case] = []
    seen: set[str] = set()
    for item in raw_cases:
        if not isinstance(item, dict):
            raise CatalogError(f"case entry must be object, got {type(item).__name__}")
        case = _parse_case(item)
        if case.id in seen:
            raise CatalogError(f"duplicate case id: {case.id}")
        seen.add(case.id)
        cases.append(case)

    weight_sum = sum(CATEGORY_WEIGHTS.values())
    if weight_sum != 100:
        raise CatalogError(f"CATEGORY_WEIGHTS sum to {weight_sum}, expected 100")

    return cases


def offline_structure_cases(cases: list[Case]) -> list[Case]:
    """Cases that participate in offline aggregate scoring."""
    return [c for c in cases if c.mode in ("offline", "structure")]


def release_gate_cases(cases: list[Case]) -> list[Case]:
    """Cases with gate=release (must pass for overall PASS)."""
    return [c for c in cases if c.gate == "release"]
