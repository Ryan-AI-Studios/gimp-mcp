# GIMP MCP

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![GIMP 3.2](https://img.shields.io/badge/GIMP-3.2-orange.svg)](https://gimp.org)
[![MCP Compatible](https://img.shields.io/badge/MCP-Compatible-green.svg)](https://modelcontextprotocol.io)
[![Python ≥3.11](https://img.shields.io/badge/Python-%E2%89%A53.11-blue.svg)](https://www.python.org)
[![Works with Claude Desktop](https://img.shields.io/badge/Works%20with-Claude%20Desktop-7B2CBF.svg)](https://claude.ai/desktop)

**Ryan-AI-Studios fork** of [maorcc/gimp-mcp](https://github.com/maorcc/gimp-mcp) —
hardened hybrid agent control for **GIMP 3.2** via [Model Context Protocol](https://modelcontextprotocol.io)
and a deterministic CLI sidecar.

## Demo

![GIMP MCP in action — AI agent driving GIMP through natural language](docs/mcpInAction.gif)

Full demo (with audio): [docs/demo.mp4](docs/demo.mp4)

*AI agent using GIMP MCP to remove a background, edit a character's expression, and verify results — all through natural language*

---

## Product one-liner

Edit images with AI assistants that can **see** the canvas, **orient** a layered
document, run **allowlisted recipes**, and **verify pixels** — under loopback auth,
workspace path jail, and a curated **30-tool** default surface (optional advanced
~90 tools).

### Product readiness

**Product mission complete at 0.2.x** — the planned hybrid MCP + CLI sequence is
delivered and offline-tested (release baseline **`v0.2.0`**). This is **not** a
SemVer **1.0.0** public API freeze. Accepted debt and declined work live in
[docs/known-residuals.md](docs/known-residuals.md).

---

## Documentation index

| Doc | Role |
|-----|------|
| [docs/architecture.md](docs/architecture.md) | Hybrid MCP + CLI architecture and capabilities |
| [docs/operator-runbook.md](docs/operator-runbook.md) | Start-order checklist, install, live ops |
| [SECURITY.md](SECURITY.md) | Threat model, env, error codes, residuals |
| [docs/performance.md](docs/performance.md) | Snapshot budgets and command timeouts |
| [docs/ci-and-testing.md](docs/ci-and-testing.md) | Offline CI SoT, markers, branch protection |
| [docs/release.md](docs/release.md) | Release checklist, version triple, build and tag |
| [docs/evaluation.md](docs/evaluation.md) | Scored eval corpus, release gates, rubric report |
| [docs/known-residuals.md](docs/known-residuals.md) | Public residual inventory (accepted / ops / declined) |
| [docs/subject-isolation.md](docs/subject-isolation.md) | Contiguous select + optional rembg host cutout paths |
| [GIMP_MCP_PROTOCOL.md](GIMP_MCP_PROTOCOL.md) | Wire protocol and tool detail |
| [adapters/](adapters/README.md) | Grok / Codex / Claude client examples |
| [skills/](skills/README.md) | Portable Agent Skills package |
| [CHANGELOG.md](CHANGELOG.md) | Release notes (Keep a Changelog) |

---

## What you get

| | |
|---|---|
| 👁️ **Live visual feedback** | `render_visible_composite` — visible canvas PNG + mapping mid-workflow |
| 🧭 **Workspace orientation** | `orient_workspace` schema-versioned state manifest |
| 🎨 **30 HL / ~90 advanced** | Curated default surface; full footgun surface via env |
| 📦 **Recipe library** | Versioned JSON recipes; MCP + CLI `run` / `batch` |
| ✅ **Pixel verification** | Host `compare_images` / `verify_artifact` (and CLI) |
| 🔒 **Hardened defaults** | Loopback, session token, exec off, path jail |
| 🔧 **GIMP 3.2 compatible** | Install/doctor for EXPECTED 10-file ship set |
| 🔌 **Universal MCP** | Claude Desktop, Claude Code, Grok, Codex, Gemini, … |

---

## High-level tools (default 30)

By default the MCP server lists **30 high-level tools**. Detail catalog:
[skills/references/hl-tool-catalog.md](skills/references/hl-tool-catalog.md).
Protocol examples: [GIMP_MCP_PROTOCOL.md](GIMP_MCP_PROTOCOL.md).

| Tool | Purpose |
|------|---------|
| `session_probe` | Connectivity, surface mode, capabilities |
| `restart_server` | Drop/reconnect TCP (prefer probe first) |
| `orient_workspace` | State-manifest orientation SoT |
| `select_image` / `select_layers` | Bind active image/layers by handle |
| `open_image` / `close_image` / `new_canvas` | Session lifecycle |
| `ensure_source_immutable` | Source layer policy before mutation |
| `checkpoint_create` / `checkpoint_restore` | XCF checkpoints |
| `undo_group_begin` / `undo_group_end` / `undo_group_rollback` | Multi-step undo TX |
| **`render_visible_composite`** | **Primary vision path** — visible composite PNG + mapping (default max edge **1024**) |
| `normalize_image_orientation` | EXIF orientation normalize |
| `map_preview_to_image` | Preview/composite coords → image coords |
| `save_xcf` / `export_image` | Atomic XCF/export; collision `fail`/`version`/`replace` |
| `verify_alpha_channel` | Alpha preflight on open document |
| `create_selection` | Unified selection (rect/ellipse/by_color/all/none) |
| `get_selection_bounds` | Bounding rectangle of current selection |
| `clear_selection_to_transparent` | Clear selection to transparent (empty fail-closed) |
| `compare_images` | Host PNG MAE / max AE / changed pixels / global SSIM |
| `verify_artifact` | Host artifact dims/format/alpha/sha256 gates |
| `list_recipes` / `apply_recipe` | Versioned allowlisted multi-step recipes |
| `apply_nde_filter` / `edit_filter_config` / `remove_nde_filter` | Re-editable NDE filters |

**Typical agent loop:** `session_probe` → `orient_workspace` → edit →
`render_visible_composite` → `compare_images` / `verify_artifact` → export.

```python
# Full-canvas preview (omit max dims → product default max edge 1024)
render_visible_composite()

# Detail crop (opt-in smaller edge is fine)
render_visible_composite(
    region={"x": 140, "y": 80, "width": 240, "height": 300},
    max_size=512,
    label="face-check",
)
```

### Advanced tools (~90)

Set **`GIMP_MCP_ADVANCED_TOOLS=1`** on the **host / stdio MCP process** for the full
advanced surface (legacy filters, low-level selection primitives, `call_api`, etc.).
Restart the MCP server **and** the LLM client session after flipping (clients cache
`list_tools`). Wire-level detail: [GIMP_MCP_PROTOCOL.md](GIMP_MCP_PROTOCOL.md).

<details>
<summary>Migration names (advanced aliases)</summary>

| Prefer (default HL) | Legacy (advanced) |
|---|---|
| `session_probe` | `check_server` |
| `render_visible_composite` | `get_image_bitmap` / `get_state_snapshot` |
| `create_selection` | `select_rectangle` / `select_ellipse` / `select_by_color` / `select_contiguous` / `select_all` / `select_none` |

`call_api` remains Class B exec and requires `GIMP_MCP_ALLOW_EXEC=1` separately.

</details>

---

## Prerequisites

- **GIMP 3.2.4+** — Windows primary operator baseline; macOS/Linux also supported
- **Python ≥3.11** — `requires-python` in `pyproject.toml`
- **uv** — Python package manager (`pip install uv` or see [astral.sh/uv](https://docs.astral.sh/uv/))
- **MCP-compatible AI client** — Claude Desktop, Claude Code, Grok, Codex, Gemini CLI, etc.

---

## Quick Start

### 1. Clone and install dependencies

```bash
git clone https://github.com/Ryan-AI-Studios/gimp-mcp.git
cd gimp-mcp
uv sync --group dev
# Optional ML subject isolation (rembg/onnxruntime; not required for default CI):
# uv sync --extra subject
# See docs/subject-isolation.md
```

### 2. Install the GIMP plugin

Host wheel/sdist (`uv build` / `pip install`) is the CLI + MCP **host package only** —
it does **not** register the plug-in with GIMP. Always run `gimp-agent install` (or
unpack the ship-set zip) for the APPDATA ship set. See
[docs/release.md](docs/release.md).

Deploy the full **EXPECTED ship set (10 files)** into the newest GIMP `3.*` user
plug-ins directory:

```bash
uv run gimp-agent install
uv run gimp-agent doctor --strict --json   # verify plugin_files 10/10
```

Useful flags: `--dry-run`, `--source DIR`, `--target DIR`, `--no-backup`, `--json`.
Optional wrappers: `scripts/install-plugin.ps1` (Windows), `scripts/install-plugin.sh` (POSIX).

**Upgrade path:** after `git pull` or a GIMP minor upgrade, re-run
`uv run gimp-agent install`. Timestamped `*.bak.*` backups accumulate beside
replaced files — prune **manually** (`--prune-backups` is not shipped yet).

Host-only modules (`gimp_mcp_state`, `gimp_mcp_surface`, `gimp_mcp_verify`,
recipes) are **never** copied into plug-ins. Fully quit and relaunch GIMP after
install, then **Tools → MCP → Start MCP Server**.

> **Which directory?** GIMP names its per-user folder after **major.minor**
> (`3.0`, `3.2`, …). `gimp-agent install` picks the highest `3.*` config dir.
> Confirm: **Edit → Preferences → Folders → Plug-ins**. Launch GIMP once before
> install so the config folder exists.

**Manual fallback** — copy these **10** files into
`…/plug-ins/gimp-mcp-plugin/`:

`gimp-mcp-plugin.py`, `gimp_mcp_security.py`, `gimp_mcp_snapshot.py`,
`gimp_mcp_export.py`, `gimp_mcp_handles.py`, `gimp_mcp_coords.py`,
`gimp_mcp_policy.py`, `gimp_mcp_atomic.py`, `gimp_mcp_filters.py`,
`gimp_mcp_tx.py`.

### 3. Workspace + start plugin

Host MCP env and the GIMP **plugin process** are separate worlds. Set the jail on
GIMP itself (not only in client config):

```powershell
uv run gimp-agent launch-gui --workspace C:\path\to\workspace
# or: powershell -ExecutionPolicy Bypass -File .\scripts\launch-gimp.ps1 -WorkspaceRoot C:\path\to\workspace
```

1. Launch GIMP with `GIMP_WORKSPACE_ROOT` on the **GIMP process** (above).
2. Open an image in GIMP.
3. **Tools → MCP → Start MCP Server** (binds `127.0.0.1:9877`, writes session token).
4. Confirm via MCP `session_probe.plugin_workspace_root` (and host root in client config for dual-delivery).

Full numbered checklist: [docs/operator-runbook.md#start-order](docs/operator-runbook.md#start-order).
Dual-env detail: [docs/operator-runbook.md#dual-env](docs/operator-runbook.md#dual-env).

### 4. Configure your MCP client

Committed examples (timeouts, Windows paths, dual-delivery notes, server id
**`gimp`**): **[adapters/](adapters/README.md)**.

#### Claude Desktop

`~/.config/Claude/claude_desktop_config.json` (Linux/macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "gimp": {
      "command": "uv",
      "args": ["run", "--directory", "/full/path/to/gimp-mcp", "gimp_mcp_server.py"]
    }
  }
}
```

#### Claude Code

```bash
cd /path/to/gimp-mcp
claude  # .mcp.json is auto-detected
```

#### Product CLI (`gimp-agent`)

```bash
uv run gimp-agent doctor --strict --json
uv run gimp-agent probe --json
uv run gimp-agent recipes --json
uv run gimp-agent compare a.png b.png --json
uv run gimp-agent verify out.png --spec spec.json --json
uv run gimp-agent run web-export --param input=in.png --param output=out.png --json
uv run gimp-agent skills validate
uv run gimp-agent skills install --target .grok/skills
```

JSON envelopes use `{ok, exit_code, code, message, data}`. Prefer `--json`, or set
`GIMP_AGENT_JSON=1`. Exit codes bind product `CODE_*` to process exits **0–12** —
see [GIMP_MCP_PROTOCOL.md](GIMP_MCP_PROTOCOL.md).

**Doctor non-strict vs `--strict`:** default `doctor` is diagnostics-only —
required failures may still exit **0** with `ok: false`. Use `--strict` for CI.

---

## Security defaults

See **[SECURITY.md](SECURITY.md)** for threat model, residuals, and full env table.

| Variable | Role | Default |
|---|---|---|
| `GIMP_MCP_HOST` | Bind/connect host | `127.0.0.1` |
| `GIMP_MCP_PORT` | TCP port | `9877` |
| `GIMP_MCP_TOKEN` | Shared session secret | env or auto-generated file |
| `GIMP_WORKSPACE_ROOT` | Path jail for open/save/export | **required** for file ops |
| `GIMP_MCP_ALLOW_EXEC` | Plugin `cmds` + MCP `call_api` | **off** |
| `GIMP_MCP_ALLOW_NON_LOOPBACK` | Non-loopback bind | **off** |
| `GIMP_MCP_ADVANCED_TOOLS` | Full ~90-tool surface | **off** → 30 HL |
| `GIMP_MCP_DEBUG` | Tracebacks only (never a policy bypass) | **off** |

**Posture:** typed tools only; per-message auth; loopback `AF_INET`; workspace
path confinement. `call_api` and plugin-internal arbitrary Python are **disabled
by default**. Stdio MCP does **not** implement HTTP OAuth.

### Advanced: enabling exec (footgun)

```bash
# Only for trusted local experimentation — never for untrusted agents
set GIMP_MCP_ALLOW_EXEC=1   # Windows
export GIMP_MCP_ALLOW_EXEC=1  # POSIX
```

---

## Architecture (short)

```text
AI Client
   │  MCP (stdio)
   ▼
gimp_mcp_server.py     ← HL tools, verify, recipes (host)
   │  TCP JSON  :9877
   ▼
gimp-mcp-plugin.py     ← inside GIMP + 9 shared ship modules
   ▼
GIMP 3.2
```

Full diagram, ship set vs host-only modules, and capability table:
**[docs/architecture.md](docs/architecture.md)**.

---

## Performance

| Surface | Product default |
|---------|-----------------|
| Snapshot max edge (`render_visible_composite`) | **1024** (hard max **4096**) |
| Host TCP command timeout | **60s** (clamp 5–600 via env) |

Prefer region-first detail crops; opt-in `max_size=512` (or other edges) is valid
for intermediate/detail work — it is **not** the product default. Full policy and
env vars: **[docs/performance.md](docs/performance.md)**.

---

## CI and tests

**Offline quality SoT** (matches CI; no GIMP process required):

```bash
uv run pytest -m "not integration and not slow"
```

Also run:

```bash
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
```

Policy, markers, fixtures, and branch protection:
**[docs/ci-and-testing.md](docs/ci-and-testing.md)**.

Scored evaluation corpus and release gates:
**[docs/evaluation.md](docs/evaluation.md)** (`uv run python scripts/run_eval_report.py --offline`).

**Product golden path (preferred):** open → orient → protect → composite →
save/export → host verify. Docs: **[docs/golden-path.md](docs/golden-path.md)**.

| Script | Description |
|---|---|
| [`scripts/golden_path_smoke.py`](scripts/golden_path_smoke.py) | **Golden-path smoke** — default dry-run; `--live` against GIMP plugin (no Class A exec) |
| [`run_tests.py`](run_tests.py) | Legacy — requires exec for some paths, prefer golden-path smoke; optional live harness |
| [`bg_remove_iterative.py`](bg_remove_iterative.py) | Legacy — requires exec, prefer golden-path smoke; iterative BG removal |
| [`bg_remove.py`](bg_remove.py) | Legacy — requires exec, prefer golden-path smoke; simple single-pass BG removal |
| [`agent_edit_demo.py`](agent_edit_demo.py) | Legacy — requires exec, prefer golden-path smoke; full pipeline demo |

> Legacy demos that use plugin `cmds` require `GIMP_MCP_ALLOW_EXEC=1` and a valid
> session token. Prefer **`scripts/golden_path_smoke.py`** for the hardened product
> path. Without advanced exec, Class A demos exit with a friendly `EXEC_DISABLED`
> message.

---

## What can it do?

```
"Remove the background from this image and keep looping until only the character remains"
"Make the character smile — paint a smile arc with teeth over her mouth"
"Open navi_portrait.png, remove the background, verify it's clean, then export as PNG"
"Boost the contrast, shift the hue warmer, then show me a before/after zoom of the face"
```

Agent feedback loop (HL names):

```text
┌─────────────┐
│  Apply edit │
└──────┬──────┘
       ▼
┌──────────────────────────┐
│ render_visible_composite │  ← AI sees live PNG
└──────┬───────────────────┘
       ▼
┌─────────────────┐     ┌──────────────────┐
│ Goal achieved?  │─ No─▶ Adjust & retry   │
└──────┬──────────┘     └──────────────────┘
       │ Yes
       ▼
┌─────────────┐
│   Export    │
└─────────────┘
```

---

## Troubleshooting

### "Could not connect to GIMP"

- GIMP running with an image open?
- **Tools → MCP → Start MCP Server** started?
- Plugin before MCP client? See [operator-runbook `#start-order`](docs/operator-runbook.md#start-order)
- Port 9877 free? Token file present?

### Plugin not visible in GIMP

- Look under **Tools → MCP** (submenu, not a top-level Tools entry)
- Confirm **10/10** ship files in the correct GIMP version plug-ins dir
- After GIMP minor upgrade, reinstall into the new version folder
- On Linux/macOS: `chmod +x` on the plugin script
- Fully restart GIMP after install

### Offline tests

```bash
uv run pytest -m "not integration and not slow"
```

For live tool smoke (requires GIMP + plugin):

```bash
uv run python run_tests.py
```

### Debug mode

```bash
GIMP_MCP_DEBUG=1 uv run --directory /path/to/gimp-mcp gimp_mcp_server.py
```

---

## Example output

<img src="gimp-screenshot1.png" alt="GIMP MCP Example" width="400">

*"Draw me a face and a sheep" — generated entirely through natural language via GIMP MCP*

---

## Known residuals

- Soft encoded-byte ImageContent size guard not yet shipped (prefer region-first + dual-delivery path)
- Headless GIMP on GitHub Actions is **document-only** (Noble apt is GIMP 2.10.x)
- Plugin backup prune CLI (`--prune-backups`) not yet shipped — delete `*.bak.*` manually

Full inventory (security, live/ops, vision, packaging, protocol, tooling, declined):
[docs/known-residuals.md](docs/known-residuals.md).

---

## Contributing

Contributions welcome — bug fixes, tools, docs, examples. Open a PR or issue on
[Ryan-AI-Studios/gimp-mcp](https://github.com/Ryan-AI-Studios/gimp-mcp).

```bash
uv sync --group dev
uv run pre-commit install
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run pytest -m "not integration and not slow"
```

Offline CI, markers, fixtures, branch-protection policy:
[docs/ci-and-testing.md](docs/ci-and-testing.md).

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## License

[GNU GPL v3](LICENSE) — package metadata: `GPL-3.0-only`.
