# GIMP MCP

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Works with Claude Desktop](https://img.shields.io/badge/Works%20with-Claude%20Desktop-7B2CBF.svg)](https://claude.ai/desktop)
[![GIMP 3.2](https://img.shields.io/badge/GIMP-3.2-orange.svg)](https://gimp.org)
[![MCP Compatible](https://img.shields.io/badge/MCP-Compatible-green.svg)](https://modelcontextprotocol.io)
[![CodeRabbit](https://img.shields.io/badge/CodeRabbit-AI%20Review-171717?logo=coderabbit)](https://coderabbit.ai)

## Demo

![GIMP MCP in action — AI agent driving GIMP through natural language](docs/mcpInAction.gif)

Full demo (with audio): https://github.com/maorcc/gimp-mcp/raw/main/docs/demo.mp4

*AI agent using GIMP MCP to remove a background, edit a character's expression, and verify results — all through natural language via Claude*

---

## Overview

GIMP MCP bridges GIMP's professional image editing capabilities with AI assistants through the [Model Context Protocol](https://modelcontextprotocol.io). It lets you edit images by describing what you want — and gives the AI a live visual feedback channel to verify each change before moving on.

**What makes it different from other GIMP integrations:**

- The AI can *see* the image at any point in the workflow without saving to disk (`render_visible_composite`)
- Supports fully autonomous multi-step pipelines: open → edit → verify → refine → export
- Default **~18 high-level tools** (full ~90-tool advanced surface via env flag)
- Fully compatible with GIMP 3.2.x (all breaking API changes resolved)

## Default high-level tool surface (track 0010)

By default the MCP server lists **~18 high-level tools** (FastMCP `include_tags={"hl"}`).
Set **`GIMP_MCP_ADVANCED_TOOLS=1`** on the **host / stdio MCP process** for the full
~90-tool advanced surface. After flipping the env var, restart the **MCP server
process and the LLM client session** (clients cache `list_tools`).

| Tool | Role |
|---|---|
| `session_probe` | Connectivity + surface mode + capabilities |
| `restart_server` | Drop/reconnect TCP (prefer probe first) |
| `orient_workspace` | State-manifest orientation SoT |
| `select_image` / `select_layers` | Handle bind |
| `open_image` / `close_image` / `new_canvas` | Session lifecycle |
| `ensure_source_immutable` | Source layer policy |
| `checkpoint_create` / `checkpoint_restore` | XCF checkpoints |
| `render_visible_composite` | Visible composite PNG + mapping |
| `normalize_image_orientation` | EXIF normalize |
| `map_preview_to_image` | Preview → image coords |
| `save_xcf` / `export_image` | Atomic XCF/export (temp→replace); collision `fail`\|`version`\|`replace` |
| `verify_alpha_channel` | Alpha preflight |
| `create_selection` | Unified selection (rect/ellipse/by_color/all/none) |

**Migration names (advanced only unless advanced mode):**

| Prefer (default) | Legacy (advanced) |
|---|---|
| `session_probe` | `check_server` |
| `render_visible_composite` | `get_image_bitmap` |
| `create_selection` | `select_rectangle` / `select_ellipse` / `select_by_color` / `select_all` / `select_none` |

## Key Features

| | |
|---|---|
| 👁️ **Live Visual Feedback** | `render_visible_composite` returns a PNG + mapping mid-workflow so the AI verifies each step |
| 🧭 **Workspace Orientation** | `orient_workspace` returns a schema-versioned state manifest (layers tree, kinds, handles, capabilities) |
| 🎨 **~18 HL / ~90 advanced** | Curated default surface; full adjustments, transforms, layers, drawing, filters in advanced mode |
| 🔧 **GIMP 3.2 Compatible** | All GIMP 3.2 API breaks fixed and tested |
| 🔁 **Iterative Workflows** | AI loops until a goal is met — e.g. keeps removing BG until no pixels remain |
| 🖼️ **Region Snapshots** | Zoom into any area for detail verification (face, mouth, corner, etc.) |
| 🔌 **Universal MCP** | Works with Claude Desktop, Claude Code, Gemini CLI, PydanticAI, and more |

## What Can It Do?

### Background Removal with Iterative Verification
The AI removes the background, takes a snapshot to inspect the result, detects remaining pixels, and loops until the image is clean:

```
"Remove the background from this image and keep looping until only the character remains"
```

### Expression Editing
```
"Make the character smile — paint a smile arc with teeth over her mouth"
```

### Complex Multi-Step Pipelines
```
"Open navi_portrait.png, remove the background, verify it's clean,
 then make her smile and export the final result as a PNG"
```

### Color & Tone Work
```
"Boost the contrast, shift the hue 15 degrees warmer, then show me a before/after zoom of the face"
```

### Text & Compositing
```
"Add a bold title at the top in white with a subtle drop shadow, then export for web"
```

---

## Prerequisites

- **GIMP 3.2+** — tested on GIMP 3.2.2 (Windows, macOS, Linux)
- **Python 3.8+** — for the MCP server
- **uv** — Python package manager (`pip install uv`)
- **MCP-compatible AI client** — Claude Desktop, Claude Code, Gemini CLI, PydanticAI, etc.

---

## Quick Start

### 1. Install Dependencies

```bash
git clone https://github.com/maorcc/gimp-mcp.git
cd gimp-mcp
uv sync
```

### 2. Install the GIMP Plugin

Copy **`gimp-mcp-plugin.py`**, **`gimp_mcp_security.py`**, **`gimp_mcp_snapshot.py`**,
**`gimp_mcp_export.py`**, **`gimp_mcp_handles.py`**, **`gimp_mcp_coords.py`**,
**`gimp_mcp_policy.py`**, and **`gimp_mcp_atomic.py`** to GIMP's plug-ins directory
(same folder) and restart GIMP.
The security, snapshot, export, handles, coords, policy, and atomic modules are
stdlib-only and must sit next to the plugin (**8 files** total: plugin + 7 shared modules).
(`gimp_mcp_state.py` / `gimp_mcp_surface.py` are host-side only.)

> **Which directory?** GIMP names its per-user folder after its **major.minor** version
> (`3.0`, `3.2`, `3.4`, …) and creates a fresh one on each minor upgrade, so the folder
> *moves* when you upgrade GIMP (e.g. `3.0` → `3.2`). The snippet below auto-selects the
> newest one, so it keeps working across upgrades. To check the path manually, open GIMP
> and look at **Edit → Preferences → Folders → Plug-ins**.
>
> Launch GIMP at least once before running this, so its config folder exists.

**macOS / Linux:**
```bash
# Pick the base directory for your platform:
BASE="$HOME/Library/Application Support/GIMP"     # macOS
# BASE="$HOME/.config/GIMP"                        # Linux (standard)
# BASE="$HOME/snap/gimp/current/.config/GIMP"      # Linux (Snap)

# Auto-select the newest GIMP 3.x config directory (3.0, 3.2, 3.4, ...):
GIMP_DIR="$(ls -d "$BASE"/3.* 2>/dev/null | sort -V | tail -1)"
if [ -z "$GIMP_DIR" ]; then
  echo "No GIMP 3.x config dir found under $BASE — launch GIMP once, then re-run." >&2
  exit 1
fi
mkdir -p "$GIMP_DIR/plug-ins/gimp-mcp-plugin"
cp gimp-mcp-plugin.py gimp_mcp_security.py gimp_mcp_snapshot.py gimp_mcp_export.py \
  gimp_mcp_handles.py gimp_mcp_coords.py gimp_mcp_policy.py gimp_mcp_atomic.py \
  "$GIMP_DIR/plug-ins/gimp-mcp-plugin/"
chmod +x "$GIMP_DIR/plug-ins/gimp-mcp-plugin/gimp-mcp-plugin.py"
echo "Installed into: $GIMP_DIR/plug-ins/gimp-mcp-plugin"
```

**Windows:**
```text
%APPDATA%\GIMP\<VERSION>\plug-ins\gimp-mcp-plugin\gimp-mcp-plugin.py
%APPDATA%\GIMP\<VERSION>\plug-ins\gimp-mcp-plugin\gimp_mcp_security.py
%APPDATA%\GIMP\<VERSION>\plug-ins\gimp-mcp-plugin\gimp_mcp_snapshot.py
%APPDATA%\GIMP\<VERSION>\plug-ins\gimp-mcp-plugin\gimp_mcp_export.py
%APPDATA%\GIMP\<VERSION>\plug-ins\gimp-mcp-plugin\gimp_mcp_handles.py
%APPDATA%\GIMP\<VERSION>\plug-ins\gimp-mcp-plugin\gimp_mcp_coords.py
%APPDATA%\GIMP\<VERSION>\plug-ins\gimp-mcp-plugin\gimp_mcp_policy.py
%APPDATA%\GIMP\<VERSION>\plug-ins\gimp-mcp-plugin\gimp_mcp_atomic.py
```
Replace `<VERSION>` with your GIMP major.minor (e.g. `3.2`). No chmod needed on Windows. Copy the plugin plus the **seven** shared modules listed above (8 files total) and restart GIMP.

> For all platforms: [GIMP Plugin Installation Guide](https://en.wikibooks.org/wiki/GIMP/Installing_Plugins)

### 3. Start the MCP Server in GIMP

1. Set `GIMP_WORKSPACE_ROOT` to a directory agents may read/write (required for file tools).
2. Open any image in GIMP
3. Go to **Tools > MCP > Start MCP Server**
4. Server binds **`127.0.0.1:9877`** (`AF_INET`) and writes a session token file
   (`%LOCALAPPDATA%\gimp-mcp\session.token` on Windows, `~/.config/gimp-mcp/session.token` elsewhere)

**Start order:** GIMP plugin first (token available) → then MCP client / `gimp_mcp_server.py`
(lazy token load with retry).

### 3b. Product CLI (`gimp-agent`)

The package ships a deterministic host CLI for agents and operators (stdlib
`argparse` — no extra deps). Install entrypoint via `uv sync`, then:

```bash
uv run gimp-agent doctor          # GIMP binary + plug-in files + TCP + workspace
uv run gimp-agent doctor --strict --json   # CI-friendly: fail on required checks
uv run gimp-agent probe --json    # authenticated get_gimp_info round-trip
uv run gimp-agent version --json  # agent + discovered GIMP versions
uv run gimp-agent codes --json    # CODE_* → exit 0–12 map (+ reverse)
```

JSON envelopes use `{ok, exit_code, code, message, data}`. Prefer `--json`, or set
`GIMP_AGENT_JSON=1`. Exit codes bind product `CODE_*` (and CLI-local
`CLI_USAGE` / `GIMP_NOT_FOUND` / `PLUGIN_NOT_FOUND`) to process exits **0–12** —
see [GIMP_MCP_PROTOCOL.md](GIMP_MCP_PROTOCOL.md).

**Doctor non-strict vs `--strict`:** default `doctor` is diagnostics-only — required
check failures still yield process exit **0** and envelope `exit_code: 0` with
`ok: false` and a failure `code` (plus full `data.checks`). Agents must inspect
the `ok` field (or `data.checks`), not only the process exit. Use
`doctor --strict` (often with `--json`) for CI/gating so the first required
failure maps to a non-zero process exit.

**Probe timeouts:** socket/read `TimeoutError` maps to product `TIMEOUT` → process
exit **9** (not transport exit 4). Connection refuse / auth failures remain exit **4**.

**Parent workspace shims vs product CLI:** repos that nest this package under a
parent workspace (e.g. `C:\dev\GIMP\bin\gimp.cmd` / `gimp-console.cmd`) may ship
hardcoded `Program Files\GIMP 3\bin\…` wrappers for local operators. Those shims
are **not** versioned with the `gimp-mcp` package. Prefer
`uv run gimp-agent` / the installed `gimp-agent` entrypoint for path discovery,
doctor, and exit-code contracts.

### 4. Configure Your MCP Client

#### Claude Desktop
`~/.config/Claude/claude_desktop_config.json` (Linux/macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

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

Or manually:
```bash
claude mcp add gimp-mcp -- uv run --directory /full/path/to/gimp-mcp gimp_mcp_server.py
```

#### Gemini CLI
`~/.config/gemini/.gemini_config.json`:
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

#### PydanticAI
```python
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPServerStdio

server = MCPServerStdio(
    "uv", args=["run", "--directory", "/path/to/gimp-mcp", "gimp_mcp_server.py"]
)
agent = Agent("openai:gpt-4o", mcp_servers=[server])
```

---

## Available MCP Tools

### 👁️ Visual Feedback

#### `get_state_snapshot(image_index, max_size, region, label)`
Returns a live PNG of the **visible composite** (all visible layers, opacity, blend
modes — GIMP's canvas projection), not a single top layer. Primary AI feedback
mechanism. Call between edits to verify results without saving to disk. Never mutates
the user's original image.

Also returns MCP **`structuredContent`** mapping metadata (`mode`, `image_index`,
source/rendered sizes, `scale_x`/`scale_y`, `region`, `composite_method`) so agents can
map preview coordinates back to the canvas. When a region is cropped, scales are
**region-relative** (`rendered / region_size`), not full-canvas. EXIF orientation is
out of scope here (track 0008).

```python
# Full image snapshot (default max edge 512)
snapshot = get_state_snapshot(max_size=512)

# Zoom into a face region for detail inspection
snapshot = get_state_snapshot(
    region={"x": 140, "y": 80, "width": 240, "height": 300}, max_size=512, label="face-check"
)
```

This enables iterative agentic workflows: **edit → snapshot → assess → refine → repeat**.

#### `get_image_bitmap(image_index, max_width, max_height, region)`
Lower-level visible-composite bitmap fetch with region extraction and scaling.
Supports `image_index` (default 0). Returns PNG image content plus the same
`structuredContent` mapping as `get_state_snapshot`.

### 🎨 Adjustments
| Tool | Description |
|---|---|
| `adjust_brightness_contrast` | Brightness and contrast |
| `adjust_curves` | Curves by channel (RGB/R/G/B/A) |
| `adjust_hue_saturation` | Hue, saturation, lightness |
| `adjust_color_balance` | Shadows/midtones/highlights color balance |
| `auto_levels` | Auto-stretch levels |
| `desaturate` | Convert to grayscale (keep RGB mode) |
| `invert_colors` | Invert all channels |
| `sharpen` | Unsharp mask sharpening |
| `blur` | Gaussian blur |
| `denoise` | Noise reduction |

### 🔄 Transforms
| Tool | Description |
|---|---|
| `scale_image` | Scale to exact dimensions |
| `scale_to_fit` | Scale within bounding box (aspect-safe) |
| `crop_to_rect` | Crop to rectangle |
| `rotate_image` | Rotate 90/180/270 or arbitrary angle |
| `flip_image` | Flip horizontal or vertical |
| `resize_canvas` | Resize canvas without scaling content |

### ✂️ Selections
| Tool | Description |
|---|---|
| `select_rectangle` | Rectangular marquee |
| `select_ellipse` | Elliptical marquee |
| `select_by_color` | Select by color (global) |
| `select_all` / `select_none` | Select all / deselect |
| `invert_selection` | Invert selection |
| `modify_selection` | Grow, shrink, feather, or border |

### 🗂️ Layers
| Tool | Description |
|---|---|
| `select_image` | Bind active document by **stable image handle** (no new display) |
| `select_layers` | Select layers by **stable item handles** (max 64; floating → `SELECTION_CONFLICT`) |
| `create_layer` | New empty layer (returns `generation` + `handle`) |
| `duplicate_layer` | Duplicate layer (returns `generation` + item `handle`) |
| `delete_layer` | Delete layer (returns `generation` + image `handle`) |
| `rename_layer` | Rename layer (non-structural; generation unchanged) |
| `set_layer_properties` | Opacity, blend mode, visibility |
| `reorder_layer` | Move layer in stack (returns `generation` + item `handle`) |
| `merge_visible_layers` | Merge visible — requires `confirm_destructive=true` |
| `flatten_image` | Flatten all layers — requires `confirm_destructive=true` |
| `ensure_source_immutable` | Protect root sources under `Source_Immutable` (working copies + locks) |
| `checkpoint_create` / `checkpoint_restore` | Workspace-jailed XCF checkpoints + integrity sidecar |
| `list_layers` | List all layers with properties |

**Agent intake order:** `orient_workspace` → `ensure_source_immutable` →
`checkpoint_create` before destructive work → `confirm_destructive=true` for live flatten/merge.

Structural layer tools (`create_layer`, `duplicate_layer`, `delete_layer`, `reorder_layer`,
`merge_visible_layers`, `flatten_image`, `ensure_source_immutable`, plus `add_text` /
`apply_drop_shadow`) return live `generation` and a stable `handle` on success so agents
can refresh stale handles after mutations.

### 🖌️ Drawing & Fill
| Tool | Description |
|---|---|
| `fill_layer` | Fill entire layer with color |
| `fill_selection` | Fill selection (foreground/background/transparent) |
| `fill_rectangle` | Fill a rectangle region |
| `fill_ellipse` | Fill an ellipse region |
| `draw_line` | Draw a line (pencil or paintbrush) |
| `draw_rectangle` | Draw a rectangle outline |
| `draw_ellipse` | Draw an ellipse outline |
| `gradient_fill` | Apply linear or radial gradient |
| `set_colors` | Set foreground/background colors |

### 🔤 Text
| Tool | Description |
|---|---|
| `add_text` | Add a text layer |
| `edit_text` | Edit existing text layer |
| `list_fonts` | List available fonts |

### ✨ Filters & Effects
| Tool | Description |
|---|---|
| `apply_gaussian_blur` | Gaussian blur filter |
| `apply_pixelate` | Pixelate/mosaic effect |
| `apply_emboss` | Emboss effect |
| `apply_vignette` | Vignette darkening |
| `apply_noise` | Add noise/grain |
| `apply_drop_shadow` | Drop shadow effect |

### 📁 File Operations
| Tool | Description |
|---|---|
| `open_image` | Open image file |
| `export_image` | Export PNG/JPEG/WEBP/TIFF (default: preserve alpha; `flatten=False`) |
| `verify_alpha_channel` | Read-only alpha preflight + format capability matrix |
| `batch_export` | Batch export with same flatten/preserve_alpha/verify params |
| `new_canvas` | Create blank canvas |
| `close_image` | Close image |
| `list_images` | List open images |

### 🔍 Info & Context
| Tool | Description |
|---|---|
| `orient_workspace` | **Orientation SoT** — schema-versioned state-manifest v1 (recursive layers, handles, selection, capabilities) |
| `get_image_metadata` | Image size, mode, layers, filename (prefer `orient_workspace` for agents) |
| `get_gimp_info` | GIMP version, platform, capabilities |
| `get_context_state` | Current colors, brush, opacity, mode |
| `get_pixel_color` | Color value at a specific pixel |
| `get_histogram` | Histogram data for a channel |
| `get_selection_bounds` | Current selection bounds |

---

## AI Agent Feedback Loop

The `get_state_snapshot` tool enables a pattern where the AI loops until a goal is visually confirmed:

```text
┌─────────────┐
│  Apply edit │
└──────┬──────┘
       │
       ▼
┌─────────────────┐
│ get_state_      │  ← AI sees live PNG, no disk save needed
│ snapshot()      │
└──────┬──────────┘
       │
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

### Example: Iterative Background Removal

See [`bg_remove_iterative.py`](bg_remove_iterative.py) for a complete example. The AI:

1. Removes the background using edge-seeded contiguous select
2. Takes a snapshot to check the result
3. Scans for remaining background-colored pixels
4. Runs targeted removal passes with progressively finer grids (25px → 1px)
5. Runs a final despeckle pass for isolated pixels
6. Loops until no background pixels remain

---

## Example Scripts

| Script | Description |
|---|---|
| [`run_tests.py`](run_tests.py) | 56-test suite — run against your GIMP to verify all tools work |
| [`bg_remove_iterative.py`](bg_remove_iterative.py) | Iterative BG removal with snapshot checkpoints |
| [`bg_remove.py`](bg_remove.py) | Simple single-pass background removal |
| [`agent_edit_demo.py`](agent_edit_demo.py) | Full pipeline: open → remove BG → edit expression → export |

> **Note:** Demos that use plugin `cmds` require `GIMP_MCP_ALLOW_EXEC=1` and a valid session
> token. They connect to `127.0.0.1` and read the token from env or the default token file.
> Without advanced exec they exit with a friendly `EXEC_DISABLED` message.

Run the test suite to verify your setup:
```bash
python run_tests.py
# Expected: 56/56 PASSED
```

---

## Security defaults

See **[SECURITY.md](SECURITY.md)** for the full threat model and residuals.

| Variable | Role | Default |
|---|---|---|
| `GIMP_MCP_HOST` | Bind/connect host | `127.0.0.1` |
| `GIMP_MCP_PORT` | TCP port | `9877` |
| `GIMP_MCP_TOKEN` | Shared session secret | env or auto-generated file |
| `GIMP_WORKSPACE_ROOT` | Path jail for open/save/export | **required** for file ops |
| `GIMP_MCP_ALLOW_EXEC` | Plugin `cmds` + MCP `call_api` | **off** |
| `GIMP_MCP_ALLOW_NON_LOOPBACK` | Allow non-loopback bind | **off** |
| `GIMP_MCP_DEBUG` | Tracebacks / verbose diagnostics only | **off** |
| `GIMP_MCP_AUDIT_LOG` | Audit dir or `.jsonl` path → split `audit-server` / `audit-plugin` | platform app data |

**Posture:** typed tools only; per-message auth; loopback `AF_INET`; workspace path confinement.
`call_api` and plugin-internal arbitrary Python are **disabled by default**.

**Structured errors (0011):** tool failures are MCP `isError` with a single-line envelope
(`CODE: message (request_id=req_…) | {json}`). Capability `structured_errors: true`.
See [GIMP_MCP_PROTOCOL.md](GIMP_MCP_PROTOCOL.md) recovery table.

### Advanced: enabling exec (footgun)

```bash
# Only for trusted local experimentation — never for untrusted agents
set GIMP_MCP_ALLOW_EXEC=1   # Windows
export GIMP_MCP_ALLOW_EXEC=1  # POSIX
```

This enables Class A (plugin `cmds`/eval) **and** Class B (MCP `call_api` / PDB-mediated exec).
It does not globally disable GIMP’s built-in `python-fu-*` PDB procedures. Prefer typed tools.

---

## Technical Architecture

### Plugin ↔ Server Communication
```text
AI Client (Claude, etc.)
      │  MCP (stdio)
      ▼
gimp_mcp_server.py          ← MCP tool definitions (+ gimp_mcp_state finalize)
      │  TCP JSON  :9877
      ▼
gimp-mcp-plugin.py          ← Runs inside GIMP process (generation registry + orient dump)
  + gimp_mcp_handles.py     ← shared pure require_*/builders (host + plug-in install)
  + gimp_mcp_coords.py      ← pure preview/layer math + EXIF op table (shared install file)
  + gimp_mcp_policy.py      ← Source_Immutable + checkpoint paths/sidecar (shared install file)
  + gimp_mcp_atomic.py      ← collision resolve + same-dir temp/backup paths (shared install)
      │  PyGObject
      ▼
GIMP 3.2 (gi.repository.Gimp)
```

- MCP server translates tool calls into JSON commands sent to the plugin over TCP
- Plugin executes operations directly in the GIMP process via PyGObject
- **`orient_workspace`:** plugin returns a raw dump; host `gimp_mcp_state.finalize_manifest`
  injects capabilities + `session.transport=stdio-proxy` and validates (schema
  `schemas/state-manifest.v1.json`). Prefer this over flat `list_layers` for orientation.
- **Stable handles:** plugin owns per-image generation; `select_image` / `select_layers`
  validate via shipped `gimp_mcp_handles` (STALE_HANDLE / FOREIGN_SESSION / …).
- **Coordinates / EXIF (0008):** snapshot mapping includes `coordinate_space`, axes, padding=0,
  `view_rotation_ignored`, and EXIF honesty flags. Host `map_preview_to_image` /
  `map_image_to_preview` / `map_layer_local_to_image` / `map_image_to_layer_local` are pure
  math (no GIMP). `normalize_image_orientation` defaults to **`assume_pixels_upright`**
  (set tags to 1 only — **never** `Image.policy_rotate`); opt-in `trust_tag` bakes pixels.
- Two message formats: `{"type": "...", "params": {...}}` for named tools, `{"cmds": ["python..."]}` for arbitrary exec

### Transparent PNG export (Issue 16)

`export_image` defaults changed so transparent PNGs are trustworthy:

| Param | Default | Notes |
|---|---|---|
| `flatten` | **`False`** (was True) | Flatten **strips alpha** — do not use for transparent PNG |
| `preserve_alpha` | `None` (auto) | Auto-true for png/webp/tiff; false for jpeg |
| `verify` | `True` | Fail-closed PNG IHDR check → `ALPHA_LOST` if alpha was present and lost |

Export prep always runs on a **duplicate** (user document unchanged). Alpha path uses
`merge_visible_layers(CLIP_TO_IMAGE)` + GIMP 3 `file-*-export` only (no `file-*-save`).
Install must include **`gimp_mcp_export.py`**, **`gimp_mcp_handles.py`**,
**`gimp_mcp_coords.py`**, **`gimp_mcp_policy.py`**, and **`gimp_mcp_atomic.py`**
next to the plugin (8 files total with security/snapshot/plugin).

For intentional opaque bake: `flatten=True` (or `preserve_alpha=False`).
Do **not** confuse with `flatten_image`, which mutates the open document.

### GIMP 3.2 Compatibility Notes

GIMP 3.x introduced breaking API changes from GIMP 2.x. Key fixes included in this release:

| Issue | Fix |
|---|---|
| `layer.copy(False)` → error | `layer.copy()` takes no args in GIMP 3.2 |
| `Gimp.text_fontname()` removed | Use PDB `gimp-text-fontname` |
| `gimp-blend` removed | Use GEGL `gegl:linear-gradient` / `gegl:radial-gradient` |
| `GimpDoubleArray` TypeError in curves | Use `drawable.curves_spline()` directly |
| `Gimp.fonts_get_list()` returns `Font` objects | Convert via `.get_name()` before JSON serialization |
| `image.select_none()` removed | Use PDB `gimp-selection-none` |
| `layer.get_pixel()` returns `Gegl.Color` | Use `.get_rgba()` to extract float components |

---

## Troubleshooting

### "Could not connect to GIMP"
- GIMP must be running with an image open
- Start the MCP server: **Tools > Start MCP Server**
- Check port 9877 is not blocked by firewall

### Plugin Not Visible in GIMP
- Look under **Tools > MCP** (the plugin adds an `MCP` submenu, not a top-level `Tools` entry)
- Confirm the plugin file is in the correct directory (see install steps above)
- **Upgraded GIMP recently?** A minor upgrade (e.g. 3.0 → 3.2) moves the per-user config folder to a new version directory; reinstall the plugin into the new version's `plug-ins` folder. Verify the active path via **Edit > Preferences > Folders > Plug-ins**.
- On Linux/macOS: ensure the file has execute permission (`chmod +x`)
- Restart GIMP after installation
- Check **Filters > Script-Fu > Console** for error messages

### Tests Failing
Run the test suite and check the failure list:
```bash
python run_tests.py
```
Each failure includes the tool name and error — most issues on GIMP 3.2 are covered by the fixes above.

### Debug Mode
```bash
GIMP_MCP_DEBUG=1 uv run --directory /path/to/gimp-mcp gimp_mcp_server.py
```

---

## Example Output

<img src="gimp-screenshot1.png" alt="GIMP MCP Example" width="400">

*"Draw me a face and a sheep" — generated entirely through natural language via GIMP MCP*

---

## Future Enhancements

- **📚 Recipe Collection**: Reusable workflow templates (portrait cleanup, product photo, etc.)
- **↩️ Undo System**: History management and rollback via MCP
- **🚀 Dynamic Discovery**: Auto-generate MCP tools from GIMP's full PDB procedure database
- **🔒 Security**: Sandboxed execution for untrusted command inputs
- **⚡ Performance**: Optimized bitmap transfer for large images
- **🌐 Remote Access**: Network-accessible GIMP instances

---

## Contributing

Contributions are welcome — bug fixes, new tools, documentation, or example scripts. Open a PR or issue on GitHub.

### Development Setup

Install dev dependencies and activate the pre-commit hook so `ruff` runs on every commit:

```bash
uv sync
uv run pre-commit install
```

After this, `ruff` checks staged files on each `git commit` (with `--fix` applied automatically). The same check runs in CI, so the hook is just a fast local safety net.

To bump the pinned hook versions later:

```bash
uv run pre-commit autoupdate
```
