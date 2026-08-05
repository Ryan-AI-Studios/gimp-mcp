"""Release packaging structure tests (version triple, metadata, docs, exit, zip).

Offline by default. ``uv build`` verification is marked ``@pytest.mark.slow``
and is excluded from the default CI collection (``not slow``).
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import re
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

from gimp_agent import __version__ as package_version
from gimp_agent.paths import EXPECTED_PLUGIN_FILES

ROOT = Path(__file__).resolve().parents[1]

PLUGIN_VERSION_RE = re.compile(r'"plugin_version":\s*"(\d+\.\d+\.\d+)"')

REQUIRED_CLASSIFIERS = [
    "Development Status :: 4 - Beta",
    "Intended Audience :: Developers",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3 :: Only",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Operating System :: Microsoft :: Windows",
    "Operating System :: POSIX",
    "Topic :: Multimedia :: Graphics",
]

REQUIRED_URL_KEYS = {
    "Homepage",
    "Source",
    "Changelog",
    "Documentation",
    "Issues",
}


def _read(rel: str) -> str:
    path = ROOT / rel
    assert path.is_file(), f"missing required file: {rel}"
    return path.read_text(encoding="utf-8")


def _load_pyproject() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def _load_run_eval_report():
    path = ROOT / "scripts" / "run_eval_report.py"
    spec = importlib.util.spec_from_file_location("run_eval_report_mod", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_pack_plugin_shipset():
    path = ROOT / "scripts" / "pack_plugin_shipset.py"
    spec = importlib.util.spec_from_file_location("pack_plugin_shipset_mod", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Version triple (H2)
# ---------------------------------------------------------------------------


def test_version_triple_sync() -> None:
    data = _load_pyproject()
    pyproject_version = data["project"]["version"]
    assert pyproject_version == package_version

    plugin_text = _read("gimp-mcp-plugin.py")
    matches = PLUGIN_VERSION_RE.findall(plugin_text)
    assert len(matches) >= 1, "plugin_version not found via exact regex"
    assert matches[0] == pyproject_version
    # All matches must agree (single product version story)
    assert all(m == pyproject_version for m in matches)
    assert pyproject_version == "0.2.0"


def test_optional_installed_metadata_version() -> None:
    try:
        installed = importlib.metadata.version("gimp-mcp")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("gimp-mcp not installed in this environment")
    assert installed == package_version


# ---------------------------------------------------------------------------
# pyproject classifiers + urls (M2/M3/M4)
# ---------------------------------------------------------------------------


def test_pyproject_classifiers() -> None:
    data = _load_pyproject()
    classifiers = data["project"].get("classifiers", [])
    for required in REQUIRED_CLASSIFIERS:
        assert required in classifiers, f"missing classifier: {required}"
    for c in classifiers:
        assert not c.startswith("License ::"), f"must not have License trove classifier: {c}"
    assert data["project"].get("license") == "GPL-3.0-only"


def test_pyproject_urls() -> None:
    data = _load_pyproject()
    urls = data["project"].get("urls", {})
    for key in REQUIRED_URL_KEYS:
        assert key in urls, f"missing project.urls key: {key}"
        assert isinstance(urls[key], str) and urls[key].startswith("http")
    # Source may also be Repository; we require Source per track lock.
    assert "Ryan-AI-Studios/gimp-mcp" in urls["Homepage"]
    assert "Ryan-AI-Studios/gimp-mcp" in urls["Source"]


# ---------------------------------------------------------------------------
# Docs structure (M5)
# ---------------------------------------------------------------------------


def test_readme_links_release_md() -> None:
    text = _read("README.md")
    assert "release.md" in text or "docs/release.md" in text


def test_release_md_links_evaluation_release_gates() -> None:
    text = _read("docs/release.md")
    assert "evaluation" in text.lower()
    assert "#release-gates" in text or "release-gates" in text


def test_evaluation_md_links_release_md() -> None:
    text = _read("docs/evaluation.md")
    assert "release.md" in text


def test_release_md_documents_fail_closed_eval() -> None:
    text = _read("docs/release.md")
    assert "run_eval_report.py" in text
    # Fail-closed default and/or --require-pass alias documented (not bare "PASS")
    assert "--require-pass" in text or "fail-closed" in text.lower()


def test_release_md_exists_with_headers() -> None:
    text = _read("docs/release.md")
    assert text.strip()
    assert re.search(r"(?m)^#{1,2}\s+\S", text)
    # Honesty + wheel ≠ APPDATA
    lower = text.lower()
    assert "wheel" in lower
    assert "install" in lower


# ---------------------------------------------------------------------------
# Eval report exit contract (H1)
# ---------------------------------------------------------------------------


def test_resolve_exit_code_fail_closed_default() -> None:
    mod = _load_run_eval_report()
    resolve = mod.resolve_exit_code
    assert resolve("PASS", require_pass=True) == 0
    assert resolve("FAIL", require_pass=True) == 1
    assert resolve("PASS", require_pass=False) == 0
    assert resolve("FAIL", require_pass=False) == 0


def test_resolve_require_pass_from_flags() -> None:
    mod = _load_run_eval_report()
    # Default: require_pass True
    assert (
        mod.resolve_require_pass(require_pass=False, inspect=False, no_require_pass=False) is True
    )
    # --require-pass explicit
    assert mod.resolve_require_pass(require_pass=True, inspect=False, no_require_pass=False) is True
    # --inspect
    assert (
        mod.resolve_require_pass(require_pass=False, inspect=True, no_require_pass=False) is False
    )
    # --no-require-pass
    assert (
        mod.resolve_require_pass(require_pass=False, inspect=False, no_require_pass=True) is False
    )
    # --require-pass wins over inspect if both somehow set (idempotent strict)
    assert mod.resolve_require_pass(require_pass=True, inspect=True, no_require_pass=False) is True


# ---------------------------------------------------------------------------
# Plugin ship-set zip (M6)
# ---------------------------------------------------------------------------


def test_pack_plugin_shipset_to_tmp(tmp_path: Path) -> None:
    mod = _load_pack_plugin_shipset()
    out_zip = tmp_path / f"gimp-mcp-plugin-{package_version}.zip"
    result = mod.pack_plugin_shipset(
        source_dir=ROOT,
        output_zip=out_zip,
        version=package_version,
    )
    assert result == out_zip
    assert out_zip.is_file()

    with zipfile.ZipFile(out_zip, "r") as zf:
        names = set(zf.namelist())
    assert len(EXPECTED_PLUGIN_FILES) == 10
    non_manifest = names - {"MANIFEST.txt"}
    assert non_manifest == set(EXPECTED_PLUGIN_FILES), (
        f"zip members must equal EXPECTED_PLUGIN_FILES; got {sorted(non_manifest)}"
    )

    # MANIFEST.txt inside zip OR sidecar .sha256
    has_manifest = "MANIFEST.txt" in names
    sidecar = Path(str(out_zip) + ".sha256")
    assert has_manifest or sidecar.is_file(), "need MANIFEST.txt in zip or .sha256 sidecar"

    if has_manifest:
        with zipfile.ZipFile(out_zip, "r") as zf:
            manifest = zf.read("MANIFEST.txt").decode("utf-8")
        for expected in EXPECTED_PLUGIN_FILES:
            assert expected in manifest
            # sha256 hex present for each file
            assert re.search(
                rf"(?m)^{re.escape(expected)}\s+[0-9a-f]{{64}}\s*$",
                manifest,
            ) or re.search(
                rf"(?m)^[0-9a-f]{{64}}\s+{re.escape(expected)}\s*$",
                manifest,
            ), f"manifest must list sha256 for {expected}"


def test_pack_plugin_shipset_missing_source_fails(tmp_path: Path) -> None:
    mod = _load_pack_plugin_shipset()
    empty = tmp_path / "empty_src"
    empty.mkdir()
    out_zip = tmp_path / "out.zip"
    with pytest.raises(SystemExit) as excinfo:
        mod.pack_plugin_shipset(source_dir=empty, output_zip=out_zip, version="0.1.0")
    assert excinfo.value.code not in (0, None)


# ---------------------------------------------------------------------------
# uv build (H3 / M7) — slow, not default CI
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_uv_build_wheel_and_sdist(tmp_path: Path) -> None:
    out_dir = tmp_path / "dist"
    out_dir.mkdir()
    proc = subprocess.run(
        ["uv", "build", "--clear", "--out-dir", str(out_dir)],
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"uv build failed:\n{proc.stdout}\n{proc.stderr}"

    wheels = list(out_dir.glob("*.whl"))
    sdists = list(out_dir.glob("*.tar.gz"))
    assert len(wheels) >= 1, f"no wheel in {out_dir}: {list(out_dir.iterdir())}"
    assert len(sdists) >= 1, f"no sdist in {out_dir}: {list(out_dir.iterdir())}"

    wheel = wheels[0]
    with zipfile.ZipFile(wheel, "r") as zf:
        wnames = zf.namelist()
    # Host modules + package
    assert any("gimp_mcp_server" in n for n in wnames), "wheel missing gimp_mcp_server"
    assert any(n.startswith("gimp_agent/") for n in wnames), "wheel missing gimp_agent/"
    assert any("recipes/" in n and n.endswith(".json") for n in wnames), (
        "wheel missing gimp_agent recipes/*.json"
    )
    # Entry points metadata
    dist_info = [n for n in wnames if n.endswith("entry_points.txt")]
    assert dist_info, "wheel missing entry_points.txt"
    with zipfile.ZipFile(wheel, "r") as zf:
        ep_text = zf.read(dist_info[0]).decode("utf-8")
    assert "gimp-agent" in ep_text
    assert "gimp-mcp-server" in ep_text
    # Plugin is NOT required in the wheel (host distribution ≠ APPDATA install)
    plugin_in_wheel = any(n.endswith("gimp-mcp-plugin.py") for n in wnames)
    # Soft: allowed if present as data, but product docs say wheel ≠ install path.
    # Do not require plugin in wheel.
    _ = plugin_in_wheel  # documented: not required

    sdist = sdists[0]
    with tarfile.open(sdist, "r:gz") as tf:
        snames = tf.getnames()

    # Paths are typically package-0.1.0/pyproject.toml
    def _has(basename: str) -> bool:
        return any(n.endswith("/" + basename) or n == basename for n in snames)

    assert _has("pyproject.toml"), f"sdist missing pyproject.toml: {snames[:20]}"
    assert _has("LICENSE"), "sdist missing LICENSE"
    assert _has("README.md"), "sdist missing README.md"
    assert _has("gimp-mcp-plugin.py"), "sdist missing gimp-mcp-plugin.py"
