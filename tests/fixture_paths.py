"""Path helpers for committed test fixtures under ``tests/fixtures/``.

Never mutate committed fixtures in place — always copy into a workspace.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

_WIN_DRIVE = re.compile(r"^[A-Za-z]:")


def fixture_path(name: str) -> Path:
    """Resolve a fixture by relative name (supports nested e.g. ``large/foo.png``).

    Rejects ``..``, empty names, POSIX/Windows absolute paths, and Windows drive
    prefixes. Backslashes are normalized to ``/`` before component parsing so
    ``..\\x`` is rejected on Linux CI the same as on Windows.
    """
    if not name or not str(name).strip():
        raise ValueError(f"fixture name must be a relative path without '..': {name!r}")
    # Reject Windows drive-letter absolute paths on every platform (CI is Linux).
    if _WIN_DRIVE.match(name):
        raise ValueError(f"fixture name must be a relative path without '..': {name!r}")
    # Normalize backslashes so ".." components are visible under PurePosixPath.
    normalized = name.replace("\\", "/")
    raw = Path(normalized)
    # Reject absolute paths before reassembly — on Windows, Path(*parts) can drop
    # absolute semantics for POSIX-style roots (e.g. "/etc/passwd" → "\etc\passwd").
    if raw.is_absolute() or normalized.startswith("/"):
        raise ValueError(f"fixture name must be a relative path without '..': {name!r}")
    rel = Path(*raw.parts) if raw.parts else Path()
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        raise ValueError(f"fixture name must be a relative path without '..': {name!r}")
    return FIXTURES_DIR / rel


def copy_fixture_to_workspace(name: str, dest_dir: Path) -> Path:
    """Copy a committed fixture into ``dest_dir``; never mutates the source.

    Returns the destination path. Destination filename is the leaf of ``name``.
    """
    src = fixture_path(name)
    if not src.is_file():
        raise FileNotFoundError(f"fixture not found: {name} ({src})")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    shutil.copy2(src, dest)
    return dest
