# GIMP PyGObject via MCP Documentation

## Overview
This document describes how to execute PyGObject commands in GIMP using the MCP (Model Context Protocol) interface. The GIMP MCP server provides multiple tools for interacting with GIMP 3.0, including image export capabilities that return MCP-compliant Image objects.

**Plugin install:** `uv run gimp-agent install` deploys the full 10-file ship set (`EXPECTED_PLUGIN_FILES`) into the newest GIMP `3.*` user plug-ins directory.

## Secure defaults (0003)

| Topic | Default |
|---|---|
| TCP host | **`127.0.0.1`** (`AF_INET`) — not bare `localhost` |
| Auth | Every JSON request must include `"auth": "<session token>"` |
| `cmds` / plugin eval | **Disabled** unless `GIMP_MCP_ALLOW_EXEC=1` → `EXEC_DISABLED` |
| MCP `call_api` | **Disabled** unless `GIMP_MCP_ALLOW_EXEC=1` (Class B / PDB-mediated) |
| File paths | Must resolve under `GIMP_WORKSPACE_ROOT` or → `PATH_DENIED` |
| String `disable_auto_disconnect` | **Deprecated / rejected** without auth; use authenticated JSON type |

Example authenticated typed command:

```json
{"type": "list_images", "params": {}, "auth": "<token>"}
```

Deprecated bare string (rejected in secure mode):

```text
disable_auto_disconnect
```

Authenticated replacement:

```json
{"type": "disable_auto_disconnect", "auth": "<token>"}
```

See [SECURITY.md](SECURITY.md) for env vars, start order, and residuals.

## Export alpha contract (Issue 16 / 0005)

| Tool | Mutates open image? | Alpha |
|---|---|---|
| `export_image` | **No** (prep on duplicate) | Default `flatten=False`; auto `preserve_alpha` for png/webp/tiff; PNG IHDR verify fail-closed |
| `batch_export` | **No** | Same params: `flatten`, `preserve_alpha`, `verify` |
| `flatten_image` | **Yes** (explicit) | Strips alpha on the open document |
| `merge_visible_layers` | **Yes** (explicit) | Can preserve alpha on the open document |
| `verify_alpha_channel` | **No** (read-only) | Image-level `has_alpha` + format capability matrix |
| `compare_images` | **No** (host-only) | MAE / max AE / changed pixels / alpha counts / global SSIM; optional grayscale diff PNG |
| `verify_artifact` | **No** (host-only) | Signature-based format + dims/alpha/sha256/bytes gates (PNG v1) |

### Pixel verification protocol (track 0014)

Objective before/after and artifact checks so silent no-ops fail closed.

| Surface | Tools / verbs |
|---|---|
| MCP HL (catalog **28**) | `compare_images`, `verify_artifact`, `list_recipes`, `apply_recipe`, NDE filter tools, undo_group_* |
| CLI (host-only) | `gimp-agent compare`, `gimp-agent verify` |
| Capability | `pixel_verification: true` (extension; not `_CAPABILITY_REQUIRED`) |
| **Not** flipped | `alpha_snapshot` stays **false** (live GIMP `render_alpha` unfinished) |

**`ok` vs `pass`:** successful operations always return `ok: true`. `pass` is the
threshold/expectation gate. Path jail / unsupported / budget errors **raise**
structured codes — they never return `ok: false`.

**Metrics (stdlib):** `mae`, `max_ae`, `changed_pixels`, `changed_fraction`,
`alpha_transparent_pixels_{a,b}`, optional `ssim` / `ssim_computed`.

**SSIM honesty:** product SSIM is **global** luminance (single window,
`C1=(0.01*255)²`, `C2=(0.03*255)²`). It is **not** ImageMagick windowed
`-metric SSIM`. `compute_ssim="auto"` disables when `w*h > 1_000_000`.

**PNG decoder:** 8-bit non-interlaced, color types 0/2/4/6, defilter types 0–4
including **Paeth**. Reject 16-bit, palette (3), interlaced → `UNSUPPORTED`.

**Budgets:** trusted default **50M** decoded pixels (untrusted **25M** when
`GIMP_MCP_UNTRUSTED_IMAGES` truthy); override `GIMP_MCP_MAX_DECODED_PIXELS`.
Max file size default **500 MiB** (`GIMP_MCP_MAX_VERIFY_FILE_BYTES`). Exceed →
`POLICY_DENIED`.

**Optional ImageMagick:** doctor reports `magick` or legacy `compare` on PATH;
subprocess list-args only; process exit **0 and 1** both OK (1 = images differ).
Stdlib metrics remain source of truth.

**Policy:**
- `flatten=True` + `preserve_alpha=None` → opaque bake (`preserve_alpha` forced false) — safe for icons/sprites.
- `flatten=True` + `preserve_alpha=True` → `POLICY_CONFLICT` error.
- JPEG + `preserve_alpha=True` → `ALPHA_UNSUPPORTED_FORMAT`.
- Preflight had alpha + `preserve_alpha` + verify + PNG without alpha → `ALPHA_LOST`
  (verify-on-temp: `left_on_disk=false`, `final_intact=true`; final path never replaced).
- Opaque source + default preserve_alpha → success with `alpha_verified="not_applicable"`.

PDB names: only `file-png-export` / `file-jpeg-export` / `file-webp-export` / `file-tiff-export`.

## Available MCP Tools

> **Default surface (0010 + 0014 + 0015 + 0016 + 0017):** hosts list **28** high-level tools unless
> `GIMP_MCP_ADVANCED_TOOLS=1`. Prefer design names: `session_probe`,
> `render_visible_composite` (alias of the composite path below), `create_selection`,
> `orient_workspace`, `apply_nde_filter` / `edit_filter_config` / `remove_nde_filter`.
> Legacy names such as `get_image_bitmap` / `check_server` remain available in
> **advanced** mode only.

### 1. Image Export Tools

#### `render_visible_composite` / `get_image_bitmap(image_index=0, max_width=None, max_height=None, region=None)`
**Default (HL):** `render_visible_composite`. **Advanced alias:** `get_image_bitmap`.
Returns the **visible composite** of a specified open image as PNG (MCP ImageContent)
plus mapping metadata in MCP **`structuredContent`**.

- **Composite:** all visible layers / opacity / blend / masks as GIMP's canvas projection
  (not the top layer alone). Works on a temporary duplicate; never mutates the user's image.
- **image_index:** which open document to capture (default `0`)
- **Format:** PNG
- **Returns:** ToolResult — vision ImageContent + structured mapping:
  `mode`, `image_index`, `source_width`/`source_height`, `rendered_width`/`rendered_height`,
  `scale_x`/`scale_y` (region-relative when `region` is set), `region`, `composite_method`,
  plus coordinate declaration (0008): `coordinate_space="image-pixels"`, `origin="top-left"`,
  `x_axis="right"`, `y_axis="down"`, `preview_padding_x/y=0`, `view_rotation_ignored=true`,
  `pixel_orientation_normalized`, `exif_orientation_original` (snapshot-time copy)
- **Region keys:** `origin_x`/`origin_y` or `x`/`y`, plus `width`/`height`
- **Recovery:** use host tools `map_preview_to_image` / `map_image_to_preview` with mapping
  scales + region origin (do not invent formulas)

#### `get_state_snapshot(image_index=0, max_size=512, region=None, label="")`
Agent-oriented live preview of the **visible composite** (same capture path as
`get_image_bitmap`). Default max edge is **512**. Region may use `{x,y,width,height}`
shorthand. Returns ToolResult with ImageContent + structuredContent mapping
(same schema as `get_image_bitmap`).

#### `orient_workspace(image_index=None, summary_only=False)` — **orientation SoT**
Schema-versioned **state manifest** (`urn:gimp-agent:state-manifest:1`, `schema_version`
`1.0.0`). Prefer this **before any mutation** and re-run after structural ops
(create/delete/reorder/merge/rasterize layers, open/close images).

- **Read-only:** no selection/display changes, no undo groups, no `displays_flush`, no export
- **Default:** full recursive layer trees for **all** open images
- **image_index:** optional filter to a single document (large workspaces)
- **summary_only:** lightweight per-image summary without deep layer trees
- **Handles:** session-stable under **generation** rules (`session_epoch` + per-image
  structural generation). Capability `stable_handle_registry: true`.
- **selected:** true only for the front display image (`Gimp.get_displays()[0]`); if no
  displays, all `selected: false` (never defaults index 0 to true)
- **Layer kinds:** `raster` | `group` | `text` | `link` | `vector` (nested `children[]`)
- **Capabilities:** honest matrix (composite snapshot true; atomic save/export true; …)
- **Transport:** agent-facing `stdio-proxy` (plugin TCP is internal)
- **Contract file:** `schemas/state-manifest.v1.json`

`list_layers` remains a flat compatibility helper — prefer `orient_workspace` for tree/kinds.

#### Stable handles (track 0007)

- Image handle: `{image_id, generation, session_epoch, fingerprint?}`
- Item handle: `{item_id, image_id, generation, session_epoch, fingerprint?}`

| Event | Effect |
|---|---|
| Open / new canvas / first orient | seed `generation` (1, or retired floor + 1 on ID recycle) |
| Structural success on **live** image | `generation += 1`; response includes `generation` + `handle` |
| Rename / opacity / paint / export / orient | **no** bump |
| Snapshot/export merge on **temp dup** | **no** bump of live image |
| Close image | drop live entry; keep retired floor for ID recycle |

**Structural mutators (bump):** `create_layer`, `duplicate_layer`, `delete_layer`,
`reorder_layer`, `flatten_image`, `merge_visible_layers`, `add_text`, `apply_drop_shadow`,
`ensure_source_immutable` (single bump at end), and live `flatten` paths inside
`rotate_image` / `resize_canvas` when they flatten.

**Error codes (handle validation + select_* + policy 0009 + structured envelope 0011):**

| Code | Recovery |
|---|---|
| `STALE_HANDLE` | `orient_workspace` **or** use `generation`/`handle` from last structural mutator |
| `FOREIGN_SESSION` | Plugin process restarted — restart MCP flow and re-orient (new epoch) |
| `HANDLE_NOT_FOUND` | Closed/invalid id or item not on claimed image |
| `INVALID_HANDLE` | Bad shape, empty/>64 select list, mixed `image_id`s, non-layer id in `select_layers` |
| `SELECTION_CONFLICT` | Floating selection blocks `set_selected_layers` — anchor or remove float first |
| `POLICY_DENIED` | Target is Source_Immutable protected, or policy name collision / bad checkpoint label |
| `CONFIRM_REQUIRED` | Live flatten/merge without `confirm_destructive=true`. Wire is MCP `isError` + envelope `approval_required: true` (not 2026-07-28 `InputRequiredResult` on this pin) |
| `CHECKPOINT_EXISTS` | Label dir/xcf exists and `overwrite=false` |
| `CHECKPOINT_NOT_FOUND` | Restore label missing |
| `CHECKPOINT_CORRUPTED` | Soft integrity hash mismatch on restore (optional) |
| `PARTIAL_MUTATION` | Mutation incomplete; **do not** blind-retry. Re-orient and inspect state (`state_may_have_changed: true`) |
| `CONNECTION_FAILED` | TCP/socket/timeout to GIMP plugin — ensure plugin running, retry (`retryable: true`) |
| `INTERNAL_ERROR` | Unexpected host/plugin failure; re-orient; check `GIMP_MCP_DEBUG` diagnostics |
| `ALPHA_LOST` | Export lost alpha on **temp** (no final replace); `details.left_on_disk=false`, `final_intact=true`; check `png_color_type`; re-export with `preserve_alpha` / non-flatten |

#### Structured error wire format (track 0011)

Capability: `structured_errors: true`.

MCP tool failures raise FastMCP `ToolError` → client `isError: true` with **single-line** text:

```text
{CODE}: {message} (request_id=req_<hex>) | {"ok":false,"error":{...full envelope...}}
```

- Exactly one line (newlines in message sanitized to spaces).
- JSON uses compact separators `(",", ":")`.
- Envelope fields: `code`, `message`, `retryable`, `approval_required`, `request_id`,
  `transaction_id`, `state_may_have_changed`,
  `rollback_available` (**true** when an open agent undo TX exists for the image;
  plugin SoT via top-level TCP fields; host open-TX hint for pre-TCP errors; default
  **false** when no open agent TX),
  `affected_handles`, `details`.
- Pure helper: `parse_tool_error_text(text) -> dict | None` in `gimp_mcp_security`.
- **Never** return plugin error dicts as successful tool results (`export_image` ALPHA_LOST included).
- `request_id` is a product TCP/audit correlation id (`req_` + uuid4.hex) — **not** MCP
  `_meta.traceparent` / W3C Trace Context (align post major when pin supports it).
- CONFIRM_REQUIRED stays `isError` + `approval_required` on mcp/fastmcp 1.10/2.10 pin;
  MCP **2026-07-28** `InputRequiredResult` / MRTR is deferred.
#### CLI exit codes (`gimp-agent`, track 0012)

Product `CODE_*` and CLI-local codes map to process exits **0–12**. Inspect the live
table with `uv run gimp-agent codes --json`.

| Exit | Meaning | Codes |
|---:|---|---|
| 0 | Success | *(ok)* |
| 1 | Generic failure | `code is None` and `ok=false` |
| 2 | Invalid CLI usage | `CLI_USAGE` (argparse; **`--help` exits 0**) |
| 3 | GIMP or plug-in unavailable | `GIMP_NOT_FOUND`, `PLUGIN_NOT_FOUND` |
| 4 | Transport / auth | `CONNECTION_FAILED`, `AUTH_FAILED`, `BIND_DENIED` |
| 5 | Stale / foreign / invalid handle | `STALE_HANDLE`, `FOREIGN_SESSION`, `INVALID_HANDLE`, `HANDLE_NOT_FOUND`, `SELECTION_CONFLICT` |
| 6 | Policy / path / approval / checkpoint / TX | `POLICY_DENIED`, `CONFIRM_REQUIRED`, `PATH_DENIED`, `EXEC_DISABLED`, `CHECKPOINT_EXISTS`, `CHECKPOINT_NOT_FOUND`, `CHECKPOINT_CORRUPTED`, `TX_MISMATCH`, `TX_NOT_FOUND`, `TX_DEPTH` |
| 7 | Internal / unmapped | `INTERNAL_ERROR`, `METADATA_WRITE_FAILED`, unknown `CODE_*` |
| 8 | Verification failed | `ALPHA_LOST`, `VERIFY_FAILED` |
| 9 | Timeout | `TIMEOUT` |
| 10 | Partial mutation | `PARTIAL_MUTATION` |
| 11 | Output collision | `OUTPUT_COLLISION` |
| 12 | Unsupported | `UNSUPPORTED` |

CLI-local codes (not raised by the TCP plugin): `CLI_USAGE`, `GIMP_NOT_FOUND`,
`PLUGIN_NOT_FOUND`. Envelope: `{ok, exit_code, code, message, data}`. JSON mode:
`--json` flag overrides env `GIMP_AGENT_JSON` (truthy: `1`/`true`/`yes`/`on`).

Commands: `doctor [--strict]`, `probe [--timeout]`, `version`, `codes`,
`save-xcf PATH [--collision fail|version|replace] [--verify-reopen|--no-verify-reopen]`,
`export PATH --format {png,jpeg,webp,tiff} [--collision …] [--verify]`,
`compare PATH_A PATH_B [--require-mutation] [--max-mae …] [--diff-out] [--json]`,
`verify PATH --spec SPEC.json [--json]`,
`recipes [--json]`,
`run RECIPE_ID [--version V] [--output PATH] [--input PATH] [--handle JSON] [--param KEY=VALUE …] [--collision fail|version|replace] [--json]`,
`batch RECIPE_ID --output-dir DIR (--inputs PATH | --input-glob GLOB) [--param …] [--collision version] [--json]`.

**Host-only pixel verification (track 0014):** `compare` and `verify` never open the
plug-in TCP socket (no token required). They import `gimp_mcp_verify` and jail paths
under `GIMP_WORKSPACE_ROOT`. Threshold / expectation failures → `VERIFY_FAILED` →
exit **8**. Pixel / file-size budget exceed → `POLICY_DENIED` → exit **6**.
Unsupported PNG (16-bit, palette, interlaced) or non-PNG `format` in verify →
`UNSUPPORTED` → exit **12**.

### Recipe library (track 0015)

Versioned allowlisted multi-step pipelines so agents run few-decision workflows.

| Surface | API |
|---|---|
| MCP HL (catalog **28**) | `list_recipes`, `apply_recipe` (+ NDE + undo_group tools) |
| CLI | `gimp-agent recipes` / `run` / `batch` |
| Module | `gimp_mcp_recipes.py` (host pure; not EXPECTED plug-in ship) |
| Package data | `gimp_agent/recipes/*.json` via `importlib.resources` |
| Capability | `recipe_library: true` (extension); `batch_interpreter: true` (**0019** constrained BatchProcedure) |

### Non-destructive DrawableFilter tools (track 0016)

Agents append / edit / remove GEGL/GIMP filter nodes on layers without merge-baking
pixels by default. Config is **not** live until `DrawableFilter.update()` — product
tools always call `update()` + `displays_flush()` before return.

| Surface | API |
|---|---|
| MCP HL (catalog **28**) | `apply_nde_filter`, `edit_filter_config`, `remove_nde_filter` (+ TX tools) |
| MCP advanced | `list_drawable_filters` (read-only), `merge_nde_filters` (destructive bake) |
| Module | `gimp_mcp_filters.py` (**9th** EXPECTED plug-in ship file + host py-module) |
| Capability | `nde_filters: true` (extension; not `_CAPABILITY_REQUIRED`) |
| CLI | **none** (session/MCP-first) |

**Apply sequence (locked):** `DrawableFilter.new` → `set_blend_mode(REPLACE default)` →
`set_opacity` → `get_config` + `set_property*` (pspec coerce) → **`update()`** →
`append_filter` → `displays_flush`.

**Edit:** resolve filter on layer → optional `set_visible` → blend/opacity/config →
`_sync_filter` (`update` + flush). **Remove:** `filter.delete()` + flush.
**Merge (advanced):** requires `confirm_destructive=true`; `filter_id` omitted → merge
all; result includes `merged_count`, `merged_filter_ids`, and a note that merged ids
are no longer addressable (re-orient required).

**v1 allowlist (13):** `gegl:gaussian-blur`, `unsharp-mask`, `noise-reduction`,
`pixelize`, `emboss`, `vignette`, `brightness-contrast`, `hue-chroma`, `color-balance`,
`exposure`, `shadows-highlights`; `gimp:levels`, `gimp:curves` (runtime-probed in
plugin). **Not** drop-shadow (manual advanced path remains).

**Soft props:** unknown config keys are never rejected; results report
`applied_props` / `ignored_props` (never silent pass). GObject coerce via pspec
(TYPE_DOUBLE→float, INT→int, BOOLEAN→bool).

**Filter identity:** session-live `filter_id` + layer membership. Invalid after
delete / merge / undo of apply / XCF reopen → `HANDLE_NOT_FOUND`. **No** layer
generation bump on filter ops. Orient fills `layer.filters[]` topmost-first
(defensive: groups / errors → `[]`).

**Agent verify loop:** composite before → apply/edit → tools already sync →
composite after → optional `compare_images` (AE≈0 ⇒ silent failure). Max 3 refine
loops.

**Routing flags (orthogonal):**

| Flag | Meaning |
|---|---|
| `requires_gimp` | Needs live plugin TCP for ≥1 step |
| `requires_open_session` | Needs caller `handle` (no `open_image` of input) |
| `batch_safe` | Recipe property: no unsaved GUI-only state; eligible for headless BatchProcedure when GIMP_OPS are contiguous before HOST_OPS |

| Condition | Path |
|---|---|
| `requires_gimp: false` | Host runner only (no TCP) |
| `requires_open_session: true` | Session + required `handle` |
| `requires_gimp: true`, open session false | Session + `open_image` from `$input_path` (backend `auto`/`session`) |
| `batch_safe: true` + plugin down + contiguous ops | **Headless** via `plug-in-gimp-mcp-batch` (**0019**) |
| `batch_safe: true` + interleaved GIMP/HOST | **UNSUPPORTED** (12) for headless — use session |
| not `batch_safe` + plugin down | **CONNECTION_FAILED** (4) — no silent headless |

### Agent Skills package (track 0020)

Portable runtime playbooks (router `gimp` + focused subskills) ship under repo
`skills/` — see [`skills/README.md`](skills/README.md). CLI: `gimp-agent skills
list|validate|install`. Source-tree discovery + optional `GIMP_MCP_SKILLS_ROOT`.

### Headless BatchProcedure (track 0019)

| Item | Value |
|---|---|
| PDB procedure / `--batch-interpreter` | **`plug-in-gimp-mcp-batch`** (never pretty label alone) |
| Pretty label | `gimp-mcp-recipe` (display only) |
| Host module | `gimp_agent/batch.py` (not EXPECTED ship) |
| Job protocol | JSON v1 path-based GIMP_OPS only; reject `script`/`python`/`eval`/`cmds`/`code` |
| Launcher | `gimp-console -i -d -f -c --batch-interpreter plug-in-gimp-mcp-batch -b <json> --quit` |
| Result SoT | Sibling `{job_stem}.result.json` (not gimp-console stdout) |
| Env | `GIMP_MCP_BATCH_MODE=1`, `GIMP_WORKSPACE_ROOT`; strip `GIMP_MCP_ALLOW_EXEC` / `GIMP_MCP_TOKEN` |
| Timeout | default 120s → `TIMEOUT` exit **9** |
| Product non-path | **not** `python-fu-eval` |

**Interpolation:** whole-value `$name` only (`^\$([A-Za-z_][A-Za-z0-9_]*)$`); single pass;
undefined → error. Path jail every path at step use site.

**Allowlist ≠ MCP surface:** recipe ops invoke plugin TCP / host modules **directly**
— not filtered by `GIMP_MCP_ADVANCED_TOOLS` (e.g. `scale_image` inside `web-export`
with advanced unset).

**Shipped recipes:** `transparent-png`, `exif-normalize`, `web-export`,
`compare-artifacts`, optional `exif-strip` (ExifTool; missing binary → UNSUPPORTED).

**Mutation log:** `{ok, recipe_id, version, backend: "session"|"host"|"headless", steps, artifacts, created_paths}`.
Rollback deletes only `created_paths` (never pre-existing `replace` targets).
CLI: `run`/`batch` accept `--backend {auto,session,headless}` (default `auto`).

**CLI batch:** `--inputs` append and/or `--input-glob` (pathlib; `\` → `/` on Windows);
continue-on-fail; aggregate report; non-zero if any failed; default collision `version`.
Unknown recipe id → exit **12**; bad params → exit **2**. MCP `batch_run` is **not** HL.

**Doctor (non-strict):** default `doctor` is diagnostics-only. When a **required**
check fails without `--strict`, process exit stays **0** and the envelope keeps
`exit_code: 0` with `ok: false`, a failure `code`, and full `data.checks`. Agents
must read `ok` (or checks), not only process exit / `exit_code`. Use
`doctor --strict` for CI/gating (first required failure → non-zero process exit).

**Probe timeout:** socket/read `TimeoutError` → `TIMEOUT` → exit **9** (CODE map).
Transport refuse / auth remain exit **4** (`CONNECTION_FAILED` / `AUTH_FAILED`).

#### Layer policy + checkpoints (track 0009)

**Agent intake order (recommended):**
1. `orient_workspace`
2. `ensure_source_immutable` — protect source roots under parasite-marked `Source_Immutable`
3. `checkpoint_create` before destructive work
4. Live flatten/merge only with `confirm_destructive=true`

| Tool | Notes |
|---|---|
| `ensure_source_immutable` | Locked order: copy working → insert → reorder original into group → hide+lock (content/position/**visibility**). Single gen bump at end. |
| `checkpoint_create` | Jailed `{workspace}/.gimp-mcp-checkpoints/{label}/project.xcf` + `checkpoint.json` after successful XCF. Label: `[A-Za-z0-9._-]+`, max 64; reject `..`, Windows reserved (`CON`…). |
| `checkpoint_restore` | Opens XCF as **new** image (alongside). Prior handles invalid if closed. **Must re-orient.** No tattoo rebind. Optional `close_prior`. |

**Capabilities:** `source_immutable_policy: true`, `checkpoints: true`,
`atomic_xcf_save: true`, `atomic_export: true` (track 0013),
`undo_group_transactions: true` (track 0017).

#### Agent undo-group transactions (track 0017)

Atomic multi-step edit transactions via GIMP `image.undo_group_start/end`.

| Surface | API |
|---|---|
| MCP HL (catalog **28**) | `undo_group_begin`, `undo_group_end`, `undo_group_rollback` |
| MCP advanced | `undo_group_status`, `undo_group_force_close` (+ step `undo`/`redo`) |
| Module | `gimp_mcp_tx.py` (**10th** EXPECTED plug-in ship file) |
| Capability | `undo_group_transactions: true` |
| CLI | **none** |

**Protocol:**
1. `undo_group_begin(handle, label="edit-pass")` → `{transaction_id, depth, timeout_s, …}`
2. short multi-step mutators (≤ **300s wall-clock from begin**, not sliding)
3a. success → `undo_group_end(handle)`
3b. failure → `undo_group_rollback(handle)` then **MUST** `orient_workspace` (gen bump)
4. long work → `checkpoint_create` or segment into multiple TXs

**Timeout:** default 300s from `opened_mono`; env `GIMP_MCP_UNDO_TX_TIMEOUT_S` (clamp 5…3600).
Expired TXs are force-closed deepest-first on begin/end/rollback/status/force_close/
close_image/mutating dispatch (no auto-undo). Max agent nest depth **8** → `TX_DEPTH`.

**Envelope:** `rollback_available: true` + `transaction_id` when an open agent TX exists
for the image (plugin `make_error` kwargs; host `raise_from_plugin_result` forwards top-level
fields as `tool_fail` kwargs; host open-TX hint for pre-TCP host errors).

**Integrity hash:** `xcf_sha256` is integrity of **as-written** bytes — not XCF reproducibility.
Soft compare on restore only. Public `save_xcf` optionally reopens the temp XCF for
structural checks (`verify_reopen`, default true) before atomic replace.

#### Atomic save / export (track 0013)

| Surface | Behavior |
|---|---|
| `save_xcf` / `gimp-agent save-xcf` | Temp sibling (real `.xcf` suffix) → size>0 → optional reopen on temp → sha256 → backup if `replace` → `os.replace` |
| `export_image` / `gimp-agent export` | Temp sibling (real format suffix) → IHDR/alpha on **temp** → sha256 → backup if `replace` → `os.replace` |
| `collision=fail` (public default) | Existing target → `OUTPUT_COLLISION` (CLI exit **11**) |
| `collision=version` | Next free `stem-N.ext` (cap 10000 → `INTERNAL` exit 7) |
| `collision=replace` | Namespaced `{stem}.gimp-mcp.bak{suffix}` (or timestamped) then atomic write |
| `VERIFY_FAILED` | Structural reopen failed **before** replace (exit **8**); final intact |
| `ALPHA_LOST` | Verify failed on temp → no replace; `left_on_disk=false`, `final_intact=true` |

Success results are flat under plugin `results`: `file_path`, `bytes`/`file_size_bytes`,
`sha256`, `collision`, `collision_resolved`, `backup_path`, `atomic: true`, and
`reopen_verified` (XCF only). Export has **no** `verify_reopen` parameter.

**Orient additive fields (layer nodes):** `tattoo` (when available), `protected` (session protected set).

**Live flatten sites requiring `confirm_destructive`:** `flatten_image`, `merge_visible_layers`, free-angle `rotate_image` (flatten branch), `resize_canvas` when fill ≠ transparent. Snapshot/export temp-dup flatten is **not** gated.

#### `select_image(handle)` / `select_layers(handles)`
- **Handles only** (not name/index). Max **64** item handles (`MAX_SELECT_LAYERS` DoS guard).
- `select_image`: resolve by id; **never** `Display.new`; `display: false` if no window.
- `select_layers`: same-image handles → `set_selected_layers`; float → `SELECTION_CONFLICT`.
- Nested layers: resolve by `layer_id`/`item_id`; name match stays **root-only**.

#### Coordinate recovery + EXIF normalize (track 0008)

Capability: `coordinate_exif_normalized: true` (product exposes contract + normalize tool).

| Tool | Where | Notes |
|---|---|---|
| `map_preview_to_image` | host pure | preview → image-pixels; optional `declaration` validate |
| `map_image_to_preview` | host pure | inverse |
| `map_layer_local_to_image` | host pure | `image = local + offset` (prefer absolute `get_offsets`) |
| `map_image_to_layer_local` | host pure | inverse |
| `normalize_image_orientation` | plugin + MCP | handle preferred; `image_index` legacy |

**`normalize_image_orientation(handle?, image_index?, mode="assume_pixels_upright")`**

| Mode | Behavior |
|---|---|
| `assume_pixels_upright` (**default**) | Set both `Exif.Image.Orientation` and `Exif.Photo.Orientation` to **1**; **no** pixel ops. Safe after normal GIMP open (`policy_rotate` may already upright pixels). |
| `trust_tag` | Apply ordered ops for tags 2–8 (`ORIENTATION_OPS`), then set tags to 1. Opt-in only when pixels still match the tag. Tags 5/7: `flip_h` then `rot90`/`rot270`. |

- **Never** call `Image.policy_rotate` for this tool.
- Atomic undo group; `set_metadata` failure → `METADATA_WRITE_FAILED` (no session flag / no gen bump).
- Success: `pixel_orientation_normalized=true`, `generation++`, returns handle + `mode_applied` + `applied`.
- Honesty formula for orient/mapping: session normalize flag **OR** tag is null/1.

#### `get_image_metadata(image_index=0)`
Returns comprehensive metadata about an open image without transferring bitmap data.
Not schema-versioned orientation SoT — prefer `orient_workspace` for agents.
- **image_index:** which open document (default `0`)
- **Returns**: Dictionary containing detailed image information
- **Performance**: Much faster than `get_image_bitmap()` - no image export required
- **Use case**: Quick property checks; orientation → `orient_workspace`

**Returned metadata includes:**
- **Basic properties**: width, height, color mode, precision, resolution, unsaved changes status
- **Structure information**: number of layers/channels/paths with detailed properties
- **Layer details**: name, visibility, opacity, blend mode, dimensions, alpha channel
- **Channel information**: name, visibility, opacity, color
- **Path/vector data**: name, visibility, stroke count
- **File information**: path, URI, basename (if image was saved)

#### `get_gimp_info()` 
Returns comprehensive information about the GIMP installation and runtime environment.
- **Returns**: Dictionary containing detailed GIMP environment information
- **Performance**: Fast - gathers system information without heavy operations
- **Use case**: Environment discovery, troubleshooting, capability detection, optimal support

**Returned information includes:**
- **Version details**: GIMP version string, major/minor/micro versions
- **Directory paths**: user directory, system data, plugins, locale, sysconf directories
- **Session information**: number of open images, file paths, basic image properties
- **PDB capabilities**: Procedure Database availability, sample procedure tests
- **Current context**: foreground/background colors, brush settings
- **System capabilities**: Python modules, MCP features, API version
- **Platform information**: OS details, environment variables, Python version

### 2. General API Tool

#### `call_api(api_path, args=[], kwargs={})`

Execute GIMP 3.0 API methods through PyGObject console.

> **Security:** Disabled by default (`EXEC_DISABLED`). Requires `GIMP_MCP_ALLOW_EXEC=1`.
> Prefer typed tools (`open_image`, adjustments, layers, etc.). This is **Class B**
> (PDB-mediated) exec — distinct from plugin-internal `cmds` (Class A).

**GIMP MCP Protocol:**
- Use api_path="exec" to execute Python code in GIMP
- args[0] should be "pyGObject-console" for executing commands
- args[1] should be array of Python code strings to execute
- Commands execute in persistent context - imports and variables persist
- Always call Gimp.displays_flush() after drawing operations

For orientation, use **`orient_workspace()`** (schema-versioned SoT). For pixels, use
`get_image_bitmap()` / `get_state_snapshot()`. `get_image_metadata()` is a thin
compat metadata dump; `get_gimp_info()` covers environment discovery.
All tools return MCP-compliant data that AI assistants can process directly.

## Basic Method

### Function Call Structure
```json
{
  "api_path": "exec",
  "args": ["pyGObject-console", ["<python_code>"]]
}
```

### Parameters Explanation
- **api_path**: `"exec"` - Accesses GIMP's Procedure Database (PDB) to run a procedure
- **args**: Array with two elements:
  - `"pyGObject-console"` - The PyGObject console procedure name
  - `["<python_code>"]` - Array containing the Python code string to execute
         all commands are executed in the same process context, 
         so ["x=5","print(x)"] will work

## Tested Examples

### Simple Print Command (Console)
```json
{
  "api_path": "exec",
  "args": ["pyGObject-console", ["print('hello world')"]]
}
```

**Result**: Returns `"hello world"` when successful.

### Simple Expression Evaluation
```json
{
  "api_path": "exec",
  "args": ["pyGObject-eval", ["2 + 2"]]
}
```

**Result**: Returns `"4"` - the actual result of the Python expression.

## Important Notes

### String Escaping
- Use single quotes inside double quotes: `["print('hello world')"]`
- Or escape double quotes: `["print(\"hello world\")"]`
- Python code must be properly escaped as a JSON string

### PyGObject Procedure Types
- **`pyGObject-console`**: Executes Python code and returns output.
- **`pyGObject-eval`**: Evaluates Python expressions and returns the actual result value.

### Return Values
- **pyGObject-console**: Returns command output on success, error messages on failure
- **pyGObject-eval**: Returns the actual result of the Python expression
- Print statements from pyGObject-console are returned in MCP response
- Errors will return error messages or exception details

### Limitations
- Commands execute in GIMP's PyGObject environment
- Access to GIMP's Python API and loaded modules

## GIMP 3.0 API Findings

### Working Methods
- **`Gimp.get_images()`**: Returns a list of currently open images
  ```python
  images = Gimp.get_images()  # Returns list of Image objects
  ```

- **`image.get_layers()`**: Gets layers from an image object
  ```python
  layers = image.get_layers()  # Returns list of Layer objects
  ```

- **`image.get_active_layer()`**: Gets the active layer from an image
  ```python
  active_layer = image.get_active_layer()  # Returns Layer object
  ```

- **Get foreground color** 
  ```python
    fg_color = Gimp.context_get_foreground(); 
    print(f'Current foreground: {fg_color}'); 
    print(type(fg_color))
  ```
  
- **Set foreground color** 
  ```python
    from gi.repository import Gegl; 
    black_color = Gegl.Color.new('black'); 
    Gimp.context_set_foreground(black_color); 
    print('Foreground color set to black')`
  ```
  
 - **Basic object access**:
  ```python
  images = Gimp.get_images()
  image = images[0]  # Get first image
  layers = image.get_layers()
  layer = layers[0]  # Get first layer
  ```

- **Draw a line**:
  ```python
Gimp.pencil(Gimp.get_images().get_layers()[0], [0, 0, 200, 200])
Gimp.displays_flush()
    ```

- **Draw a filled ellipse**: 
  ```python
  Gimp.Image.select_ellipse(image, Gimp.ChannelOps.REPLACE, 100, 100, 30, 20)
  Gimp.Drawable.edit_fill(drawable, Gimp.FillType.FOREGROUND)
  Gimp.Selection.none(image)
  Gimp.displays_flush()
  ```

- **Paint curve with paintbrush**:
  ```python
  Gimp.paintbrush_default(drawable, [50.0, 50.0, 150.0, 200.0, 250.0, 50.0, 350.0, 200.0])
  Gimp.displays_flush()
  ```

- **Draw bezier curve**:
  ```python
  path = Gimp.Path.new(image, 'my_bezier_path')
  image.insert_path(path, None, 0)
  stroke_id = path.bezier_stroke_new_moveto(100, 100)
  path.bezier_stroke_cubicto(stroke_id, 150, 50, 250, 150, 300, 100)
  Gimp.Drawable.edit_stroke_item(drawable, path)
  Gimp.Selection.none(image)
  Gimp.displays_flush()
  ```

- **Create new image**:
  ```python
  image = Gimp.Image.new(350, 800, Gimp.ImageBaseType.RGB)
  layer = Gimp.Layer.new(image, 'Background', 350, 800, Gimp.ImageType.RGB_IMAGE, 100, Gimp.LayerMode.NORMAL)
  image.insert_layer(layer, None, 0)
  drawable = layer
  white_color = Gegl.Color.new('white')
  Gimp.context_set_background(white_color)
  Gimp.Drawable.edit_fill(drawable, Gimp.FillType.BACKGROUND)
  Gimp.Display.new(image)
  ```

### Important Tips
- When filling layers with color, ensure layer has alpha channel using `Gimp.Layer.add_alpha()`
- Use `Gimp.Drawable.fill()` for reliable full-layer fills
- Specify colors precisely with rgb(R, G, B) or rgba(R, G, B, A) to avoid transparency issues
- After drawing operations, always call `Gimp.displays_flush()`
- After selection operations for drawing, unselect with `Gimp.Selection.none(image)`
- Use `from gi.repository import Gio` for file operations: `Gio.File.new_for_path(path)`

### Non-Working Methods (GIMP 3.0 Changes)
- **`Gimp.get_active_image()`**: ❌ Does not exist
- **`Gimp.list_images()`**: ❌ Does not exist  
- **`Gimp.get_active_layer()`**: ❌ Does not exist (use `image.get_active_layer()` instead)
- **`from gimpfu import *`**: ❌ gimpfu module not available in GIMP 3.0
- **`Gimp.file_new_for_path()`**: ❌ Use `Gio.File.new_for_path()` instead

### API Structure Insights
- GIMP 3.0 uses GObject Introspection (gi.repository.Gimp)
- PDB object type: `<class 'gi.repository.Gimp.PDB'>`
- Image objects: `<Gimp.Image object at 0x... (GimpImage at 0x...)>`
- Layer objects: `<Gimp.Layer object at 0x... (GimpLayer at 0x...)>`
- The API has significantly changed from GIMP 2.x to 3.0
- Colors are created with `Gegl.Color.new('color_name')`
- File objects use Gio library: `from gi.repository import Gio`

### Tested Working Examples

### Tested Working Example
- **Get layers** 
```json
{
  "api_path": "exec",
  "args": ["pyGObject-console", ["images = Gimp.get_images(); image = images[0]; layers = image.get_layers(); print(f'Found {len(images)} images with {len(layers)} layers')"]]
}
```
- **draw a diagonal line from [0,200] to [200,0]** 
```json
{
  "api_path": "exec",
  "args": ["pyGObject-console", [
    "from gi.repository import Gimp",
    "images = Gimp.get_images()", 
    "image = images[0]", 
    "layers = image.get_layers()", 
    "layer = layers[0]", 
    "drawable = layer",
    "Gimp.context_set_brush_size(2.0)",
    "Gimp.pencil(drawable, [0, 200, 200, 0])",
    "Gimp.displays_flush()"
  ]]
}
```

#### Initialize Working Context
```json
{
  "api_path": "exec",
  "args": ["pyGObject-console", [
    "images = Gimp.get_images()",
    "image1 = images[0]",
    "layers = image1.get_layers()",
    "layer1 = layers[0]",
    "drawable1 = layer1"
  ]]
}
```

## MCP Image Export Integration

### Direct Image Access
The GIMP MCP server now provides dedicated tools for image export that return MCP-compliant Image objects:

#### Using `get_image_bitmap()` / `get_state_snapshot()`
```python
# Visible composite of image 0 as PNG + structuredContent mapping
image = get_image_bitmap()
preview = get_state_snapshot(max_size=512)
```

**Purpose:** Capture the **visible composite** of a specified open image as PNG with
coordinate-mapping metadata (`structuredContent`). Not a single-layer export.

**Parameters (`get_image_bitmap`):**
- `image_index` (integer, default 0): which open image to capture
- `max_width` / `max_height` (integer, optional): full-image fit scale (aspect preserved)
- `region` (dict, optional): crop then optional scale
  - `origin_x`/`origin_y` or `x`/`y`, `width`, `height`
  - optional `max_width`/`max_height` for the cropped region

**Parameters (`get_state_snapshot`):**
- `image_index` (integer, default 0)
- `max_size` (integer, default **512**): max edge for the preview
- `region` (dict, optional): `{x,y,width,height}` or origin_* keys
- `label` (string, optional): agent bookkeeping only

**Usage Modes:**
1. **Full Image:** omit max_* — full-resolution visible composite
2. **Full Image Scaled:** `max_width`+`max_height` or `max_size` — fit scale
3. **Region Extract:** region dict — crop composite, then optional scale
4. **Other document:** `image_index=N`

**Scaling Behavior:** Aspect-preserving fit. Mapping `scale_*` uses **region** dims when
a region is set (`rendered / region_*`); otherwise `rendered / source_*`.

**Response Format:**
```json
{
  "status": "success",
  "results": {
    "image_data": "<base64-encoded-png-data>",
    "format": "png", 
    "width": 800,
    "height": 600,
    "original_width": 1920,
    "original_height": 1080,
    "encoding": "base64",
    "processing_applied": {
      "region_extracted": true,
      "scaled": true,
      "region_coords": {
        "x": 100,
        "y": 100,
        "w": 400,
        "h": 300
      }
    }
  }
}
```

#### Using `get_image_metadata()`
```python
# Fast metadata retrieval without bitmap transfer
metadata = get_image_metadata()

# Example response structure:
{
  "basic": {
    "width": 1920,
    "height": 1080,
    "base_type": "RGB",
    "precision": "8-bit integer",
    "resolution_x": 72.0,
    "resolution_y": 72.0,
    "is_dirty": true
  },
  "structure": {
    "num_layers": 3,
    "num_channels": 0,
    "num_paths": 1,
    "layers": [
      {
        "name": "Background",
        "visible": true,
        "opacity": 100.0,
        "width": 1920,
        "height": 1080,
        "has_alpha": false,
        "blend_mode": "NORMAL",
        "layer_type": "RGB_IMAGE"
      }
    ],
    "channels": [],
    "paths": [
      {
        "name": "Path 1",
        "visible": true,
        "num_strokes": 2
      }
    ]
  },
  "file": {
    "path": "/home/user/image.xcf",
    "basename": "image.xcf"
  }
}
```

#### Using `get_gimp_info()`
```python
# Get comprehensive GIMP environment information
gimp_info = get_gimp_info()

# Example response structure:
{
  "version": {
    "version_method": "3.1.4",
    "detected_version": "3.1.4",
    "available_version_attributes": ["version"],
    "gimp_module_type": "<class 'gi.module.Gimp'>"
  },
  "directories": {
    "user_directory": "/home/user/.config/GIMP/3.1",
    "system_data_directory": "/usr/share/gimp/3.1", 
    "plugin_directory": "/usr/lib/gimp/3.1/plug-ins",
    "available_directory_methods": ["directory", "data_directory", "plug_in_directory"]
  },
  "session": {
    "num_open_images": 2,
    "has_open_images": true,
    "open_image_files": [
      {
        "index": 0,
        "width": 1920,
        "height": 1080,
        "base_type": "RGB",
        "path": "/home/user/photo.jpg",
        "is_dirty": false
      }
    ]
  },
  "pdb": {
    "available": true,
    "sample_procedures": [
      {"name": "file-png-export", "available": true},
      {"name": "gimp-image-new", "available": true}
    ]
  },
  "context": {
    "foreground_color": "rgba(0,0,0,1)",
    "background_color": "rgba(255,255,255,1)",
    "brush_size": 20.0
  },
  "capabilities": {
    "has_python_console": true,
    "mcp_server_running": true,
    "supports_image_export": true,
    "supports_metadata_export": true,
    "supports_gimp_info": true,
    "api_version": "3.0+",
    "gimp_module_attributes": 127,
    "gimp_methods": ["Brush", "Channel", "Context", "Display", "Drawable"],
    "available_modules": [
      {"name": "gi.repository.Gimp", "available": true},
      {"name": "gi.repository.Gegl", "available": true}
    ]
  },
  "system": {
    "platform": "Linux-6.2.0-generic-x86_64",
    "python_version": "3.11.4",
    "environment_vars": {
      "HOME": "/home/user",
      "USER": "user"
    }
  }
}
```

#### When to Use Each Tool
- **`get_image_bitmap()`**: When you need to visually analyze or process the actual image
- **`get_image_metadata()`**: When you need image properties for decision making, validation, or information display
- **`get_gimp_info()`**: When you need environment information for troubleshooting, capability detection, or optimal support

## Plugin Architecture

### Connection Protocol
- **Host**: `127.0.0.1` (default; AF_INET literal — bare `localhost` rejected)
- **Port**: 9877 (default)
- **Transport**: TCP socket with per-message `"auth"` session token
- **Format**: JSON messages
- **Auto-disconnect**: Configurable (default: true); bare string
  `disable_auto_disconnect` is **disabled** — use authenticated JSON
  `{"type":"disable_auto_disconnect","auth":"..."}`

### Command Types
1. **Typed JSON** `{"type":"…","params":{…},"auth":"…"}`: named tools (preferred)
2. **`"get_image_bitmap"` / metadata / list / export tools**: same envelope
3. **JSON with `"cmds"`**: plugin-internal exec — **`EXEC_DISABLED` by default**
4. **MCP `call_api`**: PDB-mediated exec — **gated** unless `GIMP_MCP_ALLOW_EXEC=1`

### Error Handling
- Structured `code` field (`AUTH_FAILED`, `EXEC_DISABLED`, `PATH_DENIED`, …)
- Tracebacks stripped unless `GIMP_MCP_DEBUG=1` (DEBUG is not a policy bypass)
- Graceful handling of missing procedures
- Property name flexibility for different GIMP versions

## Potential Use Cases
- Execute GIMP automation scripts
- Test GIMP Python API functions
- Batch process images
- Create custom GIMP tools and filters
- Debug GIMP Python scripts
