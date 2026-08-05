# Release checklist

Operator checklist for a reproducible **0.1.x** release of gimp-mcp.
Primary distribution remains **clone + `uv sync` + `gimp-agent install`**.
Optional PyPI publish is documented last and is **not** required.

## Honesty: wheel ≠ plug-in install

| Artifact | What it is | What it is not |
|---|---|---|
| **Wheel / sdist** (`uv build`) | Host Python package: MCP server modules, `gimp-agent` CLI, recipes | **Not** a GIMP APPDATA plug-in install |
| **`gimp-agent install`** | Deploys the EXPECTED **10-file** ship set into the user GIMP plug-ins dir | Not replaced by `pip install` alone |
| **Plugin ship-set zip** (`scripts/pack_plugin_shipset.py`) | Offline convenience archive of those 10 files + integrity | Convenience only; install CLI remains SoT |

Installing the wheel on a host machine does **not** register the plug-in with
GIMP. Operators must still run `gimp-agent install` (or unpack the ship-set
into the correct plug-ins path).

## Preconditions

- Python **≥3.11** and a working **uv** toolchain
- Repo clean enough to tag (or intentional release branch merge to `main`)
- For **live** doctor / plug-in checks: **GIMP 3.2.4+** with the ship set installed
- Offline gates do **not** require a running GIMP process

## Version triple

Keep these three strings equal (first baseline: **0.1.0**):

1. `pyproject.toml` → `[project].version`
2. `gimp_agent.__version__`
3. `gimp-mcp-plugin.py` → `"plugin_version": "…"`

Verify:

```bash
uv run gimp-agent version
uv run pytest tests/test_release_packaging.py::test_version_triple_sync -q
```

When the package is installed, `importlib.metadata.version("gimp-mcp")` should
match as well (skipped in editable/uninstalled environments).

### SemVer policy (pre-1.0)

| Bump | When |
|---|---|
| **MAJOR** | First **stable** API commitment → **1.0.0**; after 1.x, breaking HL/security/exit-map/recipe schema |
| **MINOR** | Backward-compatible features; **also** pre-1.0 breaking changes (**0.y → 0.y+1**) per SemVer §4 |
| **PATCH** | Bugfixes, docs, non-breaking packaging/metadata |

## Full offline quality gate

From the repo root:

```bash
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run pytest -m "not integration and not slow"
```

Windows note: line endings for the plug-in file may differ from CI Linux LF;
CI Linux LF is the format source of truth. Prefer not mass-reformatting
`gimp-mcp-plugin.py` on Windows solely for CRLF noise.

## Evaluation release gates

Release gates are catalog cases with `gate: release` in `tests/evals/cases.json`.
See **[evaluation.md#release-gates](evaluation.md#release-gates)** for families
and weights.

Fail-closed report (default — **do not** treat a non-zero process exit as
optional for release):

```bash
uv run python scripts/run_eval_report.py --offline
# explicit alias of the same fail-closed contract:
uv run python scripts/run_eval_report.py --offline --require-pass
```

- `overall == PASS` → exit **0**
- otherwise → exit **1** (after writing the report)

Operator inspection only (always exit 0 after write — **not** a release gate):

```bash
uv run python scripts/run_eval_report.py --offline --inspect
# or: --no-require-pass
```

Report path: `output/eval-report.json` (gitignored).

## Build artifacts

```bash
uv build --clear
```

Produces `dist/*.whl` and `dist/*.tar.gz`. Optional verification (slow; **not**
part of default CI `not slow` collection):

```bash
uv run pytest tests/test_release_packaging.py -m slow -q
```

Wheel must include host modules, `gimp_agent` + `recipes/*.json`, and console
entry points (`gimp-agent`, `gimp-mcp-server`). Sdist must include
`pyproject.toml`, `LICENSE`, `README.md`, and `gimp-mcp-plugin.py`. The plug-in
entry is **not** required inside the wheel.

## Optional: live doctor

With GIMP + plug-in installed:

```bash
uv run gimp-agent doctor --strict --json
```

Not required for the offline merge gate.

## Optional: plugin ship-set zip

```bash
uv run python scripts/pack_plugin_shipset.py
# → output/gimp-mcp-plugin-<version>.zip
# → output/gimp-mcp-plugin-<version>.zip.sha256
```

The zip contains the EXPECTED 10 files plus `MANIFEST.txt` (per-file SHA-256).
A sidecar `.sha256` covers the zip archive itself. Prefer
`gimp-agent install` for normal deployment.

## Tag procedure

After offline gates are green **and** the release commit is on **main**:

```bash
git tag -a v0.1.0 -m "Release 0.1.0 — first tagged baseline"
```

- Create the annotated tag on the **main merge commit**, not a long-lived
  feature branch tip, unless that tip is what merges as the release.
- **Do not** `git push origin v0.1.0` (or `--tags`) without explicit operator
  approval.
- CHANGELOG footer links to compare/tag URLs may **404 until** the tag exists
  on the remote — that is expected before the first push.

## Optional: PyPI

Only with credentials and an explicit publish decision:

```bash
uv build --clear
uv publish
```

Not part of the default release DoD. Classifiers and `project.urls` are set for
discoverability when publishing is chosen.

## Live residuals

Live GIMP golden-path smoke, multi-host operator matrices, and demo polish are
**out of band** for this checklist’s offline gate. Treat them as a separate
operator pass after install (see [operator-runbook.md](operator-runbook.md) and
[evaluation.md](evaluation.md) live residual section).

## Related docs

- [evaluation.md](evaluation.md) — scored corpus and `#release-gates`
- [ci-and-testing.md](ci-and-testing.md) — offline CI SoT and markers
- [operator-runbook.md](operator-runbook.md) — start order and install
- [architecture.md](architecture.md) — hybrid MCP + CLI design
- [CHANGELOG.md](../CHANGELOG.md) — Keep a Changelog notes
- [README.md](../README.md) — public front door
