---
name: gimp-edit
description: >
  Interactive HL MCP edit loop for GIMP. Use when: masks, NDE filters,
  selections, undo groups, iterative refine, ensure_source_immutable,
  checkpoint, map_preview_to_image, confirm_destructive flatten.
license: MIT
metadata:
  version: "1.0"
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
| Selections | `create_selection` |
| Multi-step TX | `undo_group_begin` → … → `undo_group_end` or `undo_group_rollback` |
| Spatial map | `map_preview_to_image`; `normalize_image_orientation` when needed |
| Vision | `render_visible_composite` between steps |

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

## References

- [layer-policy](../references/layer-policy.md)
- [coordinate-declaration](../references/coordinate-declaration.md)
- [verification-protocol](../references/verification-protocol.md)
- [hl-tool-catalog](../references/hl-tool-catalog.md)
