"""Offline tests for track 0015 recipe library (no live GIMP)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import gimp_mcp_recipes as recipes
import gimp_mcp_security as sec
from gimp_agent import exit_codes as ec
from gimp_agent.cli import main
from tests._png_builder import build_minimal_png


@pytest.fixture(autouse=True)
def _reset_registry() -> Any:
    recipes.reset_registry_cache()
    yield
    recipes.reset_registry_cache()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(sec.ENV_WORKSPACE, str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Load / validate / package data
# ---------------------------------------------------------------------------


def test_load_package_recipes_at_least_four() -> None:
    reg = recipes.load_package_recipes()
    assert len(reg.ids()) >= 4
    for rid in (
        "transparent-png",
        "exif-normalize",
        "web-export",
        "compare-artifacts",
    ):
        r = reg.get(rid)
        assert r["id"] == rid
        major, minor, patch = recipes._parse_semver(r["version"])
        assert (major, minor, patch) == (1, 0, 0)


def test_package_resources_at_least_four_json() -> None:
    files = recipes.list_package_recipe_files()
    assert len(files) >= 4
    assert all(f.endswith(".json") for f in files)


def test_bad_op_json_fails_closed(tmp_path: Path) -> None:
    good = {
        "id": "ok-one",
        "version": "1.0.0",
        "title": "ok",
        "batch_safe": True,
        "requires_open_session": False,
        "requires_gimp": False,
        "parameters": {},
        "steps": [
            {
                "op": "compare_images",
                "with": {"path_a": "$a", "path_b": "$b"},
            }
        ],
    }
    # Bad op in sibling file
    bad = {
        "id": "bad-one",
        "version": "1.0.0",
        "title": "bad",
        "steps": [{"op": "eval_python", "with": {}}],
    }
    (tmp_path / "ok.json").write_text(json.dumps(good), encoding="utf-8")
    (tmp_path / "bad.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(sec.GimpMcpError, match=r"fail-closed|load failed") as ei:
        recipes.load_recipes_from_dir(tmp_path)
    assert ei.value.code == sec.CODE_INTERNAL


def test_corrupt_json_load_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(sec.GimpMcpError, match=r"fail-closed|load failed|JSON") as ei:
        recipes.load_recipes_from_dir(tmp_path)
    assert ei.value.code == sec.CODE_INTERNAL


def test_duplicate_id_version_fails(tmp_path: Path) -> None:
    r = {
        "id": "dup",
        "version": "1.0.0",
        "title": "d",
        "steps": [{"op": "compare_images", "with": {}}],
    }
    (tmp_path / "a.json").write_text(json.dumps(r), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(r), encoding="utf-8")
    with pytest.raises(sec.GimpMcpError):
        recipes.load_recipes_from_dir(tmp_path)


def test_semver_latest() -> None:
    reg = recipes.RecipeRegistry()
    for ver in ("1.0.0", "1.2.0", "1.1.9"):
        reg.add(
            {
                "id": "x",
                "version": ver,
                "title": "x",
                "steps": [{"op": "compare_images", "with": {}}],
            }
        )
    assert reg.get("x")["version"] == "1.2.0"
    assert reg.get("x", "1.0.0")["version"] == "1.0.0"


def test_parse_semver() -> None:
    assert recipes._parse_semver("1.2.3") == (1, 2, 3)
    with pytest.raises(ValueError):
        recipes._parse_semver("1.2")
    with pytest.raises(ValueError):
        recipes._parse_semver("v1.2.3")


# ---------------------------------------------------------------------------
# Interpolation
# ---------------------------------------------------------------------------


def test_interpolate_exact_name() -> None:
    ctx = {"output_path": "/ws/out.png", "output_path_backup": "/ws/bak.png"}
    assert recipes.interpolate("$output_path", ctx) == "/ws/out.png"
    assert recipes.interpolate("$output_path_backup", ctx) == "/ws/bak.png"
    # Mid-string must NOT interpolate
    assert recipes.interpolate("prefix_$output_path", ctx) == "prefix_$output_path"
    assert recipes.interpolate("${output_path}", ctx) == "${output_path}"


def test_interpolate_no_false_prefix_match() -> None:
    ctx = {"output_path": "A", "output_path_backup": "B"}
    nested = {"file": "$output_path", "bak": "$output_path_backup"}
    out = recipes.interpolate(nested, ctx)
    assert out == {"file": "A", "bak": "B"}


def test_interpolate_undefined_errors() -> None:
    with pytest.raises(sec.GimpMcpError, match="undefined") as ei:
        recipes.interpolate("$foo", {})
    assert ei.value.code == sec.CODE_POLICY_DENIED


def test_interpolate_single_pass_no_rescan() -> None:
    # Substituted value that looks like $name must not be re-scanned
    ctx = {"a": "$b", "b": "final"}
    assert recipes.interpolate("$a", ctx) == "$b"


def test_list_recipes_shape() -> None:
    items = recipes.list_recipes()
    assert len(items) >= 4
    for item in items:
        assert set(item.keys()) == {
            "id",
            "version",
            "title",
            "batch_safe",
            "requires_open_session",
            "requires_gimp",
        }


# ---------------------------------------------------------------------------
# Host runner: compare-artifacts
# ---------------------------------------------------------------------------


def test_compare_artifacts_offline(workspace: Path) -> None:
    png = build_minimal_png(width=2, height=2, color_type=2)
    a = workspace / "a.png"
    b = workspace / "b.png"
    a.write_bytes(png)
    b.write_bytes(png)
    log = recipes.run_recipe(
        "compare-artifacts",
        params={"path_a": str(a), "path_b": str(b)},
    )
    assert log["ok"] is True
    assert log["backend"] == "host"
    assert log["recipe_id"] == "compare-artifacts"
    assert log["steps"][0]["ok"] is True
    assert log["steps"][0]["result"].get("pass") is True


def test_handle_and_input_both_errors(workspace: Path) -> None:
    with pytest.raises(sec.GimpMcpError, match="not both") as ei:
        recipes.run_recipe(
            "transparent-png",
            handle={"image_id": 1, "generation": 1, "session_epoch": 1},
            input_path=str(workspace / "in.png"),
            output_path=str(workspace / "out.png"),
            session_send=lambda *_a, **_k: {"status": "success", "results": {}},
        )
    assert ei.value.code == sec.CODE_POLICY_DENIED


def test_requires_open_session_needs_handle(workspace: Path) -> None:
    with pytest.raises(sec.GimpMcpError, match="handle") as ei:
        recipes.run_recipe(
            "transparent-png",
            output_path=str(workspace / "out.png"),
            session_send=lambda *_a, **_k: {"status": "success", "results": {}},
        )
    assert ei.value.code == sec.CODE_POLICY_DENIED


# ---------------------------------------------------------------------------
# Session mock: web-export scale not gated by advanced
# ---------------------------------------------------------------------------


def test_web_export_scale_with_advanced_unset(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GIMP_MCP_ADVANCED_TOOLS", raising=False)
    src = workspace / "in.png"
    src.write_bytes(build_minimal_png(width=4, height=4, color_type=2))
    out = workspace / "out.png"
    calls: list[tuple[str, dict[str, Any]]] = []

    def _send(cmd: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append((cmd, dict(params)))
        if cmd == "open_image":
            return {
                "status": "success",
                "results": {
                    "image_id": 1,
                    "handle": {"image_id": 1, "generation": 1, "session_epoch": 1},
                },
            }
        if cmd == "scale_image":
            return {"status": "success", "results": {"width": 2, "height": 2}}
        if cmd == "export_image":
            # Simulate write
            Path(params["file_path"]).write_bytes(
                build_minimal_png(width=2, height=2, color_type=2)
            )
            return {
                "status": "success",
                "results": {"file_path": params["file_path"], "format": "png"},
            }
        return {"status": "error", "error": f"unexpected {cmd}", "code": sec.CODE_INTERNAL}

    log = recipes.run_recipe(
        "web-export",
        input_path=str(src),
        output_path=str(out),
        params={"width": 2, "height": 2},
        session_send=_send,
    )
    assert log["ok"] is True
    assert log["backend"] == "session"
    ops = [c[0] for c in calls]
    assert ops == ["open_image", "scale_image", "export_image"]
    assert calls[1][1]["width"] == 2
    assert calls[1][1]["height"] == 2


def test_web_export_skips_scale_without_dims(workspace: Path) -> None:
    src = workspace / "in.png"
    src.write_bytes(build_minimal_png(width=2, height=2, color_type=2))
    out = workspace / "out.png"
    calls: list[str] = []

    def _send(cmd: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append(cmd)
        if cmd == "open_image":
            return {
                "status": "success",
                "results": {
                    "handle": {"image_id": 1, "generation": 1, "session_epoch": 1},
                },
            }
        if cmd == "scale_image":
            pytest.fail("scale_image should be skipped when width/height unset")
        if cmd == "export_image":
            Path(params["file_path"]).write_bytes(
                build_minimal_png(width=2, height=2, color_type=2)
            )
            return {
                "status": "success",
                "results": {"file_path": params["file_path"]},
            }
        return {"status": "error", "error": cmd}

    log = recipes.run_recipe(
        "web-export",
        input_path=str(src),
        output_path=str(out),
        session_send=_send,
    )
    assert log["ok"] is True
    assert "scale_image" not in calls
    # scale step present but skipped
    scale_steps = [s for s in log["steps"] if s["op"] == "scale_image"]
    assert scale_steps and scale_steps[0]["result"].get("skipped") is True


def test_transparent_png_session_order(workspace: Path) -> None:
    out = workspace / "t.png"
    calls: list[str] = []

    def _send(cmd: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append(cmd)
        if cmd == "export_image":
            # RGBA png
            pixels = b"\x00" + b"\xff\x00\x00\x80"
            Path(params["file_path"]).write_bytes(
                build_minimal_png(width=1, height=1, color_type=6, pixels=pixels)
            )
            return {
                "status": "success",
                "results": {"file_path": params["file_path"], "format": "png"},
            }
        return {"status": "error", "error": cmd}

    log = recipes.run_recipe(
        "transparent-png",
        handle={"image_id": 1, "generation": 1, "session_epoch": 1},
        output_path=str(out),
        session_send=_send,
    )
    assert log["ok"] is True
    assert calls == ["export_image"]
    assert out.exists()


def test_exif_normalize_session_order(workspace: Path) -> None:
    src = workspace / "in.jpg"
    src.write_bytes(b"fake-jpeg")
    out = workspace / "out.png"
    calls: list[str] = []

    def _send(cmd: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append(cmd)
        if cmd == "open_image":
            return {
                "status": "success",
                "results": {
                    "handle": {"image_id": 1, "generation": 1, "session_epoch": 1},
                },
            }
        if cmd == "normalize_image_orientation":
            return {"status": "success", "results": {"mode": "assume_pixels_upright"}}
        if cmd == "export_image":
            Path(params["file_path"]).write_bytes(
                build_minimal_png(width=1, height=1, color_type=2)
            )
            return {
                "status": "success",
                "results": {"file_path": params["file_path"], "format": "png"},
            }
        return {"status": "error", "error": cmd}

    log = recipes.run_recipe(
        "exif-normalize",
        input_path=str(src),
        output_path=str(out),
        session_send=_send,
    )
    assert log["ok"] is True
    assert calls == ["open_image", "normalize_image_orientation", "export_image"]
    assert out.exists()


def test_version_collision_rebinds_output_path_for_verify(workspace: Path) -> None:
    """collision=version must rebind $output_path so verify sees the written file."""
    requested = workspace / "out.png"
    # Stale pre-existing file at the requested path (width=1); new export is width=2
    requested.write_bytes(build_minimal_png(width=1, height=1, color_type=2))
    resolved = workspace / "out-1.png"
    verify_paths: list[str] = []

    def _send(cmd: str, params: dict[str, Any]) -> dict[str, Any]:
        if cmd == "export_image":
            assert Path(params["file_path"]) == requested
            resolved.write_bytes(build_minimal_png(width=2, height=2, color_type=2))
            return {
                "status": "success",
                "results": {"file_path": str(resolved), "format": "png"},
            }
        return {"status": "error", "error": cmd}

    real_verify = recipes._run_host_op

    def _host_wrap(op: str, params: dict[str, Any]) -> dict[str, Any]:
        if op == "verify_artifact":
            verify_paths.append(str(params.get("path")))
        return real_verify(op, params)

    reg = recipes.RecipeRegistry()
    reg.add(
        {
            "id": "export-then-verify",
            "version": "1.0.0",
            "title": "t",
            "batch_safe": False,
            "requires_open_session": True,
            "requires_gimp": True,
            "parameters": {
                "output_path": {"type": "path", "required": True},
                "collision": {
                    "type": "string",
                    "enum": ["fail", "version", "replace"],
                    "default": "version",
                },
            },
            "steps": [
                {
                    "op": "export_image",
                    "with": {
                        "file_path": "$output_path",
                        "format": "png",
                        "collision": "$collision",
                    },
                },
                {
                    "op": "verify_artifact",
                    "with": {
                        "path": "$output_path",
                        "expected": {"format": "png", "width": 2},
                    },
                },
            ],
            "rollback": {"delete_outputs_on_fail": True},
        }
    )
    with patch.object(recipes, "_run_host_op", side_effect=_host_wrap):
        log = recipes.run_recipe(
            "export-then-verify",
            handle={"image_id": 1, "generation": 1, "session_epoch": 1},
            output_path=str(requested),
            params={"collision": "version"},
            session_send=_send,
            registry=reg,
        )
    assert log["ok"] is True
    assert verify_paths, "verify_artifact must run"
    assert Path(verify_paths[0]) == resolved
    # Must not have verified the stale requested path
    assert Path(verify_paths[0]) != requested
    art_paths = [Path(a["path"]) for a in log["artifacts"] if a.get("role") == "output"]
    assert resolved in art_paths
    assert str(resolved) in log["created_paths"] or any(
        Path(p) == resolved for p in log["created_paths"]
    )


# ---------------------------------------------------------------------------
# Rollback: replace + fail keeps pre-existing
# ---------------------------------------------------------------------------


def test_replace_fail_keeps_preexisting(workspace: Path) -> None:
    target = workspace / "out.png"
    original = build_minimal_png(width=1, height=1, color_type=2)
    target.write_bytes(original)

    def _send(cmd: str, params: dict[str, Any]) -> dict[str, Any]:
        if cmd == "export_image":
            # Overwrite with different content then next step will fail
            Path(params["file_path"]).write_bytes(
                build_minimal_png(width=2, height=2, color_type=2)
            )
            return {
                "status": "success",
                "results": {"file_path": params["file_path"]},
            }
        return {"status": "error", "error": cmd}

    # Build a mini registry: export then fail verify
    reg = recipes.RecipeRegistry()
    reg.add(
        {
            "id": "replace-then-fail",
            "version": "1.0.0",
            "title": "t",
            "batch_safe": False,
            "requires_open_session": True,
            "requires_gimp": True,
            "parameters": {
                "output_path": {"type": "path", "required": True},
                "collision": {
                    "type": "string",
                    "enum": ["fail", "version", "replace"],
                    "default": "replace",
                },
            },
            "steps": [
                {
                    "op": "export_image",
                    "with": {
                        "file_path": "$output_path",
                        "format": "png",
                        "collision": "$collision",
                    },
                },
                {
                    "op": "verify_artifact",
                    "with": {
                        "path": "$output_path",
                        "expected": {"format": "png", "width": 999},
                    },
                },
            ],
            "rollback": {"delete_outputs_on_fail": True},
        }
    )
    with pytest.raises(sec.GimpMcpError):
        recipes.run_recipe(
            "replace-then-fail",
            handle={"image_id": 1, "generation": 1, "session_epoch": 1},
            output_path=str(target),
            params={"collision": "replace"},
            session_send=_send,
            registry=reg,
        )
    # Pre-existing target must still exist (not unlinked on rollback)
    assert target.exists()
    # created_paths should not have included the pre-existing replace target
    # File may have been overwritten by export but must not be deleted
    assert target.is_file()


def test_new_output_deleted_on_fail(workspace: Path) -> None:
    target = workspace / "new_out.png"
    assert not target.exists()

    def _send(cmd: str, params: dict[str, Any]) -> dict[str, Any]:
        if cmd == "export_image":
            Path(params["file_path"]).write_bytes(
                build_minimal_png(width=1, height=1, color_type=2)
            )
            return {
                "status": "success",
                "results": {"file_path": params["file_path"]},
            }
        return {"status": "error", "error": cmd}

    reg = recipes.RecipeRegistry()
    reg.add(
        {
            "id": "new-then-fail",
            "version": "1.0.0",
            "title": "t",
            "batch_safe": False,
            "requires_open_session": True,
            "requires_gimp": True,
            "parameters": {
                "output_path": {"type": "path", "required": True},
            },
            "steps": [
                {
                    "op": "export_image",
                    "with": {"file_path": "$output_path", "format": "png"},
                },
                {
                    "op": "verify_artifact",
                    "with": {
                        "path": "$output_path",
                        "expected": {"format": "png", "width": 999},
                    },
                },
            ],
            "rollback": {"delete_outputs_on_fail": True},
        }
    )
    with pytest.raises(sec.GimpMcpError) as ei:
        recipes.run_recipe(
            "new-then-fail",
            handle={"image_id": 1, "generation": 1, "session_epoch": 1},
            output_path=str(target),
            session_send=_send,
            registry=reg,
        )
    assert not target.exists()
    mlog = (ei.value.details or {}).get("mutation_log") or {}
    assert str(target) in mlog.get("created_paths", []) or any(
        str(target) == p or Path(p) == target for p in mlog.get("created_paths", [])
    )


def test_batch_safe_plugin_down_headless_fallback(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """batch_safe + broken session + mock headless → ok backend headless."""
    src = workspace / "in.png"
    src.write_bytes(build_minimal_png(width=1, height=1, color_type=2))
    out = workspace / "out.png"

    def _send(_cmd: str, _params: dict[str, Any]) -> dict[str, Any]:
        raise ConnectionError("plugin down")

    from gimp_agent import batch as batch_mod

    monkeypatch.setattr(
        batch_mod,
        "headless_runtime_available",
        lambda: (True, "ok"),
    )

    def _fake_run(job: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        # Simulate export side-effect for HOST verify step
        out.write_bytes(src.read_bytes())
        return {
            "ok": True,
            "steps": [
                {"op": "open_image", "ok": True, "result": {}},
                {"op": "scale_image", "ok": True, "result": {"skipped": True}},
                {
                    "op": "export_image",
                    "ok": True,
                    "result": {"file_path": str(out)},
                },
            ],
        }

    monkeypatch.setattr(batch_mod, "run_headless_job", _fake_run)

    log = recipes.run_recipe(
        "web-export",
        input_path=str(src),
        output_path=str(out),
        session_send=_send,
        backend="auto",
    )
    assert log["ok"] is True
    assert log["backend"] == "headless"
    assert out.is_file()


def test_batch_safe_explicit_headless(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = workspace / "in.png"
    src.write_bytes(build_minimal_png(width=1, height=1, color_type=2))
    out = workspace / "out.png"

    from gimp_agent import batch as batch_mod

    monkeypatch.setattr(batch_mod, "headless_runtime_available", lambda: (True, "ok"))

    def _fake_run(job: dict[str, Any], **_kwargs: Any) -> dict[str, Any]:
        out.write_bytes(src.read_bytes())
        return {
            "ok": True,
            "steps": [
                {
                    "op": s["op"],
                    "ok": True,
                    "result": {"file_path": str(out)} if s["op"] == "export_image" else {},
                }
                for s in job["steps"]
            ],
        }

    monkeypatch.setattr(batch_mod, "run_headless_job", _fake_run)
    log = recipes.run_recipe(
        "web-export",
        input_path=str(src),
        output_path=str(out),
        backend="headless",
    )
    assert log["backend"] == "headless"
    assert log["ok"] is True


def test_interleaved_headless_unsupported(workspace: Path) -> None:
    reg = recipes.RecipeRegistry()
    reg.add(
        {
            "id": "interleaved",
            "version": "1.0.0",
            "title": "interleaved",
            "batch_safe": True,
            "requires_open_session": False,
            "requires_gimp": True,
            "parameters": {
                "input_path": {"type": "path", "required": True},
                "output_path": {"type": "path", "required": True},
            },
            "steps": [
                {"op": "open_image", "with": {"file_path": "$input_path"}},
                {
                    "op": "verify_artifact",
                    "with": {"path": "$input_path", "expected": {"format": "png"}},
                },
                {
                    "op": "export_image",
                    "with": {
                        "file_path": "$output_path",
                        "format": "png",
                        "collision": "fail",
                    },
                },
            ],
        }
    )
    src = workspace / "in.png"
    src.write_bytes(build_minimal_png(width=1, height=1, color_type=2))
    with pytest.raises(sec.GimpMcpError) as ei:
        recipes.run_recipe(
            "interleaved",
            input_path=str(src),
            output_path=str(workspace / "out.png"),
            backend="headless",
            registry=reg,
        )
    assert ei.value.code == sec.CODE_UNSUPPORTED
    assert "contiguous" in ei.value.message.lower()


def test_not_batch_safe_no_tcp_connection_failed(workspace: Path) -> None:
    reg = recipes.RecipeRegistry()
    reg.add(
        {
            "id": "session-only",
            "version": "1.0.0",
            "title": "session only",
            "batch_safe": False,
            "requires_open_session": False,
            "requires_gimp": True,
            "parameters": {
                "input_path": {"type": "path", "required": True},
                "output_path": {"type": "path", "required": True},
            },
            "steps": [
                {"op": "open_image", "with": {"file_path": "$input_path"}},
                {
                    "op": "export_image",
                    "with": {
                        "file_path": "$output_path",
                        "format": "png",
                        "collision": "fail",
                    },
                },
            ],
        }
    )
    src = workspace / "in.png"
    src.write_bytes(build_minimal_png(width=1, height=1, color_type=2))

    def _send(_cmd: str, _params: dict[str, Any]) -> dict[str, Any]:
        raise ConnectionError("plugin down")

    with pytest.raises(sec.GimpMcpError) as ei:
        recipes.run_recipe(
            "session-only",
            input_path=str(src),
            output_path=str(workspace / "out.png"),
            session_send=_send,
            registry=reg,
            backend="auto",
        )
    assert ei.value.code == sec.CODE_CONNECTION_FAILED


def test_batch_safe_no_console_unsupported(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = workspace / "in.png"
    src.write_bytes(build_minimal_png(width=1, height=1, color_type=2))

    from gimp_agent import batch as batch_mod

    monkeypatch.setattr(
        batch_mod,
        "headless_runtime_available",
        lambda: (False, "gimp-console not found"),
    )

    with pytest.raises(sec.GimpMcpError) as ei:
        recipes.run_recipe(
            "web-export",
            input_path=str(src),
            output_path=str(workspace / "out.png"),
            backend="headless",
        )
    assert ei.value.code == sec.CODE_UNSUPPORTED
    assert "gimp-console" in ei.value.message or "unavailable" in ei.value.message


# ---------------------------------------------------------------------------
# exiftool_strip
# ---------------------------------------------------------------------------


def test_missing_exiftool_unsupported(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = workspace / "photo.jpg"
    p.write_bytes(b"fake")
    monkeypatch.setattr(recipes.shutil, "which", lambda _n: None)
    with pytest.raises(sec.GimpMcpError) as ei:
        recipes.run_recipe("exif-strip", params={"path": str(p)})
    assert ei.value.code == sec.CODE_UNSUPPORTED


def test_exiftool_shell_false(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = workspace / "photo.jpg"
    p.write_bytes(b"fake")
    monkeypatch.setattr(recipes.shutil, "which", lambda _n: "/usr/bin/exiftool")
    ran: dict[str, Any] = {}

    def _fake_run(cmd: list[str], **kwargs: Any) -> Any:
        ran["cmd"] = cmd
        ran["kwargs"] = kwargs
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = ""
        return m

    monkeypatch.setattr(recipes.subprocess, "run", _fake_run)
    events: list[dict[str, Any]] = []

    def _audit(event: dict[str, Any], _path: Any = None) -> None:
        events.append(dict(event))

    monkeypatch.setattr(sec, "write_audit_event", _audit)
    log = recipes.run_recipe("exif-strip", params={"path": str(p)})
    assert log["ok"] is True
    assert ran["kwargs"].get("shell") is False
    assert ran["cmd"][1:3] == ["-overwrite_original_in_place", "-all="]
    assert any(e.get("event") == "exiftool_strip" for e in events)


# ---------------------------------------------------------------------------
# Capabilities / surface / packaging
# ---------------------------------------------------------------------------


def test_recipe_library_capability_true() -> None:
    from gimp_mcp_state import default_capabilities

    caps = default_capabilities()
    assert caps["recipe_library"] is True
    assert caps["batch_interpreter"] is True


def test_hl_catalog_exact_28() -> None:
    from gimp_mcp_surface import HL_TOOL_NAMES, get_hl_catalog_names

    names = get_hl_catalog_names()
    assert len(names) == 28
    assert set(names) == HL_TOOL_NAMES
    assert "list_recipes" in HL_TOOL_NAMES
    assert "apply_recipe" in HL_TOOL_NAMES
    assert "apply_nde_filter" in HL_TOOL_NAMES
    assert "undo_group_begin" in HL_TOOL_NAMES
    assert "batch_run" not in HL_TOOL_NAMES


def test_not_in_expected_plugin_files() -> None:
    from gimp_agent import paths as pathmod

    assert "gimp_mcp_recipes.py" not in pathmod.EXPECTED_PLUGIN_FILES


def test_pyproject_registers_recipes() -> None:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "gimp_mcp_recipes" in text
    assert "recipes/*.json" in text


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------


def test_mcp_list_recipes_tool() -> None:
    import gimp_mcp_server as srv

    class _Ctx:
        pass

    fn = getattr(srv.list_recipes, "fn", srv.list_recipes)
    out = fn(_Ctx())  # type: ignore[arg-type]
    assert "recipes" in out
    assert len(out["recipes"]) >= 4
    assert set(out["recipes"][0].keys()) == {
        "id",
        "version",
        "title",
        "batch_safe",
        "requires_open_session",
        "requires_gimp",
    }


def test_mcp_apply_recipe_host(workspace: Path) -> None:
    import gimp_mcp_server as srv

    class _Ctx:
        pass

    png = build_minimal_png(width=2, height=2, color_type=2)
    a = workspace / "a.png"
    b = workspace / "b.png"
    a.write_bytes(png)
    b.write_bytes(png)
    fn = getattr(srv.apply_recipe, "fn", srv.apply_recipe)
    out = fn(  # type: ignore[arg-type]
        _Ctx(),
        recipe_id="compare-artifacts",
        params={"path_a": str(a), "path_b": str(b)},
    )
    assert out["ok"] is True
    assert out["backend"] == "host"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_recipes_json(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["recipes", "--json"])
    assert code == ec.EXIT_SUCCESS
    env = json.loads(capsys.readouterr().out)
    assert env["ok"] is True
    assert len(env["data"]["recipes"]) >= 4


def test_cli_run_compare_artifacts(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    png = build_minimal_png(width=2, height=2, color_type=2)
    a = workspace / "a.png"
    b = workspace / "b.png"
    a.write_bytes(png)
    b.write_bytes(png)
    code = main(
        [
            "run",
            "compare-artifacts",
            "--param",
            f"path_a={a}",
            "--param",
            f"path_b={b}",
            "--json",
        ]
    )
    assert code == ec.EXIT_SUCCESS
    env = json.loads(capsys.readouterr().out)
    assert env["ok"] is True
    assert env["data"]["backend"] == "host"


def test_cli_run_unknown_recipe_exit_12(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["run", "no-such-recipe", "--output", "x.png", "--json"])
    assert code == ec.EXIT_UNSUPPORTED
    env = json.loads(capsys.readouterr().out)
    assert env["code"] == sec.CODE_UNSUPPORTED


def test_cli_batch_continue_on_fail(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Batch two files; one fails verify — report both, nonzero exit."""
    # Host recipe that always succeeds for identical compares; craft a custom
    # batch over compare-artifacts: path_a fixed, path_b is each input via param
    # — compare-artifacts doesn't take input_path. Use a mini mock via patch.
    good = workspace / "good.png"
    good.write_bytes(build_minimal_png(width=1, height=1, color_type=2))
    bad = workspace / "bad.png"
    bad.write_bytes(b"not-a-png")
    out_dir = workspace / "batch_out"
    out_dir.mkdir()

    # Use compare-artifacts with --param path_a=good for both; inputs unused for
    # host compare unless we pass path_b. Batch CLI always sets input_path —
    # for compare-artifacts that is ignored if not in schema... reserved name.
    # Instead: monkeypatch run_recipe to fail on second input.
    calls = {"n": 0}
    real_run = recipes.run_recipe

    def _wrap(*a: Any, **k: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            raise sec.GimpMcpError(sec.CODE_VERIFY_FAILED, "forced fail")
        # First succeeds without GIMP
        return {
            "ok": True,
            "recipe_id": "web-export",
            "version": "1.0.0",
            "backend": "host",
            "steps": [],
            "artifacts": [],
            "created_paths": [],
        }

    with patch.object(recipes, "run_recipe", side_effect=_wrap):
        # Also patch get_recipe so unknown path doesn't matter; use web-export id
        code = main(
            [
                "batch",
                "web-export",
                "--output-dir",
                str(out_dir),
                "--inputs",
                str(good),
                "--inputs",
                str(bad),
                "--json",
            ]
        )
    assert code == ec.EXIT_PARTIAL
    assert code == 10
    env = json.loads(capsys.readouterr().out)
    assert env["exit_code"] == 10
    assert env["code"] == sec.CODE_PARTIAL_MUTATION
    assert env["ok"] is False
    assert env["data"]["total"] == 2
    assert env["data"]["failed"] == 1
    assert env["data"]["results"][0]["ok"] is True
    assert env["data"]["results"][1]["ok"] is False
    _ = real_run  # silence unused if needed


def test_cli_run_bad_param_exit_2(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        [
            "run",
            "compare-artifacts",
            "--param",
            "not_a_pair",
            "--json",
        ]
    )
    assert code == ec.EXIT_CLI_USAGE


# ---------------------------------------------------------------------------
# Codex P1/P2 fixes (batch collision, CLI_USAGE, XOR, defaults, glob)
# ---------------------------------------------------------------------------


def test_params_reserved_input_path_rejected(workspace: Path) -> None:
    """Reserved input_path/handle/output_path must not come from params (XOR safe)."""
    with pytest.raises(sec.GimpMcpError, match="reserved parameter") as ei:
        recipes.run_recipe(
            "transparent-png",
            handle={"image_id": 1, "generation": 1, "session_epoch": 1},
            params={"input_path": str(workspace / "sneak.png")},
            output_path=str(workspace / "out.png"),
            session_send=lambda *_a, **_k: {"status": "success", "results": {}},
        )
    assert ei.value.code == sec.CODE_POLICY_DENIED


def test_params_both_handle_and_input_via_params_rejected(workspace: Path) -> None:
    """Both handle and input_path via params alone must error (reserved rejection)."""
    with pytest.raises(sec.GimpMcpError, match="reserved parameter") as ei:
        recipes.run_recipe(
            "web-export",
            params={
                "handle": {"image_id": 1, "generation": 1, "session_epoch": 1},
                "input_path": str(workspace / "in.png"),
                "output_path": str(workspace / "out.png"),
            },
            session_send=lambda *_a, **_k: {"status": "success", "results": {}},
        )
    assert ei.value.code == sec.CODE_POLICY_DENIED


def test_load_rejects_wrong_typed_param_default(tmp_path: Path) -> None:
    """Load-time fail-closed: default must type-check against declared type."""
    bad = {
        "id": "bad-default",
        "version": "1.0.0",
        "title": "bad default",
        "parameters": {
            "max_mae": {"type": "float", "required": False, "default": "not-a-float"},
        },
        "steps": [{"op": "compare_images", "with": {}}],
    }
    (tmp_path / "bad-default.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(sec.GimpMcpError, match=r"fail-closed|load failed") as ei:
        recipes.load_recipes_from_dir(tmp_path)
    assert ei.value.code == sec.CODE_INTERNAL
    errs = (ei.value.details or {}).get("errors") or []
    assert any("default" in str(e).lower() or "float" in str(e).lower() for e in errs)


def test_load_rejects_default_outside_enum(tmp_path: Path) -> None:
    bad = {
        "id": "bad-enum-default",
        "version": "1.0.0",
        "title": "bad enum default",
        "parameters": {
            "collision": {
                "type": "string",
                "enum": ["fail", "version", "replace"],
                "default": "overwrite",
            },
        },
        "steps": [{"op": "compare_images", "with": {}}],
    }
    (tmp_path / "bad-enum.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(sec.GimpMcpError, match=r"fail-closed|load failed") as ei:
        recipes.load_recipes_from_dir(tmp_path)
    assert ei.value.code == sec.CODE_INTERNAL


def test_cli_batch_host_compare_no_collision_injection(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """P1-1: host-only compare-artifacts batch must not fail on undeclared collision."""
    png = build_minimal_png(width=2, height=2, color_type=2)
    ref = workspace / "ref.png"
    a = workspace / "a.png"
    b = workspace / "b.png"
    ref.write_bytes(png)
    a.write_bytes(png)
    b.write_bytes(png)
    out_dir = workspace / "batch_out"
    out_dir.mkdir()
    code = main(
        [
            "batch",
            "compare-artifacts",
            "--output-dir",
            str(out_dir),
            "--inputs",
            str(a),
            "--inputs",
            str(b),
            "--param",
            f"path_a={ref}",
            "--param",
            f"path_b={ref}",
            "--json",
        ]
    )
    assert code == ec.EXIT_SUCCESS
    env = json.loads(capsys.readouterr().out)
    assert env["ok"] is True
    assert env["data"]["total"] == 2
    assert env["data"]["failed"] == 0
    assert all(r.get("ok") for r in env["data"]["results"])


def test_cli_batch_unknown_param_exit_2(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """P1-2: bad shared params → CLI_USAGE exit 2 (not PARTIAL 10)."""
    png = build_minimal_png(width=1, height=1, color_type=2)
    a = workspace / "a.png"
    a.write_bytes(png)
    out_dir = workspace / "batch_out"
    out_dir.mkdir()
    code = main(
        [
            "batch",
            "compare-artifacts",
            "--output-dir",
            str(out_dir),
            "--inputs",
            str(a),
            "--param",
            "not_a_real_param=1",
            "--json",
        ]
    )
    assert code == ec.EXIT_CLI_USAGE
    assert code == 2
    env = json.loads(capsys.readouterr().out)
    assert env["exit_code"] == 2
    assert env["ok"] is False


def test_cli_batch_one_runtime_fail_exit_10(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """P1-2: per-input runtime failure aggregates to PARTIAL exit 10."""
    png = build_minimal_png(width=2, height=2, color_type=2)
    good = workspace / "good.png"
    bad = workspace / "bad.png"
    good.write_bytes(png)
    bad.write_bytes(b"not-a-png")
    out_dir = workspace / "batch_out"
    out_dir.mkdir()
    # compare-artifacts: path_a fixed good, path_b = each batch input via --param path_b
    # but batch sets the same shared params for every input — so use a mock for
    # second-input runtime failure while first succeeds (real first call).
    calls = {"n": 0}
    real_run = recipes.run_recipe

    def _wrap(*a: Any, **k: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            raise sec.GimpMcpError(sec.CODE_VERIFY_FAILED, "forced runtime fail")
        return real_run(*a, **k)

    with patch.object(recipes, "run_recipe", side_effect=_wrap):
        code = main(
            [
                "batch",
                "compare-artifacts",
                "--output-dir",
                str(out_dir),
                "--inputs",
                str(good),
                "--inputs",
                str(bad),
                "--param",
                f"path_a={good}",
                "--param",
                f"path_b={good}",
                "--json",
            ]
        )
    assert code == ec.EXIT_PARTIAL
    assert code == 10
    env = json.loads(capsys.readouterr().out)
    assert env["code"] == sec.CODE_PARTIAL_MUTATION
    assert env["data"]["total"] == 2
    assert env["data"]["failed"] == 1
    assert env["data"]["results"][0]["ok"] is True
    assert env["data"]["results"][1]["ok"] is False


def test_cli_batch_absolute_input_glob(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """P2-2: absolute --input-glob under workspace must not crash (Windows-safe)."""
    png = build_minimal_png(width=1, height=1, color_type=2)
    sub = workspace / "globdir"
    sub.mkdir()
    f1 = sub / "one.png"
    f1.write_bytes(png)
    ref = workspace / "ref.png"
    ref.write_bytes(png)
    out_dir = workspace / "batch_out"
    out_dir.mkdir()
    # Absolute pattern pointing under workspace (would NotImplementedError on bare Path.glob)
    abs_glob = str(sub / "*.png")
    code = main(
        [
            "batch",
            "compare-artifacts",
            "--output-dir",
            str(out_dir),
            "--input-glob",
            abs_glob,
            "--param",
            f"path_a={ref}",
            "--param",
            f"path_b={ref}",
            "--json",
        ]
    )
    assert code == ec.EXIT_SUCCESS
    env = json.loads(capsys.readouterr().out)
    assert env["data"]["total"] >= 1
    assert env["ok"] is True


def test_expand_batch_input_glob_relative(workspace: Path) -> None:
    from gimp_agent.cli import _expand_batch_input_glob

    sub = workspace / "g"
    sub.mkdir()
    (sub / "x.png").write_bytes(build_minimal_png(width=1, height=1, color_type=2))
    found = _expand_batch_input_glob("g/*.png")
    assert len(found) == 1
    assert Path(found[0]).name == "x.png"
