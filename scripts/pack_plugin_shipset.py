#!/usr/bin/env python3
"""Pack the EXPECTED 10-file GIMP plug-in ship set into a versioned zip.

Convenience archive for offline distribution. ``gimp-agent install`` remains
the source of truth for deploying into the GIMP user plug-ins directory.

Usage:
    uv run python scripts/pack_plugin_shipset.py
    uv run python scripts/pack_plugin_shipset.py --output output/gimp-mcp-plugin-0.1.0.zip

Includes MANIFEST.txt (filename + SHA-256 per member) inside the zip.
Exits non-zero if any expected source file is missing.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gimp_agent import __version__ as package_version  # noqa: E402
from gimp_agent.paths import EXPECTED_PLUGIN_FILES  # noqa: E402


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def pack_plugin_shipset(
    source_dir: Path,
    output_zip: Path,
    *,
    version: str,
) -> Path:
    """Write ship-set zip + in-archive MANIFEST.txt; return output path.

    Raises SystemExit with non-zero code if any EXPECTED file is missing.
    """
    source_dir = source_dir.resolve()
    missing = [name for name in EXPECTED_PLUGIN_FILES if not (source_dir / name).is_file()]
    if missing:
        print(
            f"ERROR: missing {len(missing)} expected plugin file(s) under {source_dir}:",
            file=sys.stderr,
        )
        for name in missing:
            print(f"  - {name}", file=sys.stderr)
        raise SystemExit(2)

    digests: list[tuple[str, str]] = []
    for name in EXPECTED_PLUGIN_FILES:
        digests.append((name, _sha256_file(source_dir / name)))

    manifest_lines = [
        f"# gimp-mcp plugin ship-set {version}",
        f"# source: {source_dir}",
        "# format: <filename>  <sha256>  (two spaces)",
        "",
    ]
    for name, digest in digests:
        manifest_lines.append(f"{name}  {digest}")
    manifest_text = "\n".join(manifest_lines) + "\n"

    output_zip = output_zip.resolve()
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    if output_zip.exists():
        output_zip.unlink()

    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in EXPECTED_PLUGIN_FILES:
            zf.write(source_dir / name, arcname=name)
        zf.writestr("MANIFEST.txt", manifest_text)

    # Sidecar checksum of the zip itself (optional integrity for operators).
    zip_digest = _sha256_file(output_zip)
    sidecar = Path(str(output_zip) + ".sha256")
    sidecar.write_text(f"{zip_digest}  {output_zip.name}\n", encoding="utf-8")

    print(f"wrote {output_zip} ({len(EXPECTED_PLUGIN_FILES)} files + MANIFEST.txt)")
    print(f"wrote {sidecar}")
    return output_zip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pack EXPECTED plugin ship-set zip")
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT,
        help="Directory containing EXPECTED_PLUGIN_FILES (default: repo root)",
    )
    parser.add_argument(
        "--version",
        default=package_version,
        help=f"Version string for archive name (default: {package_version})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output zip path (default: output/gimp-mcp-plugin-<version>.zip)",
    )
    args = parser.parse_args(argv)

    version = str(args.version)
    out = args.output
    if out is None:
        out = ROOT / "output" / f"gimp-mcp-plugin-{version}.zip"

    try:
        pack_plugin_shipset(args.source, out, version=version)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
