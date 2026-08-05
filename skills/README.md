# gimp-mcp Agent Skills package

Product front door: [README.md](../README.md). Architecture:
[docs/architecture.md](../docs/architecture.md).

Portable **runtime** Agent Skills for operating GIMP via this product’s hybrid
MCP + CLI surface. Not a marketplace plugin and not the GIMP plug-in ship set.

| Skill | Role |
|-------|------|
| `gimp` | Runtime **router** (probe → orient → ensure → route → verify) |
| `gimp-orient` | Session probe, orient, handles, coordinate declaration |
| `gimp-edit` | Interactive HL MCP edit loop |
| `gimp-batch` | Recipes + headless `plug-in-gimp-mcp-batch` |
| `gimp-verify` | Composite + metrics + artifact evidence |
| `gimp-install` | `gimp-agent install` / `doctor` operator path |

Shared progressive-disclosure docs live under `references/` — **always** install
them with the skills (relative links use `../references/…` from skill dirs).

## gimp-core vs gimp

| Name | Tree | Audience |
|------|------|----------|
| `gimp-core` | local gitignored `.agents/skills/` | Maintainers **building** the product |
| `gimp` | committed `skills/` (this package) | Agents **operating** GIMP images |

## Trust caveat

Project-level skills from a fresh or untrusted clone can inject agent
instructions. **Review skill content before activation.**

Never store tokens, passwords, or `.env` contents in skills. Transport is
loopback; paths are workspace-jailed (`GIMP_WORKSPACE_ROOT`).

## Install matrix

Copy the **full package** (all six skill dirs + `references/` + `MANIFEST.json`
+ this README + `AGENTS.gimp.md`) into a host discovery root:

| Host | Typical target |
|------|----------------|
| Grok Build (project) | project `.grok/skills/` (or config `[skills] paths`) |
| Grok Build (user) | `~/.grok/skills/` |
| Codex / open Agent Skills | `.agents/skills/` or `~/.agents/skills/` |
| Claude-compatible | Same `SKILL.md` layout (Grok also reads Claude skill locations) |

Helper (preferred on a full checkout):

```text
uv run gimp-agent skills list
uv run gimp-agent skills validate
uv run gimp-agent skills install --target <dir> [--dry-run]
```

`install` **copies** the full layout (no symlink requirement on Windows). Target
must be explicit.

### Layout after install

```text
<target>/
  references/
  gimp/
  gimp-orient/
  gimp-edit/
  gimp-batch/
  gimp-verify/
  gimp-install/
  MANIFEST.json
  README.md
  AGENTS.gimp.md
```

### AGENTS merge

Append or include `AGENTS.gimp.md` into project instruction files. Do not treat
this fragment as a replacement for local maintainer governance AGENTS files.

## Discovery honesty (source-tree SoT)

This package is consumed from a **clone/source checkout** (or an explicit install
target). A bare wheel install that does not ship `skills/` will not find the
package unless:

1. Env **`GIMP_MCP_SKILLS_ROOT`** points at a package root containing
   `MANIFEST.json`, or
2. You copied `skills/` to a discovery path as above.

Discovery order used by the host skills_pack module:

1. `GIMP_MCP_SKILLS_ROOT`
2. Walk up from cwd for `skills/MANIFEST.json`
3. Fallback: repo root next to the installed agent package (`…/skills`)

## Plugin install (separate)

Agent skills are **not** GIMP APPDATA plug-in files. To install the plugin ship
set (EXPECTED **10** files):

```text
uv run gimp-agent install
uv run gimp-agent doctor --strict
```

Then restart GIMP. See skill `gimp-install`.

## Validation

```text
uv run gimp-agent skills validate
uv run pytest tests/test_skills_pack.py -q
```

## Non-goals

- Marketplace publish / Grok plugin marketplace packaging
- Client hooks enforcing orient/verify lifecycle
- Packaging maintainer governance skills as product defaults

Client MCP wiring and dual image delivery (ImageContent + `filesystem_path`)
are productized under repo-root `adapters/` and `session_probe.image_delivery`
(track 0021) — not in this skills package.
