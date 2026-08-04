# Planner Handoff — gimp-mcp

> **This is a living document.** If you are a planning AI picking up work in this repo and you learn
> something a fresh planner would need — a new gotcha, a policy change, a stale assumption here —
> **update this file before you finish your turn.** Keep it light: link to `conductor/conductor.md`
> and design docs for detail rather than duplicating them here. This file answers
> **"what do I need to know before I touch anything,"** not "what happened."

---

## 1. What this is

**gimp-mcp** is a hardened, agent-facing bridge between AI harnesses (Grok Build, Codex, Claude, etc.)
and **GIMP 3.2** on Windows. It is a fork of [maorcc/gimp-mcp](https://github.com/maorcc/gimp-mcp)
owned at [Ryan-AI-Studios/gimp-mcp](https://github.com/Ryan-AI-Studios/gimp-mcp).

**Product stance:** full hybrid architecture (secure MCP + deterministic CLI sidecar + orientation
manifests + pixel verification + recipes + skills) — **not** an MVP wrap of upstream. Design
authority:

| Doc | Path |
|---|---|
| Deep architecture | `C:\dev\GIMP\docs\CGPT.md` |
| Executive hybrid recommendation | `C:\dev\GIMP\docs\Google.md` |
| This handoff | `docs/planner-handoff.md` (this file) |

Cross-cutting agent tools used across Ryan-AI-Studios repos:

| Tool | Role |
|---|---|
| **Ledgerful** | Repo change intelligence, ledger provenance, `verify` gates |
| **ai-brains** | Cross-session memory, preflight, pinned DECISION/CONSTRAINT/HOTSPOT |

---

## 2. Repo / path map

| Path | What it is |
|---|---|
| `C:\dev\GIMP\gimp-mcp` | **Execution git repo** (this fork). Code, CI, quality gates. |
| `C:\dev\GIMP` | Workspace parent: Grok project MCP (`.grok/`), CLI wrappers (`bin/`), design docs (`docs/`). Not the product git root. |
| `origin` | `https://github.com/Ryan-AI-Studios/gimp-mcp.git` (push target) |
| `upstream` | `https://github.com/maorcc/gimp-mcp.git` (pull / optional PR source) |
| GIMP app | `C:\Program Files\GIMP 3\` (3.2.4+); console: `gimp-console-3.2.exe` |
| Plug-in install | `%APPDATA%\GIMP\3.2\plug-ins\gimp-mcp-plugin\` |

### Gitignored local governance (on purpose)

These are **local-only** (see root `.gitignore`). Do not commit them; do not plan tracks that require
publishing them:

| Path | Purpose |
|---|---|
| `.agents/` | Skills (onboarding, implement, ledgerful, ai-brains, codex-review, gimp-core) |
| `AGENTS.md` | Compact agent contract for this repo |
| `conductor/` | Track registry, specs, plans, deferred debt |
| `.ledgerful/` | Ledgerful state |
| `.env` | ai-brains project/session IDs |

---

## 3. Where things live (read in this order)

1. **`docs/planner-handoff.md`** (this file) — cold-start planner orientation.
2. **`conductor/conductor.md`** — track registry and status SoT (local, gitignored).
3. **`conductor/deferred.md`** — deferred findings to roll into related tracks.
4. **`conductor/<track>/spec.md` + `plan.md`** — what/why/DoD and phased how.
5. **`conductor/templates/0000-Description/`** — skeleton for new tracks; copy, don't freestyle.
6. **`AGENTS.md`** — verify gates, ledger categories, stop rules.
7. **`.agents/skills/onboarding/SKILL.md`** — session start + authority order.
8. **`.agents/skills/implement/SKILL.md`** — track execution loop (TDD → review → full gate).
9. **`C:\dev\GIMP\docs\CGPT.md` / `Google.md`** — full product architecture.
10. **`README.md`**, **`GIMP_MCP_PROTOCOL.md`**, **`docs/best_practices.md`** — upstream-oriented runtime docs.

---

## 4. Track lifecycle

1. **Placeholder** — gap identified; thin `spec.md` with status `Proposed — placeholder`.
2. **Full spec/plan** — research **live** GIMP 3.2 / MCP / tool versions (do not trust training data);
   ground claims in current code; check `deferred.md`. Status → `Ready — not started`.
3. **Execute** — `implement` skill; open ledger TX (`ledgerful ledger start ...`); TDD; review rounds;
   full gate.
4. **Review fold-in** — verify every external AI claim against code before adopting.
5. **Completed** — `review.md` with DoD evidence; flip status in `conductor.md`; commit ledger TX.

Numbering is sequential and stable (`####-PascalDescription`). Check the highest `####-` dir before
minting. Track IDs are creation order, not execution order.

### Current product sequence (summary)

```
0001 Quality gates bootstrap — Completed
  → 0002 Quality surface stabilization — Completed
  → 0003 SecurityHardening — Completed
  → 0004 VisibleCompositeSnapshot — Completed
  → 0005 AlphaExportCorrectness — Completed
  → 0006 StateManifestOrientation — Completed
  → 0007 StableHandleRegistry — Completed
  → 0008 CoordinateModelAndExif — Completed
  → 0009 LayerPolicyAndCheckpoints — Completed
  → 0010 HighLevelMcpSurface — Completed
  → 0011 StructuredErrorsAndAudit — Completed
  → 0012 DeterministicCliSidecar — Completed
  → 0013 AtomicSaveExport — Completed
  → 0014 PixelVerificationProtocol — Completed
  → 0015 RecipeLibrary — Completed
  → 0016 … through 0028 Final product polish (v1)
```

**28 tracks** (0001–0028). **0001–0015 Completed.** Next Ready/placeholder: **0016** NdeFilterTools.
Orientation SoT is `orient_workspace`. Handles **0007**. Alpha **0005**. Composite **0004**.
EXIF **0008**. Policy **0009**. HL surface **0010** → **22** with **0015**. Errors **0011**.
CLI **0012**. Atomic **0013**. Pixel verify **0014**. Recipes **0015**.
Authoritative table: `conductor/conductor.md`.

---

## 5. Quality bar and environment

### One-time / clone setup

```powershell
cd C:\dev\GIMP\gimp-mcp
uv sync --group dev          # creates .venv; pins ruff/basedpyright/pytest
uv run pre-commit install    # optional local hook
ledgerful init               # if .ledgerful missing
ai-brains context            # project .env for vault
```

**Does the uv environment need setup?** On a fresh clone: **yes** (`uv sync --group dev`). On this
machine after initial bootstrap: **already done** — `.venv` exists and imports `gimp_mcp_server`.
Re-run `uv sync` after `pyproject.toml` / `uv.lock` changes or when tools fail with missing packages.

### Full gate (must pass before track finalize)

```powershell
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run pytest -m "not integration and not slow"
ledgerful verify --scope full
```

Pinned tools (as of 2026-08 bootstrap; re-check PyPI when bumping):

| Tool | Pin |
|---|---|
| ruff | 0.16.1 |
| basedpyright | 1.39.9 |
| pytest | 9.1.1 |
| pre-commit | ≥4.6.1 |
| Python | ≥3.11 (CI: 3.13 via uv) |

CI: **`.github/workflows/ci.yml` is the sole quality SoT** (`actions/checkout@v7`,
`astral-sh/setup-uv@v9`, `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true`). Legacy `lint.yml` was
retired in 0002 (no branch protection required the Lint job name). Pre-commit uses
`ruff-check` then `ruff-format` at rev `v0.16.1` (local may `--fix`; CI is check-only).

### Quality surface policy (post-0002)

| Gate | Surface |
|---|---|
| **Ruff lint + format** | Full product Python: `gimp_mcp_server.py`, `gimp-mcp-plugin.py`, demos, `run_tests.py`, `scripts/**`, `tests/**` |
| **basedpyright** | `gimp_mcp_server.py` + `tests/` only |
| **pytest offline** | `tests/` unit tests only (`testpaths = ["tests"]`) |

**Excludes / ignores (justified):**

- **Ruff `extend-exclude`:** `.agents`, `conductor`, `.ledgerful`, `docs`, `*.md` — governance dirs; markdown excluded because `ruff format` rewrites fenced code blocks and we do not format docs via ruff.
- **Plugin per-file:** `E402`, `I`, `RUF001`, `RUF002` on `gimp-mcp-plugin.py` — GIMP bootstrap (`gi.require_version` before `gi.repository`); do **not** reorder imports to silence E402; do **not** bulk-ASCII-ize plugin unicode.
- **Global ruff ignores (deferred cleanup):** `B904` (~79 raise-without-from in server wrappers), `E501` (long tool docstrings). See `conductor/deferred.md`.
- **basedpyright permanent exclude:** `gimp-mcp-plugin.py` (GIMP-embedded `gi.repository` — no host stubs), plus demos/`scripts/**`/`run_tests.py`.
- **Type-hardening dials (load-bearing, not 0002):** `reportAny` / `reportUnknown*` = false for usable `standard` mode with mcp/fastmcp; candidate for a later type-hardening track — do **not** flip to basedpyright `recommended`/`all` casually.

**Runtime deps:** `mcp` / `fastmcp` are unpinned in `pyproject.toml` `dependencies`; **`uv.lock` is the pin SoT**. Optional lower bounds later — do not treat open ranges as unpinned chaos.

**Live scripts** live under `scripts/` (not pytest-collected):

- `scripts/add_text_metadata.py`
- `scripts/continuous_edit_test/` (+ `files/` fixtures)

### Live GIMP (integration only)

1. Restart GIMP after plug-in install.
2. Open an image.
3. **Tools → MCP → Start MCP Server** (loopback `:9877`).
4. Then: `pytest -m integration` or `python run_tests.py` / MCP tools / `scripts/*`.

Offline CI must not require GIMP GUI.

### Ledgerful policy notes

- `.ledgerful/` is **gitignored** (local-only). `rules.toml` must have a **single** `required_verifications` under `[global]` (duplicate keys cause parse fail). Operational verify SoT is `config.toml` `[verify].steps` — keep in sync with CI.
- After editing rules: `ledgerful config verify` and `ledgerful change-context --json` must not show rules TOML parse failures.

---

## 6. Architecture invariants (do not violate casually)

Known upstream defects (must remain visible until fixed by tracks):

| Issue | Symptom | Track |
|---|---|---|
| #17 composite | Snapshot must be visible canvas composite (not top layer) | **0004** (dup+merge+structuredContent; residual NDE/color-mgmt) |
| #16 alpha | “Success” export without transparency | **0005** (Completed: flatten default False; merge-on-dup; file-*-export; PNG IHDR ALPHA_LOST; verify_alpha_channel) |
| Orientation | Agents need schema-versioned workspace model before edit | **0006** (Completed: `orient_workspace` state-manifest v1) |
| Handles | Track by handle not name; STALE after structural mutation | **0007** (Completed: gen registry, select_*, STALE_HANDLE) |
| Coords / EXIF | Preview map + normalize | **0008** (Completed) |
| Source integrity | Source_Immutable + checkpoints + destructive confirm | **0009** (Completed) |
| Default MCP surface | ~18 HL tools; advanced env for full set | **0010** (Completed) |
| Structured errors | Uniform codes, request_id, partial-mutation honesty | **0011** (Completed) |
| Agent CLI sidecar | gimp-agent doctor/probe + CODE_* exit map | **0012** (Completed) |
| Trust boundary | TCP auth + loopback + exec gated + path jail (0003 defaults) | 0003 |
| Tests | Exception-only “pass” without pixel truth | 0014 / 0022 |

Hard rules for product work:

- Prefer **MCP** for interactive orient/edit/snapshot loops; **CLI sidecar** for atomic XCF/export/batch.
- **Orient first:** after 0006, `orient_workspace` is the orientation SoT (not flat `list_layers` alone).
  Re-orient after create/delete/reorder/merge/rasterize/relink. Manifest is **read-only**.
- Never trust `status: success` alone — require composite/alpha/objective checks when vision matters.
- Track layers by **stable handles**, not names (`orient_workspace` + `select_*`; re-orient or use mutator gen after structural ops; STALE_HANDLE on stale gen).
- Spatial edits: use snapshot **mapping** + **0008** coordinate helpers; normalize EXIF before phase-sensitive work.
- **Source integrity (0009):** `ensure_source_immutable` before first mutation; `checkpoint_create`; flatten/live-merge need `confirm_destructive`; restore → re-orient.
- **Default MCP surface (0010):** ~18 high-level tools via real FastMCP `include_tags={"hl"}`; design names (`session_probe`, `render_visible_composite`, `create_selection`); full ~90 tools only with `GIMP_MCP_ADVANCED_TOOLS=1` (restart MCP **and** LLM client). Prefer HL tools; do not major-bump mcp/fastmcp for 3.x visibility API.
- **Structured errors (0011):** product failures → FastMCP `ToolError` **single-line** text (`CODE: msg (request_id=…) | {json}`); never return error dicts as success. Split audit `audit-server.jsonl` / `audit-plugin.jsonl`. `rollback_available` false until **0017**. Parse helper: `parse_tool_error_text`.
- **Agent CLI (0012 Completed):** `uv run gimp-agent doctor|probe|version|codes` with JSON + exit codes from `CODE_*` (0–12). Probe always JSON-auth; doctor connect-only TCP; `--strict` for CI. Non-strict doctor may exit 0 with `ok:false` — check envelope or use `--strict`. Headless recipes not in 0012.
- Prefer non-destructive edits (masks, NDE filters) over flatten/erase.
- **Secure default posture (0003):** typed tools only; Class A `cmds`/eval and Class B
  `call_api` off unless `GIMP_MCP_ALLOW_EXEC=1`; per-message token auth; bind
  `127.0.0.1`/`AF_INET` only; confine all param file I/O to `GIMP_WORKSPACE_ROOT`;
  deploy `gimp_mcp_security.py` beside the plug-in. See `SECURITY.md`.
- Max **3** automatic refine loops; escalate subjective failures to humans.

---

## 7. General gotchas / working discipline

- **Governance is not in git.** `.agents/`, `AGENTS.md`, and `conductor/` are local. Losing the disk
  loses track history unless backed up elsewhere — treat conductor updates carefully; no
  `git checkout -- conductor/` recovery.
- **Never accept AI claims at face value** — spot-check load-bearing GIMP API / security / vision
  claims against live code and current GIMP 3.2 docs.
- **Research currency always.** Pin versions from PyPI / GitHub / GIMP release notes at planning time.
- **Split oversized tracks.** Security, vision, and CLI are separate on purpose; do not merge 0002–0008
  into one mega-PR.
- **Upstream sync:** `git fetch upstream` then intentional merge/rebase onto a feature branch; keep
  hardening divergences documented.
- **Do not push secrets:** `.env`, API keys, vault paths.
- **Ledgerful process policy** allows `uv`, `ruff`, `basedpyright`, `pytest`, `python` in verify steps —
  keep `config.toml` verify steps in sync with CI.
- **WSL vs native:** control native Windows GIMP from native Windows agents; avoid WSL↔Windows TCP/path
  bridging by default.

---

## 8. Session start checklist (planners and implementers)

```powershell
cd C:\dev\GIMP\gimp-mcp
ai-brains preflight --summary
ledgerful doctor
ledgerful audit
ledgerful ledger status --compact
# Read:
#   docs/planner-handoff.md
#   conductor/conductor.md
#   conductor/deferred.md
#   assigned track spec.md + plan.md
```

If indexes are empty: `ledgerful index --incremental`.

---

## 9. Minting a new track

1. Copy `conductor/templates/0000-Description/` → `conductor/####-Name/`.
2. Fill every section of `spec.md` (especially **Definition of Done**).
3. Write phased `plan.md` mapped to DoD items.
4. Register a row in `conductor/conductor.md`.
5. Check `deferred.md` for roll-ins.
6. Update **this file** only if planners need a new durable gotcha or path change.

---

## 10. Snapshot

| Item | Value |
|---|---|
| Date | 2026-08-03 |
| GIMP | 3.2.4 native Windows |
| Fork tip | origin = Ryan-AI-Studios/gimp-mcp |
| Quality gates | full product ruff + format; basedpyright server+tests; offline pytest; ledgerful verify |
| Active focus | **0016-NdeFilterTools** — Proposed (placeholder); prior **0015 Completed** PR #22 / main@51adaeb |
| Track count | 0001–0028 (see conductor.md) |
| Tool pins | ruff 0.16.1, basedpyright 1.39.9, pytest 9.1.1; mcp/fastmcp 1.10.1/2.10.1 (lock) with **pyproject** `mcp>=1.10,<2` and `fastmcp>=2.10,<3`. PyPI has mcp 2.0 / fastmcp 3.4.5 — **do not major-bump** casually. No Pillow. |

### 0005 completion notes (planners)

- **Issue 16 fixed:** default `flatten=False`; auto `preserve_alpha` for png/webp/tiff; always merge-on-dup when preserve_alpha; only `file-*-export`; PNG RGBA8 fail-closed + IHDR `ALPHA_LOST` with `left_on_disk`; host module `gimp_mcp_export.py` + install fourth plug-in file.
- **Breaking:** agents that relied on default flatten for opaque bake must pass `flatten=True`.
- **New tool:** `verify_alpha_channel` (image-level + format capability matrix).
- Residuals: live GIMP matrix (ops); NDE bake on dup → **0016**; SSIM → **0014**; no Pillow.
- Codex final: **PASS WITH DEFERRED P3** (live matrix).

### 0006 completion notes (planners)

- **Completed (PR #5 / main@718b2bb):** `orient_workspace` → state-manifest **v1.0.0**.
- **Architecture:** plugin raw dump → host `finalize_manifest` (transport **`stdio-proxy`**); no fifth install file (plugin does not import `gimp_mcp_state`).
- **Recursive** tree via `_layer_children` (`MAX_LAYER_DEPTH=32` + visited); kinds via **isinstance/gtype**; provisional handles (`generation=1`).
- **selected:** `displays[0]` only; no displays → all `selected: false`.
- **Hygiene:** `get_image_metadata(image_index)` three coordinated edits (server + dispatcher + plugin).
- **Guards:** summary_only count + `_iter_layers_recursive` also depth/visited (Codex P1).
- **OOS still:** CLI orient (**0012**). EXIF normalize → **0008 Ready plan**. Handles/STALE → **0007 Completed**.
- Residuals: live GIMP matrix (ops); large manifest size → **0023**.
- Codex final: **PASS WITH DEFERRED P3** (live matrix).

### 0007 completion notes (planners)

- **Completed (PR #6 / main@33df2b5):** stable handle registry with live generation + select_*.
- **Ship:** `gimp_mcp_handles.py` as 6th plug-in file (host + plugin same module; no mirror).
- **Session:** process-unique `session_epoch` (from `session_id`); FOREIGN_SESSION after plugin restart.
- **Generation:** per-image map; tombstones on close/prune (ID-recycle seed = floor+1); orient syncs open set.
- **Precedence (locked):** shape → epoch → id valid → generation → fingerprint → membership; select_layers pure require before layer-kind.
- **Tools:** `select_image` / `select_layers` (handles only; MAX=64; no Display.new; float → SELECTION_CONFLICT).
- **Mutators:** structural success returns `generation` + `handle`; never bump snapshot/export dups.
- **Capability:** `stable_handle_registry: true`.
- **OOS residuals:** full name ban (**0010**), error envelope (**0011**), CLI exit 5 (**0012**). Tattoos → **0009 partial plan** / **0013**. EXIF → **0008 Completed**.
- Codex final: **PASS WITH DEFERRED P3** (live matrix waiver).

### 0008 completion notes (planners / implementers)

- **Completed (PR #8 / main@8970ad2):** coordinate model + EXIF normalize.
- **Ship:** `gimp_mcp_coords.py` as **7th** plug-in file (pure stdlib; host + plugin).
- **Mapping three-path (H5):** `build_mapping_metadata` + plugin bitmap flatten + server pass-through additive keys (`coordinate_space`, padding=0, `view_rotation_ignored`, EXIF honesty).
- **Tools:** four host `map_*` (both directions; optional declaration validate); **`normalize_image_orientation`**.
- **Default mode:** **`assume_pixels_upright`** (tags→1, no pixel ops — safe after GIMP `policy_rotate` on load). Never call `policy_rotate` for normalize. `trust_tag` opt-in with ordered ops (5/7 flip then rotate).
- **Normalize fail-closed:** direct `image.rotate`/`flip` + gboolean checks; `ops_started` undo; `undo_group_end` False fails closed; both EXIF tags; `METADATA_WRITE_FAILED`; gen bump only on full success.
- **Honesty:** session flag OR tag identity (invalid present tags non-identity; manifest clamps 1..8|null).
- **Capability:** `coordinate_exif_normalized: true`. Rounding half-even. No Pillow.
- **Codex final:** **PASS WITH DEFERRED P3** (live EXIF matrix waived).
- **Residuals:** live matrix + nested offset proof (ops); legacy rotate/flip gen gaps (refactor); CLI ExifTool **0012**; declaration hard gate **0010/0020**.
- **Next:** **0009 Completed** — then **0010** high-level MCP surface (placeholder plan).

### 0009 completion notes (planners / implementers)

- **Completed (PR #10 / main@25e93ba):** Source_Immutable + checkpoints + confirm_destructive.
- **Ship:** `gimp_mcp_policy.py` as **8th** plug-in file (labels, sidecar, integrity sha256).
- **ensure_source_immutable:** copy→insert working→reorder original into parasite-marked group→hide+lock content/position/**visibility**; single end gen-bump; idempotent (working skip + no-op no bump).
- **Guard:** `_resolve_mutable_layer` on all resolve+mutate handlers; durable deny via group ancestry after restart/restore; hydrate on open/orient/restore/ensure.
- **confirm_destructive:** live flatten, merge_visible, free-angle rotate, non-transparent resize fill; `coerce_bool` fail-closed (stringly false + non-scalars).
- **Checkpoints:** XCF via `.partial`+`os.replace` then sidecar; restore new handles + re-orient; tattoos write-only; `close_prior` optional.
- **Capabilities:** `source_immutable_policy` + `checkpoints` true; `atomic_xcf_save` / `atomic_export` **true** (track **0013**).
- **Codes:** POLICY_DENIED, CONFIRM_REQUIRED, CHECKPOINT_*.
- **Codex final:** **PASS WITH DEFERRED P3** (live matrix waived).
- **Residuals:** live matrix (ops); atomic XCF **0013**; tattoo rebind later; auto-ensure on open later.
- **Next:** **0010 Ready** — high-level MCP surface (implement when asked).

### 0010 completion notes (planners / implementers)

- **Completed (PR #12 / main@ee606bc):** default **~18** high-level MCP tools via **real** FastMCP 2.10 `include_tags={"hl"}` (not mcp shim; not FastMCP 3 `enable`).
- **H1 migration:** `from fastmcp import Context, FastMCP` + `instructions=`; drop FuncMetadata ToolResult monkeypatch; keep content `Annotations` + `ToolAnnotations`.
- **Factory:** `create_mcp_server(advanced_mode=…)`; module `mcp = create_mcp_server()`.
- **Advanced:** `GIMP_MCP_ADVANCED_TOOLS=1` (truthy = sec `_env_truthy`) → `include_tags=None` (~94 tools). Restart **MCP process and LLM client**.
- **Design tools:** `session_probe`, `render_visible_composite` (shared composite path), `create_selection` (strict validate + feather; invert/modify stay advanced).
- **Handle-first:** ensure/checkpoint/save/export/verify/close/render; plugin `_resolve_image_from_params`; fail-closed explicit `layer_id` / prior handle.
- **Host-only** `gimp_mcp_surface.py` (catalog, mode, validation) — not a 9th plug-in file.
- **Capability:** `high_level_mcp_surface: true`. Pins: `mcp>=1.10,<2`, `fastmcp>=2.10,<3`.
- **Honesty:** save/export atomic as of **0013**; no HL undo until **0017** (checkpoint_restore / advanced).
- **Codex final:** **PASS**. Live GIMP matrix waived offline (operator checklist in track review/spec).
- **Next:** **0011–0013 Completed** (see later notes).

### 0011 completion notes (planners / implementers)

- **Completed (PR #14 / main@b9d1d23):** structured error envelope v1 + request_id audit correlation.
- **Wire:** FastMCP `ToolError` single-line `CODE: msg (request_id=…) | {json}`; `parse_tool_error_text` multi-candidate (messages may contain ` | `).
- **Helpers:** `tool_fail` / `raise_from_plugin_result` / `raise_from_exception` / `@with_structured_error` on all 94 tools.
- **H1 fixed:** `export_image` raises ToolError on ALPHA_LOST (details.left_on_disk etc.); never success dict.
- **call_api:** EXEC_DISABLED / plugin errors raise ToolError (no false-green string return).
- **request_id:** host contextvar mint; params **copy** + `_request_id`; plugin **pop** → `threading.local`; errors-only on agent wire.
- **Audit split:** `audit-server.jsonl` (host start/end) + `audit-plugin.jsonl` (plugin); join by request_id.
- **M5:** harvest `handle`/`handles` kwargs into `affected_handles` on INTERNAL.
- **Capability:** `structured_errors: true`. Pins hold (mcp/fastmcp no major).
- **Codex final:** **PASS WITH DEFERRED P3** (bare Exception residuals + plugin TCP body without rid).
- **Residuals:** CLI exit map → **0012 Completed**; `rollback_available` true → **0017**; InputRequiredResult/traceparent → post major.
- **Next:** **0012–0013 Completed** (see later notes).

### 0012 completion notes (planners / implementers)

- **Completed (PR #16 / main@691aaa9):** host package `gimp_agent/` + **`gimp-agent`** entrypoint (argparse; no click/typer).
- **Commands:** `doctor [--strict]`, `probe [--timeout]`, `version`, `codes` + `--json` (flag before/after subcommand; flag > `GIMP_AGENT_JSON`).
- **Exit map:** 0 success; **1** generic; **2** CLI_USAGE (help→0); 3 GIMP/plugin missing; 4 AUTH/CONNECTION; 5 handles; 6 policy/path; 7 internal/unmapped; 8 ALPHA_LOST / VERIFY_FAILED; 9 TIMEOUT; 10 PARTIAL; **11 OUTPUT_COLLISION** (0013); 12 UNSUPPORTED. Reverse map via `codes`.
- **doctor:** ordered checks; first required fail under `--strict`; TCP = **connect-only**; EXPECTED_PLUGIN_FILES = 8 (plugin+7 shared, includes `gimp_mcp_atomic.py`); workspace **info**; exiftool `which`; `batch_interpreter: false`.
- **probe:** token + JSON `auth` + `get_gimp_info`; require `status=="success"`; no `gimp_mcp_server` import.
- **Packaging:** `packages = ["gimp_agent"]` explicit + py-modules; ruff/basedpyright include.
- **Codex final:** **PASS** (after P1/P2 fixes: global `--json`, probe success, version rc, env JSON, NaN JSON).
- **Residuals:** live probe matrix (ops); incomplete APPDATA install → **0018**; headless batch → **0019**; recipes → **0015 Completed**; atomic verbs → **0013 Completed**.
- **Next:** **0013** AtomicSaveExport.

### 0013 completion notes (planners / implementers)

- **Completed (PR #18 / main@5763a69):** pure `gimp_mcp_atomic.py` + dual-ship (EXPECTED **8** = plugin+7 shared; pyproject py-modules/isort/basedpyright).
- **Atomic order:** resolve collision → jail final+temp → GIMP write TEMP → size>0 → verify-on-temp (XCF reopen / export IHDR) → sha256 → backup if replace → `os.replace` → cleanup.
- **Collision:** public default `fail` → `OUTPUT_COLLISION` exit **11**; `version` auto-suffix; `replace` namespaced `.gimp-mcp.bak*`; internal batch/icon/web/sprite/social use `replace`.
- **ALPHA_LOST:** verify-on-temp → no replace; `left_on_disk=false` + `final_intact=true`.
- **Codes:** `OUTPUT_COLLISION`→11; `VERIFY_FAILED`→8. Caps `atomic_xcf_save` / `atomic_export` **true**.
- **CLI:** `gimp-agent save-xcf` / `export` (session TCP; plugin down → exit 4).
- **Flat manifests:** save + public export results (no nested `results.status`).
- **Codex final:** **PASS WITH DEFERRED P3** (structure greps vs behavioral replace-spy residual).
- **Residuals:** live GIMP matrix (ops); backup-after-failed-replace; spy tests; full pixel → **0014 Completed**; installer → **0018**.
- **Next:** was **0014** (now Completed).

### 0014 completion notes (planners / implementers)

- **Completed (PR #20 / main@00cc49a):** host-only **`gimp_mcp_verify.py`** (not EXPECTED ship file;
  pyproject py-modules / isort / basedpyright).
- **Decoder:** PNG 8-bit non-interlaced color types 0/2/4/6; **defilter 0–4 incl. Paeth**; reject
  16-bit / palette / interlaced → `UNSUPPORTED` (12).
- **Metrics:** MAE, max_ae, changed_pixels/fraction, alpha transparent counts, **global** luminance
  SSIM (C1/C2; auto off when `w*h > 1e6`; honesty: ≠ ImageMagick windowed SSIM).
- **Budgets:** trusted **50M** / untrusted **25M** pixels; file max **500 MiB**; exceed →
  `POLICY_DENIED` (6). Env: `GIMP_MCP_MAX_DECODED_PIXELS`, `GIMP_MCP_UNTRUSTED_IMAGES`,
  `GIMP_MCP_MAX_VERIFY_FILE_BYTES`.
- **MCP HL catalog 20:** `compare_images` + `verify_artifact` (ok vs pass; path-jailed; both HL).
- **CLI host-only:** `gimp-agent compare` / `verify` — **no** TCP/token/plugin; `--spec` **workspace-jailed**;
  fail → exit **8** (`VERIFY_FAILED`).
- **Caps:** `pixel_verification: true`; **`alpha_snapshot` stays false** (live renderer only).
- **Doctor:** optional `magick` else `compare` info check.
- **Codex final:** **PASS WITH DEFERRED P3** (hand-authored Paeth fixture residual; mitigated by corner tests).
- **Residuals:** windowed SSIM; 16-bit/interlaced/palette; live `render_alpha`; recipes **0015 Completed**; skills **0020**.
- **Next:** **0015 Completed** (PR #22). Next product track **0016**.

### 0015 Completed (PR #22 / main@51adaeb)

- **Status:** **Completed** — Codex final **PASS WITH DEFERRED P3**.
- **Module:** `gimp_mcp_recipes.py` (py-modules + package-data; **not** EXPECTED ship).
- **Recipes:** `gimp_agent/recipes/*.json` — transparent-png, exif-normalize, web-export,
  compare-artifacts, exif-strip (5).
- **MCP HL 22:** `list_recipes` + `apply_recipe`; `recipe_library: true`;
  `batch_interpreter` remains **false** until **0019**.
- **CLI:** `gimp-agent recipes` / `run` / `batch` (append inputs, continue-on-fail;
  bad params exit 2; partial exit 10).
- **Locked:** whole-value `$name`; allowlist ≠ MCP advanced tags; `created_paths` rollback;
  rebind `$output_path` after collision=version; batch_safe + plugin down → UNSUPPORTED.
- **Residuals:** half-set scale skip; live GIMP matrix; headless batch_safe → **0019**.
- **Next:** **0016-NdeFilterTools** (placeholder — needs full plan) or next Ready track.

---

## Changelog (handoff only)

| Date | Change |
|---|---|
| 2026-08-04 | **0015 Completed:** recipe library, HL 22, CLI recipes/run/batch; PR #22 / main@51adaeb; Codex PASS WITH DEFERRED P3; next=0016 |
| 2026-08-04 | **0015 in progress:** recipe registry/runner, HL 22, CLI recipes/run/batch, package-data JSON on feature branch |
| 2026-08-04 | Folded AI-review into **0015**: whole-value `$name`; package-data recipes; created_paths; allowlist≠surface; batch continue-on-fail |
| 2026-08-04 | Full plan **0015** RecipeLibrary: JSON recipes, allowlist runner, HL 22, CLI run/batch; Ready — not started |
| 2026-08-03 | **0014 Completed:** pixel verify metrics, HL 20, host-only CLI compare/verify; PR #20 / main@00cc49a; Codex PASS WITH DEFERRED P3; next=0015 |
| 2026-08-03 | Folded AI-review into **0014**: Paeth defilter; 50M budget; host-only CLI; global SSIM; alpha_snapshot false; magick exit 0\|1 |
| 2026-08-03 | Full plan **0014** PixelVerificationProtocol: stdlib metrics, HL 20, CLI compare/verify, refine helper; Ready — not started |
| 2026-08-03 | **0013 Completed:** atomic save/export, collision, CLI verbs, ship set 8; PR #18 / main@5763a69; Codex PASS WITH DEFERRED P3; next=0014 |
| 2026-08-03 | **0012 Completed:** gimp-agent doctor/probe/exit map; PR #16 / main@691aaa9; Codex PASS; next=0013 |
| 2026-08-03 | Full plan **0012** DeterministicCliSidecar: gimp-agent doctor/probe/exit map; Ready — not started |
| 2026-08-03 | Folded AI-review into **0012**: path names; JSON auth probe; packages explicit; strict first-fail; full ship files; exit 1/help 0 |
| 2026-08-03 | **0011 Completed:** structured errors envelope, ToolError wire, request_id, split audit; PR #14 / main@b9d1d23; Codex PASS WITH DEFERRED P3; next=0012 |
| 2026-08-03 | Full plan **0011** StructuredErrorsAndAudit: envelope v1, ToolError wire, request_id, audit; Ready — not started |
| 2026-08-03 | Folded AI-review into **0011**: H1 export false-green; single-line wire; split audit; thread-local rid; CONNECTION_FAILED; InputRequiredResult/traceparent deferred |
| 2026-08-02 | **0009 Completed:** Source_Immutable, durable guard, confirm_destructive, checkpoints; PR #10 / main@25e93ba; Codex PASS WITH DEFERRED P3; next=0010 |
| 2026-08-02 | Folded AI-review into **0009**: live flatten inventory, central guard, lock_visibility, CONFIRM_REQUIRED, integrity hash |
| 2026-08-02 | Full plan **0009** LayerPolicyAndCheckpoints: Source_Immutable, checkpoints, confirm_destructive; Ready — not started |
| 2026-08-02 | **0008 Completed:** coords, mapping three-path, normalize, map_*; PR #8 / main@8970ad2; Codex PASS WITH DEFERRED P3; next=0009 |
| 2026-08-02 | Folded AI-review into **0008**: default assume_pixels_upright; ordered 5/7; three mapping edits; ship coords; METADATA_WRITE_FAILED |
| 2026-08-02 | Full plan **0008** CoordinateModelAndExif: coords math, EXIF normalize, mapping fields; Ready — not started |
| 2026-08-02 | **0007 Completed:** stable handles, STALE_HANDLE, select_*; PR #6; Codex PASS WITH DEFERRED P3; next=0008 |
| 2026-08-02 | Folded AI-review into **0007**: H1–H5, ship handles module, SELECTION_CONFLICT, immutable fingerprint, no Display.new |
| 2026-08-02 | Full plan **0007** StableHandleRegistry: generation registry, STALE_HANDLE, select_*; Ready — not started |
| 2026-08-02 | **0006 Completed:** orient_workspace state-manifest v1; PR #5; Codex PASS WITH DEFERRED P3; next=0007 |
| 2026-08-02 | Folded AI-review into **0006**: H1–H5 selected/metadata/session/recursive validate/jsonschema lock; host-finalize; stdio-proxy transport |
| 2026-08-02 | Full plan **0006** StateManifestOrientation: orient_workspace manifest v1 Ready — not started |
| 2026-08-02 | **0005 Completed:** Issue 16 alpha export; `gimp_mcp_export.py`; Codex PASS WITH DEFERRED P3; next=0006 |
| 2026-08-02 | Folded AI-review into **0005**: H1–H5 PDB/batch/set_property/internal callers; preflight-gated ALPHA_LOST; force RGBA8; ResolvedExportPolicy |
| 2026-08-02 | Full plan for **0005** AlphaExportCorrectness: Issue 16 root cause = flatten strips alpha; Ready — not started |
| 2026-08-02 | 0004 Completed: Issue 17 composite + ToolResult mapping; PR #3 squash-merged; Codex PASS WITH DEFERRED P3; next=0005 |
| 2026-08-02 | 0004 Issue 17: visible composite via dup+merge; ToolResult structuredContent mapping; install gimp_mcp_snapshot.py |
| 2026-08-02 | Initial planner handoff for gimp-mcp full-product program |
| 2026-08-02 | Expanded conductor to 28 placeholder tracks; 0001 Completed; 0002 stabilization Ready |
| 2026-08-02 | Full plan for 0002: pins ruff 0.16.1 / basedpyright 1.39.9 / pytest 9.1.1; roll-in rules.toml + script layout |
| 2026-08-02 | Folded AI-review.md into 0002: ruff-check hooks, unicode policy, rules apply check, CI dedupe, dead T201, DoD-8 ignores |
| 2026-08-02 | Full plan for 0003 Security: exec off, 127.0.0.1, token auth, path jail, audit; call_api gated; demos guarded |
| 2026-08-02 | Folded AI-review into 0003: Class A/B exec split; exhaustive path jail; AF_INET; env plumbing; auth-first; token ACL; DEBUG scope |
| 2026-08-02 | 0002 quality surface policy: full product ruff; plugin type exclude; uv.lock SoT; lint.yml retired; scripts/ layout |
| 2026-08-02 | 0002 Completed: full surface green; conductor + deferred updated; next=0003 |
| 2026-08-02 | 0003 secure defaults: exec off, 127.0.0.1 AF_INET, token auth, path jail, SECURITY.md |
| 2026-08-02 | 0003 Completed: Class A/B gates, auth-first, path jail, token rotate, Codex final PASS WITH DEFERRED P3; next=0004 |
