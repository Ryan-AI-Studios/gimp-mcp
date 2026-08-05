# Grok Build adapter

## Install

1. Copy [config.toml.example](config.toml.example) into:
   - **User:** `%USERPROFILE%\.grok\config.toml` (Windows) / `~/.grok/config.toml`
   - **Project:** `.grok/config.toml` (local only — this repo gitignores `.grok/`)
2. Replace `C:/path/to/gimp-mcp` and `C:/path/to/workspace` with absolute paths
   (forward slashes).
3. Ensure GIMP is open and **Tools → MCP → Start MCP Server**.
4. Verify: `grok mcp doctor gimp` (and `grok mcp doctor gimp --json` if available),
   or `grok mcp list` / `grok inspect`.

Alternative: `grok mcp add` with the same `uv run --directory … gimp_mcp_server.py`
command, server name **`gimp`**.

## Compat with `.mcp.json`

Grok also loads project `.mcp.json` / Claude-compatible MCP configs (lower
priority than `config.toml`). This repo ships `.mcp.json` with server id
**`gimp`**. To ignore Claude-style MCP configs when using config.toml only:

```toml
# optional
[compat.claude]
mcps = false
```

## Skills

MCP wiring is separate from skills. Install the 0020 package:

```bash
uv run gimp-agent skills install --target <skills-root>
```

Grok skills roots typically include `.grok/skills/` and `~/.grok/skills/`, or
`[skills] paths` in config.

## Dual-delivery / vision honesty

`render_visible_composite` emits ImageContent **and** a TextContent JSON mapping.
`session_probe.image_delivery.client_model_visibility` is always `"unknown"`.
If the model does not receive the PNG, open `filesystem_path` from
structuredContent / TextContent via host tools.

## Dual-env (host vs plugin)

`GIMP_WORKSPACE_ROOT` in this config applies to the **host** stdio MCP process
only. Plugin open/save/export/checkpoint jail needs the same (or intended) root
on the **GIMP process**:

```powershell
uv run gimp-agent launch-gui --workspace C:\path\to\workspace
# or: powershell -ExecutionPolicy Bypass -File .\scripts\launch-gimp.ps1 -WorkspaceRoot C:\path\to\workspace
```

After **Tools → MCP → Start MCP Server**, `session_probe.plugin_workspace_root`
should be set; `workspace_root_mismatch` should not be `true`.

## Dual servers (HL + advanced)

[config.toml.example](config.toml.example) keeps a single enabled HL server and a
**commented** `[mcp_servers.gimp-advanced]` block (`enabled = false`,
`GIMP_MCP_ADVANCED_TOOLS = "1"`). Prefer **one** server enabled at a time.
After enable/disable, start a **new Grok session** (tool list cache + stdio
respawn). Product cannot hot-respawn leftover `uv` host processes from inside chat.

## Notes

- Prefer **native Windows** Grok — not WSL — so loopback TCP matches GIMP.
- Do not commit live `.grok/config.toml` with machine-specific paths into shared repos.
- Timeouts 60s startup / 300s tool avoid cold `uv run` failures.
- Host env ≠ plugin env — see dual-env above and
  [operator-runbook dual-env](../../docs/operator-runbook.md#dual-env).
