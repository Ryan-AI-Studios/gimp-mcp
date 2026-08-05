"""High-level MCP surface helpers (host-only, track 0010).

Mode detection, HL catalog, create_selection validation, soft version compare.
Not a GIMP plug-in module — packaged with the stdio MCP server (py-modules).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any, Literal

# Align truthy set with gimp_mcp_security._env_truthy (1/true/yes/on).
_TRUTHY = frozenset({"1", "true", "yes", "on"})

HL_TAG = "hl"
ADVANCED_TAG = "advanced"
ENV_ADVANCED_TOOLS = "GIMP_MCP_ADVANCED_TOOLS"

# Locked default catalog — exactly 30 names (0010 + 0014 + 0015 + 0016 + 0017 TX + 0030 cutout).
HL_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "session_probe",
        "restart_server",
        "orient_workspace",
        "select_image",
        "select_layers",
        "open_image",
        "close_image",
        "new_canvas",
        "ensure_source_immutable",
        "checkpoint_create",
        "checkpoint_restore",
        "render_visible_composite",
        "normalize_image_orientation",
        "map_preview_to_image",
        "save_xcf",
        "export_image",
        "verify_alpha_channel",
        "create_selection",
        "get_selection_bounds",
        "clear_selection_to_transparent",
        "compare_images",
        "verify_artifact",
        "list_recipes",
        "apply_recipe",
        "apply_nde_filter",
        "edit_filter_config",
        "remove_nde_filter",
        "undo_group_begin",
        "undo_group_end",
        "undo_group_rollback",
    }
)

_SELECTION_TYPES = frozenset({"rectangle", "ellipse", "by_color", "contiguous", "all", "none"})
_SELECTION_OPS = frozenset({"replace", "add", "subtract", "intersect"})
_DOTTED_INT = re.compile(r"^\d+(\.\d+)*$")


def advanced_tools_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """True when ``GIMP_MCP_ADVANCED_TOOLS`` is truthy (1/true/yes/on)."""
    env = environ if environ is not None else os.environ
    raw = env.get(ENV_ADVANCED_TOOLS, "")
    if raw is None:
        return False
    return str(raw).strip().lower() in _TRUTHY


def surface_mode(
    environ: Mapping[str, str] | None = None,
    *,
    advanced_mode: bool | None = None,
) -> Literal["high-level", "advanced"]:
    """Resolve tool surface mode.

    ``advanced_mode`` overrides env when not None (factory/tests).
    """
    if advanced_mode is not None:
        return "advanced" if advanced_mode else "high-level"
    return "advanced" if advanced_tools_enabled(environ) else "high-level"


def include_tags_for_mode(mode: str) -> set[str] | None:
    """FastMCP ``include_tags``: ``{\"hl\"}`` for high-level, ``None`` for advanced."""
    if mode == "advanced":
        return None
    if mode == "high-level":
        return {HL_TAG}
    raise ValueError(f"unknown surface mode: {mode!r}")


def get_hl_catalog_names() -> list[str]:
    """Sorted list of the 30 high-level tool names."""
    return sorted(HL_TOOL_NAMES)


def is_hl_tool(name: str) -> bool:
    return name in HL_TOOL_NAMES


def soft_version_ok(live: str | None, minimum: str | None) -> bool | None:
    """Compare dotted-int version strings as int tuples.

    Returns True/False when both parse as ``^\\d+(\\.\\d+)*$``; None if either
    is missing or unparseable (no hard-fail).
    """
    if live is None or minimum is None:
        return None
    live_s = str(live).strip()
    min_s = str(minimum).strip()
    if not live_s or not min_s:
        return None
    if not _DOTTED_INT.match(live_s) or not _DOTTED_INT.match(min_s):
        return None
    live_t = tuple(int(p) for p in live_s.split("."))
    min_t = tuple(int(p) for p in min_s.split("."))
    # Pad shorter with zeros
    n = max(len(live_t), len(min_t))
    live_p = live_t + (0,) * (n - len(live_t))
    min_p = min_t + (0,) * (n - len(min_t))
    return live_p >= min_p


def validate_create_selection_params(params: dict[str, Any]) -> dict[str, Any]:
    """Strict validate + normalize create_selection inputs (host-side).

    Raises ValueError with a clear message before any TCP call.
    Returns a normalized dict ready for plugin command dispatch.
    """
    if not isinstance(params, dict):
        raise ValueError("create_selection params must be a dict")

    raw_type = params.get("type")
    if raw_type is None or (isinstance(raw_type, str) and not raw_type.strip()):
        raise ValueError("create_selection requires type")
    if not isinstance(raw_type, str):
        raise ValueError("create_selection type must be a string")
    sel_type = raw_type.strip().lower()
    if sel_type not in _SELECTION_TYPES:
        raise ValueError(
            f"create_selection unknown type {raw_type!r}; "
            f"expected one of {sorted(_SELECTION_TYPES)}"
        )

    op_raw = params.get("operation", "replace")
    if op_raw is None:
        op_raw = "replace"
    if not isinstance(op_raw, str):
        raise ValueError("create_selection operation must be a string")
    operation = op_raw.strip().lower()
    if operation not in _SELECTION_OPS:
        raise ValueError(
            f"create_selection unknown operation {op_raw!r}; "
            f"expected one of {sorted(_SELECTION_OPS)}"
        )

    feather = params.get("feather", 0)
    if feather is None:
        feather = 0
    if isinstance(feather, bool) or not isinstance(feather, (int, float)):
        raise ValueError("create_selection feather must be a number")
    feather_f = float(feather)
    if feather_f < 0:
        raise ValueError("create_selection feather must be >= 0")

    out: dict[str, Any] = {
        "type": sel_type,
        "operation": operation,
        "feather": feather_f,
    }

    # Image targeting: pass through handle / image_index for the server to resolve.
    if "handle" in params and params["handle"] is not None:
        out["handle"] = params["handle"]
    if "image_index" in params and params["image_index"] is not None:
        out["image_index"] = params["image_index"]
    if "layer_handle" in params and params["layer_handle"] is not None:
        out["layer_handle"] = params["layer_handle"]

    if sel_type in ("rectangle", "ellipse"):
        for key in ("x", "y", "width", "height"):
            if key not in params or params[key] is None:
                raise ValueError(f"create_selection type={sel_type} requires {key}")
            val = params[key]
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise ValueError(f"create_selection {key} must be a number")
            out[key] = int(val) if key in ("x", "y", "width", "height") else val
        if int(out["width"]) <= 0 or int(out["height"]) <= 0:
            raise ValueError("create_selection width and height must be > 0")

    elif sel_type == "by_color":
        color = params.get("color")
        if color is None or (isinstance(color, str) and not str(color).strip()):
            raise ValueError("create_selection type=by_color requires color")
        if not isinstance(color, str):
            raise ValueError("create_selection color must be a string")
        out["color"] = color.strip()
        threshold = params.get("threshold", 15)
        if threshold is None:
            threshold = 15
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise ValueError("create_selection threshold must be a number")
        out["threshold"] = int(threshold)
        # feather not applicable — omit from plugin payload for by_color

    elif sel_type == "contiguous":
        for key in ("x", "y"):
            if key not in params or params[key] is None:
                raise ValueError(f"create_selection type=contiguous requires {key}")
            val = params[key]
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise ValueError(f"create_selection {key} must be a number")
            out[key] = int(val)
        threshold = params.get("threshold", 15)
        if threshold is None:
            threshold = 15
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise ValueError("create_selection threshold must be a number")
        out["threshold"] = int(threshold)
        # feather not applicable — omit from normalized payload for contiguous
        out.pop("feather", None)

    # all / none: no geometry

    return out
