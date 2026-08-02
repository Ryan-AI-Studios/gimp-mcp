"""Stdlib-only export helpers for GIMP MCP alpha-preserving export (Issue 16 / 0005).

Deployable next to ``gimp-mcp-plugin.py`` under the GIMP plug-ins directory
(no third-party imports; same pattern as ``gimp_mcp_security`` / ``gimp_mcp_snapshot``).

Used by:
- the GIMP plug-in (export policy, PNG IHDR verify, result builders)
- the MCP server (capability matrix for docs/tools)
- offline unit tests

Phase-0 / GIMP 3.2.4 PNG pixel format (from GNOME file-png.c EXPORT_PROC):

- Property name: ``"format"`` (choice argument ``gimp_procedure_add_choice_argument``)
- RGBA 8-bit choice id: ``"rgba8"`` (enum ``PNG_FORMAT_RGBA8``)
- Other documented choice ids: auto, rgb8, gray8, graya8, rgb16, gray16, rgba16, graya16

Runtime still tries candidate prop/value lists in case of GI/build variance.
Alpha-critical ``set_property`` failures must be collected and surfaced — never bare pass.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gimp_mcp_snapshot as snap

# ---------------------------------------------------------------------------
# PDB / method constants (GIMP 3 only — no file-*-save)
# ---------------------------------------------------------------------------

PDB_EXPORT: dict[str, str] = {
    "png": "file-png-export",
    "jpeg": "file-jpeg-export",
    "jpg": "file-jpeg-export",
    "webp": "file-webp-export",
    "tiff": "file-tiff-export",
}

EXPORT_METHOD_MERGE = "merge_visible_layers_clip_to_image"
EXPORT_METHOD_FLATTEN = "flatten"
EXPORT_METHOD_DIRECT = "direct"

CODE_ALPHA_LOST = "ALPHA_LOST"
CODE_ALPHA_UNSUPPORTED_FORMAT = "ALPHA_UNSUPPORTED_FORMAT"
CODE_POLICY_CONFLICT = "POLICY_CONFLICT"
CODE_EXPORT_FAILED = "EXPORT_FAILED"
CODE_UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"

# Formats that can embed an alpha channel.
ALPHA_CAPABLE_FORMATS: frozenset[str] = frozenset({"png", "webp", "tiff"})
OPAQUE_ONLY_FORMATS: frozenset[str] = frozenset({"jpeg", "jpg"})
# Normalized formats with a mapped GIMP 3 file-*-export procedure.
SUPPORTED_EXPORT_FORMATS: frozenset[str] = frozenset({"png", "jpeg", "webp", "tiff"})
SUPPORTED_EXPORT_FORMATS_DISPLAY = "png/jpeg/webp/tiff"
# PNG IHDR color types (PNG spec)
PNG_COLOR_TYPE_GRAY = 0
PNG_COLOR_TYPE_RGB = 2
PNG_COLOR_TYPE_INDEXED = 3
PNG_COLOR_TYPE_GRAY_ALPHA = 4
PNG_COLOR_TYPE_RGBA = 6

PNG_ALPHA_COLOR_TYPES: frozenset[int] = frozenset({PNG_COLOR_TYPE_GRAY_ALPHA, PNG_COLOR_TYPE_RGBA})

# Phase-0 candidates — prefer documented GIMP 3 choice API first.
PNG_PIXEL_FORMAT_PROP_CANDIDATES: tuple[str, ...] = (
    "format",  # GIMP 3.2 file-png.c choice argument (confirmed)
    "pixel-format",
    "png-format",
)
PNG_RGBA8_VALUE_CANDIDATES: tuple[Any, ...] = (
    "rgba8",  # GIMP 3.2 choice id (confirmed)
    "RGBA8",
    "rgb-alpha",
    "RGBA",
    3,  # PNG_FORMAT_RGBA8 in enum if exposed as int
)

# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedExportPolicy:
    """Resolved export policy after applying format/flatten/preserve_alpha rules."""

    preserve_alpha: bool
    flatten: bool
    export_method: str
    verify: bool
    error: str | None
    code: str | None
    format: str  # normalized (jpg → jpeg)


def normalize_format(fmt: str) -> str:
    """Lowercase/strip format; map jpg → jpeg."""
    f = str(fmt).strip().lower()
    if f == "jpg":
        return "jpeg"
    return f


def is_supported_export_format(fmt: str) -> bool:
    """True when *fmt* maps to a GIMP 3 ``file-*-export`` procedure we support."""
    return normalize_format(fmt) in SUPPORTED_EXPORT_FORMATS


def can_preserve_alpha_for_format(fmt: str) -> bool:
    """True when the container format can store an alpha channel."""
    return normalize_format(fmt) in ALPHA_CAPABLE_FORMATS


def coerce_bool(value: Any, default: bool = False) -> bool:
    """Coerce TCP/JSON-ish values to bool without ``bool(\"false\") is True``.

    - ``None`` → *default*
    - ``bool`` → as-is
    - ``str`` (case-insensitive): ``true``/``1``/``yes`` → True;
      ``false``/``0``/``no``/``""`` → False; other strings → *default*
    - ``int`` / ``float`` (not bool): ``0`` → False, nonzero → True
    - **all other types** (list/dict/object) → *default* (fail-closed;
      never ``bool([0])`` / ``bool({})``)
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("true", "1", "yes"):
            return True
        if s in ("false", "0", "no", ""):
            return False
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    return default


def coerce_optional_bool(value: Any) -> bool | None:
    """Like :func:`coerce_bool` but ``None`` stays ``None`` (optional tri-state)."""
    if value is None:
        return None
    return coerce_bool(value, default=False)


def format_capability_matrix() -> dict[str, bool]:
    """Format → can_preserve_alpha for ``verify_alpha_channel`` responses."""
    return {
        "png": True,
        "jpeg": False,
        "webp": True,
        "tiff": True,
    }


def resolve_export_policy(
    format: str,
    preserve_alpha: bool | None,
    flatten: bool,
    verify: bool = True,
) -> ResolvedExportPolicy:
    """Resolve flatten / preserve_alpha / export_method from caller intent.

    Rules (spec §2.7 / decision matrix §9):

    | Inputs | Result |
    |---|---|
    | jpeg + preserve_alpha True | ALPHA_UNSUPPORTED_FORMAT |
    | flatten True + preserve_alpha True | POLICY_CONFLICT |
    | flatten True + preserve_alpha None | preserve_alpha=False, method=flatten |
    | png/webp/tiff + preserve_alpha None + flatten False | preserve_alpha=True, method=merge |
    | jpeg + preserve_alpha None | preserve_alpha=False; flatten keeps caller value (or flatten if True) |
    | preserve_alpha True (explicit) | never flatten; method=merge |
    """
    fmt = normalize_format(format)
    flatten_b = bool(flatten)
    verify_b = bool(verify)

    # Reject formats with no mapped file-*-export (no silent PNG fallback).
    if fmt not in SUPPORTED_EXPORT_FORMATS:
        return ResolvedExportPolicy(
            preserve_alpha=bool(preserve_alpha) if preserve_alpha is not None else False,
            flatten=flatten_b,
            export_method=EXPORT_METHOD_DIRECT,
            verify=verify_b,
            error=(
                f"Unsupported export format {fmt!r} (UNSUPPORTED_FORMAT). "
                f"Supported formats: {SUPPORTED_EXPORT_FORMATS_DISPLAY}."
            ),
            code=CODE_UNSUPPORTED_FORMAT,
            format=fmt,
        )

    # Explicit conflict: cannot flatten and preserve alpha.
    if flatten_b and preserve_alpha is True:
        return ResolvedExportPolicy(
            preserve_alpha=True,
            flatten=True,
            export_method=EXPORT_METHOD_MERGE,
            verify=verify_b,
            error=(
                "Cannot set both flatten=True and preserve_alpha=True: "
                "flatten strips the alpha channel. Use preserve_alpha=True "
                "(default for PNG/WEBP/TIFF) without flatten, or flatten=True "
                "for an intentional opaque bake."
            ),
            code=CODE_POLICY_CONFLICT,
            format=fmt,
        )

    # JPEG never supports alpha.
    if fmt == "jpeg" and preserve_alpha is True:
        return ResolvedExportPolicy(
            preserve_alpha=True,
            flatten=flatten_b,
            export_method=EXPORT_METHOD_FLATTEN if flatten_b else EXPORT_METHOD_DIRECT,
            verify=verify_b,
            error=(
                "JPEG cannot preserve alpha (ALPHA_UNSUPPORTED_FORMAT). "
                "Use PNG, WEBP, or TIFF for transparent exports."
            ),
            code=CODE_ALPHA_UNSUPPORTED_FORMAT,
            format=fmt,
        )

    # flatten=True with auto preserve → opaque bake (safe for internal callers).
    if flatten_b and preserve_alpha is None:
        return ResolvedExportPolicy(
            preserve_alpha=False,
            flatten=True,
            export_method=EXPORT_METHOD_FLATTEN,
            verify=False,  # alpha check N/A for intentional opaque
            error=None,
            code=None,
            format=fmt,
        )

    # Explicit preserve_alpha True (non-jpeg already handled).
    if preserve_alpha is True:
        return ResolvedExportPolicy(
            preserve_alpha=True,
            flatten=False,
            export_method=EXPORT_METHOD_MERGE,
            verify=verify_b,
            error=None,
            code=None,
            format=fmt,
        )

    # Explicit preserve_alpha False.
    if preserve_alpha is False:
        method = EXPORT_METHOD_FLATTEN if flatten_b else EXPORT_METHOD_DIRECT
        return ResolvedExportPolicy(
            preserve_alpha=False,
            flatten=flatten_b,
            export_method=method,
            verify=False,
            error=None,
            code=None,
            format=fmt,
        )

    # preserve_alpha is None, flatten is False — auto by format.
    if fmt in ALPHA_CAPABLE_FORMATS:
        return ResolvedExportPolicy(
            preserve_alpha=True,
            flatten=False,
            export_method=EXPORT_METHOD_MERGE,
            verify=verify_b,
            error=None,
            code=None,
            format=fmt,
        )

    # JPEG (opaque-only supported format): no alpha.
    # Prefer flatten for multi-layer jpeg when caller left defaults.
    return ResolvedExportPolicy(
        preserve_alpha=False,
        flatten=flatten_b,
        export_method=EXPORT_METHOD_FLATTEN if flatten_b else EXPORT_METHOD_DIRECT,
        verify=False,
        error=None,
        code=None,
        format=fmt,
    )


# ---------------------------------------------------------------------------
# PNG IHDR parsing (stdlib only)
# ---------------------------------------------------------------------------


def _read_png_bytes(path_or_bytes: str | Path | bytes) -> bytes:
    if isinstance(path_or_bytes, (bytes, bytearray)):
        return bytes(path_or_bytes)
    p = Path(path_or_bytes)
    return p.read_bytes()


def png_ihdr_info(path_or_bytes: str | Path | bytes) -> dict[str, Any]:
    """Parse PNG IHDR: width, height, bit_depth, color_type.

    Validates PNG signature via ``gimp_mcp_snapshot`` helpers. Raises
    ``ValueError`` if the file is not a well-formed PNG with an IHDR chunk.
    """
    data = _read_png_bytes(path_or_bytes)
    if not snap.validate_png_bytes(data):
        raise ValueError("Not a valid PNG (missing/invalid signature or empty)")

    # After 8-byte signature: chunk length (4) + type (4) + data + CRC (4)
    offset = 8
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        if data_end > len(data):
            raise ValueError("Truncated PNG chunk")
        if chunk_type == b"IHDR":
            if length != 13:
                raise ValueError(f"IHDR length must be 13, got {length}")
            width, height, bit_depth, color_type, _comp, _filt, _inter = struct.unpack(
                ">IIBBBBB", data[data_start:data_end]
            )
            return {
                "width": int(width),
                "height": int(height),
                "bit_depth": int(bit_depth),
                "color_type": int(color_type),
            }
        # Skip chunk data + CRC
        offset = data_end + 4

    raise ValueError("PNG missing IHDR chunk")


def file_has_alpha_channel(path_or_bytes: str | Path | bytes) -> bool:
    """True iff PNG color_type is 4 (gray+alpha) or 6 (RGBA).

    Does not scan tRNS for indexed (type 3) — export path forces RGBA8 so
    agents get type 6 when alpha is present.
    """
    info = png_ihdr_info(path_or_bytes)
    return int(info["color_type"]) in PNG_ALPHA_COLOR_TYPES


def alpha_verified_value(
    *,
    preserve_alpha: bool,
    preflight_has_alpha: bool,
    verify: bool,
    path_or_bytes: str | Path | bytes | None = None,
    format: str = "png",
) -> bool | str:
    """Compute ``alpha_verified`` field: true | false | ``\"not_applicable\"``."""
    if not preserve_alpha:
        return "not_applicable"
    if not preflight_has_alpha:
        return "not_applicable"
    if not verify:
        return "not_applicable"
    fmt = normalize_format(format)
    if fmt != "png":
        # Non-PNG alpha verify is best-effort deferred; treat as not_applicable
        # unless we have a path and can check (WEBP/TIFF not IHDR-parseable here).
        return "not_applicable"
    if path_or_bytes is None:
        return False
    try:
        return bool(file_has_alpha_channel(path_or_bytes))
    except (ValueError, OSError):
        return False


# ---------------------------------------------------------------------------
# Result builders
# ---------------------------------------------------------------------------


def build_export_success(
    *,
    file_path: str,
    format: str,
    file_size_bytes: int,
    preserve_alpha: bool,
    preflight_has_alpha: bool,
    alpha_verified: bool | str,
    export_method: str,
    pdb_procedure: str | None,
    png_color_type: int | None = None,
    property_errors: list[str] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a successful export response (status success + rich metadata)."""
    out: dict[str, Any] = {
        "status": "success",
        "file_path": file_path,
        "format": normalize_format(format),
        "file_size_bytes": int(file_size_bytes),
        "preserve_alpha": bool(preserve_alpha),
        "preflight_has_alpha": bool(preflight_has_alpha),
        "alpha_verified": alpha_verified,
        "export_method": export_method,
        "pdb_procedure": pdb_procedure,
    }
    if png_color_type is not None:
        out["png_color_type"] = int(png_color_type)
    if property_errors:
        out["property_errors"] = list(property_errors)
    if extra:
        out.update(dict(extra))
    return out


def build_export_error(
    *,
    code: str,
    error: str,
    file_path: str | None = None,
    left_on_disk: bool | None = None,
    png_color_type: int | None = None,
    preflight_has_alpha: bool | None = None,
    preserve_alpha: bool | None = None,
    property_errors: list[str] | None = None,
    export_method: str | None = None,
    pdb_procedure: str | None = None,
    format: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a structured export error (ALPHA_LOST, POLICY_CONFLICT, …)."""
    out: dict[str, Any] = {
        "status": "error",
        "code": code,
        "error": error,
    }
    if file_path is not None:
        out["file_path"] = file_path
    if left_on_disk is not None:
        out["left_on_disk"] = bool(left_on_disk)
    if png_color_type is not None:
        out["png_color_type"] = int(png_color_type)
    if preflight_has_alpha is not None:
        out["preflight_has_alpha"] = bool(preflight_has_alpha)
    if preserve_alpha is not None:
        out["preserve_alpha"] = bool(preserve_alpha)
    if property_errors is not None:
        out["property_errors"] = list(property_errors)
    if export_method is not None:
        out["export_method"] = export_method
    if pdb_procedure is not None:
        out["pdb_procedure"] = pdb_procedure
    if format is not None:
        out["format"] = normalize_format(format)
    if extra:
        out.update(dict(extra))
    return out


def pdb_procedure_for_format(fmt: str) -> str | None:
    """Return GIMP 3 ``file-*-export`` procedure name for *fmt*, or None."""
    return PDB_EXPORT.get(normalize_format(fmt))
