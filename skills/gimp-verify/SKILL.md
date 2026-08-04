---
name: gimp-verify
description: >
  Pixel and artifact verification for GIMP agent work. Use when: checking
  exports, alpha, before/after metrics, silent no-ops, compare, verify_artifact,
  never trust status success alone.
license: MIT
metadata:
  version: "1.1"
  package: gimp-mcp-skills
---

# gimp-verify

Evidence over envelopes. Never claim success from status alone.
Never assume MCP ImageContent reached the model.

## Path map

| Situation | Path |
|-----------|------|
| Open project / live MCP session | HL tools below |
| Offline files / no TCP | host-only CLI |

### Live session (MCP HL)

- `render_visible_composite` — what the canvas looks like (ImageContent + TextContent mapping)
- `verify_alpha_channel` — alpha preflight
- `compare_images` — before/after PNG metrics
- `verify_artifact` — dims/format/alpha/sha256 on a path

If ImageContent is omitted or unrendered, open **`filesystem_path`** from
structuredContent / TextContent mapping via host tools (default under
`.gimp-mcp-tmp/snapshots/`). `client_model_visibility` is always `unknown`.

### Host-only (no TCP)

- `gimp-agent compare` — stdlib PNG metrics
- `gimp-agent verify` — artifact vs JSON `--spec`

## Loop

1. Capture baseline (composite or export).
2. Mutate (caller’s edit skill).
3. Re-capture → metrics / visual check (or open `filesystem_path` if no vision).
4. On deliverable: `verify_artifact` (+ alpha when transparent).
5. Max **3** refine loops; escalate with metrics if stuck.

## Silent no-op

Near-zero change when mutation was intended → failure. Re-orient handles,
confirm the right layer, adjust plan.

## References

- [verification-protocol](../references/verification-protocol.md)
- [hl-tool-catalog](../references/hl-tool-catalog.md)
