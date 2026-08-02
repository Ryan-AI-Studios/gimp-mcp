# 0004 — Visible Composite Snapshot — Review Log

**Track:** 0004-VisibleCompositeSnapshot  
**Category:** BUGFIX (Issue 17)  
**Date:** 2026-08-02

---

## Phase progress

| Phase | Status | Notes |
|---|---|---|
| 0 Orient & algorithm | Done | Primary path locked: `duplicate` + `Selection.none` + `merge_visible_layers(CLIP_TO_IMAGE)` + optional flatten fallback; thumbnail path skipped |
| 1 Host module + unit tests | Done | `gimp_mcp_snapshot.py` + `tests/test_snapshot_mapping.py` |
| 2 Plugin rewrite | Done | `_get_current_image_bitmap` full rewrite; `_get_image` rejects negatives |
| 3 MCP server | Done | `image_index` on `get_image_bitmap`; ToolResult + structuredContent passthrough |
| 4 Tests | Done | Offline greps + ToolResult unit; integration optional (no live GIMP required for gate) |
| 5 Docs | Done | PROTOCOL, README, planner-handoff, this review |
| 6 Gates | Done | ruff / format (our surface) / basedpyright / offline pytest |

---

## DoD checklist

| ID | Criterion | Status | Evidence |
|---|---|---|---|
| DoD-1 | Composite multi-layer | **code+grep** | Plugin uses dup+merge; offline greps ban layers[0] path; **manual/integration pixel proof deferred to live GIMP** |
| DoD-2 | Region composite | **pass** | No `edit_copy`; crop on merged dup only (`tests/test_snapshot_composite.py`) |
| DoD-3 | image_index both tools + negative reject | **pass** | Server `get_image_bitmap(image_index=…)`; plugin `_get_image` |
| DoD-4 | No user mutation | **code** | All ops on `dup` + finally delete; integration dirty/layer/selection asserts optional |
| DoD-5 | Mapping structuredContent | **pass** | Region-relative scales unit-tested; `_snapshot_tool_result` |
| DoD-6 | ToolResult Image + structured | **pass** | FastMCP 2.10.1 ToolResult + convert_result passthrough |
| DoD-7 | Temp policy | **pass** | Workspace `.gimp-mcp-tmp` or pid temp; unlink in finally |
| DoD-8 | Gates | **pass** | See commands below |
| DoD-9 | Grep bans | **pass** | `test_snapshot_composite.py` |
| DoD-10 | Docs + closeout | **pass** | PROTOCOL/README/handoff/review |

---

## Commands run

```powershell
uv run pytest tests/test_snapshot_mapping.py tests/test_snapshot_composite.py -q
uv run ruff check .
uv run basedpyright
uv run pytest -m "not integration and not slow"
```

Note: `uv run ruff format --check .` on Windows with `core.autocrlf=true` can flag untouched
files as “would be reformatted” due to CRLF checkout vs ruff `line-ending = "lf"`. CI (Linux)
and pre-commit on staged files enforce LF. Our changed Python files are format-clean.

---

## Install note

Plugin install must copy **three** files into the plug-ins folder:

1. `gimp-mcp-plugin.py`
2. `gimp_mcp_security.py`
3. `gimp_mcp_snapshot.py`  ← **new in 0004**

Missing `gimp_mcp_snapshot.py` fails closed at plugin import (same pattern as security).

---

## Residual risks / open questions

| Residual | Severity | Notes / defer |
|---|---|---|
| Thumbnail path unused | Low | Optional ≤1024 optimization skipped; full path always merge+export |
| NDE filters on projection | Medium | `merge_visible_layers` matches common canvas; exotic NDE edge cases unproven — 0016 |
| Color management / display transform | Low–Med | Export uses PDB PNG; soft-proof/display filters may differ from on-screen |
| Same-user readable temps | Low | Temps under workspace or pid dir; 0o700 best-effort; same UID can still read |
| Live multi-layer pixel proof | Med | Offline greps + unit mapping; manual matrix below for operator |
| Alpha export fidelity | Out of scope | **0005** |
| EXIF orientation in mapping | Out of scope | **0008** (0004 delivers sizes/scales/image_index via structuredContent only) |
| Windows format-check vs autocrlf | Low | Documented; prefer CI LF checkout |

---

## Manual verification matrix (live GIMP)

When GIMP + plugin are running:

1. New RGB image; red bottom layer full; translucent blue top layer → snapshot must show purple/blend, not pure blue.
2. Record dirty flag, layer count, selection bounds → snapshot → assert unchanged.
3. `get_image_bitmap(image_index=1)` with two open images targets the correct document.
4. Region crop: mapping `scale_x ≈ rendered_w / region_w`.
5. Negative `image_index` → clear error.

Optional:

```powershell
uv run pytest -m integration -k snapshot
```

---

## Findings (implementation self-review)

| ID | Severity | Description | Status |
|---|---|---|---|
| — | — | No open blocking findings from implementer self-review | — |

---

## Deferred roll-outs after 0004

- **0005** Alpha export correctness  
- **0008** EXIF / orientation in coordinate model  
- **0014** SSIM / pixel verification protocol  
- **0016** NDE filter tools (projection edge cases)

---

## Internal Review (strict RO — 2026-08-02)

**Reviewer role:** Grok Build subagent (read-mostly; product code / git / conductor status / deferred.md untouched)  
**Scope:** working tree on `feature/0004-visible-composite-snapshot` vs track DoD + checklist  
**Method:** static audit of `spec.md` / `plan.md` / key implementation + tests + docs; source greps; pytest **not executed in this session** (no shell tool available to reviewer — see evidence)

### Findings

### [IR-1] medium — Export drawable ignores merge/flatten return layer
- Status: **verified**
- Description:
  `_get_current_image_bitmap` calls `dup.merge_visible_layers(CLIP_TO_IMAGE)` / `dup.flatten()` but discards the returned `GimpLayer*`. PNG export then picks
  `(dup.get_selected_layers() or dup.get_layers() or [None])[0]`.
  `merge_visible_layers` **keeps invisible layers**. If selection is empty/stale after merge+crop+scale, `get_layers()[0]` can be a remaining **invisible** top layer, so `file-png-export` may write a non-composite drawable (re-introduces Issue-17 class failure for docs with hidden layers). Spec Phase 2b / API docs return the merged layer; sibling handler `_merge_visible_layers` already captures `merged = image.merge_visible_layers(...)`.
- Files:
  - `C:\dev\GIMP\gimp-mcp\gimp-mcp-plugin.py` (`_get_current_image_bitmap` ~778–845)
  - Contrast: same file `_merge_visible_layers` ~2954–2960
- Evidence:
  - API: `gimp_image_merge_visible_layers` returns resulting layer; flatten likewise returns resulting layer.
  - Bitmap path:
    ```text
    dup.merge_visible_layers(Gimp.MergeType.CLIP_TO_IMAGE)  # return ignored
    ...
    drawable = (dup.get_selected_layers() or dup.get_layers() or [None])[0]
    export_config.set_property("drawable", drawable)  # or drawables=[drawable]
    ```
  - All-visible multi-layer docs still work (single remaining layer). Hidden-layer docs are the failure mode.
- Required fix:
  Capture `merged = dup.merge_visible_layers(...)` / `merged = dup.flatten()` and use that layer as the export drawable after crop/scale (re-resolve from returned layer if needed). Prefer failing closed if merge returns None rather than guessing layers[0].
- Fix applied (2026-08-02):
  - `merged = dup.merge_visible_layers(...)` / `merged = dup.flatten()` captured; None from merge triggers flatten fallback.
  - Fail closed if still None (`"Composite merge/flatten returned no layer"`).
  - Export drawable prefers `merged` (probed via `get_width()`); on invalidation re-resolves only to single visible or single remaining layer — no `layers[0]`/selected-layers guess when ambiguous.
  - Offline: `test_bitmap_method_captures_merge_flatten_return` asserts assignment of merge/flatten returns + fail-closed strings.

### [IR-2] medium — SECURITY.md / CLAUDE.md install still omit `gimp_mcp_snapshot.py`
- Status: **verified**
- Description:
  Plugin **fail-closes** at import if `gimp_mcp_snapshot.py` is missing (same pattern as security). Primary `README.md` install is correct (three files). Load-bearing start-order docs still say install only plugin+security (`SECURITY.md`) or only the plugin (`CLAUDE.md`). Operators following those paths get an ImportError and no MCP server after 0004.
- Files:
  - `C:\dev\GIMP\gimp-mcp\SECURITY.md` (~line 58: “Install **both** … plugin … and … security”)
  - `C:\dev\GIMP\gimp-mcp\CLAUDE.md` (~lines 35–36: `cp gimp-mcp-plugin.py` only)
  - Contrast OK: `README.md` ~94–131; `review.md` Install note; plugin import ~40–46
- Evidence:
  - `grep gimp_mcp_snapshot SECURITY.md` → no matches
  - `grep gimp_mcp_snapshot CLAUDE.md` → no matches
  - Plugin: `raise ImportError("gimp_mcp_snapshot.py must sit next to gimp-mcp-plugin.py ...")`
- Required fix:
  Update SECURITY start order and CLAUDE install snippet to copy **three** files: `gimp-mcp-plugin.py`, `gimp_mcp_security.py`, `gimp_mcp_snapshot.py` (mirror README).
- Fix applied (2026-08-02):
  - `SECURITY.md` Start order #1: install three files (plugin + security + snapshot).
  - `CLAUDE.md` install snippet: `cp gimp-mcp-plugin.py gimp_mcp_security.py gimp_mcp_snapshot.py ...` + note on fail-closed import.

### [IR-3] low — FuncMetadata.convert_result patch has no runtime unit test
- Status: **verified**
- Description:
  Server correctly monkey-patches `FuncMetadata.convert_result` so `ToolResult` becomes `(content, structured_content)` for `mcp.server.fastmcp` (SDK FastMCP does not natively understand standalone `fastmcp.tools.tool.ToolResult`). Offline tests call `_snapshot_tool_result(...).to_mcp_result()` and source-grep for `convert_result` / `to_mcp_result`, but never invoke the patched `convert_result` itself. A future refactor that drops the patch would still pass greps if strings remain and would break live MCP structuredContent.
- Files:
  - `C:\dev\GIMP\gimp-mcp\gimp_mcp_server.py` ~27–37, ~95–155
  - `C:\dev\GIMP\gimp-mcp\tests\test_snapshot_composite.py` ~121–133, ~154–227
- Evidence:
  - Without patch, SDK `_convert_to_content(ToolResult)` falls through to JSON text (no ImageContent + structured pair).
  - Tests never call `FuncMetadata.convert_result` / patched path.
- Required fix:
  Add a small unit test: construct a ToolResult via `_snapshot_tool_result`, pass it through `FuncMetadata.convert_result` (or the patched method), assert tuple shape with image content + mapping dict.
- Fix applied (2026-08-02):
  - `test_convert_result_passthrough_for_tool_result` builds ToolResult via `_snapshot_tool_result`, calls `FuncMetadata.convert_result(None, tr)`, asserts `(content_list, structured_dict)` with ImageContent + mapping keys.

### [IR-4] info — Live multi-layer pixel / mutation integration still optional
- Status: open (residual; not a code defect by itself)
- Description:
  DoD-1/4 accept code+design proof; plan Phase 4 marks GIMP integration optional. No `@pytest.mark.integration` snapshot tests exist. Residual already noted in implementer residual table. Offline greps + mapping units are present and would fail on old single-layer/`images[0]` bitmap path.
- Files:
  - `tests/test_snapshot_composite.py` (offline only)
  - `review.md` residual “Live multi-layer pixel proof”
- Evidence:
  - No integration assertions for dirty/layer-count/selection or blend pixels in suite.
- Required fix:
  Optional: add skipped-without-GIMP integration for red+blue blend + dirty/layers/selection unchanged (DoD-1/4 strength). Not blocking if IR-1 fixed and manual matrix run.

---

## Verdict

**PASS (internal re-review ready)** — IR-1 / IR-2 / IR-3 **verified** with gates below. IR-4 remains residual (optional live integration; not blocking).

### Gate evidence (IR fix verification, 2026-08-02)

```powershell
uv run pytest tests/test_snapshot_mapping.py tests/test_snapshot_composite.py -q
# ..................................  (includes captures_merge_flatten_return + convert_result_passthrough)

uv run ruff check gimp-mcp-plugin.py gimp_mcp_server.py gimp_mcp_snapshot.py tests/test_snapshot_*.py
# All checks passed!

uv run basedpyright
# 0 errors, 0 warnings, 0 notes

uv run pytest -m "not integration and not slow" -q
# 76 passed
```

---

## DoD matrix (internal)

| ID | Criterion | Status | Evidence |
|---|---|---|---|
| DoD-1 | Composite multi-layer | **Partial** | Plugin path: `duplicate` + `Selection.none` + `merge_visible_layers(CLIP_TO_IMAGE)` + flatten fallback on **dup only** (`gimp-mcp-plugin.py` ~763–792). Offline greps ban old path. No live pixel proof in suite; IR-1 weakens confidence when invisible layers remain. |
| DoD-2 | Region composite | **Met** | Region crop on `dup` after merge (`~794–800`); no `edit_copy` / `orig_layers[0]` / `select_rectangle` in `_get_current_image_bitmap` (grep + `test_bitmap_method_no_*`). |
| DoD-3 | image_index both tools + negative reject | **Met** | Server `get_image_bitmap(..., image_index: int = 0)` (~389–437); `get_state_snapshot` already has index; plugin `_get_image` rejects `< 0` (~1702–1703); bitmap uses `_get_image` not bare `images[0]`. |
| DoD-4 | No user mutation | **Met** (by design) | All mutate ops on `dup`; `finally: dup.delete()` + temp unlink (~972–982). No select/merge/flatten on `original_image`. Integration dirty/layer/selection asserts still optional (IR-4). |
| DoD-5 | Mapping structuredContent | **Met** | Region-relative scales in `build_mapping_metadata` + tests (`test_build_mapping_region_relative_scales`); plugin returns full mapping fields; server `_snapshot_tool_result` builds `structured_content`. |
| DoD-6 | ToolResult Image + structured | **Met** (with residual) | Both tools `-> ToolResult`; annotations audience user+assistant; convert_result patch required for SDK FastMCP (IR-3 weak test). |
| DoD-7 | Temp policy §2.3 | **Met** | `ensure_snapshot_temp_dir` / `snapshot_temp_path`: workspace `.gimp-mcp-tmp` or `gimp-mcp-{pid}`; chmod 0o700 best-effort; finally unlink; unit tests for both roots. |
| DoD-8 | Gates | **Partial** | Implementer review claims ruff/basedpyright/offline pytest green. **This review did not re-run gates** (no shell). Recommend orchestrator re-run listed commands. |
| DoD-9 | Grep bans | **Met** | `tests/test_snapshot_composite.py`: bans `edit_copy`, `orig_layers[0]`, `images[0]`, `select_rectangle` on original, requires duplicate/merge/CLIP/`_get_image`/temp helpers/finally delete. Would fail pre-0004 bitmap body. |
| DoD-10 | Docs + closeout | **Partial** | PROTOCOL/README/handoff document visible composite + structuredContent + `image_index`; README install lists three files; pyproject includes `gimp_mcp_snapshot` for packaging/basedpyright. **SECURITY.md + CLAUDE.md install drift (IR-2)**. Conductor Completed / deferred / ledger closeout still open per plan Phase 6 (orchestrator). |

---

## Checklist crosswalk (audit items 1–19)

| # | Result | Notes |
|---|---|---|
| 1 | **Mostly pass / IR-1** | Full+region: dup only + Selection.none + merge CLIP_TO_IMAGE + flatten fallback. Export drawable selection fragile (IR-1). |
| 2 | **Pass** | No residual single-layer `edit_copy([orig_layers[0]])` in bitmap handler. |
| 3 | **Pass** | No bare `images[0]` in bitmap handler (metadata path still uses `images[0]` — out of 0004 scope). |
| 4 | **Pass** | `_get_image` rejects negative indices. |
| 5 | **Pass w/ IR-1** | Export is post-merge on dup image; drawable choice not guaranteed merged layer if hidden layers remain. |
| 6 | **Pass** | Region-relative scale math unit-tested. |
| 7 | **Pass** | Both tools ToolResult + Image + structured_content. |
| 8 | **Pass** | `get_image_bitmap` has `image_index`. |
| 9 | **Pass** | `get_state_snapshot` default `max_size: int = 512`. |
| 10 | **Pass w/ IR-3** | convert_result patch only special-cases ToolResult; other tools fall through `_orig_convert`. Untested at runtime. |
| 11 | **Pass** | Temp policy + finally unlink. |
| 12 | **Pass** | Auth still single precheck before routing; `send_command` always attaches token; path jail handlers still covered by security tests; snapshot temp uses policy helper not user path. |
| 13 | **Pass** | No Pillow/PIL in plugin or snapshot module (comment only). |
| 14 | **Pass** | Mapping unit tests cover full vs region, aliases, negatives, temp roots. |
| 15 | **Pass** | Source-grep regressions target old Issue-17 patterns. |
| 16 | **Partial** | No TODO/stub in snapshot surface; integration placeholders absent rather than faked; convert_result runtime gap (IR-3). |
| 17 | **Partial** | README/protocol/handoff OK; SECURITY/CLAUDE install incomplete (IR-2). |
| 18 | **Pass** | `pyproject.toml` `py-modules` + basedpyright include list `gimp_mcp_snapshot`. |
| 19 | **Pass (design)** | Original not mutated by bitmap path; mutation only on deleted dup. |

---

## Grep / command evidence

### Source greps (observed this review)

```text
# Bitmap method: no edit_copy / orig_layers / bare images[0] / select_rectangle
# (matches only outside _get_current_image_bitmap for those patterns)

# merge path present:
gimp-mcp-plugin.py:778  dup.merge_visible_layers(Gimp.MergeType.CLIP_TO_IMAGE)
gimp-mcp-plugin.py:772  Gimp.Selection.none(dup)
gimp-mcp-plugin.py:764  dup = original_image.duplicate()
gimp-mcp-plugin.py:730  original_image = self._get_image(image_index)
gimp-mcp-plugin.py:1702 if image_index < 0: raise RuntimeError(... negative)

# server:
gimp_mcp_server.py:27-37  FuncMetadata.convert_result patch
gimp_mcp_server.py:389+   get_image_bitmap(..., image_index: int = 0) -> ToolResult
gimp_mcp_server.py:525+   get_state_snapshot(..., max_size: int = 512) -> ToolResult
gimp_mcp_server.py:95-155  _snapshot_tool_result -> ToolResult(content, structured_content)

# packaging:
pyproject.toml:22  py-modules includes gimp_mcp_snapshot
pyproject.toml:92  basedpyright include gimp_mcp_snapshot.py

# install docs:
README.md          three-file install  OK
SECURITY.md        gimp_mcp_snapshot   MISSING
CLAUDE.md          gimp_mcp_snapshot   MISSING

# Pillow in plugin: only comment "no Pillow" at export site
```

### Pytest (requested)

```powershell
uv run pytest tests/test_snapshot_mapping.py tests/test_snapshot_composite.py -q
```

**Result this session:** **not run** — reviewer environment had no shell/exec tool.  
**Secondary signal:** `tests/__pycache__/test_snapshot_mapping.cpython-313-pytest-9.1.1.pyc` and `test_snapshot_composite.cpython-313-pytest-9.1.1.pyc` exist (prior local run). Implementer `review.md` claims these green. **Orchestrator should re-run before closeout** and attach stdout.

Recommended full re-check after IR-1/IR-2 fixes:

```powershell
uv run pytest tests/test_snapshot_mapping.py tests/test_snapshot_composite.py -q
uv run pytest -m "not integration and not slow"
uv run ruff check .
uv run basedpyright
```

---

## Wiring / regression notes (no extra findings)

- **Composite algorithm locked correctly** for all-visible-layer case: dup → undo_disable → Selection.none → merge CLIP_TO_IMAGE → optional crop/scale → PNG via PDB (no Pillow) → finally delete dup + unlink temp.
- **0003 security surface** not regressed on the snapshot path: auth-first `execute_command`; server refuses missing token; bitmap does not open user-supplied paths (only policy temp).
- **Server uses `mcp.server.fastmcp.FastMCP`** (SDK) + **`fastmcp.tools.tool.ToolResult`** (standalone 2.10.1): the convert_result patch is load-bearing for DoD-6 (not optional polish).
- **Closeout still orchestrator-owned:** conductor Completed, deferred EXIF note, ledger commit (plan Phase 6 unchecked items).

---

## Disposition for fix pass

1. **Must fix before CLEAN:** IR-1, IR-2  
2. **Should fix:** IR-3  
3. **Accept / optional:** IR-4 (document if deferred to 0014 / integration track)  
4. Re-run offline snapshot tests + full offline pytest; optional live matrix in implementer review

---

## Re-review (strict RO — 2026-08-02, post IR-1/2/3 fixes)

**Reviewer role:** Grok Build subagent (product code / git / conductor status / deferred.md untouched; only this section appended)  
**Scope:** working tree on `feature/0004-visible-composite-snapshot` after claimed IR-1/2/3 fix pass  
**Method:** re-read `spec.md` DoD + prior IR findings; static audit of bitmap export path, install docs, convert_result test; source greps; pytest attempted (see evidence)

### Verdict

**CLEAN** — no open medium+ findings. Prior IR-1 / IR-2 / IR-3 **verified_fixed**. IR-4 remains info residual (optional live integration; not blocking).

### Prior findings — status

| ID | Prior severity | Status | Evidence |
|---|---|---|---|
| **IR-1** | medium | **verified_fixed** | `_get_current_image_bitmap` captures `merged = dup.merge_visible_layers(...)` / `merged = dup.flatten()`; fails closed if still None (`"Composite merge/flatten returned no layer"`). Export prefers `merged` after `get_width()` probe. On proxy invalidation, re-resolves only to **single visible** layer or **exactly one** remaining layer — no `get_selected_layers` / ambiguous `layers[0]` guess. Offline: `test_bitmap_method_captures_merge_flatten_return` requires assignment of merge/flatten returns + fail-closed strings. |
| **IR-2** | medium | **verified_fixed** | `SECURITY.md` Start order #1: three files (`gimp-mcp-plugin.py`, `gimp_mcp_security.py`, `gimp_mcp_snapshot.py`). `CLAUDE.md` install: `cp` all three + fail-closed note. Grep matches present; README already correct. |
| **IR-3** | low | **verified_fixed** | `test_convert_result_passthrough_for_tool_result` builds ToolResult via `_snapshot_tool_result`, calls `FuncMetadata.convert_result(None, tr)`, asserts `(content_list, structured_dict)` with ImageContent + mapping keys (`mode`, `source_width`, `rendered_width`, `composite_method`, `scale_x`, `image_index`). |
| **IR-4** | info | **still_open** (residual; not a code defect) | No `@pytest.mark.integration` snapshot tests; live multi-layer blend + dirty/layer/selection proof still optional (DoD-1/4 accept code+design). Manual matrix in this review remains operator-owned. |

### Residual Issue-17 export path (IR-1 deep check)

Primary composite algorithm on **dup only**:

1. `duplicate()` → `undo_disable` → `Selection.none(dup)`
2. `merged = merge_visible_layers(CLIP_TO_IMAGE)` with flatten fallback (return captured)
3. Optional region `dup.crop` / `dup.scale` (never on original)
4. Export drawable = `merged` if live; else single-visible / single-layer only; else error
5. `finally`: `dup.delete()` + temp unlink

**Not present in bitmap method (confirmed by method-body grep):**

- `edit_copy` / `orig_layers[0]` / `edit_paste`
- `select_rectangle` / `Selection.none(original…)`
- bare `images[0]` / `Gimp.get_images()` (uses `_get_image`, which rejects `image_index < 0`)
- `get_selected_layers` (still used elsewhere for non-snapshot handlers — out of path)

**Fail-closed vs wrong-layer:** when `merge_visible_layers` keeps invisible layers, export no longer falls through to selected/`layers[0]` (which could be hidden). That was the IR-1 Issue-17 reintroduction mode; it is closed on the primary path.

**Info residual (does not block CLEAN):** secondary export fallbacks (`Gimp.file_save` / `gimp-file-save`) are image-level and do not re-set the merged drawable. They only run if primary `file-png-export` fails. Not the pre-fix primary bug; acceptable residual.

### New findings

| ID | Severity | Description | Status |
|---|---|---|---|
| — | — | **No new medium+ findings** from the IR fix pass | — |

No new low/info items raised beyond IR-4 and the pre-existing secondary-export residual above.

### Regression sweep (fix surface)

| Area | Result |
|---|---|
| Hidden-layer export | Fixed: merged return preferred; ambiguous multi-layer re-resolve fails closed |
| All-visible multi-layer | Still OK (single remaining layer after merge) |
| Flatten fallback | Return captured; `composite_method` set to flatten |
| Install docs (SECURITY/CLAUDE/README) | Three-file install consistent |
| ToolResult / structuredContent | Patch + runtime unit test present |
| Mutation isolation | Unchanged: mutate ops on `dup` only + finally delete |
| 0003 security | Snapshot path still policy temp only; no user path open; auth unchanged |

### DoD matrix (re-review)

| ID | Status | Notes |
|---|---|---|
| DoD-1 Composite | **Met** (code+grep; live pixel optional IR-4) | IR-1 closed; confidence restored for hidden-layer docs |
| DoD-2 Region composite | **Met** | Crop on merged dup; no `edit_copy` |
| DoD-3 image_index | **Met** | Server + plugin; negatives rejected |
| DoD-4 No user mutation | **Met** (by design) | Integration asserts still optional |
| DoD-5 Mapping | **Met** | Region-relative scales unit-tested |
| DoD-6 ToolResult | **Met** | convert_result runtime unit test (IR-3 fixed) |
| DoD-7 Temp policy | **Met** | helpers + finally unlink |
| DoD-8 Gates | **Partial this session** | Snapshot pytest **not re-executed here** (no shell/exec tool in reviewer environment). Implementer gate block above claims green post-fix; orchestrator should re-confirm before closeout. |
| DoD-9 Grep bans | **Met** | composite offline tests |
| DoD-10 Docs | **Met** | SECURITY + CLAUDE install drift fixed (IR-2) |

### Pytest (requested)

```powershell
uv run pytest tests/test_snapshot_mapping.py tests/test_snapshot_composite.py -q
```

**Result this re-review session:** **not run** — reviewer environment has no shell/exec tool (static tools only).  
**Static cross-check of test surface (would be exercised by that command):**

- `tests/test_snapshot_mapping.py` — pure helpers (region/scales/temp)
- `tests/test_snapshot_composite.py` — includes `test_bitmap_method_captures_merge_flatten_return` (IR-1) and `test_convert_result_passthrough_for_tool_result` (IR-3)
- Source assertions for IR-1/2/3 production paths verified by direct file read

**Secondary signal:** implementer gate evidence in this file (post-fix block) reports both files green including the new tests; pycache for both modules present. **Orchestrator should re-run the command and attach stdout before final closeout.**

### Disposition

1. **CLEAN for engineering re-review** — IR-1/2/3 closed; no new medium+  
2. **Accept residual:** IR-4 (optional live multi-layer / mutation integration; manual matrix or 0014)  
3. **Orchestrator:** re-run offline snapshot pytest + full offline suite; conductor Completed / deferred / ledger closeout still orchestrator-owned

---

## Codex cross-model findings disposition (2026-08-02)

**Source:** `review.codex.md` (Codex track audit, verdict FAIL on P1 closeout + P2 fail-closed gaps)  
**Branch:** `feature/0004-visible-composite-snapshot`  
**Scope of this pass:** code fixes for **P2-1** and **P2-2** only. **P1-1** (orchestrator closeout — conductor Completed / deferred / ledger) **ignored** per orchestrator instruction (not a product-code fix).

| ID | Severity | Description | Disposition | Evidence |
|---|---|---|---|---|
| **P1-1** | P1 | Track closeout incomplete (plan Phase 6, conductor registry, deferred, ledger) | **Deferred / orchestrator-owned** — not fixed in this code pass | N/A (out of scope for product code) |
| **P2-1** | P2 | `Gimp.Selection.none(dup)` failure only warned then merge proceeded — inherited selection can silently clip composite | **Fixed** — fail closed | Primary clear re-raises `RuntimeError` with `"Selection.none on snapshot dup failed"`. Flatten-retry clears (merge exception path + merge-returned-None path) re-raise `"Selection.none before flatten failed"`. No bare `except …: pass` on Selection.none. Offline: `test_bitmap_method_selection_none_fail_closed` |
| **P2-2** | P2 | Export could succeed with empty mkstemp file / silent drawable property failure / unvalidated export result | **Fixed** — fail closed | `gimp_mcp_snapshot.validate_png_bytes` / `validate_png_file` (PNG signature `\x89PNG\r\n\x1a\n`). Primary `file-png-export` only runs when drawable **or** drawables property set succeeds; both-fail skips primary and tries image-level fallbacks. After every export attempt and before base64: reject empty/non-PNG. Never returns `status: success` for empty file. Unit: `test_validate_png_bytes_*`, `test_validate_png_file_*`. Grep: `test_bitmap_method_export_validates_png` |

### Code changes (this pass)

- `gimp_mcp_snapshot.py`: `PNG_SIGNATURE`, `MIN_PNG_BYTES`, `validate_png_bytes`, `validate_png_file`
- `gimp-mcp-plugin.py` `_get_current_image_bitmap`:
  - Selection.none fail-closed (primary + both flatten retries)
  - Export path: `drawable_set` gate; sequential fallbacks gated on `validate_png_file`; final `validate_png_bytes` before base64
- `tests/test_snapshot_mapping.py`: PNG validation unit tests
- `tests/test_snapshot_composite.py`: fail-closed Selection.none + export validation greps

### Gate commands (post-fix)

```powershell
uv run pytest tests/test_snapshot_mapping.py tests/test_snapshot_composite.py -q
uv run ruff check gimp-mcp-plugin.py gimp_mcp_snapshot.py tests/
uv run basedpyright
uv run pytest -m "not integration and not slow" -q
```

(Results recorded in commit / orchestrator closeout.)
