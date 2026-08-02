"""Stdlib-only state-manifest helpers for GIMP MCP orientation (track 0006).

Host-side pure module (basedpyright-checked). Plugin emits a raw dump; the MCP
server calls :func:`finalize_manifest` to inject session transport/capabilities
and run structural validation.

Deploy optional next to the plug-in only if the plug-in imports this module;
preferred architecture is plugin self-contained raw dump + host finalize.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = "1.0.0"
MAX_LAYER_DEPTH = 32

LAYER_KINDS: frozenset[str] = frozenset({"raster", "group", "text", "link", "vector"})
BASE_TYPES: frozenset[str] = frozenset({"RGB", "GRAY", "INDEXED"})
TRANSPORTS: frozenset[str] = frozenset({"local-socket", "stdio-proxy", "batch"})

# GObject type names → kind (stable; never RGB/RGBA from _get_layer_type_string).
_KIND_BY_GTYPE: dict[str, str] = {
    "GimpGroupLayer": "group",
    "GimpTextLayer": "text",
    "GimpLinkLayer": "link",
    "GimpVectorLayer": "vector",
}

_CAPABILITY_REQUIRED: tuple[str, ...] = (
    "visible_composite_snapshot",
    "isolated_layer_snapshot",
    "alpha_snapshot",
    "atomic_xcf_save",
    "atomic_export",
)


def utc_now_iso() -> str:
    """Return current UTC time as ISO-8601 with ``Z`` suffix."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def provisional_image_handle(
    image_id: int,
    *,
    session_epoch: int,
    generation: int = 1,
    fingerprint: str | None = None,
) -> dict[str, Any]:
    """Thin alias of :func:`gimp_mcp_handles.image_handle`.

    Keeps ``generation`` default ``1`` for backward compatibility on provisional_*
    only. Prefer :func:`gimp_mcp_handles.image_handle` (generation required) for
    new code paths.
    """
    import gimp_mcp_handles as _handles

    return _handles.image_handle(
        image_id,
        session_epoch=session_epoch,
        generation=generation,
        fingerprint=fingerprint,
    )


def provisional_item_handle(
    item_id: int,
    *,
    image_id: int,
    session_epoch: int,
    generation: int = 1,
    fingerprint: str | None = None,
) -> dict[str, Any]:
    """Thin alias of :func:`gimp_mcp_handles.item_handle` (generation default 1)."""
    import gimp_mcp_handles as _handles

    return _handles.item_handle(
        item_id,
        image_id=image_id,
        session_epoch=session_epoch,
        generation=generation,
        fingerprint=fingerprint,
    )


def normalize_base_type(value: Any) -> str:
    """Normalize GIMP base-type strings to ``RGB`` / ``GRAY`` / ``INDEXED``.

    Maps common variants (``Grayscale``, ``Gray``, ``Indexed``, ``RGBA``) to the
    schema enum. Unknown non-empty values fall through uppercased when they
    already match; otherwise return ``RGB`` as a safe default for validation.
    """
    if value is None:
        return "RGB"
    s = str(value).strip()
    if not s:
        return "RGB"
    # Strip enum-style prefixes e.g. "ImageBaseType.RGB"
    if "." in s:
        s = s.rsplit(".", 1)[-1]
    key = s.upper().replace(" ", "").replace("-", "").replace("_", "")
    if key in ("GRAYSCALE", "GRAY", "GREY", "GREYSCALE"):
        return "GRAY"
    if key in ("INDEXED", "INDEX", "PALETTE"):
        return "INDEXED"
    if key in ("RGB", "RGBA", "RGBALPHA"):
        return "RGB"
    if s.upper() in BASE_TYPES:
        return s.upper()
    return "RGB"


def normalize_opacity(value: Any) -> float:
    """Coerce opacity to float and clamp to ``[0, 100]`` (GIMP 3.x percent scale)."""
    try:
        if value is None:
            return 100.0
        v = float(value)
    except (TypeError, ValueError):
        return 100.0
    if v < 0.0:
        return 0.0
    if v > 100.0:
        return 100.0
    return v


def parse_layer_offsets(offsets: Any) -> tuple[int, int]:
    """Parse GIMP ``layer.get_offsets()`` return values to ``(x, y)``.

    GIMP 3.x returns an object with ``offset_x`` / ``offset_y``. Alternate
    bindings may return a 2-tuple or a one-element sequence wrapping the
    object. Returns ``(0, 0)`` on any parse failure.
    """
    ox, oy = 0, 0
    try:
        if offsets is not None:
            if hasattr(offsets, "offset_x") or hasattr(offsets, "offset_y"):
                ox = int(getattr(offsets, "offset_x", 0) or 0)
                oy = int(getattr(offsets, "offset_y", 0) or 0)
            elif isinstance(offsets, (list, tuple)) and len(offsets) >= 2:
                ox, oy = int(offsets[0]), int(offsets[1])
            elif (
                isinstance(offsets, (list, tuple))
                and len(offsets) == 1
                and hasattr(offsets[0], "offset_x")
            ):
                ox = int(offsets[0].offset_x)
                oy = int(offsets[0].offset_y)
    except (TypeError, ValueError, AttributeError, RuntimeError):
        ox, oy = 0, 0
    return ox, oy


def classify_layer_kind(type_name: str | None) -> str:
    """Map a stable GObject type name to a layer kind enum string.

    Recognizes ``GimpGroupLayer``, ``GimpTextLayer``, ``GimpLinkLayer``,
    ``GimpVectorLayer`` (and short forms without the ``Gimp`` prefix).
    Never treats RGB/RGBA pixel-type strings as special kinds — those are
    ``raster``.
    """
    if type_name is None:
        return "raster"
    raw = str(type_name).strip()
    if not raw:
        return "raster"
    # Reject pixel-format lookalikes from _get_layer_type_string.
    upper = raw.upper().replace(" ", "")
    if upper in ("RGB", "RGBA", "GRAY", "GRAYA", "INDEXED", "UNKNOWN"):
        return "raster"
    # Normalize dotted/qualified names.
    name = raw.rsplit(".", 1)[-1]
    if name in _KIND_BY_GTYPE:
        return _KIND_BY_GTYPE[name]
    # Short forms / lowercase
    lower = name.lower()
    if lower in ("gimpgrouplayer", "grouplayer", "group"):
        return "group"
    if lower in ("gimptextlayer", "textlayer", "text"):
        return "text"
    if lower in ("gimplinklayer", "linklayer", "link"):
        return "link"
    if lower in ("gimpvectorlayer", "vectorlayer", "vector"):
        return "vector"
    return "raster"


def default_capabilities() -> dict[str, bool]:
    """Honest post-0004/0005 capability matrix (spec §7.5)."""
    return {
        # Design-required
        "visible_composite_snapshot": True,  # 0004
        "isolated_layer_snapshot": False,
        "alpha_snapshot": False,  # renderer → 0014
        "atomic_xcf_save": False,  # 0013
        "atomic_export": False,  # 0013
        # Extensions
        "mcp_image_visible_to_model": True,
        "filesystem_image_attachment": True,
        "batch_interpreter": False,  # 0019
        "alpha_preserving_export": True,  # 0005
        "state_manifest_orientation": True,  # 0006
        "stable_handle_registry": True,  # 0007
        "coordinate_exif_normalized": True,  # 0008
        "source_immutable_policy": True,  # 0009
        "checkpoints": True,  # 0009
    }


def _err(path: str, msg: str) -> str:
    return f"{path}: {msg}" if path else msg


def _validate_image_handle(handle: Any, path: str, errors: list[str]) -> None:
    if not isinstance(handle, dict):
        errors.append(_err(path, "must be an object"))
        return
    for key in ("image_id", "generation", "session_epoch"):
        if key not in handle:
            errors.append(_err(path, f"missing required field '{key}'"))
        elif not isinstance(handle[key], int) or isinstance(handle[key], bool):
            errors.append(_err(f"{path}.{key}", "must be an integer"))
        elif key == "generation" and int(handle[key]) < 1:
            errors.append(_err(f"{path}.{key}", "must be >= 1"))
        elif key == "session_epoch" and int(handle[key]) < 1:
            errors.append(_err(f"{path}.{key}", "must be >= 1"))


def _validate_item_handle(handle: Any, path: str, errors: list[str]) -> None:
    if not isinstance(handle, dict):
        errors.append(_err(path, "must be an object"))
        return
    for key in ("item_id", "generation", "image_id", "session_epoch"):
        if key not in handle:
            errors.append(_err(path, f"missing required field '{key}'"))
        elif not isinstance(handle[key], int) or isinstance(handle[key], bool):
            errors.append(_err(f"{path}.{key}", "must be an integer"))
        elif key in ("generation", "session_epoch") and int(handle[key]) < 1:
            errors.append(_err(f"{path}.{key}", "must be >= 1"))


def _validate_layer_node(node: Any, path: str, errors: list[str], depth: int = 0) -> None:
    if depth > MAX_LAYER_DEPTH:
        errors.append(_err(path, f"exceeds MAX_LAYER_DEPTH ({MAX_LAYER_DEPTH})"))
        return
    if not isinstance(node, dict):
        errors.append(_err(path, "must be an object"))
        return
    required = (
        "handle",
        "name",
        "kind",
        "parent_handle",
        "visible",
        "opacity",
        "blend_mode",
        "offset",
        "size",
        "children",
    )
    for key in required:
        if key not in node:
            errors.append(_err(path, f"missing required field '{key}'"))

    if "handle" in node:
        _validate_item_handle(node["handle"], f"{path}.handle", errors)

    if "name" in node and not isinstance(node["name"], str):
        errors.append(_err(f"{path}.name", "must be a string"))

    if "kind" in node:
        kind = node["kind"]
        if kind not in LAYER_KINDS:
            errors.append(
                _err(f"{path}.kind", f"must be one of {sorted(LAYER_KINDS)}, got {kind!r}")
            )

    if "visible" in node and not isinstance(node["visible"], bool):
        errors.append(_err(f"{path}.visible", "must be a boolean"))

    if "opacity" in node:
        op = node["opacity"]
        if not isinstance(op, (int, float)) or isinstance(op, bool):
            errors.append(_err(f"{path}.opacity", "must be a number"))
        elif not (0.0 <= float(op) <= 100.0):
            errors.append(_err(f"{path}.opacity", "must be in 0..100"))

    if "blend_mode" in node and not isinstance(node["blend_mode"], str):
        errors.append(_err(f"{path}.blend_mode", "must be a string"))

    if "offset" in node:
        off = node["offset"]
        if not isinstance(off, dict):
            errors.append(_err(f"{path}.offset", "must be an object"))
        else:
            for k in ("x", "y"):
                if k not in off:
                    errors.append(_err(f"{path}.offset", f"missing '{k}'"))
                elif not isinstance(off[k], int) or isinstance(off[k], bool):
                    errors.append(_err(f"{path}.offset.{k}", "must be an integer"))

    if "size" in node:
        size = node["size"]
        if not isinstance(size, dict):
            errors.append(_err(f"{path}.size", "must be an object"))
        else:
            for k in ("width", "height"):
                if k not in size:
                    errors.append(_err(f"{path}.size", f"missing '{k}'"))
                elif not isinstance(size[k], int) or isinstance(size[k], bool):
                    errors.append(_err(f"{path}.size.{k}", "must be an integer"))

    if "parent_handle" in node and node["parent_handle"] is not None:
        _validate_item_handle(node["parent_handle"], f"{path}.parent_handle", errors)

    children = node.get("children")
    if "children" in node:
        if not isinstance(children, list):
            errors.append(_err(f"{path}.children", "must be an array"))
        else:
            for i, child in enumerate(children):
                _validate_layer_node(child, f"{path}.children[{i}]", errors, depth=depth + 1)


def _validate_item_summary(item: Any, path: str, errors: list[str]) -> None:
    if not isinstance(item, dict):
        errors.append(_err(path, "must be an object"))
        return
    if "handle" not in item:
        errors.append(_err(path, "missing required field 'handle'"))
    else:
        _validate_item_handle(item["handle"], f"{path}.handle", errors)
    if "name" not in item:
        errors.append(_err(path, "missing required field 'name'"))
    elif not isinstance(item["name"], str):
        errors.append(_err(f"{path}.name", "must be a string"))


def _validate_image(image: Any, path: str, errors: list[str]) -> None:
    if not isinstance(image, dict):
        errors.append(_err(path, "must be an object"))
        return
    required = (
        "handle",
        "name",
        "width",
        "height",
        "base_type",
        "precision",
        "dirty",
        "selected",
        "selection",
        "alpha_present",
        "color_profile",
        "metadata",
        "active_layer_handles",
        "layers",
        "channels",
        "paths",
        "source_path",
    )
    for key in required:
        if key not in image:
            errors.append(_err(path, f"missing required field '{key}'"))

    if "handle" in image:
        _validate_image_handle(image["handle"], f"{path}.handle", errors)

    if "name" in image and not isinstance(image["name"], str):
        errors.append(_err(f"{path}.name", "must be a string"))

    if "source_path" in image:
        sp = image["source_path"]
        if sp is not None and not isinstance(sp, str):
            errors.append(_err(f"{path}.source_path", "must be a string or null"))

    for dim in ("width", "height"):
        if dim in image:
            v = image[dim]
            if not isinstance(v, int) or isinstance(v, bool):
                errors.append(_err(f"{path}.{dim}", "must be an integer"))
            elif int(v) < 1:
                errors.append(_err(f"{path}.{dim}", "must be >= 1"))

    if "base_type" in image:
        bt = image["base_type"]
        if bt not in BASE_TYPES:
            errors.append(
                _err(f"{path}.base_type", f"must be one of {sorted(BASE_TYPES)}, got {bt!r}")
            )

    if "precision" in image and not isinstance(image["precision"], str):
        errors.append(_err(f"{path}.precision", "must be a string"))

    if "dirty" in image and not isinstance(image["dirty"], bool):
        errors.append(_err(f"{path}.dirty", "must be a boolean"))

    if "selected" in image and not isinstance(image["selected"], bool):
        errors.append(_err(f"{path}.selected", "must be a boolean"))

    if "alpha_present" in image and not isinstance(image["alpha_present"], bool):
        errors.append(_err(f"{path}.alpha_present", "must be a boolean"))

    if "color_profile" in image and image["color_profile"] is not None:
        if not isinstance(image["color_profile"], dict):
            errors.append(_err(f"{path}.color_profile", "must be an object or null"))

    if "selection" in image:
        sel = image["selection"]
        if not isinstance(sel, dict):
            errors.append(_err(f"{path}.selection", "must be an object"))
        elif "empty" not in sel:
            errors.append(_err(f"{path}.selection", "missing required field 'empty'"))
        else:
            if not isinstance(sel["empty"], bool):
                errors.append(_err(f"{path}.selection.empty", "must be a boolean"))
            # When bounds is present and non-null, require integer x/y/width/height
            if "bounds" in sel and sel["bounds"] is not None:
                bounds = sel["bounds"]
                if not isinstance(bounds, dict):
                    errors.append(_err(f"{path}.selection.bounds", "must be an object or null"))
                else:
                    for bkey in ("x", "y", "width", "height"):
                        if bkey not in bounds:
                            errors.append(
                                _err(
                                    f"{path}.selection.bounds",
                                    f"missing required field '{bkey}'",
                                )
                            )
                        else:
                            bv = bounds[bkey]
                            if not isinstance(bv, int) or isinstance(bv, bool):
                                errors.append(
                                    _err(
                                        f"{path}.selection.bounds.{bkey}",
                                        "must be an integer",
                                    )
                                )

    if "metadata" in image:
        meta = image["metadata"]
        if not isinstance(meta, dict):
            errors.append(_err(f"{path}.metadata", "must be an object"))
        else:
            if "exif_orientation_original" not in meta:
                errors.append(
                    _err(f"{path}.metadata", "missing required field 'exif_orientation_original'")
                )
            else:
                exo = meta["exif_orientation_original"]
                if exo is not None and (
                    not isinstance(exo, int) or isinstance(exo, bool) or not (1 <= int(exo) <= 8)
                ):
                    errors.append(
                        _err(
                            f"{path}.metadata.exif_orientation_original",
                            "must be 1..8 or null",
                        )
                    )
            if "pixel_orientation_normalized" not in meta:
                errors.append(
                    _err(
                        f"{path}.metadata",
                        "missing required field 'pixel_orientation_normalized'",
                    )
                )
            elif not isinstance(meta["pixel_orientation_normalized"], bool):
                errors.append(
                    _err(f"{path}.metadata.pixel_orientation_normalized", "must be a boolean")
                )

    if "active_layer_handles" in image:
        alh = image["active_layer_handles"]
        if not isinstance(alh, list):
            errors.append(_err(f"{path}.active_layer_handles", "must be an array"))
        else:
            for i, h in enumerate(alh):
                _validate_item_handle(h, f"{path}.active_layer_handles[{i}]", errors)

    if "layers" in image:
        layers = image["layers"]
        if not isinstance(layers, list):
            errors.append(_err(f"{path}.layers", "must be an array"))
        else:
            for i, layer in enumerate(layers):
                _validate_layer_node(layer, f"{path}.layers[{i}]", errors, depth=0)

    for list_key in ("channels", "paths"):
        if list_key in image:
            items = image[list_key]
            if not isinstance(items, list):
                errors.append(_err(f"{path}.{list_key}", "must be an array"))
            else:
                for i, item in enumerate(items):
                    _validate_item_summary(item, f"{path}.{list_key}[{i}]", errors)


def validate_manifest(doc: Any) -> list[str]:
    """Recursive structural validation; return path-prefixed error strings.

    Empty list means valid. Stdlib only — product path does not depend on
    jsonschema (tests may additionally run Draft202012Validator).
    """
    errors: list[str] = []
    if not isinstance(doc, dict):
        return ["$: must be an object"]

    for key in (
        "schema_version",
        "captured_at",
        "session",
        "gimp",
        "images",
        "context",
        "capabilities",
    ):
        if key not in doc:
            errors.append(_err("$", f"missing required field '{key}'"))

    if "schema_version" in doc and doc["schema_version"] != SCHEMA_VERSION:
        errors.append(
            _err(
                "schema_version",
                f"must be {SCHEMA_VERSION!r}, got {doc['schema_version']!r}",
            )
        )

    if "captured_at" in doc:
        ca = doc["captured_at"]
        if not isinstance(ca, str) or not ca.strip():
            errors.append(_err("captured_at", "must be a non-empty string"))
        elif "T" not in ca and not ca.endswith("Z"):
            # Light ISO-8601-ish check (full parse not required offline)
            errors.append(
                _err(
                    "captured_at",
                    "must look like an ISO-8601 datetime (contain 'T' or end with 'Z')",
                )
            )

    session = doc.get("session")
    if "session" in doc:
        if not isinstance(session, dict):
            errors.append(_err("session", "must be an object"))
        else:
            for key in ("session_id", "epoch", "transport", "authenticated"):
                if key not in session:
                    errors.append(_err("session", f"missing required field '{key}'"))
            if "session_id" in session and not isinstance(session["session_id"], str):
                errors.append(_err("session.session_id", "must be a string"))
            if "epoch" in session:
                ep = session["epoch"]
                if not isinstance(ep, int) or isinstance(ep, bool) or ep < 1:
                    errors.append(_err("session.epoch", "must be an integer >= 1"))
            if "transport" in session and session["transport"] not in TRANSPORTS:
                errors.append(
                    _err(
                        "session.transport",
                        f"must be one of {sorted(TRANSPORTS)}, got {session['transport']!r}",
                    )
                )
            if "authenticated" in session and not isinstance(session["authenticated"], bool):
                errors.append(_err("session.authenticated", "must be a boolean"))

    gimp = doc.get("gimp")
    if "gimp" in doc:
        if not isinstance(gimp, dict):
            errors.append(_err("gimp", "must be an object"))
        else:
            for key in ("version", "api_version", "os", "executable"):
                if key not in gimp:
                    errors.append(_err("gimp", f"missing required field '{key}'"))
                elif not isinstance(gimp[key], str):
                    errors.append(_err(f"gimp.{key}", "must be a string"))

    if "images" in doc:
        images = doc["images"]
        if not isinstance(images, list):
            errors.append(_err("images", "must be an array"))
        else:
            for i, image in enumerate(images):
                _validate_image(image, f"images[{i}]", errors)

    if "context" in doc and not isinstance(doc["context"], dict):
        errors.append(_err("context", "must be an object"))

    caps = doc.get("capabilities")
    if "capabilities" in doc:
        if not isinstance(caps, dict):
            errors.append(_err("capabilities", "must be an object"))
        else:
            for key in _CAPABILITY_REQUIRED:
                if key not in caps:
                    errors.append(_err("capabilities", f"missing required field '{key}'"))
                elif not isinstance(caps[key], bool):
                    errors.append(_err(f"capabilities.{key}", "must be a boolean"))
            for key, val in caps.items():
                if not isinstance(val, bool):
                    errors.append(_err(f"capabilities.{key}", "must be a boolean"))

    return errors


def _normalize_layer_tree(node: dict[str, Any]) -> None:
    """In-place normalize opacity / kind on a layer node and children."""
    if "opacity" in node:
        node["opacity"] = normalize_opacity(node["opacity"])
    if "kind" in node and isinstance(node["kind"], str):
        # Re-classify if caller passed a raw type name; leave enum alone.
        if node["kind"] not in LAYER_KINDS:
            node["kind"] = classify_layer_kind(node["kind"])
    children = node.get("children")
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict):
                _normalize_layer_tree(child)


def finalize_manifest(
    raw: dict[str, Any],
    *,
    authenticated: bool,
    host: str | None = None,
    port: int | None = None,
    transport: str = "stdio-proxy",
    captured_at: str | None = None,
) -> dict[str, Any]:
    """Inject host fields, normalize enums, validate, return finalized manifest.

    Raises :class:`ValueError` when structural validation fails (with joined
    path-prefixed errors). Does not mutate *raw*.
    """
    if not isinstance(raw, dict):
        raise TypeError("raw manifest must be a dict")

    doc = copy.deepcopy(raw)
    doc["schema_version"] = SCHEMA_VERSION
    doc["captured_at"] = captured_at or utc_now_iso()

    session = doc.get("session")
    if not isinstance(session, dict):
        session = {}
        doc["session"] = session
    session["transport"] = transport
    session["authenticated"] = bool(authenticated)
    if port is not None:
        session["port"] = int(port)
    if host is not None:
        session["host"] = host
    # Ensure epoch is int >= 1 when present
    if "epoch" in session:
        try:
            session["epoch"] = max(1, int(session["epoch"]))
        except (TypeError, ValueError):
            session["epoch"] = 1
    elif "session_epoch" in session:
        # Tolerate plugin alias
        try:
            session["epoch"] = max(1, int(session.pop("session_epoch")))
        except (TypeError, ValueError):
            session["epoch"] = 1
            session.pop("session_epoch", None)

    gimp = doc.get("gimp")
    if not isinstance(gimp, dict):
        gimp = {
            "version": "unknown",
            "api_version": "3.0",
            "os": "unknown",
            "executable": "unknown",
        }
        doc["gimp"] = gimp
    else:
        for key, default in (
            ("version", "unknown"),
            ("api_version", "3.0"),
            ("os", "unknown"),
            ("executable", "unknown"),
        ):
            if key not in gimp or gimp[key] is None:
                gimp[key] = default
            else:
                gimp[key] = str(gimp[key])

    if not isinstance(doc.get("context"), dict):
        doc["context"] = {}

    if not isinstance(doc.get("images"), list):
        doc["images"] = []

    for image in doc["images"]:
        if not isinstance(image, dict):
            continue
        if "base_type" in image:
            image["base_type"] = normalize_base_type(image["base_type"])
        layers = image.get("layers")
        if isinstance(layers, list):
            for layer in layers:
                if isinstance(layer, dict):
                    _normalize_layer_tree(layer)

    doc["capabilities"] = default_capabilities()

    errors = validate_manifest(doc)
    if errors:
        raise ValueError("state manifest validation failed:\n" + "\n".join(errors))
    return doc
