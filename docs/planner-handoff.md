# Planner Handoff — gimp-mcp

> **This is a living document.** If you are a planning AI picking up work in this repo and you learn
> something a fresh planner would need — a new gotcha, a policy change, a stale assumption here —
> **update this file before you finish your turn.** Keep it light: link to `conductor/conductor.md`
> and design docs for detail rather than duplicating them here. This file answers
> **"what do I need to know before I touch anything,"** not "what happened."

---

## 1. What this is

**gimp-mcp** is a hardened, agent-facing bridge between AI harnesses (Grok Build, Codex, Claude, etc.)
and **GIMP 3.2** on Windows. It is a fork of [maorcc/gimp-mcp](https://github.com/maorcc/gimp-mcp)
owned at [Ryan-AI-Studios/gimp-mcp](https://github.com/Ryan-AI-Studios/gimp-mcp).

**Product stance:** full hybrid architecture (secure MCP + deterministic CLI sidecar + orientation
manifests + pixel verification + recipes + skills) — **not** an MVP wrap of upstream. Design
authority:

| Doc | Path |
|---|---|
| Deep architecture | `C:\dev\GIMP\docs\CGPT.md` |
| Executive hybrid recommendation | `C:\dev\GIMP\docs\Google.md` |
| This handoff | `docs/planner-handoff.md` (this file) |

Cross-cutting agent tools used across Ryan-AI-Studios repos:

| Tool | Role |
|---|---|
| **Ledgerful** | Repo change intelligence, ledger provenance, `verify` gates |
| **ai-brains** | Cross-session memory, preflight, pinned DECISION/CONSTRAINT/HOTSPOT |

---

## 2. Repo / path map

| Path | What it is |
|---|---|
| `C:\dev\GIMP\gimp-mcp` | **Execution git repo** (this fork). Code, CI, quality gates. |
| `C:\dev\GIMP` | Workspace parent: Grok project MCP (`.grok/`), CLI wrappers (`bin/`), design docs (`docs/`). Not the product git root. |
| `origin` | `https://github.com/Ryan-AI-Studios/gimp-mcp.git` (push target) |
| `upstream` | `https://github.com/maorcc/gimp-mcp.git` (pull / optional PR source) |
| GIMP app | `C:\Program Files\GIMP 3\` (3.2.4+); console: `gimp-console-3.2.exe` |
| Plug-in install | `%APPDATA%\GIMP\3.2\plug-ins\gimp-mcp-plugin\` |

### Gitignored local governance (on purpose)

These are **local-only** (see root `.gitignore`). Do not commit them; do not plan tracks that require
publishing them:

| Path | Purpose |
|---|---|
| `.agents/` | Skills (onboarding, implement, ledgerful, ai-brains, codex-review, gimp-core) |
| `AGENTS.md` | Compact agent contract for this repo |
| `conductor/` | Track registry, specs, plans, deferred debt |
| `.ledgerful/` | Ledgerful state |
| `.env` | ai-brains project/session IDs |

---

## 3. Where things live (read in this order)

1. **`docs/planner-handoff.md`** (this file) — cold-start planner orientation.
2. **`conductor/conductor.md`** — track registry and status SoT (local, gitignored).
3. **`conductor/deferred.md`** — deferred findings to roll into related tracks.
4. **`conductor/<track>/spec.md` + `plan.md`** — what/why/DoD and phased how.
5. **`conductor/templates/0000-Description/`** — skeleton for new tracks; copy, don't freestyle.
6. **`AGENTS.md`** — verify gates, ledger categories, stop rules.
7. **`.agents/skills/onboarding/SKILL.md`** — session start + authority order.
8. **`.agents/skills/implement/SKILL.md`** — track execution loop (TDD → review → full gate).
9. **`C:\dev\GIMP\docs\CGPT.md` / `Google.md`** — full product architecture.
10. **`README.md`**, **`GIMP_MCP_PROTOCOL.md`**, **`docs/best_practices.md`** — upstream-oriented runtime docs.

---

## 4. Track lifecycle

1. **Placeholder** — gap identified; thin `spec.md` with status `Proposed — placeholder`.
2. **Full spec/plan** — research **live** GIMP 3.2 / MCP / tool versions (do not trust training data);
   ground claims in current code; check `deferred.md`. Status → `Ready — not started`.
3. **Execute** — `implement` skill; open ledger TX (`ledgerful ledger start ...`); TDD; review rounds;
   full gate.
4. **Review fold-in** — verify every external AI claim against code before adopting.
5. **Completed** — `review.md` with DoD evidence; flip status in `conductor.md`; commit ledger TX.

Numbering is sequential and stable (`####-PascalDescription`). Check the highest `####-` dir before
minting. Track IDs are creation order, not execution order.

### Current product sequence (summary)

```
0001 Quality gates bootstrap — Completed (87bc111)
  → 0002 Quality surface stabilization — NEXT (full clean lint/format/types/tests)
  → 0003 Security … through 0028 Final product polish (v1)
```

**28 tracks** (0001–0028) cover bootstrap → stabilization → security/vision → agent surface → CLI →
recipes → packaging → golden path → v1 polish. Most are **Proposed placeholders** until a full
planning pass. **0002 is Ready.** Authoritative table: `conductor/conductor.md`.

---

## 5. Quality bar and environment

### One-time / clone setup

```powershell
cd C:\dev\GIMP\gimp-mcp
uv sync --group dev          # creates .venv; pins ruff/basedpyright/pytest
uv run pre-commit install    # optional local hook
ledgerful init               # if .ledgerful missing
ai-brains context            # project .env for vault
```

**Does the uv environment need setup?** On a fresh clone: **yes** (`uv sync --group dev`). On this
machine after initial bootstrap: **already done** — `.venv` exists and imports `gimp_mcp_server`.
Re-run `uv sync` after `pyproject.toml` / `uv.lock` changes or when tools fail with missing packages.

### Full gate (must pass before track finalize)

```powershell
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run pytest -m "not integration and not slow"
ledgerful verify --scope full
```

Pinned tools (as of 2026-08 bootstrap; re-check PyPI when bumping):

| Tool | Pin |
|---|---|
| ruff | 0.16.1 |
| basedpyright | 1.39.9 |
| pytest | 9.1.1 |
| pre-commit | ≥4.6.1 |
| Python | ≥3.11 (CI: 3.13 via uv) |

CI: **`.github/workflows/ci.yml` is the sole quality SoT** (`actions/checkout@v7`,
`astral-sh/setup-uv@v9`, `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true`). Legacy `lint.yml` was
retired in 0002 (no branch protection required the Lint job name). Pre-commit uses
`ruff-check` then `ruff-format` at rev `v0.16.1` (local may `--fix`; CI is check-only).

### Quality surface policy (post-0002)

| Gate | Surface |
|---|---|
| **Ruff lint + format** | Full product Python: `gimp_mcp_server.py`, `gimp-mcp-plugin.py`, demos, `run_tests.py`, `scripts/**`, `tests/**` |
| **basedpyright** | `gimp_mcp_server.py` + `tests/` only |
| **pytest offline** | `tests/` unit tests only (`testpaths = ["tests"]`) |

**Excludes / ignores (justified):**

- **Ruff `extend-exclude`:** `.agents`, `conductor`, `.ledgerful`, `docs`, `*.md` — governance dirs; markdown excluded because `ruff format` rewrites fenced code blocks and we do not format docs via ruff.
- **Plugin per-file:** `E402`, `I`, `RUF001`, `RUF002` on `gimp-mcp-plugin.py` — GIMP bootstrap (`gi.require_version` before `gi.repository`); do **not** reorder imports to silence E402; do **not** bulk-ASCII-ize plugin unicode.
- **Global ruff ignores (deferred cleanup):** `B904` (~79 raise-without-from in server wrappers), `E501` (long tool docstrings). See `conductor/deferred.md`.
- **basedpyright permanent exclude:** `gimp-mcp-plugin.py` (GIMP-embedded `gi.repository` — no host stubs), plus demos/`scripts/**`/`run_tests.py`.
- **Type-hardening dials (load-bearing, not 0002):** `reportAny` / `reportUnknown*` = false for usable `standard` mode with mcp/fastmcp; candidate for a later type-hardening track — do **not** flip to basedpyright `recommended`/`all` casually.

**Runtime deps:** `mcp` / `fastmcp` are unpinned in `pyproject.toml` `dependencies`; **`uv.lock` is the pin SoT**. Optional lower bounds later — do not treat open ranges as unpinned chaos.

**Live scripts** live under `scripts/` (not pytest-collected):

- `scripts/add_text_metadata.py`
- `scripts/continuous_edit_test/` (+ `files/` fixtures)

### Live GIMP (integration only)

1. Restart GIMP after plug-in install.
2. Open an image.
3. **Tools → MCP → Start MCP Server** (loopback `:9877`).
4. Then: `pytest -m integration` or `python run_tests.py` / MCP tools / `scripts/*`.

Offline CI must not require GIMP GUI.

### Ledgerful policy notes

- `.ledgerful/` is **gitignored** (local-only). `rules.toml` must have a **single** `required_verifications` under `[global]` (duplicate keys cause parse fail). Operational verify SoT is `config.toml` `[verify].steps` — keep in sync with CI.
- After editing rules: `ledgerful config verify` and `ledgerful change-context --json` must not show rules TOML parse failures.

---

## 6. Architecture invariants (do not violate casually)

Known upstream defects (must remain visible until fixed by tracks):

| Issue | Symptom | Track |
|---|---|---|
| #17 composite | Snapshot/top-layer buffer ≠ visible canvas | 0004 |
| #16 alpha | “Success” export without transparency | 0005 |
| Trust boundary | Unauthenticated TCP + arbitrary `cmds`/exec | 0003 |
| Tests | Exception-only “pass” without pixel truth | 0014 / 0022 |

Hard rules for product work:

- Prefer **MCP** for interactive orient/edit/snapshot loops; **CLI sidecar** for atomic XCF/export/batch.
- Never trust `status: success` alone — require composite/alpha/objective checks when vision matters.
- Track layers by **stable handles**, not names (once 0007 lands; until then prefer IDs over names).
- Prefer non-destructive edits (masks, NDE filters) over flatten/erase.
- Disable arbitrary Python exec in production plugin paths (0003).
- Bind TCP to `127.0.0.1` only; confine paths to workspace roots.
- Max **3** automatic refine loops; escalate subjective failures to humans.

---

## 7. General gotchas / working discipline

- **Governance is not in git.** `.agents/`, `AGENTS.md`, and `conductor/` are local. Losing the disk
  loses track history unless backed up elsewhere — treat conductor updates carefully; no
  `git checkout -- conductor/` recovery.
- **Never accept AI claims at face value** — spot-check load-bearing GIMP API / security / vision
  claims against live code and current GIMP 3.2 docs.
- **Research currency always.** Pin versions from PyPI / GitHub / GIMP release notes at planning time.
- **Split oversized tracks.** Security, vision, and CLI are separate on purpose; do not merge 0002–0008
  into one mega-PR.
- **Upstream sync:** `git fetch upstream` then intentional merge/rebase onto a feature branch; keep
  hardening divergences documented.
- **Do not push secrets:** `.env`, API keys, vault paths.
- **Ledgerful process policy** allows `uv`, `ruff`, `basedpyright`, `pytest`, `python` in verify steps —
  keep `config.toml` verify steps in sync with CI.
- **WSL vs native:** control native Windows GIMP from native Windows agents; avoid WSL↔Windows TCP/path
  bridging by default.

---

## 8. Session start checklist (planners and implementers)

```powershell
cd C:\dev\GIMP\gimp-mcp
ai-brains preflight --summary
ledgerful doctor
ledgerful audit
ledgerful ledger status --compact
# Read:
#   docs/planner-handoff.md
#   conductor/conductor.md
#   conductor/deferred.md
#   assigned track spec.md + plan.md
```

If indexes are empty: `ledgerful index --incremental`.

---

## 9. Minting a new track

1. Copy `conductor/templates/0000-Description/` → `conductor/####-Name/`.
2. Fill every section of `spec.md` (especially **Definition of Done**).
3. Write phased `plan.md` mapped to DoD items.
4. Register a row in `conductor/conductor.md`.
5. Check `deferred.md` for roll-ins.
6. Update **this file** only if planners need a new durable gotcha or path change.

---

## 10. Snapshot

| Item | Value |
|---|---|
| Date | 2026-08-02 |
| GIMP | 3.2.4 native Windows |
| Fork tip | origin = Ryan-AI-Studios/gimp-mcp |
| Quality gates | full product ruff + format; basedpyright server+tests; offline pytest; ledgerful verify |
| Active focus | **0002 implement done (await orchestrator review/close)** → then 0003 SecurityHardening |
| Track count | 0001–0028 (see conductor.md) |

---

## Changelog (handoff only)

| Date | Change |
|---|---|
| 2026-08-02 | Initial planner handoff for gimp-mcp full-product program |
| 2026-08-02 | Expanded conductor to 28 placeholder tracks; 0001 Completed; 0002 stabilization Ready |
| 2026-08-02 | Full plan for 0002: pins ruff 0.16.1 / basedpyright 1.39.9 / pytest 9.1.1; roll-in rules.toml + script layout |
| 2026-08-02 | Folded AI-review.md into 0002: ruff-check hooks, unicode policy, rules apply check, CI dedupe, dead T201, DoD-8 ignores |
| 2026-08-02 | 0002 quality surface policy: full product ruff; plugin type exclude; uv.lock SoT; lint.yml retired; scripts/ layout |
