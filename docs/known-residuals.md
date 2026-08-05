# Known residuals

Public inventory of **accepted debt**, **ops residuals**, and **explicitly
declined** work after the hybrid product sequence (**0001–0028**). Product
readiness is declared at **0.2.x** — this is **not** a SemVer **1.0.0** public
API freeze.

Status values:

| Status | Meaning |
|---|---|
| `accepted` | Known limitation; safe defaults still hold |
| `future` | Real feature or cleanup later; not in 0.2.x fence |
| `ops` | Operator / environment action; not product code DoD |

Related: [SECURITY.md](../SECURITY.md) (security defaults + accepted local
threats), [docs/performance.md](performance.md), [docs/release.md](release.md),
[docs/ci-and-testing.md](ci-and-testing.md), [docs/golden-path.md](golden-path.md).

---

## Security accepted

| Residual | Status | Operator action |
|---|---|---|
| **Same-user token theft** — any process as the same OS user can read the session token file / LOCALAPPDATA | `accepted` | Run MCP only under a trusted local account; do not share the host with untrusted local processes |
| **Audit log is not a secret** — `audit-server.jsonl` / `audit-plugin.jsonl` are diagnostics only (not tamper-evident) | `accepted` | Do not treat audit JSONL as evidence of non-repudiation; rotate/delete as ops hygiene |
| **Symlink / reparse races** — Windows junctions and reparse points are not fully neutralized by path resolve | `accepted` | Prefer ordinary workspace directories; avoid binding the jail over network junctions |
| **Token file race** — MCP server retries if the plugin has not written the token yet | `accepted` | Start the plug-in server before heavy MCP traffic (see operator-runbook start order) |
| **Temp / snapshot path permissions** — workspace `.gimp-mcp-tmp` primary; system temp fallback; `0o700` best-effort | `accepted` | Keep `GIMP_WORKSPACE_ROOT` set so dual-delivery stays under the workspace jail |

---

## Live / ops

| Residual | Status | Operator action |
|---|---|---|
| **Headless GIMP on GitHub Actions is document-only** — Noble apt is GIMP **2.10.x**, not product **3.2.x** | `ops` | Do not treat apt-GIMP smoke on GA as product green; offline CI remains SoT |
| **Self-hosted Windows GIMP 3.2.4 CI runner** — live/headless BatchProcedure honesty needs native GIMP 3.2 | `ops` / `future` | Optional self-hosted runner; not required for release gates |
| **Per-track live operator matrices** — golden path covers **one** core sequence only | `ops` | Use track review checklists / `docs/ci-and-testing.md` index when validating a surface live |
| **Live golden-path evidence optional** — offline `E-OFFLINE-GOLDEN` is release SoT; `--live` needs plugin up | `ops` | After start-order: `uv run python scripts/golden_path_smoke.py --live` |
| **Local-only `v0.1.0` honesty** — remote product release tag SoT is **`v0.2.0`** | `ops` | Do not push historical `v0.1.0` unless intentionally documenting history; remote SoT remains `v0.2.0` |
| **Branch-protection UI** — require check name **Lint · Format · Types · Tests** | `ops` | Apply in GitHub repo settings (checklist text is product; applying is operator) |
| **Codex re-run when credits return** — several tracks gated on Claude when Codex was rate-limited | `ops` | Optional cross-model re-run; not a product code residual |

---

## Vision / perf

| Residual | Status | Operator action |
|---|---|---|
| **Soft encoded-byte ImageContent guard** — drop ImageContent when PNG exceeds N MiB | `future` | Prefer region-first snapshots + dual-delivery `filesystem_path`; default max edge **1024** covers common cases |
| **Cooperative GEGL cancel** — plugin-side cancel of long merge/export | `future` | Host wall-clock timeout + disconnect only in v1; keep `GIMP_MCP_COMMAND_TIMEOUT_S` sane |
| **Orient huge-stack auto-summary** — hard auto-`summary_only` on large layer counts | `accepted` (declined silent) | Use `summary_only` / skill guidance intentionally; no silent truncate |
| **Live Windows empirical timing tables** — methodology in performance.md; numbers unmeasured | `ops` | Fill bench tables locally if needed; offline gates do not require them |

---

## Packaging / install

| Residual | Status | Operator action |
|---|---|---|
| **`--prune-backups` not shipped** — install leaves `*.bak.*` beside plug-in files | `future` | Delete `*.bak.*` under the plug-in dir manually when reclaiming space |
| **Skills / adapters not in wheel package-data** — source-tree + `GIMP_MCP_SKILLS_ROOT` SoT | `accepted` | Use repo tree or install skills via `gimp-agent skills install` |
| **Auto PyPI publish declined** — no tag-triggered upload | `accepted` | Manual `uv publish` only with credentials and explicit decision |
| **Microsoft Store / MSIX GIMP paths** — traditional APPDATA remains primary | `accepted` | Prefer official GIMP installer paths unless Store layout is verified |

---

## Protocol / product

| Residual | Status | Operator action |
|---|---|---|
| **Tattoo-primary handles** — item_id does not survive XCF save/reopen; tattoos are write-only in checkpoints | `future` | Re-orient after restore/reopen; do not cache item_id across sessions as durable identity |
| **Server-side spatial declaration hard-gate** — skill protocol only; mutators not hard-gated | `future` | Agents/skills should declare coordinate intent; no server refuse yet |
| **Auto ensure_source_immutable on open** — agent/skill duty, not automatic on every `open_image` | `accepted` | Call `ensure_source_immutable` before first mutation (router / gimp-edit sequence) |
| **PROTOCOL full narrative rewrite** — spot consistency only | `future` | Prefer HL catalog + architecture for agent orientation |
| **Prune-snapshots CLI verb** — dual-delivery has helper prune; no dedicated CLI | `future` | Manual prune under `.gimp-mcp-tmp/snapshots/` if needed |
| **Host vs plugin env (dual-env)** — `GIMP_WORKSPACE_ROOT` on host MCP alone does not jail plugin checkpoints | `accepted` (absorbed) | Use `uv run gimp-agent launch-gui` or `scripts/launch-gimp.*`; verify with `session_probe.plugin_workspace_root` |
| **HL cutout gap** — default surface selects but cannot fill/clear to transparent | `accepted` (absorbed **0030**) | Use HL `get_selection_bounds` + `clear_selection_to_transparent` (empty → `SELECTION_EMPTY`); hard subject isolation → **0032** |
| **PNG export drawable failures** — live `EXPORT_FAILED` / `file-png-export` property errors | `future` | Retry after re-orient/checkpoint; placeholder **0031** |
| **Subject isolation quality** — color-select poor on soft ghosts; rembg worked out-of-band | `future` | Host rembg pipeline optional; placeholder **0032** (not a required product dep) |

---

## Tooling / deps

| Residual | Status | Operator action |
|---|---|---|
| **mcp / fastmcp major hold** — lock **1.10.1** / **2.10.1**; pins `<2` / `<3` | `accepted` | Do not major-bump casually; re-plan migration separately |
| **B904 / E501 global ignores** — exception-chaining and long docstrings flood if enabled | `future` | Dedicated refactor track if ever; not a polish pass |
| **Type-hardening dials load-bearing** — `reportAny` / `reportUnknown*` false for usable standard mode | `accepted` | Do not flip basedpyright to `recommended`/`all` casually |
| **pytest DeprecationWarning ignore global** — may hide noise from deps | `accepted` | Narrow later if needed |

---

## Explicitly declined for sequence

Items **not** delivered by the 0001–0028 product sequence and **not** planned as
0028 scope. May be reconsidered in a future operator-minted track.

| Residual | Status | Notes |
|---|---|---|
| **SemVer 1.0.0 stable API freeze** | declined | Readiness at **0.2.x** ≠ MAJOR; formal freeze is a separate decision |
| **Live GIMP required in default CI** | declined | Offline quality job remains sole required gate |
| **Marketing video / re-record demo.mp4** | declined | Existing demo media is illustrative only |
| **Hatchling build-backend rewrite** | declined | setuptools flat layout held |
| **Auto PyPI on tag** | declined | Manual publish residual only |
| **Skills wheel package-data** | declined | Source-tree discovery remains SoT |
| **Closing every historical live operator matrix as product DoD** | declined | Golden path + offline gates are sequence SoT |
| **Upstream maorcc PR of full fork surface** | declined | Optional later; not sequence DoD |
| **Soft ImageContent guard in 0028** | declined (inventory only) | See Vision / perf |
| **`--prune-backups` in 0028** | declined (inventory only) | See Packaging / install |
| **mcp 2.x / fastmcp 3.x+ / 4.x beta migration in 0028** | declined | See Tooling / deps |
