"""Pure rubric scorer: maps cases + passed nodeids → RubricReport.

Scorer core never imports pytest and never depends on JUnit schema.
Optional JUnit adapter lives in parse_junit_xml only.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from tests.evals.loader import CATEGORY_WEIGHTS, Case, offline_structure_cases, release_gate_cases

Overall = Literal["PASS", "FAIL"]


@dataclass(frozen=True, slots=True)
class CategoryScore:
    category: str
    weight: int
    passed: int
    total: int
    case_pass_rate: float


@dataclass(frozen=True, slots=True)
class ReleaseGateResult:
    case_id: str
    passed: bool


@dataclass(frozen=True, slots=True)
class RubricReport:
    schema_version: int
    generated_at: str
    offline_pass_rate: float
    category_scores: list[CategoryScore]
    release_gates: list[ReleaseGateResult]
    residuals: list[str]
    overall: Overall
    case_results: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable dict (schema_version 1)."""
        d = asdict(self)
        return d


def case_passed(case: Case, passed_nodeids: set[str]) -> bool | None:
    """Return True/False for offline|structure; None for residual modes (N/A).

    Empty nodeids for live_residual/oos_agent → N/A (None).
    Empty nodeids for offline/structure → fail (should be rejected by loader for release).
    """
    if case.mode in ("live_residual", "oos_agent"):
        return None
    if not case.test_nodeids:
        return False
    return all(nid in passed_nodeids for nid in case.test_nodeids)


def score(cases: list[Case], passed_nodeids: set[str]) -> RubricReport:
    """Compute rubric from cases and the set of passed pytest nodeids.

    - Case passes iff ALL its test_nodeids ⊆ passed_nodeids.
    - Category score = equal-case pass rate among offline/structure cases in category.
    - Categories with zero offline/structure cases are omitted from the weighted
      denominator (documented choice: omit so overall is never NaN/div0).
    - overall PASS only if every gate:release case passes (release cases that are
      live_residual/oos_agent with empty nodeids are treated as not-passed for
      honesty — catalog should not mark residual as release without nodeids).
    """
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()

    scored = offline_structure_cases(cases)
    # Per-category equal-case rates
    by_cat: dict[str, list[bool]] = {k: [] for k in CATEGORY_WEIGHTS}
    case_results: list[dict[str, Any]] = []
    for case in scored:
        ok = case_passed(case, passed_nodeids)
        assert ok is not None  # offline/structure always bool
        by_cat[case.category].append(ok)
        case_results.append(
            {
                "case_id": case.id,
                "mode": case.mode,
                "category": case.category,
                "passed": ok,
                "nodeids": list(case.test_nodeids),
            }
        )

    category_scores: list[CategoryScore] = []
    weighted_sum = 0.0
    weight_denom = 0
    for cat, weight in CATEGORY_WEIGHTS.items():
        results = by_cat[cat]
        total = len(results)
        if total == 0:
            # Omit from denominator (no offline/structure cases in this category).
            continue
        passed = sum(1 for r in results if r)
        rate = passed / total
        category_scores.append(
            CategoryScore(
                category=cat,
                weight=weight,
                passed=passed,
                total=total,
                case_pass_rate=rate,
            )
        )
        weighted_sum += weight * rate
        weight_denom += weight

    offline_pass_rate = (weighted_sum / weight_denom) if weight_denom else 1.0

    release_results: list[ReleaseGateResult] = []
    all_release_ok = True
    for case in release_gate_cases(cases):
        ok = case_passed(case, passed_nodeids)
        # Residual release (should not happen): treat as fail for honesty.
        passed_flag = bool(ok) if ok is not None else False
        if not passed_flag:
            all_release_ok = False
        release_results.append(ReleaseGateResult(case_id=case.id, passed=passed_flag))

    residuals = [
        f"{c.id} ({c.design_ref}; {c.mode})"
        for c in cases
        if c.mode in ("live_residual", "oos_agent")
    ]

    overall: Overall = "PASS" if all_release_ok else "FAIL"
    return RubricReport(
        schema_version=1,
        generated_at=generated_at,
        offline_pass_rate=offline_pass_rate,
        category_scores=category_scores,
        release_gates=release_results,
        residuals=residuals,
        overall=overall,
        case_results=case_results,
    )


def parse_junit_xml(path: Path | str) -> set[str]:
    """Optional adapter: extract passed pytest nodeids from a JUnit XML file.

    Pytest junit nodeids appear as classname::name (file path as classname with
    dots, or path-like depending on pytest version). We reconstruct:
      ``{classname}::{name}`` when classname does not already contain '::'.
    Also accepts full nodeid in the ``name`` attribute alone.
    """
    tree = ET.parse(Path(path))
    root = tree.getroot()
    passed: set[str] = set()
    for case in root.iter("testcase"):
        # Skip failures / errors / skipped
        if case.find("failure") is not None:
            continue
        if case.find("error") is not None:
            continue
        if case.find("skipped") is not None:
            continue
        name = case.get("name") or ""
        classname = case.get("classname") or ""
        if "::" in name:
            nodeid = name
        elif classname:
            # pytest often uses dots for path: tests.test_foo → tests/test_foo
            # Prefer leaving classname as-is if it already looks like a path.
            if "/" in classname or "\\" in classname:
                nodeid = f"{classname}::{name}"
            else:
                # Convert package-style classname to path-style for matching catalog
                path_cls = classname.replace(".", "/")
                if not path_cls.endswith(name.split("[", 1)[0]):
                    nodeid = f"{path_cls}.py::{name}"
                else:
                    # classname already includes module file stem as last segment
                    # e.g. tests.test_verify + test_foo → tests/test_verify.py::test_foo
                    nodeid = f"{path_cls}.py::{name}"
        else:
            nodeid = name
        if nodeid:
            passed.add(nodeid)
    return passed
