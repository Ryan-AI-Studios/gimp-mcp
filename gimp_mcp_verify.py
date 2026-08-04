"""Host-only pure PNG pixel verification (track 0014).

Stdlib-only 8-bit non-interlaced PNG decoder (defilter types 0-4 including Paeth),
MAE / max_ae / changed-pixel stats / alpha counts / global luminance SSIM,
threshold gates, refine-loop stop helper, grayscale diff heatmap, and optional
ImageMagick compare companion.

**Not** a GIMP plug-in ship file — packaged with the stdio MCP server (py-modules).
No Pillow / numpy / scikit-image.
"""

from __future__ import annotations

import hashlib
import os
import struct
import subprocess
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gimp_mcp_security as sec
import gimp_mcp_snapshot as snap

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TRUSTED_MAX_PIXELS = 50_000_000
DEFAULT_UNTRUSTED_MAX_PIXELS = 25_000_000
DEFAULT_MAX_VERIFY_FILE_BYTES = 500 * 1024 * 1024  # 500 MiB
SSIM_AUTO_MAX_PIXELS = 1_000_000
SSIM_C1 = (0.01 * 255) ** 2
SSIM_C2 = (0.03 * 255) ** 2

ENV_MAX_DECODED_PIXELS = "GIMP_MCP_MAX_DECODED_PIXELS"
ENV_UNTRUSTED_IMAGES = "GIMP_MCP_UNTRUSTED_IMAGES"
ENV_MAX_VERIFY_FILE_BYTES = "GIMP_MCP_MAX_VERIFY_FILE_BYTES"

# Color types we decode (PNG §11.2.2); reject palette (3).
_SUPPORTED_COLOR_TYPES = frozenset({0, 2, 4, 6})
_CHANNELS_BY_COLOR_TYPE: dict[int, int] = {0: 1, 2: 3, 4: 2, 6: 4}
_MODE_BY_COLOR_TYPE: dict[int, str] = {0: "L", 2: "RGB", 4: "LA", 6: "RGBA"}

PNG_SIGNATURE = snap.PNG_SIGNATURE

# Truthy set aligned with gimp_mcp_security._env_truthy
_TRUTHY = frozenset({"1", "true", "yes", "on"})


# ---------------------------------------------------------------------------
# Public result / decision types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoadedPng:
    """Decoded 8-bit PNG raster."""

    width: int
    height: int
    color_type: int
    mode: str  # L | LA | RGB | RGBA
    channels: int
    pixels: bytes  # row-major, no filter bytes, length = w*h*channels


@dataclass(frozen=True)
class RefineDecision:
    """Pure refine-loop stop decision (skills / agent policy; not an MCP auto-loop)."""

    stop: bool
    reason: str
    loops: int


# ---------------------------------------------------------------------------
# Env / budgets
# ---------------------------------------------------------------------------


def _env_truthy_map(environ: Mapping[str, str], name: str) -> bool:
    raw = environ.get(name, "")
    if raw is None:
        return False
    return str(raw).strip().lower() in _TRUTHY


def max_decoded_pixels(environ: Mapping[str, str] | None = None) -> int:
    """Resolved decoded-pixel budget (memory guard, not path jail)."""
    env = environ if environ is not None else os.environ
    raw = env.get(ENV_MAX_DECODED_PIXELS)
    if raw is not None and str(raw).strip() != "":
        try:
            val = int(str(raw).strip())
            if val < 1:
                raise ValueError("budget must be positive")
            return val
        except ValueError as exc:
            raise sec.GimpMcpError(
                sec.CODE_POLICY_DENIED,
                f"invalid {ENV_MAX_DECODED_PIXELS}={raw!r}: must be a positive int",
            ) from exc
    if _env_truthy_map(env, ENV_UNTRUSTED_IMAGES):
        return DEFAULT_UNTRUSTED_MAX_PIXELS
    return DEFAULT_TRUSTED_MAX_PIXELS


def max_verify_file_bytes(environ: Mapping[str, str] | None = None) -> int:
    """Max on-disk file size before read (default 500 MiB)."""
    env = environ if environ is not None else os.environ
    raw = env.get(ENV_MAX_VERIFY_FILE_BYTES)
    if raw is not None and str(raw).strip() != "":
        try:
            val = int(str(raw).strip())
            if val < 1:
                raise ValueError("limit must be positive")
            return val
        except ValueError as exc:
            raise sec.GimpMcpError(
                sec.CODE_POLICY_DENIED,
                f"invalid {ENV_MAX_VERIFY_FILE_BYTES}={raw!r}: must be a positive int",
            ) from exc
    return DEFAULT_MAX_VERIFY_FILE_BYTES


def _check_file_size(path: Path, *, environ: Mapping[str, str] | None = None) -> int:
    limit = max_verify_file_bytes(environ)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise sec.GimpMcpError(
            sec.CODE_INTERNAL,
            f"cannot stat verify path {path}: {exc}",
        ) from exc
    if size > limit:
        raise sec.GimpMcpError(
            sec.CODE_POLICY_DENIED,
            f"verify file size {size} exceeds limit {limit} bytes ({ENV_MAX_VERIFY_FILE_BYTES})",
            details={"path": str(path), "size": size, "limit": limit},
        )
    return size


def _check_pixel_budget(
    width: int,
    height: int,
    *,
    environ: Mapping[str, str] | None = None,
) -> None:
    budget = max_decoded_pixels(environ)
    n = int(width) * int(height)
    if n > budget:
        raise sec.GimpMcpError(
            sec.CODE_POLICY_DENIED,
            f"decoded pixel count {n} ({width}x{height}) exceeds budget {budget} "
            f"({ENV_MAX_DECODED_PIXELS} / {ENV_UNTRUSTED_IMAGES})",
            details={
                "width": int(width),
                "height": int(height),
                "pixels": n,
                "budget": budget,
            },
        )


# ---------------------------------------------------------------------------
# PNG parse helpers
# ---------------------------------------------------------------------------


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _parse_ihdr_from_bytes(data: bytes) -> dict[str, int]:
    if not snap.validate_png_bytes(data):
        raise sec.GimpMcpError(sec.CODE_UNSUPPORTED, "Not a valid PNG (missing/invalid signature)")
    offset = 8
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        if data_end + 4 > len(data):
            raise sec.GimpMcpError(sec.CODE_UNSUPPORTED, "Truncated PNG chunk")
        if chunk_type == b"IHDR":
            if length != 13:
                raise sec.GimpMcpError(
                    sec.CODE_UNSUPPORTED, f"IHDR length must be 13, got {length}"
                )
            width, height, bit_depth, color_type, comp, filt, interlace = struct.unpack(
                ">IIBBBBB", data[data_start:data_end]
            )
            return {
                "width": int(width),
                "height": int(height),
                "bit_depth": int(bit_depth),
                "color_type": int(color_type),
                "compression": int(comp),
                "filter_method": int(filt),
                "interlace": int(interlace),
            }
        offset = data_end + 4
    raise sec.GimpMcpError(sec.CODE_UNSUPPORTED, "PNG missing IHDR chunk")


def _collect_idat(data: bytes) -> bytes:
    parts: list[bytes] = []
    offset = 8
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        if data_end + 4 > len(data):
            raise sec.GimpMcpError(sec.CODE_UNSUPPORTED, "Truncated PNG chunk while reading IDAT")
        if chunk_type == b"IDAT":
            parts.append(data[data_start:data_end])
        elif chunk_type == b"IEND":
            break
        offset = data_end + 4
    if not parts:
        raise sec.GimpMcpError(sec.CODE_UNSUPPORTED, "PNG missing IDAT")
    return b"".join(parts)


def paeth_predictor(a: int, b: int, c: int) -> int:
    """PNG §7.10 Paeth: a=left, b=up, c=up-left."""
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def defilter_scanlines(
    raw: bytes,
    *,
    width: int,
    height: int,
    channels: int,
) -> bytes:
    """Reconstruct filtered PNG scanlines (types 0-4) to raw pixel bytes.

    ``raw`` is zlib-decompressed IDAT: each scanline is 1 filter byte + row_bytes.
    """
    if width < 1 or height < 1:
        raise sec.GimpMcpError(sec.CODE_UNSUPPORTED, f"invalid PNG dimensions {width}x{height}")
    bpp = channels  # 8-bit only
    row_bytes = width * bpp
    expected = height * (1 + row_bytes)
    if len(raw) < expected:
        raise sec.GimpMcpError(
            sec.CODE_UNSUPPORTED,
            f"IDAT too short: need {expected} bytes after inflate, got {len(raw)}",
        )

    out = bytearray(height * row_bytes)
    prev = bytearray(row_bytes)  # zeros for first row "up"

    for y in range(height):
        row_off = y * (1 + row_bytes)
        filter_type = raw[row_off]
        src = raw[row_off + 1 : row_off + 1 + row_bytes]
        dst_off = y * row_bytes
        if filter_type == 0:  # None
            out[dst_off : dst_off + row_bytes] = src
        elif filter_type == 1:  # Sub
            for i in range(row_bytes):
                left = out[dst_off + i - bpp] if i >= bpp else 0
                out[dst_off + i] = (src[i] + left) & 0xFF
        elif filter_type == 2:  # Up
            for i in range(row_bytes):
                out[dst_off + i] = (src[i] + prev[i]) & 0xFF
        elif filter_type == 3:  # Average
            for i in range(row_bytes):
                left = out[dst_off + i - bpp] if i >= bpp else 0
                up = prev[i]
                out[dst_off + i] = (src[i] + ((left + up) // 2)) & 0xFF
        elif filter_type == 4:  # Paeth
            for i in range(row_bytes):
                left = out[dst_off + i - bpp] if i >= bpp else 0
                up = prev[i]
                up_left = prev[i - bpp] if i >= bpp else 0
                out[dst_off + i] = (src[i] + paeth_predictor(left, up, up_left)) & 0xFF
        else:
            raise sec.GimpMcpError(
                sec.CODE_UNSUPPORTED,
                f"unsupported PNG filter type {filter_type} (supported 0-4)",
            )
        prev = out[dst_off : dst_off + row_bytes]

    return bytes(out)


def load_png(
    path_or_bytes: str | Path | bytes,
    *,
    environ: Mapping[str, str] | None = None,
) -> LoadedPng:
    """Load 8-bit non-interlaced PNG color types 0/2/4/6 with full defilter.

    Rejects 16-bit, palette (3), interlaced → ``UNSUPPORTED``.
    Exceeded pixel/file budgets → ``POLICY_DENIED``.
    """
    if isinstance(path_or_bytes, (bytes, bytearray)):
        data = bytes(path_or_bytes)
    else:
        path = Path(path_or_bytes)
        _check_file_size(path, environ=environ)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise sec.GimpMcpError(sec.CODE_INTERNAL, f"cannot read PNG {path}: {exc}") from exc

    ihdr = _parse_ihdr_from_bytes(data)
    width = ihdr["width"]
    height = ihdr["height"]
    bit_depth = ihdr["bit_depth"]
    color_type = ihdr["color_type"]
    interlace = ihdr["interlace"]

    if bit_depth != 8:
        raise sec.GimpMcpError(
            sec.CODE_UNSUPPORTED,
            f"PNG bit_depth {bit_depth} unsupported (v1: 8-bit only)",
        )
    if color_type not in _SUPPORTED_COLOR_TYPES:
        raise sec.GimpMcpError(
            sec.CODE_UNSUPPORTED,
            f"PNG color_type {color_type} unsupported "
            f"(v1: 0/2/4/6 only; palette and others rejected)",
        )
    if interlace != 0:
        raise sec.GimpMcpError(
            sec.CODE_UNSUPPORTED,
            "interlaced PNG (Adam7) unsupported in v1 decoder",
        )
    if ihdr["compression"] != 0 or ihdr["filter_method"] != 0:
        raise sec.GimpMcpError(
            sec.CODE_UNSUPPORTED,
            "PNG compression/filter method must be 0",
        )

    _check_pixel_budget(width, height, environ=environ)

    channels = _CHANNELS_BY_COLOR_TYPE[color_type]
    mode = _MODE_BY_COLOR_TYPE[color_type]
    try:
        inflated = zlib.decompress(_collect_idat(data))
    except zlib.error as exc:
        raise sec.GimpMcpError(sec.CODE_UNSUPPORTED, f"PNG IDAT inflate failed: {exc}") from exc

    pixels = defilter_scanlines(inflated, width=width, height=height, channels=channels)
    return LoadedPng(
        width=width,
        height=height,
        color_type=color_type,
        mode=mode,
        channels=channels,
        pixels=pixels,
    )


def load_png_rgba8(
    path_or_bytes: str | Path | bytes,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[bytes, int, int, str]:
    """Convenience: return ``(pixels, width, height, mode)`` from :func:`load_png`."""
    img = load_png(path_or_bytes, environ=environ)
    return img.pixels, img.width, img.height, img.mode


# ---------------------------------------------------------------------------
# Mode promotion / channel compare rules (§2.9.2)
# ---------------------------------------------------------------------------


def _promote_to_rgb(pixels: bytes, mode: str, n_pixels: int) -> bytes:
    if mode == "RGB":
        return pixels
    if mode == "L":
        out = bytearray(n_pixels * 3)
        for i in range(n_pixels):
            g = pixels[i]
            j = i * 3
            out[j] = g
            out[j + 1] = g
            out[j + 2] = g
        return bytes(out)
    raise sec.GimpMcpError(
        sec.CODE_VERIFY_FAILED,
        f"cannot promote mode {mode!r} to RGB",
    )


def _promote_to_rgba(pixels: bytes, mode: str, n_pixels: int) -> bytes:
    if mode == "RGBA":
        return pixels
    if mode == "LA":
        out = bytearray(n_pixels * 4)
        for i in range(n_pixels):
            g = pixels[i * 2]
            a = pixels[i * 2 + 1]
            j = i * 4
            out[j] = g
            out[j + 1] = g
            out[j + 2] = g
            out[j + 3] = a
        return bytes(out)
    raise sec.GimpMcpError(
        sec.CODE_VERIFY_FAILED,
        f"cannot promote mode {mode!r} to RGBA",
    )


def _drop_alpha_rgb(pixels: bytes, mode: str, n_pixels: int) -> bytes:
    """Return RGB plane (3 channels) for RGB or RGBA."""
    if mode == "RGB":
        return pixels
    if mode == "RGBA":
        out = bytearray(n_pixels * 3)
        for i in range(n_pixels):
            s = i * 4
            d = i * 3
            out[d] = pixels[s]
            out[d + 1] = pixels[s + 1]
            out[d + 2] = pixels[s + 2]
        return bytes(out)
    raise sec.GimpMcpError(
        sec.CODE_VERIFY_FAILED,
        f"cannot drop alpha for mode {mode!r}",
    )


def _drop_alpha_gray(pixels: bytes, mode: str, n_pixels: int) -> bytes:
    if mode == "L":
        return pixels
    if mode == "LA":
        out = bytearray(n_pixels)
        for i in range(n_pixels):
            out[i] = pixels[i * 2]
        return bytes(out)
    raise sec.GimpMcpError(
        sec.CODE_VERIFY_FAILED,
        f"cannot drop alpha for mode {mode!r}",
    )


def resolve_compare_buffers(
    a: LoadedPng,
    b: LoadedPng,
    *,
    ignore_alpha: bool = False,
) -> tuple[bytes, bytes, int, str]:
    """Resolve channel modes per §2.9.2 → ``(buf_a, buf_b, channels, label)``."""
    ma, mb = a.mode, b.mode
    n = a.width * a.height
    if a.width != b.width or a.height != b.height:
        # Size handled by thresholds; still allow buffer resolve only when equal.
        raise sec.GimpMcpError(
            sec.CODE_VERIFY_FAILED,
            f"size mismatch for buffer resolve: {a.width}x{a.height} vs {b.width}x{b.height}",
        )

    if ma == mb:
        return a.pixels, b.pixels, a.channels, ma

    # RGB vs RGBA
    if {ma, mb} == {"RGB", "RGBA"}:
        if not ignore_alpha:
            raise sec.GimpMcpError(
                sec.CODE_VERIFY_FAILED,
                f"mode mismatch {ma} vs {mb} (set ignore_alpha=true to compare RGB only)",
            )
        return (
            _drop_alpha_rgb(a.pixels, ma, n),
            _drop_alpha_rgb(b.pixels, mb, n),
            3,
            "RGB",
        )

    # L vs LA
    if {ma, mb} == {"L", "LA"}:
        if not ignore_alpha:
            raise sec.GimpMcpError(
                sec.CODE_VERIFY_FAILED,
                f"mode mismatch {ma} vs {mb} (set ignore_alpha=true to compare L only)",
            )
        return (
            _drop_alpha_gray(a.pixels, ma, n),
            _drop_alpha_gray(b.pixels, mb, n),
            1,
            "L",
        )

    # Gray vs RGB → promote gray to RGB
    if {ma, mb} == {"L", "RGB"}:
        return (
            _promote_to_rgb(a.pixels, ma, n),
            _promote_to_rgb(b.pixels, mb, n),
            3,
            "RGB",
        )

    # Gray+A vs RGBA → promote to RGBA
    if {ma, mb} == {"LA", "RGBA"}:
        return (
            _promote_to_rgba(a.pixels, ma, n),
            _promote_to_rgba(b.pixels, mb, n),
            4,
            "RGBA",
        )

    raise sec.GimpMcpError(
        sec.CODE_VERIFY_FAILED,
        f"unsupported mode pair for compare: {ma} vs {mb}",
    )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _luminance_plane(pixels: bytes, mode: str, n_pixels: int) -> list[float]:
    """Per-pixel luminance Y = 0.299R+0.587G+0.114B (or gray channel)."""
    y: list[float] = []
    if mode in ("L", "LA"):
        step = 1 if mode == "L" else 2
        for i in range(n_pixels):
            y.append(float(pixels[i * step]))
        return y
    if mode == "RGB":
        for i in range(n_pixels):
            o = i * 3
            y.append(0.299 * pixels[o] + 0.587 * pixels[o + 1] + 0.114 * pixels[o + 2])
        return y
    if mode == "RGBA":
        for i in range(n_pixels):
            o = i * 4
            y.append(0.299 * pixels[o] + 0.587 * pixels[o + 1] + 0.114 * pixels[o + 2])
        return y
    # Promoted buffers always use L/RGB/RGBA labels from resolve
    raise sec.GimpMcpError(sec.CODE_INTERNAL, f"unknown mode for luminance: {mode}")


def global_ssim_luminance(
    pixels_a: bytes,
    pixels_b: bytes,
    *,
    mode: str,
    n_pixels: int,
) -> float:
    """Global (single-window) luminance SSIM in [0,1].

    **Honesty:** this is **not** ImageMagick windowed ``-metric SSIM``.
    Constants: C1=(0.01*255)^2, C2=(0.03*255)^2 (Wang et al.).
    """
    if n_pixels < 1:
        return 1.0
    xa = _luminance_plane(pixels_a, mode, n_pixels)
    xb = _luminance_plane(pixels_b, mode, n_pixels)
    n = float(n_pixels)
    mu_x = sum(xa) / n
    mu_y = sum(xb) / n
    var_x = 0.0
    var_y = 0.0
    cov = 0.0
    for i in range(n_pixels):
        dx = xa[i] - mu_x
        dy = xb[i] - mu_y
        var_x += dx * dx
        var_y += dy * dy
        cov += dx * dy
    var_x /= n
    var_y /= n
    cov /= n
    c1 = SSIM_C1
    c2 = SSIM_C2
    num = (2.0 * mu_x * mu_y + c1) * (2.0 * cov + c2)
    den = (mu_x * mu_x + mu_y * mu_y + c1) * (var_x + var_y + c2)
    if den == 0.0:
        return 1.0
    val = num / den
    # Clamp tiny float noise outside [0,1]
    if val < 0.0:
        return 0.0
    if val > 1.0:
        return 1.0
    return float(val)


def _alpha_transparent_count(img: LoadedPng) -> int | None:
    if img.mode == "RGBA":
        n = 0
        for i in range(img.width * img.height):
            if img.pixels[i * 4 + 3] == 0:
                n += 1
        return n
    if img.mode == "LA":
        n = 0
        for i in range(img.width * img.height):
            if img.pixels[i * 2 + 1] == 0:
                n += 1
        return n
    return None


def metrics_buffers(
    buf_a: bytes,
    buf_b: bytes,
    *,
    width: int,
    height: int,
    channels: int,
    change_threshold: int = 1,
    mode_label: str = "RGB",
    compute_ssim: bool | str = "auto",
    alpha_transparent_a: int | None = None,
    alpha_transparent_b: int | None = None,
) -> dict[str, Any]:
    """Compute MAE / max_ae / changed stats / optional global SSIM on equal-size buffers."""
    n_pixels = width * height
    if n_pixels < 1:
        raise sec.GimpMcpError(sec.CODE_UNSUPPORTED, "empty image")
    expected = n_pixels * channels
    if len(buf_a) != expected or len(buf_b) != expected:
        raise sec.GimpMcpError(
            sec.CODE_INTERNAL,
            f"buffer length mismatch: expected {expected}, got {len(buf_a)}/{len(buf_b)}",
        )

    thr = max(1, int(change_threshold))
    abs_sum = 0
    max_ae = 0
    changed = 0
    for i in range(n_pixels):
        base = i * channels
        pixel_changed = False
        for c in range(channels):
            d = abs(buf_a[base + c] - buf_b[base + c])
            abs_sum += d
            if d > max_ae:
                max_ae = d
            if d >= thr:
                pixel_changed = True
        if pixel_changed:
            changed += 1

    total_samples = n_pixels * channels
    mae = float(abs_sum) / float(total_samples) if total_samples else 0.0
    changed_fraction = float(changed) / float(n_pixels)

    do_ssim = False
    if compute_ssim is True:
        do_ssim = True
    elif compute_ssim is False:
        do_ssim = False
    elif isinstance(compute_ssim, str) and compute_ssim.strip().lower() == "auto":
        do_ssim = n_pixels <= SSIM_AUTO_MAX_PIXELS
    else:
        raise sec.GimpMcpError(
            sec.CODE_INTERNAL,
            f"compute_ssim must be bool or 'auto', got {compute_ssim!r}",
        )

    ssim_val: float | None = None
    if do_ssim:
        # Luminance plane uses RGB/L semantics; for multi-channel labels use mode_label.
        ssim_mode = mode_label if mode_label in ("L", "LA", "RGB", "RGBA") else "RGB"
        if channels == 1:
            ssim_mode = "L"
        elif channels == 3:
            ssim_mode = "RGB"
        elif channels == 4:
            ssim_mode = "RGBA"
        elif channels == 2:
            ssim_mode = "LA"
        ssim_val = global_ssim_luminance(buf_a, buf_b, mode=ssim_mode, n_pixels=n_pixels)

    return {
        "width": width,
        "height": height,
        "channels": channels,
        "mae": mae,
        "max_ae": int(max_ae),
        "changed_pixels": int(changed),
        "changed_fraction": changed_fraction,
        "change_threshold": thr,
        "alpha_transparent_pixels_a": alpha_transparent_a,
        "alpha_transparent_pixels_b": alpha_transparent_b,
        "ssim": ssim_val,
        "ssim_computed": bool(do_ssim),
    }


# ---------------------------------------------------------------------------
# Thresholds / raise
# ---------------------------------------------------------------------------


def evaluate_thresholds(
    metrics: Mapping[str, Any],
    thresholds: Mapping[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    """Apply threshold gates; return ``(pass, failures)``.

    Defaults: require_same_size true; other numeric gates null/off;
    require_mutation false.
    """
    t = dict(thresholds or {})
    failures: list[str] = []

    require_same_size = t.get("require_same_size", True)
    if require_same_size is None:
        require_same_size = True
    if require_same_size and metrics.get("size_mismatch"):
        failures.append(
            f"size mismatch: {metrics.get('width_a')}x{metrics.get('height_a')} vs "
            f"{metrics.get('width_b')}x{metrics.get('height_b')}"
        )

    require_mutation = bool(t.get("require_mutation", False))
    min_changed = int(t.get("min_changed_pixels", 1) or 1)
    if require_mutation:
        changed = int(metrics.get("changed_pixels") or 0)
        if changed < min_changed:
            failures.append(
                f"require_mutation: changed_pixels={changed} < min_changed_pixels={min_changed}"
            )

    max_mae = t.get("max_mae")
    if max_mae is not None and metrics.get("mae") is not None:
        if float(metrics["mae"]) > float(max_mae):
            failures.append(f"mae {metrics['mae']} exceeds max_mae {max_mae}")

    max_max_ae = t.get("max_max_ae")
    if max_max_ae is not None and metrics.get("max_ae") is not None:
        if int(metrics["max_ae"]) > int(max_max_ae):
            failures.append(f"max_ae {metrics['max_ae']} exceeds max_max_ae {max_max_ae}")

    min_ssim = t.get("min_ssim")
    if min_ssim is not None:
        ssim = metrics.get("ssim")
        if ssim is None:
            failures.append("min_ssim set but ssim was not computed")
        elif float(ssim) < float(min_ssim):
            failures.append(f"ssim {ssim} below min_ssim {min_ssim}")

    max_changed_fraction = t.get("max_changed_fraction")
    if max_changed_fraction is not None and metrics.get("changed_fraction") is not None:
        if float(metrics["changed_fraction"]) > float(max_changed_fraction):
            failures.append(
                f"changed_fraction {metrics['changed_fraction']} exceeds "
                f"max_changed_fraction {max_changed_fraction}"
            )

    return (len(failures) == 0, failures)


def _maybe_raise_on_fail(
    report: dict[str, Any],
    *,
    raise_on_fail: bool,
    action: str,
) -> dict[str, Any]:
    if raise_on_fail and not report.get("pass", False):
        failures = report.get("failures") or []
        msg = f"{action} failed: " + (
            "; ".join(str(f) for f in failures) if failures else "pass=false"
        )
        raise sec.GimpMcpError(
            sec.CODE_VERIFY_FAILED,
            msg,
            details={"report": report},
        )
    return report


# ---------------------------------------------------------------------------
# Diff PNG (grayscale heatmap)
# ---------------------------------------------------------------------------


def write_png_gray8(path: str | Path, width: int, height: int, pixels: bytes) -> None:
    """Write 8-bit grayscale non-interlaced PNG (filter 0)."""
    if len(pixels) != width * height:
        raise sec.GimpMcpError(
            sec.CODE_INTERNAL,
            f"gray pixels length {len(pixels)} != {width}*{height}",
        )
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter None
        row = y * width
        raw.extend(pixels[row : row + width])
    compressed = zlib.compress(bytes(raw), 9)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    data = (
        PNG_SIGNATURE
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", compressed)
        + _png_chunk(b"IEND", b"")
    )
    Path(path).write_bytes(data)


def write_diff_png(
    path: str | Path,
    buf_a: bytes,
    buf_b: bytes,
    *,
    width: int,
    height: int,
    channels: int,
) -> str:
    """Write grayscale heatmap: pixel = max(|ΔR|,|ΔG|,|ΔB|) (ignore alpha in heatmap)."""
    n = width * height
    # For heatmap RGB channels only when channels >= 3; else use max over all compared.
    heat = bytearray(n)
    for i in range(n):
        base = i * channels
        if channels >= 3:
            # max of first 3 (RGB); ignore alpha even if present
            d = max(
                abs(buf_a[base] - buf_b[base]),
                abs(buf_a[base + 1] - buf_b[base + 1]),
                abs(buf_a[base + 2] - buf_b[base + 2]),
            )
        else:
            d = 0
            for c in range(channels):
                d = max(d, abs(buf_a[base + c] - buf_b[base + c]))
        heat[i] = d & 0xFF
    write_png_gray8(path, width, height, bytes(heat))
    return str(path)


# ---------------------------------------------------------------------------
# Refine helper
# ---------------------------------------------------------------------------


def refine_should_stop(
    history: Sequence[Mapping[str, Any]],
    *,
    max_loops: int = 3,
    min_improvement: float = 0.0,
    metric: str = "mae",
) -> RefineDecision:
    """Decide whether a refine loop should stop (pure; does not call edit tools).

    Stops when: ``loops >= max_loops`` | last snapshot ``pass`` | no improvement |
    regression on ``metric`` (default mae; lower is better).
    """
    loops = len(history)
    if loops <= 0:
        return RefineDecision(stop=False, reason="no_history", loops=0)
    if loops >= max_loops:
        return RefineDecision(stop=True, reason="max_loops", loops=loops)

    last = history[-1]
    if last.get("pass") is True:
        return RefineDecision(stop=True, reason="passed", loops=loops)

    if loops >= 2:
        prev = history[-2]
        try:
            cur_v = float(last[metric])
            prev_v = float(prev[metric])
        except (KeyError, TypeError, ValueError):
            return RefineDecision(stop=False, reason="metric_unavailable", loops=loops)
        # Lower is better for mae / max_ae / changed_*; for ssim higher is better.
        higher_better = metric in ("ssim",)
        if higher_better:
            improvement = cur_v - prev_v
            if cur_v < prev_v:
                return RefineDecision(stop=True, reason="regression", loops=loops)
            if improvement < min_improvement:
                return RefineDecision(stop=True, reason="no_improvement", loops=loops)
        else:
            improvement = prev_v - cur_v
            if cur_v > prev_v:
                return RefineDecision(stop=True, reason="regression", loops=loops)
            if improvement < min_improvement:
                return RefineDecision(stop=True, reason="no_improvement", loops=loops)

    return RefineDecision(stop=False, reason="continue", loops=loops)


# ---------------------------------------------------------------------------
# Optional ImageMagick
# ---------------------------------------------------------------------------


def imagemagick_compare(
    path_a: str | Path,
    path_b: str | Path,
    *,
    metric: str = "ae",
    timeout: float = 30.0,
) -> dict[str, Any] | None:
    """Optional companion: run ``magick compare`` or legacy ``compare``.

    Exit codes **0 and 1** both count as process success (1 = images differ).
    Returns None if neither binary is on PATH. Never uses ``shell=True``.
    """
    import shutil

    magick = shutil.which("magick")
    compare_bin = shutil.which("compare")
    if magick:
        cmd = [
            magick,
            "compare",
            "-metric",
            metric,
            str(path_a),
            str(path_b),
            "null:",
        ]
        backend = "magick"
    elif compare_bin:
        cmd = [
            compare_bin,
            "-metric",
            metric,
            str(path_a),
            str(path_b),
            "null:",
        ]
        backend = "compare"
    else:
        return None

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "backend": backend,
            "ok": False,
            "error": str(exc),
            "cmd": cmd,
        }

    # IM writes metric to stderr; exit 0 identical, 1 differ — both OK
    if proc.returncode not in (0, 1):
        return {
            "backend": backend,
            "ok": False,
            "returncode": proc.returncode,
            "stderr": (proc.stderr or "").strip(),
            "stdout": (proc.stdout or "").strip(),
            "cmd": cmd,
        }

    metric_text = (proc.stderr or proc.stdout or "").strip()
    return {
        "backend": backend,
        "ok": True,
        "returncode": proc.returncode,
        "metric": metric,
        "metric_value": metric_text,
        "cmd": cmd,
    }


# ---------------------------------------------------------------------------
# High-level compare / verify
# ---------------------------------------------------------------------------


def compare_images(
    path_a: str | Path,
    path_b: str | Path,
    *,
    thresholds: Mapping[str, Any] | None = None,
    write_diff_path: str | Path | None = None,
    raise_on_fail: bool = False,
    ignore_alpha: bool = False,
    compute_ssim: bool | str = "auto",
    change_threshold: int = 1,
    environ: Mapping[str, str] | None = None,
    try_imagemagick: bool = False,
) -> dict[str, Any]:
    """Compare two PNG paths; return metrics with ``ok: true`` and ``pass`` gate.

    Path jail is the caller's responsibility (MCP ``_jail_path_or_raise`` / CLI).
    Errors for unsupported/budget raise structured ``GimpMcpError``.
    """
    thr = dict(thresholds or {})
    if "ignore_alpha" in thr:
        ignore_alpha = bool(thr["ignore_alpha"])
    if "change_threshold" in thr:
        change_threshold = int(thr["change_threshold"])

    img_a = load_png(path_a, environ=environ)
    img_b = load_png(path_b, environ=environ)

    size_mismatch = img_a.width != img_b.width or img_a.height != img_b.height
    base_metrics: dict[str, Any] = {
        "ok": True,
        "backend": "stdlib",
        "width": img_a.width,
        "height": img_a.height,
        "width_a": img_a.width,
        "height_a": img_a.height,
        "width_b": img_b.width,
        "height_b": img_b.height,
        "size_mismatch": size_mismatch,
        "diff_path": None,
        "thresholds": thr,
        "mode_a": img_a.mode,
        "mode_b": img_b.mode,
    }

    if size_mismatch:
        base_metrics.update(
            {
                "channels": None,
                "mae": None,
                "max_ae": None,
                "changed_pixels": None,
                "changed_fraction": None,
                "change_threshold": int(change_threshold),
                "alpha_transparent_pixels_a": _alpha_transparent_count(img_a),
                "alpha_transparent_pixels_b": _alpha_transparent_count(img_b),
                "ssim": None,
                "ssim_computed": False,
            }
        )
        passed, failures = evaluate_thresholds(base_metrics, thr)
        base_metrics["pass"] = passed
        base_metrics["failures"] = failures
        if try_imagemagick:
            base_metrics["imagemagick"] = imagemagick_compare(path_a, path_b)
        return _maybe_raise_on_fail(
            base_metrics, raise_on_fail=raise_on_fail, action="compare_images"
        )

    try:
        buf_a, buf_b, channels, mode_label = resolve_compare_buffers(
            img_a, img_b, ignore_alpha=ignore_alpha
        )
    except sec.GimpMcpError as exc:
        if exc.code == sec.CODE_VERIFY_FAILED and not raise_on_fail:
            # Mode mismatch as soft fail when raise_on_fail=false
            base_metrics.update(
                {
                    "pass": False,
                    "failures": [exc.message],
                    "channels": None,
                    "mae": None,
                    "max_ae": None,
                    "changed_pixels": None,
                    "changed_fraction": None,
                    "change_threshold": int(change_threshold),
                    "alpha_transparent_pixels_a": _alpha_transparent_count(img_a),
                    "alpha_transparent_pixels_b": _alpha_transparent_count(img_b),
                    "ssim": None,
                    "ssim_computed": False,
                }
            )
            return base_metrics
        raise

    m = metrics_buffers(
        buf_a,
        buf_b,
        width=img_a.width,
        height=img_a.height,
        channels=channels,
        change_threshold=change_threshold,
        mode_label=mode_label,
        compute_ssim=compute_ssim,
        alpha_transparent_a=_alpha_transparent_count(img_a),
        alpha_transparent_b=_alpha_transparent_count(img_b),
    )
    base_metrics.update(m)
    base_metrics["compare_mode"] = mode_label

    diff_path_out: str | None = None
    if write_diff_path is not None:
        diff_path_out = write_diff_png(
            write_diff_path,
            buf_a,
            buf_b,
            width=img_a.width,
            height=img_a.height,
            channels=channels,
        )
    base_metrics["diff_path"] = diff_path_out

    passed, failures = evaluate_thresholds(base_metrics, thr)
    base_metrics["pass"] = passed
    base_metrics["failures"] = failures

    if try_imagemagick:
        im = imagemagick_compare(path_a, path_b)
        if im is not None:
            base_metrics["imagemagick"] = im

    return _maybe_raise_on_fail(base_metrics, raise_on_fail=raise_on_fail, action="compare_images")


def _detect_format_signature(data: bytes) -> str | None:
    if len(data) >= 8 and data[:8] == PNG_SIGNATURE:
        return "png"
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if len(data) >= 4 and data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return "webp"
    if len(data) >= 4 and data[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    return None


def verify_artifact(
    path: str | Path,
    expected: Mapping[str, Any],
    *,
    raise_on_fail: bool = False,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate one artifact against a typed expectation (signature-based format).

    Supported expected keys (v1):
    - min_width, max_width, width, height, min_height, max_height
    - format (``png`` only; other formats → ``UNSUPPORTED``)
    - require_alpha (bool | null)
    - sha256 (hex digest)
    - min_bytes, max_bytes

    Format detection is **signature-based**, not extension. ``ok`` is always true
    when the operation completes; ``pass`` is the threshold/spec gate.
    """
    p = Path(path)
    size = _check_file_size(p, environ=environ)
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise sec.GimpMcpError(sec.CODE_INTERNAL, f"cannot read artifact {p}: {exc}") from exc

    exp = dict(expected or {})
    want_format = exp.get("format")
    if want_format is not None:
        want_format = str(want_format).strip().lower()
        if want_format in ("jpg",):
            want_format = "jpeg"

    detected = _detect_format_signature(data)
    failures: list[str] = []

    # Non-PNG formats requested → UNSUPPORTED (raise always; not a soft pass=false)
    if want_format is not None and want_format != "png":
        raise sec.GimpMcpError(
            sec.CODE_UNSUPPORTED,
            f"verify_artifact format={want_format!r} unsupported in v1 (png only)",
            details={"format": want_format, "detected": detected},
        )

    if want_format == "png" or want_format is None:
        if detected != "png":
            # Spec: format=png but lacks signature → VERIFY_FAILED
            if want_format == "png":
                failures.append("file lacks PNG signature")
            elif detected is None:
                failures.append("unrecognized file signature")
            else:
                failures.append(f"detected format {detected!r}, expected png")

    report: dict[str, Any] = {
        "ok": True,
        "path": str(p),
        "bytes": size,
        "detected_format": detected,
        "expected": exp,
        "width": None,
        "height": None,
        "bit_depth": None,
        "color_type": None,
        "has_alpha": None,
        "sha256": None,
    }

    # Size gates (always)
    min_bytes = exp.get("min_bytes")
    if min_bytes is not None and size < int(min_bytes):
        failures.append(f"bytes {size} < min_bytes {min_bytes}")
    max_bytes = exp.get("max_bytes")
    if max_bytes is not None and size > int(max_bytes):
        failures.append(f"bytes {size} > max_bytes {max_bytes}")

    sha_want = exp.get("sha256")
    digest = hashlib.sha256(data).hexdigest()
    report["sha256"] = digest
    if sha_want is not None and str(sha_want).lower() != digest.lower():
        failures.append("sha256 mismatch")

    if detected == "png" and "file lacks PNG signature" not in failures:
        # Budget + IHDR (may decode fully if alpha needed — IHDR is enough for dims/alpha presence)
        try:
            ihdr = _parse_ihdr_from_bytes(data)
            _check_pixel_budget(ihdr["width"], ihdr["height"], environ=environ)
            report["width"] = ihdr["width"]
            report["height"] = ihdr["height"]
            report["bit_depth"] = ihdr["bit_depth"]
            report["color_type"] = ihdr["color_type"]
            report["has_alpha"] = ihdr["color_type"] in (4, 6)
        except sec.GimpMcpError as exc:
            if exc.code == sec.CODE_POLICY_DENIED:
                raise
            failures.append(exc.message)
        else:
            w = report["width"]
            h = report["height"]
            if exp.get("width") is not None and int(exp["width"]) != int(w):
                failures.append(f"width {w} != expected {exp['width']}")
            if exp.get("height") is not None and int(exp["height"]) != int(h):
                failures.append(f"height {h} != expected {exp['height']}")
            if exp.get("min_width") is not None and int(w) < int(exp["min_width"]):
                failures.append(f"width {w} < min_width {exp['min_width']}")
            if exp.get("max_width") is not None and int(w) > int(exp["max_width"]):
                failures.append(f"width {w} > max_width {exp['max_width']}")
            if exp.get("min_height") is not None and int(h) < int(exp["min_height"]):
                failures.append(f"height {h} < min_height {exp['min_height']}")
            if exp.get("max_height") is not None and int(h) > int(exp["max_height"]):
                failures.append(f"height {h} > max_height {exp['max_height']}")

            req_alpha = exp.get("require_alpha")
            if req_alpha is True and not report["has_alpha"]:
                failures.append("require_alpha=true but PNG has no alpha (color_type not 4/6)")
            if req_alpha is False and report["has_alpha"]:
                failures.append("require_alpha=false but PNG has alpha")

    report["pass"] = len(failures) == 0
    report["failures"] = failures
    return _maybe_raise_on_fail(report, raise_on_fail=raise_on_fail, action="verify_artifact")
