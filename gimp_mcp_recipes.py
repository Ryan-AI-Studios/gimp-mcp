"""Versioned allowlisted recipe library (track 0015).

Host-only pure module: load/validate package-data JSON recipes, whole-value
``$name`` interpolation, host + session step runner with ``created_paths``
rollback. No PyYAML / Pillow / numpy. Recipe ops call plugin TCP / host
modules directly — **not** gated by MCP advanced/HL tags.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from importlib import resources
from pathlib import Path
from typing import Any

import gimp_mcp_security as sec

logger = logging.getLogger("GimpMCPRecipes")

# ---------------------------------------------------------------------------
# Allowlist + constants
# ---------------------------------------------------------------------------

ALLOWLISTED_OPS: frozenset[str] = frozenset(
    {
        "export_image",
        "save_xcf",
        "open_image",
        "normalize_image_orientation",
        "ensure_source_immutable",
        "verify_alpha_channel",
        "verify_artifact",
        "compare_images",
        "scale_image",
        "checkpoint_create",
        "exiftool_strip",
    }
)

GIMP_OPS: frozenset[str] = frozenset(
    {
        "export_image",
        "save_xcf",
        "open_image",
        "normalize_image_orientation",
        "ensure_source_immutable",
        "verify_alpha_channel",
        "scale_image",
        "checkpoint_create",
    }
)

HOST_OPS: frozenset[str] = frozenset(
    {
        "verify_artifact",
        "compare_images",
        "exiftool_strip",
    }
)

PARAM_TYPES: frozenset[str] = frozenset({"path", "string", "int", "float", "bool"})
COLLISION_ENUM: frozenset[str] = frozenset({"fail", "version", "replace"})
RESERVED_PARAM_NAMES: frozenset[str] = frozenset({"input_path", "output_path", "handle"})

# Whole-value only: string interpolates iff exactly ``$name``.
_INTERPOLATE_RE = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)$")
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

# Path-like keys jailed at step use site (after interpolation).
_PATH_KEYS: frozenset[str] = frozenset(
    {
        "file_path",
        "path",
        "path_a",
        "path_b",
        "write_diff_path",
        "diff_out",
        "input_path",
        "output_path",
    }
)

ENV_EXIF_TIMEOUT = "GIMP_MCP_EXIF_TIMEOUT"
DEFAULT_EXIF_TIMEOUT_S = 30.0

SessionSend = Callable[[str, dict[str, Any]], dict[str, Any]]

# Module-level registry cache (loaded on first use).
_REGISTRY: RecipeRegistry | None = None


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class RecipeRegistry:
    """In-memory registry of validated recipes keyed by ``id`` → version map."""

    def __init__(self) -> None:
        # id -> {version_str -> recipe dict}
        self._by_id: dict[str, dict[str, dict[str, Any]]] = {}

    def add(self, recipe: dict[str, Any]) -> None:
        rid = str(recipe["id"])
        ver = str(recipe["version"])
        bucket = self._by_id.setdefault(rid, {})
        if ver in bucket:
            raise sec.GimpMcpError(
                sec.CODE_INTERNAL,
                f"duplicate recipe id@version: {rid}@{ver}",
                details={"recipe_id": rid, "version": ver},
            )
        bucket[ver] = recipe

    def get(self, recipe_id: str, version: str | None = None) -> dict[str, Any]:
        bucket = self._by_id.get(recipe_id)
        if not bucket:
            raise sec.GimpMcpError(
                sec.CODE_UNSUPPORTED,
                f"unknown recipe id: {recipe_id!r}",
                details={"recipe_id": recipe_id},
            )
        if version is None:
            best_ver = max(bucket.keys(), key=_parse_semver)
            return dict(bucket[best_ver])
        if version not in bucket:
            raise sec.GimpMcpError(
                sec.CODE_UNSUPPORTED,
                f"unknown recipe version: {recipe_id}@{version}",
                details={"recipe_id": recipe_id, "version": version},
            )
        return dict(bucket[version])

    def list_summaries(self) -> list[dict[str, Any]]:
        """Return list_recipes-shaped summaries (latest version per id)."""
        out: list[dict[str, Any]] = []
        for rid in sorted(self._by_id.keys()):
            recipe = self.get(rid)
            out.append(
                {
                    "id": recipe["id"],
                    "version": recipe["version"],
                    "title": recipe.get("title") or recipe["id"],
                    "batch_safe": bool(recipe.get("batch_safe", False)),
                    "requires_open_session": bool(recipe.get("requires_open_session", False)),
                    "requires_gimp": bool(recipe.get("requires_gimp", True)),
                }
            )
        return out

    def ids(self) -> list[str]:
        return sorted(self._by_id.keys())

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_id.values())


# ---------------------------------------------------------------------------
# Semver (stdlib only)
# ---------------------------------------------------------------------------


def _parse_semver(value: str) -> tuple[int, int, int]:
    """Parse ``MAJOR.MINOR.PATCH`` to an int tuple. Raises ValueError if invalid."""
    m = _SEMVER_RE.match(str(value).strip())
    if not m:
        raise ValueError(f"invalid semver (need MAJOR.MINOR.PATCH): {value!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


# ---------------------------------------------------------------------------
# Interpolation (whole-value ``$name`` only, single pass)
# ---------------------------------------------------------------------------


def interpolate(value: Any, context: Mapping[str, Any]) -> Any:
    """Recursively interpolate whole-value ``$name`` strings (single pass).

    A string interpolates iff it is **exactly** ``$name`` matching
    ``^\\$([A-Za-z_][A-Za-z0-9_]*)$``. No ``${name}``, no mid-string replace.
    Substituted values are **not** re-scanned. Undefined ``$name`` raises.
    """
    if isinstance(value, str):
        m = _INTERPOLATE_RE.match(value)
        if not m:
            return value
        name = m.group(1)
        if name not in context:
            raise sec.GimpMcpError(
                sec.CODE_POLICY_DENIED,
                f"undefined recipe parameter ${name}",
                details={"param": name},
            )
        # Single pass: return raw context value without re-scanning.
        return context[name]
    if isinstance(value, dict):
        return {k: interpolate(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate(v, context) for v in value]
    return value


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_param_schema(name: str, spec: Any, path: str) -> None:
    if not isinstance(spec, dict):
        raise ValueError(f"{path}: parameter {name!r} must be an object")
    ptype = spec.get("type")
    if ptype not in PARAM_TYPES:
        raise ValueError(f"{path}: parameter {name!r} type must be one of {sorted(PARAM_TYPES)}")
    if "required" in spec and not isinstance(spec["required"], bool):
        raise ValueError(f"{path}: parameter {name!r} required must be bool")
    if "enum" in spec:
        enum = spec["enum"]
        if not isinstance(enum, list) or not enum:
            raise ValueError(f"{path}: parameter {name!r} enum must be a non-empty list")
        if not all(isinstance(x, str) for x in enum):
            raise ValueError(f"{path}: parameter {name!r} enum values must be strings")
    if "default" in spec:
        # Fail-closed: defaults must type-check (and satisfy enum) same as runtime.
        try:
            _coerce_and_check(name, spec["default"], spec)
        except sec.GimpMcpError as exc:
            raise ValueError(
                f"{path}: parameter {name!r} default is invalid for type {ptype!r}: {exc.message}"
            ) from exc


def validate_recipe(recipe: Any, *, source: str = "<recipe>") -> dict[str, Any]:
    """Validate one recipe dict. Raises ValueError on schema failure."""
    if not isinstance(recipe, dict):
        raise ValueError(f"{source}: recipe must be a JSON object")
    for key in ("id", "version", "steps"):
        if key not in recipe:
            raise ValueError(f"{source}: missing required field {key!r}")
    rid = recipe["id"]
    if not isinstance(rid, str) or not rid.strip():
        raise ValueError(f"{source}: id must be a non-empty string")
    try:
        _parse_semver(str(recipe["version"]))
    except ValueError as exc:
        raise ValueError(f"{source}: {exc}") from exc
    for flag in ("batch_safe", "requires_open_session", "requires_gimp"):
        if flag in recipe and not isinstance(recipe[flag], bool):
            raise ValueError(f"{source}: {flag} must be bool")
    params = recipe.get("parameters", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError(f"{source}: parameters must be an object")
    for pname, pspec in params.items():
        if not isinstance(pname, str) or not pname:
            raise ValueError(f"{source}: parameter names must be non-empty strings")
        _validate_param_schema(pname, pspec, source)
    steps = recipe["steps"]
    if not isinstance(steps, list) or not steps:
        raise ValueError(f"{source}: steps must be a non-empty array")
    for i, step in enumerate(steps):
        sp = f"{source}.steps[{i}]"
        if not isinstance(step, dict):
            raise ValueError(f"{sp}: step must be an object")
        op = step.get("op")
        if not isinstance(op, str) or op not in ALLOWLISTED_OPS:
            raise ValueError(f"{sp}: op must be one of allowlisted ops, got {op!r}")
        with_block = step.get("with", {})
        if with_block is None:
            with_block = {}
        if not isinstance(with_block, dict):
            raise ValueError(f"{sp}: with must be an object")
    rollback = recipe.get("rollback")
    if rollback is not None:
        if not isinstance(rollback, dict):
            raise ValueError(f"{source}: rollback must be an object")
        if "delete_outputs_on_fail" in rollback and not isinstance(
            rollback["delete_outputs_on_fail"], bool
        ):
            raise ValueError(f"{source}: rollback.delete_outputs_on_fail must be bool")
    return recipe


def load_recipes_from_dir(directory: str | Path) -> RecipeRegistry:
    """Load and validate all ``*.json`` recipes under ``directory`` (fail-closed)."""
    root = Path(directory)
    if not root.is_dir():
        raise sec.GimpMcpError(
            sec.CODE_INTERNAL,
            f"recipe directory not found: {root}",
            details={"path": str(root)},
        )
    registry = RecipeRegistry()
    files = sorted(root.glob("*.json"))
    if not files:
        raise sec.GimpMcpError(
            sec.CODE_INTERNAL,
            f"no recipe JSON files in {root}",
            details={"path": str(root)},
        )
    errors: list[str] = []
    for path in files:
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            recipe = validate_recipe(data, source=str(path.name))
            registry.add(recipe)
        except (OSError, json.JSONDecodeError, ValueError, sec.GimpMcpError) as exc:
            errors.append(f"{path.name}: {exc}")
    if errors:
        raise sec.GimpMcpError(
            sec.CODE_INTERNAL,
            "recipe registry load failed (fail-closed)",
            details={"errors": errors},
        )
    return registry


def recipes_package_dir() -> Path:
    """Resolve package-data recipes directory via importlib.resources."""
    # Traversable → Path for callers that need filesystem path (tests / editable).
    base = resources.files("gimp_agent").joinpath("recipes")
    # Prefer as_file when available; otherwise str() of Traversable.
    try:
        return Path(str(base))
    except TypeError:
        return Path(os.fspath(base))  # type: ignore[arg-type]


def load_package_recipes(*, force_reload: bool = False) -> RecipeRegistry:
    """Load recipes from ``gimp_agent`` package data (cached)."""
    global _REGISTRY
    if _REGISTRY is not None and not force_reload:
        return _REGISTRY
    base = resources.files("gimp_agent").joinpath("recipes")
    registry = RecipeRegistry()
    errors: list[str] = []
    try:
        entries = [p for p in base.iterdir() if p.name.endswith(".json")]
        entries.sort(key=lambda p: p.name)
    except (OSError, FileNotFoundError, AttributeError) as exc:
        raise sec.GimpMcpError(
            sec.CODE_INTERNAL,
            f"recipe package resources unavailable: {exc}",
            details={},
        ) from exc
    if not entries:
        raise sec.GimpMcpError(
            sec.CODE_INTERNAL,
            "no recipe JSON in package data gimp_agent/recipes",
            details={},
        )
    for entry in entries:
        try:
            raw = entry.read_text(encoding="utf-8")
            data = json.loads(raw)
            recipe = validate_recipe(data, source=entry.name)
            registry.add(recipe)
        except (OSError, json.JSONDecodeError, ValueError, sec.GimpMcpError) as exc:
            errors.append(f"{entry.name}: {exc}")
    if errors:
        raise sec.GimpMcpError(
            sec.CODE_INTERNAL,
            "recipe registry load failed (fail-closed)",
            details={"errors": errors},
        )
    _REGISTRY = registry
    return registry


def reset_registry_cache() -> None:
    """Clear module registry cache (tests)."""
    global _REGISTRY
    _REGISTRY = None


def get_recipe(recipe_id: str, version: str | None = None) -> dict[str, Any]:
    """Return a recipe by id (latest semver when version omitted)."""
    return load_package_recipes().get(recipe_id, version)


def list_recipes() -> list[dict[str, Any]]:
    """Return list_recipes MCP shape: ``[{id, version, title, flags...}, ...]``."""
    return load_package_recipes().list_summaries()


def list_package_recipe_files() -> list[str]:
    """Return sorted recipe JSON filenames from package data."""
    base = resources.files("gimp_agent").joinpath("recipes")
    return sorted(p.name for p in base.iterdir() if p.name.endswith(".json"))


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


def apply_defaults_and_check_params(
    recipe: Mapping[str, Any],
    raw_params: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Apply defaults, require required, type-check. Returns merged param dict.

    Optional params without default become ``None`` so ``$name`` can resolve
    (e.g. optional scale width/height).
    """
    schema = recipe.get("parameters") or {}
    if not isinstance(schema, dict):
        schema = {}
    raw = dict(raw_params or {})
    out: dict[str, Any] = {}
    # Reject unknown keys
    for key in raw:
        if key not in schema and key not in RESERVED_PARAM_NAMES:
            raise sec.GimpMcpError(
                sec.CODE_POLICY_DENIED,
                f"unknown recipe parameter: {key!r}",
                details={"param": key, "recipe_id": recipe.get("id")},
            )
    for name, spec in schema.items():
        if not isinstance(spec, dict):
            continue
        required = bool(spec.get("required", False))
        if name in raw and raw[name] is not None:
            value = raw[name]
        elif "default" in spec:
            value = spec["default"]
        elif required:
            raise sec.GimpMcpError(
                sec.CODE_POLICY_DENIED,
                f"missing required recipe parameter: {name!r}",
                details={"param": name, "recipe_id": recipe.get("id")},
            )
        else:
            value = None
        if value is not None:
            value = _coerce_and_check(name, value, spec)
        out[name] = value
    # Pass through reserved names if present in raw (handle/input/output set by runner)
    for reserved in RESERVED_PARAM_NAMES:
        if reserved in raw and reserved not in out:
            out[reserved] = raw[reserved]
    return out


def _coerce_and_check(name: str, value: Any, spec: Mapping[str, Any]) -> Any:
    ptype = spec.get("type", "string")
    if ptype == "path":
        if not isinstance(value, str) or not value.strip():
            raise sec.GimpMcpError(
                sec.CODE_POLICY_DENIED,
                f"parameter {name!r} must be a non-empty path string",
                details={"param": name},
            )
        return value
    if ptype == "string":
        if not isinstance(value, str):
            raise sec.GimpMcpError(
                sec.CODE_POLICY_DENIED,
                f"parameter {name!r} must be a string",
                details={"param": name},
            )
        if "enum" in spec and value not in spec["enum"]:
            raise sec.GimpMcpError(
                sec.CODE_POLICY_DENIED,
                f"parameter {name!r} must be one of {spec['enum']}",
                details={"param": name, "value": value},
            )
        return value
    if ptype == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            # Allow numeric strings from CLI
            if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
                value = int(value.strip())
            else:
                raise sec.GimpMcpError(
                    sec.CODE_POLICY_DENIED,
                    f"parameter {name!r} must be an int",
                    details={"param": name},
                )
        return int(value)
    if ptype == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            if isinstance(value, str):
                try:
                    value = float(value.strip())
                except ValueError as exc:
                    raise sec.GimpMcpError(
                        sec.CODE_POLICY_DENIED,
                        f"parameter {name!r} must be a float",
                        details={"param": name},
                    ) from exc
            else:
                raise sec.GimpMcpError(
                    sec.CODE_POLICY_DENIED,
                    f"parameter {name!r} must be a float",
                    details={"param": name},
                )
        return float(value)
    if ptype == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("1", "true", "yes", "on"):
                return True
            if low in ("0", "false", "no", "off"):
                return False
        raise sec.GimpMcpError(
            sec.CODE_POLICY_DENIED,
            f"parameter {name!r} must be a bool",
            details={"param": name},
        )
    raise sec.GimpMcpError(
        sec.CODE_POLICY_DENIED,
        f"parameter {name!r} has unsupported type {ptype!r}",
        details={"param": name},
    )


def parse_cli_param_pairs(pairs: list[str] | None) -> dict[str, str]:
    """Parse ``KEY=VALUE`` CLI pairs into a string dict (typed later)."""
    out: dict[str, str] = {}
    for raw in pairs or []:
        if "=" not in raw:
            raise sec.GimpMcpError(
                sec.CODE_POLICY_DENIED,
                f"invalid --param {raw!r}; expected KEY=VALUE",
                details={"param": raw},
            )
        key, _, value = raw.partition("=")
        key = key.strip()
        if not key:
            raise sec.GimpMcpError(
                sec.CODE_POLICY_DENIED,
                f"invalid --param {raw!r}; empty key",
                details={"param": raw},
            )
        out[key] = value
    return out


# ---------------------------------------------------------------------------
# Path jail helper
# ---------------------------------------------------------------------------


def _jail_path(path: str | Path | None, label: str = "path") -> str | None:
    if path is None:
        return None
    if not isinstance(path, (str, Path)):
        raise sec.GimpMcpError(
            sec.CODE_POLICY_DENIED,
            f"{label} must be a path string",
            details={"label": label},
        )
    text = str(path).strip()
    if not text:
        raise sec.SecurityError(sec.CODE_PATH_DENIED, f"Empty {label} denied")
    return str(sec.resolve_under_root(text))


def _jail_paths_in_mapping(data: dict[str, Any]) -> dict[str, Any]:
    """Jail known path keys at top level of step ``with`` (not nested expected)."""
    out = dict(data)
    for key in list(out.keys()):
        if key in _PATH_KEYS and out[key] is not None:
            out[key] = _jail_path(out[key], key)
    return out


# ---------------------------------------------------------------------------
# Host ops
# ---------------------------------------------------------------------------


def _exif_timeout_s() -> float:
    raw = os.environ.get(ENV_EXIF_TIMEOUT, "")
    if raw and str(raw).strip():
        try:
            val = float(str(raw).strip())
            if val > 0 and val == val:  # finite positive
                return val
        except ValueError:
            pass
    return DEFAULT_EXIF_TIMEOUT_S


def run_exiftool_strip(path: str) -> dict[str, Any]:
    """Strip metadata via ExifTool (fixed flags, shell=False).

    Missing binary → ``CODE_UNSUPPORTED``.
    """
    jailed = _jail_path(path, "path")
    assert jailed is not None
    exe = shutil.which("exiftool")
    if not exe:
        raise sec.GimpMcpError(
            sec.CODE_UNSUPPORTED,
            "exiftool not found on PATH (required for exiftool_strip)",
            details={"path": jailed},
        )
    timeout = _exif_timeout_s()
    cmd = [exe, "-overwrite_original_in_place", "-all=", jailed]
    try:
        completed = subprocess.run(
            cmd,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise sec.GimpMcpError(
            sec.CODE_TIMEOUT,
            f"exiftool timed out after {timeout}s",
            details={"path": jailed, "timeout": timeout},
        ) from exc
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        raise sec.GimpMcpError(
            sec.CODE_INTERNAL,
            f"exiftool failed (exit {completed.returncode}): {err or 'no output'}",
            details={"path": jailed, "returncode": completed.returncode},
        )
    # Best-effort audit (never log file contents); write_audit_event swallows OSError
    sec.write_audit_event(
        {
            "event": "exiftool_strip",
            "path": jailed,
            "exe": exe,
            "returncode": completed.returncode,
        },
        sec.audit_server_path(),
    )
    return {
        "path": jailed,
        "stripped": True,
        "exiftool": exe,
    }


def _run_host_op(op: str, params: dict[str, Any]) -> dict[str, Any]:
    import gimp_mcp_verify as verify

    if op == "verify_artifact":
        path = params.get("path")
        if not path:
            raise sec.GimpMcpError(
                sec.CODE_POLICY_DENIED,
                "verify_artifact requires path",
                details={},
            )
        expected = params.get("expected") or {}
        if not isinstance(expected, dict):
            raise sec.GimpMcpError(
                sec.CODE_POLICY_DENIED,
                "verify_artifact expected must be an object",
                details={},
            )
        # Drop None-valued gates (optional interpolated params)
        clean_expected = {k: v for k, v in expected.items() if v is not None}
        report = verify.verify_artifact(
            str(path),
            clean_expected,
            raise_on_fail=bool(params.get("raise_on_fail", True)),
        )
        if not report.get("pass", False) and params.get("raise_on_fail", True):
            raise sec.GimpMcpError(
                sec.CODE_VERIFY_FAILED,
                "verify_artifact expectations failed",
                details=report,
            )
        return report

    if op == "compare_images":
        path_a = params.get("path_a")
        path_b = params.get("path_b")
        if not path_a or not path_b:
            raise sec.GimpMcpError(
                sec.CODE_POLICY_DENIED,
                "compare_images requires path_a and path_b",
                details={},
            )
        thresholds_raw = params.get("thresholds")
        thresholds: dict[str, Any] = {}
        if isinstance(thresholds_raw, dict):
            thresholds = {k: v for k, v in thresholds_raw.items() if v is not None}
        # Flatten optional top-level threshold keys
        for key in (
            "max_mae",
            "min_ssim",
            "max_max_ae",
            "min_changed_pixels",
            "max_changed_fraction",
            "require_mutation",
            "require_same_size",
        ):
            if key in params and params[key] is not None:
                thresholds[key] = params[key]
        write_diff = params.get("write_diff_path") or params.get("diff_out")
        report = verify.compare_images(
            str(path_a),
            str(path_b),
            thresholds=thresholds or None,
            write_diff_path=str(write_diff) if write_diff else None,
            raise_on_fail=bool(params.get("raise_on_fail", True)),
            ignore_alpha=bool(params.get("ignore_alpha", False)),
        )
        if not report.get("pass", False) and params.get("raise_on_fail", True):
            raise sec.GimpMcpError(
                sec.CODE_VERIFY_FAILED,
                "compare_images thresholds failed",
                details=report,
            )
        return report

    if op == "exiftool_strip":
        path = params.get("path")
        if not path:
            raise sec.GimpMcpError(
                sec.CODE_POLICY_DENIED,
                "exiftool_strip requires path",
                details={},
            )
        return run_exiftool_strip(str(path))

    raise sec.GimpMcpError(
        sec.CODE_UNSUPPORTED,
        f"host op not implemented: {op}",
        details={"op": op},
    )


# ---------------------------------------------------------------------------
# Session ops
# ---------------------------------------------------------------------------


def _plugin_result_or_raise(result: dict[str, Any], op: str) -> dict[str, Any]:
    if result.get("status") == "success":
        raw = result.get("results")
        if isinstance(raw, dict):
            return raw
        return {"results": raw}
    code = result.get("code")
    err_code = code if isinstance(code, str) else sec.CODE_INTERNAL
    message = str(result.get("error") or f"plugin op {op} failed")
    details = {k: v for k, v in result.items() if k not in ("status", "error", "code")}
    raise sec.GimpMcpError(err_code, message, details=details or None)


def _run_session_op(
    op: str,
    params: dict[str, Any],
    *,
    session_send: SessionSend,
    current_handle: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = dict(params)
    # Inject session handle for image-targeted ops when not open_image.
    if op != "open_image" and current_handle is not None:
        if "handle" not in payload and "image_index" not in payload:
            payload["handle"] = current_handle
    # scale_image optional skip when dimensions missing
    if op == "scale_image":
        width = payload.get("width")
        height = payload.get("height")
        if width is None or height is None:
            return {"skipped": True, "reason": "width/height not provided"}
        payload["width"] = int(width)
        payload["height"] = int(height)
    result = session_send(op, payload)
    if not isinstance(result, dict):
        raise sec.GimpMcpError(
            sec.CODE_INTERNAL,
            f"session op {op} returned non-object",
            details={},
        )
    return _plugin_result_or_raise(result, op)


def _default_session_send(command_type: str, params: dict[str, Any]) -> dict[str, Any]:
    """CLI/MCP default: authenticated TCP via gimp_agent.probe."""
    from gimp_agent import probe as probe_mod

    return probe_mod.send_authenticated_command(command_type, params)


def _run_recipe_headless(
    *,
    recipe_id: str,
    recipe: dict[str, Any],
    steps: list[Any],
    context: dict[str, Any],
    delete_on_fail: bool,
    current_handle: dict[str, Any] | None = None,
    session_failure: BaseException | None = None,
) -> dict[str, Any]:
    """Execute batch_safe recipe via BatchProcedure job + host HOST_OPS (0019).

    Path-based jobs only — ``current_handle`` is unused (no cross-process handles).
    """
    from gimp_agent import batch as batch_mod

    _ = current_handle  # session handles do not cross into headless jobs
    batch_safe = bool(recipe.get("batch_safe", False))
    if not batch_safe:
        detail = f": {session_failure}" if session_failure else ""
        raise sec.GimpMcpError(
            sec.CODE_CONNECTION_FAILED,
            (f"recipe {recipe_id!r} is not batch_safe; headless unavailable{detail}"),
            details={"recipe_id": recipe_id},
        )

    if not batch_mod.headless_eligible(recipe):
        raise sec.GimpMcpError(
            sec.CODE_UNSUPPORTED,
            (
                f"recipe {recipe_id!r} headless requires contiguous GIMP_OPS before "
                f"HOST_OPS (interleaved steps need a live session backend)"
            ),
            details={"recipe_id": recipe_id, "backend": "headless"},
        )

    available, reason = batch_mod.headless_runtime_available()
    if not available:
        raise sec.GimpMcpError(
            sec.CODE_UNSUPPORTED,
            (f"recipe {recipe_id!r} is batch_safe but headless runtime unavailable ({reason})"),
            details={"recipe_id": recipe_id, "reason": reason},
        )

    # Split contiguous GIMP then HOST (eligibility already checked).
    # GIMP steps: interpolate now. HOST steps: keep raw templates; interpolate after job.
    gimp_steps: list[dict[str, Any]] = []
    host_step_templates: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        op = str(step.get("op"))
        with_raw = step.get("with") or {}
        if not isinstance(with_raw, dict):
            with_raw = {}
        if op in GIMP_OPS:
            interpolated = interpolate(with_raw, context)
            if not isinstance(interpolated, dict):
                raise sec.GimpMcpError(
                    sec.CODE_INTERNAL,
                    f"step with must interpolate to object for op {op}",
                    details={"op": op},
                )
            step_params = _jail_paths_in_mapping(interpolated)
            gimp_steps.append({"op": op, "with": step_params})
        elif op in HOST_OPS:
            host_step_templates.append({"op": op, "with": dict(with_raw)})
        else:
            raise sec.GimpMcpError(
                sec.CODE_UNSUPPORTED,
                f"op not allowlisted at runtime: {op}",
                details={"op": op},
            )

    created_paths: list[str] = []
    step_logs: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    run_ok = True
    fail_error: sec.GimpMcpError | None = None

    # Snapshot existence for export targets before job runs.
    existed_before: dict[str, bool] = {}
    for gs in gimp_steps:
        with_map = gs.get("with") or {}
        if isinstance(with_map, dict):
            for key in _PATH_KEYS:
                p = with_map.get(key)
                if isinstance(p, str) and p:
                    existed_before[p] = Path(p).exists()

    try:
        job = batch_mod.build_job_from_recipe_steps(recipe_id, gimp_steps)
        batch_result = batch_mod.run_headless_job(job)
        # Merge GIMP step logs from result when present
        remote_steps = batch_result.get("steps")
        if isinstance(remote_steps, list) and remote_steps:
            for item in remote_steps:
                if isinstance(item, dict):
                    step_logs.append(
                        {
                            "op": item.get("op"),
                            "ok": bool(item.get("ok", True)),
                            "result": item.get("result") or {},
                        }
                    )
                    # Rebind output_path from export results
                    res = item.get("result")
                    if item.get("op") in ("export_image", "save_xcf") and isinstance(res, dict):
                        result_path = res.get("file_path")
                        if isinstance(result_path, str) and result_path:
                            jailed_resolved = _jail_path(result_path, "output_path")
                            assert jailed_resolved is not None
                            context["output_path"] = jailed_resolved
                            if (
                                Path(jailed_resolved).exists()
                                and not existed_before.get(jailed_resolved, False)
                                and jailed_resolved not in created_paths
                            ):
                                created_paths.append(jailed_resolved)
                                artifacts.append({"path": jailed_resolved, "role": "output"})
        else:
            for gs in gimp_steps:
                step_logs.append({"op": gs["op"], "ok": True, "result": {}})
                with_map = gs.get("with") or {}
                if gs["op"] in ("export_image", "save_xcf") and isinstance(with_map, dict):
                    fp = with_map.get("file_path")
                    if isinstance(fp, str) and fp and Path(fp).exists():
                        if not existed_before.get(fp, False) and fp not in created_paths:
                            created_paths.append(fp)
                            artifacts.append({"path": fp, "role": "output"})
                        context["output_path"] = fp

        # HOST_OPS on host after GIMP job (re-interpolate with export rebinds)
        for hs in host_step_templates:
            op = str(hs["op"])
            with_raw = hs.get("with") or {}
            interpolated = interpolate(with_raw, context)
            if not isinstance(interpolated, dict):
                raise sec.GimpMcpError(
                    sec.CODE_INTERNAL,
                    f"step with must interpolate to object for op {op}",
                    details={"op": op},
                )
            step_params = _jail_paths_in_mapping(interpolated)
            try:
                result = _run_host_op(op, step_params)
            except (sec.GimpMcpError, sec.SecurityError) as exc:
                run_ok = False
                if isinstance(exc, sec.SecurityError):
                    fail_error = sec.GimpMcpError(exc.code, exc.message)
                else:
                    fail_error = exc
                step_logs.append(
                    {
                        "op": op,
                        "ok": False,
                        "error": {
                            "code": fail_error.code,
                            "message": fail_error.message,
                            "details": fail_error.details,
                        },
                    }
                )
                break
            step_logs.append({"op": op, "ok": True, "result": result})
            if op == "compare_images" and isinstance(result, dict):
                diff_p = result.get("diff_path")
                if isinstance(diff_p, str) and diff_p and Path(diff_p).exists():
                    if diff_p not in created_paths:
                        created_paths.append(diff_p)
                        artifacts.append({"path": diff_p, "role": "diff"})
    except (sec.GimpMcpError, sec.SecurityError) as exc:
        run_ok = False
        if isinstance(exc, sec.SecurityError):
            fail_error = sec.GimpMcpError(exc.code, exc.message)
        else:
            fail_error = exc
        step_logs.append(
            {
                "op": "headless_job",
                "ok": False,
                "error": {
                    "code": fail_error.code,
                    "message": fail_error.message,
                    "details": fail_error.details,
                },
            }
        )
    except Exception as exc:
        run_ok = False
        fail_error = sec.GimpMcpError(
            sec.CODE_INTERNAL,
            f"headless recipe failed: {exc}",
            details={"recipe_id": recipe_id},
        )
        step_logs.append(
            {
                "op": "headless_job",
                "ok": False,
                "error": {
                    "code": fail_error.code,
                    "message": fail_error.message,
                },
            }
        )

    if not run_ok and delete_on_fail:
        for path in list(created_paths):
            try:
                p = Path(path)
                if p.is_file():
                    p.unlink()
            except OSError as exc:
                logger.warning("rollback unlink failed for %s: %s", path, exc)

    log: dict[str, Any] = {
        "ok": run_ok,
        "recipe_id": recipe["id"],
        "version": recipe["version"],
        "backend": "headless",
        "steps": step_logs,
        "artifacts": artifacts,
        "created_paths": created_paths,
    }
    if not run_ok and fail_error is not None:
        log["error"] = {
            "code": fail_error.code,
            "message": fail_error.message,
            "details": fail_error.details,
        }
        raise sec.GimpMcpError(
            fail_error.code,
            fail_error.message,
            details={**(fail_error.details or {}), "mutation_log": log},
        )
    return log


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_recipe(
    recipe_id: str,
    *,
    version: str | None = None,
    params: Mapping[str, Any] | None = None,
    input_path: str | None = None,
    output_path: str | None = None,
    handle: dict[str, Any] | None = None,
    session_send: SessionSend | None = None,
    registry: RecipeRegistry | None = None,
    backend: str = "auto",
) -> dict[str, Any]:
    """Run a recipe; return mutation log.

    ``session_send`` is injectable for tests (``(command_type, params) -> result``).
    When omitted and the recipe ``requires_gimp``, uses authenticated TCP probe.

    ``backend``: ``auto`` (session then headless), ``session``, or ``headless``.
    Headless uses constrained BatchProcedure jobs for contiguous GIMP_OPS (0019).

    Reserved names ``input_path``, ``output_path``, and ``handle`` must be set only
    via the dedicated kwargs (or CLI/MCP flags) — not inside ``params``.
    """
    reg = registry if registry is not None else load_package_recipes()
    recipe = reg.get(recipe_id, version)

    requires_gimp = bool(recipe.get("requires_gimp", True))
    requires_open = bool(recipe.get("requires_open_session", False))
    batch_safe = bool(recipe.get("batch_safe", False))
    backend_pref = str(backend or "auto").strip().lower()
    if backend_pref not in ("auto", "session", "headless"):
        raise sec.GimpMcpError(
            sec.CODE_POLICY_DENIED,
            f"backend must be auto|session|headless, got {backend!r}",
            details={"backend": backend},
        )
    # Resolved after path selection: session | headless | host
    backend_used: str = "session" if requires_gimp else "host"

    # Reserved I/O binding names come only from dedicated kwargs / CLI flags.
    user_params: dict[str, Any] = dict(params or {})
    for key in user_params:
        if key in RESERVED_PARAM_NAMES:
            raise sec.GimpMcpError(
                sec.CODE_POLICY_DENIED,
                (
                    f"reserved parameter {key!r} must be set via dedicated argument "
                    f"(input_path/output_path/handle), not params"
                ),
                details={"param": key, "recipe_id": recipe_id},
            )

    # handle XOR input_path (v1: error if both) — kwargs only; params cannot reintroduce
    if handle is not None and input_path is not None:
        raise sec.GimpMcpError(
            sec.CODE_POLICY_DENIED,
            "provide handle or input_path, not both",
            details={"recipe_id": recipe_id},
        )
    if requires_open and handle is None:
        raise sec.GimpMcpError(
            sec.CODE_POLICY_DENIED,
            f"recipe {recipe_id!r} requires_open_session: handle is required",
            details={"recipe_id": recipe_id},
        )
    if requires_open and input_path is not None:
        raise sec.GimpMcpError(
            sec.CODE_POLICY_DENIED,
            f"recipe {recipe_id!r} requires_open_session: do not pass input_path",
            details={"recipe_id": recipe_id},
        )

    # Merge reserved kwargs + user params (reserved never from params)
    merged_raw: dict[str, Any] = dict(user_params)
    if input_path is not None:
        merged_raw["input_path"] = input_path
    if output_path is not None:
        merged_raw["output_path"] = output_path
    if handle is not None:
        merged_raw["handle"] = handle

    # Defense in depth: effective binding after merge must still satisfy XOR
    effective_handle = merged_raw.get("handle")
    effective_input = merged_raw.get("input_path")
    if effective_handle is not None and effective_input is not None:
        raise sec.GimpMcpError(
            sec.CODE_POLICY_DENIED,
            "provide handle or input_path, not both",
            details={"recipe_id": recipe_id},
        )

    checked = apply_defaults_and_check_params(recipe, merged_raw)

    # Build interpolation context (include reserved after jail for paths)
    context: dict[str, Any] = dict(checked)
    if "input_path" in context and context["input_path"] is not None:
        context["input_path"] = _jail_path(context["input_path"], "input_path")
    if "output_path" in context and context["output_path"] is not None:
        context["output_path"] = _jail_path(context["output_path"], "output_path")
    # Jail schema path params
    schema = recipe.get("parameters") or {}
    if isinstance(schema, dict):
        for pname, pspec in schema.items():
            if (
                isinstance(pspec, dict)
                and pspec.get("type") == "path"
                and context.get(pname) is not None
            ):
                context[pname] = _jail_path(context[pname], pname)
    if handle is not None:
        context["handle"] = handle

    # Recipes that need input_path (open_image style) without open session
    if (
        not requires_open
        and requires_gimp
        and handle is None
        and context.get("input_path") is None
        and any(
            isinstance(s, dict) and s.get("op") == "open_image" for s in (recipe.get("steps") or [])
        )
    ):
        raise sec.GimpMcpError(
            sec.CODE_POLICY_DENIED,
            f"recipe {recipe_id!r} requires input_path (or handle)",
            details={"recipe_id": recipe_id},
        )

    send: SessionSend | None = session_send
    if requires_gimp and send is None and backend_pref != "headless":
        send = _default_session_send

    rollback_cfg = recipe.get("rollback") or {}
    delete_on_fail = bool(
        rollback_cfg.get("delete_outputs_on_fail", False)
        if isinstance(rollback_cfg, dict)
        else False
    )

    created_paths: list[str] = []
    step_logs: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    current_handle: dict[str, Any] | None = handle if isinstance(handle, dict) else None
    run_ok = True
    fail_error: sec.GimpMcpError | None = None

    steps = recipe.get("steps") or []

    # Explicit headless: run GIMP_OPS via BatchProcedure then HOST_OPS on host.
    if requires_gimp and backend_pref == "headless":
        return _run_recipe_headless(
            recipe_id=recipe_id,
            recipe=recipe,
            steps=steps if isinstance(steps, list) else [],
            context=context,
            delete_on_fail=delete_on_fail,
            current_handle=current_handle,
        )

    for step in steps:
        if not isinstance(step, dict):
            continue
        op = str(step.get("op"))
        with_raw = step.get("with") or {}
        if not isinstance(with_raw, dict):
            with_raw = {}
        try:
            interpolated = interpolate(with_raw, context)
            if not isinstance(interpolated, dict):
                raise sec.GimpMcpError(
                    sec.CODE_INTERNAL,
                    f"step with must interpolate to object for op {op}",
                    details={"op": op},
                )
            # Jail path keys at use site
            step_params = _jail_paths_in_mapping(interpolated)
            # Nested expected.path etc. already top-level jailed via path key

            existed_before: dict[str, bool] = {}
            for key in _PATH_KEYS:
                p = step_params.get(key)
                if isinstance(p, str) and p:
                    existed_before[p] = Path(p).exists()

            if op in HOST_OPS:
                result = _run_host_op(op, step_params)
            elif op in GIMP_OPS:
                if not requires_gimp or send is None:
                    raise sec.GimpMcpError(
                        sec.CODE_INTERNAL,
                        f"GIMP op {op} but recipe is host-only",
                        details={"op": op},
                    )
                try:
                    result = _run_session_op(
                        op,
                        step_params,
                        session_send=send,
                        current_handle=current_handle,
                    )
                except (OSError, TimeoutError, ConnectionError, RuntimeError) as exc:
                    # auto: headless fallback for batch_safe contiguous recipes (0019)
                    if backend_pref == "auto" and batch_safe:
                        return _run_recipe_headless(
                            recipe_id=recipe_id,
                            recipe=recipe,
                            steps=steps if isinstance(steps, list) else [],
                            context=context,
                            delete_on_fail=delete_on_fail,
                            current_handle=handle if isinstance(handle, dict) else None,
                            session_failure=exc,
                        )
                    if backend_pref == "session" and batch_safe:
                        raise sec.GimpMcpError(
                            sec.CODE_CONNECTION_FAILED,
                            f"plugin connection failed for op {op}: {exc}",
                            details={"op": op, "backend": "session"},
                        ) from exc
                    if batch_safe:
                        # session-only legacy path shouldn't hit, but honest fail
                        raise sec.GimpMcpError(
                            sec.CODE_CONNECTION_FAILED,
                            f"plugin connection failed for op {op}: {exc}",
                            details={"op": op},
                        ) from exc
                    raise sec.GimpMcpError(
                        sec.CODE_CONNECTION_FAILED,
                        f"plugin connection failed for op {op}: {exc}",
                        details={"op": op},
                    ) from exc
                # Track handle from open_image
                if op == "open_image" and isinstance(result.get("handle"), dict):
                    current_handle = result["handle"]
                    context["handle"] = current_handle
            else:
                raise sec.GimpMcpError(
                    sec.CODE_UNSUPPORTED,
                    f"op not allowlisted at runtime: {op}",
                    details={"op": op},
                )

            # created_paths: only files newly written this run (never pre-existing replace targets).
            # Rebind $output_path when plugin returns a resolved path (collision=version → out-1.png).
            if op in ("export_image", "save_xcf") and isinstance(result, dict):
                result_path = result.get("file_path")
                if isinstance(result_path, str) and result_path:
                    # Always rebind when plugin reports a concrete path so later
                    # verify_artifact / steps see the file that was actually written.
                    jailed_resolved = _jail_path(result_path, "output_path")
                    assert jailed_resolved is not None
                    context["output_path"] = jailed_resolved
                    resolved = jailed_resolved
                else:
                    fallback = step_params.get("file_path")
                    resolved = fallback if isinstance(fallback, str) and fallback else None

                if isinstance(resolved, str) and resolved and Path(resolved).exists():
                    pre_existed = existed_before.get(resolved, False)
                    if not pre_existed and resolved not in created_paths:
                        created_paths.append(resolved)
                        artifacts.append({"path": resolved, "role": "output"})

            if op == "compare_images" and isinstance(result, dict):
                diff_p = result.get("diff_path")
                if isinstance(diff_p, str) and diff_p and Path(diff_p).exists():
                    if not existed_before.get(diff_p, False) and diff_p not in created_paths:
                        created_paths.append(diff_p)
                        artifacts.append({"path": diff_p, "role": "diff"})

            step_logs.append({"op": op, "ok": True, "result": result})
        except (sec.GimpMcpError, sec.SecurityError) as exc:
            run_ok = False
            if isinstance(exc, sec.SecurityError):
                fail_error = sec.GimpMcpError(exc.code, exc.message)
            else:
                fail_error = exc
            step_logs.append(
                {
                    "op": op,
                    "ok": False,
                    "error": {
                        "code": fail_error.code,
                        "message": fail_error.message,
                        "details": fail_error.details,
                    },
                }
            )
            break
        except Exception as exc:
            run_ok = False
            fail_error = sec.GimpMcpError(
                sec.CODE_INTERNAL,
                f"recipe step {op} failed: {exc}",
                details={"op": op},
            )
            step_logs.append(
                {
                    "op": op,
                    "ok": False,
                    "error": {
                        "code": fail_error.code,
                        "message": fail_error.message,
                    },
                }
            )
            break

    if not run_ok and delete_on_fail:
        for path in list(created_paths):
            try:
                p = Path(path)
                if p.is_file():
                    p.unlink()
            except OSError as exc:
                logger.warning("rollback unlink failed for %s: %s", path, exc)

    log: dict[str, Any] = {
        "ok": run_ok,
        "recipe_id": recipe["id"],
        "version": recipe["version"],
        "backend": backend_used,
        "steps": step_logs,
        "artifacts": artifacts,
        "created_paths": created_paths,
    }
    if not run_ok and fail_error is not None:
        log["error"] = {
            "code": fail_error.code,
            "message": fail_error.message,
            "details": fail_error.details,
        }
        # Re-raise so MCP/CLI can map exit codes; caller may catch
        raise sec.GimpMcpError(
            fail_error.code,
            fail_error.message,
            details={
                **(fail_error.details or {}),
                "mutation_log": log,
            },
        )
    return log
