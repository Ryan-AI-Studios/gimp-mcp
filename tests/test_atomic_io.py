"""Offline unit tests for pure atomic I/O helpers (track 0013)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

import gimp_mcp_atomic as atomic
import gimp_mcp_security as sec
from gimp_agent import exit_codes as ec
from gimp_agent import paths as pathmod

# ---------------------------------------------------------------------------
# parse_collision
# ---------------------------------------------------------------------------


def test_parse_collision_default_fail() -> None:
    assert atomic.parse_collision(None) == "fail"
    assert atomic.parse_collision("") == "fail"
    assert atomic.parse_collision("  ") == "fail"


def test_parse_collision_valid_modes() -> None:
    for mode in ("fail", "version", "replace"):
        assert atomic.parse_collision(mode) == mode
        assert atomic.parse_collision(mode.upper()) == mode


def test_parse_collision_invalid_raises() -> None:
    with pytest.raises(ValueError, match="fail/version/replace"):
        atomic.parse_collision("overwrite")
    with pytest.raises(ValueError, match="fail/version/replace"):
        atomic.parse_collision(123)


def test_parse_collision_custom_default() -> None:
    assert atomic.parse_collision(None, default="replace") == "replace"


# ---------------------------------------------------------------------------
# resolve_output_path
# ---------------------------------------------------------------------------


def test_resolve_fail_new_path() -> None:
    existing: set[Path] = set()
    r = atomic.resolve_output_path(
        Path("/ws/out.xcf"),
        "fail",
        exists=lambda p: p in existing,
    )
    assert r.path == Path("/ws/out.xcf")
    assert r.collision == "fail"
    assert r.collision_resolved is False
    assert r.needs_backup is False


def test_resolve_fail_existing_raises_output_collision() -> None:
    target = Path("/ws/out.xcf")
    with pytest.raises(atomic.OutputCollisionError) as ei:
        atomic.resolve_output_path(target, "fail", exists=lambda p: p == target)
    assert ei.value.code == sec.CODE_OUTPUT_COLLISION
    assert ei.value.path == target


def test_resolve_version_skips_existing_stem_n() -> None:
    base = Path("/ws/photo.png")
    existing = {base, Path("/ws/photo-1.png")}
    r = atomic.resolve_output_path(
        base,
        "version",
        exists=lambda p: p in existing,
    )
    assert r.path == Path("/ws/photo-2.png")
    assert r.collision_resolved is True
    assert r.needs_backup is False


def test_resolve_version_free_base() -> None:
    base = Path("/ws/photo.png")
    r = atomic.resolve_output_path(base, "version", exists=lambda _p: False)
    assert r.path == base
    assert r.collision_resolved is False


def test_resolve_version_cap_is_internal_not_collision() -> None:
    base = Path("/ws/out.xcf")

    # Every candidate including base and stem-1..VERSION_CAP appears taken
    def all_exist(_p: Path) -> bool:
        return True

    with pytest.raises(atomic.VersionCapExceededError) as ei:
        atomic.resolve_output_path(base, "version", exists=all_exist)
    assert ei.value.code == sec.CODE_INTERNAL
    assert ei.value.code != sec.CODE_OUTPUT_COLLISION


def test_resolve_replace_needs_backup_when_exists() -> None:
    target = Path("/ws/out.png")
    r = atomic.resolve_output_path(target, "replace", exists=lambda p: p == target)
    assert r.path == target
    assert r.needs_backup is True
    assert r.collision_resolved is True


def test_resolve_replace_no_backup_when_new() -> None:
    target = Path("/ws/out.png")
    r = atomic.resolve_output_path(target, "replace", exists=lambda _p: False)
    assert r.needs_backup is False
    assert r.collision_resolved is False


# ---------------------------------------------------------------------------
# temp / backup paths
# ---------------------------------------------------------------------------


def test_make_temp_path_same_parent_preserves_suffix() -> None:
    final = Path("/ws/dir/out.png")
    tmp = atomic.make_temp_path(final, pid=4242, token="ab12")
    assert tmp.parent == final.parent
    assert tmp.suffix == ".png"
    assert tmp.name == "out.gimp-mcp-4242-ab12.png"
    assert "out.png" not in tmp.name or tmp.name != "out.png"


def test_make_temp_path_xcf_suffix() -> None:
    final = Path("C:/ws/project.xcf")
    tmp = atomic.make_temp_path(final, pid=1, token="dead")
    assert tmp.suffix == ".xcf"
    assert tmp.name == "project.gimp-mcp-1-dead.xcf"


def test_make_backup_path_preferred_when_free() -> None:
    final = Path("/ws/out.png")
    bak = atomic.make_backup_path(final, exists=lambda _p: False)
    assert bak == Path("/ws/out.gimp-mcp.bak.png")


def test_make_backup_path_timestamped_when_preferred_taken() -> None:
    final = Path("/ws/out.png")
    preferred = Path("/ws/out.gimp-mcp.bak.png")
    when = datetime(2026, 8, 3, 12, 30, 45, tzinfo=UTC)
    bak = atomic.make_backup_path(
        final,
        exists=lambda p: p == preferred,
        now_utc=when,
    )
    assert bak == Path("/ws/out.gimp-mcp.20260803T123045Z.bak.png")


# ---------------------------------------------------------------------------
# Exit map + CODE_DEFAULTS
# ---------------------------------------------------------------------------


def test_exit_output_collision_is_11() -> None:
    assert ec.exit_code_for(sec.CODE_OUTPUT_COLLISION) == 11
    assert ec.EXIT_COLLISION == 11


def test_exit_verify_failed_is_8() -> None:
    assert ec.exit_code_for(sec.CODE_VERIFY_FAILED) == 8
    assert ec.exit_code_for(sec.CODE_ALPHA_LOST) == 8


def test_code_defaults_output_collision_and_verify() -> None:
    oc = sec.CODE_DEFAULTS[sec.CODE_OUTPUT_COLLISION]
    assert oc["retryable"] is False
    vf = sec.CODE_DEFAULTS[sec.CODE_VERIFY_FAILED]
    assert vf["retryable"] is False
    assert vf["state_may_have_changed"] is False


def test_reverse_map_exit_11_has_output_collision() -> None:
    reverse = ec.exit_to_codes_table()
    assert sec.CODE_OUTPUT_COLLISION in reverse[11]
    assert reverse[11] == [sec.CODE_OUTPUT_COLLISION]


def test_reverse_map_exit_8_includes_verify_failed() -> None:
    reverse = ec.exit_to_codes_table()
    assert sec.CODE_VERIFY_FAILED in reverse[8]
    assert sec.CODE_ALPHA_LOST in reverse[8]


# ---------------------------------------------------------------------------
# Ship set
# ---------------------------------------------------------------------------


def test_expected_plugin_files_includes_atomic_len_9() -> None:
    expected = {
        "gimp-mcp-plugin.py",
        "gimp_mcp_security.py",
        "gimp_mcp_snapshot.py",
        "gimp_mcp_export.py",
        "gimp_mcp_handles.py",
        "gimp_mcp_coords.py",
        "gimp_mcp_policy.py",
        "gimp_mcp_atomic.py",
        "gimp_mcp_filters.py",
        "gimp_mcp_tx.py",
    }
    assert set(pathmod.EXPECTED_PLUGIN_FILES) == expected
    assert len(pathmod.EXPECTED_PLUGIN_FILES) == 10
    assert "gimp_mcp_state.py" not in pathmod.EXPECTED_PLUGIN_FILES


def test_atomic_module_stdlib_only() -> None:
    text = Path("gimp_mcp_atomic.py").read_text(encoding="utf-8")
    assert "from PIL" not in text
    assert "import gi" not in text
    assert "gi.repository" not in text
    # Must not import security (pure dual-ship contract)
    assert "import gimp_mcp_security" not in text
    assert "from gimp_mcp_security" not in text
