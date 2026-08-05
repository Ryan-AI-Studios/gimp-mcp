"""Docs structure tests for evaluation.md and cross-links (M5)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    path = ROOT / rel
    assert path.is_file(), f"missing required product doc: {rel}"
    return path.read_text(encoding="utf-8")


def _has_md_header(text: str) -> bool:
    return bool(re.search(r"(?m)^#{1,2}\s+\S", text))


def test_evaluation_md_exists_with_header() -> None:
    text = _read("docs/evaluation.md")
    assert text.strip()
    assert _has_md_header(text)


def test_evaluation_md_has_release_gates_heading() -> None:
    """GFM-safe ## Release gates → #release-gates (no pandoc {#id})."""
    text = _read("docs/evaluation.md")
    assert re.search(r"(?m)^##\s+Release gates\s*$", text)
    assert re.search(r"(?m)^##\s+Release gates\s*\{#", text) is None


def test_evaluation_md_mentions_run_tests_for_live() -> None:
    text = _read("docs/evaluation.md")
    assert "run_tests.py" in text


def test_readme_links_evaluation_md() -> None:
    text = _read("README.md")
    assert "evaluation.md" in text or "docs/evaluation.md" in text


def test_ci_and_testing_links_evaluation_md() -> None:
    text = _read("docs/ci-and-testing.md")
    assert "evaluation.md" in text
