# GIMP MCP — portable agent fragment

Append or merge this snippet into project instruction files (do not overwrite
local maintainer-only AGENTS governance).

## Runtime skill router: `gimp`

When operating GIMP images with this product:

1. `session_probe` (or `gimp-agent probe` / `doctor`)
2. `orient_workspace` — bind handles, not names
3. Coordinate declaration before spatial work
4. `ensure_source_immutable` + `checkpoint_create` before first mutation
5. Route: interactive → `gimp-edit`; bulk/CI → `gimp-batch`; evidence →
   `gimp-verify`; setup → `gimp-install`
6. Prefer the 28 HL tools; advanced only with `GIMP_MCP_ADVANCED_TOOLS=1` + reason
7. Re-orient after structural mutation; max 3 refine loops
8. `save_xcf` then `export_image` separately; never sole-source overwrite
9. Never trust status alone — verify with composite/metrics/artifacts
10. Headless: `plug-in-gimp-mcp-batch` only; never product `python-fu-eval`

Package root: repo `skills/` (or `GIMP_MCP_SKILLS_ROOT`). Install helper:
`uv run gimp-agent skills install --target <dir>`.

Trust: review skill content from untrusted clones before activation. No secrets
in skills or commits.
