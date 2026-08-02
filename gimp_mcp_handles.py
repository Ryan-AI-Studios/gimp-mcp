"""Pure stdlib stable-handle builders and validators (track 0007).

Shipped next to ``gimp-mcp-plugin.py`` (plug-in install) and imported by the
MCP host. No third-party imports; no GIMP/gi dependency.

Handle shape matches state-manifest provisional handles (0006) with live
per-image structural ``generation`` counters owned by the plugin.
"""

from __future__ import annotations

import hashlib
from typing import Any

from gimp_mcp_security import (
    CODE_FOREIGN_SESSION,
    CODE_HANDLE_NOT_FOUND,
    CODE_INVALID_HANDLE,
    CODE_STALE_HANDLE,
)

# Plugin DoS guard for select_layers (not a GIMP API limit).
MAX_SELECT_LAYERS = 64


def prune_image_generations(
    generations: dict[Any, Any],
    open_ids: set[int] | list[int] | tuple[int, ...],
) -> list[int]:
    """Drop generation-map keys not in the open-id set. Does not reseed.

    Mutates ``generations`` in place. Returns the list of dropped image ids.
    Closed ids are removed only — never re-inserted at generation 1.
    """
    open_set = {int(i) for i in open_ids}
    dropped: list[int] = []
    for key in list(generations.keys()):
        try:
            iid = int(key)
        except (TypeError, ValueError):
            generations.pop(key, None)
            continue
        if iid not in open_set:
            generations.pop(key, None)
            dropped.append(iid)
    return dropped


class HandleError(Exception):
    """Handle validation failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _is_int(value: Any) -> bool:
    """True for real ints only (bool is a subclass of int — reject)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _require_int_field(handle: dict[str, Any], key: str, *, min_value: int | None = None) -> int:
    if key not in handle:
        raise HandleError(CODE_INVALID_HANDLE, f"handle missing required field '{key}'")
    value = handle[key]
    if not _is_int(value):
        raise HandleError(CODE_INVALID_HANDLE, f"handle field '{key}' must be an integer")
    iv = int(value)
    if min_value is not None and iv < min_value:
        raise HandleError(CODE_INVALID_HANDLE, f"handle field '{key}' must be >= {min_value}")
    return iv


def image_handle(
    image_id: int,
    *,
    session_epoch: int,
    generation: int,
    fingerprint: str | None = None,
) -> dict[str, Any]:
    """Build a stable image handle. ``generation`` is required (no silent default)."""
    if not _is_int(image_id):
        raise HandleError(CODE_INVALID_HANDLE, "image_id must be an integer")
    if not _is_int(session_epoch) or int(session_epoch) < 1:
        raise HandleError(CODE_INVALID_HANDLE, "session_epoch must be an integer >= 1")
    if not _is_int(generation) or int(generation) < 1:
        raise HandleError(CODE_INVALID_HANDLE, "generation must be an integer >= 1")
    handle: dict[str, Any] = {
        "image_id": int(image_id),
        "generation": int(generation),
        "session_epoch": int(session_epoch),
    }
    if fingerprint is not None:
        handle["fingerprint"] = str(fingerprint)
    return handle


def item_handle(
    item_id: int,
    *,
    image_id: int,
    session_epoch: int,
    generation: int,
    fingerprint: str | None = None,
) -> dict[str, Any]:
    """Build a stable item (layer/channel/path) handle. ``generation`` is required."""
    if not _is_int(item_id):
        raise HandleError(CODE_INVALID_HANDLE, "item_id must be an integer")
    if not _is_int(image_id):
        raise HandleError(CODE_INVALID_HANDLE, "image_id must be an integer")
    if not _is_int(session_epoch) or int(session_epoch) < 1:
        raise HandleError(CODE_INVALID_HANDLE, "session_epoch must be an integer >= 1")
    if not _is_int(generation) or int(generation) < 1:
        raise HandleError(CODE_INVALID_HANDLE, "generation must be an integer >= 1")
    handle: dict[str, Any] = {
        "item_id": int(item_id),
        "image_id": int(image_id),
        "generation": int(generation),
        "session_epoch": int(session_epoch),
    }
    if fingerprint is not None:
        handle["fingerprint"] = str(fingerprint)
    return handle


def fingerprint_image(image_id: int, base_type: str, width: int, height: int) -> str:
    """SHA-256 hex of immutable image identity fields (no name)."""
    payload = f"{int(image_id)}|{base_type}|{int(width)}|{int(height)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def fingerprint_item(
    item_id: int,
    image_id: int,
    kind: str,
    width: int,
    height: int,
) -> str:
    """SHA-256 hex of immutable item identity fields (no name / parent / z)."""
    payload = f"{int(item_id)}|{int(image_id)}|{kind}|{int(width)}|{int(height)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _check_fingerprint(
    claimed: Any,
    current: str | None,
) -> None:
    """If both sides present and differ → STALE_HANDLE; one missing → ok."""
    if claimed is None or current is None:
        return
    claimed_s = str(claimed)
    if claimed_s != str(current):
        raise HandleError(
            CODE_STALE_HANDLE,
            "handle fingerprint does not match current identity (possible ID reuse)",
        )


def require_image_handle(
    handle: Any,
    *,
    live_epoch: int,
    live_generation: int,
    id_valid: bool,
    current_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Validate an image handle with locked precedence (§7.7).

    Order: shape → epoch → id validity → generation → fingerprint.
    """
    # 1. shape / types
    if not isinstance(handle, dict):
        raise HandleError(CODE_INVALID_HANDLE, "image handle must be an object")
    image_id = _require_int_field(handle, "image_id")
    generation = _require_int_field(handle, "generation", min_value=1)
    session_epoch = _require_int_field(handle, "session_epoch", min_value=1)
    if "fingerprint" in handle and handle["fingerprint"] is not None:
        if not isinstance(handle["fingerprint"], str):
            raise HandleError(CODE_INVALID_HANDLE, "fingerprint must be a string when present")

    # 2. session_epoch must equal live_epoch
    if int(session_epoch) != int(live_epoch):
        raise HandleError(
            CODE_FOREIGN_SESSION,
            f"handle session_epoch {session_epoch} != live epoch {live_epoch}; "
            "restart plugin and re-orient",
        )

    # 3. id validity
    if not id_valid:
        raise HandleError(
            CODE_HANDLE_NOT_FOUND,
            f"image_id {image_id} is not valid (closed or never existed)",
        )

    # 4. generation
    if int(generation) != int(live_generation):
        raise HandleError(
            CODE_STALE_HANDLE,
            f"handle generation {generation} != live generation {live_generation}; "
            "re-orient or use generation from last structural mutator",
        )

    # 5. fingerprint (both present)
    _check_fingerprint(handle.get("fingerprint"), current_fingerprint)

    return {
        "image_id": image_id,
        "generation": generation,
        "session_epoch": session_epoch,
        **({"fingerprint": handle["fingerprint"]} if handle.get("fingerprint") is not None else {}),
    }


def require_item_handle(
    handle: Any,
    *,
    live_epoch: int,
    live_generation: int,
    id_valid: bool,
    expected_image_id: int | None = None,
    item_belongs_to_image: bool | None = None,
    current_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Validate an item handle with locked precedence (§7.7).

    Membership (expected image / belongs) is checked after generation/fingerprint.
    """
    # 1. shape / types
    if not isinstance(handle, dict):
        raise HandleError(CODE_INVALID_HANDLE, "item handle must be an object")
    item_id = _require_int_field(handle, "item_id")
    image_id = _require_int_field(handle, "image_id")
    generation = _require_int_field(handle, "generation", min_value=1)
    session_epoch = _require_int_field(handle, "session_epoch", min_value=1)
    if "fingerprint" in handle and handle["fingerprint"] is not None:
        if not isinstance(handle["fingerprint"], str):
            raise HandleError(CODE_INVALID_HANDLE, "fingerprint must be a string when present")

    # 2. epoch
    if int(session_epoch) != int(live_epoch):
        raise HandleError(
            CODE_FOREIGN_SESSION,
            f"handle session_epoch {session_epoch} != live epoch {live_epoch}; "
            "restart plugin and re-orient",
        )

    # 3. id validity
    if not id_valid:
        raise HandleError(
            CODE_HANDLE_NOT_FOUND,
            f"item_id {item_id} is not valid (closed, deleted, or never existed)",
        )

    # 4. generation
    if int(generation) != int(live_generation):
        raise HandleError(
            CODE_STALE_HANDLE,
            f"handle generation {generation} != live generation {live_generation}; "
            "re-orient or use generation from last structural mutator",
        )

    # 5. fingerprint
    _check_fingerprint(handle.get("fingerprint"), current_fingerprint)

    # 6. membership
    if expected_image_id is not None and int(image_id) != int(expected_image_id):
        raise HandleError(
            CODE_HANDLE_NOT_FOUND,
            f"item handle image_id {image_id} does not match expected image {expected_image_id}",
        )
    if item_belongs_to_image is False:
        raise HandleError(
            CODE_HANDLE_NOT_FOUND,
            f"item_id {item_id} does not belong to image_id {image_id}",
        )

    out: dict[str, Any] = {
        "item_id": item_id,
        "image_id": image_id,
        "generation": generation,
        "session_epoch": session_epoch,
    }
    if handle.get("fingerprint") is not None:
        out["fingerprint"] = handle["fingerprint"]
    return out


def require_item_handles(
    handles: Any,
    *,
    live_epoch: int,
    live_generation: int,
    id_valid_flags: list[bool] | None = None,
    item_belongs_flags: list[bool | None] | None = None,
    current_fingerprints: list[str | None] | None = None,
    max_count: int = MAX_SELECT_LAYERS,
) -> list[dict[str, Any]]:
    """Validate a list of item handles (same image, 1..max_count).

    Empty list, >max_count, or mixed image_ids → INVALID_HANDLE.
    """
    if not isinstance(handles, list):
        raise HandleError(CODE_INVALID_HANDLE, "handles must be a list")
    if len(handles) == 0:
        raise HandleError(CODE_INVALID_HANDLE, "handles list must not be empty")
    if len(handles) > int(max_count):
        raise HandleError(
            CODE_INVALID_HANDLE,
            f"handles list length {len(handles)} exceeds max_count {max_count}",
        )

    # Pre-scan shape for mixed image_ids before deeper checks
    image_ids: list[int] = []
    for i, h in enumerate(handles):
        if not isinstance(h, dict):
            raise HandleError(CODE_INVALID_HANDLE, f"handles[{i}] must be an object")
        try:
            image_ids.append(_require_int_field(h, "image_id"))
        except HandleError:
            raise
    if len(set(image_ids)) > 1:
        raise HandleError(CODE_INVALID_HANDLE, "handles must all share the same image_id")

    expected_image_id = image_ids[0]
    validated: list[dict[str, Any]] = []
    for i, h in enumerate(handles):
        id_valid = True
        if id_valid_flags is not None and i < len(id_valid_flags):
            id_valid = bool(id_valid_flags[i])
        belongs: bool | None = None
        if item_belongs_flags is not None and i < len(item_belongs_flags):
            belongs = item_belongs_flags[i]
        fp: str | None = None
        if current_fingerprints is not None and i < len(current_fingerprints):
            fp = current_fingerprints[i]
        validated.append(
            require_item_handle(
                h,
                live_epoch=live_epoch,
                live_generation=live_generation,
                id_valid=id_valid,
                expected_image_id=expected_image_id,
                item_belongs_to_image=belongs,
                current_fingerprint=fp,
            )
        )
    return validated
