# Test fixtures (min corpus)

Small deterministic PNG fixtures for offline CI and host-only verification
(track **0022**). No multi-MB photography.

## Purpose

| File | Purpose |
|---|---|
| `rgb_2x2_opaque.png` | RGB color_type 2 baseline (2×2 solid white) |
| `rgba_2x2_alpha.png` | RGBA color_type 6 with non-full alpha on some pixels |
| `rgb_2x2_delta.png` | RGB color_type 2 same size as opaque but different pixels (nonzero MAE/changed vs opaque) |

Path helpers: `tests/fixture_paths.py`. Always **copy** into a workspace
(`copy_fixture_to_workspace`); never mutate these binaries in place. Set
`GIMP_WORKSPACE_ROOT` for path-jailed APIs (see `tmp_workspace` in
`tests/conftest.py`).

## Generator

```bash
uv run python scripts/generate_test_fixtures.py
```

Binaries in git remain CI SoT; re-run the generator after intentional pixel
layout changes and update the hashes below.

## sha256

| File | sha256 |
|---|---|
| `rgb_2x2_opaque.png` | `7a85b76bbe808dab07fd927f64b9c8dbfa00743889eb376162c9ab0bf616b4d5` |
| `rgba_2x2_alpha.png` | `a9d1b76d3d9d086248bc2d4f413f1e2829636a8a0a75b802e7025664a6248264` |
| `rgb_2x2_delta.png` | `68c41bb798155f8ad4c0280b6540e49f18457b263986fa6edbf58dc0821f3cb1` |

## Ownership

**0025 owns corpus growth; 0022 ships min-set only.** Do not expand this tree
with large eval photography here — that belongs to track 0025.

Root `.gitattributes` marks `tests/fixtures/**` as `binary` / `-text`.
