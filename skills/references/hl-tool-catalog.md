# High-level MCP tool catalog (28)

Source of truth: `gimp_mcp_surface.HL_TOOL_NAMES`. Prefer these names. Advanced tools
require `GIMP_MCP_ADVANCED_TOOLS=1` on the host MCP process plus a written reason.

| Tool | Purpose |
|------|---------|
| `session_probe` | Connectivity, surface mode, capabilities |
| `restart_server` | Drop/reconnect TCP (prefer probe first) |
| `orient_workspace` | State-manifest orientation SoT (handles, layers, caps) |
| `select_image` | Bind active image by handle |
| `select_layers` | Bind active layer set by handles |
| `open_image` | Open workspace-jailed path into session |
| `close_image` | Close open image |
| `new_canvas` | Create new image |
| `ensure_source_immutable` | Source_Immutable layer policy before mutation |
| `checkpoint_create` | XCF checkpoint snapshot |
| `checkpoint_restore` | Restore checkpoint (then re-orient) |
| `render_visible_composite` | Visible composite PNG + mapping for vision |
| `normalize_image_orientation` | EXIF orientation normalize |
| `map_preview_to_image` | Preview/composite coords → image coords |
| `save_xcf` | Atomic XCF save (temp→replace); collision `fail`/`version`/`replace` |
| `export_image` | Atomic raster export; alpha-preserving by default for PNG |
| `verify_alpha_channel` | Alpha preflight on open document |
| `create_selection` | Unified selection (rectangle/ellipse/by_color/all/none) |
| `compare_images` | Host PNG metrics (MAE / max AE / changed / SSIM) |
| `verify_artifact` | Host artifact dims/format/alpha/sha256 gates |
| `list_recipes` | Shipped versioned recipe catalog |
| `apply_recipe` | Run one allowlisted multi-step recipe |
| `apply_nde_filter` | Append re-editable NDE filter node |
| `edit_filter_config` | Edit NDE filter config/opacity/blend/visible |
| `remove_nde_filter` | Delete NDE filter by filter_id |
| `undo_group_begin` | Start agent multi-step undo transaction |
| `undo_group_end` | Commit undo group |
| `undo_group_rollback` | Roll back undo group (then re-orient if structure changed) |

## Not default HL

Legacy advanced-only aliases (for example get-image-bitmap / check-server style
names on the advanced surface) are not default HL. Do not invent tools that are
not in this catalog.
