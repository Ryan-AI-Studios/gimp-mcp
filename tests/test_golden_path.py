"""Offline structure tests for golden-path demo + smoke (track 0027).

No GIMP process required. Guards docs anchors, wire-name honesty, Class A bans,
cross-links, and the optional @integration live consumer marker (M3).
Also covers dry-run fail-closed contracts (M1/M2/L5) and optional L4 mapping.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DOC = ROOT / "docs" / "golden-path.md"
SMOKE_SCRIPT = ROOT / "scripts" / "golden_path_smoke.py"
LIVE_TEST_CANDIDATES = (
    ROOT / "tests" / "test_golden_path.py",
    ROOT / "tests" / "test_golden_path_live.py",
)


def _load_smoke_module() -> Any:
    """Import scripts/golden_path_smoke.py as a module for pure helper unit tests."""
    spec = importlib.util.spec_from_file_location("golden_path_smoke_under_test", SMOKE_SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


REQUIRED_WIRES = (
    "open_image",
    "orient_workspace",
    "get_image_bitmap",
    "save_xcf",
    "export",
)

# Forbid sending HL-only names as plugin wire types
_CREATE_SELECTION_WIRE = re.compile(
    r"""send_authenticated_command\s*\(\s*["']create_selection["']"""
)
_RENDER_COMPOSITE_WIRE = re.compile(
    r"""send_authenticated_command\s*\(\s*["']render_visible_composite["']"""
)
_CMDS_PAYLOAD = re.compile(r"""["']cmds["']\s*:""")


def _read(rel: str) -> str:
    path = ROOT / rel
    assert path.is_file(), f"missing required product file: {rel}"
    return path.read_text(encoding="utf-8")


def _live_test_sources() -> list[Path]:
    found = [p for p in LIVE_TEST_CANDIDATES if p.is_file()]
    assert found, "expected golden-path live test source under tests/"
    return found


# ---------------------------------------------------------------------------
# Docs SoT
# ---------------------------------------------------------------------------


def test_golden_path_doc_exists_with_anchor() -> None:
    assert GOLDEN_DOC.is_file(), "docs/golden-path.md must exist"
    text = GOLDEN_DOC.read_text(encoding="utf-8")
    assert text.strip(), "docs/golden-path.md must be non-empty"
    # L6: fragment #golden-path from H2 containing "Golden path" (or similar)
    assert re.search(r"(?im)^#{1,2}\s+.*golden\s+path", text), (
        "docs/golden-path.md must have an H1/H2 that yields #golden-path"
    )


def test_golden_path_doc_wire_vs_hl_honesty() -> None:
    text = _read("docs/golden-path.md")
    for wire in REQUIRED_WIRES:
        assert wire in text, f"docs/golden-path.md must document wire {wire!r}"
    # HL aliases (docs-only; not smoke wire types)
    assert "render_visible_composite" in text, "docs must mention HL alias render_visible_composite"
    assert "create_selection" in text, "docs must mention HL create_selection (as non-wire)"
    # Explicit ban language
    lower = text.lower()
    assert "never" in lower or "must not" in lower or "does not send" in lower
    assert "plugin" in lower and ("wire" in lower or "tcp" in lower)
    # Offline honesty
    assert "E-OFFLINE-GOLDEN" in text
    # Start-order + release gates links
    assert "start-order" in text or "#start-order" in text
    assert "release-gates" in text or "#release-gates" in text


def test_golden_path_doc_hybrid_table_surfaces() -> None:
    text = _read("docs/golden-path.md")
    lower = text.lower()
    assert "plugin" in lower and "tcp" in lower
    assert "mcp" in lower
    assert "cli" in lower
    assert "host" in lower


# ---------------------------------------------------------------------------
# Smoke script structure
# ---------------------------------------------------------------------------


def test_smoke_script_exists_and_wires() -> None:
    assert SMOKE_SCRIPT.is_file(), "scripts/golden_path_smoke.py must exist"
    src = SMOKE_SCRIPT.read_text(encoding="utf-8")
    assert "get_image_bitmap" in src
    for wire in (
        "open_image",
        "orient_workspace",
        "ensure_source_immutable",
        "checkpoint_create",
        "save_xcf",
        "export_image",
        "get_gimp_info",
    ):
        assert wire in src, f"smoke script must reference wire {wire!r}"


def test_smoke_script_no_class_a_or_hl_as_wire() -> None:
    src = SMOKE_SCRIPT.read_text(encoding="utf-8")
    assert _CREATE_SELECTION_WIRE.search(src) is None, (
        "smoke must never send create_selection as plugin wire type"
    )
    assert _RENDER_COMPOSITE_WIRE.search(src) is None, (
        "smoke must never send render_visible_composite as plugin wire type"
    )
    assert _CMDS_PAYLOAD.search(src) is None, "smoke must not construct cmds payload"
    # Hard Class A bans on the product path (comments may mention names)
    assert re.search(r"""send_authenticated_command\s*\(\s*["']python-fu-eval["']""", src) is None
    assert re.search(r"""send_authenticated_command\s*\(\s*["']call_api["']""", src) is None
    assert re.search(r"""["']python-fu-eval["']""", src) is None
    assert re.search(r"""["']call_api["']""", src) is None
    assert '"cmds"' not in src and "'cmds'" not in src
    # Must not require ALLOW_EXEC for success (no getenv/set of that env)
    assert re.search(r"""environ\.(?:get|setdefault)\(\s*["']GIMP_MCP_ALLOW_EXEC""", src) is None
    assert re.search(r"""os\.environ\s*\[\s*["']GIMP_MCP_ALLOW_EXEC""", src) is None


def test_smoke_script_default_dry_run_and_flags() -> None:
    src = SMOKE_SCRIPT.read_text(encoding="utf-8")
    assert "--dry-run" in src
    assert "--live" in src
    assert "--out-dir" in src
    assert "--timeout" in src
    assert "--json" in src
    assert "GIMP_MCP_LIVE" in src
    assert "evidence.json" in src or "evidence" in src
    assert "schema_version" in src
    assert "verify_artifact" in src
    assert "send_authenticated_command" in src
    assert "exit_code_for" in src or "exit_codes" in src


# ---------------------------------------------------------------------------
# Cross-links (M7 / M8)
# ---------------------------------------------------------------------------


def test_release_md_points_at_golden_path() -> None:
    text = _read("docs/release.md")
    assert "golden-path" in text.lower() or "golden_path" in text
    assert "golden_path_smoke" in text or "golden-path.md" in text


def test_operator_runbook_links_golden_path() -> None:
    text = _read("docs/operator-runbook.md")
    assert "golden-path" in text.lower() or "golden_path" in text


def test_readme_demos_golden_path_first_legacy_labeled() -> None:
    text = _read("README.md")
    assert "golden_path_smoke" in text or "golden-path" in text
    # M7: legacy demos demoted
    assert "Legacy — requires exec" in text or "Legacy — requires exec, prefer golden-path" in text
    # Golden path should appear before legacy demo scripts in the demos table region
    gp_pos = text.find("golden_path_smoke")
    if gp_pos < 0:
        gp_pos = text.lower().find("golden-path")
    legacy_pos = text.find("agent_edit_demo.py")
    assert gp_pos >= 0, "README must list golden-path smoke"
    assert legacy_pos >= 0, "README still lists agent_edit_demo (legacy)"
    assert gp_pos < legacy_pos, "golden-path smoke must be first demos row (before legacy)"


def test_ci_and_testing_integration_consumer_updated() -> None:
    text = _read("docs/ci-and-testing.md")
    # M8: must NOT claim 0022 ships zero integration as current truth without update
    stale = re.search(
        r"(?i)0022\s+ships\s+zero\s+@?integration",
        text,
    )
    assert stale is None, (
        "ci-and-testing.md must replace '0022 ships zero @integration' "
        "with 0027 golden-path live smoke as first consumer"
    )
    assert "GIMP_MCP_LIVE" in text
    assert "integration" in text
    assert "golden" in text.lower()


# ---------------------------------------------------------------------------
# M3: live integration marker present in source
# ---------------------------------------------------------------------------


def test_live_test_has_integration_marker_and_live_env() -> None:
    combined = "\n".join(p.read_text(encoding="utf-8") for p in _live_test_sources())
    assert "@pytest.mark.integration" in combined, (
        "live golden-path test must use @pytest.mark.integration (M3)"
    )
    assert "GIMP_MCP_LIVE" in combined, "live golden-path test must gate on GIMP_MCP_LIVE (M3)"


# ---------------------------------------------------------------------------
# Optional dry-run unit (offline, no socket)
# ---------------------------------------------------------------------------


def test_smoke_dry_run_exit_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("GIMP_WORKSPACE_ROOT", str(ws))
    # Ensure live env is off
    monkeypatch.delenv("GIMP_MCP_LIVE", raising=False)
    proc = subprocess.run(
        [sys.executable, str(SMOKE_SCRIPT), "--dry-run"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "GIMP_WORKSPACE_ROOT": str(ws)},
    )
    assert proc.returncode == 0, (
        f"dry-run failed rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    assert "get_image_bitmap" in out or "open_image" in out
    # No socket evidence of live steps succeeding
    assert "evidence.json" not in out or "dry" in out.lower()


def test_smoke_dry_run_missing_workspace_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """M1: dry-run without workspace (env unset, no --workspace) fails non-zero."""
    monkeypatch.delenv("GIMP_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("GIMP_MCP_LIVE", raising=False)
    env = {k: v for k, v in os.environ.items() if k not in ("GIMP_WORKSPACE_ROOT", "GIMP_MCP_LIVE")}
    proc = subprocess.run(
        [sys.executable, str(SMOKE_SCRIPT), "--dry-run", "--json"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode != 0, (
        f"expected non-zero without workspace; stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert (
        "PATH_DENIED" in combined
        or "Workspace required" in combined
        or "workspace" in combined.lower()
    )
    # JSON envelope when --json
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    if payload:
        assert payload.get("ok") is False
        assert payload.get("exit_code", 0) != 0
        assert (
            payload.get("code") == "PATH_DENIED"
            or "workspace" in str(payload.get("message", "")).lower()
        )


def test_smoke_out_dir_outside_workspace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M2: --out-dir absolute path outside workspace jail → PATH_DENIED."""
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside-out"
    outside.mkdir()
    monkeypatch.setenv("GIMP_WORKSPACE_ROOT", str(ws))
    monkeypatch.delenv("GIMP_MCP_LIVE", raising=False)
    proc = subprocess.run(
        [
            sys.executable,
            str(SMOKE_SCRIPT),
            "--dry-run",
            "--json",
            "--workspace",
            str(ws),
            "--out-dir",
            str(outside),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "GIMP_WORKSPACE_ROOT": str(ws)},
    )
    assert proc.returncode != 0, (
        f"expected PATH_DENIED for out-dir outside jail; stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert "PATH_DENIED" in combined
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    if payload:
        assert payload.get("ok") is False
        assert payload.get("code") == "PATH_DENIED"


def test_clamp_timeout_bounds() -> None:
    """L5: --timeout clamp 5-600 via pure helper."""
    smoke = _load_smoke_module()
    assert smoke._clamp_timeout(1) == 5.0
    assert smoke._clamp_timeout(999) == 600.0
    assert smoke._clamp_timeout(9999) == 600.0
    assert smoke._clamp_timeout(60) == 60.0
    assert smoke._clamp_timeout(5) == 5.0
    assert smoke._clamp_timeout(600) == 600.0


def test_map_exception_auth_failed_exit_4() -> None:
    """L4: AUTH_FAILED RuntimeError maps to transport/auth exit 4."""
    smoke = _load_smoke_module()
    import gimp_mcp_security as sec
    from gimp_agent import exit_codes as ec

    code, exit_n, message = smoke._map_exception(
        RuntimeError(f"{sec.CODE_AUTH_FAILED}: token missing")
    )
    assert code == sec.CODE_AUTH_FAILED
    assert exit_n == ec.EXIT_TRANSPORT_AUTH
    assert exit_n == 4
    assert "AUTH_FAILED" in message


# ---------------------------------------------------------------------------
# Optional live integration (skipped without GIMP_MCP_LIVE=1)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_golden_path_live_smoke_evidence() -> None:
    """Live golden-path smoke via subprocess; skip unless GIMP_MCP_LIVE=1."""
    if os.environ.get("GIMP_MCP_LIVE", "").strip() not in ("1", "true", "yes", "on"):
        pytest.skip("GIMP_MCP_LIVE=1 required for live golden-path smoke")

    ws_raw = os.environ.get("GIMP_WORKSPACE_ROOT", "").strip()
    if not ws_raw:
        pytest.skip("GIMP_WORKSPACE_ROOT required for live golden-path smoke")

    out_dir = Path(ws_raw) / "output" / "golden-path"
    proc = subprocess.run(
        [
            sys.executable,
            str(SMOKE_SCRIPT),
            "--live",
            "--out-dir",
            str(out_dir),
            "--json",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        f"live smoke failed rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    evidence_path = out_dir / "evidence.json"
    assert evidence_path.is_file(), f"missing evidence.json under {out_dir}"

    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence.get("schema_version") == 1
    assert evidence.get("overall") == "PASS"
    arts = evidence.get("artifacts") or {}

    export_png = arts.get("export_png")
    assert export_png and Path(str(export_png)).is_file(), "artifacts.export_png must exist"

    composite_png = arts.get("composite_png")
    assert composite_png and Path(str(composite_png)).is_file(), (
        "artifacts.composite_png must exist as a file"
    )

    xcf = arts.get("xcf")
    assert xcf and Path(str(xcf)).is_file(), "artifacts.xcf must exist as a file"

    checkpoint = arts.get("checkpoint")
    assert checkpoint and str(checkpoint).strip(), "artifacts.checkpoint path must be non-empty"
    ck_path = Path(str(checkpoint))
    assert ck_path.is_file(), f"artifacts.checkpoint must exist as a file: {ck_path}"

    ev = evidence.get("export_verification") or {}
    assert ev.get("pass") is True
