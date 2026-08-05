"""Offline structure tests for product documentation (track 0024).

No GIMP process required. Guards public front-door accuracy:
README fork URL, Python floor, license metadata, CHANGELOG, architecture,
operator runbook, CLAUDE.md HL-first posture, and banned stale phrases.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    path = ROOT / rel
    assert path.is_file(), f"missing required product doc: {rel}"
    return path.read_text(encoding="utf-8")


def _has_md_header(text: str) -> bool:
    return bool(re.search(r"(?m)^#{1,2}\s+\S", text))


def test_changelog_exists_with_unreleased() -> None:
    text = _read("CHANGELOG.md")
    assert "[Unreleased]" in text
    # First tagged baseline notes live under dated [0.1.0].
    assert re.search(r"(?m)^##\s+\[0\.1\.0\]\s+-\s+\d{4}-\d{2}-\d{2}\s*$", text), (
        "CHANGELOG must have ## [0.1.0] - YYYY-MM-DD section"
    )
    # Stale packaging-hold preamble must be gone after promote.
    stale_phrases = (
        "until packaging promotes",
        "remains 0.1.0 until first tagged",
        "remains `0.1.0` until the first tagged",
        "stay under **`[Unreleased]`**",
        "stay under `[Unreleased]`",
    )
    lower = text.lower()
    for phrase in stale_phrases:
        assert phrase.lower() not in lower, (
            f"stale CHANGELOG preamble phrase still present: {phrase}"
        )


def test_architecture_and_runbook_exist_with_headers() -> None:
    for rel in ("docs/architecture.md", "docs/operator-runbook.md"):
        text = _read(rel)
        assert text.strip(), f"{rel} must be non-empty"
        assert _has_md_header(text), f"{rel} must contain at least one # or ## header"


def test_readme_fork_url() -> None:
    text = _read("README.md")
    assert "Ryan-AI-Studios/gimp-mcp" in text


def test_readme_python_not_38_minimum() -> None:
    text = _read("README.md")
    # Product requires-python >=3.11; ban stale "Python 3.8" as minimum claims.
    stale = re.compile(
        r"(?i)python\s*3\.8\+|"
        r"python\s*>=?\s*3\.8(?!\d)|"
        r"python\s*3\.8\s*(or|/|\+|and)",
    )
    assert not stale.search(text), "README must not claim Python 3.8 as minimum"


def test_readme_not_56_of_56_product_sot() -> None:
    text = _read("README.md")
    assert "56/56 PASSED" not in text
    assert "56/56" not in text


def test_readme_snapshot_default_max_edge() -> None:
    text = _read("README.md")
    # Ban only the stale *default* phrasing; opt-in max_size=512 remains allowed.
    assert "default max edge 512" not in text.lower()
    has_1024 = "1024" in text
    has_perf_sot = "performance.md" in text.lower() or "docs/performance.md" in text
    assert has_1024 or has_perf_sot, (
        "README must state product default max edge 1024 or link performance.md as default SoT"
    )


def test_pyproject_license_gpl_not_mit() -> None:
    text = _read("pyproject.toml")
    assert 'license = "MIT"' not in text
    assert re.search(r'license\s*=\s*"(GPL-3\.0-only|GPL-3\.0-or-later)"', text), (
        'pyproject license must be "GPL-3.0-only" (or GPL-3.0-or-later)'
    )


def test_claude_md_not_call_api_main_interface() -> None:
    text = _read("CLAUDE.md")
    assert "The main interface is the call_api tool" not in text


def test_readme_future_enhancements_no_shipped_bullets() -> None:
    text = _read("README.md")
    # Section-scoped: only if ## Future Enhancements remains, ban shipped claims.
    match = re.search(
        r"(?ms)^##\s+Future Enhancements\s*\n(.*?)(?=^##\s|\Z)",
        text,
    )
    if match is None:
        return
    section = match.group(1)
    for phrase in ("Recipe Collection", "Undo System"):
        assert phrase not in section, (
            f"Future Enhancements must not list shipped '{phrase}' as unimplemented"
        )


def test_run_tests_py_retained() -> None:
    """Product policy: demote in README, do not delete the live harness."""
    assert (ROOT / "run_tests.py").is_file()


def test_security_tmp_and_timeout_residuals() -> None:
    """SECURITY: workspace .gimp-mcp-tmp primary; distinct TIMEOUT code."""
    text = _read("SECURITY.md")
    assert ".gimp-mcp-tmp" in text
    assert "TIMEOUT" in text
    # Must not present system temp as the primary residual path.
    assert "system temp (not workspace)" not in text.lower()


def test_operator_runbook_start_order_gfm_anchor() -> None:
    """GFM does not honor Pandoc {#id}; keep auto-slug or HTML id."""
    text = _read("docs/operator-runbook.md")
    assert re.search(r"(?m)^##\s+Start order\s*\{#start-order\}", text) is None
    plain = re.search(r"(?m)^##\s+Start order\s*$", text)
    html_id = 'id="start-order"' in text
    assert plain or html_id, (
        'operator-runbook must use ## Start order (GFM slug) or id="start-order"'
    )


def test_architecture_no_track_ids() -> None:
    """Product architecture doc must not cite conductor track IDs 0000-0029."""
    text = _read("docs/architecture.md")
    match = re.search(r"\b00[0-2][0-9]\b", text)
    assert match is None, f"architecture.md must not contain track id {match.group(0)!r}"
