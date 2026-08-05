"""Host-only subject isolation via optional rembg (track 0032).

Not a GIMP plug-in module — never copied to APPDATA. Requires optional extra:
``uv sync --extra subject`` (rembg[cpu]). Default install stays free of rembg.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import gimp_mcp_security as sec

# Module-level session cache keyed by model name (avoid reloading ~176MB u2net).
_SESSION_CACHE: dict[str, Any] = {}

_INSTALL_HINT = "uv sync --extra subject"
_DOWNLOAD_HINT = (
    "Model download/runtime failed. Check network, disk space, and "
    "U2NET_HOME (default ~/.u2net). Retry after install: "
    f"{_INSTALL_HINT}"
)


def rembg_available() -> bool:
    """Return True when rembg can be imported (optional extra present)."""
    try:
        import rembg  # noqa: F401  # pyright: ignore[reportMissingImports]
    except Exception:
        return False
    return True


def _get_session(model: str) -> Any:
    """Return a cached rembg session for ``model`` (create on first use)."""
    if model not in _SESSION_CACHE:
        from rembg import new_session  # pyright: ignore[reportMissingImports]

        _SESSION_CACHE[model] = new_session(model)
    return _SESSION_CACHE[model]


def _resolve_workspace_root(workspace_root: str | Path | None) -> Path | None:
    if workspace_root is not None and str(workspace_root).strip():
        return Path(workspace_root)
    return None


def isolate_subject(
    input_path: str | Path,
    output_path: str | Path,
    *,
    model: str = "u2net",
    alpha_matting: bool = False,
    session: Any | None = None,
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    """Isolate subject from background via rembg; write alpha PNG.

    Both paths are workspace-jailed. Missing rembg or model/runtime failures
    raise ``GimpMcpError(CODE_UNSUPPORTED)`` (never bare success).

    Parameters:
        input_path: Workspace-jailed source image path.
        output_path: Workspace-jailed destination path (always written as PNG).
        model: rembg model name (default ``u2net``).
        alpha_matting: Soft-edge matting (slower; default False).
        session: Optional rembg session override (tests / caller-owned).
        workspace_root: Optional jail root; defaults to ``GIMP_WORKSPACE_ROOT``.

    Returns:
        Result dict with jailed paths, model, and byte size.
    """
    root = _resolve_workspace_root(workspace_root)
    try:
        in_resolved = sec.resolve_under_root(str(input_path), root)
        out_resolved = sec.resolve_under_root(str(output_path), root)
    except sec.SecurityError:
        raise

    if not rembg_available():
        raise sec.GimpMcpError(
            sec.CODE_UNSUPPORTED,
            f"rembg not installed; install optional extra: {_INSTALL_HINT}",
            details={
                "input_path": str(in_resolved),
                "output_path": str(out_resolved),
                "extra": "subject",
            },
        )

    if not in_resolved.is_file():
        raise sec.GimpMcpError(
            sec.CODE_UNSUPPORTED,
            f"input image not found: {in_resolved}",
            details={"input_path": str(in_resolved)},
        )

    model_name = str(model or "u2net").strip() or "u2net"

    try:
        data = in_resolved.read_bytes()
    except OSError as exc:
        raise sec.GimpMcpError(
            sec.CODE_UNSUPPORTED,
            f"failed to read input: {exc}",
            details={"input_path": str(in_resolved)},
        ) from exc

    try:
        from rembg import remove  # pyright: ignore[reportMissingImports]

        sess = session if session is not None else _get_session(model_name)
        result = remove(
            data,
            session=sess,
            alpha_matting=bool(alpha_matting),
            force_return_bytes=True,
        )
    except sec.GimpMcpError:
        raise
    except Exception as exc:
        # rembg / pooch / onnxruntime / network download failures
        raise sec.GimpMcpError(
            sec.CODE_UNSUPPORTED,
            f"{_DOWNLOAD_HINT} ({type(exc).__name__}: {exc})",
            details={
                "input_path": str(in_resolved),
                "output_path": str(out_resolved),
                "model": model_name,
                "error_type": type(exc).__name__,
            },
        ) from exc

    if not isinstance(result, (bytes, bytearray)):
        # Fallback: coerce PIL-like objects if force_return_bytes ignored
        try:
            import io

            buf = io.BytesIO()
            if hasattr(result, "save"):
                result.save(buf, format="PNG")
                result = buf.getvalue()
            else:
                raise TypeError(f"unexpected rembg result type: {type(result)!r}")
        except Exception as exc:
            raise sec.GimpMcpError(
                sec.CODE_UNSUPPORTED,
                f"rembg did not return PNG bytes: {exc}",
                details={"model": model_name},
            ) from exc

    out_bytes = bytes(result)
    # Ensure parent exists under jail
    try:
        out_resolved.parent.mkdir(parents=True, exist_ok=True)
        out_resolved.write_bytes(out_bytes)
    except OSError as exc:
        raise sec.GimpMcpError(
            sec.CODE_UNSUPPORTED,
            f"failed to write output PNG: {exc}",
            details={"output_path": str(out_resolved)},
        ) from exc

    return {
        "input_path": str(in_resolved),
        "output_path": str(out_resolved),
        "model": model_name,
        "alpha_matting": bool(alpha_matting),
        "bytes_written": len(out_bytes),
        "format": "png",
    }


def clear_session_cache() -> None:
    """Drop cached rembg sessions (tests / explicit reload)."""
    _SESSION_CACHE.clear()
