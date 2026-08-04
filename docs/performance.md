# Performance & snapshot budgets

Product policy for agent vision loops on large canvases (4K / ~50MP). Offline CI
never requires live GIMP or large-image wall-clock timing — see
[ci-and-testing.md](ci-and-testing.md).

## Policy table

| Surface | Default | Cap / notes |
|---------|---------|-------------|
| Snapshot max edge (HL `render_visible_composite`, advanced `get_image_bitmap`, advanced `get_state_snapshot`) | **1024** | Hard max **4096** |
| Region source crop edge | — | **8192** (crop *before* fit-scale; output still hard-max 4096) |
| Host TCP command I/O timeout | **60s** | Clamp **5–600s** via env |
| Adapter outer `tool_timeout_sec` | **300** (examples) | Should exceed host command timeout |
| Verify decoded pixels | 50M trusted / 25M untrusted | **Separate** from snapshot edge policy (0014) |
| Resize-fit padding | **0** | No letterbox |
| Batch headless timeout | 120s | Unchanged (0019) |
| Undo TX wall-clock | 300s | Unchanged (0017) |

### Why region edge 8192 (not 4096)?

Region is a **source-canvas crop** before fit-scale into the snapshot max box.
Operators open 8K canvases; a full-width strip scaled to ≤1024 (or ≤4096 hard)
is a valid detail workflow. The cap stops pathological width/height typos, not
legitimate large crops. **Output** remains governed by the hard max edge.

### Why max command timeout 600s (not 3600)?

This timeout is **per TCP command I/O** (connect + send + recv of one JSON
response), not a long-lived GIMP session or undo-group lifetime. Adapter outer
harness is typically 300s; host ceiling 600s ≈ 2× adapter margin so the host can
surface `TIMEOUT` before the harness hard-kills when env is raised for large work.

## Environment variables

| Env | Default | Meaning |
|-----|---------|---------|
| `GIMP_MCP_SNAPSHOT_MAX_EDGE` | 1024 | Default max edge when agent omits `max_*` / `max_size` |
| `GIMP_MCP_SNAPSHOT_HARD_MAX_EDGE` | 4096 | Absolute ceiling for any requested edge |
| `GIMP_MCP_COMMAND_TIMEOUT_S` | 60 | Host TCP connect/recv timeout (clamped 5–600) |

Related (unchanged): `GIMP_MCP_BATCH_TIMEOUT_S`, `GIMP_MCP_UNDO_TX_TIMEOUT_S`,
`GIMP_MCP_MAX_DECODED_PIXELS`, `GIMP_MCP_SNAPSHOT_WRITE`.

Probe honesty: `session_probe.snapshot_budget` reports **resolved** values plus
nested `env_names`.

## MCP ImageContent has no protocol size limit

The MCP specification (2025-06-18) does **not** define a protocol-level max size
for ImageContent. This product therefore enforces **edge defaults and hard caps**
so vision payloads stay usable in agent context windows.

### Operator awareness (encoded bytes)

Default edge 1024 keeps typical encoded PNG well under ~4 MiB. An explicit
request at the hard max (**4096**) can produce large base64 payloads over stdio.
Prefer:

1. **Region-first** detail crops at 512–1024
2. Dual-delivery **`filesystem_path`** (jailed write under `.gimp-mcp-tmp/snapshots/`)
3. Objective full-res via `compare_images` / `verify_artifact` / `export_image` — not vision

There is **no** soft encoded-byte drop of ImageContent in v1 (deferred residual).

## Agent guidance

| Intent | Recommendation |
|--------|----------------|
| Full-canvas preview | Omit `max_*` → default edge **1024** |
| Intermediate refine | Explicit **768** edge |
| Detail / defect inspect | **Region** + max edge **512–1024** |
| Objective full-res | Export / verify tools — not routine vision |
| Huge layer stacks | `orient_workspace(summary_only=True)` or `image_index` |

## Bench methodology (Windows operator residual)

Live wall times are **not** measured in CI. Operators may fill the results table
below on a machine with GIMP 3.2 + plugin running and a large sample under the
workspace jail. Do **not** invent numbers.

Timed ops (wall-clock, single run or median of 3):

1. `session_probe` (cheap TCP RTT when connected)
2. `orient_workspace` full vs `summary_only=True`
3. Snapshot default (omit `max_*` on `render_visible_composite`)
4. Region snapshot (e.g. 512–1024 max on a crop)
5. `export_image` / atomic export path
6. `compare_images` / `verify_artifact` (host-side; PNG-size-dependent)

### Results (unmeasured)

| Op | Sample | Wall time | Notes |
|----|--------|-----------|-------|
| session_probe | — | *unmeasured* | |
| orient full | — | *unmeasured* | |
| orient summary_only | — | *unmeasured* | |
| snapshot default 1024 | — | *unmeasured* | |
| region snapshot | — | *unmeasured* | |
| export | — | *unmeasured* | |
| compare/verify | — | *unmeasured* | |

Offline unit coverage for resolve/clamp/region reject lives under
`tests/test_snapshot_budget.py`. Full offline suite:

```bash
uv run pytest -m "not integration and not slow"
```

See [ci-and-testing.md](ci-and-testing.md).
