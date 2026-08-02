"""Stdlib-only snapshot helpers for GIMP MCP visible-composite capture.

Deployable next to ``gimp-mcp-plugin.py`` under the GIMP plug-ins directory
(no third-party imports; same pattern as ``gimp_mcp_security``).

Used by:
- the GIMP plug-in (composite bitmap path, temp files, mapping payload)
- the MCP server (region key normalization, mapping/structuredContent)
- offline unit tests
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COMPOSITE_METHOD_MERGE = "merge_visible_layers_clip_to_image"
COMPOSITE_METHOD_FLATTEN = "flatten"
MODE_VISIBLE_COMPOSITE = "visible_composite"

ENV_WORKSPACE = "GIMP_WORKSPACE_ROOT"
SNAPSHOT_TMP_SUBDIR = ".gimp-mcp-tmp"


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
) -> dict[str, Any]:
    """Build structuredContent mapping for canvas↔snapshot coordinate recovery.

    When *region* is set, ``scale_* = rendered / region_*`` (region-relative).
    Full-canvas: ``scale_* = rendered / source_*``.
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
