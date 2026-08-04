"""Stdlib-only snapshot helpers for GIMP MCP visible-composite capture.

Deployable next to ``gimp-mcp-plugin.py`` under the GIMP plug-ins directory
(no third-party imports; same pattern as ``gimp_mcp_security``).

Used by:
- the GIMP plug-in (composite bitmap path, temp files, mapping payload)
- the MCP server (region key normalization, mapping/structuredContent)
- offline unit tests
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COMPOSITE_METHOD_MERGE = "merge_visible_layers_clip_to_image"
COMPOSITE_METHOD_FLATTEN = "flatten"
MODE_VISIBLE_COMPOSITE = "visible_composite"

ENV_WORKSPACE = "GIMP_WORKSPACE_ROOT"
ENV_SNAPSHOT_WRITE = "GIMP_MCP_SNAPSHOT_WRITE"
ENV_SNAPSHOT_DIR = "GIMP_MCP_SNAPSHOT_DIR"
SNAPSHOT_TMP_SUBDIR = ".gimp-mcp-tmp"
SNAPSHOT_WRITE_SUBDIR = "snapshots"

# Snapshot edge / timeout policy (track 0023)
DEFAULT_SNAPSHOT_MAX_EDGE = 1024
HARD_MAX_SNAPSHOT_EDGE = 4096
MAX_REGION_EDGE = 8192
DEFAULT_COMMAND_TIMEOUT_S = 60.0
MIN_COMMAND_TIMEOUT_S = 5.0
MAX_COMMAND_TIMEOUT_S = 600.0

ENV_SNAPSHOT_MAX_EDGE = "GIMP_MCP_SNAPSHOT_MAX_EDGE"
ENV_SNAPSHOT_HARD_MAX_EDGE = "GIMP_MCP_SNAPSHOT_HARD_MAX_EDGE"
ENV_COMMAND_TIMEOUT_S = "GIMP_MCP_COMMAND_TIMEOUT_S"

# Truthy / falsey sets aligned with product env conventions (+ explicit off for default-on).
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})


# ---------------------------------------------------------------------------
# Snapshot budget (track 0023)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResolvedSnapshotBudget:
    """Complete max box for a snapshot request plus optional filled region."""

    max_width: int
    max_height: int
    region: dict[str, Any] | None = None


def hard_max_snapshot_edge(environ: Mapping[str, str] | None = None) -> int:
    """Resolve absolute snapshot edge ceiling (default 4096). Invalid env → default."""
    env = environ if environ is not None else os.environ
    raw = env.get(ENV_SNAPSHOT_HARD_MAX_EDGE)
    if raw is None or str(raw).strip() == "":
        return HARD_MAX_SNAPSHOT_EDGE
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return HARD_MAX_SNAPSHOT_EDGE
    if value <= 0:
        return HARD_MAX_SNAPSHOT_EDGE
    return value


def default_snapshot_max_edge(environ: Mapping[str, str] | None = None) -> int:
    """Resolve default max edge (default 1024), clamped to hard max. Invalid → default."""
    env = environ if environ is not None else os.environ
    hard = hard_max_snapshot_edge(environ)
    raw = env.get(ENV_SNAPSHOT_MAX_EDGE)
    if raw is None or str(raw).strip() == "":
        return min(DEFAULT_SNAPSHOT_MAX_EDGE, hard)
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return min(DEFAULT_SNAPSHOT_MAX_EDGE, hard)
    if value <= 0:
        return min(DEFAULT_SNAPSHOT_MAX_EDGE, hard)
    return min(value, hard)


def clamp_edge(value: Any, *, hard_max: int | None = None) -> int:
    """Clamp a positive edge to *hard_max* (default: env-resolved hard max).

    Call only with non-None resolved values. ``None`` means “use default” and
    must be handled by :func:`resolve_snapshot_max_box`, not here.
    """
    if value is None:
        raise TypeError("clamp_edge requires a non-None value")
    try:
        edge = int(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"edge must be a positive integer, got {value!r}") from e
    if edge <= 0:
        raise ValueError(f"edge must be positive, got {edge}")
    ceiling = hard_max if hard_max is not None else hard_max_snapshot_edge()
    try:
        ceiling_i = int(ceiling)
    except (TypeError, ValueError):
        ceiling_i = HARD_MAX_SNAPSHOT_EDGE
    if ceiling_i <= 0:
        ceiling_i = HARD_MAX_SNAPSHOT_EDGE
    return min(edge, ceiling_i)


def validate_region_edges(region: Mapping[str, Any] | None) -> None:
    """Reject region width/height above ``MAX_REGION_EDGE`` (source crop cap).

    Raises:
        ValueError: when width or height exceeds the cap.
    """
    if region is None:
        return
    for key in ("width", "height"):
        if key not in region or region[key] is None:
            continue
        try:
            dim = int(region[key])
        except (TypeError, ValueError) as e:
            raise ValueError(f"region {key} must be an integer, got {region[key]!r}") from e
        if dim > MAX_REGION_EDGE:
            raise ValueError(
                f"region {key} {dim} exceeds MAX_REGION_EDGE {MAX_REGION_EDGE} "
                f"(source crop cap; output still limited by hard max edge "
                f"{HARD_MAX_SNAPSHOT_EDGE})"
            )


def command_timeout_s(environ: Mapping[str, str] | None = None) -> float:
    """Host TCP command I/O timeout seconds (default 60; clamp 5-600). Invalid env → default."""
    env = environ if environ is not None else os.environ
    raw = env.get(ENV_COMMAND_TIMEOUT_S)
    if raw is None or str(raw).strip() == "":
        return DEFAULT_COMMAND_TIMEOUT_S
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_COMMAND_TIMEOUT_S
    if value != value:  # NaN
        return DEFAULT_COMMAND_TIMEOUT_S
    if value < MIN_COMMAND_TIMEOUT_S:
        return MIN_COMMAND_TIMEOUT_S
    if value > MAX_COMMAND_TIMEOUT_S:
        return MAX_COMMAND_TIMEOUT_S
    return float(value)


def resolve_snapshot_max_box(
    max_width: int | None = None,
    max_height: int | None = None,
    *,
    max_size: int | None = None,
    region: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> ResolvedSnapshotBudget:
    """Resolve a complete snapshot max box and optionally fill region max_* (M3).

    Rules (product lock 0023):
    1. ``max_size`` (advanced path) maps to both dims; ``max_size<=0`` rejected.
    2. Both ``max_width`` and ``max_height`` → use them (clamped).
    3. Only one dim → square box after clamp.
    4. Neither → default max edge (env) for both.
    5. Region width/height validated against ``MAX_REGION_EDGE``.
    6. Region max fill: both → clamp; one → fill missing from full box; none → inherit box.
    7. Always returns a complete ``(max_width, max_height)``.
    8. :func:`clamp_edge` is only called on non-None values.
    """
    hard = hard_max_snapshot_edge(environ)

    if max_size is not None:
        try:
            size_i = int(max_size)
        except (TypeError, ValueError) as e:
            raise ValueError(f"max_size must be a positive integer, got {max_size!r}") from e
        if size_i <= 0:
            raise ValueError(f"max_size must be positive, got {size_i}")
        edge = clamp_edge(size_i, hard_max=hard)
        mw = mh = edge
    else:
        has_w = max_width is not None
        has_h = max_height is not None
        if has_w and has_h:
            mw = clamp_edge(max_width, hard_max=hard)
            mh = clamp_edge(max_height, hard_max=hard)
        elif has_w:
            mw = mh = clamp_edge(max_width, hard_max=hard)
        elif has_h:
            mw = mh = clamp_edge(max_height, hard_max=hard)
        else:
            mw = mh = default_snapshot_max_edge(environ)

    region_out: dict[str, Any] | None = None
    if region is not None:
        region_out = normalize_region(region)
        if region_out is not None:
            validate_region_edges(region_out)
            rmw = region_out.get("max_width")
            rmh = region_out.get("max_height")
            if rmw is not None and rmh is not None:
                region_out["max_width"] = clamp_edge(rmw, hard_max=hard)
                region_out["max_height"] = clamp_edge(rmh, hard_max=hard)
            elif rmw is not None:
                region_out["max_width"] = clamp_edge(rmw, hard_max=hard)
                region_out["max_height"] = mh
            elif rmh is not None:
                region_out["max_width"] = mw
                region_out["max_height"] = clamp_edge(rmh, hard_max=hard)
            else:
                region_out["max_width"] = mw
                region_out["max_height"] = mh

    return ResolvedSnapshotBudget(max_width=mw, max_height=mh, region=region_out)


def snapshot_budget_probe_fields(
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolved snapshot budget fields for ``session_probe`` honesty."""
    return {
        "default_max_edge": default_snapshot_max_edge(environ),
        "hard_max_edge": hard_max_snapshot_edge(environ),
        "max_region_edge": MAX_REGION_EDGE,
        "command_timeout_s": command_timeout_s(environ),
        "env_names": {
            "snapshot_max_edge": ENV_SNAPSHOT_MAX_EDGE,
            "snapshot_hard_max_edge": ENV_SNAPSHOT_HARD_MAX_EDGE,
            "command_timeout_s": ENV_COMMAND_TIMEOUT_S,
        },
        "guidance": (
            "region-first detail; omit max_* → default edge 1024; hard max 4096; "
            "huge layer stacks use orient_workspace(summary_only=True)"
        ),
    }


# ---------------------------------------------------------------------------
# Region normalization
# ---------------------------------------------------------------------------


def normalize_region(region: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Normalize a region dict to canonical origin_x/origin_y keys.

    Accepts ``x``/``y`` or ``origin_x``/``origin_y``. Rejects negative values.
    Returns ``None`` for empty/None input. Optional ``max_width``/``max_height``
    are preserved when present.
    """
    if region is None:
        return None
    if not isinstance(region, Mapping):
        raise TypeError(f"region must be a mapping, got {type(region).__name__}")
    if len(region) == 0:
        return None

    out: dict[str, Any] = {}

    has_ox = "origin_x" in region or "x" in region
    has_oy = "origin_y" in region or "y" in region
    if has_ox:
        ox = region["origin_x"] if "origin_x" in region else region["x"]
        if ox is not None:
            ox_i = int(ox)
            if ox_i < 0:
                raise ValueError(f"region origin_x/x must be non-negative, got {ox_i}")
            out["origin_x"] = ox_i
    if has_oy:
        oy = region["origin_y"] if "origin_y" in region else region["y"]
        if oy is not None:
            oy_i = int(oy)
            if oy_i < 0:
                raise ValueError(f"region origin_y/y must be non-negative, got {oy_i}")
            out["origin_y"] = oy_i

    for key in ("width", "height", "max_width", "max_height"):
        if key in region and region[key] is not None:
            val = int(region[key])
            if val < 0:
                raise ValueError(f"region {key} must be non-negative, got {val}")
            out[key] = val

    return out if out else None


# ---------------------------------------------------------------------------
# Fit scale
# ---------------------------------------------------------------------------


def compute_fit_scale(src_w: int, src_h: int, max_w: int, max_h: int) -> tuple[int, int]:
    """Aspect-preserving fit of ``src`` into ``max`` box; returns (target_w, target_h)."""
    if src_w <= 0 or src_h <= 0:
        raise ValueError(f"source dimensions must be positive, got {src_w}x{src_h}")
    if max_w <= 0 or max_h <= 0:
        raise ValueError(f"max dimensions must be positive, got {max_w}x{max_h}")

    aspect = src_w / src_h
    max_aspect = max_w / max_h
    if aspect > max_aspect:
        target_w = int(max_w)
        target_h = max(1, int(max_w / aspect))
    else:
        target_h = int(max_h)
        target_w = max(1, int(max_h * aspect))
    return target_w, target_h


# ---------------------------------------------------------------------------
# Mapping metadata
# ---------------------------------------------------------------------------


def build_mapping_metadata(
    *,
    image_index: int,
    source_width: int,
    source_height: int,
    rendered_width: int,
    rendered_height: int,
    region: Mapping[str, Any] | None = None,
    composite_method: str = COMPOSITE_METHOD_MERGE,
    mode: str = MODE_VISIBLE_COMPOSITE,
    pixel_orientation_normalized: bool = False,
    exif_orientation_original: int | None = None,
) -> dict[str, Any]:
    """Build structuredContent mapping for canvas↔snapshot coordinate recovery.

    When *region* is set, ``scale_* = rendered / region_*`` (region-relative).
    Full-canvas: ``scale_* = rendered / source_*``.

    Additive coordinate-declaration fields (track 0008): coordinate space,
    axes, padding=0, view_rotation_ignored, and snapshot-time EXIF/normalize
    honesty flags. Existing keys are unchanged.
    """
    region_out: dict[str, int] | None = None
    if region is not None:
        # Prefer already-normalized keys; fall back to x/y.
        try:
            norm = normalize_region(region)
        except (TypeError, ValueError):
            norm = None
        if norm is not None and all(k in norm for k in ("origin_x", "origin_y", "width", "height")):
            region_out = {
                "origin_x": int(norm["origin_x"]),
                "origin_y": int(norm["origin_y"]),
                "width": int(norm["width"]),
                "height": int(norm["height"]),
            }
            rw = region_out["width"]
            rh = region_out["height"]
            if rw <= 0 or rh <= 0:
                raise ValueError(f"region dimensions must be positive, got {rw}x{rh}")
            scale_x = rendered_width / rw
            scale_y = rendered_height / rh
        else:
            # Incomplete region object — treat as full canvas for scale.
            if source_width <= 0 or source_height <= 0:
                raise ValueError(
                    f"source dimensions must be positive, got {source_width}x{source_height}"
                )
            scale_x = rendered_width / source_width
            scale_y = rendered_height / source_height
    else:
        if source_width <= 0 or source_height <= 0:
            raise ValueError(
                f"source dimensions must be positive, got {source_width}x{source_height}"
            )
        scale_x = rendered_width / source_width
        scale_y = rendered_height / source_height

    return {
        "mode": mode,
        "image_index": int(image_index),
        "source_width": int(source_width),
        "source_height": int(source_height),
        "rendered_width": int(rendered_width),
        "rendered_height": int(rendered_height),
        "scale_x": float(scale_x),
        "scale_y": float(scale_y),
        "region": region_out,
        "composite_method": composite_method,
        # Coordinate declaration (0008) — additive; padding 0 under resize-fit
        "coordinate_space": "image-pixels",
        "origin": "top-left",
        "x_axis": "right",
        "y_axis": "down",
        "preview_padding_x": 0,
        "preview_padding_y": 0,
        "view_rotation_ignored": True,
        "pixel_orientation_normalized": bool(pixel_orientation_normalized),
        "exif_orientation_original": exif_orientation_original,
    }


# ---------------------------------------------------------------------------
# Image index selection (pure, for tests + shared validation semantics)
# ---------------------------------------------------------------------------


def select_image_index(images: Sequence[Any], index: int) -> Any:
    """Return ``images[index]`` or raise ``IndexError`` for out-of-range/negative."""
    n = len(images)
    if index < 0:
        raise IndexError(f"image_index {index} is negative")
    if index >= n:
        raise IndexError(f"image_index {index} out of range (only {n} images open)")
    return images[index]


# ---------------------------------------------------------------------------
# Temp path policy (spec §2.3)
# ---------------------------------------------------------------------------


def ensure_snapshot_temp_dir() -> Path:
    """Create and return the snapshot temp directory per workspace/pid policy.

    - If ``GIMP_WORKSPACE_ROOT`` is set → ``{root}/.gimp-mcp-tmp/``
    - Else → ``{gettempdir()}/gimp-mcp-{pid}/``

    Restrictive permissions (0o700) applied where the OS allows.
    """
    root_raw = os.environ.get(ENV_WORKSPACE)
    if root_raw is not None and str(root_raw).strip() != "":
        d = Path(str(root_raw).strip()) / SNAPSHOT_TMP_SUBDIR
    else:
        d = Path(tempfile.gettempdir()) / f"gimp-mcp-{os.getpid()}"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass  # Windows / non-POSIX may not honor fully
    return d


def snapshot_temp_path(prefix: str = "snapshot-", suffix: str = ".png") -> Path:
    """Allocate a unique temp file path under the snapshot temp directory."""
    d = ensure_snapshot_temp_dir()
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=str(d))
    os.close(fd)
    return Path(path)


# ---------------------------------------------------------------------------
# Filesystem dual-delivery write path (track 0021)
# ---------------------------------------------------------------------------


def snapshot_write_enabled(
    environ: Mapping[str, str] | None = None,
    param: bool | None = None,
) -> bool:
    """Return whether jailed snapshot PNG write is enabled.

    ``param`` True/False wins when not None. When ``param`` is None, env
    ``GIMP_MCP_SNAPSHOT_WRITE`` is consulted: default **on** when unset/empty;
    ``0``/``false``/``no``/``off`` turn it off; ``1``/``true``/``yes``/``on`` on.
    """
    if param is not None:
        return bool(param)
    env = environ if environ is not None else os.environ
    raw = env.get(ENV_SNAPSHOT_WRITE)
    if raw is None or str(raw).strip() == "":
        return True
    val = str(raw).strip().lower()
    if val in _FALSY:
        return False
    if val in _TRUTHY:
        return True
    # Unknown non-empty value: keep default-on posture.
    return True


def _normalize_resolved(path: Path) -> Path:
    """Resolve and normalize Windows drive-letter casing for comparisons."""
    resolved = path.resolve()
    s = str(resolved)
    if len(s) >= 2 and s[1] == ":":
        s = s[0].upper() + s[1:]
        return Path(s)
    return resolved


def _path_under_root(candidate: Path, root: Path) -> bool:
    """Return True if *candidate* is under *root* (both resolved)."""
    try:
        target = _normalize_resolved(candidate)
        root_resolved = _normalize_resolved(root)
    except (OSError, RuntimeError):
        return False
    try:
        if hasattr(target, "is_relative_to"):
            return target.is_relative_to(root_resolved)
        common = os.path.commonpath([str(root_resolved), str(target)])
        return _normalize_resolved(Path(common)) == root_resolved
    except ValueError:
        return False


def resolve_snapshot_write_dir(
    environ: Mapping[str, str] | None = None,
    *,
    create: bool = True,
) -> Path:
    """Resolve the directory for dual-delivery snapshot PNG writes.

    Default: ``{ensure_snapshot_temp_dir()}/snapshots/``.
    If ``GIMP_MCP_SNAPSHOT_DIR`` is set, resolve it under the workspace jail
    (``GIMP_WORKSPACE_ROOT``); paths outside the jail raise ``ValueError``.
    Creates the directory with mode ``0o700`` best-effort when *create* is True.

    When *environ* is provided, ``GIMP_WORKSPACE_ROOT`` / ``GIMP_MCP_SNAPSHOT_DIR``
    are read from that mapping (tests may pass a dict without mutating
    ``os.environ`` for the override path). The default base still uses
    :func:`ensure_snapshot_temp_dir` (``os.environ``) unless workspace root
    is present in *environ*.
    """
    env = environ if environ is not None else os.environ
    override = env.get(ENV_SNAPSHOT_DIR)
    if override is not None and str(override).strip() != "":
        root_raw = env.get(ENV_WORKSPACE)
        if root_raw is None or str(root_raw).strip() == "":
            raise ValueError(f"{ENV_SNAPSHOT_DIR} requires {ENV_WORKSPACE} (workspace jail)")
        root = Path(str(root_raw).strip())
        candidate = Path(str(override).strip())
        if not candidate.is_absolute():
            candidate = root / candidate
        if not _path_under_root(candidate, root):
            raise ValueError(f"{ENV_SNAPSHOT_DIR} escapes workspace root: {candidate}")
        d = _normalize_resolved(candidate)
    else:
        root_raw = env.get(ENV_WORKSPACE) if environ is not None else None
        if root_raw is not None and str(root_raw).strip() != "":
            base = Path(str(root_raw).strip()) / SNAPSHOT_TMP_SUBDIR
            if create:
                base.mkdir(parents=True, exist_ok=True)
                try:
                    os.chmod(base, 0o700)
                except OSError:
                    pass
        else:
            base = ensure_snapshot_temp_dir()
        d = base / SNAPSHOT_WRITE_SUBDIR

    if create:
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
    return d


def _unique_snap_name(directory: Path, *, pid: int | None = None) -> str:
    """Allocate a unique ``snap-{utc}-{pid}-{n}.png`` filename under *directory*."""
    pid_i = int(pid if pid is not None else os.getpid())
    utc = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    for n in range(0, 10_000):
        name = f"snap-{utc}-{pid_i}-{n}.png"
        if not (directory / name).exists():
            return name
    return f"snap-{utc}-{pid_i}-{time.time_ns()}.png"


def write_snapshot_png(
    data: bytes,
    *,
    environ: Mapping[str, str] | None = None,
    write_dir: Path | str | None = None,
    param: bool | None = None,
) -> dict[str, Any]:
    """Write PNG *data* under the jailed snapshot write directory.

    Returns a dict with keys:
    - ``ok`` (bool)
    - ``filesystem_path`` (abs str) when written
    - ``filesystem_write`` (bool)
    - optional ``filesystem_sha256`` (hex)
    - optional ``filesystem_error`` (non-secret message)

    Never raises for ordinary I/O failure — callers treat write as non-fatal.
    """
    if not snapshot_write_enabled(environ=environ, param=param):
        return {
            "ok": False,
            "filesystem_write": False,
            "filesystem_path": None,
        }

    try:
        if write_dir is not None:
            d = Path(write_dir)
            d.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(d, 0o700)
            except OSError:
                pass
        else:
            d = resolve_snapshot_write_dir(environ=environ, create=True)

        path: Path | None = None
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        last_err: Exception | None = None
        for _ in range(8):
            name = _unique_snap_name(d)
            candidate = d / name
            try:
                fd = os.open(str(candidate), flags, 0o600)
            except OSError as e:
                last_err = e
                continue
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
            except Exception:
                try:
                    if candidate.exists():
                        candidate.unlink()
                except OSError:
                    pass
                raise
            path = candidate
            break
        if path is None:
            raise OSError(f"Could not create unique snapshot file: {last_err}")

        digest = hashlib.sha256(data).hexdigest()
        abs_path = str(_normalize_resolved(path))
        try:
            prune_snapshot_write_dir(d, max_files=50)
        except Exception:
            pass  # prune is best-effort; never fail the write result
        return {
            "ok": True,
            "filesystem_write": True,
            "filesystem_path": abs_path,
            "filesystem_sha256": digest,
        }
    except Exception as e:
        # Non-secret, short error string only.
        msg = f"{type(e).__name__}: {e}"
        if len(msg) > 200:
            msg = msg[:200]
        return {
            "ok": False,
            "filesystem_write": False,
            "filesystem_path": None,
            "filesystem_error": msg,
        }


def prune_snapshot_write_dir(
    directory: Path | str,
    *,
    max_files: int = 50,
) -> int:
    """Best-effort: delete oldest excess ``snap-*.png`` files only.

    Returns the number of files deleted. Ignores ``OSError``. Does not delete
    non-matching names.
    """
    d = Path(directory)
    if max_files < 0:
        max_files = 0
    try:
        if not d.is_dir():
            return 0
        files = [
            p
            for p in d.iterdir()
            if p.is_file() and p.name.startswith("snap-") and p.suffix.lower() == ".png"
        ]
    except OSError:
        return 0

    if len(files) <= max_files:
        return 0

    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    files.sort(key=_mtime)
    excess = len(files) - max_files
    deleted = 0
    for p in files[:excess]:
        try:
            p.unlink()
            deleted += 1
        except OSError:
            pass
    return deleted


def merge_filesystem_fields(
    mapping: MutableMapping[str, Any],
    write_result: Mapping[str, Any],
) -> MutableMapping[str, Any]:
    """Merge dual-delivery filesystem_* fields from *write_result* into *mapping*.

    Mutates and returns *mapping*. Always sets ``filesystem_write``; sets path /
    sha256 / error only when present and meaningful.
    """
    wrote = bool(write_result.get("filesystem_write")) or bool(write_result.get("ok"))
    # Prefer explicit filesystem_write key when present.
    if "filesystem_write" in write_result:
        wrote = bool(write_result["filesystem_write"])
    mapping["filesystem_write"] = wrote

    path = write_result.get("filesystem_path")
    if wrote and path:
        mapping["filesystem_path"] = str(path)
    elif "filesystem_path" in mapping and not wrote:
        # Do not leave a stale path when write failed/disabled.
        mapping.pop("filesystem_path", None)

    if wrote and write_result.get("filesystem_sha256"):
        mapping["filesystem_sha256"] = str(write_result["filesystem_sha256"])
    else:
        mapping.pop("filesystem_sha256", None)

    err = write_result.get("filesystem_error")
    if err and not wrote:
        mapping["filesystem_error"] = str(err)
    else:
        mapping.pop("filesystem_error", None)

    return mapping


# ---------------------------------------------------------------------------
# PNG validation (fail-closed export)
# ---------------------------------------------------------------------------

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MIN_PNG_BYTES = 8  # signature length; empty mkstemp files fail this check


def validate_png_bytes(data: bytes) -> bool:
    """Return True if *data* is non-empty and starts with the PNG signature.

    Used after export so an empty mkstemp file or garbage write cannot be
    base64-encoded and returned as a successful snapshot.
    """
    return len(data) >= MIN_PNG_BYTES and data[:8] == PNG_SIGNATURE


def validate_png_file(path: str | Path) -> bool:
    """Return True if *path* exists and contains a valid PNG signature."""
    try:
        p = Path(path)
        if not p.is_file():
            return False
        with p.open("rb") as f:
            head = f.read(MIN_PNG_BYTES)
        return validate_png_bytes(head)
    except OSError:
        return False
