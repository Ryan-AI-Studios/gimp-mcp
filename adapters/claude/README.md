# Claude / universal `.mcp.json`

Committed project config: [../../.mcp.json](../../.mcp.json) with server id
**`gimp`**.

This directory’s [mcp.json.example](mcp.json.example) shows the absolute-path
Windows form (`C:/path/to/...` placeholders) for clients that need a full path
instead of `"--directory", "."`.

## Migration

Rename key `gimp-mcp` → **`gimp`** in any older Claude / Cursor / Grok-compat
MCP JSON.

## Notes

- Relative `"--directory", "."` is fine when the client starts with the clone as cwd.
- Set `GIMP_WORKSPACE_ROOT` in the client env or in `mcpServers.gimp.env`.
- Grok may also load this file; prefer `adapters/grok` TOML for timeouts.
