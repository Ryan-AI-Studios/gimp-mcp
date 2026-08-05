# Golden path

End-to-end operator story for the hardened hybrid product: open → orient →
protect source → light typed edit → visible composite → atomic save XCF →
export PNG → host pixel verify — with a **scripted smoke** and offline honesty.

This page is the product SoT for the golden path. Offline eval case
**E-OFFLINE-GOLDEN** is a **host-only** verify/compare/recipes path
(`tests/test_offline_e2e.py`); it is **not** a live plugin open/orient/save
smoke. Live plugin evidence uses `scripts/golden_path_smoke.py --live`.

---

## Preconditions

Follow **[operator-runbook.md#start-order](operator-runbook.md#start-order)**
before live smoke:

1. Ship set installed (`uv run gimp-agent install`) — EXPECTED **10** files.
2. GIMP **3.2.4+** fully quit and relaunched after install.
3. `GIMP_WORKSPACE_ROOT` set to a writable project directory.
4. **Tools → MCP → Start MCP Server** (loopback TCP `:9877`, session token).
5. Optional sanity: `uv run gimp-agent doctor --strict --json` and
   `uv run gimp-agent probe --json`.

The smoke script **never** requires `GIMP_MCP_ALLOW_EXEC`, Class A `cmds`,
`python-fu-eval`, or `call_api`.

---

## Golden path steps (product story)

Ordered steps with **plugin TCP wire names** used by the smoke script:

| Step | Product story | Smoke wire (`type`) | MCP HL alias (docs / agents) |
|---:|---|---|---|
| 1 | Probe | `get_gimp_info` | `session_probe` |
| 2 | Open | `open_image` | `open_image` |
| 3 | Orient | `orient_workspace` | `orient_workspace` |
| 4 | Protect source | `ensure_source_immutable` | same |
| 5 | Checkpoint (persisted XCF) | `checkpoint_create` | same |
| 6 | Optional light edit | `select_all` → `select_none` | HL `create_selection` (host maps; **not** a wire) |
| 7 | Visible composite | **`get_image_bitmap` only** | HL `render_visible_composite` (host wraps bitmap) |
| 8 | Save XCF | `save_xcf` | same |
| 9 | Export PNG | `export_image` | same |
| 10 | Host verify | N/A (host `gimp_mcp_verify`) | `verify_artifact` / `compare_images` |
| 11 | Evidence | write `evidence.json` | — |

**Hard rule:** the smoke script sends **plugin TCP wire names only** via
`send_authenticated_command`. It **never** sends `create_selection` or
`render_visible_composite` as plugin `type` (those are host MCP HL tools that
map to different wire types on the server).

Composite proof is **non-status**: decode base64 PNG from `get_image_bitmap`,
write `composite.png`, assert bytes/dims; then host-verify the export PNG.
Never treat `status: success` alone as composite success.

---

## Hybrid surface table

| Surface | Role on this path | Names |
|---|---|---|
| **Plugin TCP (smoke)** | Live GIMP ops over authenticated loopback | Wire: `get_gimp_info`, `open_image`, `orient_workspace`, `ensure_source_immutable`, `checkpoint_create`, optional `select_all`/`select_none`, `get_image_bitmap`, `save_xcf`, `export_image` |
| **MCP stdio HL (agents)** | Same product story via FastMCP tools | HL aliases: `session_probe`, `create_selection`, `render_visible_composite`, … (28 default tools) |
| **Host-only** | Pixel/metadata gates without GIMP | `verify_artifact`, `compare_images` (`gimp_mcp_verify`) |
| **CLI** | Hybrid operator verbs | `gimp-agent probe` / `doctor` / `save-xcf` / `export` / `compare` / `verify` |

---

## Commands

### Dry-run (default — no socket)

Default mode when neither `--live` nor `GIMP_MCP_LIVE=1` is set. Validates
fixture presence, **requires workspace**, prints the **wire-name** step plan,
exits **0**. No plugin TCP I/O.

```powershell
$env:GIMP_WORKSPACE_ROOT = "C:\path\to\workspace"
uv run python scripts/golden_path_smoke.py --dry-run
# equivalent default:
uv run python scripts/golden_path_smoke.py
```

### Live smoke

```powershell
# After start-order (plugin server up, workspace set)
$env:GIMP_MCP_LIVE = "1"
uv run python scripts/golden_path_smoke.py --live
# JSON stdout envelope; evidence.json always written under jailed out-dir
uv run python scripts/golden_path_smoke.py --live --json
```

Flags:

| Flag | Default | Notes |
|---|---|---|
| `--dry-run` | on unless live | Fixture + workspace + wire plan; no socket |
| `--live` | off | Full path; also enabled by `GIMP_MCP_LIVE=1` |
| `--workspace` | `GIMP_WORKSPACE_ROOT` | Jail root (required even for dry-run) |
| `--out-dir` | `<workspace>/output/golden-path` | Must resolve **under** workspace jail |
| `--timeout` | `60` | Seconds; clamp **5–600** |
| `--json` | off | Stdout envelope only; live still writes `evidence.json` |

### Optional pytest integration

Live test is marked `@pytest.mark.integration` and **skipped** unless
`GIMP_MCP_LIVE=1` (default CI stays green without GIMP):

```powershell
$env:GIMP_MCP_LIVE = "1"
uv run pytest -m integration
```

Default offline gate (does **not** run integration):

```bash
uv run pytest -m "not integration and not slow"
```

---

## Offline vs live honesty

| Path | What it proves | Gate |
|---|---|---|
| **E-OFFLINE-GOLDEN** | Host `verify_artifact` / `compare_images` / recipe ids on fixtures | Offline **release** gate — see [evaluation.md#release-gates](evaluation.md#release-gates) |
| **Dry-run smoke** | Fixture + workspace config + documented wire plan | Local / CI-friendly structure |
| **Live smoke** | Real plugin open → … → export → host verify + `evidence.json` | Operator / optional `@integration` |

Do not claim E-OFFLINE-GOLDEN replaces live plugin smoke.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| **AUTH_FAILED** (exit **4**) | No session token / wrong token | Start MCP Server in GIMP; check `GIMP_MCP_TOKEN` / token file |
| **CONNECTION_FAILED** (exit **4**) | Plugin not listening on `:9877` | Start-order: plugin first, then clients; free the port |
| **PATH_DENIED** / jail | Workspace unset or `--out-dir` outside jail | Set `GIMP_WORKSPACE_ROOT`; keep out-dir under workspace |
| **TIMEOUT** (exit **9**) | Slow/hung plugin | Raise `--timeout` (max 600); check GIMP UI |
| GIMP version rejected | Live probe &lt; **3.2.4** | Upgrade GIMP; product baseline is 3.2.4+ |
| Dry-run fails missing workspace | Jail required even offline | Set `--workspace` or `GIMP_WORKSPACE_ROOT` |

Exit codes for AUTH/CONNECTION use `gimp_agent.exit_codes` (transport/auth → **4**).

---

## Evidence (`evidence.json`)

On live runs the smoke always writes schema_version **1** under the jailed
out-dir (default `<workspace>/output/golden-path/evidence.json`):

- `schema_version`, `product`, `version`, `started` / `ended` (ISO-8601)
- `gimp_version`, `plugin_version`
- `steps[]` with per-step ok
- `artifacts`: `checkpoint`, `composite_png`, `xcf`, `export_png`
- `export_verification` (host verify fields)
- `overall`: `PASS` / `FAIL`

Artifacts are left in place; committed fixtures under `tests/fixtures/` are
**never** mutated (smoke copies into the workspace).

---

## Related docs

- [operator-runbook.md#start-order](operator-runbook.md#start-order) — install and start order
- [evaluation.md#release-gates](evaluation.md#release-gates) — offline release gates / E-OFFLINE-GOLDEN
- [release.md](release.md) — release checklist + live residual pointer
- [ci-and-testing.md](ci-and-testing.md) — markers, `GIMP_MCP_LIVE`, offline CI SoT
- [architecture.md](architecture.md) — hybrid MCP + CLI design
- Skills: [skills/gimp/SKILL.md](../skills/gimp/SKILL.md), [skills/README.md](../skills/README.md)
- Script: [`scripts/golden_path_smoke.py`](../scripts/golden_path_smoke.py)
