# Layer policy: Source_Immutable, checkpoints, NDE

## Before first mutation

1. `ensure_source_immutable` — protect original pixels under Source_Immutable policy.
2. `checkpoint_create` — recoverable XCF checkpoint.

Both are **agent duties**. The product does not auto-ensure on every `open_image`.

## Prefer non-destructive

| Prefer | Avoid (unless confirm_destructive) |
|--------|-------------------------------------|
| masks | live erase of sole source |
| `apply_nde_filter` / `edit_filter_config` / `remove_nde_filter` | flatten / merge_visible on live doc |
| duplicate + work layer | overwrite sole source file |

## Destructive confirmation

Live flatten / merge paths require confirm_destructive=true (fail-closed). Plan →
validate → execute. Prefer export-time flatten on a **duplicate** when baking
opaque output.

## Undo groups

Multi-step agent edits:

1. `undo_group_begin`
2. … HL mutations …
3. `undo_group_end` on success, or `undo_group_rollback` on failure

After rollback or checkpoint restore: **re-orient** before further spatial work.

## Save / export

- Working project: `save_xcf` (or CLI `save-xcf`)
- Deliverable: `export_image` (or CLI `export`) separately
- Never sole-source overwrite; collision policies: `fail` | `version` | `replace`
