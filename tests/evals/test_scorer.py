"""Unit tests for pure scorer — synthetic pass/fail maps only (no pytest subprocess)."""

from __future__ import annotations

import json
from pathlib import Path

from tests.evals.loader import Case
from tests.evals.scorer import parse_junit_xml, score


def _case(
    id_: str,
    *,
    category: str = "security",
    weight: int = 15,
    mode: str = "offline",
    gate: str = "release",
    nodeids: list[str] | None = None,
    design_ref: str = "t",
) -> Case:
    return Case(
        id=id_,
        design_ref=design_ref,
        category=category,
        weight_category=weight,
        mode=mode,  # type: ignore[arg-type]
        gate=gate,  # type: ignore[arg-type]
        test_nodeids=tuple(nodeids or []),
        notes="",
    )


def test_case_passes_when_all_nodeids_present() -> None:
    cases = [
        _case("A", nodeids=["t::a", "t::b"]),
        _case("B", nodeids=["t::c"], category="determinism", weight=8),
    ]
    report = score(cases, {"t::a", "t::b", "t::c"})
    assert report.overall == "PASS"
    assert all(g.passed for g in report.release_gates)
    assert report.offline_pass_rate == 1.0


def test_case_fails_if_any_nodeid_missing() -> None:
    cases = [_case("A", nodeids=["t::a", "t::b"])]
    report = score(cases, {"t::a"})
    assert report.overall == "FAIL"
    assert report.release_gates[0].passed is False


def test_equal_case_pass_rate_within_category() -> None:
    cases = [
        _case("S1", nodeids=["t::1"], category="security", weight=15),
        _case("S2", nodeids=["t::2"], category="security", weight=15),
        _case("S3", nodeids=["t::3"], category="security", weight=15),
    ]
    # 2 of 3 pass
    report = score(cases, {"t::1", "t::3"})
    sec = next(c for c in report.category_scores if c.category == "security")
    assert sec.passed == 2
    assert sec.total == 3
    assert abs(sec.case_pass_rate - (2 / 3)) < 1e-9
    assert abs(report.offline_pass_rate - (2 / 3)) < 1e-9


def test_residual_modes_not_in_offline_aggregate() -> None:
    cases = [
        _case("OK", nodeids=["t::a"], category="security", weight=15),
        _case(
            "LIVE",
            mode="live_residual",
            gate="informational",
            nodeids=[],
            category="visual_quality",
            weight=7,
        ),
        _case(
            "OOS",
            mode="oos_agent",
            gate="informational",
            nodeids=[],
            category="visual_quality",
            weight=7,
        ),
    ]
    report = score(cases, {"t::a"})
    assert report.overall == "PASS"
    cats = {c.category for c in report.category_scores}
    assert "visual_quality" not in cats  # omitted: zero offline/structure
    assert any("LIVE" in r for r in report.residuals)
    assert any("OOS" in r for r in report.residuals)


def test_weighted_aggregate_across_categories() -> None:
    # security weight 15 all pass; determinism weight 8 all fail
    cases = [
        _case("S", nodeids=["t::s"], category="security", weight=15),
        _case("D", nodeids=["t::d"], category="determinism", weight=8),
    ]
    report = score(cases, {"t::s"})
    expected = (15 * 1.0 + 8 * 0.0) / (15 + 8)
    assert abs(report.offline_pass_rate - expected) < 1e-9
    assert report.overall == "FAIL"  # D is release and failed


def test_informational_fail_does_not_fail_overall_if_no_release_fail() -> None:
    cases = [
        _case("R", nodeids=["t::r"], gate="release"),
        _case(
            "I",
            nodeids=["t::i"],
            gate="informational",
            category="speed",
            weight=4,
        ),
    ]
    report = score(cases, {"t::r"})  # I missing
    assert report.overall == "PASS"
    assert any(not c["passed"] for c in report.case_results if c["case_id"] == "I")


def test_parse_junit_xml_passed_only(tmp_path: Path) -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
    <testsuite tests="3" failures="1" errors="0" skipped="1">
      <testcase classname="tests.test_verify" name="test_identical_mae_zero" time="0.01"/>
      <testcase classname="tests.test_verify" name="test_one_pixel_change" time="0.01">
        <failure message="fail">boom</failure>
      </testcase>
      <testcase classname="tests.test_handles" name="test_require_image_handle_stale_gen" time="0.0">
        <skipped message="skip"/>
      </testcase>
    </testsuite>
    """
    path = tmp_path / "junit.xml"
    path.write_text(xml, encoding="utf-8")
    passed = parse_junit_xml(path)
    assert "tests/test_verify.py::test_identical_mae_zero" in passed
    assert "tests/test_verify.py::test_one_pixel_change" not in passed
    assert not any("stale" in p for p in passed)


def test_report_to_dict_schema_keys() -> None:
    cases = [_case("A", nodeids=["t::a"])]
    report = score(cases, {"t::a"})
    d = report.to_dict()
    assert d["schema_version"] == 1
    assert "generated_at" in d
    assert "offline_pass_rate" in d
    assert "category_scores" in d
    assert "release_gates" in d
    assert "residuals" in d
    assert d["overall"] in ("PASS", "FAIL")
    # JSON round-trip
    json.dumps(d)
