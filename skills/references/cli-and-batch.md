# CLI and headless batch

## `gimp-agent` surface

```text
gimp-agent install|uninstall|doctor|probe|version|codes
gimp-agent save-xcf|export
gimp-agent compare|verify
gimp-agent recipes|run|batch
gimp-agent skills list|validate|install
```

JSON envelopes: pass `--json` or set `GIMP_AGENT_JSON=1`.

## Recipes

- List: MCP `list_recipes` or CLI `recipes`
- One shot: MCP `apply_recipe` or CLI `run <recipe_id>`
- Many inputs: CLI `batch` (continue-on-fail; not BatchProcedure itself)

## Backend tri-state (`--backend`)

| Value | Behavior |
|-------|----------|
| `auto` (default) | Prefer live MCP **session**; on session failure/unavailable, fall back to **headless** only if recipe is `batch_safe` **and** contiguous GIMP_OPS then HOST_OPS |
| `session` | Session only; never headless |
| `headless` | Headless only (eligible recipes); no session attempt |

Interleaved GIMP/HOST steps → **session-only** (headless returns UNSUPPORTED).

## Headless interpreter

- Product interpreter: `plug-in-gimp-mcp-batch` (BatchProcedure)
- Procedure pretty label may appear as `gimp-mcp-recipe` in GIMP UI — not the
  interpreter id passed to gimp-console
- **Never** product-default `python-fu-eval`
- Result-file is source of truth for headless (host does not parse gimp-console stdout)

Env (names only): `GIMP_MCP_BATCH_MODE`, `GIMP_MCP_BATCH_TIMEOUT_S`.

## Atomic IO collision

`fail` | `version` | `replace` on `save-xcf` / `export` and recipe outputs.
