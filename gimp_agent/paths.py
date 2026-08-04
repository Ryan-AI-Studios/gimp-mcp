"""GIMP binary and plug-in path discovery (Windows-primary, best-effort elsewhere)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Full product ship set that must live under plug-ins/gimp-mcp-plugin/
# (host-only modules gimp_mcp_state / gimp_mcp_surface are NOT included).
EXPECTED_PLUGIN_FILES: tuple[str, ...] = (
    "gimp-mcp-plugin.py",
    "gimp_mcp_security.py",
    "gimp_mcp_snapshot.py",
    "gimp_mcp_export.py",
    "gimp_mcp_handles.py",
    "gimp_mcp_coords.py",
    "gimp_mcp_policy.py",
    "gimp_mcp_atomic.py",
    "gimp_mcp_filters.py",
    "gimp_mcp_tx.py",
)

CONSOLE_CANDIDATES: tuple[str, ...] = (
    "gimp-console.exe",
    "gimp-console-3.exe",
    "gimp-console-3.2.exe",
    "gimp-console-3.0.exe",
)

GUI_CANDIDATES: tuple[str, ...] = (
    "gimp.exe",
    "gimp-3.exe",
    "gimp-3.2.exe",
    "gimp-3.0.exe",
)

ENV_CONSOLE = "GIMP_CONSOLE_EXE"
ENV_GUI = "GIMP_EXE"

# Subprocess timeout for --version probes
VERSION_TIMEOUT_S = 15.0

_PLUGIN_SUBDIR = Path("plug-ins") / "gimp-mcp-plugin"
_VERSION_DIR_RE = re.compile(r"^3\.\d+(?:\.\d+)*$")


def parse_semver_tuple(name: str) -> tuple[int, ...] | None:
    """Parse a GIMP config dir name like ``3.2`` or ``3.10`` into an int tuple.

    Lexical sort would put ``3.10`` before ``3.2``; int-tuple compares correctly.
    Returns None if not a ``3.*`` numeric version string.
    """
    text = name.strip()
    if not _VERSION_DIR_RE.match(text):
        return None
    parts: list[int] = []
    for piece in text.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            return None
    return tuple(parts) if parts else None


def _env_absolute(name: str) -> Path | None:
    raw = os.environ.get(name)
    if not raw or not str(raw).strip():
        return None
    p = Path(str(raw).strip())
    if p.is_absolute() and p.is_file():
        return p
    return None


def _path_lookup(names: tuple[str, ...]) -> Path | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def _winreg_app_path(exe_name: str) -> Path | None:
    """Best-effort HKLM/HKCU App Paths lookup (Windows only)."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None

    subkey = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, subkey) as key:
                # Default value of the App Paths key is the executable path
                value, _ = winreg.QueryValueEx(key, "")
            p = Path(str(value))
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def _candidate_install_bins() -> list[Path]:
    dirs: list[Path] = []
    if sys.platform == "win32":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        local = os.environ.get("LOCALAPPDATA")
        for base in (
            Path(pf) / "GIMP 3" / "bin",
            Path(pf) / "GIMP 3.2" / "bin",
        ):
            dirs.append(base)
        if local:
            dirs.append(Path(local) / "Programs" / "GIMP 3" / "bin")
    return dirs


def _scan_dirs_for(names: tuple[str, ...], dirs: list[Path]) -> Path | None:
    for d in dirs:
        for name in names:
            candidate = d / name
            if candidate.is_file():
                return candidate
    return None


def find_gimp_console() -> Path | None:
    """Locate gimp-console binary: env → PATH → App Paths → install dirs."""
    env = _env_absolute(ENV_CONSOLE)
    if env is not None:
        return env
    found = _path_lookup(CONSOLE_CANDIDATES)
    if found is not None:
        return found
    for name in CONSOLE_CANDIDATES:
        reg = _winreg_app_path(name)
        if reg is not None:
            return reg
    return _scan_dirs_for(CONSOLE_CANDIDATES, _candidate_install_bins())


def find_gimp_gui() -> Path | None:
    """Locate gimp GUI binary: env → PATH → App Paths → install dirs."""
    env = _env_absolute(ENV_GUI)
    if env is not None:
        return env
    found = _path_lookup(GUI_CANDIDATES)
    if found is not None:
        return found
    for name in GUI_CANDIDATES:
        reg = _winreg_app_path(name)
        if reg is not None:
            return reg
    return _scan_dirs_for(GUI_CANDIDATES, _candidate_install_bins())


def gimp_config_base() -> Path | None:
    """Return platform GIMP user config root (parent of ``3.*`` dirs), if known."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "GIMP"
        return None
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "GIMP"
    # Linux (and other POSIX)
    return Path.home() / ".config" / "GIMP"


def highest_gimp_version_dir(base: Path | None = None) -> Path | None:
    """Pick highest numeric ``3.*`` config directory under GIMP user base."""
    root = base if base is not None else gimp_config_base()
    if root is None or not root.is_dir():
        return None
    best: tuple[tuple[int, ...], Path] | None = None
    try:
        entries = list(root.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.is_dir():
            continue
        ver = parse_semver_tuple(entry.name)
        if ver is None:
            continue
        if best is None or ver > best[0]:
            best = (ver, entry)
    return best[1] if best else None


def find_plugin_dir(base: Path | None = None) -> Path | None:
    """Return ``…/GIMP/<highest 3.*>/plug-ins/gimp-mcp-plugin`` if parent exists.

    The directory itself need not exist yet (doctor reports missing files).
    Returns None only when no GIMP ``3.*`` config dir is found.
    """
    version_dir = highest_gimp_version_dir(base)
    if version_dir is None:
        return None
    return version_dir / _PLUGIN_SUBDIR


def missing_plugin_files(plugin_dir: Path | None) -> list[str]:
    """Return names from EXPECTED_PLUGIN_FILES missing under ``plugin_dir``."""
    if plugin_dir is None:
        return list(EXPECTED_PLUGIN_FILES)
    missing: list[str] = []
    for name in EXPECTED_PLUGIN_FILES:
        if not (plugin_dir / name).is_file():
            missing.append(name)
    return missing


def run_console_version(
    console: Path,
    *,
    timeout: float = VERSION_TIMEOUT_S,
) -> tuple[str | None, str | None]:
    """Run ``console --version``; return ``(stdout_strip_or_None, error_or_None)``.

    Uses absolute Path, capture_output, timeout ≤15s by default.
    """
    exe = Path(console)
    if not exe.is_absolute():
        exe = exe.resolve()
    try:
        completed = subprocess.run(
            [str(exe), "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if completed.returncode != 0:
        err_bits = (completed.stderr or "").strip() or (completed.stdout or "").strip()
        detail = f"exit {completed.returncode}"
        if err_bits:
            detail = f"{detail}: {err_bits}"
        return None, detail
    out = (completed.stdout or "").strip()
    if not out:
        # Prefer stdout; allow stderr only as a last resort when rc==0.
        out = (completed.stderr or "").strip()
    return (out or None), None
