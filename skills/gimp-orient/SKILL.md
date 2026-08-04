---
name: gimp-orient
description: >
  Session probe, workspace orientation, and handle binding for GIMP MCP.
  Use when: starting a session, rebinding handles, coordinate declaration,
  FOREIGN_SESSION or stale handles, after structural layer changes.
license: MIT
metadata:
  version: "1.0"
  package: gimp-mcp-skills
---

# gimp-orient

Establish a trustworthy session picture before any edit.

## Tools

- `session_probe` — connectivity, surface mode, capabilities
- `orient_workspace` — state-manifest orientation SoT
- `select_image` / `select_layers` — bind **handles**, never bare names alone

CLI when no MCP: `gimp-agent probe` / `doctor`.

## Sequence

1. `session_probe` (or CLI `probe`).
2. `orient_workspace` — record image/layer handles, sizes, capabilities.
3. `select_image` / `select_layers` as needed.
4. Emit a **coordinate declaration** before spatial work (see
   [coordinate-declaration](../references/coordinate-declaration.md)).
5. Reject stale handles / FOREIGN_SESSION / missing capabilities honestly —
   re-probe, do not force.

## Re-orient after structure change

Re-run `orient_workspace` after create/delete/reorder/merge/rasterize/relink,
`undo_group_rollback`, `checkpoint_restore`, close/open, `new_canvas`.

## Gotchas

- **Declaration is agent discipline:** the server does **not** hard-fail spatial
  mutators without a declaration. Missing declaration is still a skill violation.
- Prefer handles from the latest orient over remembered names.
- After `restart_server`, full re-probe + re-orient.
- Huge layer trees → `orient_workspace(summary_only=True)` (or filter by image).
- `session_probe.snapshot_budget` reports resolved edges/timeouts (default/hard
  max edge, region edge, host TCP timeout seconds).

## References

- [coordinate-declaration](../references/coordinate-declaration.md)
- [hl-tool-catalog](../references/hl-tool-catalog.md)
