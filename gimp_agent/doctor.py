"""Ordered environment diagnostics for gimp-agent doctor."""

from __future__ import annotations

import os
import shutil
import socket
import sys
from dataclasses import dataclass, field
from typing import Any

import gimp_mcp_security as sec
from gimp_agent import exit_codes as ec
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
        out["batch_interpreter"] = False
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
        "batch_interpreter": False,
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
    if plugin_dir is None or missing:
        detail: dict[str, Any] = {"missing": missing}
        if plugin_dir is not None:
            detail["plugin_dir"] = str(plugin_dir)
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
                detail=detail,
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
                message=f"all {len(pathmod.EXPECTED_PLUGIN_FILES)} ship files present in {plugin_dir}",
                detail={"plugin_dir": str(plugin_dir)},
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

    # 6. GIMP_WORKSPACE_ROOT (info)
    ws = sec.workspace_root()
    data["workspace_root"] = str(ws) if ws else None
    if ws is None:
        checks.append(
            CheckResult(
                name="workspace",
                severity="info",
                status="info",
                message=f"{sec.ENV_WORKSPACE} unset (required later for filesystem tools)",
            )
        )
    else:
        exists = ws.is_dir()
        checks.append(
            CheckResult(
                name="workspace",
                severity="info",
                status="pass" if exists else "info",
                message=f"{sec.ENV_WORKSPACE}={ws}" + ("" if exists else " (path does not exist)"),
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

    # 8. python / tool pin notes (info)
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    pin_msg = (
        f"python {py_ver}; mcp/fastmcp pins held (mcp>=1.10,<2, fastmcp>=2.10,<3); "
        "batch_interpreter=false until track 0019"
    )
    data["python_version"] = py_ver
    checks.append(
        CheckResult(
            name="tool_pins",
            severity="info",
            status="info",
            message=pin_msg,
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
