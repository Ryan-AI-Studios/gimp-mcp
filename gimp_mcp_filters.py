"""Pure stdlib NDE DrawableFilter helpers (track 0016).

Shipped next to ``gimp-mcp-plugin.py`` as the 9th plug-in install file
and imported by the host MCP server for allowlist pre-validation.

No third-party imports; no GIMP/gi dependency.

Provides:
- curated GEGL/GIMP op allowlist with soft known_props hints
- soft config key validation (never rejects unknown keys)
- blend-mode string allowlist (host fast-fail)
- filter summary normalization for orient / list tools
- pure GObject-type coercion hints for plugin pspec mapping
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

# Product codes as strings so this module stays free of security import.
CODE_UNSUPPORTED = "UNSUPPORTED"
CODE_INTERNAL = "INTERNAL"

# ---------------------------------------------------------------------------
# Allowlist (v1) — curated ops only
# ---------------------------------------------------------------------------

# Per-op metadata. All v1 ops use soft prop policy (never reject unknown keys).
# ``requires_runtime_probe``: plugin must call operation_get_available() before new.
# ``expand_class``: may expand outside layer bounds (crop-node clip residual).
# ``known_props``: soft hints only for friendlier messages — not a hard schema.
_OP_META: dict[str, dict[str, Any]] = {
    "gegl:gaussian-blur": {
        "known_props": frozenset(
            {"std-dev-x", "std-dev-y", "filter", "abyss-policy", "clip-extent"}
        ),
        "requires_runtime_probe": False,
        "expand_class": False,
    },
    "gegl:unsharp-mask": {
        "known_props": frozenset({"std-dev", "scale", "threshold"}),
        "requires_runtime_probe": False,
        "expand_class": False,
    },
    "gegl:noise-reduction": {
        "known_props": frozenset({"iterations", "strength"}),
        "requires_runtime_probe": False,
        "expand_class": False,
    },
    "gegl:pixelize": {
        "known_props": frozenset({"size-x", "size-y", "norm"}),
        "requires_runtime_probe": False,
        "expand_class": False,
    },
    "gegl:emboss": {
        "known_props": frozenset({"azimuth", "elevation", "depth", "type", "emboss"}),
        "requires_runtime_probe": False,
        "expand_class": False,
    },
    "gegl:vignette": {
        "known_props": frozenset(
            {"radius", "softness", "shape", "gamma", "proportion", "squeeze", "x", "y", "rotation"}
        ),
        "requires_runtime_probe": False,
        "expand_class": True,  # soft residual note; crop-node may clip expand effects
    },
    "gegl:brightness-contrast": {
        "known_props": frozenset({"brightness", "contrast"}),
        "requires_runtime_probe": False,
        "expand_class": False,
    },
    "gegl:hue-chroma": {
        "known_props": frozenset({"hue", "chroma", "lightness"}),
        "requires_runtime_probe": False,
        "expand_class": False,
    },
    "gegl:color-balance": {
        "known_props": frozenset(
            {
                "cyan-red",
                "magenta-green",
                "yellow-blue",
                "range",
                "preserve-luminosity",
            }
        ),
        "requires_runtime_probe": False,
        "expand_class": False,
    },
    "gegl:exposure": {
        "known_props": frozenset({"exposure", "offset", "gamma", "black-level"}),
        "requires_runtime_probe": False,
        "expand_class": False,
    },
    "gegl:shadows-highlights": {
        "known_props": frozenset(
            {
                "shadows",
                "highlights",
                "whitepoint",
                "radius",
                "compress",
                "shadows-ccorrect",
                "highlights-ccorrect",
            }
        ),
        "requires_runtime_probe": False,
        "expand_class": False,
    },
    "gimp:levels": {
        "known_props": frozenset(),  # soft; props as GI exposes
        "requires_runtime_probe": True,
        "expand_class": False,
    },
    "gimp:curves": {
        "known_props": frozenset(),
        "requires_runtime_probe": True,
        "expand_class": False,
    },
}

ALLOWED_OPS: frozenset[str] = frozenset(_OP_META.keys())

# Blend mode strings accepted by host pre-validation.
# Plugin maps via _blend_mode_from_string (includes REPLACE).
ALLOWED_BLEND_MODES: frozenset[str] = frozenset(
    {
        "REPLACE",
        "NORMAL",
        "MULTIPLY",
        "SCREEN",
        "OVERLAY",
        "DARKEN",
        "LIGHTEN",
        "DODGE",
        "BURN",
        "HARD_LIGHT",
        "SOFT_LIGHT",
        "DIFFERENCE",
        "HUE",
        "SATURATION",
        "COLOR",
        "LUMINOSITY",
        "DISSOLVE",
    }
)

DEFAULT_BLEND_MODE = "REPLACE"
DEFAULT_OPACITY = 1.0

# Summary fields for orient / list
_SUMMARY_CORE_KEYS = (
    "filter_id",
    "name",
    "operation_name",
    "visible",
    "opacity",
    "blend_mode",
)


# ---------------------------------------------------------------------------
# Op metadata accessors
# ---------------------------------------------------------------------------


def op_meta(operation: str) -> dict[str, Any] | None:
    """Return per-op metadata dict, or None if not allowlisted."""
    if not isinstance(operation, str):
        return None
    return _OP_META.get(operation.strip())


def requires_runtime_probe(operation: str) -> bool:
    """True when plugin must probe ``operation_get_available`` before apply."""
    meta = op_meta(operation)
    if meta is None:
        return False
    return bool(meta.get("requires_runtime_probe", False))


def check_runtime_probe(
    operation: str,
    available: Sequence[str] | None,
) -> dict[str, Any]:
    """Decide whether a runtime-probed op may proceed (pure; no GIMP).

    Used by the plugin when ``requires_runtime_probe`` is true (gimp:* ops).

    *available*:
      - ``None``: probe API missing / failed → allow try (``ok`` True, ``probed`` False)
      - sequence of op name strings: *operation* must be present or UNSUPPORTED

    Ops that do not require a runtime probe always return ``ok`` True.
    """
    if not isinstance(operation, str) or not operation.strip():
        return {
            "ok": False,
            "code": CODE_UNSUPPORTED,
            "message": "operation must be a non-empty string",
            "details": {"reason": "operation_get_available"},
        }
    op = operation.strip()
    if not requires_runtime_probe(op):
        return {"ok": True, "probed": False, "operation": op}
    if available is None:
        return {
            "ok": True,
            "probed": False,
            "operation": op,
            "reason": "probe_api_unavailable",
        }
    names = {str(x) for x in available}
    if op not in names:
        return {
            "ok": False,
            "code": CODE_UNSUPPORTED,
            "message": f"operation {op!r} is not available in this GIMP build",
            "details": {"operation": op, "reason": "operation_get_available"},
        }
    return {"ok": True, "probed": True, "operation": op}


# Markers that indicate DrawableFilter.new failed because the op is unavailable
# (map to UNSUPPORTED). Other failures stay INTERNAL for the caller.
_NEW_UNAVAILABLE_MARKERS: tuple[str, ...] = (
    "unavailable",
    "unknown operation",
    "unknown op",
    "not available",
    "no such operation",
    "invalid operation",
    "unsupported operation",
    "returned none",
    "could not create filter",
    "failed to create filter",
    "operation does not exist",
)


def classify_drawable_filter_new_failure(
    operation: str,
    *,
    filtr_is_none: bool = False,
    exception_message: str | None = None,
) -> dict[str, Any]:
    """Map ``DrawableFilter.new`` None/raise to a product error decision (pure).

    Returns ``{"ok": False, "code": ..., "message": ..., "details": ...}``.

    - ``filtr_is_none`` or clearly unavailable/unknown-op messages → ``UNSUPPORTED``
    - other exception messages → ``INTERNAL`` (plugin may set ``state_may_have_changed``
      after a later mutation; new() itself is pre-append)
    """
    op = operation.strip() if isinstance(operation, str) else str(operation or "")
    base_details: dict[str, Any] = {"operation": op, "reason": "drawable_filter_new"}

    if filtr_is_none:
        return {
            "ok": False,
            "code": CODE_UNSUPPORTED,
            "message": (
                f"DrawableFilter.new returned None for {op!r} "
                "(operation unavailable or unsupported in this GIMP build)"
            ),
            "details": {**base_details, "reason": "drawable_filter_new_none"},
        }

    msg = (exception_message or "").strip()
    lower = msg.lower()
    if any(marker in lower for marker in _NEW_UNAVAILABLE_MARKERS):
        return {
            "ok": False,
            "code": CODE_UNSUPPORTED,
            "message": msg
            or (f"DrawableFilter.new failed for {op!r}: operation unavailable or unsupported"),
            "details": {**base_details, "reason": "drawable_filter_new_unavailable"},
        }

    return {
        "ok": False,
        "code": CODE_INTERNAL,
        "message": msg or f"DrawableFilter.new failed for {op!r}",
        "details": base_details,
    }


def is_expand_class_op(operation: str) -> bool:
    """True when op may expand outside layer bounds (notes residual)."""
    meta = op_meta(operation)
    if meta is None:
        return False
    return bool(meta.get("expand_class", False))


def known_props_for(operation: str) -> frozenset[str]:
    """Soft known property name hints for *operation* (may be empty)."""
    meta = op_meta(operation)
    if meta is None:
        return frozenset()
    props = meta.get("known_props") or frozenset()
    return frozenset(props) if not isinstance(props, frozenset) else props


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_operation(operation: str) -> dict[str, Any]:
    """Validate *operation* against the v1 allowlist.

    Returns ``{"ok": True, "operation": <normalized>}`` or
    ``{"ok": False, "code": "UNSUPPORTED", "message": ...}``.
    """
    if not isinstance(operation, str) or not operation.strip():
        return {
            "ok": False,
            "code": CODE_UNSUPPORTED,
            "message": "operation must be a non-empty string",
        }
    op = operation.strip()
    if op not in ALLOWED_OPS:
        return {
            "ok": False,
            "code": CODE_UNSUPPORTED,
            "message": (
                f"operation {op!r} is not in the v1 NDE allowlist "
                f"({len(ALLOWED_OPS)} ops); use an allowlisted GEGL/GIMP op"
            ),
            "details": {"operation": op, "allowlist_size": len(ALLOWED_OPS)},
        }
    return {"ok": True, "operation": op}


def validate_blend_mode(blend_mode: str | None) -> dict[str, Any]:
    """Validate optional blend_mode string (None → default REPLACE).

    Returns ``{"ok": True, "blend_mode": <upper>}`` or error dict.
    """
    if blend_mode is None or (isinstance(blend_mode, str) and not blend_mode.strip()):
        return {"ok": True, "blend_mode": DEFAULT_BLEND_MODE}
    if not isinstance(blend_mode, str):
        return {
            "ok": False,
            "code": CODE_UNSUPPORTED,
            "message": "blend_mode must be a string",
        }
    mode = blend_mode.strip().upper()
    if mode not in ALLOWED_BLEND_MODES:
        return {
            "ok": False,
            "code": CODE_UNSUPPORTED,
            "message": (
                f"blend_mode {blend_mode!r} is not allowed; "
                f"expected one of {sorted(ALLOWED_BLEND_MODES)}"
            ),
        }
    return {"ok": True, "blend_mode": mode}


def validate_config_keys(
    config: dict[str, Any] | None,
    operation: str,
) -> dict[str, Any]:
    """Soft-validate config keys for *operation*.

    **Never** rejects unknown keys (v1 soft-only policy). May return
    ``unknown_keys`` and ``known_props`` hints for friendlier messages.
    """
    op_result = validate_operation(operation)
    if not op_result.get("ok"):
        return op_result
    op = str(op_result["operation"])
    if config is None:
        return {
            "ok": True,
            "operation": op,
            "unknown_keys": [],
            "known_props": sorted(known_props_for(op)),
        }
    if not isinstance(config, dict):
        return {
            "ok": False,
            "code": CODE_UNSUPPORTED,
            "message": "config must be an object/dict when provided",
        }
    known = known_props_for(op)
    unknown: list[str] = []
    if known:
        for key in config:
            if str(key) not in known:
                unknown.append(str(key))
    hints = [
        f"key {k!r} is not in soft known_props for {op} (still forwarded to plugin)"
        for k in unknown
    ]
    return {
        "ok": True,
        "operation": op,
        "unknown_keys": unknown,
        "known_props": sorted(known),
        "hints": hints,
    }


# ---------------------------------------------------------------------------
# Pure type coercion (plugin maps pspec → type_name, then calls this)
# ---------------------------------------------------------------------------


def coerce_config_value(value: Any, type_name: str | None) -> Any:
    """Coerce a JSON-ish *value* toward a GObject property type name.

    *type_name* is a loose label from plugin pspec mapping, e.g.
    ``DOUBLE``, ``FLOAT``, ``INT``, ``BOOLEAN``, ``STRING``.
    Unknown / empty type_name → value returned as-is (plugin may still try set).
    """
    if type_name is None:
        return value
    t = str(type_name).strip().upper()
    # Strip common GObject / GLib prefixes
    for prefix in ("G_TYPE_", "TYPE_", "GOBJECT.", "GLIB.", "G_"):
        if t.startswith(prefix):
            t = t[len(prefix) :]
            break
    if t in ("DOUBLE", "FLOAT", "GDOUBLE", "GFLOAT"):
        if isinstance(value, bool):
            return float(value)
        return float(value)
    if t in ("INT", "LONG", "UINT", "ULONG", "INT64", "UINT64", "GINT", "GUINT", "GLONG"):
        if isinstance(value, bool):
            return int(value)
        return int(value)
    if t in ("BOOLEAN", "BOOL", "GBOOLEAN"):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("1", "true", "yes", "on"):
                return True
            if low in ("0", "false", "no", "off"):
                return False
        return bool(value)
    if t in ("STRING", "STR", "GSTRING", "ENUM"):
        if value is None:
            return value
        return str(value)
    # else: pass through (enums, objects, arrays — plugin handles)
    return value


def set_config_props(
    config: dict[str, Any] | None,
    *,
    set_property: Callable[[str, Any], None],
    find_property: Callable[[str], Any] | None = None,
    pspec_type_name: Callable[[Any], str | None] | None = None,
) -> tuple[list[str], list[dict[str, str]]]:
    """Apply config keys via callbacks; never silent-pass.

    Pure helper used by the plugin (and offline tests with FakeCfg).

    Returns ``(applied_props, ignored_props)`` where ignored entries are
    ``{"key": str, "error": str}``.
    """
    applied: list[str] = []
    ignored: list[dict[str, str]] = []
    if not config:
        return applied, ignored
    if not isinstance(config, dict):
        ignored.append({"key": "*", "error": "config must be an object"})
        return applied, ignored
    for key, raw_val in config.items():
        k = str(key)
        try:
            pspec = None
            if find_property is not None:
                try:
                    pspec = find_property(k)
                except Exception:
                    pspec = None
            type_name: str | None = None
            if pspec_type_name is not None and pspec is not None:
                try:
                    type_name = pspec_type_name(pspec)
                except Exception:
                    type_name = None
            try:
                coerced = coerce_config_value(raw_val, type_name)
            except (TypeError, ValueError) as ce:
                ignored.append({"key": k, "error": f"coerce failed: {ce}"})
                continue
            set_property(k, coerced)
            applied.append(k)
        except Exception as e:
            ignored.append({"key": k, "error": str(e)})
    return applied, ignored


# ---------------------------------------------------------------------------
# Summary normalization
# ---------------------------------------------------------------------------


def normalize_filter_summary(
    raw: dict[str, Any] | None,
    *,
    include_config: bool = True,
) -> dict[str, Any]:
    """Normalize a raw filter dict into the product summary shape.

    Missing fields get safe defaults. ``config`` is included only when
    *include_config* is True and present on *raw*.
    """
    src = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}

    fid = src.get("filter_id", src.get("id"))
    try:
        out["filter_id"] = int(fid) if fid is not None else None
    except (TypeError, ValueError):
        out["filter_id"] = None

    name = src.get("name")
    out["name"] = str(name) if name is not None else ""

    op = src.get("operation_name", src.get("operation", src.get("op")))
    out["operation_name"] = str(op) if op is not None else ""

    vis = src.get("visible", True)
    out["visible"] = bool(vis) if not isinstance(vis, bool) else vis

    opacity = src.get("opacity", DEFAULT_OPACITY)
    try:
        out["opacity"] = float(opacity)
    except (TypeError, ValueError):
        out["opacity"] = DEFAULT_OPACITY

    blend = src.get("blend_mode", DEFAULT_BLEND_MODE)
    out["blend_mode"] = str(blend).upper() if blend is not None else DEFAULT_BLEND_MODE

    if include_config and "config" in src and src["config"] is not None:
        cfg = src["config"]
        if isinstance(cfg, dict):
            # Drop oversized / non-JSON-friendly blobs by stringifying unknowns
            clean: dict[str, Any] = {}
            for k, v in cfg.items():
                key = str(k)
                if isinstance(v, (bool, int, float, str)) or v is None:
                    clean[key] = v
                else:
                    try:
                        clean[key] = str(v)
                    except Exception:
                        continue
            out["config"] = clean
        else:
            out["config"] = {}

    return out


def expand_class_note(operation: str) -> str | None:
    """Optional response note when *operation* is expand-class."""
    if is_expand_class_op(operation):
        return (
            "Operation may expand outside current layer bounds; "
            "internal crop nodes can clip the effect. "
            "Consider enlarging the layer or layer-to-image-size if clipped."
        )
    return None
