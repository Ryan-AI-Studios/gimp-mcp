"""Pure stdlib atomic path helpers for XCF save / raster export (track 0013).

Deployable next to ``gimp-mcp-plugin.py`` under the GIMP plug-ins directory
(no third-party imports; no GIMP/gi dependency).

Provides:
- collision policy parse / resolve (``fail`` | ``version`` | ``replace``)
- same-directory temp path with preserved suffix
- namespaced backup path selection
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CollisionMode = Literal["fail", "version", "replace"]
COLLISION_MODES: frozenset[str] = frozenset({"fail", "version", "replace"})
DEFAULT_COLLISION: CollisionMode = "fail"
VERSION_CAP = 10_000

# Product codes mirrored as strings so this module stays free of security import.
CODE_OUTPUT_COLLISION = "OUTPUT_COLLISION"
CODE_INTERNAL = "INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AtomicError(Exception):
    """Base for pure atomic-path failures (callers map ``code`` to CODE_*)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class OutputCollisionError(AtomicError):
    """Target path exists under ``collision=fail``."""

    def __init__(self, path: Path | str, message: str | None = None) -> None:
        p = Path(path)
        msg = message or f"output path already exists (collision=fail): {p}"
        self.path = p
        super().__init__(CODE_OUTPUT_COLLISION, msg)


class VersionCapExceededError(AtomicError):
    """``version`` mode found no free path within ``VERSION_CAP``."""

    def __init__(self, path: Path | str, message: str | None = None) -> None:
        p = Path(path)
        msg = message or (
            f"version collision exhausted after {VERSION_CAP} candidates "
            f"for base path {p} (not OUTPUT_COLLISION)"
        )
        self.path = p
        super().__init__(CODE_INTERNAL, msg)


# ---------------------------------------------------------------------------
# Resolved output
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedOutput:
    """Result of applying a collision policy to a requested path."""

    path: Path
    collision: CollisionMode
    collision_resolved: bool
    needs_backup: bool


# ---------------------------------------------------------------------------
# Parse / resolve
# ---------------------------------------------------------------------------


def parse_collision(raw: object, *, default: str = DEFAULT_COLLISION) -> CollisionMode:
    """Parse a collision mode string.

    ``None`` / empty → *default*. Invalid non-empty values raise ``ValueError``
    (host maps to POLICY_DENIED; CLI uses argparse choices before this).
    """
    if raw is None:
        mode = default
    elif isinstance(raw, str):
        mode = raw.strip().lower() if raw.strip() else default
    else:
        raise ValueError(
            f"invalid collision; must be fail/version/replace (got {type(raw).__name__})"
        )
    if mode not in COLLISION_MODES:
        raise ValueError(f"invalid collision; must be fail/version/replace (got {raw!r})")
    return mode  # type: ignore[return-value]


def _default_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def resolve_output_path(
    path: Path | str,
    mode: CollisionMode,
    *,
    exists: Callable[[Path], bool] | None = None,
) -> ResolvedOutput:
    """Resolve *path* under *mode* without writing.

    - ``fail``: if target exists → :class:`OutputCollisionError`
    - ``version``: if exists → first free ``{stem}-N{suffix}`` (N=1..VERSION_CAP);
      cap exhausted → :class:`VersionCapExceededError` (INTERNAL, not collision)
    - ``replace``: same path; ``needs_backup`` when target exists
    """
    final = Path(path)
    exists_fn = exists if exists is not None else _default_exists
    present = bool(exists_fn(final))

    if mode == "fail":
        if present:
            raise OutputCollisionError(final)
        return ResolvedOutput(
            path=final,
            collision="fail",
            collision_resolved=False,
            needs_backup=False,
        )

    if mode == "replace":
        return ResolvedOutput(
            path=final,
            collision="replace",
            collision_resolved=present,
            needs_backup=present,
        )

    # version
    if not present:
        return ResolvedOutput(
            path=final,
            collision="version",
            collision_resolved=False,
            needs_backup=False,
        )

    parent = final.parent
    stem = final.stem
    suffix = final.suffix  # includes leading dot, or ""
    for n in range(1, VERSION_CAP + 1):
        candidate = parent / f"{stem}-{n}{suffix}"
        if not exists_fn(candidate):
            return ResolvedOutput(
                path=candidate,
                collision="version",
                collision_resolved=True,
                needs_backup=False,
            )
    raise VersionCapExceededError(final)


# ---------------------------------------------------------------------------
# Temp / backup naming
# ---------------------------------------------------------------------------


def make_temp_path(
    final: Path | str,
    *,
    pid: int | None = None,
    token: str | None = None,
) -> Path:
    """Same-parent temp path preserving final suffix.

    Pattern: ``{stem}.gimp-mcp-{pid}-{token}{suffix}``
    """
    final_p = Path(final)
    use_pid = os.getpid() if pid is None else int(pid)
    use_token = token if token is not None else secrets.token_hex(4)
    name = f"{final_p.stem}.gimp-mcp-{use_pid}-{use_token}{final_p.suffix}"
    return final_p.parent / name


def make_backup_path(
    final: Path | str,
    *,
    exists: Callable[[Path], bool] | None = None,
    now_utc: datetime | None = None,
) -> Path:
    """Namespaced backup path for replace mode.

    Prefer ``{stem}.gimp-mcp.bak{suffix}``; if taken, use
    ``{stem}.gimp-mcp.{YYYYMMDDTHHMMSSZ}.bak{suffix}``.
    """
    final_p = Path(final)
    exists_fn = exists if exists is not None else _default_exists
    preferred = final_p.parent / f"{final_p.stem}.gimp-mcp.bak{final_p.suffix}"
    if not exists_fn(preferred):
        return preferred
    when = now_utc if now_utc is not None else datetime.now(UTC)
    # Always emit Z-suffixed UTC stamp (injectable for tests)
    if when.tzinfo is None:
        stamp = when.strftime("%Y%m%dT%H%M%SZ")
    else:
        stamp = when.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return final_p.parent / f"{final_p.stem}.gimp-mcp.{stamp}.bak{final_p.suffix}"
