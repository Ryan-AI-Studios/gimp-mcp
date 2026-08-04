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

## Notes

- Prefer **native Windows** Grok — not WSL — so loopback TCP matches GIMP.
- Do not commit live `.grok/config.toml` with machine-specific paths into shared repos.
- Timeouts 60s startup / 300s tool avoid cold `uv run` failures.
