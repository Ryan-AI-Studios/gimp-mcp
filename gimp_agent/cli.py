"""argparse CLI entrypoint for gimp-agent (no click/typer)."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Sequence
from importlib import metadata
from typing import Any

import gimp_mcp_security as sec
from gimp_agent import __version__ as package_version
from gimp_agent import doctor as doctor_mod
from gimp_agent import exit_codes as ec
from gimp_agent import jsonio
from gimp_agent import paths as pathmod
from gimp_agent import probe as probe_mod


def _agent_version() -> str:
    try:
        return metadata.version("gimp-mcp")
    except metadata.PackageNotFoundError:
        return package_version


def _json_flag(args: argparse.Namespace) -> bool | None:
    """OR parent/subcommand ``--json``; return None when unset so env is consulted.

    Spec: ``--json`` flag overrides env; env ``GIMP_AGENT_JSON`` applies only when
    neither CLI flag is present. Returning explicit False would suppress env.
    """
    if getattr(args, "json_global", False) or getattr(args, "json_local", False):
        return True
    return None


def _cmd_doctor(args: argparse.Namespace) -> int:
    report = doctor_mod.run_doctor(strict=bool(args.strict))
    envelope = jsonio.make_envelope(
        ok=report.ok,
        exit_code=report.exit_code,
        code=report.code,
        message=report.message,
        data=report.envelope_data(),
    )
    jsonio.emit(envelope, as_json=jsonio.json_mode_enabled(flag=_json_flag(args)))
    return report.exit_code


def _cmd_probe(args: argparse.Namespace) -> int:
    timeout = float(args.timeout)
    if not math.isfinite(timeout) or timeout <= 0:
        # Serialize timeout as string so json.dumps stays standards-compliant
        # (Python would otherwise emit NaN/Infinity, which strict parsers reject).
        timeout_repr = str(timeout)
        envelope = jsonio.make_envelope(
            ok=False,
            exit_code=ec.EXIT_CLI_USAGE,
            code=ec.CLI_USAGE,
            message=(f"invalid --timeout {timeout_repr!r}: must be a finite positive number"),
            data={"timeout": timeout_repr},
        )
        jsonio.emit(envelope, as_json=jsonio.json_mode_enabled(flag=_json_flag(args)))
        return ec.EXIT_CLI_USAGE

    report = probe_mod.run_probe(timeout=timeout)
    envelope = jsonio.make_envelope(
        ok=report.ok,
        exit_code=report.exit_code,
        code=report.code,
        message=report.message,
        data=report.data,
    )
    jsonio.emit(envelope, as_json=jsonio.json_mode_enabled(flag=_json_flag(args)))
    return report.exit_code


def _cmd_version(args: argparse.Namespace) -> int:
    console = pathmod.find_gimp_console()
    gimp_version: str | None = None
    console_path = str(console) if console else None
    if console is not None:
        out, _err = pathmod.run_console_version(console)
        gimp_version = out
    data: dict[str, Any] = {
        "agent_version": _agent_version(),
        "gimp_version": gimp_version,
        "gimp_console_path": console_path,
    }
    envelope = jsonio.make_envelope(
        ok=True,
        exit_code=ec.EXIT_SUCCESS,
        code=None,
        message=f"gimp-agent {_agent_version()}",
        data=data,
    )
    as_json = jsonio.json_mode_enabled(flag=_json_flag(args))
    human = [
        f"gimp-agent {data['agent_version']}",
        f"  gimp_console_path: {console_path or '(not found)'}",
        f"  gimp_version: {gimp_version or '(unknown)'}",
    ]
    jsonio.emit(envelope, as_json=as_json, human_lines=human)
    return ec.EXIT_SUCCESS


def _cmd_codes(args: argparse.Namespace) -> int:
    code_to_exit = ec.code_to_exit_table()
    # JSON object keys must be strings for exit_to_codes
    exit_to_codes = {str(k): v for k, v in sorted(ec.exit_to_codes_table().items())}
    envelope = jsonio.make_envelope(
        ok=True,
        exit_code=ec.EXIT_SUCCESS,
        code=None,
        message="exit code map",
        data={
            "code_to_exit": code_to_exit,
            "exit_to_codes": exit_to_codes,
        },
    )
    as_json = jsonio.json_mode_enabled(flag=_json_flag(args))
    if as_json:
        jsonio.emit(envelope, as_json=True)
    else:
        lines = ["code → exit:"]
        for code, exit_n in sorted(code_to_exit.items(), key=lambda kv: (kv[1], kv[0])):
            lines.append(f"  {code}: {exit_n}")
        lines.append("exit → codes:")
        for exit_n, codes in sorted(ec.exit_to_codes_table().items()):
            joined = ", ".join(codes) if codes else "(none)"
            lines.append(f"  {exit_n}: {joined}")
        jsonio.emit(envelope, as_json=False, human_lines=lines)
    return ec.EXIT_SUCCESS


def _parse_handle_arg(raw: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid --handle JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("--handle must be a JSON object")
    return parsed


def _image_selection_params(args: argparse.Namespace) -> dict[str, Any]:
    """Build handle / image_index params from CLI selection flags."""
    params: dict[str, Any] = {}
    handle = getattr(args, "handle", None)
    index = getattr(args, "index", None)
    if handle is not None:
        params["handle"] = handle
    if index is not None:
        params["image_index"] = int(index)
    if "handle" not in params and "image_index" not in params:
        params["image_index"] = 0
    return params


def _emit_plugin_result(
    *,
    result: dict[str, Any],
    as_json: bool,
    human_ok: list[str] | None = None,
) -> int:
    """Map a plugin TCP response to CLI envelope + exit code."""
    if result.get("status") == "success":
        raw_results = result.get("results")
        success_data: dict[str, Any] = (
            raw_results if isinstance(raw_results, dict) else {"results": raw_results}
        )
        envelope = jsonio.make_envelope(
            ok=True,
            exit_code=ec.EXIT_SUCCESS,
            code=None,
            message="ok",
            data=success_data,
        )
        jsonio.emit(envelope, as_json=as_json, human_lines=human_ok)
        return ec.EXIT_SUCCESS

    code = result.get("code")
    err_code = code if isinstance(code, str) else sec.CODE_INTERNAL
    message = str(result.get("error") or "plugin error")
    err_data: dict[str, Any] = {k: v for k, v in result.items() if k not in ("status",)}
    exit_n = ec.exit_code_for(err_code)
    envelope = jsonio.make_envelope(
        ok=False,
        exit_code=exit_n,
        code=err_code,
        message=message,
        data=err_data,
    )
    jsonio.emit(envelope, as_json=as_json)
    return exit_n


def _run_plugin_command(
    command_type: str,
    params: dict[str, Any],
    *,
    args: argparse.Namespace,
    human_ok: list[str] | None = None,
) -> int:
    """Send authenticated plugin command; map transport/auth/result to exit codes."""
    as_json = jsonio.json_mode_enabled(flag=_json_flag(args))
    timeout = float(getattr(args, "timeout", 30.0))
    if not math.isfinite(timeout) or timeout <= 0:
        envelope = jsonio.make_envelope(
            ok=False,
            exit_code=ec.EXIT_CLI_USAGE,
            code=ec.CLI_USAGE,
            message=f"invalid --timeout {timeout!r}: must be a finite positive number",
            data={"timeout": str(timeout)},
        )
        jsonio.emit(envelope, as_json=as_json)
        return ec.EXIT_CLI_USAGE

    try:
        result = probe_mod.send_authenticated_command(command_type, params, timeout=timeout)
    except RuntimeError as exc:
        text = str(exc)
        code = sec.CODE_AUTH_FAILED if sec.CODE_AUTH_FAILED in text else sec.CODE_INTERNAL
        exit_n = ec.exit_code_for(code)
        envelope = jsonio.make_envelope(
            ok=False,
            exit_code=exit_n,
            code=code,
            message=text,
            data={},
        )
        jsonio.emit(envelope, as_json=as_json)
        return exit_n
    except TimeoutError:
        exit_n = ec.exit_code_for(sec.CODE_TIMEOUT)
        envelope = jsonio.make_envelope(
            ok=False,
            exit_code=exit_n,
            code=sec.CODE_TIMEOUT,
            message=f"timeout after {timeout}s",
            data={"timeout": timeout},
        )
        jsonio.emit(envelope, as_json=as_json)
        return exit_n
    except OSError as exc:
        exit_n = ec.exit_code_for(sec.CODE_CONNECTION_FAILED)
        envelope = jsonio.make_envelope(
            ok=False,
            exit_code=exit_n,
            code=sec.CODE_CONNECTION_FAILED,
            message=f"connection failed: {exc}",
            data={},
        )
        jsonio.emit(envelope, as_json=as_json)
        return exit_n

    return _emit_plugin_result(result=result, as_json=as_json, human_ok=human_ok)


def _cmd_save_xcf(args: argparse.Namespace) -> int:
    params: dict[str, Any] = {
        "file_path": str(args.path),
        "collision": str(args.collision),
        "verify_reopen": bool(args.verify_reopen),
    }
    params.update(_image_selection_params(args))
    human = [f"saved {args.path}"]
    return _run_plugin_command("save_xcf", params, args=args, human_ok=human)


def _cmd_export(args: argparse.Namespace) -> int:
    params: dict[str, Any] = {
        "file_path": str(args.path),
        "format": str(args.format),
        "quality": int(args.quality),
        "flatten": bool(args.flatten),
        "verify": bool(args.verify),
        "collision": str(args.collision),
    }
    if args.preserve_alpha is not None:
        params["preserve_alpha"] = bool(args.preserve_alpha)
    params.update(_image_selection_params(args))
    human = [f"exported {args.path} ({args.format})"]
    return _run_plugin_command("export_image", params, args=args, human_ok=human)


def _jail_cli_path(path: str, label: str = "path") -> str:
    """Workspace-jail a path for host-only verify/compare (PATH_DENIED if unset/escape)."""
    try:
        return str(sec.resolve_under_root(path))
    except sec.SecurityError:
        raise


def _emit_host_error(
    *,
    code: str,
    message: str,
    as_json: bool,
    data: dict[str, Any] | None = None,
) -> int:
    exit_n = ec.exit_code_for(code)
    envelope = jsonio.make_envelope(
        ok=False,
        exit_code=exit_n,
        code=code,
        message=message,
        data=data or {},
    )
    jsonio.emit(envelope, as_json=as_json)
    return exit_n


def _cmd_compare(args: argparse.Namespace) -> int:
    """Host-only PNG compare — no TCP/token/plugin."""
    import gimp_mcp_verify as verify

    as_json = jsonio.json_mode_enabled(flag=_json_flag(args))
    try:
        path_a = _jail_cli_path(str(args.path_a), "path_a")
        path_b = _jail_cli_path(str(args.path_b), "path_b")
        diff_out = (
            _jail_cli_path(str(args.diff_out), "diff_out")
            if getattr(args, "diff_out", None)
            else None
        )
    except sec.SecurityError as exc:
        return _emit_host_error(code=exc.code, message=exc.message, as_json=as_json)

    thresholds: dict[str, Any] = {}
    if getattr(args, "require_mutation", False):
        thresholds["require_mutation"] = True
    if getattr(args, "min_changed_pixels", None) is not None:
        thresholds["min_changed_pixels"] = int(args.min_changed_pixels)
    if getattr(args, "max_mae", None) is not None:
        thresholds["max_mae"] = float(args.max_mae)
    if getattr(args, "max_max_ae", None) is not None:
        thresholds["max_max_ae"] = int(args.max_max_ae)
    if getattr(args, "min_ssim", None) is not None:
        thresholds["min_ssim"] = float(args.min_ssim)
    if getattr(args, "max_changed_fraction", None) is not None:
        thresholds["max_changed_fraction"] = float(args.max_changed_fraction)

    compute_ssim: bool | str
    if args.ssim is None:
        compute_ssim = "auto"
    else:
        compute_ssim = bool(args.ssim)

    try:
        report = verify.compare_images(
            path_a,
            path_b,
            thresholds=thresholds or None,
            write_diff_path=diff_out,
            raise_on_fail=False,
            ignore_alpha=bool(args.ignore_alpha),
            compute_ssim=compute_ssim,
            change_threshold=int(args.change_threshold),
        )
    except sec.GimpMcpError as exc:
        return _emit_host_error(
            code=exc.code,
            message=exc.message,
            as_json=as_json,
            data=exc.details or {},
        )
    except sec.SecurityError as exc:
        return _emit_host_error(code=exc.code, message=exc.message, as_json=as_json)

    if not report.get("pass", False):
        exit_n = ec.exit_code_for(sec.CODE_VERIFY_FAILED)
        envelope = jsonio.make_envelope(
            ok=False,
            exit_code=exit_n,
            code=sec.CODE_VERIFY_FAILED,
            message="compare thresholds failed",
            data=report,
        )
        jsonio.emit(envelope, as_json=as_json)
        return exit_n

    envelope = jsonio.make_envelope(
        ok=True,
        exit_code=ec.EXIT_SUCCESS,
        code=None,
        message="compare ok",
        data=report,
    )
    human = [
        f"compare pass mae={report.get('mae')} max_ae={report.get('max_ae')} "
        f"changed={report.get('changed_pixels')}"
    ]
    jsonio.emit(envelope, as_json=as_json, human_lines=human)
    return ec.EXIT_SUCCESS


def _cmd_verify(args: argparse.Namespace) -> int:
    """Host-only artifact verify — no TCP/token/plugin."""
    import gimp_mcp_verify as verify

    as_json = jsonio.json_mode_enabled(flag=_json_flag(args))
    try:
        path = _jail_cli_path(str(args.path), "path")
        # Spec JSON is a path under workspace rules (no out-of-root reads / exfil).
        spec_path = _jail_cli_path(str(args.spec), "spec")
    except sec.SecurityError as exc:
        return _emit_host_error(code=exc.code, message=exc.message, as_json=as_json)

    try:
        with open(spec_path, encoding="utf-8") as fh:
            expected = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return _emit_host_error(
            code=ec.CLI_USAGE,
            message=f"invalid --spec {spec_path!r}: {exc}",
            as_json=as_json,
        )
    if not isinstance(expected, dict):
        return _emit_host_error(
            code=ec.CLI_USAGE,
            message="--spec JSON must be an object",
            as_json=as_json,
        )

    try:
        report = verify.verify_artifact(path, expected, raise_on_fail=False)
    except sec.GimpMcpError as exc:
        return _emit_host_error(
            code=exc.code,
            message=exc.message,
            as_json=as_json,
            data=exc.details or {},
        )
    except sec.SecurityError as exc:
        return _emit_host_error(code=exc.code, message=exc.message, as_json=as_json)

    if not report.get("pass", False):
        exit_n = ec.exit_code_for(sec.CODE_VERIFY_FAILED)
        envelope = jsonio.make_envelope(
            ok=False,
            exit_code=exit_n,
            code=sec.CODE_VERIFY_FAILED,
            message="verify_artifact expectations failed",
            data=report,
        )
        jsonio.emit(envelope, as_json=as_json)
        return exit_n

    envelope = jsonio.make_envelope(
        ok=True,
        exit_code=ec.EXIT_SUCCESS,
        code=None,
        message="verify ok",
        data=report,
    )
    human = [
        f"verify pass {report.get('path')} "
        f"{report.get('width')}x{report.get('height')} {report.get('detected_format')}"
    ]
    jsonio.emit(envelope, as_json=as_json, human_lines=human)
    return ec.EXIT_SUCCESS


def _add_image_selection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--index",
        type=int,
        default=None,
        help="Open image index when --handle is omitted (default 0)",
    )
    parser.add_argument(
        "--handle",
        type=_parse_handle_arg,
        default=None,
        help="Image handle as JSON object (preferred over --index)",
    )


def _add_json_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_local",
        default=False,
        help="Emit JSON envelope on stdout",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gimp-agent",
        description=(
            "Deterministic CLI sidecar for GIMP MCP "
            "(doctor, probe, save-xcf, export, compare, verify, exit codes)."
        ),
    )
    # Separate dest from subcommand --json so parent True is not overwritten by
    # subparser default=False when the flag is only given before the subcommand.
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_global",
        default=False,
        help="Emit JSON envelope on stdout (overrides GIMP_AGENT_JSON)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_doctor = sub.add_parser("doctor", help="Run ordered environment diagnostics")
    p_doctor.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on first required check failure",
    )
    p_doctor.add_argument(
        "--json",
        action="store_true",
        dest="json_local",
        default=False,
        help="Emit JSON envelope on stdout",
    )
    p_doctor.set_defaults(func=_cmd_doctor)

    p_probe = sub.add_parser("probe", help="Authenticated TCP probe (get_gimp_info)")
    p_probe.add_argument(
        "--timeout",
        type=float,
        default=2.0,
        help="Connect/read timeout seconds (default 2.0; must be finite and > 0)",
    )
    p_probe.add_argument(
        "--json",
        action="store_true",
        dest="json_local",
        default=False,
        help="Emit JSON envelope on stdout",
    )
    p_probe.set_defaults(func=_cmd_probe)

    p_version = sub.add_parser("version", help="Show agent and discovered GIMP versions")
    p_version.add_argument(
        "--json",
        action="store_true",
        dest="json_local",
        default=False,
        help="Emit JSON envelope on stdout",
    )
    p_version.set_defaults(func=_cmd_version)

    p_codes = sub.add_parser("codes", help="Print CODE_* → exit map and reverse table")
    _add_json_arg(p_codes)
    p_codes.set_defaults(func=_cmd_codes)

    p_save = sub.add_parser(
        "save-xcf",
        help="Atomic XCF save via live plugin TCP (requires MCP server started in GIMP)",
    )
    p_save.add_argument("path", help="Workspace-jailed output .xcf path")
    p_save.add_argument(
        "--collision",
        choices=("fail", "version", "replace"),
        default="fail",
        help="Output collision policy (default: fail)",
    )
    p_save.add_argument(
        "--verify-reopen",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Structural reopen of temp XCF before replace (default: true)",
    )
    p_save.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Connect/read timeout seconds (default 30)",
    )
    _add_image_selection_args(p_save)
    _add_json_arg(p_save)
    p_save.set_defaults(func=_cmd_save_xcf)

    p_export = sub.add_parser(
        "export",
        help="Atomic raster export via live plugin TCP (requires MCP server started in GIMP)",
    )
    p_export.add_argument("path", help="Workspace-jailed output path")
    p_export.add_argument(
        "--format",
        choices=("png", "jpeg", "webp", "tiff"),
        required=True,
        help="Raster format",
    )
    p_export.add_argument(
        "--quality",
        type=int,
        default=90,
        help="JPEG/WEBP quality 1-100 (default 90)",
    )
    p_export.add_argument(
        "--flatten",
        action="store_true",
        default=False,
        help="Flatten on duplicate before export (strips alpha)",
    )
    p_export.add_argument(
        "--preserve-alpha",
        dest="preserve_alpha",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Alpha policy override (default: auto from format/flatten)",
    )
    p_export.add_argument(
        "--verify",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="PNG IHDR alpha verify when preserve_alpha (default: true)",
    )
    p_export.add_argument(
        "--collision",
        choices=("fail", "version", "replace"),
        default="fail",
        help="Output collision policy (default: fail)",
    )
    p_export.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Connect/read timeout seconds (default 30)",
    )
    _add_image_selection_args(p_export)
    _add_json_arg(p_export)
    p_export.set_defaults(func=_cmd_export)

    p_compare = sub.add_parser(
        "compare",
        help="Host-only PNG pixel compare (no plugin/TCP); exit 8 on threshold fail",
    )
    p_compare.add_argument("path_a", help="Workspace-jailed PNG path A")
    p_compare.add_argument("path_b", help="Workspace-jailed PNG path B")
    p_compare.add_argument(
        "--diff-out",
        default=None,
        help="Optional grayscale max-|ΔRGB| heatmap PNG path (workspace-jailed)",
    )
    p_compare.add_argument(
        "--require-mutation",
        action="store_true",
        default=False,
        help="Fail when changed_pixels < min-changed-pixels",
    )
    p_compare.add_argument(
        "--min-changed-pixels",
        type=int,
        default=None,
        help="With --require-mutation (default min 1)",
    )
    p_compare.add_argument("--max-mae", type=float, default=None, help="Max mean absolute error")
    p_compare.add_argument(
        "--max-max-ae",
        type=int,
        default=None,
        help="Max per-channel absolute error",
    )
    p_compare.add_argument("--min-ssim", type=float, default=None, help="Min global luminance SSIM")
    p_compare.add_argument(
        "--max-changed-fraction",
        type=float,
        default=None,
        help="Max changed_pixels/(w*h)",
    )
    p_compare.add_argument(
        "--ignore-alpha",
        action="store_true",
        default=False,
        help="Compare RGB only when modes are RGB vs RGBA",
    )
    p_compare.add_argument(
        "--change-threshold",
        type=int,
        default=1,
        help="Per-channel abs delta to count a pixel changed (default 1)",
    )
    p_compare.add_argument(
        "--ssim",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Force SSIM on/off (default: auto, off when w*h > 1e6)",
    )
    _add_json_arg(p_compare)
    p_compare.set_defaults(func=_cmd_compare)

    p_verify = sub.add_parser(
        "verify",
        help="Host-only artifact verify against JSON spec (no plugin/TCP); exit 8 on fail",
    )
    p_verify.add_argument("path", help="Workspace-jailed artifact path")
    p_verify.add_argument(
        "--spec",
        required=True,
        help="Path to JSON expectation object (format/dims/alpha/sha256/bytes)",
    )
    _add_json_arg(p_verify)
    p_verify.set_defaults(func=_cmd_verify)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint. Returns process exit code (setuptools wraps with sys.exit)."""
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        # argparse: help → 0, usage error → 2 (default). Re-raise as return.
        code = exc.code
        if code is None:
            return ec.EXIT_SUCCESS
        if isinstance(code, int):
            return code
        return ec.EXIT_CLI_USAGE

    # Parent and subcommand both expose --json (separate dests); either enables it.
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
