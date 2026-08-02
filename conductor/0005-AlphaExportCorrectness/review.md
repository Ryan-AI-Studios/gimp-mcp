# 0005 — Alpha Export Correctness — Review

- **Branch:** `feature/0005-alpha-export-correctness`
- **Date:** 2026-08-02
- **Ledger TX:** `52b27598-1a1b-4082-abb8-5b231ae616c5` (BUGFIX) — orchestrator owns commit
- **Status:** Implementation complete; offline gates green; live GIMP matrix deferred (checklist below)

---

## What was implemented

1. **`gimp_mcp_export.py`** (new host module, stdlib-only, deploy next to plugin)
   - `ResolvedExportPolicy`, `resolve_export_policy`, `normalize_format`
   - `PDB_EXPORT` — only `file-*-export` names
   - PNG IHDR parse (`png_ihdr_info`, `file_has_alpha_channel`) reusing snapshot signature
   - Result builders with `left_on_disk`, `png_color_type`, `property_errors`, etc.
   - Phase-0 PNG pixel-format candidates documented from GNOME `file-png.c`

2. **Plugin rewrite** (`gimp-mcp-plugin.py`)
   - Fail-closed import of `gimp_mcp_export as _exp`
   - `_export_to_path` returns rich dict; prep only on duplicate; merge when preserve_alpha; flatten when opaque bake
   - Deleted `file-*-save` map
   - PNG RGBA8 set via candidates; critical props not bare-pass
   - Preflight-gated ALPHA_LOST with file left on disk
   - `_export_image` defaults: `flatten=False`, `preserve_alpha=None`, `verify=True`; accepts `file_type` alias
   - `_batch_export` reads flatten/preserve_alpha/verify (no hardcode flatten True)
   - `_verify_alpha_channel` + command router dispatch
   - Internal callers (icons, web, sprite, social) use `flatten=True` + `file_size_bytes` from result

3. **Server** (`gimp_mcp_server.py`)
   - `export_image` / `batch_export` param parity; breaking flatten default False
   - New tool `verify_alpha_channel`

4. **Tests** `tests/test_export_alpha.py` — policy matrix, synthetic PNG type 2/6/16-bit, greps, internal-caller policy

5. **Docs** — README install (4 files) + Issue 16; SECURITY/CLAUDE; PROTOCOL alpha contract; best_practices

6. **pyproject** — py-modules, known-first-party, basedpyright include

---

## DoD checklist

| ID | Status | Evidence |
|---|---|---|
| DoD-1 Default PNG no flatten when preserve_alpha | **pass** | policy auto + plugin merge path |
| DoD-2 merge-on-dup single/multi | **pass** | always merge when preserve_alpha |
| DoD-3 ALPHA_LOST preflight-gated; opaque → not_applicable | **pass** | host + plugin verify |
| DoD-4 verify_alpha_channel richer | **pass** | has_alpha, layers, format matrix |
| DoD-5 success/error metadata + left_on_disk | **pass** | builders + plugin |
| DoD-6 only file-*-export; no -save | **pass** | grep tests |
| DoD-7 batch_export parity | **pass** | server + plugin |
| DoD-8 offline tests green | **pass** | 122 pytest offline |
| DoD-9 full gate | **pass offline** | ruff/basedpyright/pytest; ledgerful deferred to orchestrator |
| DoD-10 docs + review.md | **pass** | this file + docs updates |
| DoD-11 manual GIMP matrix | **waived offline** | checklist below |
| DoD-12 Phase-0 property names recorded; no swallow critical | **pass** | see below |

---

## Phase 0 property name notes

From GNOME GIMP master `plug-ins/common/file-png.c` (`EXPORT_PROC` / `file-png-export`):

| Item | Value |
|---|---|
| Property | `"format"` (`gimp_procedure_add_choice_argument`) |
| RGBA8 choice id | `"rgba8"` (`PNG_FORMAT_RGBA8`) |
| Other ids | auto, rgb8, gray8, graya8, rgb16, gray16, rgba16, graya16 |

Module constants also try fallbacks: props `pixel-format`, `png-format`; values `RGBA8`, `rgb-alpha`, `RGBA`, int `3`.

Live dump via `get_config_properties()` on installed 3.2.4 was not run in this environment (GIMP may be available but not required for offline gate). Residual: if a build renames the choice API, `property_errors` surfaces set failures rather than silent success.

---

## Residual risks / deferred

| Item | Notes |
|---|---|
| Live GIMP smoke | Manual matrix below not executed in this session |
| WEBP/TIFF alpha verify | IHDR is PNG-only; non-PNG alpha trust is PDB path + preflight, not post-file parse |
| NDE filters | Merge on dup bakes visible filter output; original NDE stays live → **0016** |
| tRNS indexed | Deferred; force RGBA8 avoids type-3 false negatives |
| Atomic export | **0013** |
| SSIM/pixel protocol | **0014** |
| Degraded `file_save`/`file_overwrite` | Logged; still runs verify when preserve_alpha |
| Choice property via GI | Some GI builds may need enum object not string — candidate int `3` tried; live confirm recommended |

---

## Manual test matrix (operator checklist)

| # | Case | Expect |
|---|---|---|
| 1 | Single-layer RGBA cutout, `export_image` defaults | PNG color type 6; success; alpha_verified true |
| 2 | Multi-layer transparency, defaults | Composite alpha preserved; type 6 |
| 3 | Opaque RGB single layer, defaults | success; alpha_verified=`not_applicable` |
| 4 | `flatten=True` (or preserve_alpha=False) | opaque success; no ALPHA_LOST |
| 5 | Force RGB path / broken format prop (if injectable) | ALPHA_LOST + left_on_disk |
| 6 | JPEG + preserve_alpha=True | ALPHA_UNSUPPORTED_FORMAT |
| 7 | flatten=True + preserve_alpha=True | POLICY_CONFLICT |
| 8 | Icon / sprite / social internal paths | opaque OK |
| 9 | User image layer count / dirty after export | unchanged |
| 10 | `verify_alpha_channel` | has_alpha + matrix |

**Offline waiver:** Live GIMP not exercised in implementer session; gates above cover pure logic + source greps.

---

## Offline verification commands

```text
uv run pytest tests/test_export_alpha.py -q          → 40 passed
uv run ruff check gimp_mcp_export.py gimp_mcp_server.py gimp-mcp-plugin.py tests/test_export_alpha.py tests/test_security_policy.py
uv run basedpyright                                  → 0 errors
uv run pytest -m "not integration and not slow"    → 122 passed
```

---

## Files touched (summary)

| Path | Action |
|---|---|
| `gimp_mcp_export.py` | **created** |
| `tests/test_export_alpha.py` | **created** |
| `gimp-mcp-plugin.py` | export rewrite + verify tool + callers |
| `gimp_mcp_server.py` | export/batch defaults + verify tool |
| `pyproject.toml` | module wiring |
| `tests/test_security_policy.py` | multi-line `_export_to_path` regex |
| `README.md`, `SECURITY.md`, `CLAUDE.md`, `GIMP_MCP_PROTOCOL.md`, `docs/best_practices.md` | install + alpha contract |
| `docs/planner-handoff.md` | Issue 16 progress note |
| `conductor/0005-AlphaExportCorrectness/review.md` | this file |

---

## Internal review round 1

- **Reviewer:** Grok Build (strict code review, read-only except this section)
- **Date:** 2026-08-02
- **Scope:** `feature/0005-alpha-export-correctness` vs main intent; `spec.md` §2 algorithm / §2.7 contracts / §7 DoD; `plan.md`; key files + tests + install docs
- **Gates re-run this session:** not executed (no shell in this review worker). Implementer-reported: `tests/test_export_alpha.py` 40 passed; offline pytest 122; ruff/basedpyright clean — treat as **reported**, not re-observed
- **Verdict:** **NOT CLEAN** — one **medium** correctness gap (group preflight) plus test/contract nits; core algorithm otherwise solid. Medium blocks clearance per track policy

### Findings

| id | severity | description | files | required_fix | status | evidence |
|---|---|---|---|---|---|---|
| IR1-01 | medium | Preflight alpha and `verify_alpha_channel` only walk top-level `image.get_layers()` / selected layers — **no recursion into layer groups** (`get_children`). Spec §2.3: *any visible drawable has_alpha*. If alpha exists only inside a group, `preflight_has_alpha=False` → IHDR verify and RGBA8 force are **skipped** → can report success / `alpha_verified=not_applicable` even when a transparent stack loses alpha on export (false trust). Same hole for `layers_with_alpha` / image-level `has_alpha`. | `gimp-mcp-plugin.py` (`_preflight_has_alpha`, `_verify_alpha_channel`) | Recurse visible group children (or equivalent drawable walk) for preflight and verify tool; add offline note/test expectation | **fixed_pending_verification** | Added `_layer_children` + `_iter_layers_recursive`; preflight uses `visible_only=True`; verify collects names recursively; tests `test_preflight_has_alpha_walks_groups_recursively`, verify body grep |
| IR1-02 | medium | MCP `export_image` **discards structured error payload** required by §2.7 (`left_on_disk`, `png_color_type`, `property_errors`, etc.). Plugin returns full `build_export_error` dict; server raises `Exception(f"{code}: {err}")` then **re-wraps** as `export_image failed: …`. Agents on the primary MCP path cannot machine-read `left_on_disk` / `png_color_type`. TCP raw path keeps structure. DoD-5 partial for MCP surface. | `gimp_mcp_server.py` `export_image` | On export errors, return structured dict without double-wrap loss | **fixed_pending_verification** | On `status==error` with `code`, **return** full result dict (keeps `left_on_disk`, `png_color_type`, `property_errors`); generic errors still raise; test `test_server_export_image_returns_structured_error_dict` |
| IR1-03 | low | Alpha-critical PNG pixel-format set total failure is **soft**: `_set_png_rgba8_format` appends `property_errors` and export still runs. Fail-closed depends entirely on post-export IHDR when `verify` stays on. Spec H3/DoD-12 require surface (met) not necessarily abort; residual if `verify=False` or non-PNG. | `gimp-mcp-plugin.py` `_set_png_rgba8_format` | Optional: hard fail or keep soft + document; ensure `verify` default remains True | open (deferred — soft continue OK if IHDR catches; verify default True) | Returns `False` after loop; caller ignores return value; IHDR path still fail-closed when verify on |
| IR1-04 | low | Drawable critical path uses bare `except Exception:` on first `drawable` attempt (no log), then `drawables`; on preserve_alpha failure only `property_errors` and **continues** `proc.run` without drawable (snapshot path refuses to run without drawable). | `gimp-mcp-plugin.py` `_export_to_path` | Log first failure; fail-closed when `preserve_alpha` and drawable unset | **fixed_pending_verification** | Logs first drawable failure; when `preserve_alpha` and not `drawable_set` → `EXPORT_FAILED` (no `proc.run`); test `test_export_drawable_fail_closed_when_preserve_alpha` |
| IR1-05 | low | Tests: policy matrix + IHDR 8/16 + greps exist, but **missing** (a) source grep that icons/web/sprite/social pass `flatten=True`, (b) assert preserve_alpha branch is not paired with unguarded `.flatten(`, (c) color_type 4 gray-alpha fixture, (d) coverage for MCP structured-error surfacing. | `tests/test_export_alpha.py` | Add greps for internal callers + type-4 + optional server error-shape test | **fixed_pending_verification** | `test_internal_callers_pass_flatten_true`, `test_png_ihdr_gray_alpha_type4`, structured-error + recursion greps |
| IR1-06 | low | Raw TCP `preserve_alpha` / `verify` use `bool(x)` when not None → `bool("false") is True` (stringly JSON footgun). MCP schema uses real bools. | `gimp-mcp-plugin.py` `_export_image` / `_batch_export` | Strict parse: only accept `bool` / `None` (or explicit true/false/1/0) | open | `if preserve_alpha is not None: preserve_alpha = bool(preserve_alpha)` |
| IR1-07 | low | Live Phase-0 `get_config_properties()` dump and DoD-11 GIMP matrix not executed (implementer waived). Property names from GNOME `file-png.c` are recorded; residual GI/choice binding risk. | `gimp_mcp_export.py` constants; `review.md` Phase 0 | Operator matrix when GIMP available; optional runtime prop dump | open | review.md: “Live dump … was not run” |
| IR1-08 | low | Docs hygiene incomplete for completion: `docs/planner-handoff.md` still says 0005 “Ready (full plan); implement when asked”; conductor “In progress” (expected mid-track). Not a code defect. | `docs/planner-handoff.md`, `conductor/conductor.md` | Update on finalize (DoD-10) | open | handoff “Active focus \| 0005 … Ready” |

### Non-findings (verified OK)

| Area | Evidence |
|---|---|
| Policy: never flatten when preserve_alpha; JPEG+preserve → `ALPHA_UNSUPPORTED_FORMAT`; flatten+preserve True → `POLICY_CONFLICT`; flatten True + preserve None → opaque, `verify=False` | `resolve_export_policy` in `gimp_mcp_export.py` + matrix tests |
| Always merge-on-dup when preserve_alpha; prep only on `duplicate()`; `finally: dup.delete()` | `_export_to_path` ~1961–1996, 2228–2233 |
| No `file-*-save` PDB names in export path; map is `file-*-export` only; degraded `Gimp.file_save`/`file_overwrite` logged | `PDB_EXPORT`; grep of `_export_to_path`; tests |
| ALPHA_LOST preflight-gated; leaves file; `left_on_disk=true` on plugin wire | ~2175–2203 |
| Opaque source → `alpha_verified="not_applicable"` | ~2205–2206 + host helper tests |
| Path jail still on export/batch/export_to_path; exec not re-enabled by this track | `_jail_path` call sites; GEGL exec still `_sec.exec_allowed()` |
| batch_export server+plugin param parity (`flatten`/`preserve_alpha`/`verify`) | server ~893–932; plugin ~2395–2440 |
| Internal callers pass `flatten=True` (icons, web, sprite, social) | ~4411, 4459–4460, 4607, 4673 |
| `verify_alpha_channel` richer fields + dispatch + server tool | plugin ~2473–2516, dispatch ~512–513; server ~947–973 |
| Install docs list `gimp_mcp_export.py` (4 files) | README/SECURITY/CLAUDE |
| Alpha-critical image/file set_property fail hard | `_set_export_property_critical` required=True → early `EXPORT_FAILED` |
| pyproject wires export module | `py-modules`, isort, basedpyright include |

### DoD status (internal review)

| DoD | Status | Notes |
|---|---|---|
| **DoD-1** Default PNG no flatten when preserve_alpha; auto preserve capable formats | **met** | policy + plugin merge path |
| **DoD-2** Multi/single-layer merge-on-dup | **met** | always merge when preserve_alpha (groups merged; preflight incomplete — IR1-01) |
| **DoD-3** ALPHA_LOST preflight-gated; opaque → not_applicable | **partial** | logic correct for top-level layers; group-nested alpha can skip verify (IR1-01) |
| **DoD-4** `verify_alpha_channel` richer than metadata | **partial** | fields present; group walk missing same as preflight |
| **DoD-5** Success/error metadata + `left_on_disk` | **partial** | full on plugin/TCP; MCP tool raises and drops structured fields (IR1-02) |
| **DoD-6** Only `file-*-export`; ban `file-*-save` | **met** | code + offline greps |
| **DoD-7** batch_export server+plugin parity | **met** | params wired both sides |
| **DoD-8** Offline tests + greps + internal-caller policy | **partial** | strong policy/IHDR/greps; weak internal-caller source greps (IR1-05); **not re-run** this session |
| **DoD-9** Full gate (ruff/format/basedpyright/pytest/ledgerful) | **partial** | implementer-reported offline green; ledgerful/orchestrator gates not observed here |
| **DoD-10** Docs + review + deferred + conductor Completed + ledger | **partial** | docs mostly updated; handoff/conductor finalize pending orchestrator |
| **DoD-11** Manual GIMP matrix | **missing** (waived offline) | checklist only |
| **DoD-12** Phase-0 property names; no swallow critical | **partial** | names recorded from source; live dump missing; pixel-format soft-fail (IR1-03); image/file hard-fail OK |

### Blocking for clearance

1. **IR1-01 (medium)** — group-aware preflight / verify_alpha → **fixed_pending_verification**
2. **IR1-02 (medium)** — MCP structured ALPHA_LOST / error contract for DoD-5 → **fixed_pending_verification**

Lows IR1-04/05 fixed opportunistically; IR1-03 soft RGBA8 continue deferred (IHDR still fail-closed); IR1-06/07/08 open. DoD-9/10/11 remain orchestrator/operator.

### Fix round (IR1-01/02) — 2026-08-02

- **IR1-01:** `_layer_children` + `_iter_layers_recursive` on plugin; `_preflight_has_alpha` / `_verify_alpha_channel` use recursive walk.
- **IR1-02:** MCP `export_image` returns structured `status=error` + `code` payload (incl. `left_on_disk`) instead of raise-only.
- **IR1-04:** preserve_alpha path fail-closed without drawable set.
- **IR1-05:** greps + type-4 PNG fixture + structured-error source test.
- **Gates:** re-run offline suite after this fix (see commands below).

### Summary for orchestrator

**Pending verification of IR1-01/02 fixes.** Core Issue 16 path remains; medium findings addressed in code + offline greps. Re-review should confirm recursive walk + MCP structured error return, then re-run gates. Residual opens: IR1-03 (soft pixel-format), IR1-06 (stringly bool), IR1-07 (live matrix), IR1-08 (docs finalize).

---

## Internal review round 2

- **Reviewer:** Grok Build (strict re-review after fix commit; read-only except this section)
- **Date:** 2026-08-02
- **Branch / HEAD:** `feature/0005-alpha-export-correctness` @ `9d73a44368675688887f89edbea0da111f3fdf12`
- **Scope:** Prior IR1 mediums + optional lows; fix commit vs main; `spec.md` §2.3 / §2.7 / §7 DoD; plugin/server/export module + `tests/test_export_alpha.py`
- **Fix commit message:** `fix(export): recursive alpha preflight + structured MCP errors` (IR1-01/02/04/05)
- **Branch commits (main..HEAD, from reflog):**
  1. `0c633d75` feat(export): add gimp_mcp_export host module + offline alpha tests
  2. `cb342baa` fix(export): alpha-preserving merge-on-dup + file-*-export + verify tool
  3. `4bc2c7ac` docs: Issue 16 alpha export defaults, install gimp_mcp_export.py
  4. `9d73a443` fix(export): recursive alpha preflight + structured MCP errors
- **Gates this session:** **pytest not re-executed** (no shell in this review worker). Static verification of production code + offline greps/tests source. Treat implementer offline green as **reported**, not re-observed. Orchestrator should re-run:
  ```text
  uv run pytest tests/test_export_alpha.py -q
  uv run pytest -m "not integration and not slow"
  ```
- **Verdict:** **CLEAN** — both prior **medium** blockers **verified_fixed**; no new findings above **low**. Residual lows only (deferred / finalize / live matrix).

### Prior finding dispositions

| id | severity | status | evidence |
|---|---|---|---|
| IR1-01 | medium | **verified_fixed** | `gimp-mcp-plugin.py`: `_layer_children` (is_group + get_children/get_layers), `_iter_layers_recursive` (DFS; `visible_only` skips invisible nodes and does not descend), `_preflight_has_alpha` uses recursive walk with `visible_only=True` + selected-layer walk; `_verify_alpha_channel` collects `layers_with_alpha` via recursive walk (`visible_only=False`) and ORs `_preflight_has_alpha`. Offline: `test_preflight_has_alpha_walks_groups_recursively`, `test_verify_alpha_channel_handler_exists` asserts `_iter_layers_recursive` in verify body. Spec §2.3 “any visible drawable” addressed for preflight gating of IHDR/RGBA8. |
| IR1-02 | medium | **verified_fixed** | Plugin `_export_image` returns structured `build_export_error` dicts top-level on `status==error` (not re-wrapped). Server `export_image` (~888–889): when `status==error` **and** `code` present → **`return result`** (full payload: `left_on_disk`, `png_color_type`, `property_errors`, `code`). Docstring documents structured-return contract. Old `raise Exception(f"{code}: {err}")` pattern absent. Offline: `test_server_export_image_returns_structured_error_dict`. TCP raw path unchanged (already structured). DoD-5 for primary MCP `export_image` surface met. |
| IR1-03 | low | **open** (deferred) | `_set_png_rgba8_format` still soft-continues after total failure (appends `property_errors`, export proceeds). Fail-closed still depends on post-export IHDR when `verify` default True. Acceptable residual; no regression. |
| IR1-04 | low | **verified_fixed** | When `policy.preserve_alpha` and not `drawable_set` → `EXPORT_FAILED` before `proc.run` with clear message; first drawable failure logged. Offline: `test_export_drawable_fail_closed_when_preserve_alpha`. |
| IR1-05 | low | **verified_fixed** | Tests added: recursive greps, type-4 gray-alpha IHDR, internal `flatten=True` call-site count ≥4, drawable fail-closed grep, MCP structured-error source test. |
| IR1-06 | low | **open** | Raw TCP still `bool(preserve_alpha)` when not None → `bool("false") is True`. MCP schema uses real bools. Non-blocking. |
| IR1-07 | low | **open** | Live Phase-0 `get_config_properties()` dump + DoD-11 GIMP matrix still waived offline. Property names from GNOME `file-png.c` remain recorded. |
| IR1-08 | low | **open** | `docs/planner-handoff.md` still “0005 Ready / implement when asked”; `conductor.md` “In progress”. Expected mid-track; finalize on completion. |

### New findings (round 2)

| id | severity | description | files | required_fix | status | evidence |
|---|---|---|---|---|---|---|
| IR2-01 | low | `batch_export` per-item error records keep `code`/`error`/`file_path` but **drop** `left_on_disk`, `png_color_type`, `property_errors`. Primary DoD-5 contract is `export_image` (§2.7); batch remains multi-result success envelope with partial error list. Agents debugging ALPHA_LOST via batch alone cannot machine-read left-on-disk. | `gimp-mcp-plugin.py` `_batch_export` ~2507–2515 | Optionally forward `left_on_disk` / `png_color_type` into batch error items | open | errors.append only index/error/code/file_path |
| IR2-02 | low | Offline greps prove source shape; no behavioral unit test of recursive walker with mock group trees (GIMP GI unavailable offline). Acceptable for this track’s grep-heavy offline style; residual if GI group API diverges from `is_group`/`get_children`. | `tests/test_export_alpha.py` | Optional pure mock walker test later | open | greps only |

No new **medium** or higher findings. No incomplete wiring of the IR1-01/02 fix paths. No regressions of policy/merge-on-dup/`file-*-export`/path jail/internal flatten callers found.

### Non-findings reconfirmed

| Area | Evidence |
|---|---|
| Policy matrix (no flatten when preserve_alpha; JPEG+preserve → ALPHA_UNSUPPORTED; conflict → POLICY_CONFLICT; flatten True + preserve None → opaque, verify off) | `gimp_mcp_export.resolve_export_policy` + tests |
| Always merge-on-dup when preserve_alpha; prep only on `duplicate()`; `finally: dup.delete()` | `_export_to_path` |
| No `file-*-save` PDB names in export path | body greps; only tests ban strings |
| ALPHA_LOST preflight-gated; leaves file; `left_on_disk=true` | ~2219–2268 |
| Opaque source → `alpha_verified="not_applicable"` | ~2270–2271 |
| MCP defaults: flatten False, preserve_alpha None, verify True; schema `format` only | server `export_image` |
| batch_export param parity (flatten/preserve_alpha/verify) | server + plugin |
| Internal callers flatten=True (icons/web/sprite/social) | call sites ~4477, 4525–26, 4673, 4739 |
| Fail-closed import of `gimp_mcp_export` | plugin ~48–53 |
| Install docs list 4 files incl. export module | README/SECURITY/CLAUDE |

### DoD status (re-review)

| DoD | Status | Notes |
|---|---|---|
| **DoD-1** Default PNG no flatten when preserve_alpha | **met** | policy + merge path |
| **DoD-2** merge-on-dup single/multi | **met** | always merge when preserve_alpha |
| **DoD-3** ALPHA_LOST preflight-gated; opaque → not_applicable | **met** | group recursion fixed (IR1-01) |
| **DoD-4** verify_alpha_channel richer | **met** | recursive names + format matrix |
| **DoD-5** Success/error metadata + left_on_disk | **met** (export_image MCP + plugin/TCP); batch items partial (IR2-01 low) | structured return on MCP path |
| **DoD-6** Only file-*-export; ban -save | **met** | code + greps |
| **DoD-7** batch_export param parity | **met** | server + plugin |
| **DoD-8** Offline tests + greps | **met** (static); **pytest not re-run this session** | orchestrator re-run recommended |
| **DoD-9** Full gate | **partial** | orchestrator owns ruff/basedpyright/full pytest/ledgerful |
| **DoD-10** Docs + review + conductor Completed + ledger | **partial** | mid-track; handoff finalize pending |
| **DoD-11** Manual GIMP matrix | **missing** (waived offline) | checklist only |
| **DoD-12** Phase-0 names; no swallow critical | **met** with residual soft RGBA8 (IR1-03 low) | image/file hard-fail; pixel-format soft + IHDR |

### Blocking for clearance

**None** above **low**.

Prior mediums IR1-01 / IR1-02 → **verified_fixed**. Optional IR1-04/05 → **verified_fixed**. Open residuals all **low** (IR1-03/06/07/08, IR2-01/02).

### Summary for orchestrator

**CLEAN** for engineering clearance of IR fix round. Re-run offline pytest (and full gate when finalizing). Live GIMP matrix (DoD-11) and docs/conductor finalize (DoD-10/IR1-08) remain operator/orchestrator. No further code fix required for medium blockers.

---

## Codex review findings (P1/P2/P3)

- **Reviewer:** Codex track completion audit
- **Date:** 2026-08-02
- **Source:** `conductor/0005-AlphaExportCorrectness/review.codex.md`
- **Fix commit (this round):** `fix(export): codex P1/P2 — no silent format fallback, fail-closed RGBA8, batch metadata`

| id | severity | status | notes |
|---|---|---|---|
| **P1-1** Unsupported formats silently export as PNG | P1 | **fixed_pending_verification** | `CODE_UNSUPPORTED_FORMAT` + early reject in `resolve_export_policy`; `_export_to_path` errors when `pdb_procedure_for_format` is None (no `file-png-export` silent fallback). Tests: `test_policy_unsupported_format_bmp_gif`, `test_export_path_no_silent_png_format_fallback`. |
| **P1-2** RGBA8 property failure can still return success | P1 | **fixed_pending_verification** | When `png` + `preserve_alpha` + `preflight_has_alpha`, `_set_png_rgba8_format` failure → `EXPORT_FAILED` before `proc.run` (DoD-12 fail-closed). Test: `test_export_path_rgba8_fail_closed`. |
| **P1-3** Required completion governance not closed | P1 | **orchestrator** | Ledgerful DB / conductor Completed / handoff finalize / full format gate owned by orchestrator — not this fix commit. |
| **P2-1** Preflight selected-layer `visible_only=False` | P2 | **fixed_pending_verification** | Selected walk now `visible_only=True`. Test: selected walk greps in `test_preflight_has_alpha_walks_groups_recursively`. |
| **P2-2** Batch errors drop structured metadata | P2 | **fixed_pending_verification** | Batch error items merge `left_on_disk`, `png_color_type`, `preflight_has_alpha`, `property_errors`, `format`, etc. Test: `test_batch_export_errors_include_structured_fields`. |
| **P2-3** Live GIMP verification not performed | P2 | **residual/ops waiver** | DoD-11 manual matrix + Phase-0 live prop dump remain operator when GIMP available; offline gates cover host logic + greps. |
| **P3-1** Raw TCP `bool("false") is True` | P3 | **fixed_pending_verification** | Pure `coerce_bool` / `coerce_optional_bool` in `gimp_mcp_export.py`; used by `_export_image` / `_batch_export`. Tests: `test_coerce_bool_stringly_tcp`. |

**Hygiene:** README trailing whitespace at L459 removed (`git diff --check`).

### Codex fix summary for orchestrator

Code fixes for **P1-1, P1-2, P2-1, P2-2, P3-1** landed; re-run offline pytest + ruff + basedpyright to move to **verified_fixed**. **P1-3** governance and **P2-3** live GIMP remain outside this engineering patch.