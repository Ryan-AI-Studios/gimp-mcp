"""Pure stdlib layer-policy + checkpoint helpers (track 0009).

Shipped next to ``gimp-mcp-plugin.py`` as one of the six shared plug-in install files.
No third-party imports; no GIMP/gi dependency.

Provides:
- checkpoint label sanitization (charset, reserved Windows names, traversal)
- workspace-relative checkpoint paths
- integrity ``sha256`` of as-written XCF bytes (not reproducibility)
- sidecar schema build/validate (``schema_version`` 1.0.0)
- Source_Immutable constants (group name + parasite marker)
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Source_Immutable constants
# ---------------------------------------------------------------------------

SOURCE_IMMUTABLE_GROUP_NAME = "Source_Immutable"
PARASITE_SOURCE_IMMUTABLE = "gimp-mcp:source-immutable"

# Coordinate-space constants (aligned with gimp_mcp_coords; no import required)
COORDINATE_SPACE = "image-pixels"
VIEW_ROTATION_IGNORED = True

# Sidecar contract
SIDECAR_SCHEMA_VERSION = "1.0.0"
CHECKPOINT_DIR_NAME = ".gimp-mcp-checkpoints"
CHECKPOINT_XCF_NAME = "project.xcf"
CHECKPOINT_JSON_NAME = "checkpoint.json"

# Label rules
_LABEL_MAX_LEN = 64
_LABEL_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Working-layer name suffix from ensure_source_immutable (idempotency skip)
_WORKING_NAME_RE = re.compile(r" \(working\)(?: \d+)?$")

# Windows reserved basenames (case-insensitive, without extension)
WINDOWS_RESERVED_NAMES: frozenset[str] = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)

_ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


def is_working_layer_name(name: str) -> bool:
    """True if *name* looks like an ensure_source_immutable working copy.

    Matches ``"{base} (working)"`` and ``"{base} (working) {n}"`` (n >= 2).
    Used so a second ensure call does not re-protect working layers.
    """
    if not isinstance(name, str) or not name:
        return False
    return bool(_WORKING_NAME_RE.search(name))


def sanitize_checkpoint_label(label: str) -> str:
    """Validate and return a safe checkpoint label.

    Rules (locked):
    - charset ``[A-Za-z0-9._-]+``
    - max length 64
    - reject empty, lone ``.``, ``..`` anywhere, trailing ``.`` or space
    - reject Windows reserved basenames (CON/PRN/AUX/NUL/COM1-9/LPT1-9),
      case-insensitive, without extension

    Raises ``ValueError`` on any violation.
    """
    if not isinstance(label, str):
        raise ValueError("checkpoint label must be a string")
    raw = label
    if raw == "" or raw.strip() == "":
        raise ValueError("checkpoint label must not be empty")
    # Traversal / reserved path tokens first (before trailing-dot strip eats "..")
    if ".." in raw:
        raise ValueError("checkpoint label must not contain '..'")
    if raw in (".",):
        raise ValueError("checkpoint label must not be '.'")
    # Reject trailing space/dot before charset check (explicit contract)
    if raw != raw.rstrip(" ."):
        raise ValueError("checkpoint label must not end with '.' or space")
    if len(raw) > _LABEL_MAX_LEN:
        raise ValueError(f"checkpoint label exceeds max length {_LABEL_MAX_LEN}")
    if not _LABEL_RE.match(raw):
        raise ValueError(f"checkpoint label must match [A-Za-z0-9._-]+ (got {raw!r})")
    # Reserved basename (case-insensitive); strip a trailing extension-like suffix
    # only for the reserved check so "CON.txt" is also rejected if it slipped charset
    # — but charset forbids most of those; still check the full token uppercased.
    base = raw.split(".", 1)[0].upper() if "." in raw else raw.upper()
    # Also check full label upper (e.g. label "CON")
    if raw.upper() in WINDOWS_RESERVED_NAMES or base in WINDOWS_RESERVED_NAMES:
        raise ValueError(f"checkpoint label is a reserved Windows device name: {raw!r}")
    return raw


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def checkpoint_dir(workspace_root: str | Path, label: str) -> Path:
    """Return ``{root}/.gimp-mcp-checkpoints/{label}/`` (label must already be sanitized)."""
    safe = sanitize_checkpoint_label(label)
    root = Path(workspace_root)
    return root / CHECKPOINT_DIR_NAME / safe


def checkpoint_xcf_path(workspace_root: str | Path, label: str) -> Path:
    """Return path to ``project.xcf`` under the checkpoint directory."""
    return checkpoint_dir(workspace_root, label) / CHECKPOINT_XCF_NAME


def checkpoint_json_path(workspace_root: str | Path, label: str) -> Path:
    """Return path to ``checkpoint.json`` under the checkpoint directory."""
    return checkpoint_dir(workspace_root, label) / CHECKPOINT_JSON_NAME


# ---------------------------------------------------------------------------
# Integrity hash (as-written bytes — not XCF reproducibility)
# ---------------------------------------------------------------------------


def sha256_file(path: str | Path) -> str:
    """Return lowercase hex SHA-256 of file bytes (integrity of as-written content).

    This is **not** a reproducibility hash: XCF encoding is non-deterministic.
    Soft-compare on restore only; hard reopen verify is track 0013.
    """
    p = Path(path)
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Sidecar
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    """Current UTC time as ISO-8601 with ``Z`` suffix (no microseconds)."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_sidecar(
    *,
    label: str,
    session_epoch: int,
    image: dict[str, Any],
    xcf_path: str,
    xcf_sha256: str,
    layers: list[dict[str, Any]] | None = None,
    created_at: str | None = None,
    coordinate_space: str = COORDINATE_SPACE,
    view_rotation_ignored: bool = VIEW_ROTATION_IGNORED,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a v1.0.0 checkpoint sidecar dict (not yet validated).

    ``image`` should include at least ``image_id``, ``generation``, ``width``,
    ``height``; optional ``name``.
    ``layers`` entries: ``item_id``, ``name``, ``kind``; optional ``tattoo``,
    ``parent_item_id``, ``protected``.
    """
    safe_label = sanitize_checkpoint_label(label)
    doc: dict[str, Any] = {
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "label": safe_label,
        "created_at": created_at if created_at is not None else utc_now_iso(),
        "session_epoch": int(session_epoch),
        "image": dict(image),
        "xcf_path": str(xcf_path),
        "xcf_sha256": str(xcf_sha256),
        "coordinate_space": coordinate_space,
        "view_rotation_ignored": bool(view_rotation_ignored),
        "layers": list(layers) if layers is not None else [],
    }
    if extra:
        for k, v in extra.items():
            if k not in doc:
                doc[k] = v
    return doc


def _parse_iso_datetime(value: str) -> datetime:
    """Parse ISO-8601 datetime with explicit Z or offset (raise ValueError)."""
    if not isinstance(value, str) or not value:
        raise ValueError("created_at must be a non-empty ISO-8601 string")
    if not _ISO_Z_RE.match(value):
        raise ValueError(f"created_at is not a valid ISO-8601 datetime: {value!r}")
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError as e:
        raise ValueError(f"created_at parse failed: {value!r}") from e


def validate_sidecar(data: Any) -> dict[str, Any]:
    """Validate a checkpoint sidecar mapping; return a shallow-normalized copy.

    Raises ``ValueError`` on any contract violation. Explicitly parses
    ``created_at`` as ISO datetime.
    """
    if not isinstance(data, dict):
        raise ValueError("sidecar must be a dict/object")
    out: dict[str, Any] = dict(data)

    sv = out.get("schema_version")
    if sv != SIDECAR_SCHEMA_VERSION:
        raise ValueError(f"sidecar schema_version must be {SIDECAR_SCHEMA_VERSION!r}, got {sv!r}")

    label = out.get("label")
    if not isinstance(label, str):
        raise ValueError("sidecar label must be a string")
    sanitize_checkpoint_label(label)

    created_at = out.get("created_at")
    if not isinstance(created_at, str):
        raise ValueError("sidecar created_at must be a string")
    _parse_iso_datetime(created_at)

    epoch = out.get("session_epoch")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
        raise ValueError("sidecar session_epoch must be an integer >= 1")

    image = out.get("image")
    if not isinstance(image, dict):
        raise ValueError("sidecar image must be an object")
    for key in ("image_id", "generation", "width", "height"):
        if key not in image:
            raise ValueError(f"sidecar image missing required field '{key}'")
        val = image[key]
        if not isinstance(val, int) or isinstance(val, bool):
            raise ValueError(f"sidecar image.{key} must be an integer")
    if int(image["generation"]) < 1:
        raise ValueError("sidecar image.generation must be >= 1")
    if "name" in image and image["name"] is not None and not isinstance(image["name"], str):
        raise ValueError("sidecar image.name must be a string when present")

    xcf_path = out.get("xcf_path")
    if not isinstance(xcf_path, str) or not xcf_path:
        raise ValueError("sidecar xcf_path must be a non-empty string")

    digest = out.get("xcf_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise ValueError("sidecar xcf_sha256 must be a 64-char hex digest")

    cs = out.get("coordinate_space")
    if cs != COORDINATE_SPACE:
        raise ValueError(f"sidecar coordinate_space must be {COORDINATE_SPACE!r}, got {cs!r}")
    vri = out.get("view_rotation_ignored")
    if vri is not True:
        raise ValueError(f"sidecar view_rotation_ignored must be true, got {vri!r}")

    layers = out.get("layers")
    if not isinstance(layers, list):
        raise ValueError("sidecar layers must be a list")
    for i, layer in enumerate(layers):
        if not isinstance(layer, dict):
            raise ValueError(f"sidecar layers[{i}] must be an object")
        for key in ("item_id", "name", "kind"):
            if key not in layer:
                raise ValueError(f"sidecar layers[{i}] missing required field '{key}'")
        if not isinstance(layer["item_id"], int) or isinstance(layer["item_id"], bool):
            raise ValueError(f"sidecar layers[{i}].item_id must be an integer")
        if not isinstance(layer["name"], str):
            raise ValueError(f"sidecar layers[{i}].name must be a string")
        if not isinstance(layer["kind"], str):
            raise ValueError(f"sidecar layers[{i}].kind must be a string")
        if "tattoo" in layer and layer["tattoo"] is not None:
            if not isinstance(layer["tattoo"], int) or isinstance(layer["tattoo"], bool):
                raise ValueError(f"sidecar layers[{i}].tattoo must be an integer or null")
        if "parent_item_id" in layer and layer["parent_item_id"] is not None:
            if not isinstance(layer["parent_item_id"], int) or isinstance(
                layer["parent_item_id"], bool
            ):
                raise ValueError(f"sidecar layers[{i}].parent_item_id must be an integer or null")
        if "protected" in layer and layer["protected"] is not None:
            if not isinstance(layer["protected"], bool):
                raise ValueError(f"sidecar layers[{i}].protected must be a boolean")

    return out
