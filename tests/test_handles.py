"""Offline unit tests for stable handle registry (track 0007). No GIMP required."""

from __future__ import annotations

from pathlib import Path

import pytest

import gimp_mcp_handles as handles
import gimp_mcp_security as sec
import gimp_mcp_state as state

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# CODE_* exports
# ---------------------------------------------------------------------------


def test_handle_error_codes_defined() -> None:
    assert sec.CODE_STALE_HANDLE == "STALE_HANDLE"
    assert sec.CODE_FOREIGN_SESSION == "FOREIGN_SESSION"
    assert sec.CODE_INVALID_HANDLE == "INVALID_HANDLE"
    assert sec.CODE_HANDLE_NOT_FOUND == "HANDLE_NOT_FOUND"
    assert sec.CODE_SELECTION_CONFLICT == "SELECTION_CONFLICT"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def test_image_handle_requires_generation() -> None:
    h = handles.image_handle(1, session_epoch=1, generation=2)
    assert h == {"image_id": 1, "generation": 2, "session_epoch": 1}
    with pytest.raises(TypeError):
        handles.image_handle(1, session_epoch=1)  # type: ignore[call-arg]


def test_item_handle_with_fingerprint() -> None:
    h = handles.item_handle(9, image_id=1, session_epoch=1, generation=1, fingerprint="abc")
    assert h["fingerprint"] == "abc"
    assert h["item_id"] == 9


# ---------------------------------------------------------------------------
# require_image_handle precedence
# ---------------------------------------------------------------------------


def test_require_image_handle_valid() -> None:
    h = handles.image_handle(5, session_epoch=1, generation=3)
    out = handles.require_image_handle(h, live_epoch=1, live_generation=3, id_valid=True)
    assert out["image_id"] == 5
    assert out["generation"] == 3


def test_require_image_handle_stale_gen() -> None:
    h = handles.image_handle(5, session_epoch=1, generation=1)
    with pytest.raises(handles.HandleError) as ei:
        handles.require_image_handle(h, live_epoch=1, live_generation=2, id_valid=True)
    assert ei.value.code == sec.CODE_STALE_HANDLE


def test_require_image_handle_foreign_epoch_before_stale() -> None:
    """Wrong epoch wins even when generation is also wrong (precedence)."""
    h = handles.image_handle(5, session_epoch=99, generation=1)
    with pytest.raises(handles.HandleError) as ei:
        handles.require_image_handle(h, live_epoch=1, live_generation=9, id_valid=True)
    assert ei.value.code == sec.CODE_FOREIGN_SESSION


def test_require_image_handle_invalid_shape() -> None:
    with pytest.raises(handles.HandleError) as ei:
        handles.require_image_handle("not-a-dict", live_epoch=1, live_generation=1, id_valid=True)
    assert ei.value.code == sec.CODE_INVALID_HANDLE

    with pytest.raises(handles.HandleError) as ei2:
        handles.require_image_handle(
            {"image_id": True, "generation": 1, "session_epoch": 1},
            live_epoch=1,
            live_generation=1,
            id_valid=True,
        )
    assert ei2.value.code == sec.CODE_INVALID_HANDLE

    with pytest.raises(handles.HandleError) as ei3:
        handles.require_image_handle(
            {"image_id": 1, "generation": 1},
            live_epoch=1,
            live_generation=1,
            id_valid=True,
        )
    assert ei3.value.code == sec.CODE_INVALID_HANDLE


def test_require_image_handle_not_found() -> None:
    h = handles.image_handle(5, session_epoch=1, generation=1)
    with pytest.raises(handles.HandleError) as ei:
        handles.require_image_handle(h, live_epoch=1, live_generation=1, id_valid=False)
    assert ei.value.code == sec.CODE_HANDLE_NOT_FOUND


def test_require_image_handle_fingerprint_mismatch() -> None:
    h = handles.image_handle(5, session_epoch=1, generation=1, fingerprint="aaa")
    with pytest.raises(handles.HandleError) as ei:
        handles.require_image_handle(
            h,
            live_epoch=1,
            live_generation=1,
            id_valid=True,
            current_fingerprint="bbb",
        )
    assert ei.value.code == sec.CODE_STALE_HANDLE


def test_require_image_handle_fingerprint_one_missing_ok() -> None:
    h = handles.image_handle(5, session_epoch=1, generation=1, fingerprint="aaa")
    out = handles.require_image_handle(
        h, live_epoch=1, live_generation=1, id_valid=True, current_fingerprint=None
    )
    assert out["image_id"] == 5
    h2 = handles.image_handle(5, session_epoch=1, generation=1)
    out2 = handles.require_image_handle(
        h2,
        live_epoch=1,
        live_generation=1,
        id_valid=True,
        current_fingerprint="bbb",
    )
    assert out2["image_id"] == 5


# ---------------------------------------------------------------------------
# require_item_handle
# ---------------------------------------------------------------------------


def test_require_item_handle_valid() -> None:
    h = handles.item_handle(9, image_id=1, session_epoch=1, generation=2)
    out = handles.require_item_handle(
        h, live_epoch=1, live_generation=2, id_valid=True, item_belongs_to_image=True
    )
    assert out["item_id"] == 9


def test_require_item_handle_membership_fail() -> None:
    h = handles.item_handle(9, image_id=1, session_epoch=1, generation=1)
    with pytest.raises(handles.HandleError) as ei:
        handles.require_item_handle(
            h,
            live_epoch=1,
            live_generation=1,
            id_valid=True,
            item_belongs_to_image=False,
        )
    assert ei.value.code == sec.CODE_HANDLE_NOT_FOUND


def test_require_item_handle_image_id_mismatch() -> None:
    h = handles.item_handle(9, image_id=1, session_epoch=1, generation=1)
    with pytest.raises(handles.HandleError) as ei:
        handles.require_item_handle(
            h,
            live_epoch=1,
            live_generation=1,
            id_valid=True,
            expected_image_id=2,
        )
    assert ei.value.code == sec.CODE_HANDLE_NOT_FOUND


# ---------------------------------------------------------------------------
# require_item_handles
# ---------------------------------------------------------------------------


def test_require_item_handles_empty_and_oversize() -> None:
    with pytest.raises(handles.HandleError) as ei:
        handles.require_item_handles([], live_epoch=1, live_generation=1)
    assert ei.value.code == sec.CODE_INVALID_HANDLE

    too_many = [
        handles.item_handle(i, image_id=1, session_epoch=1, generation=1) for i in range(65)
    ]
    with pytest.raises(handles.HandleError) as ei2:
        handles.require_item_handles(too_many, live_epoch=1, live_generation=1)
    assert ei2.value.code == sec.CODE_INVALID_HANDLE


def test_require_item_handles_mixed_image_ids() -> None:
    hs = [
        handles.item_handle(1, image_id=1, session_epoch=1, generation=1),
        handles.item_handle(2, image_id=2, session_epoch=1, generation=1),
    ]
    with pytest.raises(handles.HandleError) as ei:
        handles.require_item_handles(hs, live_epoch=1, live_generation=1)
    assert ei.value.code == sec.CODE_INVALID_HANDLE


def test_require_item_handles_ok() -> None:
    hs = [
        handles.item_handle(1, image_id=7, session_epoch=1, generation=3),
        handles.item_handle(2, image_id=7, session_epoch=1, generation=3),
    ]
    out = handles.require_item_handles(
        hs,
        live_epoch=1,
        live_generation=3,
        id_valid_flags=[True, True],
        item_belongs_flags=[True, True],
    )
    assert len(out) == 2
    assert out[0]["image_id"] == 7


# ---------------------------------------------------------------------------
# Fingerprint rename-stable
# ---------------------------------------------------------------------------


def test_fingerprint_excludes_name() -> None:
    a = handles.fingerprint_item(1, 2, "raster", 100, 200)
    b = handles.fingerprint_item(1, 2, "raster", 100, 200)
    assert a == b
    # name is not a parameter — rename cannot change fingerprint
    assert "Layer" not in a
    img = handles.fingerprint_image(3, "RGB", 800, 600)
    assert len(img) == 64  # sha256 hex
    assert handles.fingerprint_image(3, "RGB", 800, 600) == img
    assert handles.fingerprint_image(3, "GRAY", 800, 600) != img


# ---------------------------------------------------------------------------
# Capability + provisional aliases
# ---------------------------------------------------------------------------


def test_capability_stable_handle_registry_true() -> None:
    assert state.default_capabilities()["stable_handle_registry"] is True


def test_provisional_aliases_match_builders() -> None:
    a = state.provisional_image_handle(7, session_epoch=3)
    b = handles.image_handle(7, session_epoch=3, generation=1)
    assert a == b
    c = state.provisional_item_handle(9, image_id=7, session_epoch=3, generation=2)
    d = handles.item_handle(9, image_id=7, session_epoch=3, generation=2)
    assert c == d


# ---------------------------------------------------------------------------
# Wiring assertions (source-level, no GIMP)
# ---------------------------------------------------------------------------


def test_wiring_pyproject_lists_handles() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "gimp_mcp_handles" in text


def test_wiring_server_has_select_tools() -> None:
    text = (ROOT / "gimp_mcp_server.py").read_text(encoding="utf-8")
    assert "def select_image(" in text
    assert "def select_layers(" in text


def test_wiring_plugin_registry_and_bumps() -> None:
    text = (ROOT / "gimp-mcp-plugin.py").read_text(encoding="utf-8")
    assert "_image_generations" in text
    assert "def _bump_image_generation" in text
    assert "def _get_image_by_id" in text
    assert 'j["type"] == "select_image"' in text or '"select_image"' in text
    assert 'j["type"] == "select_layers"' in text or '"select_layers"' in text
    # Structural mutators must reference bump
    for name in (
        "_create_layer",
        "_duplicate_layer",
        "_delete_layer",
        "_reorder_layer",
        "_flatten_image",
        "_merge_visible_layers",
        "_add_text",
        "_apply_drop_shadow",
    ):
        # Find method body region roughly: method def to next def at same indent
        assert name in text
    assert "_bump_image_generation" in text
    # Snapshot composite path must NOT call bump (temp dup only)
    # Locate _get_current_image_bitmap / merge_visible on dup region
    # Soft check: count of bump in file vs ensure snapshot method body lacks it
    snap_start = text.find("def _get_current_image_bitmap")
    assert snap_start != -1
    # next top-level method-ish after a large chunk
    snap_chunk = text[snap_start : snap_start + 8000]
    assert "_bump_image_generation" not in snap_chunk


def test_wiring_structural_mutators_call_bump() -> None:
    text = (ROOT / "gimp-mcp-plugin.py").read_text(encoding="utf-8")
    methods = [
        "def _create_layer",
        "def _duplicate_layer",
        "def _delete_layer",
        "def _reorder_layer",
        "def _flatten_image",
        "def _merge_visible_layers",
        "def _add_text",
        "def _apply_drop_shadow",
    ]
    for m in methods:
        start = text.find(m)
        assert start != -1, m
        # body until next "    def " at class method indent
        rest = text[start + len(m) :]
        end = rest.find("\n    def ")
        body = rest[:end] if end != -1 else rest[:4000]
        assert "_bump_image_generation" in body, f"{m} missing bump"


def test_wiring_orient_uses_live_generation_not_hardcoded_alone() -> None:
    text = (ROOT / "gimp-mcp-plugin.py").read_text(encoding="utf-8")
    # Orient builders should call registry / emit helpers, not hardcode generation: 1
    ih = text.find("def _orient_item_handle")
    assert ih != -1
    body = text[ih : ih + 500]
    assert (
        "_image_generation" in body or "_handles.item_handle" in body or "_emit_item_handle" in body
    )
    # Must not be the old hardcoded-only form without registry path
    assert '"generation": 1' not in body


def test_wiring_security_codes_in_handles_module() -> None:
    text = (ROOT / "gimp_mcp_handles.py").read_text(encoding="utf-8")
    assert "CODE_STALE_HANDLE" in text
    assert "CODE_FOREIGN_SESSION" in text
    assert "CODE_SELECTION_CONFLICT" in sec.__dict__ or hasattr(sec, "CODE_SELECTION_CONFLICT")
