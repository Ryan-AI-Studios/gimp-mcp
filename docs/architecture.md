# Architecture — GIMP MCP

Product architecture for the **Ryan-AI-Studios** fork of gimp-mcp: a hardened
hybrid bridge between AI harnesses and **GIMP 3.2**. This document is the
user-facing architecture SoT (no internal track IDs).

## Hybrid model: MCP + CLI

| Path | Role | When to use |
|------|------|-------------|
| **MCP (stdio)** | Interactive agent loop: orient → edit → vision → verify | Live sessions with Claude, Grok, Codex, etc. |
| **CLI (`gimp-agent`)** | Deterministic install, doctor, recipes, batch, host-only compare/verify | CI, scripts, headless jobs, operators |

Both share the same security posture (loopback TCP + session token + workspace
path jail) and the same high-level capability surface. Prefer **typed HL tools**
and allowlisted recipes over arbitrary execution.

## Process diagram

```text
AI client (Claude / Grok / Codex / …)
      │  MCP over stdio
      ▼
gimp_mcp_server.py          ← host process: HL tools, verify, recipes, surface
      │  TCP JSON  127.0.0.1:9877  (session auth token)
      ▼
gimp-mcp-plugin.py          ← runs inside GIMP (PyGObject)
      │
      ▼
GIMP 3.2 (gi.repository.Gimp)
```

**CLI sidecar** (same host package): `gimp-agent` talks to the plugin when a
session is required, or drives constrained headless `gimp-console` batch via
`plug-in-gimp-mcp-batch`. Host-only commands (`compare`, `verify`, parts of
`doctor`) never open the plugin socket.

## Ship set vs host-only modules

### EXPECTED plugin ship set (10 files)

Deployed by `uv run gimp-agent install` into the GIMP user plug-ins directory:

| File | Role |
|------|------|
| `gimp-mcp-plugin.py` | Plugin entry, TCP server, command dispatch |
| `gimp_mcp_security.py` | Auth, path jail, bind policy |
| `gimp_mcp_snapshot.py` | Visible-composite capture + budgets |
| `gimp_mcp_export.py` | Alpha-preserving export prep |
| `gimp_mcp_handles.py` | Stable handle builders / validators |
| `gimp_mcp_coords.py` | Preview/layer math + EXIF op table |
| `gimp_mcp_policy.py` | Source_Immutable + checkpoint paths |
| `gimp_mcp_atomic.py` | Collision resolve + atomic temp/replace |
| `gimp_mcp_filters.py` | NDE op allowlist + config helpers |
| `gimp_mcp_tx.py` | Agent undo-group TX helpers |

Missing any helper fails closed at plugin import. After install, fully quit and
relaunch GIMP, then **Tools → MCP → Start MCP Server**.

### Host-only (never copied into plug-ins)

| Module / package | Role |
|------------------|------|
| `gimp_mcp_server.py` | FastMCP stdio server + tool wiring |
| `gimp_mcp_surface.py` | HL vs advanced tool surface |
| `gimp_mcp_state.py` | State-manifest finalize / capabilities |
| `gimp_mcp_verify.py` | Host PNG compare / artifact gates |
| `gimp_mcp_recipes.py` | Recipe catalog + apply orchestration |
| `gimp_agent/` | CLI, install, doctor, batch, skills pack |

## User-facing capabilities

| Capability | What it gives you | Where to read more |
|------------|-------------------|--------------------|
| **Default HL surface (28 tools)** | Curated agent tools: probe, orient, open/export, selection, vision, recipes, NDE, undo groups | [hl-tool-catalog](../skills/references/hl-tool-catalog.md), [protocol](../GIMP_MCP_PROTOCOL.md) |
| **Live vision** | `render_visible_composite` — visible canvas PNG + mapping (default max edge **1024**) | [performance.md](performance.md) |
| **Workspace orientation** | `orient_workspace` state-manifest (layers, handles, capabilities) | [protocol](../GIMP_MCP_PROTOCOL.md) |
| **Stable handles** | Bind images/layers by handle; STALE after structural mutation | [protocol](../GIMP_MCP_PROTOCOL.md) |
| **Atomic save / export** | Temp→replace; collision `fail` / `version` / `replace`; alpha-preserving PNG by default | [protocol](../GIMP_MCP_PROTOCOL.md) |
| **Pixel verification** | `compare_images` / `verify_artifact` (MCP) and `gimp-agent compare` / `verify` (CLI) | [protocol](../GIMP_MCP_PROTOCOL.md) |
| **Recipe library** | Versioned allowlisted multi-step recipes (`list_recipes` / `apply_recipe`, CLI `run` / `batch`) | [protocol](../GIMP_MCP_PROTOCOL.md) |
| **Non-destructive filters** | `apply_nde_filter` / `edit_filter_config` / `remove_nde_filter` | [protocol](../GIMP_MCP_PROTOCOL.md) |
| **Undo groups** | `undo_group_begin` / `end` / `rollback` for multi-step agent transactions | [protocol](../GIMP_MCP_PROTOCOL.md) |
| **Install & doctor** | `gimp-agent install` / `doctor --strict` for the 10-file ship set | [operator-runbook.md](operator-runbook.md) |
| **Headless batch** | Constrained `plug-in-gimp-mcp-batch` (not freeform `python-fu-eval`) | [operator-runbook.md](operator-runbook.md) |
| **Agent skills** | Portable skill pack under `skills/` | [skills/README.md](../skills/README.md) |
| **Client adapters** | Grok / Codex / Claude examples under `adapters/` | [adapters/README.md](../adapters/README.md) |
| **Security hardening** | Loopback bind, session token, Class A/B exec off, path jail, structured errors, audit JSONL | [SECURITY.md](../SECURITY.md) |

## Default vs advanced tool surface

- **Default:** FastMCP lists **28** high-level tools (`include_tags={"hl"}`).
- **Advanced (~90 tools):** set `GIMP_MCP_ADVANCED_TOOLS=1` on the **host stdio
  MCP process**, then restart the MCP server **and** the LLM client session
  (clients cache `list_tools`).
- **Exec footgun:** `call_api` and plugin `cmds` / eval remain **off** unless
  `GIMP_MCP_ALLOW_EXEC=1`. Prefer HL tools and recipes.

## Explicit non-goals (not product)

| Non-goal | Why |
|----------|-----|
| Arbitrary agent `cmds` / unrestricted Python by default | Security: Class A/B exec is opt-in only |
| Stock `python-fu-eval` as the headless path | Product uses constrained `plug-in-gimp-mcp-batch` |
| GIMP 2.10 smoke on GitHub Actions as a quality gate | Noble apt GIMP is 2.10.x — API-incompatible with 3.2 |
| Marketing site beyond README | Docs live in-repo |
| Publishing local governance (`conductor/`, `.agents/`) | Gitignored; not product surface |

## Related docs

| Doc | Role |
|-----|------|
| [operator-runbook.md](operator-runbook.md) | Start-order checklist, install, live ops |
| [SECURITY.md](../SECURITY.md) | Threat model, env, error codes, residuals |
| [performance.md](performance.md) | Snapshot budgets and command timeouts |
| [ci-and-testing.md](ci-and-testing.md) | Offline CI SoT, markers, branch protection |
| [release.md](release.md) | Release checklist, version triple, build and tag |
| [evaluation.md](evaluation.md) | Scored offline eval corpus and release gates |
| [GIMP_MCP_PROTOCOL.md](../GIMP_MCP_PROTOCOL.md) | Wire protocol and tool detail |
| [README.md](../README.md) | Public front door and quick start |
