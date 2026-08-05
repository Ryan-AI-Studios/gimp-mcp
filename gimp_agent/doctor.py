"""Ordered environment diagnostics for gimp-agent doctor."""

from __future__ import annotations

import os
import shutil
import socket
import sys
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any

import gimp_mcp_security as sec
from gimp_agent import exit_codes as ec
from gimp_agent import install as install_mod
from gimp_agent import paths as pathmod


@dataclass
class CheckResult:
    name: str
    severity: str  # required | warn | info
    status: str  # pass | fail | warn | info | skip
    message: str
    code: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": self.name,
            "severity": self.severity,
            "status": self.status,
            "message": self.message,
        }
        if self.code is not None:
            body["code"] = self.code
        if self.detail:
            body["detail"] = self.detail
        return body


@dataclass
class DoctorReport:
    ok: bool
    code: str | None
    message: str
    exit_code: int
    checks: list[CheckResult]
    data: dict[str, Any]

    def envelope_data(self) -> dict[str, Any]:
        out = dict(self.data)
        out["checks"] = [c.as_dict() for c in self.checks]
        out["batch_interpreter"] = True
        return out


def _tcp_connect_only(host: str, port: int, timeout: float = 2.0) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return True, f"connected to {host}:{port}"
    except OSError as exc:
        return False, f"connect {host}:{port} failed: {exc}"


def _resolve_host() -> str:
    try:
        return sec.get_host()
    except sec.SecurityError:
        return sec.DEFAULT_HOST


def run_doctor(*, strict: bool = False) -> DoctorReport:
    """Run ordered checks; under ``strict``, first required failure sets exit.

    All checks are always recorded in the report.
    """
    checks: list[CheckResult] = []
    first_required_code: str | None = None
    first_required_message: str | None = None

    data: dict[str, Any] = {
        "batch_interpreter": True,
        "strict": strict,
    }

    # 1. gimp_console path + --version (required)
    console = pathmod.find_gimp_console()
    data["gimp_console_path"] = str(console) if console else None
    if console is None:
        checks.append(
            CheckResult(
                name="gimp_console",
                severity="required",
                status="fail",
                message="GIMP console binary not found (env/PATH/install dirs)",
                code=ec.GIMP_NOT_FOUND,
            )
        )
        if first_required_code is None:
            first_required_code = ec.GIMP_NOT_FOUND
            first_required_message = "GIMP console binary not found"
        data["gimp_version"] = None
    else:
        version_out, version_err = pathmod.run_console_version(console)
        data["gimp_version"] = version_out
        if version_out is None:
            checks.append(
                CheckResult(
                    name="gimp_console",
                    severity="required",
                    status="fail",
                    message=f"found {console} but --version failed: {version_err}",
                    code=ec.GIMP_NOT_FOUND,
                    detail={"path": str(console)},
                )
            )
            if first_required_code is None:
                first_required_code = ec.GIMP_NOT_FOUND
                first_required_message = f"gimp-console --version failed: {version_err}"
        else:
            checks.append(
                CheckResult(
                    name="gimp_console",
                    severity="required",
                    status="pass",
                    message=f"{console}: {version_out.splitlines()[0]}",
                    detail={"path": str(console), "version": version_out},
                )
            )

    # 2. plugin_dir + EXPECTED_PLUGIN_FILES (required)
    plugin_dir = pathmod.find_plugin_dir()
    data["plugin_dir"] = str(plugin_dir) if plugin_dir else None
    missing = pathmod.missing_plugin_files(plugin_dir)
    data["missing_plugin_files"] = missing
    expected_list = list(pathmod.EXPECTED_PLUGIN_FILES)
    expected_count = len(expected_list)
    present = (
        [n for n in expected_list if (plugin_dir / n).is_file()] if plugin_dir is not None else []
    )
    data["plugin_files_present"] = present
    data["plugin_files_expected_count"] = expected_count
    files_detail: dict[str, Any] = {
        "expected": expected_list,
        "present": present,
        "missing": missing,
        "expected_count": expected_count,
    }
    if plugin_dir is not None:
        files_detail["plugin_dir"] = str(plugin_dir)
    if plugin_dir is None or missing:
        msg = (
            "GIMP plug-in directory not found under %APPDATA%/GIMP/3.*"
            if plugin_dir is None
            else f"incomplete plug-in install at {plugin_dir}: missing {', '.join(missing)}"
        )
        checks.append(
            CheckResult(
                name="plugin_files",
                severity="required",
                status="fail",
                message=msg,
                code=ec.PLUGIN_NOT_FOUND,
                detail=files_detail,
            )
        )
        if first_required_code is None:
            first_required_code = ec.PLUGIN_NOT_FOUND
            first_required_message = msg
    else:
        checks.append(
            CheckResult(
                name="plugin_files",
                severity="required",
                status="pass",
                message=f"all {expected_count} ship files present in {plugin_dir}",
                detail=files_detail,
            )
        )

    # 2b. plugin_stale (warn only) — after plugin_files; never strict-fail
    # Only when all files present AND source resolvable; sha256 both-present mismatches.
    if plugin_dir is None or missing:
        checks.append(
            CheckResult(
                name="plugin_stale",
                severity="warn",
                status="skip",
                message="skipped (plugin_files incomplete)",
            )
        )
    else:
        try:
            source_dir = install_mod.resolve_source_dir(None)
        except FileNotFoundError:
            checks.append(
                CheckResult(
                    name="plugin_stale",
                    severity="warn",
                    status="skip",
                    message="skipped (plugin source not resolvable; pass --source for install)",
                )
            )
        else:
            mismatches = install_mod.compare_installed(source_dir, plugin_dir)
            data["plugin_stale_mismatches"] = mismatches
            data["plugin_stale_source"] = str(source_dir)
            if mismatches:
                checks.append(
                    CheckResult(
                        name="plugin_stale",
                        severity="warn",
                        status="warn",
                        message=(
                            f"{len(mismatches)} installed file(s) differ from source "
                            f"{source_dir}: {', '.join(mismatches)}"
                        ),
                        detail={
                            "mismatches": mismatches,
                            "source_dir": str(source_dir),
                            "plugin_dir": str(plugin_dir),
                        },
                    )
                )
            else:
                checks.append(
                    CheckResult(
                        name="plugin_stale",
                        severity="warn",
                        status="pass",
                        message=f"installed ship files match source {source_dir}",
                        detail={
                            "mismatches": [],
                            "source_dir": str(source_dir),
                            "plugin_dir": str(plugin_dir),
                        },
                    )
                )

    # 3. Token env or file readable (warn)
    env_tok = os.environ.get(sec.ENV_TOKEN)
    token_path = sec.default_token_path()
    file_tok = sec.read_token_file(token_path)
    data["token_env_set"] = bool(env_tok and str(env_tok).strip())
    data["token_file"] = str(token_path)
    data["token_file_readable"] = bool(file_tok)
    if data["token_env_set"] or data["token_file_readable"]:
        src = f"{sec.ENV_TOKEN}" if data["token_env_set"] else str(token_path)
        checks.append(
            CheckResult(
                name="token",
                severity="warn",
                status="pass",
                message=f"token available via {src}",
            )
        )
    else:
        checks.append(
            CheckResult(
                name="token",
                severity="warn",
                status="warn",
                message=(f"no token: set {sec.ENV_TOKEN} or start plugin to write {token_path}"),
            )
        )

    # 4. TCP connect-only no auth (warn)
    host = _resolve_host()
    port = sec.get_port()
    data["tcp_host"] = host
    data["tcp_port"] = port
    tcp_ok, tcp_msg = _tcp_connect_only(host, port)
    data["tcp_connect"] = tcp_ok
    checks.append(
        CheckResult(
            name="tcp_connect",
            severity="warn",
            status="pass" if tcp_ok else "warn",
            message=tcp_msg,
            detail={"host": host, "port": port},
        )
    )

    # 5. gimp_gui path (warn)
    gui = pathmod.find_gimp_gui()
    data["gimp_gui_path"] = str(gui) if gui else None
    if gui is None:
        checks.append(
            CheckResult(
                name="gimp_gui",
                severity="warn",
                status="warn",
                message="GIMP GUI binary not found (optional for headless)",
            )
        )
    else:
        checks.append(
            CheckResult(
                name="gimp_gui",
                severity="warn",
                status="pass",
                message=str(gui),
                detail={"path": str(gui)},
            )
        )

    # 6. GIMP_WORKSPACE_ROOT (info) — host CLI env only; plugin jail is separate
    ws = sec.workspace_root()
    data["workspace_root"] = str(ws) if ws else None
    if ws is None:
        checks.append(
            CheckResult(
                name="workspace",
                severity="info",
                status="info",
                message=(
                    f"{sec.ENV_WORKSPACE} unset in CLI env "
                    "(set on GIMP process via launcher for plugin jail)"
                ),
            )
        )
    else:
        exists = ws.is_dir()
        honesty = (
            f"{sec.ENV_WORKSPACE}={ws} "
            "(host CLI env; GIMP plugin env may differ — "
            "use launcher or set env on GIMP process)"
        )
        if not exists:
            honesty = f"{honesty} (path does not exist)"
        checks.append(
            CheckResult(
                name="workspace",
                severity="info",
                status="pass" if exists else "info",
                message=honesty,
                detail={"path": str(ws), "exists": exists},
            )
        )

    # 7. exiftool via shutil.which (info)
    exif = shutil.which("exiftool")
    data["exiftool"] = exif
    checks.append(
        CheckResult(
            name="exiftool",
            severity="info",
            status="pass" if exif else "info",
            message=exif if exif else "exiftool not on PATH (optional companion)",
        )
    )

    # 7b. ImageMagick magick or legacy compare (info) — optional pixel companion
    magick = shutil.which("magick")
    compare_bin = shutil.which("compare") if not magick else None
    im_path = magick or compare_bin
    data["imagemagick"] = im_path
    data["imagemagick_backend"] = "magick" if magick else ("compare" if compare_bin else None)
    checks.append(
        CheckResult(
            name="imagemagick",
            severity="info",
            status="pass" if im_path else "info",
            message=(
                im_path if im_path else "magick/compare not on PATH (optional AE/SSIM companion)"
            ),
            detail={"backend": data["imagemagick_backend"]},
        )
    )

    # 8. python / tool pin notes (info) — live package versions when installed
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    data["python_version"] = py_ver

    def _pkg_version(name: str) -> str:
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            return "(not installed)"

    mcp_ver = _pkg_version("mcp")
    fastmcp_ver = _pkg_version("fastmcp")
    data["mcp_version"] = mcp_ver
    data["fastmcp_version"] = fastmcp_ver
    pin_msg = (
        f"python {py_ver}; mcp={mcp_ver} fastmcp={fastmcp_ver}; "
        "mcp/fastmcp pins held (mcp>=1.10,<2, fastmcp>=2.10,<3); "
        "batch_interpreter=true (constrained job protocol; "
        "--batch-interpreter plug-in-gimp-mcp-batch; not python-fu-eval)"
    )
    checks.append(
        CheckResult(
            name="tool_pins",
            severity="info",
            status="info",
            message=pin_msg,
            detail={"mcp": mcp_ver, "fastmcp": fastmcp_ver, "python": py_ver},
        )
    )

    # Outcome
    if first_required_code is not None:
        if strict:
            exit_n = ec.exit_code_for(first_required_code, ok=False)
            return DoctorReport(
                ok=False,
                code=first_required_code,
                message=first_required_message or first_required_code,
                exit_code=exit_n,
                checks=checks,
                data=data,
            )
        # Non-strict: still report failure code but exit 0? Spec says:
        # "default doctor can warn" — required fails under --strict only for process exit.
        # Required failures should still mark ok=False in envelope when not strict?
        # Spec: "--strict: walk table; first required failure sets process exit; JSON still lists all"
        # Default (non-strict) can warn — typically exit 0 with ok reflecting state.
        # Operators use --strict in CI. Non-strict: exit 0, ok may still be false if required fail.
        # Re-read: "default doctor can warn" risk mitigation.
        # I'll use: non-strict → exit 0 always (diagnostics only); ok=False if required failed
        # so agents can still inspect envelope.ok.
        return DoctorReport(
            ok=False,
            code=first_required_code,
            message=first_required_message or first_required_code,
            exit_code=ec.EXIT_SUCCESS,
            checks=checks,
            data=data,
        )

    return DoctorReport(
        ok=True,
        code=None,
        message="doctor ok",
        exit_code=ec.EXIT_SUCCESS,
        checks=checks,
        data=data,
    )
