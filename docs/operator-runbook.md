# Operator runbook — GIMP MCP

Practical checklist for installing, starting, and operating the Ryan-AI-Studios
gimp-mcp product on a workstation. For architecture context see
[architecture.md](architecture.md). For security detail see
[SECURITY.md](../SECURITY.md).

---

## Start order

Numbered sequence for a working interactive MCP session. Plugin-process env must
be set **before** the plugin server starts (see [Dual-env](#dual-env)).

### One-time / after upgrade

1. **Clone and sync** the fork; install Python deps (`uv sync --group dev`).
2. **Deploy the plugin ship set** (`uv run gimp-agent install`) — EXPECTED **10**
   files into the newest GIMP `3.*` user plug-ins directory.
3. **Verify install** (`uv run gimp-agent doctor --strict --json`) — expect
   `plugin_files` 10/10. Doctor workspace lines report **CLI env only**, not
   the GIMP plugin jail.

### Every interactive session

4. **Set workspace root for the GIMP process** and **launch GIMP** with that env
   (primary):

   ```powershell
   uv run gimp-agent launch-gui --workspace C:\path\to\workspace
   # or:
   powershell -ExecutionPolicy Bypass -File .\scripts\launch-gimp.ps1 -WorkspaceRoot C:\path\to\workspace
   ```

   ```bash
   uv run gimp-agent launch-gui --workspace /path/to/workspace
   # or: ./scripts/launch-gimp.sh --workspace /path/to/workspace
   # macOS: export GIMP_EXE="/Applications/GIMP.app/Contents/MacOS/gimp" if needed
   ```

   Do **not** rely on host MCP `config.toml` alone — that env does not reach the
   plugin process.

5. **Open an image** in GIMP (plugin expects a live document for most tools).
6. **Start the plugin server** — **Tools → MCP → Start MCP Server** (binds
   `127.0.0.1:9877`, writes session token file). Confirm the GIMP console log
   shows `[MCP] Workspace root: …` (not the unset warning).
7. **Start the MCP client** / `gimp_mcp_server.py` (set host
   `GIMP_WORKSPACE_ROOT` in client config for dual-delivery; lazy token load
   with retry if the token file appears slightly late).
8. **Probe** — `session_probe` (MCP) or `uv run gimp-agent probe --json` should
   succeed before heavy edit traffic. Prefer MCP `session_probe` and check
   `plugin_workspace_root` is set and `workspace_root_mismatch` is not `true`.

> **Rule:** plugin first (token available), then MCP client. Reverse order causes
> connection failures until the plugin is up.

### Golden path

After start-order, run the product golden-path smoke (plugin wire names only;
no Class A exec). Full steps, hybrid surface table, and troubleshooting:
**[golden-path.md#golden-path](golden-path.md#golden-path)**.

```powershell
$env:GIMP_WORKSPACE_ROOT = "C:\path\to\workspace"
uv run python scripts/golden_path_smoke.py --dry-run   # default; no socket
uv run python scripts/golden_path_smoke.py --live      # open→…→export→verify
```

---

## Install / upgrade / doctor

### Install (primary)

From the repo root after `uv sync`:

```bash
uv run gimp-agent install
uv run gimp-agent doctor --strict --json
```

Optional wrappers: `scripts/install-plugin.ps1` (Windows),
`scripts/install-plugin.sh` (macOS/Linux).

Useful flags: `--dry-run`, `--source DIR`, `--target DIR` (exact full plugin
dir — no auto-append), `--no-backup`, `--json`.

### Upgrade

After `git pull` or a GIMP minor upgrade (e.g. `3.0` → `3.2`), re-run:

```bash
uv run gimp-agent install
```

By default existing files are overwritten and a timestamped sibling backup
`*.bak.YYYYMMDD-HHMMSS` is written beside each replaced file.

### Backup prune honesty

Backups **accumulate** in the plug-in folder over repeated upgrades. Prune old
`*.bak.*` files **manually** when convenient. There is **no** `--prune-backups`
flag yet.

### Doctor: non-strict vs `--strict`

| Mode | Behavior |
|------|----------|
| `doctor` (default) | Diagnostics; required check failures may still yield process exit **0** with `ok: false` |
| `doctor --strict` | First required failure → non-zero process exit (use for CI/gating) |

Agents must inspect the `ok` field (or `data.checks`), not only the process exit.

### Which directory?

GIMP names its per-user folder after **major.minor** (`3.0`, `3.2`, …) and
creates a fresh one on each minor upgrade. `gimp-agent install` selects the
highest `3.*` config dir automatically. Confirm via **Edit → Preferences →
Folders → Plug-ins**. Launch GIMP at least once before install so the config
folder exists.

---

## Dual-env

Host MCP and the GIMP plugin are **two environment worlds**:

| World | Process | Sets `GIMP_WORKSPACE_ROOT` for |
|-------|---------|--------------------------------|
| **Host** | Client-spawned `gimp_mcp_server.py` | Host dual-delivery / host path helpers |
| **Plugin** | GIMP process + plug-in | open / save / export / **checkpoint** path jail |

- Host client config **cannot** inject env into already-running GIMP.
- Primary launch: `uv run gimp-agent launch-gui --workspace <path>`.
- Scripts: `scripts/launch-gimp.ps1`, `scripts/launch-gimp.sh` (require
  workspace param or env; no hardcoded machine path).
- PowerShell env syntax: `$env:GIMP_WORKSPACE_ROOT = "C:\path\to\workspace"`.
- If `.ps1` is blocked:  
  `powershell -ExecutionPolicy Bypass -File .\scripts\launch-gimp.ps1 -WorkspaceRoot <path>`.
- macOS: override GUI discovery with `GIMP_EXE` when PATH lookup fails  
  (e.g. `export GIMP_EXE="/Applications/GIMP.app/Contents/MacOS/gimp"`).
- After plugin start, `session_probe` always exposes `plugin_workspace_root`,
  `host_workspace_root`, and `workspace_root_mismatch`.

Architecture detail: [architecture.md](architecture.md) (dual-env section).

## Workspace jail

| Variable | Role |
|----------|------|
| `GIMP_WORKSPACE_ROOT` | Path jail root for open/save/export and dual-delivery snapshot writes |

- **Required** for file tools (fail-closed when unset).
- For **plugin** path ops, the variable must be set on the **GIMP process**
  (launcher / launch-gui), not only on the host MCP spawn.
- Snapshot dual-delivery writes go under
  `{GIMP_WORKSPACE_ROOT}/.gimp-mcp-tmp/snapshots/` when the **host** workspace
  is set (default write on). See [SECURITY.md](../SECURITY.md) residuals and
  [performance.md](performance.md).

---

## High-level vs advanced tools

| Mode | How | Surface |
|------|-----|---------|
| **Default** | No special env | **28** high-level tools |
| **Advanced** | `GIMP_MCP_ADVANCED_TOOLS=1` on the **host stdio MCP process** | Full ~90-tool surface |

After flipping `GIMP_MCP_ADVANCED_TOOLS`, restart the **MCP server process and
the LLM client session** (clients cache `list_tools`). Product cannot hot-respawn
Grok/Claude stdio hosts from inside chat — start a **new client session**.

### Dual MCP servers (optional pattern)

Some clients (e.g. Grok) can declare two server blocks:

- `[mcp_servers.gimp]` — HL default (`enabled = true`, no advanced env)
- `[mcp_servers.gimp-advanced]` — `GIMP_MCP_ADVANCED_TOOLS=1` (prefer
  `enabled = false` until needed)

**Prefer one enabled at a time** (overlapping tools / prefixes). See
[adapters/grok/](../adapters/grok/).

`call_api` / plugin exec still require `GIMP_MCP_ALLOW_EXEC=1` separately — do
not enable for untrusted agents. Details: [SECURITY.md](../SECURITY.md).

---

## Live operator matrix

For offline vs live vs headless-on-GA policy, pytest markers, and fixture rules,
see **[ci-and-testing.md](ci-and-testing.md)** (link only — do not duplicate the
matrix here).

Quick live path after start-order:

```bash
uv run gimp-agent probe --json
uv run python scripts/golden_path_smoke.py --live   # preferred product smoke
# Optional legacy: uv run python run_tests.py   # live harness; requires GIMP + plugin
```

---

## Performance / snapshot budgets

Default snapshot max edge, hard caps, and host command timeouts: see
**[performance.md](performance.md)** (link only).

---

## Branch protection

GitHub branch-protection checklist (required check name, GIMP-on-GA policy):
see **[ci-and-testing.md § Branch-protection checklist](ci-and-testing.md#branch-protection-checklist-text-dod)**.

---

## Common failures

| Symptom | Check |
|---------|--------|
| Could not connect | Plugin started? Image open? Port 9877 free? Token file present? |
| Plugin missing under Tools | 10/10 ship files? Correct GIMP version plug-ins dir? Restart GIMP? |
| PATH_DENIED (plugin / checkpoint) | Is `GIMP_WORKSPACE_ROOT` set on the **GIMP process**? Launch via `launch-gui` / `scripts/launch-gimp.*`; confirm log `[MCP] Workspace root:` and `session_probe.plugin_workspace_root` |
| PATH_DENIED (host dual-delivery) | Is `GIMP_WORKSPACE_ROOT` set on the **host** MCP process (client config)? Path under that root? |
| workspace_root_mismatch true | Host and plugin roots both set but differ — align paths or re-launch GIMP with the intended root |
| EXEC_DISABLED | Typed tools only by default — do not expect `call_api` without ALLOW_EXEC |
| TIMEOUT | Host command wall-clock; raise carefully via envs in performance.md |

## Related

- [README.md](../README.md) — quick start and documentation index
- [architecture.md](architecture.md) — hybrid design and capabilities
- [adapters/README.md](../adapters/README.md) — client config examples
