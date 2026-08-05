# Test fixtures

Small deterministic PNG fixtures for offline CI and host-only verification.
No multi-MB photography.

## Min corpus (root of `tests/fixtures/`)

| File | Purpose |
|---|---|
| `rgb_2x2_opaque.png` | RGB color_type 2 baseline (2×2 solid white) |
| `rgba_2x2_alpha.png` | RGBA color_type 6 with non-full alpha on some pixels |
| `rgb_2x2_delta.png` | RGB color_type 2 same size as opaque but different pixels (nonzero MAE/changed vs opaque) |

## Eval corpus (`tests/fixtures/eval/`)

Product evaluation fixtures used by the scored rubric catalog
(`tests/evals/cases.json`) and optional micro-benches. Synthetic 16×16 only.

| File | Purpose |
|---|---|
| `eval/rgb_16x16_opaque.png` | RGB solid white 16×16 |
| `eval/rgba_16x16_alpha.png` | RGBA with half-alpha checker |
| `eval/rgb_16x16_delta_corner.png` | RGB white with red corner pixel (nonzero MAE vs opaque) |

Path helpers: `tests/fixture_paths.py` (supports nested names such as
`eval/rgb_16x16_opaque.png`). Always **copy** into a workspace
(`copy_fixture_to_workspace`); never mutate these binaries in place. Set
`GIMP_WORKSPACE_ROOT` for path-jailed APIs (see `tmp_workspace` in
`tests/conftest.py`).

## Generators

```bash
uv run python scripts/generate_test_fixtures.py
uv run python scripts/generate_eval_fixtures.py
```

Binaries in git remain CI SoT; re-run the generators after intentional pixel
layout changes and update the hashes below.

## sha256

| File | sha256 |
|---|---|
| `rgb_2x2_opaque.png` | `7a85b76bbe808dab07fd927f64b9c8dbfa00743889eb376162c9ab0bf616b4d5` |
| `rgba_2x2_alpha.png` | `a9d1b76d3d9d086248bc2d4f413f1e2829636a8a0a75b802e7025664a6248264` |
| `rgb_2x2_delta.png` | `68c41bb798155f8ad4c0280b6540e49f18457b263986fa6edbf58dc0821f3cb1` |
| `eval/rgb_16x16_opaque.png` | `7c0cea95ddd259c4b4d95f6129b5ea18d7ff01425d96bf8e62a2965d4ee7250e` |
| `eval/rgba_16x16_alpha.png` | `52882724747507b2c7d05aea642d4504a9e38eba8fa725a965f4823378c66c47` |
| `eval/rgb_16x16_delta_corner.png` | `640ac73972052a0cf70b760856b153f06b30f4a845535f79442a6c1aeabcd749` |

Root `.gitattributes` marks `tests/fixtures/**` as `binary` / `-text`.
