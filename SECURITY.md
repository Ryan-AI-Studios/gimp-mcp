# Security — GIMP MCP bridge

Hardening for the **GIMP plug-in TCP hop** (not MCP stdio itself). Defaults after track 0003:

| Control | Default |
|---|---|
| Bind / connect | `127.0.0.1` + `socket.AF_INET` (no bare `localhost`) |
| Session auth | Per-message `"auth"` token (env or auto-generated file) |
| Class A exec | Plugin `cmds` / `python-fu-eval` / `python-fu-exec` **off** |
| Class B exec | MCP `call_api` **hard-fails** unless `GIMP_MCP_ALLOW_EXEC=1` |
| Path jail | All param-driven open/save/export under `GIMP_WORKSPACE_ROOT` |
| Errors | Structured `code`; no traceback unless `GIMP_MCP_DEBUG` |
| Audit | JSONL diagnostics (not a secret, not tamper-evident) |

## Threat model (local)

```
AI host  --stdio MCP-->  gimp_mcp_server.py  --TCP JSON-->  gimp-mcp-plugin.py (in GIMP)
```

| Boundary | Assumption |
|---|---|
| Host ↔ MCP server | Local stdio MCP; OS user boundary |
| MCP server ↔ plug-in | Same-machine TCP; unauthenticated clients must fail |
| Plug-in | Typed tools only by default; no agent-reachable unrestricted Python |

References:

- [MCP Authorization (2025-06-18)](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)
- MCP Security Best Practices — *Local MCP Server Compromise* (stdio preferred; alternative transports need auth + restricted access)

## Environment

| Variable | Role | Default |
|---|---|---|
| `GIMP_MCP_HOST` | Bind/connect | `127.0.0.1` |
| `GIMP_MCP_PORT` | Port | `9877` |
| `GIMP_MCP_TOKEN` | Shared secret | env, else generated file |
| `GIMP_MCP_TOKEN_FILE` | Override token path | platform default below |
| `GIMP_WORKSPACE_ROOT` | Path jail root | **required** for file ops (fail-closed) |
| `GIMP_MCP_ALLOW_EXEC` | Class A + Class B exec | **off** |
| `GIMP_MCP_ALLOW_NON_LOOPBACK` | Non-loopback bind | **off** |
| `GIMP_MCP_DEBUG` | Tracebacks + verbose diagnostics only | **off** |
| `GIMP_MCP_AUDIT_LOG` | Audit JSONL path | platform default |

Token file default:

- Windows: `%LOCALAPPDATA%\gimp-mcp\session.token` (best-effort `icacls` user ACL)
- POSIX: `~/.config/gimp-mcp/session.token` mode `0600`

Audit default:

- Windows: `%LOCALAPPDATA%\gimp-mcp\audit.jsonl`
- POSIX: `~/.local/state/gimp-mcp/audit.jsonl` (or XDG)

## Start order

1. Install **both** `gimp-mcp-plugin.py` **and** `gimp_mcp_security.py` into the GIMP plug-ins folder.
2. Set `GIMP_WORKSPACE_ROOT` (and optional `GIMP_MCP_TOKEN`) for the GIMP process if needed.
3. Start GIMP → **Tools → MCP → Start MCP Server** (writes/reads token file, binds `127.0.0.1`).
4. Start MCP client / `gimp_mcp_server.py` (lazy token load with retry if file appears late).

## Advanced exec footgun

`GIMP_MCP_ALLOW_EXEC=1` enables:

- **Class A:** plugin `cmds` / eval / exec routing  
- **Class B:** MCP `call_api` (PDB-mediated `pyGObject-console` / eval)

It does **not** disable GIMP’s built-in PDB procedures globally. Startup audit event: `exec_mode_enabled`; exec audits tagged `mode: elevated`.

## Residuals (accepted)

| Residual | Notes |
|---|---|
| Same-user token | Any process as the same OS user can read the token file / LOCALAPPDATA |
| Windows reparse points / junctions | Path resolve does not fully neutralize network junctions |
| Audit log | Diagnostics only — not secret, not tamper-evident |
| `GIMP_MCP_DEBUG` | Expands errors/logs only; **never** bypasses bind/auth/exec/path |
| Token file race | MCP server retries; start plugin before heavy MCP traffic |
| Temp / snapshot paths | Bitmap temp files under system temp (not workspace) are intentional |

## Error codes

| Code | Meaning |
|---|---|
| `AUTH_FAILED` | Missing/wrong token or deprecated unauthenticated string command |
| `EXEC_DISABLED` | Class A or Class B exec without `ALLOW_EXEC` |
| `PATH_DENIED` | Workspace unset or path escapes root |
| `BIND_DENIED` | Non-loopback / bare `localhost` without override |
| `INTERNAL_ERROR` | Unexpected failure (detail depends on DEBUG) |
