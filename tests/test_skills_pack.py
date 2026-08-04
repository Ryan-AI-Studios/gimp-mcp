"""Offline tests for Agent Skills package (track 0020) — no live GIMP required."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gimp_agent import skills_pack as sp
from gimp_mcp_surface import HL_TOOL_NAMES

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / "skills"


def _package_available() -> bool:
    return (SKILLS_ROOT / "MANIFEST.json").is_file()


requires_package = pytest.mark.skipif(
    not _package_available(),
    reason="skills/ package not present yet",
)


# ---------------------------------------------------------------------------
# parse_frontmatter unit tests
# ---------------------------------------------------------------------------


def test_parse_frontmatter_simple() -> None:
    text = """---
name: gimp-edit
description: Short description here
license: MIT
metadata:
  version: "1.0"
  package: gimp-mcp-skills
---

# Body
"""
    fm = sp.parse_frontmatter(text)
    assert fm["name"] == "gimp-edit"
    assert fm["description"] == "Short description here"
    assert fm["license"] == "MIT"
    assert fm["metadata"]["version"] == "1.0"
    assert fm["metadata"]["package"] == "gimp-mcp-skills"


def test_frontmatter_colon_in_description() -> None:
    """Block scalar description with colons must parse (AI1 M3)."""
    text = """---
name: gimp
description: >
  Use when: editing images, MCP vs CLI routing, and keywords: orient, ensure.
license: MIT
metadata:
  version: "1.0"
---

# Body
"""
    fm = sp.parse_frontmatter(text)
    assert fm["name"] == "gimp"
    assert "Use when:" in fm["description"]
    assert "keywords:" in fm["description"]
    assert "orient" in fm["description"]


def test_frontmatter_quoted_description_with_colon() -> None:
    text = """---
name: demo
description: "What it does: when to use it"
---
"""
    fm = sp.parse_frontmatter(text)
    assert fm["description"] == "What it does: when to use it"


def test_frontmatter_literal_block() -> None:
    text = """---
name: demo
description: |
  line one
  line two
---
"""
    fm = sp.parse_frontmatter(text)
    assert "line one" in fm["description"]
    assert "line two" in fm["description"]


def test_frontmatter_missing_open_raises() -> None:
    with pytest.raises(ValueError, match="---"):
        sp.parse_frontmatter("# no frontmatter\n")


def test_frontmatter_missing_close_raises() -> None:
    with pytest.raises(ValueError, match="closing"):
        sp.parse_frontmatter("---\nname: x\n")


def test_name_re_accepts_skill_names() -> None:
    for name in sp.SKILL_NAMES:
        assert sp.NAME_RE.match(name), name


def test_name_re_rejects_bad() -> None:
    for bad in ("-gimp", "gimp-", "Gimp", "gimp--edit", "has space", "a" * 65):
        assert not sp.NAME_RE.match(bad), bad


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discover_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "custom-skills"
    root.mkdir()
    (root / "MANIFEST.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv(sp.ENV_SKILLS_ROOT, str(root))
    found = sp.discover_package_root(cwd=tmp_path / "elsewhere")
    assert found == root.resolve()


def test_discover_walk_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sp.ENV_SKILLS_ROOT, raising=False)
    pkg = tmp_path / "skills"
    pkg.mkdir()
    (pkg / "MANIFEST.json").write_text("{}", encoding="utf-8")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    found = sp.discover_package_root(cwd=nested, environ={})
    assert found == pkg.resolve()


def test_discover_module_fallback(tmp_path: Path) -> None:
    # No env, cwd without skills → module parents[1]/skills
    fake_module = tmp_path / "pkg" / "skills_pack.py"
    fake_module.parent.mkdir(parents=True)
    fake_module.write_text("#", encoding="utf-8")
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "MANIFEST.json").write_text("{}", encoding="utf-8")
    found = sp.discover_package_root(
        cwd=tmp_path / "other",
        environ={},
        module_file=fake_module,
    )
    assert found == skills.resolve()


# ---------------------------------------------------------------------------
# validate_skill edge cases (no full package required)
# ---------------------------------------------------------------------------


def test_validate_skill_name_mismatch(tmp_path: Path) -> None:
    d = tmp_path / "gimp-edit"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: wrong-name\ndescription: ok description\n---\n\n# x\n",
        encoding="utf-8",
    )
    errs = sp.validate_skill(d, hl_names=HL_TOOL_NAMES)
    assert any("does not match" in e for e in errs)


def test_validate_skill_empty_description(tmp_path: Path) -> None:
    d = tmp_path / "gimp"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: gimp\ndescription: \n---\n\n# x\n",
        encoding="utf-8",
    )
    errs = sp.validate_skill(d, hl_names=HL_TOOL_NAMES)
    assert any("description" in e for e in errs)


def test_validate_skill_description_too_long(tmp_path: Path) -> None:
    d = tmp_path / "gimp"
    d.mkdir()
    long_desc = "x" * (sp.DESC_MAX + 1)
    (d / "SKILL.md").write_text(
        f'---\nname: gimp\ndescription: "{long_desc}"\n---\n\n# x\n',
        encoding="utf-8",
    )
    errs = sp.validate_skill(d, hl_names=HL_TOOL_NAMES)
    assert any("description length" in e for e in errs)


def test_validate_skill_unknown_tool(tmp_path: Path) -> None:
    d = tmp_path / "gimp"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: gimp\ndescription: uses a fake tool\n---\n\n"
        "Call `get_state_snapshot` always.\n",
        encoding="utf-8",
    )
    errs = sp.validate_skill(d, hl_names=HL_TOOL_NAMES)
    assert any("get_state_snapshot" in e for e in errs)


def test_validate_skill_allowlisted_cli_token(tmp_path: Path) -> None:
    d = tmp_path / "gimp-install"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: gimp-install\ndescription: install helper\n---\n\n"
        "Run `install` then `doctor` with `--strict`.\n",
        encoding="utf-8",
    )
    errs = sp.validate_skill(d, hl_names=HL_TOOL_NAMES)
    assert errs == []


def test_secret_scan_detects_token() -> None:
    hits = sp.secret_scan("export GIMP_MCP_TOKEN=abc123")
    assert hits


def test_is_allowed_identifier_hl_and_extra() -> None:
    assert sp.is_allowed_identifier("session_probe", HL_TOOL_NAMES)
    assert sp.is_allowed_identifier("plug-in-gimp-mcp-batch", HL_TOOL_NAMES)
    assert sp.is_allowed_identifier("--backend", HL_TOOL_NAMES)
    assert not sp.is_allowed_identifier("get_state_snapshot", HL_TOOL_NAMES)
    assert sp.is_allowed_identifier("auto", HL_TOOL_NAMES)


# ---------------------------------------------------------------------------
# Package-level tests (require skills/ content)
# ---------------------------------------------------------------------------


@requires_package
def test_package_manifest_complete() -> None:
    manifest = json.loads((SKILLS_ROOT / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["package"] == sp.PACKAGE_NAME
    assert isinstance(manifest["version"], str) and manifest["version"]
    assert set(manifest["skills"]) == set(sp.SKILL_NAMES)


@requires_package
def test_frontmatter_name_matches_dir() -> None:
    for name in sp.SKILL_NAMES:
        skill_md = SKILLS_ROOT / name / "SKILL.md"
        fm = sp.parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        assert fm["name"] == name


@requires_package
def test_description_bounds() -> None:
    for name in sp.SKILL_NAMES:
        skill_md = SKILLS_ROOT / name / "SKILL.md"
        fm = sp.parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        desc = fm["description"]
        assert isinstance(desc, str)
        assert 1 <= len(desc) <= sp.DESC_MAX, name


@requires_package
def test_no_secret_patterns() -> None:
    hits = sp.secret_scan_tree(SKILLS_ROOT)
    assert hits == [], hits


@requires_package
def test_hl_tool_names_known() -> None:
    """All tool-shaped backtick identifiers subset of HL union EXTRA_ALLOWED."""
    allowed = set(HL_TOOL_NAMES) | set(sp.EXTRA_ALLOWED_IDENTIFIERS)
    for path in SKILLS_ROOT.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        for ident in sp.extract_backtick_identifiers(text):
            token = ident.split()[0].rstrip(".,;:)")
            if not token or "/" in token or "\\" in token or token.startswith("."):
                continue
            if sp.looks_like_mcp_tool(token) or token in HL_TOOL_NAMES:
                assert token in allowed, f"{path.name}: unknown `{token}`"


@requires_package
def test_batch_procedure_name() -> None:
    body = (SKILLS_ROOT / "gimp-batch" / "SKILL.md").read_text(encoding="utf-8")
    assert "plug-in-gimp-mcp-batch" in body
    # Must not recommend python-fu-eval as product path (negation required if mentioned)
    lower = body.lower()
    if "python-fu-eval" in lower:
        assert any(
            neg in lower
            for neg in (
                "never",
                "not a product",
                "not product",
                "do not",
                "don't",
                "avoid",
                "forbidden",
            )
        )


@requires_package
def test_router_mentions_orient_and_ensure() -> None:
    body = (SKILLS_ROOT / "gimp" / "SKILL.md").read_text(encoding="utf-8").lower()
    assert "orient" in body
    assert "ensure" in body


@requires_package
def test_agents_gimp_fragment() -> None:
    path = SKILLS_ROOT / "AGENTS.gimp.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "gimp" in text
    assert len(text.strip()) >= 20
    assert len(text.splitlines()) < 200


@requires_package
def test_validate_package_ok() -> None:
    report = sp.validate_package(SKILLS_ROOT)
    assert report.ok, report.errors
    assert set(report.skills) == set(sp.SKILL_NAMES)


@requires_package
def test_list_skills_returns_six() -> None:
    infos = sp.list_skills(SKILLS_ROOT)
    assert len(infos) == 6
    assert {i.name for i in infos} == set(sp.SKILL_NAMES)
    for info in infos:
        assert info.description


@requires_package
def test_install_includes_references(tmp_path: Path) -> None:
    dry = sp.install_skills(tmp_path / "dry", dry_run=True, root=SKILLS_ROOT)
    assert dry.ok
    assert dry.dry_run
    assert "references" in dry.planned
    for name in sp.SKILL_NAMES:
        assert name in dry.planned

    target = tmp_path / "installed"
    report = sp.install_skills(target, dry_run=False, root=SKILLS_ROOT)
    assert report.ok, report.errors
    assert (target / "references").is_dir()
    assert (target / "MANIFEST.json").is_file()
    assert (target / "README.md").is_file()
    assert (target / "AGENTS.gimp.md").is_file()
    for name in sp.SKILL_NAMES:
        assert (target / name / "SKILL.md").is_file()
    # references not empty
    assert any((target / "references").iterdir())


def test_install_refuses_secrets(tmp_path: Path) -> None:
    """install_skills refuses to copy when source tree contains secret patterns."""
    import shutil

    src = tmp_path / "poisoned"
    shutil.copytree(SKILLS_ROOT, src)
    (src / "gimp" / "SKILL.md").write_text(
        "---\nname: gimp\ndescription: >\n  x\n---\n\nGIMP_MCP_TOKEN=abc\n",
        encoding="utf-8",
    )
    report = sp.install_skills(tmp_path / "out", dry_run=False, root=src)
    assert report.ok is False
    assert report.code == "VERIFY_FAILED"
    assert not (tmp_path / "out" / "MANIFEST.json").exists()


@requires_package
def test_line_count_within_hard_max() -> None:
    for name in sp.SKILL_NAMES:
        text = (SKILLS_ROOT / name / "SKILL.md").read_text(encoding="utf-8")
        n = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
        assert n <= sp.BODY_HARD_LINES, f"{name}: {n} lines"


@requires_package
def test_references_exist() -> None:
    refs = SKILLS_ROOT / "references"
    expected = {
        "hybrid-decision-tree.md",
        "hl-tool-catalog.md",
        "coordinate-declaration.md",
        "layer-policy.md",
        "verification-protocol.md",
        "cli-and-batch.md",
    }
    found = {p.name for p in refs.iterdir() if p.is_file()}
    assert expected <= found


@requires_package
def test_compatibility_on_batch_and_install() -> None:
    for name in ("gimp-batch", "gimp-install"):
        fm = sp.parse_frontmatter((SKILLS_ROOT / name / "SKILL.md").read_text(encoding="utf-8"))
        assert isinstance(fm.get("compatibility"), str) and fm["compatibility"]


def test_extra_allowed_identifiers_pinned() -> None:
    """Sanity: pinned set includes key tokens from spec."""
    for token in (
        "plug-in-gimp-mcp-batch",
        "python-fu-eval",
        "GIMP_MCP_SKILLS_ROOT",
        "--backend",
        "gimp-core",
        "batch_safe",
        "skills",
        "validate",
    ):
        assert token in sp.EXTRA_ALLOWED_IDENTIFIERS
