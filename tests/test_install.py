"""Offline tests for gimp-agent install/uninstall + doctor extensions (track 0018)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from gimp_agent import exit_codes as ec
from gimp_agent import install as install_mod
from gimp_agent import paths as pathmod
from gimp_agent.cli import main
from gimp_agent.doctor import run_doctor

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_complete_source(root: Path, *, content_prefix: str = "src") -> Path:
    """Create a complete EXPECTED ship set under ``root``."""
    root.mkdir(parents=True, exist_ok=True)
    for name in pathmod.EXPECTED_PLUGIN_FILES:
        (root / name).write_text(f"{content_prefix}:{name}\n", encoding="utf-8")
    return root


def _assert_host_only_not_in_expected() -> None:
    for name in install_mod.HOST_ONLY_MODULE_NAMES:
        assert name not in pathmod.EXPECTED_PLUGIN_FILES


# ---------------------------------------------------------------------------
# SoT / static
# ---------------------------------------------------------------------------


def test_expected_is_single_sot_len_10() -> None:
    assert len(pathmod.EXPECTED_PLUGIN_FILES) == 10
    _assert_host_only_not_in_expected()
    assert "gimp_mcp_state.py" not in pathmod.EXPECTED_PLUGIN_FILES
    assert "gimp_mcp_surface.py" not in pathmod.EXPECTED_PLUGIN_FILES
    assert "gimp_mcp_verify.py" not in pathmod.EXPECTED_PLUGIN_FILES
    assert "gimp_mcp_recipes.py" not in pathmod.EXPECTED_PLUGIN_FILES


def test_backup_suffix_format() -> None:
    from datetime import datetime

    fixed = datetime(2026, 8, 4, 12, 34, 56)
    assert install_mod.backup_suffix(when=fixed) == ".bak.20260804-123456"


# ---------------------------------------------------------------------------
# resolve_source_dir
# ---------------------------------------------------------------------------


def test_resolve_source_complete_explicit(tmp_path: Path) -> None:
    src = _write_complete_source(tmp_path / "ship")
    resolved = install_mod.resolve_source_dir(src)
    assert resolved == src.resolve()


def test_resolve_source_incomplete_explicit_fails(tmp_path: Path) -> None:
    src = tmp_path / "partial"
    src.mkdir()
    (src / "gimp-mcp-plugin.py").write_text("x\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="10 files"):
        install_mod.resolve_source_dir(src)


def test_resolve_source_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = _write_complete_source(tmp_path / "cwd_ship")
    monkeypatch.chdir(src)
    monkeypatch.delenv(install_mod.ENV_SOURCE, raising=False)
    # Force package walk to miss by not relying on real repo; cwd should win.
    resolved = install_mod.resolve_source_dir(None)
    assert resolved == src.resolve()


def test_resolve_source_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = _write_complete_source(tmp_path / "env_ship")
    monkeypatch.setenv(install_mod.ENV_SOURCE, str(src))
    # cwd incomplete
    empty = tmp_path / "empty_cwd"
    empty.mkdir()
    monkeypatch.chdir(empty)
    resolved = install_mod.resolve_source_dir(None)
    assert resolved == src.resolve()


def test_resolve_source_package_parent_walk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When cwd/env incomplete, package parent walk finds checkout root."""
    # Real package parents include the actual repo (complete ship set in CI/dev).
    monkeypatch.delenv(install_mod.ENV_SOURCE, raising=False)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    resolved = install_mod.resolve_source_dir(None)
    assert install_mod.is_complete_source(resolved)
    assert (resolved / "gimp-mcp-plugin.py").is_file()


# ---------------------------------------------------------------------------
# plan / dry-run / install
# ---------------------------------------------------------------------------


def test_dry_run_zero_writes(tmp_path: Path) -> None:
    src = _write_complete_source(tmp_path / "src")
    target = tmp_path / "plug-ins" / "gimp-mcp-plugin"
    report = install_mod.install_plugin(source=src, target=target, dry_run=True)
    assert report.ok is True
    assert report.dry_run is True
    assert report.planned
    assert len(report.planned) == 10
    assert not target.exists()
    assert report.restart_required is False
    assert report.envelope_data()["expected_count"] == 10


def test_install_full_10_into_tmp_target(tmp_path: Path) -> None:
    src = _write_complete_source(tmp_path / "src")
    target = tmp_path / "plug-ins" / "gimp-mcp-plugin"
    report = install_mod.install_plugin(source=src, target=target, dry_run=False)
    assert report.ok is True
    assert report.code is None
    assert set(report.copied) == set(pathmod.EXPECTED_PLUGIN_FILES)
    assert report.failed == []
    assert report.restart_required is True
    for name in pathmod.EXPECTED_PLUGIN_FILES:
        assert (target / name).is_file()
        assert (target / name).read_text(encoding="utf-8") == f"src:{name}\n"


def test_install_incomplete_to_complete(tmp_path: Path) -> None:
    src = _write_complete_source(tmp_path / "src", content_prefix="new")
    target = tmp_path / "plug-ins" / "gimp-mcp-plugin"
    target.mkdir(parents=True)
    (target / "gimp-mcp-plugin.py").write_text("old only\n", encoding="utf-8")
    assert len(pathmod.missing_plugin_files(target)) == 9

    report = install_mod.install_plugin(source=src, target=target)
    assert report.ok is True
    assert set(pathmod.missing_plugin_files(target)) == set()
    present = {p.name for p in target.iterdir() if p.is_file() and ".bak." not in p.name}
    assert present == set(pathmod.EXPECTED_PLUGIN_FILES)


def test_overwrite_with_backup(tmp_path: Path) -> None:
    src = _write_complete_source(tmp_path / "src", content_prefix="v2")
    target = tmp_path / "plug-ins" / "gimp-mcp-plugin"
    target.mkdir(parents=True)
    (target / "gimp-mcp-plugin.py").write_text("v1:plugin\n", encoding="utf-8")

    report = install_mod.install_plugin(source=src, target=target, backup=True)
    assert report.ok is True
    assert report.backed_up
    assert any(b.startswith("gimp-mcp-plugin.py.bak.") for b in report.backed_up)
    assert (target / "gimp-mcp-plugin.py").read_text(encoding="utf-8") == "v2:gimp-mcp-plugin.py\n"
    bak_files = list(target.glob("gimp-mcp-plugin.py.bak.*"))
    assert len(bak_files) == 1
    assert bak_files[0].read_text(encoding="utf-8") == "v1:plugin\n"


def test_overwrite_no_backup(tmp_path: Path) -> None:
    src = _write_complete_source(tmp_path / "src", content_prefix="v2")
    target = tmp_path / "plug-ins" / "gimp-mcp-plugin"
    target.mkdir(parents=True)
    (target / "gimp-mcp-plugin.py").write_text("v1\n", encoding="utf-8")

    report = install_mod.install_plugin(source=src, target=target, backup=False)
    assert report.ok is True
    assert report.backed_up == []
    assert list(target.glob("*.bak.*")) == []
    assert (target / "gimp-mcp-plugin.py").read_text(encoding="utf-8").startswith("v2:")


def test_permission_error_continues_others(tmp_path: Path) -> None:
    src = _write_complete_source(tmp_path / "src")
    target = tmp_path / "plug-ins" / "gimp-mcp-plugin"

    real_copy2 = install_mod.shutil.copy2

    def _flaky_copy2(src_p: Any, dst_p: Any, *a: Any, **k: Any) -> Any:
        if Path(dst_p).name == "gimp-mcp-plugin.py" or Path(src_p).name == "gimp-mcp-plugin.py":
            # Fail on the entrypoint destination copy (not backup of old)
            dst_path = Path(dst_p)
            if dst_path.name == "gimp-mcp-plugin.py":
                raise PermissionError("simulated lock")
        return real_copy2(src_p, dst_p, *a, **k)

    with patch.object(install_mod.shutil, "copy2", side_effect=_flaky_copy2):
        report = install_mod.install_plugin(source=src, target=target, backup=False)

    assert report.ok is False
    assert report.code == ec.PLUGIN_NOT_FOUND
    assert report.failed
    assert any(f["name"] == "gimp-mcp-plugin.py" for f in report.failed)
    # Other files still attempted / present
    others = [n for n in pathmod.EXPECTED_PLUGIN_FILES if n != "gimp-mcp-plugin.py"]
    assert len(report.copied) >= 1
    for name in others:
        assert (target / name).is_file() or any(f["name"] == name for f in report.failed)


def test_never_copies_host_only(tmp_path: Path) -> None:
    src = _write_complete_source(tmp_path / "src")
    # Plant host-only modules in source — must not be deployed
    for name in install_mod.HOST_ONLY_MODULE_NAMES:
        (src / name).write_text("host-only\n", encoding="utf-8")
    target = tmp_path / "plug-ins" / "gimp-mcp-plugin"
    report = install_mod.install_plugin(source=src, target=target)
    assert report.ok is True
    for name in install_mod.HOST_ONLY_MODULE_NAMES:
        assert not (target / name).exists()
    assert set(report.copied) == set(pathmod.EXPECTED_PLUGIN_FILES)


def test_extra_files_in_target_left_untouched(tmp_path: Path) -> None:
    src = _write_complete_source(tmp_path / "src")
    target = tmp_path / "plug-ins" / "gimp-mcp-plugin"
    target.mkdir(parents=True)
    stranger = target / "local_hack.py"
    stranger.write_text("keep me\n", encoding="utf-8")
    install_mod.install_plugin(source=src, target=target)
    assert stranger.is_file()
    assert stranger.read_text(encoding="utf-8") == "keep me\n"


def test_target_is_exact_path_no_auto_append(tmp_path: Path) -> None:
    src = _write_complete_source(tmp_path / "src")
    # Pass a non-standard dir name — must use it exactly
    target = tmp_path / "custom-plugin-dir"
    report = install_mod.install_plugin(source=src, target=target)
    assert report.ok is True
    assert report.target_dir == str(target)
    assert (target / "gimp-mcp-plugin.py").is_file()
    assert not (target / "plug-ins" / "gimp-mcp-plugin").exists()


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------


def test_uninstall_expected_gone_stranger_remains(tmp_path: Path) -> None:
    src = _write_complete_source(tmp_path / "src")
    target = tmp_path / "plug-ins" / "gimp-mcp-plugin"
    install_mod.install_plugin(source=src, target=target)
    stranger = target / "notes.txt"
    stranger.write_text("keep\n", encoding="utf-8")

    report = install_mod.uninstall_plugin(target=target, dry_run=False)
    assert report.ok is True
    for name in pathmod.EXPECTED_PLUGIN_FILES:
        assert not (target / name).exists()
    assert stranger.is_file()
    assert target.is_dir()  # empty-dir left for manual delete


def test_uninstall_dry_run_no_deletes(tmp_path: Path) -> None:
    src = _write_complete_source(tmp_path / "src")
    target = tmp_path / "plug-ins" / "gimp-mcp-plugin"
    install_mod.install_plugin(source=src, target=target)
    report = install_mod.uninstall_plugin(target=target, dry_run=True)
    assert report.ok is True
    assert report.dry_run is True
    assert report.planned
    for name in pathmod.EXPECTED_PLUGIN_FILES:
        assert (target / name).is_file()


# ---------------------------------------------------------------------------
# compare_installed
# ---------------------------------------------------------------------------


def test_compare_installed_only_both_present_mismatches(tmp_path: Path) -> None:
    src = _write_complete_source(tmp_path / "src", content_prefix="a")
    target = tmp_path / "tgt"
    target.mkdir()
    # Both present matching
    (target / "gimp-mcp-plugin.py").write_text("a:gimp-mcp-plugin.py\n", encoding="utf-8")
    # Both present mismatch
    (target / "gimp_mcp_security.py").write_text("STALE\n", encoding="utf-8")
    # Missing on target — not a mismatch
    # (other EXPECTED files absent)

    mismatches = install_mod.compare_installed(src, target)
    assert "gimp_mcp_security.py" in mismatches
    assert "gimp-mcp-plugin.py" not in mismatches
    assert "gimp_mcp_tx.py" not in mismatches  # missing ≠ stale


def test_sha256_file_stable(tmp_path: Path) -> None:
    p = tmp_path / "f.txt"
    p.write_bytes(b"hello")
    h1 = install_mod.sha256_file(p)
    h2 = install_mod.sha256_file(p)
    assert h1 == h2
    assert len(h1) == 64


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_uninstall_without_yes_exit_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["uninstall", "--json"])
    assert code == 2
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is False
    assert body["exit_code"] == 2
    assert body["code"] == ec.CLI_USAGE


def test_cli_uninstall_dry_run_without_yes_ok(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    src = _write_complete_source(tmp_path / "src")
    target = tmp_path / "plug-ins" / "gimp-mcp-plugin"
    install_mod.install_plugin(source=src, target=target)
    code = main(
        [
            "uninstall",
            "--dry-run",
            "--target",
            str(target),
            "--json",
        ]
    )
    assert code == 0
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is True
    assert body["data"]["dry_run"] is True
    assert (target / "gimp-mcp-plugin.py").is_file()


def test_cli_install_dry_run_json_shape(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    src = _write_complete_source(tmp_path / "src")
    target = tmp_path / "plug-ins" / "gimp-mcp-plugin"
    code = main(
        [
            "install",
            "--dry-run",
            "--source",
            str(src),
            "--target",
            str(target),
            "--json",
        ]
    )
    assert code == 0
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is True
    assert body["exit_code"] == 0
    assert body["code"] is None
    data = body["data"]
    assert data["dry_run"] is True
    assert data["expected_count"] == 10
    assert data["restart_required"] is False
    assert isinstance(data["planned"], list)
    assert len(data["planned"]) == 10
    assert data["target_dir"] == str(target)
    assert not target.exists()


def test_cli_install_source_target(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    src = _write_complete_source(tmp_path / "src")
    target = tmp_path / "plug-ins" / "gimp-mcp-plugin"
    code = main(
        [
            "install",
            "--source",
            str(src),
            "--target",
            str(target),
            "--json",
        ]
    )
    assert code == 0
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is True
    assert body["data"]["restart_required"] is True
    assert len(body["data"]["copied"]) == 10
    assert set(body["data"]["copied"]) == set(pathmod.EXPECTED_PLUGIN_FILES)


def test_cli_uninstall_yes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    src = _write_complete_source(tmp_path / "src")
    target = tmp_path / "plug-ins" / "gimp-mcp-plugin"
    install_mod.install_plugin(source=src, target=target)
    code = main(["uninstall", "--yes", "--target", str(target), "--json"])
    assert code == 0
    body = json.loads(capsys.readouterr().out)
    assert body["ok"] is True
    for name in pathmod.EXPECTED_PLUGIN_FILES:
        assert not (target / name).exists()


# ---------------------------------------------------------------------------
# Doctor extensions
# ---------------------------------------------------------------------------


def _patch_doctor_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    plugin_dir: Path,
) -> None:
    fake_console = tmp_path / "gimp-console-3.2.exe"
    fake_console.write_bytes(b"")
    monkeypatch.setattr(pathmod, "find_gimp_console", lambda: fake_console)
    monkeypatch.setattr(
        pathmod,
        "run_console_version",
        lambda console, *, timeout=15.0: ("GIMP 3.2.4", None),
    )
    monkeypatch.setattr(pathmod, "find_gimp_gui", lambda: None)
    monkeypatch.setattr(pathmod, "find_plugin_dir", lambda base=None: plugin_dir)
    import gimp_mcp_security as sec

    monkeypatch.delenv(sec.ENV_TOKEN, raising=False)
    monkeypatch.setattr(sec, "read_token_file", lambda path=None: None)
    monkeypatch.setattr(sec, "default_token_path", lambda: tmp_path / "session.token")
    monkeypatch.setattr(sec, "workspace_root", lambda: None)
    monkeypatch.setattr(sec, "get_port", lambda: 9877)
    monkeypatch.setattr(
        "gimp_agent.doctor._tcp_connect_only",
        lambda host, port, timeout=2.0: (False, "refused"),
    )


def test_doctor_incomplete_strict_exit_3(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir = tmp_path / "plug-ins" / "gimp-mcp-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "gimp-mcp-plugin.py").write_text("# only\n", encoding="utf-8")
    _patch_doctor_base(tmp_path, monkeypatch, plugin_dir)

    report = run_doctor(strict=True)
    assert report.ok is False
    assert report.code == ec.PLUGIN_NOT_FOUND
    assert report.exit_code == 3
    pf = next(c for c in report.checks if c.name == "plugin_files")
    assert pf.status == "fail"
    assert pf.detail.get("expected_count") == 10
    assert "missing" in pf.detail
    assert "present" in pf.detail
    assert "expected" in pf.detail
    assert len(pf.detail["missing"]) == 9


def test_doctor_stale_warn_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = _write_complete_source(tmp_path / "src", content_prefix="fresh")
    plugin_dir = tmp_path / "plug-ins" / "gimp-mcp-plugin"
    # Install then mutate one file to create stale content
    install_mod.install_plugin(source=src, target=plugin_dir)
    (plugin_dir / "gimp_mcp_security.py").write_text("STALE BYTES\n", encoding="utf-8")
    _patch_doctor_base(tmp_path, monkeypatch, plugin_dir)
    monkeypatch.setattr(install_mod, "resolve_source_dir", lambda explicit=None: src)

    report = run_doctor(strict=True)
    # All files present → required pass; stale is warn only
    assert report.ok is True
    assert report.exit_code == 0
    names = [c.name for c in report.checks]
    assert names.index("plugin_files") < names.index("plugin_stale")
    assert names.index("plugin_stale") < names.index("token")
    stale = next(c for c in report.checks if c.name == "plugin_stale")
    assert stale.severity == "warn"
    assert stale.status == "warn"
    assert "gimp_mcp_security.py" in (stale.detail.get("mismatches") or [])


def test_doctor_stale_skip_no_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir = tmp_path / "plug-ins" / "gimp-mcp-plugin"
    _write_complete_source(plugin_dir, content_prefix="installed")
    _patch_doctor_base(tmp_path, monkeypatch, plugin_dir)

    def _no_source(explicit: Path | None = None) -> Path:
        raise FileNotFoundError(install_mod._SOURCE_FAIL_MSG)

    monkeypatch.setattr(install_mod, "resolve_source_dir", _no_source)

    report = run_doctor(strict=False)
    stale = next(c for c in report.checks if c.name == "plugin_stale")
    assert stale.status in ("skip", "info")
    assert stale.severity == "warn"


def test_doctor_check_order_includes_plugin_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir = tmp_path / "plug-ins" / "gimp-mcp-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "gimp-mcp-plugin.py").write_text("x\n", encoding="utf-8")
    _patch_doctor_base(tmp_path, monkeypatch, plugin_dir)
    report = run_doctor(strict=False)
    names = [c.name for c in report.checks]
    expected_order = [
        "gimp_console",
        "plugin_files",
        "plugin_stale",
        "token",
        "tcp_connect",
        "gimp_gui",
        "workspace",
        "exiftool",
        "imagemagick",
        "tool_pins",
    ]
    # Relative order of known checks
    positions = [names.index(n) for n in expected_order]
    assert positions == sorted(positions)


def test_doctor_tool_pins_package_not_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_dir = tmp_path / "plug-ins" / "gimp-mcp-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "gimp-mcp-plugin.py").write_text("x\n", encoding="utf-8")
    _patch_doctor_base(tmp_path, monkeypatch, plugin_dir)

    from importlib import metadata

    def _boom(name: str) -> str:
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr("gimp_agent.doctor.metadata.version", _boom)
    report = run_doctor(strict=False)
    pins = next(c for c in report.checks if c.name == "tool_pins")
    assert pins.severity == "info"
    assert "(not installed)" in pins.message
    assert pins.detail.get("mcp") == "(not installed)"
    assert pins.detail.get("fastmcp") == "(not installed)"


# ---------------------------------------------------------------------------
# CLAUDE.md doc guard (AI2 H1)
# ---------------------------------------------------------------------------


def test_claude_md_doc_guard() -> None:
    root = Path(__file__).resolve().parents[1]
    # Prefer CLAUDE.md; also check Claude.md on case-sensitive FS
    candidates = [root / "CLAUDE.md", root / "Claude.md"]
    text = ""
    for path in candidates:
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            break
    assert text, "CLAUDE.md / Claude.md not found"
    assert "four files" not in text
    assert "gimp-plugin/gimp-agent-plugin" not in text


def test_scripts_exist_and_thin() -> None:
    root = Path(__file__).resolve().parents[1]
    ps1 = root / "scripts" / "install-plugin.ps1"
    sh = root / "scripts" / "install-plugin.sh"
    assert ps1.is_file()
    assert sh.is_file()
    ps1_text = ps1.read_text(encoding="utf-8")
    sh_text = sh.read_text(encoding="utf-8")
    assert "Tee-Object" not in ps1_text
    assert "$LASTEXITCODE" in ps1_text
    assert "gimp-agent install" in ps1_text
    assert "set -euo pipefail" in sh_text
    assert 'gimp-agent install "$@"' in sh_text or "gimp-agent install" in sh_text
