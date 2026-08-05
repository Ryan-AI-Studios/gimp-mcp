# Evaluation corpus and scored rubric

This document is the product SoT for **tool-correctness evaluation** of gimp-mcp:
pixels, metadata, structure greps, security codes, and handle recoverability.
It is **not** an LLM beauty contest or human visual scoring harness.

The machine-readable catalog is `tests/evals/cases.json`. The pure scorer is
`tests/evals/scorer.py` (`score(cases, passed_nodeids) → RubricReport`). Reports
land under gitignored `output/eval-report.json`.

## Modes

| Mode | Meaning | CI default |
|---|---|---|
| **offline** | Host-only pytest asserts (pixels, codes, pure helpers) | **Yes** |
| **structure** | Source greps / schema validate (no live GIMP pixels) | **Yes** |
| **live_residual** | Needs GIMP process / operator path | No |
| **oos_agent** | Agent refinement / human visual loops | No |

Honesty rule: never label a live-only design row as offline. Structure greps
(merge-visible composite, export defaults, batch procedure name) are first-class
non-status asserts.

## How to run

Offline eval package (default collection; sub-second):

```bash
uv run pytest tests/evals -m "not integration and not slow"
```

Full offline quality gate (includes mapped product tests):

```bash
uv run pytest -m "not integration and not slow"
```

Scored rubric report (subprocesses the offline/structure nodeids from the
catalog, writes JSON). **Default exit is fail-closed** (`overall` PASS → 0,
else 1). Use `--require-pass` as an explicit alias; `--inspect` /
`--no-require-pass` write the report and always exit 0 (operator inspection
only). Release operators should follow [release.md](release.md).

```bash
uv run python scripts/run_eval_report.py --offline
# alias: ... --require-pass
# inspect only: ... --inspect
```

Optional micro-bench (`@slow`, not required for merge):

```bash
uv run pytest tests/evals/test_eval_bench.py -m slow
```

## Release gates

<a id="release-gates"></a>

Every catalog case with `gate: release` must pass its offline/structure
nodeids. Overall report `overall` is **PASS** only when all release gates pass
(regardless of weighted offline aggregate).

Release families include:

- **Security** — path jail, Class A exec off, loopback bind, overwrite denied
- **Alpha / export** — RGBA stats/IHDR, no silent PNG fallback, flatten default false
- **Handles** — `STALE_HANDLE`
- **Silent no-op / pixel truth** — identical MAE=0 + `require_mutation` fail; one-pixel delta
- **Coords / source policy / budgets / recipes / golden path / batch structure**

Deep-link fragment: `#release-gates` (GFM heading slug from `## Release gates`).

Category weights (sum 100; **equal-case** pass rate within each category):

| Category | Weight |
|---|---:|
| pixel_metadata | 25 |
| layer_preservation | 15 |
| orientation_coords | 10 |
| recoverability | 10 |
| security | 15 |
| determinism | 8 |
| visual_quality | 7 |
| speed | 4 |
| portability | 3 |
| tool_token_efficiency | 3 |

Weighted `offline_pass_rate` uses only categories that have offline/structure
cases (empty categories are omitted from the denominator).

## Catalog summary

The catalog enumerates the full design competency set (30 CGPT rows + Google
critical coverage) with modes above. Core ship case IDs include:

`E-SEC-PATH-TRAVERSAL`, `E-SEC-EXEC`, `E-SEC-BIND`, `E-HANDLE-STALE`,
`E-PIXEL-DELTA`, `E-PIXEL-IDENTICAL`, `E-ALPHA-PNG`, `E-VERIFY-BUDGET`,
`E-SNAPSHOT-BUDGET`, `E-COORDS-NORMALIZE`, `E-COMPOSITE-STRUCTURE`,
`E-EXPORT-ALPHA-STRUCTURE`, `E-RECIPE-CATALOG`, `E-OFFLINE-GOLDEN`,
`E-BATCH-SMALL` (structure only: `validate_job` ≤5 steps + procedure flags —
**not** a host batch pixel run).

Fixtures for eval growth live under `tests/fixtures/eval/` (16×16 synthetic
PNGs). Regenerator: `uv run python scripts/generate_eval_fixtures.py`.

## Live residual

Rows marked `live_residual` need a running GIMP 3.2 + MCP plugin. Operator path:

1. Install ship set and start the server (see [operator-runbook.md](operator-runbook.md))
2. Optional live harness: [`run_tests.py`](../run_tests.py) (requires GIMP; **not** CI)
3. Policy: [ci-and-testing.md](ci-and-testing.md) — offline only on GitHub-hosted runners

Performance methodology and snapshot edge defaults:
[performance.md](performance.md).

## Related docs

- [release.md](release.md) — operator release checklist (version triple, fail-closed gates, build/tag)
- [ci-and-testing.md](ci-and-testing.md) — offline CI SoT, markers, branch protection
- [performance.md](performance.md) — snapshot budgets and timeouts
- [architecture.md](architecture.md) — hybrid MCP + CLI design
