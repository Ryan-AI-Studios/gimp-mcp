# Codex adapter

## Install

1. Merge [config.toml.example](config.toml.example) into **`~/.codex/config.toml`**
   (user scope). On Windows: `%USERPROFILE%\.codex\config.toml`.
2. Replace `C:/path/to/gimp-mcp` and `C:/path/to/workspace` (forward slashes).
3. Confirm **`startup_timeout_sec = 60`** and **`tool_timeout_sec = 300`**
   (Codex defaults 10s / 60s are too low for `uv run` cold-start).
4. Open GIMP → **Tools → MCP → Start MCP Server**.
5. Confirm tools list includes namespaced probe (e.g. `gimp__session_probe`).

### Project config (trusted only)

Project-scoped `.codex/config.toml` is only appropriate when the project is
**trusted** by Codex. Prefer user config for untrusted clones.

## `env` vs `env_vars`

| Key | Meaning |
|-----|---------|
| `env` / `[mcp_servers.gimp.env]` | **Sets** environment variables for the MCP child |
| `env_vars` | **Forwards** named variables from the host process |

Use `env` for fixed workspace paths in the example; use `env_vars =
["GIMP_WORKSPACE_ROOT"]` when the host already exports the jail root.

## Optional hardening

```toml
# under [mcp_servers.gimp]
required = true
default_tools_approval_mode = "writes"
# cwd = "C:/path/to/gimp-mcp"  # alternative to uv --directory
```

## Windows / WSL

Run Codex on **native Windows**. A WSL-side MCP process cannot reliably share
GIMP’s `127.0.0.1:9877` TCP listener or Windows workspace paths.

## Skills

```bash
uv run gimp-agent skills install --target <skills-root>
```

## Dual-delivery / vision honesty

Snapshots return TextContent (JSON mapping) + ImageContent. Never assume the
model rendered the PNG (`client_model_visibility=unknown`). Prefer
`filesystem_path` under `{workspace}/.gimp-mcp-tmp/snapshots/` when visual
proof is mandatory.

## Doctor

```bash
uv run gimp-agent doctor --strict
uv run gimp-agent doctor --strict --json
```
