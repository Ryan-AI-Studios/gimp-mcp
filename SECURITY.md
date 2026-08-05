# Security — GIMP MCP bridge

Hardening for the **GIMP plug-in TCP hop** (not MCP HTTP transports). Product
defaults:

| Control | Default |
|---|---|
| Bind / connect | `127.0.0.1` + `socket.AF_INET` (no bare `localhost`) |
| Session auth | Per-message `"auth"` token (env or auto-generated file) |
| Class A exec | Plugin `cmds` / `python-fu-eval` / `python-fu-exec` **off** |
| Class B exec | MCP `call_api` **hard-fails** unless `GIMP_MCP_ALLOW_EXEC=1` |
| Path jail | All param-driven open/save/export under `GIMP_WORKSPACE_ROOT` |
| Errors | Structured envelope + `code`; ToolError single-line wire; no traceback unless `GIMP_MCP_DEBUG` |
| Audit | Split JSONL: `audit-server.jsonl` + `audit-plugin.jsonl` (join by `request_id`; not a secret, not tamper-evident) |

## Threat model (local)

```
AI host  --stdio MCP-->  gimp_mcp_server.py  --TCP JSON-->  gimp-mcp-plugin.py (in GIMP)
```

| Boundary | Assumption |
|---|---|
| Host ↔ MCP server | Local **stdio** MCP; OS user boundary |
| MCP server ↔ plug-in | Same-machine TCP; unauthenticated clients must fail |
| Plug-in | Typed tools only by default; no agent-reachable unrestricted Python |

### Stdio vs HTTP OAuth

This product’s primary transport is **stdio MCP**. Per the
[MCP Authorization specification (2025-06-18)](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization),
**stdio SHOULD NOT** use HTTP OAuth. Credentials and workspace policy come from
the **environment** (and the local session token file for the TCP hop). This
repo does **not** implement HTTP OAuth for the stdio server.

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
| `GIMP_MCP_DEBUG` | Tracebacks + verbose diagnostics only (never a policy bypass) | **off** |
| `GIMP_MCP_AUDIT_LOG` | Audit directory **or** `.jsonl` file path (sibling `audit-server` / `audit-plugin` names) | platform default dir |
| `GIMP_MCP_ADVANCED_TOOLS` | Full ~90-tool MCP surface (`1`/`true`/`yes`/`on`) | **off** → **28** high-level tools |
| `GIMP_MCP_BATCH_MODE` | Set by host for headless `gimp-console` child only | **off** (GUI plugin start) |
| `GIMP_MCP_BATCH_TIMEOUT_S` | Host wall-clock kill for headless jobs (clamp 15…3600) | `120` |
| `GIMP_MCP_SNAPSHOT_MAX_EDGE` | Default snapshot max edge when agent omits max dims | `1024` |
| `GIMP_MCP_SNAPSHOT_HARD_MAX_EDGE` | Absolute ceiling for any requested snapshot edge | `4096` |
| `GIMP_MCP_COMMAND_TIMEOUT_S` | Host TCP command I/O timeout (clamp 5…600) | `60` |
| `GIMP_MCP_SNAPSHOT_WRITE` | Dual-delivery filesystem snapshot write | **on** when unset |

Snapshot budget and timeout detail (operator tables, agent guidance):
**[docs/performance.md](docs/performance.md)**.

Token file default:

- Windows: `%LOCALAPPDATA%\gimp-mcp\session.token` (best-effort `icacls` user ACL)
- POSIX: `~/.config/gimp-mcp/session.token` mode `0600`

Audit default (split files — avoids Windows sharing locks):

- Windows: `%LOCALAPPDATA%\gimp-mcp\audit-server.jsonl` + `audit-plugin.jsonl`
- POSIX: `~/.local/state/gimp-mcp/audit-server.jsonl` + `audit-plugin.jsonl` (or XDG)

`GIMP_MCP_AUDIT_LOG` rules:

- If set to a path ending in `.jsonl`, use that path's **directory** and write the sibling filenames above.
- If set to a directory, place `audit-server.jsonl` / `audit-plugin.jsonl` under it.
- Host events: `mcp_tool_start` / `mcp_tool_end` (tool, request_id, success, code?).
- Plugin events: existing command / auth / path / command_complete + `request_id`.
- **Never** log tokens, auth secrets, or file bytes.
- Host does **not** call `traceback.print_exc()` on expected tool failures unless `GIMP_MCP_DEBUG=1`.

## Start order

1. Install **10 files** into the GIMP plug-ins folder (same directory):
   `gimp-mcp-plugin.py`, `gimp_mcp_security.py`, `gimp_mcp_snapshot.py`,
   `gimp_mcp_export.py`, `gimp_mcp_handles.py`, `gimp_mcp_coords.py`,
   `gimp_mcp_policy.py`, `gimp_mcp_atomic.py`, `gimp_mcp_filters.py`,
   and `gimp_mcp_tx.py`
   (NDE + agent undo TX helpers; shared install set matches
   `EXPECTED_PLUGIN_FILES` / README).
2. Set `GIMP_WORKSPACE_ROOT` (and optional `GIMP_MCP_TOKEN`) for the GIMP process if needed.
3. Start GIMP → **Tools → MCP → Start MCP Server** (writes/reads token file, binds `127.0.0.1`).
4. Start MCP client / `gimp_mcp_server.py` (lazy token load with retry if file appears late).

Operator checklist with install/doctor detail:
**[docs/operator-runbook.md#start-order](docs/operator-runbook.md#start-order)**.

## Advanced MCP tool surface

Default model-facing surface is **28 high-level tools** (including NDE filter
and undo-group TX tools). Setting `GIMP_MCP_ADVANCED_TOOLS=1` exposes the full
~90-tool footgun surface (legacy filters, low-level selection primitives, `call_api`,
etc.). After flipping this env var, restart the **stdio MCP process and the LLM
client** — clients cache `list_tools` and will keep the old list until re-handshake.

## Advanced exec footgun

`GIMP_MCP_ALLOW_EXEC=1` enables:

- **Class A:** plugin `cmds` / eval / exec routing
- **Class B:** MCP `call_api` (PDB-mediated `pyGObject-console` / eval)

It does **not** disable GIMP’s built-in PDB procedures globally. Startup audit event: `exec_mode_enabled`; exec audits tagged `mode: elevated`.

## Headless batch

Product headless path is a **constrained** `Gimp.BatchProcedure` (`plug-in-gimp-mcp-batch`,
pretty label `gimp-mcp-recipe`) — **not** stock `python-fu-eval` and not arbitrary `-b` Python.

| Control | Behavior |
|---|---|
| Job protocol | Host writes allowlisted JSON job (GIMP_OPS only); rejects freeform keys (`script`/`python`/`eval`/`cmds`/`code`) |
| `--batch-interpreter` | Procedure name only: `plug-in-gimp-mcp-batch` |
| Child env | Host sets `GIMP_MCP_BATCH_MODE=1` + `GIMP_WORKSPACE_ROOT`; **strips** `GIMP_MCP_ALLOW_EXEC` and `GIMP_MCP_TOKEN` |
| Batch-mode plugin init | No token file rotation (avoids invalidating concurrent GUI MCP auth); no MCP TCP server start |
| Result SoT | Sibling `{job}.result.json` — host does **not** parse noisy gimp-console stdout for success |
| Timeout | Host wall-clock kill → structured `TIMEOUT` / exit 9 |

## Residuals (accepted)

| Residual | Notes |
|---|---|
| Same-user token | Any process as the same OS user can read the token file / LOCALAPPDATA |
| Windows reparse points / junctions | Path resolve does not fully neutralize network junctions |
| Audit log | Diagnostics only — not secret, not tamper-evident |
| `GIMP_MCP_DEBUG` | Expands errors/logs only; **never** bypasses bind/auth/exec/path |
| Token file race | MCP server retries; start plugin before heavy MCP traffic |
| Temp / snapshot paths | **Primary:** when `GIMP_WORKSPACE_ROOT` is set, dual-delivery snapshot writes use `{GIMP_WORKSPACE_ROOT}/.gimp-mcp-tmp/snapshots/` (write default **on**). **Fallback:** when workspace is unset, `{tempdir}/gimp-mcp-{pid}/` (and related temp helpers). Permissions **0o700** best-effort where the OS allows. |

Broader product debt (vision, packaging, live/ops, tooling, explicitly declined
work) is inventoried in **[docs/known-residuals.md](docs/known-residuals.md)** —
not security-default bypasses.

## Error codes

| Code | Meaning |
|---|---|
| `AUTH_FAILED` | Missing/wrong token or deprecated unauthenticated string command |
| `EXEC_DISABLED` | Class A or Class B exec without `ALLOW_EXEC` |
| `PATH_DENIED` | Workspace unset or path escapes root |
| `BIND_DENIED` | Non-loopback without `GIMP_MCP_ALLOW_NON_LOOPBACK=1`; bare `localhost` is **always** denied (use `127.0.0.1`) |
| `INTERNAL_ERROR` | Unexpected failure (detail depends on DEBUG); `state_may_have_changed: true` conservative |
| `CONNECTION_FAILED` | Host↔plugin TCP **connect/refuse/reset** or transport failure before a completed command (`retryable: true`). **Not** the host command wall-clock deadline |
| `TIMEOUT` | Host command / I/O **wall-clock deadline** exceeded (e.g. `GIMP_MCP_COMMAND_TIMEOUT_S` or batch timeout). Distinct from `CONNECTION_FAILED`. CLI often maps to exit **9**; `retryable: true` |
| `PARTIAL_MUTATION` | Incomplete mutation (`state_may_have_changed: true`) |
| `ALPHA_LOST` | Export lost alpha; see envelope `details` |

Product envelope (MCP `isError` text) is single-line `CODE: message (request_id=…) | {json}` —
see `GIMP_MCP_PROTOCOL.md`. Do not rely on FastMCP `mask_error_details` alone (no code matrix /
request_id control).
