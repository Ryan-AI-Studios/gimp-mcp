# CI and testing

This document is the product SoT for offline CI policy, pytest markers, fixture
corpus rules, and the branch-protection checklist. Required merge gates never
start GIMP GUI or install GIMP from apt on GitHub-hosted runners. Snapshot edge
defaults, timeouts, and operator bench methodology live in
[performance.md](performance.md) (offline tests never require live large-image timing).

## Offline vs live vs headless

| Path | What it is | Required for merge? |
|---|---|---|
| **Offline** | Host-only unit + fixture + golden-path E2E (`pytest -m "not integration and not slow"`). No `gi`, no GIMP process. | **Yes** — sole quality SoT |
| **Live** | Operator path with GIMP 3.2 + plug-in TCP `:9877` (`Tools > MCP > Start MCP Server`). Future tests may opt in with `GIMP_MCP_LIVE=1`. | No |
| **Headless on GA** | Running `gimp-console` / BatchProcedure on GitHub-hosted `ubuntu-latest`. | **Document-only** — not implemented as a default-on or required job |

### Why headless GIMP is document-only on GitHub Actions

`ubuntu-latest` is **Ubuntu 24.04 Noble**. The apt GIMP package there is the
**2.10.x** series (e.g. **2.10.36**). That generation is **API-incompatible**
with the GIMP **3.2** plug-in / `BatchProcedure` product path shipped by this
repo (`plug-in-gimp-mcp-batch`, GIMP 3.2.4 operator baseline).

Therefore:

- CI **must not** `apt install gimp` on GA runners as a green gate.
- CI **must not** pretend a GIMP 2.10 apt smoke exercises the GIMP 3.2 ship set.
- Residual for real headless/live automation: **self-hosted Windows** with
  GIMP **3.2.4** (optional future beta images have **no SLA** for merge gates).

## Pytest markers

| Marker | Meaning | Default CI |
|---|---|---|
| *(none)* | Offline unit / fixture / offline E2E | **run** |
| `slow` | Intentionally long host path (**runtime > 1s** on typical CI hardware) | **exclude** |
| `integration` | Live GIMP plugin TCP `:9877` | **exclude** |

- Track **0022 ships zero** `@integration` tests. The marker stays declared for
  future use.
- Offline golden-path E2E (`tests/test_offline_e2e.py`) is **not** `@slow` and
  must stay sub-second.
- Future live tests should gate on env **`GIMP_MCP_LIVE=1`** (convention only
  until such tests exist):

  ```powershell
  $env:GIMP_MCP_LIVE=1; uv run pytest -m integration
  ```

Default quality command (matches CI):

```bash
uv run pytest -m "not integration and not slow"
```

## Fixtures

Committed min corpus under `tests/fixtures/`:

| File | Role |
|---|---|
| `rgb_2x2_opaque.png` | RGB baseline (2×2) |
| `rgba_2x2_alpha.png` | RGBA with non-full alpha |
| `rgb_2x2_delta.png` | Same size, different pixels (nonzero MAE vs opaque) |

- Path helpers: `tests/fixture_paths.py` (`fixture_path`, `copy_fixture_to_workspace`).
- Workspace jail: path-jailed APIs need **`GIMP_WORKSPACE_ROOT`**. Prefer the
  shared `tmp_workspace` fixture in `tests/conftest.py` (sets the env from
  `tmp_path` via `gimp_mcp_security.ENV_WORKSPACE`).
- **Always copy** fixtures into the temp workspace; **never mutate** committed
  binaries in place.
- Root `.gitattributes` marks `tests/fixtures/**` as `binary` / `-text`.
- Regenerator: `uv run python scripts/generate_test_fixtures.py`
- **0025 owns corpus growth; 0022 ships the min-set only.**

Shared PNG builder (not fixtures): `tests/_png_builder.py`
(`build_minimal_png`).

## Branch-protection checklist (text DoD)

Operators apply these in GitHub UI (cannot be automated from the repo alone):

1. Protect branch `main` (or the repo default).
2. Require status check **exactly**: `Lint · Format · Types · Tests`
   (middle dots `·`, not ASCII hyphens).
3. Do **not** require any headless/GIMP job until a self-hosted Windows runner
   with GIMP 3.2.4 is intentional product.
4. Applying settings is an **operator residual** — checklist text in-repo is the
   track DoD; green CI history alone does not flip branch protection.

## Job name immutability

The quality job `name:` in `.github/workflows/ci.yml` is:

```text
Lint · Format · Types · Tests
```

Treat this string as **immutable**. Renaming breaks branch protection that
matches the exact check name. If a rename is ever required, update GitHub
branch protection in the **same** change (comment in `ci.yml` reinforces this).

## Actions pins and security notes

| Pin | Policy |
|---|---|
| `actions/checkout@v7` | Hold major `@v7`. |
| `astral-sh/setup-uv@v9.0.0` | Hold **full tag** `v9.0.0` (do not float `@v9`). `prune-cache` defaults **false**. |
| Node 24 force | `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true` already set in workflow env. |
| `timeout-minutes: 15` | On the quality job. |

**checkout@v7 / `pull_request_target`:** v7 defaults deny unsafe fork PR checkout
for sensitive event types. Current workflow uses `pull_request` only (OK).
**Never** casually set `allow-unsafe-pr-checkout: true` without explicit security
review.

## Windows ruff format noise

Local Windows checkouts may show `ruff format --check` churn from **CRLF** line
endings. **CI Linux LF is the source of truth** — if CI format is green, do not
“fix” purely for local CRLF noise without aligning `.gitattributes` / editor
settings.

## Operator live-matrix index

Historical operator live matrices and deferred ops rows live under
`conductor/` review / deferred notes (governance; not always in the published
tree). Live product path summary:

1. Install ship set: `uv run gimp-agent install`
2. Fully quit and relaunch GIMP 3.2.x
3. `Tools > MCP > Start MCP Server` (loopback TCP `:9877`)
4. Set **`GIMP_WORKSPACE_ROOT`** to a workspace directory for path-jailed FS ops
5. `uv run gimp-agent doctor` / `probe` as needed
6. Optional future: `$env:GIMP_MCP_LIVE=1` then `pytest -m integration`

Live matrices are **not** required CI. Do not re-run every historical ops matrix
as automated GA jobs.

## Foresight (not in 0022)

- Multi-OS matrix jobs should use `fail-fast: false` when introduced.
- `pytest-xdist` parallel CI is deferred (no dep in 0022).

## Related commands

```bash
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run pytest -m "not integration and not slow"
uv run pytest tests/test_offline_e2e.py tests/test_fixtures.py -q
uv run gimp-agent doctor --strict --json
```

## Related docs

- [architecture.md](architecture.md) — hybrid design and capability overview
- [operator-runbook.md](operator-runbook.md) — start-order and live ops checklist
- [performance.md](performance.md) — snapshot budgets (not measured in CI)
- [README.md](../README.md) — public front door
