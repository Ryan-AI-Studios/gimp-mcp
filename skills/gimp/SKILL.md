---
name: gimp
description: >
  Runtime router for GIMP MCP agent work: probe, orient, ensure, route to
  gimp-edit / gimp-batch / gimp-verify / gimp-install. Use when: editing
  images in GIMP, hybrid MCP vs CLI, checkpoints, export, refine loops.
license: MIT
metadata:
  version: "1.1"
  package: gimp-mcp-skills
---

# gimp (runtime router)

Portable runtime playbook for operating GIMP via this product’s hybrid surface
(30 HL MCP tools + `gimp-agent` CLI + recipes + pixel verify + headless batch).

**Not** the maintainer governance skill (`gimp-core` in a gitignored `.agents/`
tree). This package is for *running* image work.

## Required sequence

1. **Probe** — `session_probe` (or CLI `probe` / `doctor` when no MCP).
2. **Orient** — `orient_workspace`; bind **handles**, not names alone.
3. **Declare coordinates** when spatial
   ([coordinate-declaration](../references/coordinate-declaration.md)).
4. **Protect source** — `ensure_source_immutable` + `checkpoint_create` before
   first mutation.
5. **Route**
   | Intent | Skill |
   |--------|-------|
   | Interactive layers / masks / NDE / refine | **gimp-edit** |
   | Bulk / CI / recipes / headless | **gimp-batch** |
   | Evidence / metrics / artifacts | **gimp-verify** |
   | Plugin setup / doctor | **gimp-install** |
6. **Prefer HL** — advanced only with `GIMP_MCP_ADVANCED_TOOLS=1` + reason.
7. **Re-orient** after structural mutation.
8. **Max 3** refine loops; escalate with evidence.
9. **Save then export** — `save_xcf` / CLI `save-xcf`, then `export_image` /
   CLI `export` separately; never sole-source overwrite.
10. **Never trust** `status: success` alone — verify (see **gimp-verify**).
11. **Never assume** MCP ImageContent reached the model
    (`image_delivery.client_model_visibility=unknown`). If vision is missing,
    open `filesystem_path` from structuredContent / TextContent via host tools.
12. **Snapshot budget** — full preview default edge **1024** (hard **4096**);
    intermediate **768**; detail via region **512–1024**. Region-first after a
    coarse preview; huge stacks → orient with summary_only.

## Hard safety

- No Class A plugin exec / arbitrary code execution paths.
- TCP **loopback** only; workspace jail (`GIMP_WORKSPACE_ROOT` on the **GIMP
  process** for plugin path ops — host MCP env alone is not enough).
- No `python-fu-eval` product path.
- Headless uses `plug-in-gimp-mcp-batch` only.

## Decision snapshot

| Work | Path |
|------|------|
| Open doc, iterative vision loop | MCP → gimp-edit |
| Bulk convert, CI recipe | CLI → gimp-batch |
| Offline PNG compare | CLI `compare` / `verify` |
| First-time plugin install | gimp-install |

Details: [hybrid-decision-tree](../references/hybrid-decision-tree.md).

## Subskills

- [gimp-orient](../gimp-orient/SKILL.md)
- [gimp-edit](../gimp-edit/SKILL.md)
- [gimp-batch](../gimp-batch/SKILL.md)
- [gimp-verify](../gimp-verify/SKILL.md)
- [gimp-install](../gimp-install/SKILL.md)

## References

- [hl-tool-catalog](../references/hl-tool-catalog.md)
- [layer-policy](../references/layer-policy.md)
- [verification-protocol](../references/verification-protocol.md)
- [cli-and-batch](../references/cli-and-batch.md)
- [coordinate-declaration](../references/coordinate-declaration.md)
