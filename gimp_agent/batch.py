"""Host-side constrained BatchProcedure launcher (track 0019).

Not an EXPECTED plug-in ship file. Pure helpers: job schema, gimp-console argv,
headless subprocess with result-file SoT. Never uses python-fu-eval.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import gimp_mcp_security as sec
from gimp_agent import paths as pathmod
from gimp_mcp_recipes import GIMP_OPS, HOST_OPS

# ---------------------------------------------------------------------------
# Constants (locked — AI1 B1)
# ---------------------------------------------------------------------------

PROCEDURE_NAME = "plug-in-gimp-mcp-batch"
LABEL = "gimp-mcp-recipe"
DEFAULT_TIMEOUT_S = 120
ENV_BATCH_TIMEOUT = "GIMP_MCP_BATCH_TIMEOUT_S"
ENV_BATCH_MODE = "GIMP_MCP_BATCH_MODE"
MAX_JOB_BYTES = 256 * 1024
MAX_STEPS = 32
JOB_VERSION = 1
TMP_DIR_NAME = ".gimp-mcp-tmp"
PRUNE_AGE_S = 3600.0

# Reject freeform/code keys anywhere in job JSON (v1 allowlist only).
FORBIDDEN_KEYS: frozenset[str] = frozenset({"script", "python", "eval", "cmds", "code"})

_PATH_KEYS: frozenset[str] = frozenset(
    {
        "file_path",
        "path",
        "path_a",
        "path_b",
        "write_diff_path",
        "diff_out",
        "input_path",
        "output_path",
    }
)


def batch_timeout_s(override: float | None = None) -> float:
    """Wall-clock timeout seconds; env clamp 15…3600, default 120."""
    if override is not None:
        try:
            val = float(override)
            if val == val and val > 0:  # finite positive
                return max(15.0, min(3600.0, val))
        except (TypeError, ValueError):
            pass
    raw = os.environ.get(ENV_BATCH_TIMEOUT, "")
    if raw and str(raw).strip():
        try:
            val = float(str(raw).strip())
            if val == val and val > 0:
                return max(15.0, min(3600.0, val))
        except ValueError:
            pass
    return float(DEFAULT_TIMEOUT_S)


def _contains_forbidden_keys(obj: Any) -> str | None:
    """Return first forbidden key found, or None."""
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key in FORBIDDEN_KEYS:
                return str(key)
            found = _contains_forbidden_keys(val)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _contains_forbidden_keys(item)
            if found is not None:
                return found
    return None


def validate_job(job: Any) -> dict[str, Any]:
    """Validate job schema v1. Returns the job dict or raises GimpMcpError."""
    if not isinstance(job, dict):
        raise sec.GimpMcpError(
            sec.CODE_POLICY_DENIED,
            "batch job must be a JSON object",
            details={},
        )
    forbidden = _contains_forbidden_keys(job)
    if forbidden is not None:
        raise sec.GimpMcpError(
            sec.CODE_POLICY_DENIED,
            f"batch job rejects freeform key {forbidden!r}",
            details={"key": forbidden},
        )
    ver = job.get("v")
    if ver != JOB_VERSION:
        raise sec.GimpMcpError(
            sec.CODE_UNSUPPORTED,
            f"batch job version must be {JOB_VERSION}, got {ver!r}",
            details={"v": ver},
        )
    steps = job.get("steps")
    if not isinstance(steps, list):
        raise sec.GimpMcpError(
            sec.CODE_POLICY_DENIED,
            "batch job steps must be a list",
            details={},
        )
    if len(steps) > MAX_STEPS:
        raise sec.GimpMcpError(
            sec.CODE_POLICY_DENIED,
            f"batch job exceeds max steps ({MAX_STEPS})",
            details={"steps": len(steps), "max": MAX_STEPS},
        )
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise sec.GimpMcpError(
                sec.CODE_POLICY_DENIED,
                f"batch job step {i} must be an object",
                details={"index": i},
            )
        op = step.get("op")
        if not isinstance(op, str) or op not in GIMP_OPS:
            raise sec.GimpMcpError(
                sec.CODE_POLICY_DENIED,
                f"batch job step {i} op must be a GIMP_OPS allowlisted op, got {op!r}",
                details={"index": i, "op": op},
            )
        with_map = step.get("with", {})
        if with_map is None:
            with_map = {}
        if not isinstance(with_map, dict):
            raise sec.GimpMcpError(
                sec.CODE_POLICY_DENIED,
                f"batch job step {i} with must be an object",
                details={"index": i},
            )
    # Size cap on compact serialization
    try:
        raw = json.dumps(job, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise sec.GimpMcpError(
            sec.CODE_POLICY_DENIED,
            f"batch job is not JSON-serializable: {exc}",
            details={},
        ) from exc
    if len(raw.encode("utf-8")) > MAX_JOB_BYTES:
        raise sec.GimpMcpError(
            sec.CODE_POLICY_DENIED,
            f"batch job exceeds max size ({MAX_JOB_BYTES} bytes)",
            details={"bytes": len(raw.encode("utf-8")), "max": MAX_JOB_BYTES},
        )
    return job


def _forward_slash_paths(data: dict[str, Any]) -> dict[str, Any]:
    """Copy mapping; convert known path string values to forward-slash form."""
    out = dict(data)
    for key in list(out.keys()):
        val = out[key]
        if key in _PATH_KEYS and isinstance(val, str) and val:
            out[key] = val.replace("\\", "/")
        elif isinstance(val, dict):
            # Nested expected etc. — only path-like keys at this level matter for jobs
            nested = dict(val)
            for nk, nv in list(nested.items()):
                if nk in _PATH_KEYS and isinstance(nv, str) and nv:
                    nested[nk] = nv.replace("\\", "/")
            out[key] = nested
    return out


def build_job_from_recipe_steps(
    recipe_id: str,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a v1 path-based job from already-interpolated GIMP recipe steps."""
    out_steps: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        op = str(step.get("op"))
        if op not in GIMP_OPS:
            raise sec.GimpMcpError(
                sec.CODE_POLICY_DENIED,
                f"cannot put non-GIMP op {op!r} in batch job",
                details={"op": op},
            )
        with_raw = step.get("with") or {}
        if not isinstance(with_raw, dict):
            with_raw = {}
        out_steps.append({"op": op, "with": _forward_slash_paths(dict(with_raw))})
    job: dict[str, Any] = {
        "v": JOB_VERSION,
        "recipe_id": str(recipe_id),
        "steps": out_steps,
    }
    return validate_job(job)


def headless_eligible(recipe: dict[str, Any]) -> bool:
    """True when recipe is batch_safe with contiguous GIMP_OPS then HOST_OPS.

    No GIMP op may appear after a HOST op (v1 single job).
    """
    if not bool(recipe.get("batch_safe", False)):
        return False
    steps = recipe.get("steps") or []
    if not isinstance(steps, list):
        return False
    seen_host = False
    has_gimp = False
    for step in steps:
        if not isinstance(step, dict):
            return False
        op = step.get("op")
        if not isinstance(op, str):
            return False
        if op in HOST_OPS:
            seen_host = True
        elif op in GIMP_OPS:
            if seen_host:
                return False
            has_gimp = True
        else:
            return False
    return has_gimp


def headless_runtime_available() -> tuple[bool, str]:
    """Check console binary + plugin entrypoint present (static, not live PDB)."""
    console = pathmod.find_gimp_console()
    if console is None:
        return False, "gimp-console not found"
    plugin_dir = pathmod.find_plugin_dir()
    if plugin_dir is None:
        return False, "GIMP plug-in config dir not found"
    entry = plugin_dir / "gimp-mcp-plugin.py"
    if not entry.is_file():
        return False, f"plugin not installed ({entry})"
    return True, "ok"


def result_path_for(job_path: Path | str) -> Path:
    """Sibling result file: ``<stem>.result.json`` next to the job file."""
    p = Path(job_path)
    return p.with_name(f"{p.stem}.result.json")


def prune_old_jobs(tmp_dir: Path, *, max_age_s: float = PRUNE_AGE_S) -> int:
    """Delete job/result files older than max_age_s. Returns count removed."""
    if not tmp_dir.is_dir():
        return 0
    now = time.time()
    removed = 0
    try:
        entries = list(tmp_dir.iterdir())
    except OSError:
        return 0
    for entry in entries:
        if not entry.is_file():
            continue
        if entry.suffix not in (".json",) and not entry.name.endswith(".result.json"):
            # still allow *.json and *result.json
            if not entry.name.endswith(".json"):
                continue
        try:
            age = now - entry.stat().st_mtime
        except OSError:
            continue
        if age > max_age_s:
            try:
                entry.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def write_job_file(
    job: dict[str, Any],
    *,
    workspace_root: Path | str | None = None,
) -> Path:
    """Write validated job under workspace ``.gimp-mcp-tmp/<uuid>.json``."""
    validate_job(job)
    root = Path(workspace_root) if workspace_root is not None else sec.workspace_root()
    if root is None:
        raise sec.GimpMcpError(
            sec.CODE_POLICY_DENIED,
            f"batch job requires {sec.ENV_WORKSPACE}",
            details={},
        )
    root = Path(root)
    tmp_dir = root / TMP_DIR_NAME
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise sec.GimpMcpError(
            sec.CODE_INTERNAL,
            f"cannot create batch tmp dir: {exc}",
            details={"path": str(tmp_dir)},
        ) from exc
    prune_old_jobs(tmp_dir)
    job_path = tmp_dir / f"{uuid.uuid4()}.json"
    # Prefer forward-slash absolute path in job file content for paths already set
    payload = json.dumps(job, separators=(",", ":"), ensure_ascii=False)
    try:
        job_path.write_text(payload, encoding="utf-8")
    except OSError as exc:
        raise sec.GimpMcpError(
            sec.CODE_INTERNAL,
            f"cannot write batch job file: {exc}",
            details={"path": str(job_path)},
        ) from exc
    return job_path


def build_run_job_payload(job_path: Path | str) -> str:
    """Compact JSON for ``-b``: ``{"v":1,"op":"run_job","job":"..."}``."""
    path_text = str(job_path).replace("\\", "/")
    return json.dumps(
        {"v": 1, "op": "run_job", "job": path_text},
        separators=(",", ":"),
        ensure_ascii=False,
    )


def build_ping_payload() -> str:
    return json.dumps({"v": 1, "op": "ping"}, separators=(",", ":"), ensure_ascii=False)


def build_console_argv(
    console: Path | str,
    batch_payload: str,
) -> list[str]:
    """Absolute gimp-console argv with procedure interpreter + ``--quit``."""
    exe = Path(console)
    if not exe.is_absolute():
        exe = exe.resolve()
    return [
        str(exe),
        "-i",
        "-d",
        "-f",
        "-c",
        "--batch-interpreter",
        PROCEDURE_NAME,
        "-b",
        batch_payload,
        "--quit",
    ]


def filtered_batch_env(
    *,
    workspace_root: Path | str,
    base: dict[str, str] | None = None,
) -> dict[str, str]:
    """Child env: BATCH_MODE=1, workspace set; strip ALLOW_EXEC and TOKEN."""
    env = dict(base if base is not None else os.environ)
    env[ENV_BATCH_MODE] = "1"
    env[sec.ENV_WORKSPACE] = str(workspace_root)
    env.pop(sec.ENV_ALLOW_EXEC, None)
    env.pop(sec.ENV_TOKEN, None)
    return env


def _cleanup_job_files(job_path: Path, result_path: Path) -> None:
    for p in (job_path, result_path):
        try:
            if p.is_file():
                p.unlink()
        except OSError:
            pass


def run_headless_job(
    job: dict[str, Any],
    *,
    workspace_root: Path | str | None = None,
    console: Path | str | None = None,
    timeout_s: float | None = None,
    env: dict[str, str] | None = None,
    runner: Any | None = None,
) -> dict[str, Any]:
    """Write job, launch gimp-console, read result file (not stdout).

    ``runner`` is injectable for tests: ``(argv, env, timeout) -> CompletedProcess-like``.
    On success, deletes job + result. On failure, leaves them for debug.
    """
    root = Path(workspace_root) if workspace_root is not None else sec.workspace_root()
    if root is None:
        raise sec.GimpMcpError(
            sec.CODE_POLICY_DENIED,
            f"headless batch requires {sec.ENV_WORKSPACE}",
            details={},
        )
    root = Path(root)
    validate_job(job)
    job_path = write_job_file(job, workspace_root=root)
    result_path = result_path_for(job_path)

    exe = Path(console) if console is not None else pathmod.find_gimp_console()
    if exe is None:
        raise sec.GimpMcpError(
            sec.CODE_UNSUPPORTED,
            "gimp-console not found for headless batch",
            details={},
        )

    payload = build_run_job_payload(job_path)
    argv = build_console_argv(exe, payload)
    child_env = filtered_batch_env(workspace_root=root, base=env)
    timeout = batch_timeout_s(timeout_s)

    run_fn = runner if runner is not None else _default_subprocess_run
    try:
        completed = run_fn(argv, child_env, timeout)
    except subprocess.TimeoutExpired as exc:
        # Leave job/result for debug
        raise sec.GimpMcpError(
            sec.CODE_TIMEOUT,
            f"headless batch timed out after {timeout}s",
            details={
                "timeout_s": timeout,
                "job_path": str(job_path),
                "result_path": str(result_path),
            },
        ) from exc
    except OSError as exc:
        raise sec.GimpMcpError(
            sec.CODE_INTERNAL,
            f"failed to launch gimp-console: {exc}",
            details={"argv0": argv[0] if argv else None},
        ) from exc

    # Result file is authoritative (H2) — ignore noisy stdout/stderr.
    if not result_path.is_file():
        raise sec.GimpMcpError(
            sec.CODE_INTERNAL,
            "headless batch exited without result file",
            details={
                "job_path": str(job_path),
                "result_path": str(result_path),
                "returncode": getattr(completed, "returncode", None),
            },
        )

    try:
        text = result_path.read_text(encoding="utf-8")
        result = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise sec.GimpMcpError(
            sec.CODE_INTERNAL,
            f"cannot read headless batch result file: {exc}",
            details={"result_path": str(result_path)},
        ) from exc

    if not isinstance(result, dict):
        raise sec.GimpMcpError(
            sec.CODE_INTERNAL,
            "headless batch result file is not a JSON object",
            details={"result_path": str(result_path)},
        )

    ok = bool(result.get("ok", False))
    if not ok:
        code = result.get("code") or sec.CODE_INTERNAL
        if not isinstance(code, str):
            code = sec.CODE_INTERNAL
        message = str(result.get("error") or result.get("message") or "headless batch failed")
        details = {k: v for k, v in result.items() if k not in ("ok", "error", "message", "code")}
        details["job_path"] = str(job_path)
        details["result_path"] = str(result_path)
        raise sec.GimpMcpError(code, message, details=details)

    _cleanup_job_files(job_path, result_path)
    return result


def _default_subprocess_run(
    argv: list[str],
    env: dict[str, str],
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Launch gimp-console; kill on timeout (Windows: proc.kill)."""
    try:
        return subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run already kills on timeout on modern Python; re-raise
        raise
