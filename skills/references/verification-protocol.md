# Verification protocol

## Never success-from-status

`status: success` / `ok: true` alone is **not** evidence. Always obtain pixels,
metrics, or artifact gates before claiming done.

## Live session (MCP HL)

| Tool | Use |
|------|-----|
| `render_visible_composite` | Vision: what the canvas looks like now |
| `verify_alpha_channel` | Alpha preflight before transparent export |
| `compare_images` | Before/after PNG metrics |
| `verify_artifact` | Dims / format / alpha / sha256 on export path |

### ImageContent may not reach the model

`session_probe.image_delivery.client_model_visibility` is always **`unknown`**.
Server emits MCP `ImageContent` and (by default) a jailed filesystem snapshot —
that is **not** proof the client model rendered the PNG.

If ImageContent is omitted or unrendered, open **`filesystem_path`** from
`structuredContent` or the TextContent JSON mapping via host tools
(`{workspace}/.gimp-mcp-tmp/snapshots/snap-*.png`). Prefer that path when visual
proof is mandatory.

Typical loop:

1. Composite (or export) baseline  
2. Mutate (max **3** refine loops)  
3. Composite again → `compare_images` / visual check (or open `filesystem_path`)  
4. On deliverable: `verify_artifact` + alpha checks  

## Host-only (no TCP)

When GIMP MCP is unavailable:

- `gimp-agent compare` — stdlib PNG metrics  
- `gimp-agent verify --spec …` — artifact expectation JSON  

## Silent no-op detection

If metrics show near-zero change when a mutation was intended, treat as failure:
re-orient, confirm handles, adjust plan — do not declare success.

## Max refine

At most **3** automatic refine loops; escalate with evidence if still failing.
