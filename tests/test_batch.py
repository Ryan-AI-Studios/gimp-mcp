"""Offline tests for track 0019 constrained BatchProcedure host launcher."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import gimp_mcp_security as sec
from gimp_agent import batch as batch_mod


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(sec.ENV_WORKSPACE, str(tmp_path))
    return tmp_path


def _valid_job(**overrides: Any) -> dict[str, Any]:
    job: dict[str, Any] = {
        "v": 1,
        "recipe_id": "web-export",
        "steps": [
            {"op": "open_image", "with": {"file_path": "C:/ws/in.png"}},
            {
                "op": "export_image",
                "with": {
                    "file_path": "C:/ws/out.png",
                    "format": "png",
                    "preserve_alpha": True,
                    "collision": "fail",
                    "verify": True,
                },
            },
        ],
    }
    job.update(overrides)
    return job


# ---------------------------------------------------------------------------
# validate_job
# ---------------------------------------------------------------------------


def test_validate_job_ok() -> None:
    assert batch_mod.validate_job(_valid_job())["v"] == 1


def test_validate_rejects_host_ops() -> None:
    job = _valid_job(
        steps=[
            {"op": "open_image", "with": {"file_path": "C:/ws/in.png"}},
            {"op": "verify_artifact", "with": {"path": "C:/ws/out.png"}},
        ]
    )
    with pytest.raises(sec.GimpMcpError) as ei:
        batch_mod.validate_job(job)
    assert ei.value.code == sec.CODE_POLICY_DENIED
    assert "GIMP_OPS" in ei.value.message or "verify_artifact" in ei.value.message


def test_validate_rejects_eval_keys() -> None:
    job = _valid_job(script="print(1)")
    with pytest.raises(sec.GimpMcpError) as ei:
        batch_mod.validate_job(job)
    assert ei.value.code == sec.CODE_POLICY_DENIED
    assert "script" in ei.value.message

    job2 = _valid_job(steps=[{"op": "open_image", "with": {"file_path": "x", "python": "evil()"}}])
    with pytest.raises(sec.GimpMcpError) as ei2:
        batch_mod.validate_job(job2)
    assert ei2.value.code == sec.CODE_POLICY_DENIED


def test_validate_rejects_v2() -> None:
    with pytest.raises(sec.GimpMcpError) as ei:
        batch_mod.validate_job(_valid_job(v=2))
    assert ei.value.code == sec.CODE_UNSUPPORTED


def test_validate_rejects_oversized() -> None:
    steps = [
        {"op": "open_image", "with": {"file_path": "C:/ws/" + ("x" * 200) + f"{i}.png"}}
        for i in range(40)
    ]
    # Also exceed step cap first
    with pytest.raises(sec.GimpMcpError) as ei:
        batch_mod.validate_job(_valid_job(steps=steps[:33]))
    assert ei.value.code == sec.CODE_POLICY_DENIED

    # Size: pad with huge path under step cap
    huge = _valid_job(
        steps=[
            {
                "op": "open_image",
                "with": {"file_path": "C:/ws/" + ("Z" * (256 * 1024))},
            }
        ]
    )
    with pytest.raises(sec.GimpMcpError) as ei2:
        batch_mod.validate_job(huge)
    assert ei2.value.code == sec.CODE_POLICY_DENIED


# ---------------------------------------------------------------------------
# argv / procedure name lock
# ---------------------------------------------------------------------------


def test_argv_has_procedure_not_label() -> None:
    argv = batch_mod.build_console_argv(
        Path("C:/Program Files/GIMP 3/bin/gimp-console-3.2.exe"),
        batch_mod.build_run_job_payload("C:/ws/.gimp-mcp-tmp/abc.json"),
    )
    assert "--batch-interpreter" in argv
    idx = argv.index("--batch-interpreter")
    assert argv[idx + 1] == batch_mod.PROCEDURE_NAME
    assert batch_mod.PROCEDURE_NAME == "plug-in-gimp-mcp-batch"
    assert batch_mod.LABEL not in argv
    assert "gimp-mcp-recipe" not in argv
    assert "--quit" in argv
    assert argv[-1] == "--quit"
    assert "-b" in argv
    assert "-i" in argv and "-d" in argv and "-f" in argv and "-c" in argv
    # list argv — no shell quoting of whole command
    assert all(isinstance(a, str) for a in argv)


def test_result_path_for_sibling() -> None:
    p = Path("C:/ws/.gimp-mcp-tmp/deadbeef.json")
    assert batch_mod.result_path_for(p) == Path("C:/ws/.gimp-mcp-tmp/deadbeef.result.json")


def test_build_job_forward_slash_paths(workspace: Path) -> None:
    win_path = str(workspace / "in.png")
    # force backslashes on Windows
    with_bs = win_path.replace("/", "\\")
    job = batch_mod.build_job_from_recipe_steps(
        "web-export",
        [{"op": "open_image", "with": {"file_path": with_bs}}],
    )
    fp = job["steps"][0]["with"]["file_path"]
    assert "\\" not in fp
    assert "/" in fp or fp.startswith("C:")


# ---------------------------------------------------------------------------
# headless_eligible
# ---------------------------------------------------------------------------


def test_interleaved_not_headless_eligible() -> None:
    recipe = {
        "batch_safe": True,
        "steps": [
            {"op": "open_image", "with": {}},
            {"op": "verify_artifact", "with": {}},
            {"op": "export_image", "with": {}},
        ],
    }
    assert batch_mod.headless_eligible(recipe) is False


def test_contiguous_gimp_then_host_eligible() -> None:
    recipe = {
        "batch_safe": True,
        "steps": [
            {"op": "open_image", "with": {}},
            {"op": "export_image", "with": {}},
            {"op": "verify_artifact", "with": {}},
        ],
    }
    assert batch_mod.headless_eligible(recipe) is True


def test_not_batch_safe_not_eligible() -> None:
    recipe = {
        "batch_safe": False,
        "steps": [{"op": "open_image", "with": {}}],
    }
    assert batch_mod.headless_eligible(recipe) is False


# ---------------------------------------------------------------------------
# run_headless_job
# ---------------------------------------------------------------------------


def test_default_subprocess_run_uses_shell_false(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Host launcher must never invoke cmd.exe via shell=True (AI2 BS1)."""
    seen: dict[str, Any] = {}

    def _fake_run(*args: Any, **kwargs: Any) -> Any:
        seen["args"] = args
        seen["kwargs"] = kwargs
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = ""
        return m

    monkeypatch.setattr(subprocess, "run", _fake_run)
    batch_mod._default_subprocess_run(
        [str(workspace / "fake-console"), "-b", "{}"],
        env={"GIMP_WORKSPACE_ROOT": str(workspace)},
        timeout=5.0,
    )
    assert seen["kwargs"].get("shell") is False
    assert isinstance(seen["args"][0], list)


def test_timeout_maps_to_code_timeout(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_console = workspace / "gimp-console.exe"
    fake_console.write_bytes(b"")

    def _timeout(_argv: list[str], _env: dict[str, str], _timeout: float) -> Any:
        raise subprocess.TimeoutExpired(cmd=_argv, timeout=_timeout)

    with pytest.raises(sec.GimpMcpError) as ei:
        batch_mod.run_headless_job(
            _valid_job(),
            workspace_root=workspace,
            console=fake_console,
            timeout_s=15,
            runner=_timeout,
        )
    assert ei.value.code == sec.CODE_TIMEOUT


def test_result_file_sot_ignores_noisy_stdout(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_console = workspace / "gimp-console.exe"
    fake_console.write_bytes(b"")
    captured: dict[str, Any] = {}

    def _runner(argv: list[str], env: dict[str, str], timeout: float) -> Any:
        captured["argv"] = argv
        captured["env"] = env
        # Locate job path from -b payload
        b_idx = argv.index("-b")
        payload = json.loads(argv[b_idx + 1])
        job_path = Path(payload["job"])
        result_path = batch_mod.result_path_for(job_path)
        result_path.write_text(
            json.dumps(
                {
                    "ok": True,
                    "procedure": batch_mod.PROCEDURE_NAME,
                    "steps": [{"op": "open_image", "ok": True}],
                }
            ),
            encoding="utf-8",
        )
        m = MagicMock()
        m.returncode = 0
        m.stdout = "GLib-GObject-WARNING **: noisy\nnot-json\n"
        m.stderr = "more noise"
        return m

    result = batch_mod.run_headless_job(
        _valid_job(),
        workspace_root=workspace,
        console=fake_console,
        runner=_runner,
    )
    assert result["ok"] is True
    assert captured["argv"][captured["argv"].index("--batch-interpreter") + 1] == (
        batch_mod.PROCEDURE_NAME
    )
    # cleanup on success
    tmp = workspace / batch_mod.TMP_DIR_NAME
    remaining = list(tmp.glob("*.json")) if tmp.is_dir() else []
    assert remaining == []


def test_missing_result_code_internal(workspace: Path) -> None:
    fake_console = workspace / "gimp-console.exe"
    fake_console.write_bytes(b"")

    def _runner(_argv: list[str], _env: dict[str, str], _timeout: float) -> Any:
        m = MagicMock()
        m.returncode = 0
        m.stdout = '{"ok":true}'  # stdout must NOT be trusted
        m.stderr = ""
        return m

    with pytest.raises(sec.GimpMcpError) as ei:
        batch_mod.run_headless_job(
            _valid_job(),
            workspace_root=workspace,
            console=fake_console,
            runner=_runner,
        )
    assert ei.value.code == sec.CODE_INTERNAL
    assert "result file" in ei.value.message.lower()


def test_env_lacks_allow_exec(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_console = workspace / "gimp-console.exe"
    fake_console.write_bytes(b"")
    monkeypatch.setenv(sec.ENV_ALLOW_EXEC, "1")
    monkeypatch.setenv(sec.ENV_TOKEN, "secret-token")
    seen_env: dict[str, str] = {}

    def _runner(_argv: list[str], env: dict[str, str], _timeout: float) -> Any:
        seen_env.update(env)
        # write ok result so we complete
        # find job via tmp dir
        tmp = workspace / batch_mod.TMP_DIR_NAME
        jobs = list(tmp.glob("*.json"))
        assert jobs
        job_path = jobs[0]
        # skip result files
        job_path = next(p for p in jobs if not p.name.endswith(".result.json"))
        batch_mod.result_path_for(job_path).write_text(json.dumps({"ok": True}), encoding="utf-8")
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = ""
        return m

    batch_mod.run_headless_job(
        _valid_job(),
        workspace_root=workspace,
        console=fake_console,
        runner=_runner,
    )
    assert sec.ENV_ALLOW_EXEC not in seen_env
    assert sec.ENV_TOKEN not in seen_env
    assert seen_env.get(batch_mod.ENV_BATCH_MODE) == "1"
    assert seen_env.get(sec.ENV_WORKSPACE) == str(workspace)


def test_filtered_batch_env_strips(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    monkeypatch.setenv(sec.ENV_ALLOW_EXEC, "1")
    monkeypatch.setenv(sec.ENV_TOKEN, "tok")
    env = batch_mod.filtered_batch_env(workspace_root=workspace)
    assert sec.ENV_ALLOW_EXEC not in env
    assert sec.ENV_TOKEN not in env
    assert env[batch_mod.ENV_BATCH_MODE] == "1"


def test_write_job_file_under_tmp(workspace: Path) -> None:
    path = batch_mod.write_job_file(_valid_job(), workspace_root=workspace)
    assert path.parent.name == batch_mod.TMP_DIR_NAME
    assert path.suffix == ".json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["v"] == 1
