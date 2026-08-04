# Client adapters (Grok / Codex / Claude)

Committed **examples only** — no secrets, no real machine paths. Copy into your
local client config and substitute placeholders.

| Client | Config surface | Example |
|--------|----------------|---------|
| **Grok Build** | `~/.grok/config.toml` or project `.grok/config.toml` (gitignored here) | [grok/](grok/) |
| **Codex** | `~/.codex/config.toml` (user); project `.codex/config.toml` only when **trusted** | [codex/](codex/) |
| **Claude / universal** | Project [`.mcp.json`](../.mcp.json) (server id **`gimp`**) | [claude/](claude/) |
| **Skills (0020)** | `uv run gimp-agent skills install --target <skills-root>` | [`skills/`](../skills/) |

## Server id: `gimp`

All surfaces use the MCP server id **`gimp`** (not `gimp-mcp`):

- TOML: `[mcp_servers.gimp]`
- JSON: `"mcpServers": { "gimp": { ... } }`

**Migration:** if an existing config uses `gimp-mcp`, rename the key to `gimp`.
Grok tool namespacing follows the server id (e.g. `gimp__session_probe`).

## Shared launch pattern (Windows-primary)

```toml
[mcp_servers.gimp]
command = "uv"
args = [
  "run",
  "--directory",
  "C:/path/to/gimp-mcp",   # PLACEHOLDER — absolute clone path
  "gimp_mcp_server.py",
]
enabled = true
startup_timeout_sec = 60
tool_timeout_sec = 300

[mcp_servers.gimp.env]
GIMP_WORKSPACE_ROOT = "C:/path/to/workspace"  # PLACEHOLDER — jail root
PYTHONUNBUFFERED = "1"
```

Rules:

- **Windows TOML paths:** always **forward slashes** (`C:/path/...`) in double-quoted strings.
- **No** API keys, MCP session credentials, or real user-home absolute paths in committed examples.
- Plugin auth uses a **file token** under the GIMP config dir (not client env secrets).
- Codex defaults (startup 10s / tool 60s) are **too low** for `uv run` cold-start — examples set **60 / 300**.
- Codex: `env` **sets** vars; `env_vars` **forwards** host vars (document both in [codex/README.md](codex/README.md)).
- Prefer **native Windows** (PowerShell / Codex / Grok), **not WSL**, so TCP `127.0.0.1:9877` and paths match GIMP.

## Prerequisites

1. Install plugin: `uv run gimp-agent install` then fully quit/relaunch GIMP.
2. Open GIMP → **Tools → MCP → Start MCP Server**.
3. Set `GIMP_WORKSPACE_ROOT` to the project image workspace (path jail).
4. Optional doctor: `uv run gimp-agent doctor --strict` (and `--json` for automation).
5. Skills: `uv run gimp-agent skills install --target <skills-root>` (see 0020 package).

## Image dual-delivery (agents)

`render_visible_composite` returns:

1. **TextContent** — compact JSON mapping (includes `filesystem_path` when write ok)
2. **ImageContent** — PNG vision payload
3. **structuredContent** — same mapping

Default write path: `{GIMP_WORKSPACE_ROOT}/.gimp-mcp-tmp/snapshots/snap-*.png`
(env `GIMP_MCP_SNAPSHOT_WRITE`, default on). Best-effort prune keeps ≤ 50 `snap-*.png`.

**Honest probe:** `session_probe.image_delivery.client_model_visibility` is always
`"unknown"`. Never assume the model saw the PNG. If ImageContent is omitted or
unrendered, open `structuredContent.filesystem_path` (or TextContent JSON) via host tools.

## Manual snapshot prune

```text
# under the workspace jail
{GIMP_WORKSPACE_ROOT}/.gimp-mcp-tmp/snapshots/snap-*.png
```

Delete old `snap-*.png` files as needed (no `gimp-agent prune` verb in this track).

## Checklist

- [ ] Server id `gimp` everywhere
- [ ] Codex timeouts ≥ 60 / 300
- [ ] Forward-slash Windows paths only
- [ ] `GIMP_WORKSPACE_ROOT` set
- [ ] GIMP open + Start MCP Server
- [ ] `session_probe` lists tools / `image_delivery`
- [ ] Skills installed if you use agent playbooks
- [ ] No secrets committed in local copies you share
