"""Pure Agent Skills package discover / parse / validate / list / install (track 0020).

Stdlib only — no PyYAML, no network, no GIMP. Skills live under repo ``skills/``
(source-tree SoT) or an explicit ``GIMP_MCP_SKILLS_ROOT``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants (pinned by 0020 spec)
# ---------------------------------------------------------------------------

PACKAGE_NAME = "gimp-mcp-skills"
PACKAGE_VERSION = "1.0"
ENV_SKILLS_ROOT = "GIMP_MCP_SKILLS_ROOT"

SKILL_NAMES: tuple[str, ...] = (
    "gimp",
    "gimp-orient",
    "gimp-edit",
    "gimp-batch",
    "gimp-verify",
    "gimp-install",
)

# lowercase a-z0-9-, ≤64, no leading/trailing/consecutive hyphens
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){0,63}$")
DESC_MAX = 1024
BODY_SOFT_LINES = 500
BODY_HARD_LINES = 800

# Package-level files always installed with skills
PACKAGE_META_FILES: tuple[str, ...] = (
    "MANIFEST.json",
    "README.md",
    "AGENTS.gimp.md",
)

# CLI / procedure / env tokens allowed in backtick identifiers (not HL tools)
EXTRA_ALLOWED_IDENTIFIERS: frozenset[str] = frozenset(
    {
        # CLI subcommands
        "install",
        "uninstall",
        "doctor",
        "probe",
        "version",
        "codes",
        "save-xcf",
        "export",
        "compare",
        "verify",
        "recipes",
        "run",
        "batch",
        "skills",
        "list",
        "validate",
        # Batch / procedure
        "plug-in-gimp-mcp-batch",
        "gimp-mcp-recipe",
        "python-fu-eval",
        # Env / flags (names only)
        "GIMP_MCP_ADVANCED_TOOLS",
        "GIMP_WORKSPACE_ROOT",
        "GIMP_MCP_SKILLS_ROOT",
        "GIMP_MCP_BATCH_MODE",
        "GIMP_MCP_BATCH_TIMEOUT_S",
        "--batch-interpreter",
        "--backend",
        "--strict",
        "--no-backup",
        "--dry-run",
        "--yes",
        "--json",
        "--target",
        # Package / files
        "SKILL.md",
        "MANIFEST.json",
        "AGENTS.gimp.md",
        "README.md",
        # Skill names
        "gimp",
        "gimp-orient",
        "gimp-edit",
        "gimp-batch",
        "gimp-verify",
        "gimp-install",
        "gimp-core",
        # Backends / policies
        "auto",
        "session",
        "headless",
        "batch_safe",
        "fail",
        # "version" already listed under CLI subcommands
        "replace",
    }
)

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"GIMP_MCP_TOKEN\s*="),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)secret\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)password\s*[:=]\s*['\"]?\S+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*"),
)

# snake_case identifiers that look like MCP tools
_SNAKE_TOOL_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
_TOP_KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*:\s*(.*)$")


# ---------------------------------------------------------------------------
# Reports / skill info
# ---------------------------------------------------------------------------


@dataclass
class SkillInfo:
    """Summary of one skill directory."""

    name: str
    path: str
    description: str = ""
    version: str | None = None
    license: str | None = None
    compatibility: str | None = None
    line_count: int = 0


@dataclass
class ValidationReport:
    """Outcome of validate_skill / validate_package."""

    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    root: str | None = None
    skills: list[str] = field(default_factory=list)

    def envelope_data(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "skills": list(self.skills),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


@dataclass
class InstallReport:
    """Outcome of install_skills (copy full package layout)."""

    ok: bool
    code: str | None
    message: str
    source_root: str | None = None
    target: str | None = None
    copied: list[str] = field(default_factory=list)
    planned: list[str] = field(default_factory=list)
    dry_run: bool = False
    errors: list[str] = field(default_factory=list)

    def envelope_data(self) -> dict[str, Any]:
        return {
            "source_root": self.source_root,
            "target": self.target,
            "copied": list(self.copied),
            "planned": list(self.planned),
            "dry_run": self.dry_run,
            "errors": list(self.errors),
        }


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_package_root(
    *,
    cwd: Path | None = None,
    environ: dict[str, str] | None = None,
    module_file: Path | None = None,
) -> Path:
    """Locate the skills package root (directory containing MANIFEST.json).

    Order (locked):
    1. Env ``GIMP_MCP_SKILLS_ROOT`` if set (absolute path to package root)
    2. Walk up from cwd looking for ``skills/MANIFEST.json``
    3. Fallback: ``Path(__file__).resolve().parents[1] / "skills"``
    """
    env = environ if environ is not None else os.environ
    raw = env.get(ENV_SKILLS_ROOT)
    if raw is not None and str(raw).strip():
        root = Path(str(raw).strip()).expanduser()
        if not root.is_absolute():
            root = root.resolve()
        else:
            root = root.resolve()
        return root

    start = (cwd if cwd is not None else Path.cwd()).resolve()
    for directory in (start, *start.parents):
        candidate = directory / "skills" / "MANIFEST.json"
        if candidate.is_file():
            return (directory / "skills").resolve()

    base = module_file if module_file is not None else Path(__file__)
    return (base.resolve().parents[1] / "skills").resolve()


# ---------------------------------------------------------------------------
# Frontmatter parser (stdlib state machine — not full YAML)
# ---------------------------------------------------------------------------


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Parse Agent Skills YAML-ish frontmatter into a dict.

    Supports:
    - Top-level ``key: value`` and ``key:`` + block scalar (``>`` folded / ``|`` literal)
    - One-level nested map under ``metadata:`` (indent 2 spaces)
    - Double-quoted string values

    Raises ``ValueError`` when the document has no valid ``---`` frontmatter.
    """
    lines = text.splitlines()
    # Skip leading blank lines
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or lines[i].strip() != "---":
        raise ValueError("frontmatter must start with --- on the first non-empty line")
    i += 1

    data: dict[str, Any] = {}
    while i < len(lines):
        line = lines[i]
        if line.strip() == "---":
            return data
        if not line.strip():
            i += 1
            continue
        # Nested metadata map (indent 2)
        if line.startswith("  ") and "metadata" in data and isinstance(data["metadata"], dict):
            nested = line[2:]  # strip one level of indent
            m = _TOP_KEY_RE.match(nested)
            if m is None:
                raise ValueError(f"invalid nested frontmatter line: {line!r}")
            nk, nv = m.group(1), m.group(2)
            data["metadata"][nk] = _parse_scalar(nv)
            i += 1
            continue

        m = _TOP_KEY_RE.match(line)
        if m is None:
            raise ValueError(f"invalid frontmatter line: {line!r}")
        key, rest = m.group(1), m.group(2)

        if rest in (">", "|"):
            style = rest
            block_lines: list[str] = []
            i += 1
            while i < len(lines):
                bl = lines[i]
                if bl.strip() == "---":
                    break
                if bl == "" or bl.startswith(" ") or bl.startswith("\t"):
                    block_lines.append(bl)
                    i += 1
                    continue
                # Next top-level key or nested (not indented) ends the block
                if _TOP_KEY_RE.match(bl) and not bl.startswith(" "):
                    break
                # Still part of block if indented content after blank?
                break
            data[key] = _fold_block(block_lines, style=style)
            continue

        if rest == "" and key == "metadata":
            data["metadata"] = {}
            i += 1
            continue

        data[key] = _parse_scalar(rest)
        i += 1

    raise ValueError("frontmatter missing closing ---")


def _parse_scalar(raw: str) -> Any:
    s = raw.strip()
    if s == "":
        return ""
    if (s.startswith('"') and s.endswith('"') and len(s) >= 2) or (
        s.startswith("'") and s.endswith("'") and len(s) >= 2
    ):
        return s[1:-1]
    # Bare true/false/null not required for skills; keep as string
    return s


def _fold_block(block_lines: list[str], *, style: str) -> str:
    """Fold (>) or literal (|) block scalar — strip common indent, trim trailing blanks."""
    if not block_lines:
        return ""
    # Drop leading blank lines
    while block_lines and not block_lines[0].strip():
        block_lines = block_lines[1:]
    while block_lines and not block_lines[-1].strip():
        block_lines = block_lines[:-1]
    if not block_lines:
        return ""

    # Common indent of non-empty lines
    indents: list[int] = []
    for bl in block_lines:
        if not bl.strip():
            continue
        stripped = bl.lstrip(" ")
        indents.append(len(bl) - len(stripped))
    common = min(indents) if indents else 0

    cleaned: list[str] = []
    for bl in block_lines:
        if bl.strip():
            cleaned.append(bl[common:] if len(bl) >= common else bl.lstrip(" "))
        else:
            cleaned.append("")

    if style == "|":
        return "\n".join(cleaned).rstrip("\n")

    # Folded: join non-empty with spaces; blank lines become paragraph breaks
    parts: list[str] = []
    para: list[str] = []
    for cl in cleaned:
        if cl.strip() == "":
            if para:
                parts.append(" ".join(para))
                para = []
            parts.append("")
        else:
            para.append(cl.strip())
    if para:
        parts.append(" ".join(para))
    # Collapse multiple paragraph markers and join with space (single para common)
    text = " ".join(p for p in parts if p != "")
    # If we had intentional blanks, use double-space sep is fine for descriptions
    return text.strip()


# ---------------------------------------------------------------------------
# Identifier / secret helpers
# ---------------------------------------------------------------------------


def extract_backtick_identifiers(text: str) -> list[str]:
    """Extract contents of single-backtick spans (no nested fences)."""
    return [m.group(1).strip() for m in _BACKTICK_RE.finditer(text) if m.group(1).strip()]


def looks_like_mcp_tool(name: str) -> bool:
    """True when identifier looks like a snake_case MCP tool name."""
    if not _SNAKE_TOOL_RE.match(name):
        return False
    # underscore present OR will be checked against HL set by caller
    return "_" in name


def is_allowed_identifier(name: str, hl_names: frozenset[str] | set[str]) -> bool:
    """Return True if name is HL tool, allowlisted, or not a tool-shaped token."""
    if name in hl_names:
        return True
    if name in EXTRA_ALLOWED_IDENTIFIERS:
        return True
    # Pure CLI tokens / kebab / paths — only tool-shaped need HL membership
    if looks_like_mcp_tool(name):
        return False
    # bare snake without underscore that is also an HL name is handled above
    if _SNAKE_TOOL_RE.match(name) and name in hl_names:
        return True
    # Non-tool-shaped tokens (paths, mixed case, hyphens, flags) are free
    return True


def secret_scan(text: str) -> list[str]:
    """Return human-readable hits for forbidden secret patterns."""
    hits: list[str] = []
    for pat in _SECRET_PATTERNS:
        for m in pat.finditer(text):
            snippet = m.group(0)
            if len(snippet) > 60:
                snippet = snippet[:57] + "..."
            hits.append(f"secret pattern: {snippet!r}")
    return hits


def secret_scan_tree(root: Path) -> list[str]:
    """Scan all files under root for secret patterns."""
    errors: list[str] = []
    if not root.is_dir():
        return [f"not a directory: {root}"]
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".md", ".json", ".txt", ".yml", ".yaml", ""}:
            # still scan common text; skip binaries by extension heuristic
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pyc", ".pyo"}:
                continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for hit in secret_scan(text):
            errors.append(f"{path.relative_to(root).as_posix()}: {hit}")
    return errors


def _hl_tool_names() -> frozenset[str]:
    try:
        from gimp_mcp_surface import HL_TOOL_NAMES

        return frozenset(HL_TOOL_NAMES)
    except Exception:
        return frozenset()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_skill(
    path: Path | str, *, hl_names: frozenset[str] | set[str] | None = None
) -> list[str]:
    """Validate one skill directory. Returns list of errors (empty = ok)."""
    skill_dir = Path(path)
    errors: list[str] = []
    if not skill_dir.is_dir():
        return [f"skill path is not a directory: {skill_dir}"]

    dir_name = skill_dir.name
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return [f"{dir_name}: missing SKILL.md"]

    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{dir_name}: cannot read SKILL.md: {exc}"]

    try:
        fm = parse_frontmatter(text)
    except ValueError as exc:
        return [f"{dir_name}: frontmatter: {exc}"]

    name = fm.get("name")
    if not isinstance(name, str) or not name:
        errors.append(f"{dir_name}: frontmatter missing name")
    else:
        if name != dir_name:
            errors.append(f"{dir_name}: name {name!r} does not match directory")
        if not NAME_RE.match(name):
            errors.append(f"{dir_name}: name {name!r} fails NAME_RE")

    desc = fm.get("description")
    if not isinstance(desc, str) or not desc.strip():
        errors.append(f"{dir_name}: description missing or empty")
    else:
        if len(desc) > DESC_MAX:
            errors.append(f"{dir_name}: description length {len(desc)} > {DESC_MAX}")

    line_count = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    if line_count > BODY_HARD_LINES:
        errors.append(f"{dir_name}: SKILL.md has {line_count} lines (hard max {BODY_HARD_LINES})")

    for hit in secret_scan(text):
        errors.append(f"{dir_name}: {hit}")

    hl = frozenset(hl_names) if hl_names is not None else _hl_tool_names()
    for ident in extract_backtick_identifiers(text):
        # Skip multi-word / path-like spans
        token = ident.split()[0] if " " in ident else ident
        # Strip trailing punctuation often left in prose
        token = token.rstrip(".,;:)")
        if not token:
            continue
        # Ignore paths and dotted module paths
        if "/" in token or "\\" in token or token.startswith("."):
            continue
        if not is_allowed_identifier(token, hl):
            errors.append(f"{dir_name}: unknown tool-like identifier `{token}`")

    return errors


def validate_package(
    root: Path | str | None = None,
    *,
    hl_names: frozenset[str] | set[str] | None = None,
) -> ValidationReport:
    """Validate the full skills package. ``ok`` when errors is empty."""
    package_root = Path(root) if root is not None else discover_package_root()
    errors: list[str] = []
    warnings: list[str] = []
    skills_found: list[str] = []

    if not package_root.is_dir():
        return ValidationReport(
            ok=False,
            errors=[f"package root not found: {package_root}"],
            root=str(package_root),
        )

    manifest_path = package_root / "MANIFEST.json"
    if not manifest_path.is_file():
        errors.append("missing MANIFEST.json")
        return ValidationReport(ok=False, errors=errors, root=str(package_root))

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"MANIFEST.json invalid: {exc}")
        return ValidationReport(ok=False, errors=errors, root=str(package_root))

    if not isinstance(manifest, dict):
        errors.append("MANIFEST.json must be an object")
    else:
        if manifest.get("package") != PACKAGE_NAME:
            errors.append(f"MANIFEST package {manifest.get('package')!r} != {PACKAGE_NAME!r}")
        if not isinstance(manifest.get("version"), str) or not manifest.get("version"):
            errors.append("MANIFEST version must be a non-empty string")
        skills_raw = manifest.get("skills")
        if not isinstance(skills_raw, list) or not all(isinstance(s, str) for s in skills_raw):
            errors.append("MANIFEST skills must be an array of strings")
        else:
            skills_set = set(skills_raw)
            expected = set(SKILL_NAMES)
            if skills_set != expected:
                missing = sorted(expected - skills_set)
                extra = sorted(skills_set - expected)
                if missing:
                    errors.append(f"MANIFEST skills missing: {missing}")
                if extra:
                    errors.append(f"MANIFEST skills extra: {extra}")

    for meta in PACKAGE_META_FILES:
        if meta == "MANIFEST.json":
            continue
        if not (package_root / meta).is_file():
            errors.append(f"missing package file: {meta}")

    agents = package_root / "AGENTS.gimp.md"
    if agents.is_file():
        try:
            agents_text = agents.read_text(encoding="utf-8")
            if "gimp" not in agents_text:
                errors.append("AGENTS.gimp.md must mention router name gimp")
            if len(agents_text.strip()) < 20:
                errors.append("AGENTS.gimp.md is too short")
        except OSError as exc:
            errors.append(f"AGENTS.gimp.md unreadable: {exc}")

    refs = package_root / "references"
    if not refs.is_dir():
        errors.append("missing references/ directory")

    hl = frozenset(hl_names) if hl_names is not None else _hl_tool_names()
    for name in SKILL_NAMES:
        skill_dir = package_root / name
        if not skill_dir.is_dir():
            errors.append(f"missing skill directory: {name}")
            continue
        skills_found.append(name)
        skill_errors = validate_skill(skill_dir, hl_names=hl)
        errors.extend(skill_errors)
        # Soft line-count warning
        skill_md = skill_dir / "SKILL.md"
        if skill_md.is_file():
            try:
                text = skill_md.read_text(encoding="utf-8")
                n = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
                if BODY_SOFT_LINES < n <= BODY_HARD_LINES:
                    warnings.append(
                        f"{name}: SKILL.md has {n} lines (soft prefer <{BODY_SOFT_LINES})"
                    )
            except OSError:
                pass

    # Secret scan whole tree
    errors.extend(secret_scan_tree(package_root))

    # Content contracts (batch procedure + router keywords)
    batch_md = package_root / "gimp-batch" / "SKILL.md"
    if batch_md.is_file():
        try:
            body = batch_md.read_text(encoding="utf-8")
            if "plug-in-gimp-mcp-batch" not in body:
                errors.append("gimp-batch: must document plug-in-gimp-mcp-batch")
            # Forbid recommending python-fu-eval as product path
            if re.search(
                r"(?i)use\s+`?python-fu-eval`?|prefer\s+`?python-fu-eval`?|"
                r"product\s+path.*python-fu-eval|python-fu-eval.*product",
                body,
            ):
                # Allow "never" / "not" / "do not" nearby — check negation
                if not re.search(
                    r"(?i)(never|not|do not|don't|avoid|forbidden|not a product).{0,40}python-fu-eval"
                    r"|python-fu-eval.{0,40}(never|not product|not a product|do not|don't|avoid)",
                    body,
                ):
                    errors.append("gimp-batch: must not recommend python-fu-eval as product path")
        except OSError:
            pass

    router_md = package_root / "gimp" / "SKILL.md"
    if router_md.is_file():
        try:
            body = router_md.read_text(encoding="utf-8").lower()
            if "orient" not in body:
                errors.append("gimp router: must mention orient")
            if "ensure" not in body:
                errors.append("gimp router: must mention ensure")
        except OSError:
            pass

    return ValidationReport(
        ok=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        root=str(package_root),
        skills=skills_found,
    )


# ---------------------------------------------------------------------------
# List / install
# ---------------------------------------------------------------------------


def list_skills(root: Path | str | None = None) -> list[SkillInfo]:
    """Return SkillInfo for each skill under the package root."""
    package_root = Path(root) if root is not None else discover_package_root()
    out: list[SkillInfo] = []
    for name in SKILL_NAMES:
        skill_dir = package_root / name
        skill_md = skill_dir / "SKILL.md"
        info = SkillInfo(name=name, path=str(skill_dir))
        if skill_md.is_file():
            try:
                text = skill_md.read_text(encoding="utf-8")
                info.line_count = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
                try:
                    fm = parse_frontmatter(text)
                    desc = fm.get("description")
                    if isinstance(desc, str):
                        info.description = desc
                    info.license = fm.get("license") if isinstance(fm.get("license"), str) else None
                    info.compatibility = (
                        fm.get("compatibility")
                        if isinstance(fm.get("compatibility"), str)
                        else None
                    )
                    meta = fm.get("metadata")
                    if isinstance(meta, dict):
                        ver = meta.get("version")
                        if isinstance(ver, str):
                            info.version = ver
                except ValueError:
                    pass
            except OSError:
                pass
        out.append(info)
    return out


def install_skills(
    target: Path | str,
    *,
    dry_run: bool = False,
    root: Path | str | None = None,
) -> InstallReport:
    """Copy full package layout into ``target`` (skills + references + meta).

    Always includes ``references/``. No symlinks. Target must be explicit.
    """
    package_root = Path(root) if root is not None else discover_package_root()
    dest = Path(target)
    planned: list[str] = []
    copied: list[str] = []
    errors: list[str] = []

    if not package_root.is_dir():
        return InstallReport(
            ok=False,
            code="PLUGIN_NOT_FOUND",
            message=f"skills package root not found: {package_root}",
            source_root=str(package_root),
            target=str(dest),
            dry_run=dry_run,
            errors=[f"source missing: {package_root}"],
        )

    # Preflight: source must have references + skills
    refs_src = package_root / "references"
    if not refs_src.is_dir():
        errors.append("source missing references/")
    for name in SKILL_NAMES:
        if not (package_root / name / "SKILL.md").is_file():
            errors.append(f"source missing skill: {name}")
    for meta in PACKAGE_META_FILES:
        if not (package_root / meta).is_file():
            errors.append(f"source missing: {meta}")
    if errors:
        return InstallReport(
            ok=False,
            code="PLUGIN_NOT_FOUND",
            message="skills package incomplete; cannot install",
            source_root=str(package_root),
            target=str(dest),
            dry_run=dry_run,
            errors=errors,
        )

    # Build copy plan: relative paths from package root
    items: list[str] = []
    for meta in PACKAGE_META_FILES:
        items.append(meta)
    items.append("references")
    for name in SKILL_NAMES:
        items.append(name)

    for rel in items:
        planned.append(rel)

    if dry_run:
        return InstallReport(
            ok=True,
            code=None,
            message=f"dry-run: would install {len(planned)} items to {dest}",
            source_root=str(package_root),
            target=str(dest),
            planned=planned,
            dry_run=True,
        )

    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return InstallReport(
            ok=False,
            code="CLI_USAGE",
            message=f"cannot create target directory: {exc}",
            source_root=str(package_root),
            target=str(dest),
            planned=planned,
            dry_run=False,
            errors=[str(exc)],
        )

    for rel in items:
        src = package_root / rel
        dst = dest / rel
        try:
            if src.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            copied.append(rel)
        except OSError as exc:
            errors.append(f"{rel}: {exc}")

    # Verify references landed
    if not (dest / "references").is_dir():
        errors.append("install did not produce references/")

    ok = len(errors) == 0
    return InstallReport(
        ok=ok,
        code=None if ok else "INTERNAL",
        message=(
            f"installed {len(copied)} items to {dest}"
            if ok
            else f"install failed with {len(errors)} error(s)"
        ),
        source_root=str(package_root),
        target=str(dest),
        copied=copied,
        planned=planned,
        dry_run=False,
        errors=errors,
    )
