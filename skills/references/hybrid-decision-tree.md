# Hybrid decision tree: MCP vs CLI

## When to use MCP (live GIMP session)

- Open project already in GIMP; iterative layer/mask/NDE work
- Need `render_visible_composite` mid-loop vision
- Handle-bound edits (`select_image` / `select_layers`)
- Interactive refine with max **3** loops

Primary tools: full HL set — start with `session_probe` → `orient_workspace`.

## When to use CLI (`gimp-agent`)

| Verb | Role |
|------|------|
| `install` / `uninstall` / `doctor` / `probe` / `version` / `codes` | Setup & diagnostics |
| `save-xcf` / `export` | Atomic IO via live plugin TCP |
| `compare` / `verify` | Host-only pixel/artifact gates (no TCP) |
| `subject-isolate` | Host-only optional rembg cutout (`uv sync --extra subject`) |
| `recipes` / `run` / `batch` | Versioned recipes; optional headless backend |
| `skills` `list` / `validate` / `install` | This skills package helper |

## Hybrid patterns

1. **Inspect then deterministic op:** MCP orient + composite → CLI `export` / `run`.
2. **Bulk / CI:** CLI `batch` with `--backend auto|session|headless`.
3. **No MCP server:** host-only `compare` / `verify` / `subject-isolate` / `recipes`; headless `run` when `batch_safe`.

## Prefer HL

Use the 30 HL tools by default. Enable advanced tools only with
`GIMP_MCP_ADVANCED_TOOLS=1` and a documented reason. Never Class A plugin exec;
never product-default `python-fu-eval`.
