# Subject isolation

Honest product paths for hard cutouts (soft ghosts, multi-character comic art)
where classic global color-select over-selects.

**Do not** use Class A demos (`bg_remove.py`, `bg_remove_iterative.py`) or
`cmds` / free Python as the product path.

## Two paths

```text
Path Classic (GIMP session)
  orient → ensure_source_immutable → create_selection (rect|by_color|contiguous)
  → get_selection_bounds → clear_selection_to_transparent → export_image preserve_alpha
  → host verify_artifact / verify_alpha_channel

Path Host ML (optional rembg; no Class A)
  open/export source into workspace jail → gimp-agent subject-isolate
  → open isolated PNG → optional signature/rect clean with HL → export + verify
```

Host rembg does **not** run inside the GIMP plug-in process. Models download to
user home (`~/.u2net` or `U2NET_HOME`), not the repo.

---

## Path A — Classic contiguous select (session)

Use when the background is a reasonably uniform paper/flat color and you can
seed on **background only**.

### Contiguous (magic wand / fuzzy)

```text
create_selection type=contiguous  x=…  y=…  [threshold=15]  [operation=replace|add|…]
```

| Param | Notes |
|-------|--------|
| `x`, `y` | **Drawable-relative** seed (layer pixel origin). Product locks `sample_merged=False`. If the layer has non-zero offsets, subtract `offset_x` / `offset_y` from canvas-space seeds (from `orient_workspace`). |
| `threshold` | Default **15** (`sample_threshold_int`) |
| `operation` | Multi-seed: first `replace`, then `add` for additional edge seeds |
| `layer_handle` | Prefer explicit layer; invalid id **fails closed** (no silent active fallback) |
| `feather` | **Not** applied for contiguous; use advanced `modify_selection` if needed |

### Multi-seed example

1. Seed a background corner with `operation=replace`.
2. Additional corners / edges with `operation=add`.
3. `get_selection_bounds` — if empty or huge/ambiguous, **stop** and reselect or escalate.
4. `clear_selection_to_transparent` (prefer `layer_handle`).
5. `export_image` with preserve_alpha → `verify_artifact` / `verify_alpha_channel`.

Advanced alias (not HL): `select_contiguous` when `GIMP_MCP_ADVANCED_TOOLS=1`.

Global `by_color` remains available but often over-selects costume greens and soft edges.

---

## Path B — Host rembg (optional ML)

Best quality path for hard isolation (feedback winner: `u2net` on complex art).

### Install (optional; not default CI)

```bash
uv sync --extra subject
# GPU is operator-only (not a product extra): pip install 'rembg[gpu]' if desired
```

Default `uv sync` (no `--extra subject`) **does not** install rembg/onnxruntime.

### CLI

```bash
# Paths must be under GIMP_WORKSPACE_ROOT (workspace jail)
uv run gimp-agent subject-isolate --input in.png --output out.png
uv run gimp-agent subject-isolate --input in.png --output out.png --model isnet-anime
uv run gimp-agent subject-isolate --input in.png --output out.png --alpha-matting
```

JSON envelope + exit map: missing rembg / model download failures → code
`UNSUPPORTED` → exit **12**.

### Models

| Model | When |
|-------|------|
| `u2net` (default) | General subject isolation (~176MB first download) |
| `u2netp` | Smaller / faster approximate model |
| `isnet-anime` | Comic / anime style art |

First run downloads weights (~176MB for u2net) into `~/.u2net` unless redirected.

### Environment

| Env | Role |
|-----|------|
| `U2NET_HOME` | Model cache directory (default `~/.u2net`) |
| `MODEL_CHECKSUM_DISABLED` | rembg/pooch checksum override (operator use; not recommended casually) |
| `OMP_NUM_THREADS` | Limit onnxruntime/OpenMP threads on constrained hosts |
| `GIMP_WORKSPACE_ROOT` | **Required** for path jail on CLI / host module |

### Alpha matting

`--alpha-matting` (default off) softens edges via rembg’s pymatting path — slower,
better hair/soft edges when needed.

### Recipe host op

Optional recipe step op name: `subject_isolate` (`input_path`, `output_path`,
`model`, `alpha_matting`) in `HOST_OPS`.

---

## Decision tree

| Situation | Prefer |
|-----------|--------|
| Clean rectangle/ellipse mask | Classic rect/ellipse → clear |
| Flat unique background color | Contiguous multi-seed → clear (or careful by_color) |
| Soft ghosts / hair / complex BG / multi-character | **Host rembg** then optional HL cleanup |
| Need perfect comic cutout guarantee | **Not** a product guarantee — iterate + verify |

---

## Class A ban

- Do **not** re-enable Class A `cmds` / free Python for demos.
- Do **not** treat `bg_remove.py` / `bg_remove_iterative.py` as the product path
  (legacy demos only; banners point here).
- Contiguous is allowlisted TCP (`select_contiguous` / `create_selection type=contiguous`).

---

## Workspace jail and verify

1. Export or place source under `GIMP_WORKSPACE_ROOT`.
2. Run `subject-isolate` (or classic clear path) into a jailed output path.
3. Verify: `gimp-agent verify` / HL `verify_artifact` / `verify_alpha_channel`.
4. Open isolated PNG in GIMP for optional signature wipe or rect cleanup on HL.

See also: [known-residuals.md](known-residuals.md), [architecture.md](architecture.md),
skills `gimp-edit` cutout protocol.
