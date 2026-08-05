# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

**gimp-mcp** (Ryan-AI-Studios fork) is a hardened hybrid bridge: AI clients control
**GIMP 3.2** through MCP over stdio plus a deterministic `gimp-agent` CLI. Product
docs: [README.md](README.md), [docs/architecture.md](docs/architecture.md),
[docs/operator-runbook.md](docs/operator-runbook.md), [SECURITY.md](SECURITY.md).

### Components

1. **GIMP Plugin** (`gimp-mcp-plugin.py` + 9 shared modules): TCP server inside GIMP
2. **MCP Server** (`gimp_mcp_server.py`): stdio MCP host — default **28 high-level tools**
3. **CLI** (`gimp_agent/`): install, doctor, probe, recipes, batch, skills

## Architecture (agent posture)

```text
AI client  --stdio MCP-->  gimp_mcp_server.py  --TCP JSON-->  plugin in GIMP
```

- Bind: `127.0.0.1:9877` with per-message session auth
- Path jail: `GIMP_WORKSPACE_ROOT` required for file tools
- Arbitrary Python-Fu / `cmds` **disabled** by default
- Detail: [docs/architecture.md](docs/architecture.md)

## Primary MCP interface (HL-first)

Prefer the default **28 high-level tools**. Start every session with:

| Tool | Role |
|------|------|
| `session_probe` | Connectivity, surface mode, capabilities, snapshot budget |
| `orient_workspace` | State-manifest orientation SoT (handles, layers, caps) |
| `render_visible_composite` | Visible composite PNG + mapping (default max edge **1024**) |

Other HL tools: open/close/export, selection, checkpoints, recipes, NDE filters,
undo groups, `compare_images`, `verify_artifact`. Full catalog:
[skills/references/hl-tool-catalog.md](skills/references/hl-tool-catalog.md).

Set `GIMP_MCP_ADVANCED_TOOLS=1` on the host MCP process only when you need the
~90-tool advanced surface; restart MCP server **and** client after flipping.

### Advanced exec footgun (`call_api`)

`call_api` is **not** the primary agent interface. It is Class B exec (PDB-mediated
`pyGObject-console` / eval) and **hard-fails** unless `GIMP_MCP_ALLOW_EXEC=1`.
Use only for trusted local experimentation — never for untrusted agents. Prefer
typed HL tools and allowlisted recipes. See [SECURITY.md](SECURITY.md).

## Installation & Setup

### GIMP Plugin Installation

GIMP's per-user config dir is named after its **major.minor** version (`3.0`, `3.2`, …)
and moves on each minor upgrade. Install into the directory matching the installed
GIMP; active path: **Edit > Preferences > Folders > Plug-ins**.

**Primary install** (from repo root after `uv sync`):

```bash
uv run gimp-agent install
uv run gimp-agent doctor --strict --json
```

This copies the full ship set defined by `gimp_agent.paths.EXPECTED_PLUGIN_FILES`
(**10 files**: `gimp-mcp-plugin.py` plus 9 shared modules:
`gimp_mcp_security.py`, `gimp_mcp_snapshot.py`, `gimp_mcp_export.py`,
`gimp_mcp_handles.py`, `gimp_mcp_coords.py`, `gimp_mcp_policy.py`,
`gimp_mcp_atomic.py`, `gimp_mcp_filters.py`, `gimp_mcp_tx.py`). Missing any helper
fails closed at plugin import. Host-only modules (`gimp_mcp_state` / `surface` /
`verify` / recipes) are never deployed into plug-ins. After install, restart GIMP
and start the server from **Tools > MCP > Start MCP Server**.

Start-order checklist: [docs/operator-runbook.md#start-order](docs/operator-runbook.md#start-order).

### MCP Server Configuration

Add to Claude Desktop config (`~/.config/Claude/claude_desktop_config.json` or
`%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "gimp": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/gimp-mcp", "gimp_mcp_server.py"]
    }
  }
}
```

Committed examples: [adapters/](adapters/README.md).

## Development Commands

```bash
uv sync --group dev
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run pytest -m "not integration and not slow"
uv run gimp-agent doctor --strict --json
uv run gimp-agent install --dry-run --json
```

Offline CI / markers: [docs/ci-and-testing.md](docs/ci-and-testing.md).
Snapshot budgets: [docs/performance.md](docs/performance.md).

## GIMP 3.2 API notes (when using elevated exec)

These notes apply only if `GIMP_MCP_ALLOW_EXEC=1` and you are writing Python-Fu
intentionally. Prefer HL tools in normal agent flows.

- Use `Gimp.get_images()` instead of deprecated `Gimp.list_images()`
- Access layers via `image.get_layers()` instead of `Gimp.get_active_layer()`
- Colors: `Gegl.Color.new('color_name')` or `Gegl.Color.new("rgb(1.0, 0.647, 0.0)")`
  (RGB components in 0–1)
- Always call `Gimp.displays_flush()` after drawing operations
- The `gimpfu` module is not available in GIMP 3.2
- API reference: https://developer.gimp.org/api/3.0/libgimp/

### Essential initialization pattern (elevated exec)

```python
images = Gimp.get_images()
image = images[0]
layers = image.get_layers()
layer = layers[0]
drawable = layer
```

### Common operations (elevated exec)

**Drawing a line:**

```python
Gimp.pencil(drawable, [x1, y1, x2, y2])
Gimp.displays_flush()
```

**Setting colors:**

```python
red_color = Gegl.Color.new("red")
Gimp.context_set_foreground(red_color)
```

**Creating shapes:**

```python
Gimp.Image.select_ellipse(image, Gimp.ChannelOps.REPLACE, x, y, width, height)
Gimp.Drawable.edit_fill(drawable, Gimp.FillType.FOREGROUND)
Gimp.Selection.none(image)
Gimp.displays_flush()
```

## Important Notes

- Commands in elevated exec share a persistent Python context — imports/vars persist
- GIMP 3.2 API differs significantly from 2.x
- Always verify results with `render_visible_composite` or host `compare_images`
- Socket connections can fail if the plugin is not started first

## File Structure

- `gimp-mcp-plugin.py`: GIMP plugin + TCP server
- `gimp_mcp_server.py`: MCP host (HL + optional advanced surface)
- `gimp_agent/`: CLI install/doctor/batch/skills
- `docs/architecture.md`: Hybrid architecture SoT
- `docs/operator-runbook.md`: Operator start-order checklist
- `SECURITY.md`: Threat model and env
- `GIMP_MCP_PROTOCOL.md`: Wire/protocol detail
- `README.md`: Public front door
- `docs/best_practices.md` / `docs/iterative_workflow.md`: Legacy agent prompts (prefer skills package)
