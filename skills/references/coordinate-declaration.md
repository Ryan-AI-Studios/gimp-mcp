# Coordinate declaration protocol

## Agent duty (skill protocol)

Before any **spatial** edit (selection, paint, transform, map, region snapshot):

1. Call `orient_workspace` (fresh handles + dimensions).
2. State a coordinate declaration: space, units, origin, scale, EXIF notes.
3. Use `map_preview_to_image` when mapping from composite/preview pixels to image space.
4. Call `normalize_image_orientation` when EXIF orientation is not identity and you
   need upright image coords.

## Declaration honesty (gotcha)

The server does **not** hard-fail spatial mutators when a declaration is missing.
This is **agent discipline**, not tool enforcement. Omitting a declaration is a
skill violation even if the tool returns success.

## Re-orient triggers

Re-run `orient_workspace` (and refresh handles) after:

- create / delete / reorder / merge / rasterize layers
- relink, undo TX restore (`undo_group_rollback` / `checkpoint_restore`)
- close / open images, `new_canvas`
- any structural mutation that invalidates handles or bounds

## Reject stale state

Treat `FOREIGN_SESSION`, stale handles, and unsupported capabilities as hard stops —
re-probe / re-orient; do not force edits on unknown structure.
