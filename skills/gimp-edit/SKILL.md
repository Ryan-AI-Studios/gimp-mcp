---
name: gimp-edit
description: >
  Interactive HL MCP edit loop for GIMP. Use when: masks, NDE filters,
  selections, undo groups, iterative refine, ensure_source_immutable,
  checkpoint, map_preview_to_image, confirm_destructive flatten.
license: MIT
metadata:
  version: "1.1"
  package: gimp-mcp-skills
---

# gimp-edit

Interactive edit path on a live MCP session. Prefer non-destructive ops.

## Preconditions

1. Fresh orient (`session_probe` + `orient_workspace` + handles) — see **gimp-orient**.
2. `ensure_source_immutable` before first mutation.
3. `checkpoint_create` before first mutation.
4. Coordinate declaration when spatial
   ([coordinate-declaration](../references/coordinate-declaration.md)).

## Prefer

| Prefer | Tools / notes |
|--------|----------------|
| Masks / NDE | `apply_nde_filter`, `edit_filter_config`, `remove_nde_filter` |
| Selections | `create_selection` → `get_selection_bounds` → `clear_selection_to_transparent` |
| Multi-step TX | `undo_group_begin` → … → `undo_group_end` or `undo_group_rollback` |
| Spatial map | `map_preview_to_image`; `normalize_image_orientation` when needed |
| Vision | `render_visible_composite` between steps (prefer `filesystem_path` if ImageContent unrendered). Full preview **1024**; intermediate **768**; detail via region **512–1024** — not full-res vision |

## Cutout sequence (HL)

1. `ensure_source_immutable`
2. `checkpoint_create`
3. `create_selection` — prefer **rectangle** / **ellipse**; use type=by_color only when
   the color is unique and unambiguous (costume greens, soft edges, and similar
   signatures often over-select and wipe working layers)
4. `get_selection_bounds` — if has_selection is false, **STOP** (clear fails closed
   with SELECTION_EMPTY); if area is huge/ambiguous, reselect or escalate
5. `clear_selection_to_transparent` — prefer layer_handle; Source_Immutable-aware
6. `render_visible_composite` / `export_image` / `verify_artifact`

Hard subject isolation (soft ghosts, hair, complex backgrounds) remains **0032**
(rembg / ML pipeline) — do not treat by_color + clear as a perfect cutout.

Advanced remain for invert/grow/feather/border and unrestricted color/pattern
fill (advanced surface). Do **not** enable full advanced surface just for transparent clear.

## Destructive

Live flatten / merge requires confirm_destructive=true (fail-closed). Use
plan-validate-execute. Prefer export-time bake on a duplicate over destroying
the working stack.

## After mutation

1. Re-orient if structure changed.
2. Hand off to **gimp-verify** (`render_visible_composite` → metrics).
3. Max **3** refine loops; escalate if no improvement.
4. Save: `save_xcf` then `export_image` separately — never sole-source overwrite.

## Gotchas

- Declaration honesty: server does not hard-gate spatial mutators.
- Advanced tools only with `GIMP_MCP_ADVANCED_TOOLS=1` + reason.
- After `undo_group_rollback` / `checkpoint_restore`: re-orient before spatial work.
- Snapshot budget: omit `max_*` → default edge 1024 (not full-res); hard max 4096.
  Full preview 1024; intermediate 768; detail via region 512–1024.
  Region-first after a coarse preview; objective full-res via export/verify.
- Huge layer stacks: `orient_workspace(summary_only=True)` (or filter by image index).

## References

- [layer-policy](../references/layer-policy.md)
- [coordinate-declaration](../references/coordinate-declaration.md)
- [verification-protocol](../references/verification-protocol.md)
- [hl-tool-catalog](../references/hl-tool-catalog.md)
