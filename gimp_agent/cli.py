"""argparse CLI entrypoint for gimp-agent (no click/typer)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from importlib import metadata
from typing import Any

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


def _cmd_doctor(args: argparse.Namespace) -> int:
    report = doctor_mod.run_doctor(strict=bool(args.strict))
    envelope = jsonio.make_envelope(
        ok=report.ok,
        exit_code=report.exit_code,
        code=report.code,
        message=report.message,
        data=report.envelope_data(),
    )
    jsonio.emit(envelope, as_json=jsonio.json_mode_enabled(flag=args.json))
    return report.exit_code


def _cmd_probe(args: argparse.Namespace) -> int:
    report = probe_mod.run_probe(timeout=float(args.timeout))
    envelope = jsonio.make_envelope(
        ok=report.ok,
        exit_code=report.exit_code,
        code=report.code,
        message=report.message,
        data=report.data,
    )
    jsonio.emit(envelope, as_json=jsonio.json_mode_enabled(flag=args.json))
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
    as_json = jsonio.json_mode_enabled(flag=args.json)
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
    as_json = jsonio.json_mode_enabled(flag=args.json)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gimp-agent",
        description="Deterministic CLI sidecar for GIMP MCP (doctor, probe, exit codes).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=None,
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
        default=None,
        help="Emit JSON envelope on stdout",
    )
    p_doctor.set_defaults(func=_cmd_doctor)

    p_probe = sub.add_parser("probe", help="Authenticated TCP probe (get_gimp_info)")
    p_probe.add_argument(
        "--timeout",
        type=float,
        default=2.0,
        help="Connect/read timeout seconds (default 2.0)",
    )
    p_probe.add_argument(
        "--json",
        action="store_true",
        default=None,
        help="Emit JSON envelope on stdout",
    )
    p_probe.set_defaults(func=_cmd_probe)

    p_version = sub.add_parser("version", help="Show agent and discovered GIMP versions")
    p_version.add_argument(
        "--json",
        action="store_true",
        default=None,
        help="Emit JSON envelope on stdout",
    )
    p_version.set_defaults(func=_cmd_version)

    p_codes = sub.add_parser("codes", help="Print CODE_* → exit map and reverse table")
    p_codes.add_argument(
        "--json",
        action="store_true",
        default=None,
        help="Emit JSON envelope on stdout",
    )
    p_codes.set_defaults(func=_cmd_codes)

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

    # Parent and subcommand both expose --json (same dest); either position enables it.
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
