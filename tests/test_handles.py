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


def _method_body(text: str, method_def: str) -> str:
    start = text.find(method_def)
    assert start != -1, method_def
    rest = text[start + len(method_def) :]
    end = rest.find("\n    def ")
    return rest[:end] if end != -1 else rest[:4000]


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
        body = _method_body(text, m)
        assert "_bump_image_generation" in body, f"{m} missing bump"


def test_wiring_structural_mutators_success_return_generation_handle() -> None:
    """F4: structural success paths must include generation (+ handle for item mutators)."""
    text = (ROOT / "gimp-mcp-plugin.py").read_text(encoding="utf-8")
    # All structural mutators return generation in results construction
    with_generation = [
        "def _create_layer",
        "def _duplicate_layer",
        "def _delete_layer",
        "def _reorder_layer",
        "def _flatten_image",
        "def _merge_visible_layers",
        "def _add_text",
        "def _apply_drop_shadow",
    ]
    # Item-handle mutators (delete/flatten may return image handle instead)
    with_item_or_image_handle = [
        "def _create_layer",
        "def _duplicate_layer",
        "def _delete_layer",
        "def _reorder_layer",
        "def _flatten_image",
        "def _merge_visible_layers",
        "def _add_text",
        "def _apply_drop_shadow",
    ]
    for m in with_generation:
        body = _method_body(text, m)
        assert "generation" in body, f"{m} success path missing generation"
        assert '"generation"' in body or "'generation'" in body, (
            f"{m} results construction missing generation key"
        )
    for m in with_item_or_image_handle:
        body = _method_body(text, m)
        assert "handle" in body, f"{m} success path missing handle"
        assert '"handle"' in body or "'handle'" in body, (
            f"{m} results construction missing handle key"
        )


def test_wiring_export_to_path_no_bump() -> None:
    """F5: export path must not bump structural generation."""
    text = (ROOT / "gimp-mcp-plugin.py").read_text(encoding="utf-8")
    body = _method_body(text, "def _export_to_path")
    assert "_bump_image_generation" not in body


def test_wiring_orient_syncs_image_generations() -> None:
    """F1: orient must call _sync_image_generations to prune closed ids."""
    text = (ROOT / "gimp-mcp-plugin.py").read_text(encoding="utf-8")
    assert "def _sync_image_generations" in text
    body = _method_body(text, "def _orient_workspace")
    assert "_sync_image_generations" in body


def test_wiring_select_layers_no_seed_before_validity() -> None:
    """F3: select_layers must not call _image_generation before image is confirmed open."""
    text = (ROOT / "gimp-mcp-plugin.py").read_text(encoding="utf-8")
    body = _method_body(text, "def _select_layers")
    # live_gen during validation uses .get, not seeding helper
    assert "_image_generations.get" in body
    # Seed helper only after open image path (emit / success generation), not for live_gen=
    live_gen_line = [ln for ln in body.splitlines() if "live_gen" in ln]
    for ln in live_gen_line:
        assert "_image_generation(" not in ln, f"select_layers seeds via live_gen: {ln!r}"


def test_wiring_selection_conflict_narrow() -> None:
    """F2: SELECTION_CONFLICT only for float/floating/anchor — not bare execution error."""
    text = (ROOT / "gimp-mcp-plugin.py").read_text(encoding="utf-8")
    body = _method_body(text, "def _select_layers")
    # Old bare match must be gone (substring check on source)
    assert '"execution error"' not in body
    assert "'execution error'" not in body
    assert "float" in body
    assert "floating" in body or "anchor" in body
    assert "CODE_SELECTION_CONFLICT" in body


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


def test_prune_image_generations_pure() -> None:
    """F1: pure prune helper drops closed ids without reseeding."""
    gens = {1: 3, 2: 5, 9: 1}
    dropped = handles.prune_image_generations(gens, {1, 2})
    assert set(dropped) == {9}
    assert gens == {1: 3, 2: 5}
    # empty open set clears all; no reseed
    dropped2 = handles.prune_image_generations(gens, set())
    assert set(dropped2) == {1, 2}
    assert gens == {}
    # open id not in map is not added
    handles.prune_image_generations(gens, {42})
    assert gens == {}


def test_prune_image_generations_records_retired() -> None:
    """Tombstone last gen when pruning so ID recycle can seed above floor."""
    gens = {1: 3, 9: 5}
    retired: dict[int, int] = {9: 2}
    dropped = handles.prune_image_generations(gens, {1}, retired=retired)
    assert set(dropped) == {9}
    assert gens == {1: 3}
    assert retired[9] == 5  # max(existing 2, dropped 5)


def test_next_seed_generation_after_retire() -> None:
    """ID recycle: retired floor 5 → next seed 6; gen=1 fails STALE vs live 6."""
    assert handles.next_seed_generation(None) == 1
    assert handles.next_seed_generation(0) == 1
    assert handles.next_seed_generation(5) == 6
    seed = handles.next_seed_generation(5)
    h = handles.image_handle(42, session_epoch=1, generation=1)
    with pytest.raises(handles.HandleError) as ei:
        handles.require_image_handle(h, live_epoch=1, live_generation=seed, id_valid=True)
    assert ei.value.code == sec.CODE_STALE_HANDLE
    # Live seed itself is accepted
    h_live = handles.image_handle(42, session_epoch=1, generation=seed)
    out = handles.require_image_handle(h_live, live_epoch=1, live_generation=seed, id_valid=True)
    assert out["generation"] == 6


def test_foreign_epoch_rejects_after_restart_semantics() -> None:
    """Process-unique epoch: foreign session_epoch rejects (FOREIGN_SESSION)."""
    h = handles.image_handle(5, session_epoch=1, generation=1)
    with pytest.raises(handles.HandleError) as ei:
        handles.require_image_handle(h, live_epoch=1_234_567, live_generation=1, id_valid=True)
    assert ei.value.code == sec.CODE_FOREIGN_SESSION


def test_wiring_session_epoch_from_session_id() -> None:
    """session_epoch must be derived from session_id, not hardcoded 1 only."""
    text = (ROOT / "gimp-mcp-plugin.py").read_text(encoding="utf-8")
    body = _method_body(text, "def __init__")
    assert "self.session_id" in body
    assert "self.session_epoch" in body
    # Not the old constant-only assignment as the sole epoch source
    assert "self.session_epoch = 1" not in body
    assert "uuid.UUID" in body or "2_000_000_000" in body
    assert "_retired_generations" in body


def test_wiring_select_layers_checks_layer_ness() -> None:
    """select_layers must verify layer-ness (Layer.get_by_id / id_is_layer)."""
    text = (ROOT / "gimp-mcp-plugin.py").read_text(encoding="utf-8")
    body = _method_body(text, "def _select_layers")
    assert "Layer.get_by_id" in body or "id_is_layer" in body
    assert "not a layer" in body
    # Must not unconditionally accept bare Item before layer check
    # (Layer path / id_is_layer present; wrong-kind → INVALID_HANDLE)
    assert "INVALID_HANDLE" in body or "CODE_INVALID_HANDLE" in body


def test_wiring_security_codes_in_handles_module() -> None:
    text = (ROOT / "gimp_mcp_handles.py").read_text(encoding="utf-8")
    assert "CODE_STALE_HANDLE" in text
    assert "CODE_FOREIGN_SESSION" in text
    assert "CODE_SELECTION_CONFLICT" in sec.__dict__ or hasattr(sec, "CODE_SELECTION_CONFLICT")
    assert "def prune_image_generations" in text
    assert "def next_seed_generation" in text
