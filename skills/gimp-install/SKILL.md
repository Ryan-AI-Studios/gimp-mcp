---
name: gimp-install
description: >
  Operator path to install the GIMP MCP plugin and doctor the environment.
  Use when: first setup, EXPECTED ship files, uv run gimp-agent install,
  doctor --strict, restart GIMP, --no-backup, plug-in missing.
license: MIT
compatibility: "GIMP 3.2+, gimp-console, Windows-primary"
metadata:
  version: "1.0"
  package: gimp-mcp-skills
---

# gimp-install

Host-side plugin ship-set install. Skills package ≠ plug-in files.

## Primary commands

```text
uv run gimp-agent install
uv run gimp-agent doctor --strict
uv run gimp-agent probe
uv run gimp-agent version
```

Optional thin scripts exist; prefer `uv run gimp-agent install`.

## Expectations

- EXPECTED ship set size: **10** files (product lock; skills are not among them).
- After install: **fully quit and restart GIMP**, then start MCP server from the menu.
- `doctor --strict` exits non-zero on first required failure.

## Backups

- Default: timestamped `.bak.YYYYMMDD-HHMMSS` siblings before overwrite.
- `--no-backup` skips backups.
- Backups **accumulate** — prune manually. There is **no** `--prune-backups` flag.

## Uninstall

```text
uv run gimp-agent uninstall --yes
# or preview:
uv run gimp-agent uninstall --dry-run
```

## Security / no secrets

- Never put tokens in skill notes or commit `.env`.
- Workspace: `GIMP_WORKSPACE_ROOT`; transport loopback only.
- This skill does not install agent skills into Grok/Codex — see package README
  and `gimp-agent skills install --target …`.

## Gotchas

- Run install from a full source checkout (or pass `--source`) so all 10 files resolve.
- Host-only modules are never copied into APPDATA plug-ins.

## References

- [cli-and-batch](../references/cli-and-batch.md)
- Package install notes: [../README.md](../README.md)
