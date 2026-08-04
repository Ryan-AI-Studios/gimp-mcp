# Operator runbook — GIMP MCP

Practical checklist for installing, starting, and operating the Ryan-AI-Studios
gimp-mcp product on a workstation. For architecture context see
[architecture.md](architecture.md). For security detail see
[SECURITY.md](../SECURITY.md).

---

## Start order {#start-order}

Numbered sequence for a working interactive MCP session:

1. **Clone and sync** the fork; install Python deps (`uv sync --group dev`).
2. **Deploy the plugin ship set** (`uv run gimp-agent install`) — EXPECTED **10**
   files into the newest GIMP `3.*` user plug-ins directory.
3. **Verify install** (`uv run gimp-agent doctor --strict --json`) — expect
   `plugin_files` 10/10.
4. **Fully quit and relaunch GIMP** after install or upgrade.
5. **Set workspace jail** — `GIMP_WORKSPACE_ROOT` to a directory agents may
   read/write (required for open/save/export).
6. **Open an image** in GIMP (plugin expects a live document for most tools).
7. **Start the plugin server** — **Tools → MCP → Start MCP Server** (binds
   `127.0.0.1:9877`, writes session token file).
8. **Start the MCP client** / `gimp_mcp_server.py` (lazy token load with retry if
   the token file appears slightly late).
9. **Probe** — `session_probe` (MCP) or `uv run gimp-agent probe --json` should
   succeed before heavy edit traffic.

> **Rule:** plugin first (token available), then MCP client. Reverse order causes
> connection failures until the plugin is up.

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

## Workspace jail

| Variable | Role |
|----------|------|
| `GIMP_WORKSPACE_ROOT` | Path jail root for open/save/export and dual-delivery snapshot writes |

- **Required** for file tools (fail-closed when unset).
- Snapshot dual-delivery writes go under
  `{GIMP_WORKSPACE_ROOT}/.gimp-mcp-tmp/snapshots/` when the workspace is set
  (default write on). See [SECURITY.md](../SECURITY.md) residuals and
  [performance.md](performance.md).

---

## High-level vs advanced tools

| Mode | How | Surface |
|------|-----|---------|
| **Default** | No special env | **28** high-level tools |
| **Advanced** | `GIMP_MCP_ADVANCED_TOOLS=1` on the **host stdio MCP process** | Full ~90-tool surface |

After flipping `GIMP_MCP_ADVANCED_TOOLS`, restart the **MCP server process and
the LLM client session** (clients cache `list_tools`).

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
# Optional: uv run python run_tests.py   # live harness; requires GIMP + plugin
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
| PATH_DENIED | `GIMP_WORKSPACE_ROOT` set and path under that root? |
| EXEC_DISABLED | Typed tools only by default — do not expect `call_api` without ALLOW_EXEC |
| TIMEOUT | Host command wall-clock; raise carefully via envs in performance.md |

## Related

- [README.md](../README.md) — quick start and documentation index
- [architecture.md](architecture.md) — hybrid design and capabilities
- [adapters/README.md](../adapters/README.md) — client config examples
