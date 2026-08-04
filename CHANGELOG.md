# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Package version in `pyproject.toml` remains `0.1.0` until the first tagged
release. Release notes for the product baseline stay under **`[Unreleased]`**
until packaging promotes them to a dated version section.

## [Unreleased]

### Added

- Hardened hybrid product surface: secure MCP stdio bridge + deterministic
  `gimp-agent` CLI sidecar (install, doctor, probe, recipes, batch, skills).
- Default **28 high-level MCP tools** for agent workflows (session probe,
  workspace orientation, open/export, selection, live vision, recipes, NDE
  filters, undo groups, pixel verification). Optional full ~90-tool advanced
  surface via `GIMP_MCP_ADVANCED_TOOLS=1`.
- Live visual feedback via `render_visible_composite` (visible canvas composite
  PNG + coordinate mapping; dual ImageContent + jailed `filesystem_path`).
- Schema-versioned `orient_workspace` state manifest (layers, handles,
  capabilities) as orientation source of truth.
- Stable image/layer handle registry with STALE/FOREIGN validation.
- Coordinate model: preview↔image mapping and EXIF orientation normalize
  (default assume pixels upright).
- Source_Immutable layer policy and XCF checkpoints
  (`ensure_source_immutable`, `checkpoint_create` / `checkpoint_restore`).
- Atomic XCF save and raster export (temp→replace; collision
  `fail` / `version` / `replace`); alpha-preserving PNG path by default with
  fail-closed `ALPHA_LOST` detection.
- Host-only pixel verification: `compare_images` / `verify_artifact` (MCP) and
  `gimp-agent compare` / `verify` (CLI) with MAE, max AE, changed-pixel stats,
  optional global SSIM, and artifact gates.
- Versioned recipe library (`list_recipes` / `apply_recipe`; CLI `run` / `batch`)
  including shipped recipes such as transparent PNG, EXIF normalize, web
  export, and compare-artifacts.
- Non-destructive filter tools: `apply_nde_filter`, `edit_filter_config`,
  `remove_nde_filter` (allowlisted GEGL/GIMP ops).
- Agent multi-step undo transactions: `undo_group_begin` / `end` / `rollback`.
- Plugin install and doctor CLI for the EXPECTED **10-file** ship set; upgrade
  backups (`*.bak.*`); strict doctor for CI gating.
- Constrained headless batch via GIMP `BatchProcedure`
  (`plug-in-gimp-mcp-batch`) — not freeform `python-fu-eval`.
- Portable Agent Skills package under `skills/` with install/validate helpers.
- Client adapter examples for Grok, Codex, and Claude under `adapters/`.
- Offline CI quality gate, pytest markers (`integration`, `slow`), min fixture
  corpus, and documented branch-protection policy.
- Snapshot performance budgets: default max edge **1024**, hard max **4096**,
  host TCP command timeout defaults, and operator performance docs.
- Product documentation set: architecture, operator runbook, security model,
  performance, CI/testing, protocol reference, and this changelog.

### Changed

- Product baseline targets **GIMP 3.2.4+** (Windows primary) and **Python ≥3.11**.
- Public clone and packaging identity: Ryan-AI-Studios fork of maorcc/gimp-mcp.
- License metadata aligned to **GPL-3.0-only** (matches the LICENSE file).
- Default MCP catalog is high-level-first; advanced tools and `call_api` are
  explicit opt-in footguns, not the primary agent interface.
- Offline quality SoT is `uv run pytest -m "not integration and not slow"`;
  live `run_tests.py` remains an optional operator harness only.

### Security

- Loopback-only bind (`127.0.0.1` / `AF_INET`); bare `localhost` denied.
- Per-message session auth token (env or auto-generated token file).
- Class A plugin exec (`cmds` / eval) and Class B MCP `call_api` disabled
  unless `GIMP_MCP_ALLOW_EXEC=1`.
- Workspace path jail (`GIMP_WORKSPACE_ROOT`) for open/save/export.
- Structured error envelope with product codes (including distinct `TIMEOUT`
  vs `CONNECTION_FAILED`); split host/plugin audit JSONL.
- Stdio MCP does not implement HTTP OAuth; credentials come from environment
  (MCP Authorization 2025-06-18 local posture).
