---
name: gimp-batch
description: >
  Deterministic recipes and headless batch for GIMP MCP. Use when: bulk
  convert, CI, list_recipes, apply_recipe, gimp-agent run batch, backend
  auto session headless, batch_safe, plug-in-gimp-mcp-batch.
license: MIT
compatibility: "GIMP 3.2+, gimp-console, Windows-primary"
metadata:
  version: "1.0"
  package: gimp-mcp-skills
---

# gimp-batch

Versioned recipes and bulk paths. Prefer recipes over ad-hoc scripts.

## Surfaces

| Goal | MCP | CLI |
|------|-----|-----|
| List recipes | `list_recipes` | `recipes` |
| One recipe | `apply_recipe` | `run <recipe_id>` |
| Many inputs | — | `batch` (continue-on-fail) |

## Backend tri-state (`--backend`)

| Value | Behavior |
|-------|----------|
| `auto` (default) | Prefer live MCP **session**; on session failure/unavailable, fall back to **headless** only if recipe is `batch_safe` **and** contiguous GIMP_OPS then HOST_OPS |
| `session` | Session only; never headless |
| `headless` | Headless only (eligible recipes); no session attempt |

Interleaved GIMP/HOST steps → session-only (headless returns UNSUPPORTED).

## Headless product path

- Interpreter: **`plug-in-gimp-mcp-batch`** (BatchProcedure)
- UI label may show `gimp-mcp-recipe` — that is not the gimp-console interpreter id
- **Never** use `python-fu-eval` as a product path (forbidden default)
- Result-file is source of truth (host does not parse gimp-console stdout)

Pass interpreter via `--batch-interpreter` when invoking gimp-console integrations
documented by the product; agents should use `gimp-agent run` / `batch` rather
than hand-rolling console flags.

## Session path IO

Atomic `save-xcf` / `export` with collision `fail` | `version` | `replace`.
Workspace jail + loopback still apply.

## Gotchas

- `batch` CLI is multi-input orchestration, not BatchProcedure itself.
- Headless requires `batch_safe` eligibility; do not force headless on interactive recipes.
- Env names: `GIMP_MCP_BATCH_MODE`, `GIMP_MCP_BATCH_TIMEOUT_S`.

## References

- [cli-and-batch](../references/cli-and-batch.md)
- [hybrid-decision-tree](../references/hybrid-decision-tree.md)
- [hl-tool-catalog](../references/hl-tool-catalog.md)
