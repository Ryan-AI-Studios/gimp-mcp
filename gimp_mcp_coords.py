"""Pure stdlib coordinate math + EXIF orientation op table (track 0008).

Shipped next to ``gimp-mcp-plugin.py`` as the **7th** plug-in install file and
imported by the MCP host. No third-party imports; no GIMP/gi dependency.

Coordinate space (locked):
  coordinate_space: "image-pixels"
  origin: "top-left"
  x_axis: "right"
  y_axis: "down"
  view_rotation_ignored: True

Offset semantics: prefer **absolute canvas** offsets as returned by GIMP
``layer.get_offsets()`` (common libgimp convention). Nested group children
should use the absolute canvas offset; if a future GIMP build returns
parent-relative offsets, accumulation would be required — document that
deviation when proven.

Rounding: ``int(round(x))`` — Python half-even (banker's rounding).
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Coordinate-space declaration constants
# ---------------------------------------------------------------------------

COORDINATE_SPACE = "image-pixels"
ORIGIN = "top-left"
X_AXIS = "right"
Y_AXIS = "down"
VIEW_ROTATION_IGNORED = True

# Alias constant required by track contract.
view_rotation_ignored = True

# Default padding under resize-fit (letterbox product change declined).
DEFAULT_PREVIEW_PADDING_X = 0
DEFAULT_PREVIEW_PADDING_Y = 0

# Mode tokens for normalize_image_orientation
MODE_ASSUME_PIXELS_UPRIGHT = "assume_pixels_upright"
MODE_TRUST_TAG = "trust_tag"
NORMALIZE_MODES = frozenset({MODE_ASSUME_PIXELS_UPRIGHT, MODE_TRUST_TAG})

# Op tokens for ORIENTATION_OPS (apply left→right)
OP_FLIP_H = "flip_h"
OP_FLIP_V = "flip_v"
OP_ROT90 = "rot90"
OP_ROT180 = "rot180"
OP_ROT270 = "rot270"
OP_TOKENS = frozenset({OP_FLIP_H, OP_FLIP_V, OP_ROT90, OP_ROT180, OP_ROT270})

# EXIF Orientation 1-8 -> ordered pixel ops (H2). Tags 5/7 are transpose /
# transverse compositions: flip_h first, then rotation.
ORIENTATION_OPS: dict[int, list[str]] = {
    1: [],
    2: [OP_FLIP_H],
    3: [OP_ROT180],
    4: [OP_FLIP_V],
    5: [OP_FLIP_H, OP_ROT90],
    6: [OP_ROT90],
    7: [OP_FLIP_H, OP_ROT270],
    8: [OP_ROT270],
}

# Required declaration keys (additive mapping contract)
_DECLARATION_REQUIRED: dict[str, Any] = {
    "coordinate_space": COORDINATE_SPACE,
    "origin": ORIGIN,
    "x_axis": X_AXIS,
    "y_axis": Y_AXIS,
}


def validate_declaration(decl: Any) -> dict[str, Any]:
    """Validate and return a normalized coordinate-declaration dict.

    Raises ``ValueError`` on missing/invalid fields. Accepts a mapping with
    the locked coordinate-space fields; unknown keys are preserved.
    """
    if not isinstance(decl, dict):
        raise ValueError("declaration must be a dict/object")
    out: dict[str, Any] = dict(decl)
    for key, expected in _DECLARATION_REQUIRED.items():
        if key not in out:
            raise ValueError(f"declaration missing required field '{key}'")
        if out[key] != expected:
            raise ValueError(f"declaration field '{key}' must be {expected!r}, got {out[key]!r}")
    # view_rotation_ignored defaults to True when absent
    if "view_rotation_ignored" in out:
        if out["view_rotation_ignored"] is not True:
            raise ValueError(
                "declaration field 'view_rotation_ignored' must be true "
                f"(got {out['view_rotation_ignored']!r})"
            )
    else:
        out["view_rotation_ignored"] = True
    # Optional padding — must be non-negative numbers if present
    for pad_key in ("preview_padding_x", "preview_padding_y"):
        if pad_key in out:
            try:
                pv = float(out[pad_key])
            except (TypeError, ValueError) as e:
                raise ValueError(f"declaration field '{pad_key}' must be a number") from e
            if pv < 0:
                raise ValueError(f"declaration field '{pad_key}' must be >= 0")
            out[pad_key] = pv
    return out


def orientation_is_identity(tag: int | None) -> bool:
    """True when orientation tag is None (absent) or EXIF 1 (normal).

    Tags 2-8 are non-identity. Present-but-invalid ints (outside 1-8) are
    also non-identity so malformed EXIF is not reported as normalized.
    """
    if tag is None:
        return True
    try:
        return int(tag) == 1
    except (TypeError, ValueError):
        return False


def orientation_for_manifest(tag: int | None) -> int | None:
    """Clamp tag to schema 1..8 or null (invalid → null for state-manifest)."""
    if tag is None:
        return None
    try:
        v = int(tag)
    except (TypeError, ValueError):
        return None
    if 1 <= v <= 8:
        return v
    return None


def plan_normalize_ops(mode: str, original_orientation: int | None) -> dict[str, Any]:
    """Pure plan for normalize_image_orientation (offline-testable).

    Returns:
      mode, ops (ordered list), applied (bool), write_tags (always True on
      success path intent), identity_before (bool).
    Raises ValueError for unknown mode.
    """
    if mode not in NORMALIZE_MODES:
        raise ValueError(f"mode must be one of {sorted(NORMALIZE_MODES)}, got {mode!r}")
    ops: list[str] = []
    applied = False
    if mode == MODE_TRUST_TAG:
        if original_orientation is not None:
            try:
                t = int(original_orientation)
            except (TypeError, ValueError):
                t = None
            if t is not None and 2 <= t <= 8:
                ops = list(ORIENTATION_OPS.get(t, []))
                applied = bool(ops)
    return {
        "mode": mode,
        "ops": ops,
        "applied": applied,
        "write_tags": True,
        "identity_before": orientation_is_identity(original_orientation),
    }


def _round_px(value: float) -> int:
    """Pixel rounding: Python half-even via ``int(round(x))`` (spec-locked form)."""
    return int(round(value))  # noqa: RUF046


def preview_to_image_xy(
    preview_x: float,
    preview_y: float,
    *,
    scale_x: float,
    scale_y: float,
    region_origin_x: float = 0,
    region_origin_y: float = 0,
    preview_padding_x: float = 0,
    preview_padding_y: float = 0,
) -> tuple[int, int]:
    """Map preview (snapshot) pixel coords → full-canvas image-pixel coords.

    Formula (spec §7.2)::

        image_x = region_origin_x + (preview_x - preview_padding_x) / scale_x
        image_y = region_origin_y + (preview_y - preview_padding_y) / scale_y
    """
    if scale_x == 0:
        raise ValueError("scale_x must be non-zero")
    if scale_y == 0:
        raise ValueError("scale_y must be non-zero")
    image_x = region_origin_x + (float(preview_x) - float(preview_padding_x)) / float(scale_x)
    image_y = region_origin_y + (float(preview_y) - float(preview_padding_y)) / float(scale_y)
    return _round_px(image_x), _round_px(image_y)


def image_to_preview_xy(
    image_x: float,
    image_y: float,
    *,
    scale_x: float,
    scale_y: float,
    region_origin_x: float = 0,
    region_origin_y: float = 0,
    preview_padding_x: float = 0,
    preview_padding_y: float = 0,
) -> tuple[int, int]:
    """Inverse of ``preview_to_image_xy`` (image-pixel → preview)."""
    preview_x = float(preview_padding_x) + (float(image_x) - float(region_origin_x)) * float(
        scale_x
    )
    preview_y = float(preview_padding_y) + (float(image_y) - float(region_origin_y)) * float(
        scale_y
    )
    return _round_px(preview_x), _round_px(preview_y)


def layer_local_to_image_xy(
    local_x: float,
    local_y: float,
    offset_x: float,
    offset_y: float,
) -> tuple[int, int]:
    """Map layer-local coords → image canvas coords: ``image = local + offset``."""
    return _round_px(float(local_x) + float(offset_x)), _round_px(float(local_y) + float(offset_y))


def image_to_layer_local_xy(
    image_x: float,
    image_y: float,
    offset_x: float,
    offset_y: float,
) -> tuple[int, int]:
    """Inverse: ``local = image - offset``."""
    return _round_px(float(image_x) - float(offset_x)), _round_px(float(image_y) - float(offset_y))


def clamp_to_image(
    x: float,
    y: float,
    width: int,
    height: int,
) -> tuple[int, int]:
    """Clamp integer pixel coords into ``[0, width-1]`` x ``[0, height-1]``.

    Empty/non-positive dimensions clamp to ``(0, 0)``.
    """
    xi = _round_px(float(x))
    yi = _round_px(float(y))
    if width <= 0 or height <= 0:
        return 0, 0
    if xi < 0:
        xi = 0
    elif xi >= width:
        xi = width - 1
    if yi < 0:
        yi = 0
    elif yi >= height:
        yi = height - 1
    return xi, yi
