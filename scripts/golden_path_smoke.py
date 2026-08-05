#!/usr/bin/env python3
"""Golden-path smoke: plugin TCP wire names only; default dry-run; optional --live.

Product path (track 0027):
  get_gimp_info → open_image → orient_workspace → ensure_source_immutable
  → checkpoint_create → [optional select_all / select_none] → get_image_bitmap
  → save_xcf → export_image → host verify_artifact → evidence.json

Never sends create_selection / render_visible_composite as plugin wire types.
Never requires Class A cmds / GIMP_MCP_ALLOW_EXEC / python-fu-eval / call_api.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Repo root on sys.path when invoked as ``python scripts/golden_path_smoke.py``
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import gimp_mcp_security as sec  # noqa: E402
from gimp_agent import __version__ as package_version  # noqa: E402
from gimp_agent import exit_codes as ec  # noqa: E402
from gimp_agent.probe import send_authenticated_command  # noqa: E402
from gimp_mcp_verify import verify_artifact  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_GIMP_VERSION = "3.2.4"
DEFAULT_TIMEOUT_S = 60.0
TIMEOUT_MIN_S = 5.0
TIMEOUT_MAX_S = 600.0
FIXTURE_REL = Path("tests") / "fixtures" / "rgb_2x2_opaque.png"
FIXTURE_WIDTH = 2
FIXTURE_HEIGHT = 2
CHECKPOINT_LABEL = "golden-path"
PRODUCT_NAME = "gimp-mcp"

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_DOTTED_INT = re.compile(r"^\d+(\.\d+)*$")

# Ordered wire plan printed by dry-run / used as documentation of live steps
WIRE_PLAN: list[tuple[int, str, str]] = [
    (1, "probe", "get_gimp_info"),
    (2, "open", "open_image"),
    (3, "orient", "orient_workspace"),
    (4, "protect", "ensure_source_immutable"),
    (5, "checkpoint", "checkpoint_create"),
    (6, "optional_selection", "select_all then select_none"),
    (7, "composite", "get_image_bitmap"),
    (8, "save_xcf", "save_xcf"),
    (9, "export", "export_image"),
    (10, "host_verify", "verify_artifact (host)"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _env_truthy(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in _TRUTHY


def _clamp_timeout(raw: float) -> float:
    return max(TIMEOUT_MIN_S, min(TIMEOUT_MAX_S, float(raw)))


def _version_tuple(s: str) -> tuple[int, ...] | None:
    text = str(s).strip()
    if not text or not _DOTTED_INT.match(text):
        return None
    return tuple(int(p) for p in text.split("."))


def _version_at_least(live: str | None, minimum: str) -> bool:
    """Fail-closed version compare: True only when both parse and live >= min."""
    live_t = _version_tuple(live or "")
    min_t = _version_tuple(minimum)
    if live_t is None or min_t is None:
        return False
    n = max(len(live_t), len(min_t))
    live_p = live_t + (0,) * (n - len(live_t))
    min_p = min_t + (0,) * (n - len(min_t))
    return live_p >= min_p


def _resolve_workspace(explicit: str | None) -> Path:
    if explicit and str(explicit).strip():
        return Path(str(explicit).strip()).resolve()
    root = sec.workspace_root()
    if root is None:
        raise sec.SecurityError(
            sec.CODE_PATH_DENIED,
            f"Workspace required: set {sec.ENV_WORKSPACE} or pass --workspace",
        )
    return Path(root).resolve()


def _resolve_out_dir(workspace: Path, out_dir: str | None) -> Path:
    """Resolve --out-dir under workspace jail (M2)."""
    if out_dir is None or not str(out_dir).strip():
        candidate = workspace / "output" / "golden-path"
    else:
        candidate = Path(str(out_dir).strip())
        if not candidate.is_absolute():
            candidate = workspace / candidate
    try:
        resolved = sec.resolve_under_root(candidate, root=workspace)
    except sec.SecurityError:
        raise
    return resolved


def _fixture_path() -> Path:
    return (_ROOT / FIXTURE_REL).resolve()


def _extract_gimp_version(result: dict[str, Any]) -> str | None:
    results = result.get("results")
    if not isinstance(results, dict):
        return None
    version = results.get("version")
    if isinstance(version, dict):
        for key in ("detected_version", "version_method", "VERSION"):
            val = version.get(key)
            if val is not None and str(val) and str(val) != "Unknown":
                return str(val)
        major = version.get("major_version")
        minor = version.get("minor_version")
        micro = version.get("micro_version")
        if major is not None and minor is not None:
            if micro is not None:
                return f"{major}.{minor}.{micro}"
            return f"{major}.{minor}"
    if isinstance(version, str) and version:
        return version
    # Some payloads nest under results.gimp / top-level
    for key in ("gimp_version", "plugin_version"):
        val = results.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def _extract_plugin_version(result: dict[str, Any]) -> str | None:
    results = result.get("results")
    if not isinstance(results, dict):
        return None
    for key in ("plugin_version", "mcp_plugin_version"):
        val = results.get(key)
        if isinstance(val, str) and val:
            return val
    version = results.get("version")
    if isinstance(version, dict):
        for key in ("plugin_version", "mcp_version"):
            val = version.get(key)
            if isinstance(val, str) and val:
                return val
    return None


def _require_success(result: dict[str, Any], step: str) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise RuntimeError(f"{step}: non-dict plugin response")
    code = result.get("code")
    if isinstance(code, str) and code == sec.CODE_AUTH_FAILED:
        raise RuntimeError(f"{sec.CODE_AUTH_FAILED}: {result.get('error') or step}")
    if result.get("status") == "error" or (
        result.get("status") is not None and result.get("status") != "success"
    ):
        err = result.get("error") or result.get("message") or "plugin error"
        if isinstance(code, str) and code:
            raise RuntimeError(f"{code}: {step}: {err}")
        raise RuntimeError(f"{step}: {err}")
    if result.get("status") != "success":
        raise RuntimeError(f"{step}: unexpected status {result.get('status')!r}")
    results = result.get("results")
    return results if isinstance(results, dict) else {}


def _handle_params(handle: Any | None) -> dict[str, Any]:
    if handle is None:
        return {}
    return {"handle": handle}


def _emit(
    *,
    ok: bool,
    exit_code: int,
    code: str | None,
    message: str,
    data: dict[str, Any] | None,
    as_json: bool,
    human_lines: list[str] | None = None,
) -> int:
    envelope = {
        "ok": bool(ok),
        "exit_code": int(exit_code),
        "code": code,
        "message": message,
        "data": data if data is not None else {},
    }
    if as_json:
        sys.stdout.write(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n")
    else:
        lines = human_lines if human_lines is not None else []
        if not lines:
            status = "ok" if ok else "fail"
            head = f"[{status}] exit={exit_code}"
            if code:
                head += f" code={code}"
            lines = [head]
            if message:
                lines.append(message)
        text = "\n".join(lines)
        if text and not text.endswith("\n"):
            text += "\n"
        sys.stdout.write(text)
    return exit_code


def _map_exception(exc: BaseException) -> tuple[str, int, str]:
    """Map transport/auth/policy exceptions to (code, exit, message)."""
    if isinstance(exc, sec.SecurityError):
        return exc.code, ec.exit_code_for(exc.code), exc.message
    if isinstance(exc, TimeoutError):
        return sec.CODE_TIMEOUT, ec.exit_code_for(sec.CODE_TIMEOUT), str(exc)
    if isinstance(exc, (ConnectionError, OSError)):
        return (
            sec.CODE_CONNECTION_FAILED,
            ec.exit_code_for(sec.CODE_CONNECTION_FAILED),
            f"connection failed: {exc}",
        )
    if isinstance(exc, RuntimeError):
        text = str(exc)
        if sec.CODE_AUTH_FAILED in text:
            return (
                sec.CODE_AUTH_FAILED,
                ec.exit_code_for(sec.CODE_AUTH_FAILED),
                text,
            )
        if sec.CODE_CONNECTION_FAILED in text:
            return (
                sec.CODE_CONNECTION_FAILED,
                ec.exit_code_for(sec.CODE_CONNECTION_FAILED),
                text,
            )
        # Prefer leading CODE_* when present
        m = re.match(r"^([A-Z][A-Z0-9_]+):\s*(.*)$", text)
        if m:
            code = m.group(1)
            return code, ec.exit_code_for(code), text
        return sec.CODE_INTERNAL, ec.exit_code_for(sec.CODE_INTERNAL), text
    return sec.CODE_INTERNAL, ec.EXIT_INTERNAL, f"{type(exc).__name__}: {exc}"


def _write_evidence(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


def run_dry_run(
    *,
    workspace: Path,
    out_dir: Path,
    as_json: bool,
) -> int:
    fixture = _fixture_path()
    if not fixture.is_file():
        return _emit(
            ok=False,
            exit_code=ec.EXIT_GENERIC,
            code=None,
            message=f"fixture missing: {fixture}",
            data={"fixture": str(fixture)},
            as_json=as_json,
        )
    if fixture.stat().st_size <= 0:
        return _emit(
            ok=False,
            exit_code=ec.EXIT_GENERIC,
            code=None,
            message=f"fixture empty: {fixture}",
            data={"fixture": str(fixture)},
            as_json=as_json,
        )

    plan_lines = [
        "golden-path smoke dry-run (no socket)",
        f"  fixture:   {fixture} ({fixture.stat().st_size} bytes)",
        f"  workspace: {workspace}",
        f"  out-dir:   {out_dir}",
        "  wire plan:",
    ]
    for step, name, wire in WIRE_PLAN:
        plan_lines.append(f"    {step:2d}. {name:20s}  wire={wire}")
    plan_lines.append(
        "  note: smoke never sends create_selection / render_visible_composite as wire types"
    )
    plan_lines.append("  Class A ban: no cmds / ALLOW_EXEC / python-fu-eval / call_api")

    data = {
        "mode": "dry-run",
        "fixture": str(fixture),
        "workspace": str(workspace),
        "out_dir": str(out_dir),
        "wire_plan": [{"step": s, "name": n, "wire": w} for s, n, w in WIRE_PLAN],
    }
    return _emit(
        ok=True,
        exit_code=ec.EXIT_SUCCESS,
        code=None,
        message="dry-run ok",
        data=data,
        as_json=as_json,
        human_lines=plan_lines,
    )


# ---------------------------------------------------------------------------
# Live
# ---------------------------------------------------------------------------


def run_live(
    *,
    workspace: Path,
    out_dir: Path,
    timeout: float,
    as_json: bool,
) -> int:
    started = _iso_now()
    steps: list[dict[str, Any]] = []
    artifacts: dict[str, Any] = {
        "checkpoint": None,
        "composite_png": None,
        "xcf": None,
        "export_png": None,
    }
    export_verification: dict[str, Any] | None = None
    gimp_version: str | None = None
    plugin_version: str | None = None
    handle: Any | None = None
    overall = "FAIL"

    def record(step: int, name: str, ok: bool, code: str | None = None) -> None:
        steps.append({"step": step, "name": name, "ok": ok, "code": code})

    def fail_evidence() -> Path:
        evidence = {
            "schema_version": 1,
            "product": PRODUCT_NAME,
            "version": package_version,
            "started": started,
            "ended": _iso_now(),
            "gimp_version": gimp_version,
            "plugin_version": plugin_version,
            "steps": steps,
            "artifacts": artifacts,
            "export_verification": export_verification
            or {"pass": False, "width": None, "height": None, "format": None},
            "overall": "FAIL",
        }
        path = out_dir / "evidence.json"
        try:
            _write_evidence(path, evidence)
        except OSError:
            pass
        return path

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        # Host workspace env is set in main(); plugin jail is fixed at MCP server
        # start in the GIMP process — operators must set GIMP_WORKSPACE_ROOT before
        # start-order (host env write does not update a running plugin).

        fixture = _fixture_path()
        if not fixture.is_file() or fixture.stat().st_size <= 0:
            raise RuntimeError(f"fixture missing or empty: {fixture}")

        # Copy fixture into workspace (never mutate committed fixtures)
        src_dir = workspace / "golden-path-src"
        src_dir.mkdir(parents=True, exist_ok=True)
        src_image = src_dir / fixture.name
        shutil.copy2(fixture, src_image)
        src_image_s = str(src_image.resolve())

        # 1. Probe
        probe_raw = send_authenticated_command("get_gimp_info", {}, timeout=timeout)
        _require_success(probe_raw, "get_gimp_info")
        gimp_version = _extract_gimp_version(probe_raw)
        plugin_version = _extract_plugin_version(probe_raw)
        if not _version_at_least(gimp_version, MIN_GIMP_VERSION):
            record(1, "probe", False, sec.CODE_UNSUPPORTED)
            raise RuntimeError(
                f"{sec.CODE_UNSUPPORTED}: GIMP version {gimp_version!r} "
                f"< required {MIN_GIMP_VERSION}"
            )
        record(1, "probe", True)

        # 2. Open
        open_raw = send_authenticated_command(
            "open_image",
            {"file_path": src_image_s},
            timeout=timeout,
        )
        open_results = _require_success(open_raw, "open_image")
        handle = open_results.get("handle")
        if handle is None:
            record(2, "open", False, sec.CODE_INVALID_HANDLE)
            raise RuntimeError(f"{sec.CODE_INVALID_HANDLE}: open_image returned no handle")
        record(2, "open", True)

        # Retain open_image handle for subsequent steps (orient has no top-level handle)
        hp = _handle_params(handle)

        # 3. Orient — assert open image(s) present (spec §2.5 step 3)
        orient_raw = send_authenticated_command(
            "orient_workspace",
            {**hp},
            timeout=timeout,
        )
        orient_results = _require_success(orient_raw, "orient_workspace")
        images = orient_results.get("images")
        has_images = isinstance(images, list) and len(images) > 0
        if not has_images:
            # Fail-closed: orient success with empty images is not a usable workspace
            record(3, "orient", False, sec.CODE_INVALID_HANDLE)
            raise RuntimeError(
                f"{sec.CODE_INVALID_HANDLE}: orient_workspace returned no open images "
                "(assert handles / image present)"
            )
        record(3, "orient", True)

        # 4. Protect
        protect_raw = send_authenticated_command(
            "ensure_source_immutable",
            {**hp},
            timeout=timeout,
        )
        _require_success(protect_raw, "ensure_source_immutable")
        record(4, "protect", True)

        # 5. Checkpoint (persisted XCF — M5); only record path when file exists
        ck_raw = send_authenticated_command(
            "checkpoint_create",
            {**hp, "label": CHECKPOINT_LABEL, "overwrite": True},
            timeout=timeout,
        )
        ck_results = _require_success(ck_raw, "checkpoint_create")
        ck_path = (
            ck_results.get("xcf_path") or ck_results.get("file_path") or ck_results.get("path")
        )
        if ck_path and Path(str(ck_path)).is_file():
            artifacts["checkpoint"] = str(ck_path)
        else:
            # Convention fallback only when the guessed file actually exists
            guess = workspace / ".gimp-mcp-checkpoints" / CHECKPOINT_LABEL / "project.xcf"
            if guess.is_file():
                artifacts["checkpoint"] = str(guess)
        if not artifacts["checkpoint"]:
            record(5, "checkpoint", False, sec.CODE_VERIFY_FAILED)
            raise RuntimeError(
                f"{sec.CODE_VERIFY_FAILED}: checkpoint_create succeeded but no "
                "checkpoint file path on disk"
            )
        record(5, "checkpoint", True)

        # 6. Optional selection no-op (wire select_all / select_none only)
        try:
            sel_all = send_authenticated_command(
                "select_all",
                {**hp},
                timeout=timeout,
            )
            _require_success(sel_all, "select_all")
            sel_none = send_authenticated_command(
                "select_none",
                {**hp},
                timeout=timeout,
            )
            _require_success(sel_none, "select_none")
            record(6, "optional_selection", True)
        except sec.SecurityError:
            raise
        except (ConnectionError, OSError, TimeoutError):
            raise
        except Exception as sel_exc:
            # Re-raise hard transport/auth failures; swallow soft selection policy only
            text = str(sel_exc)
            if sec.CODE_AUTH_FAILED in text or sec.CODE_CONNECTION_FAILED in text:
                raise
            record(6, "optional_selection", False, type(sel_exc).__name__)
            # Non-fatal soft failure: light edit primary is ensure+checkpoint

        # 7. Composite via get_image_bitmap only (H2/H3)
        bmp_raw = send_authenticated_command(
            "get_image_bitmap",
            {
                **hp,
                "max_width": 64,
                "max_height": 64,
            },
            timeout=timeout,
        )
        bmp_results = _require_success(bmp_raw, "get_image_bitmap")
        image_b64 = (
            bmp_results.get("image_data")
            or bmp_results.get("image")
            or bmp_results.get("png_base64")
        )
        if not image_b64 or not str(image_b64).strip():
            record(7, "composite", False, sec.CODE_VERIFY_FAILED)
            raise RuntimeError(
                f"{sec.CODE_VERIFY_FAILED}: get_image_bitmap missing base64 image_data"
            )
        try:
            png_bytes = base64.b64decode(str(image_b64), validate=False)
        except Exception as dec_exc:
            record(7, "composite", False, sec.CODE_VERIFY_FAILED)
            raise RuntimeError(
                f"{sec.CODE_VERIFY_FAILED}: base64 decode failed: {dec_exc}"
            ) from dec_exc
        if len(png_bytes) < 8 or png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
            record(7, "composite", False, sec.CODE_VERIFY_FAILED)
            raise RuntimeError(f"{sec.CODE_VERIFY_FAILED}: composite payload is not a PNG")
        # Optional mapping dims
        mapping = bmp_results.get("mapping") if isinstance(bmp_results.get("mapping"), dict) else {}
        map_w = mapping.get("image_width") or mapping.get("width") or bmp_results.get("width")
        map_h = mapping.get("image_height") or mapping.get("height") or bmp_results.get("height")
        if map_w is not None and int(map_w) < FIXTURE_WIDTH:
            record(7, "composite", False, sec.CODE_VERIFY_FAILED)
            raise RuntimeError(
                f"{sec.CODE_VERIFY_FAILED}: composite width {map_w} < fixture {FIXTURE_WIDTH}"
            )
        if map_h is not None and int(map_h) < FIXTURE_HEIGHT:
            record(7, "composite", False, sec.CODE_VERIFY_FAILED)
            raise RuntimeError(
                f"{sec.CODE_VERIFY_FAILED}: composite height {map_h} < fixture {FIXTURE_HEIGHT}"
            )

        composite_path = out_dir / "composite.png"
        composite_path.write_bytes(png_bytes)
        artifacts["composite_png"] = str(composite_path)
        # Prefer host verify on composite
        comp_v = verify_artifact(
            composite_path,
            {
                "format": "png",
                "min_width": FIXTURE_WIDTH,
                "min_height": FIXTURE_HEIGHT,
            },
            raise_on_fail=False,
        )
        if not comp_v.get("pass"):
            record(7, "composite", False, sec.CODE_VERIFY_FAILED)
            raise RuntimeError(
                f"{sec.CODE_VERIFY_FAILED}: composite verify failed: {comp_v.get('failures')}"
            )
        record(7, "composite", True)

        # 8. save_xcf
        xcf_path = out_dir / "golden-path.xcf"
        save_raw = send_authenticated_command(
            "save_xcf",
            {
                **hp,
                "file_path": str(xcf_path),
                "collision": "replace",
                "verify_reopen": True,
            },
            timeout=timeout,
        )
        save_results = _require_success(save_raw, "save_xcf")
        artifacts["xcf"] = str(save_results.get("file_path") or xcf_path)
        record(8, "save_xcf", True)

        # 9. export_image PNG preserve_alpha
        export_path = out_dir / "golden-path.png"
        export_raw = send_authenticated_command(
            "export_image",
            {
                **hp,
                "file_path": str(export_path),
                "format": "png",
                "preserve_alpha": True,
                "flatten": False,
                "verify": True,
                "collision": "replace",
            },
            timeout=timeout,
        )
        export_results = _require_success(export_raw, "export_image")
        artifacts["export_png"] = str(export_results.get("file_path") or export_path)
        record(9, "export", True)

        # 10. Host verify_artifact on export (required)
        exp_path = Path(str(artifacts["export_png"]))
        ver = verify_artifact(
            exp_path,
            {
                "format": "png",
                "width": FIXTURE_WIDTH,
                "height": FIXTURE_HEIGHT,
            },
            raise_on_fail=False,
        )
        export_verification = {
            "pass": bool(ver.get("pass")),
            "width": ver.get("width"),
            "height": ver.get("height"),
            "format": ver.get("detected_format"),
        }
        if not ver.get("pass"):
            record(10, "host_verify", False, sec.CODE_VERIFY_FAILED)
            raise RuntimeError(
                f"{sec.CODE_VERIFY_FAILED}: export verify failed: {ver.get('failures')}"
            )
        record(10, "host_verify", True)

        overall = "PASS"
        evidence = {
            "schema_version": 1,
            "product": PRODUCT_NAME,
            "version": package_version,
            "started": started,
            "ended": _iso_now(),
            "gimp_version": gimp_version,
            "plugin_version": plugin_version,
            "steps": steps,
            "artifacts": artifacts,
            "export_verification": export_verification,
            "overall": overall,
        }
        evidence_path = out_dir / "evidence.json"
        _write_evidence(evidence_path, evidence)

        human = [
            "golden-path smoke LIVE PASS",
            f"  gimp:      {gimp_version}",
            f"  out-dir:   {out_dir}",
            f"  evidence:  {evidence_path}",
            f"  export:    {artifacts['export_png']}",
            f"  composite: {artifacts['composite_png']}",
            f"  xcf:       {artifacts['xcf']}",
        ]
        return _emit(
            ok=True,
            exit_code=ec.EXIT_SUCCESS,
            code=None,
            message="live golden-path PASS",
            data={
                "mode": "live",
                "evidence": str(evidence_path),
                "artifacts": artifacts,
                "export_verification": export_verification,
                "gimp_version": gimp_version,
                "overall": overall,
            },
            as_json=as_json,
            human_lines=human,
        )

    except Exception as exc:
        code, exit_n, message = _map_exception(exc)
        # Ensure a failed step is recorded if the last call didn't
        if not steps or steps[-1].get("ok") is True:
            steps.append(
                {
                    "step": len(steps) + 1,
                    "name": "failed",
                    "ok": False,
                    "code": code,
                }
            )
        evidence_path = fail_evidence()
        return _emit(
            ok=False,
            exit_code=exit_n,
            code=code,
            message=message,
            data={
                "mode": "live",
                "evidence": str(evidence_path),
                "artifacts": artifacts,
                "steps": steps,
                "overall": "FAIL",
            },
            as_json=as_json,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Golden-path smoke for gimp-mcp (plugin wire names only). "
            "Default mode is dry-run (no socket)."
        )
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Fixture + workspace checks + print wire plan; no socket (default)",
    )
    mode.add_argument(
        "--live",
        action="store_true",
        help="Run full live path against GIMP plugin TCP",
    )
    p.add_argument(
        "--workspace",
        default=None,
        help=f"Workspace jail root (default: env {sec.ENV_WORKSPACE})",
    )
    p.add_argument(
        "--out-dir",
        default=None,
        help="Output directory under workspace (default: <workspace>/output/golden-path)",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help=f"Plugin TCP timeout seconds (default {DEFAULT_TIMEOUT_S:g}, clamp "
        f"{TIMEOUT_MIN_S:g}-{TIMEOUT_MAX_S:g})",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON envelope on stdout (evidence.json still written on live)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Default dry-run unless --live or GIMP_MCP_LIVE=1 (AI2 BS4)
    if args.dry_run:
        live = False
    elif args.live:
        live = True
    else:
        live = _env_truthy("GIMP_MCP_LIVE")

    timeout = _clamp_timeout(float(args.timeout))
    as_json = bool(args.json)

    try:
        workspace = _resolve_workspace(args.workspace)
        # Materialize workspace so resolve_under_root / relative paths work
        workspace.mkdir(parents=True, exist_ok=True)
        os.environ[sec.ENV_WORKSPACE] = str(workspace)
        out_dir = _resolve_out_dir(workspace, args.out_dir)
    except sec.SecurityError as exc:
        return _emit(
            ok=False,
            exit_code=ec.exit_code_for(exc.code),
            code=exc.code,
            message=exc.message,
            data={},
            as_json=as_json,
        )
    except Exception as exc:
        code, exit_n, message = _map_exception(exc)
        return _emit(
            ok=False,
            exit_code=exit_n,
            code=code,
            message=message,
            data={},
            as_json=as_json,
        )

    if not live:
        return run_dry_run(workspace=workspace, out_dir=out_dir, as_json=as_json)
    return run_live(
        workspace=workspace,
        out_dir=out_dir,
        timeout=timeout,
        as_json=as_json,
    )


if __name__ == "__main__":
    raise SystemExit(main())
