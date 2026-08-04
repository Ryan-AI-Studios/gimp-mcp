"""Plugin ship-set install / uninstall for gimp-agent (host-only; not a plug-in file).

Source of truth for which files to deploy is ``paths.EXPECTED_PLUGIN_FILES`` —
never redeclare a second ship list here.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from gimp_agent import exit_codes as ec
from gimp_agent import paths as pathmod

ENV_SOURCE = "GIMP_MCP_SOURCE"
_ENTRYPOINT = "gimp-mcp-plugin.py"
_SOURCE_FAIL_MSG = (
    "Could not locate full plugin ship set (10 files). Run from repo root or pass --source <path>."
)

# Host-only modules that must never be copied into the plug-ins dir.
HOST_ONLY_MODULE_NAMES: frozenset[str] = frozenset(
    {
        "gimp_mcp_state.py",
        "gimp_mcp_surface.py",
        "gimp_mcp_verify.py",
        "gimp_mcp_recipes.py",
    }
)


@dataclass
class InstallReport:
    """Outcome of plan/install/uninstall (also mapped into CLI JSON envelopes)."""

    ok: bool
    code: str | None
    message: str
    source_dir: str | None = None
    target_dir: str | None = None
    copied: list[str] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)
    backed_up: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    planned: list[dict[str, Any]] = field(default_factory=list)
    missing_source: list[str] = field(default_factory=list)
    dry_run: bool = False
    restart_required: bool = False

    def envelope_data(self) -> dict[str, Any]:
        return {
            "source_dir": self.source_dir,
            "target_dir": self.target_dir,
            "copied": list(self.copied),
            "failed": list(self.failed),
            "backed_up": list(self.backed_up),
            "skipped": list(self.skipped),
            "planned": list(self.planned),
            "missing_source": list(self.missing_source),
            "dry_run": self.dry_run,
            "restart_required": self.restart_required,
            "expected_count": len(pathmod.EXPECTED_PLUGIN_FILES),
        }


def backup_suffix(*, when: datetime | None = None) -> str:
    """Return ``.bak.YYYYMMDD-HHMMSS`` for timestamped sibling backups."""
    dt = when if when is not None else datetime.now()
    return f".bak.{dt.strftime('%Y%m%d-%H%M%S')}"


def missing_source_files(directory: Path) -> list[str]:
    """Names from EXPECTED_PLUGIN_FILES missing under ``directory``."""
    return [name for name in pathmod.EXPECTED_PLUGIN_FILES if not (directory / name).is_file()]


def is_complete_source(directory: Path) -> bool:
    """True when every EXPECTED_PLUGIN_FILES entry is a regular file under ``directory``."""
    return not missing_source_files(directory)


def resolve_source_dir(explicit: Path | None = None) -> Path:
    """Locate a directory containing the full EXPECTED ship set.

    Order (first complete wins):
    1. ``explicit`` / ``--source`` (must be complete or fail)
    2. Env ``GIMP_MCP_SOURCE`` if complete
    3. ``Path.cwd()`` if complete
    4. Walk parents of the ``gimp_agent`` package for a complete dir
    5. Fail with the locked 10-file error message
    """
    if explicit is not None:
        p = Path(explicit)
        missing = missing_source_files(p)
        if missing:
            raise FileNotFoundError(f"{_SOURCE_FAIL_MSG} Missing from {p}: {', '.join(missing)}.")
        return p.resolve()

    env_raw = os.environ.get(ENV_SOURCE)
    if env_raw and str(env_raw).strip():
        env_path = Path(str(env_raw).strip())
        if is_complete_source(env_path):
            return env_path.resolve()

    cwd = Path.cwd()
    if is_complete_source(cwd):
        return cwd.resolve()

    import gimp_agent

    pkg_dir = Path(gimp_agent.__file__).resolve().parent
    for candidate in (pkg_dir, *pkg_dir.parents):
        if is_complete_source(candidate):
            return candidate

    raise FileNotFoundError(_SOURCE_FAIL_MSG)


def sha256_file(path: Path) -> str:
    """Return lowercase hex sha256 of file contents."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def plan_install(source: Path, target: Path) -> list[dict[str, Any]]:
    """Pure plan: one op dict per EXPECTED file (no filesystem writes)."""
    ops: list[dict[str, Any]] = []
    for name in pathmod.EXPECTED_PLUGIN_FILES:
        src = source / name
        dst = target / name
        exists = dst.is_file()
        ops.append(
            {
                "name": name,
                "action": "backup_and_copy" if exists else "copy",
                "source": str(src),
                "target": str(dst),
                "exists": exists,
            }
        )
    return ops


def plan_uninstall(target: Path) -> list[dict[str, Any]]:
    """Pure plan: delete ops for EXPECTED names present under ``target``."""
    ops: list[dict[str, Any]] = []
    for name in pathmod.EXPECTED_PLUGIN_FILES:
        dst = target / name
        if dst.is_file():
            ops.append(
                {
                    "name": name,
                    "action": "delete",
                    "target": str(dst),
                }
            )
    return ops


def compare_installed(source: Path, target: Path) -> list[str]:
    """Return EXPECTED names where both sides exist but sha256 differs.

    Missing on either side is not a mismatch (not "stale").
    """
    mismatches: list[str] = []
    for name in pathmod.EXPECTED_PLUGIN_FILES:
        src = source / name
        dst = target / name
        if not src.is_file() or not dst.is_file():
            continue
        if sha256_file(src) != sha256_file(dst):
            mismatches.append(name)
    return mismatches


def _fail_report(
    *,
    message: str,
    code: str = ec.PLUGIN_NOT_FOUND,
    source_dir: str | None = None,
    target_dir: str | None = None,
    missing_source: list[str] | None = None,
    dry_run: bool = False,
    failed: list[dict[str, str]] | None = None,
    copied: list[str] | None = None,
    backed_up: list[str] | None = None,
    planned: list[dict[str, Any]] | None = None,
) -> InstallReport:
    return InstallReport(
        ok=False,
        code=code,
        message=message,
        source_dir=source_dir,
        target_dir=target_dir,
        missing_source=list(missing_source or []),
        dry_run=dry_run,
        failed=list(failed or []),
        copied=list(copied or []),
        backed_up=list(backed_up or []),
        planned=list(planned or []),
        restart_required=False,
    )


def install_plugin(
    *,
    source: Path | None = None,
    target: Path | None = None,
    dry_run: bool = False,
    backup: bool = True,
) -> InstallReport:
    """Copy full EXPECTED ship set from source into the plug-in directory.

    - Default target: ``paths.find_plugin_dir()`` (highest GIMP 3.* config).
    - Explicit ``target`` is the exact full plugin dir (no auto-append of subdir).
    - Per-file ``OSError``/``PermissionError``: record in ``failed``, continue.
    - Any failed or still-missing → ``ok=False``, ``code=PLUGIN_NOT_FOUND``.
    - Successful real install → ``restart_required=True``.
    """
    # Resolve source
    try:
        source_dir = resolve_source_dir(source)
    except FileNotFoundError as exc:
        missing: list[str] = []
        if source is not None:
            missing = missing_source_files(Path(source))
        return _fail_report(
            message=str(exc),
            missing_source=missing,
            dry_run=dry_run,
            source_dir=str(source) if source is not None else None,
            target_dir=str(target) if target is not None else None,
        )

    # Resolve target
    if target is not None:
        target_dir = Path(target)
    else:
        found = pathmod.find_plugin_dir()
        if found is None:
            return _fail_report(
                message=(
                    "GIMP plug-in directory not found under GIMP 3.* user config; "
                    "launch GIMP once or pass --target <path>"
                ),
                source_dir=str(source_dir),
                dry_run=dry_run,
            )
        target_dir = found

    planned = plan_install(source_dir, target_dir)

    if dry_run:
        n = len(pathmod.EXPECTED_PLUGIN_FILES)
        return InstallReport(
            ok=True,
            code=None,
            message=f"Dry-run: would install {n}/{n} ship files to {target_dir}",
            source_dir=str(source_dir),
            target_dir=str(target_dir),
            planned=planned,
            dry_run=True,
            restart_required=False,
        )

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _fail_report(
            message=(
                f"Cannot create plug-in target directory {target_dir}: {exc}. "
                "If a file is locked, fully quit GIMP and re-run install."
            ),
            source_dir=str(source_dir),
            target_dir=str(target_dir),
            dry_run=False,
        )

    suffix = backup_suffix()
    copied: list[str] = []
    failed: list[dict[str, str]] = []
    backed_up: list[str] = []

    for name in pathmod.EXPECTED_PLUGIN_FILES:
        src = source_dir / name
        dst = target_dir / name
        try:
            if backup and dst.is_file():
                bak_path = target_dir / f"{name}{suffix}"
                shutil.copy2(dst, bak_path)
                backed_up.append(str(bak_path.name))
            shutil.copy2(src, dst)
            if name == _ENTRYPOINT and sys.platform != "win32":
                os.chmod(dst, 0o755)
            copied.append(name)
        except OSError as exc:
            failed.append({"name": name, "error": str(exc)})

    still_missing = pathmod.missing_plugin_files(target_dir)
    n_expected = len(pathmod.EXPECTED_PLUGIN_FILES)
    n_ok = n_expected - len(still_missing)

    if failed or still_missing:
        bits = []
        if failed:
            bits.append(f"{len(failed)} copy error(s)")
        if still_missing:
            bits.append(f"missing {', '.join(still_missing)}")
        detail = "; ".join(bits)
        return InstallReport(
            ok=False,
            code=ec.PLUGIN_NOT_FOUND,
            message=(
                f"Plugin install incomplete ({n_ok}/{n_expected} files) at {target_dir}: "
                f"{detail}. If copy failed, fully quit GIMP and re-run install."
            ),
            source_dir=str(source_dir),
            target_dir=str(target_dir),
            copied=copied,
            failed=failed,
            backed_up=backed_up,
            dry_run=False,
            restart_required=bool(copied),
        )

    return InstallReport(
        ok=True,
        code=None,
        message=(
            f"Plugin installed successfully ({n_expected}/{n_expected} files) to {target_dir}. "
            "Restart GIMP, then run: uv run gimp-agent doctor --strict. "
            "If copy failed, fully quit GIMP and re-run install."
        ),
        source_dir=str(source_dir),
        target_dir=str(target_dir),
        copied=copied,
        failed=failed,
        backed_up=backed_up,
        dry_run=False,
        restart_required=True,
    )


def uninstall_plugin(
    *,
    target: Path | None = None,
    dry_run: bool = False,
) -> InstallReport:
    """Delete only EXPECTED_PLUGIN_FILES names under the plug-in directory.

    Extra files in the target dir are left untouched. Empty dir is left in place
    (safe to delete manually).
    """
    if target is not None:
        target_dir = Path(target)
    else:
        found = pathmod.find_plugin_dir()
        if found is None:
            return _fail_report(
                message=(
                    "GIMP plug-in directory not found under GIMP 3.* user config; "
                    "pass --target <path>"
                ),
                dry_run=dry_run,
            )
        target_dir = found

    planned = plan_uninstall(target_dir)

    if dry_run:
        return InstallReport(
            ok=True,
            code=None,
            message=f"Dry-run: would remove {len(planned)} ship file(s) from {target_dir}",
            target_dir=str(target_dir),
            planned=planned,
            dry_run=True,
            restart_required=False,
        )

    removed: list[str] = []
    failed: list[dict[str, str]] = []
    skipped: list[str] = []

    for name in pathmod.EXPECTED_PLUGIN_FILES:
        dst = target_dir / name
        if not dst.is_file():
            skipped.append(name)
            continue
        try:
            dst.unlink()
            removed.append(name)
        except OSError as exc:
            failed.append({"name": name, "error": str(exc)})

    if failed:
        return InstallReport(
            ok=False,
            code=ec.PLUGIN_NOT_FOUND,
            message=(
                f"Plugin uninstall incomplete at {target_dir}: "
                f"{len(failed)} error(s). If delete failed, fully quit GIMP and re-run."
            ),
            target_dir=str(target_dir),
            copied=removed,  # reused field: names removed
            failed=failed,
            skipped=skipped,
            dry_run=False,
            restart_required=bool(removed),
        )

    return InstallReport(
        ok=True,
        code=None,
        message=(
            f"Plugin ship files removed ({len(removed)} deleted) from {target_dir}. "
            "Empty plug-in directory left in place (safe to delete manually). "
            "Restart GIMP if it is running."
        ),
        target_dir=str(target_dir),
        copied=removed,
        failed=failed,
        skipped=skipped,
        dry_run=False,
        restart_required=bool(removed),
    )
