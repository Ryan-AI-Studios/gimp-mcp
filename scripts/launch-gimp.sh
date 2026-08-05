#!/usr/bin/env bash
# Launch GIMP GUI with GIMP_WORKSPACE_ROOT set on the GIMP process (plugin path jail).
# Host MCP env (client config) does NOT set plugin env — use this launcher or set env
# on the GIMP process before Tools → MCP → Start MCP Server.
#
# Usage:
#   ./scripts/launch-gimp.sh --workspace /path/to/workspace
#   GIMP_WORKSPACE_ROOT=/path/to/workspace ./scripts/launch-gimp.sh
#   ./scripts/launch-gimp.sh --workspace /ws -- path/to/image.xcf
#
# macOS: if PATH lookup fails, set GIMP_EXE to the app-bundle binary, e.g.
#   export GIMP_EXE="/Applications/GIMP.app/Contents/MacOS/gimp"
#
# Prefer primary CLI: uv run gimp-agent launch-gui --workspace <path>
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.."

WS="${GIMP_WORKSPACE_ROOT:-}"
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace)
      if [[ $# -lt 2 ]]; then
        echo "error: --workspace requires a path" >&2
        exit 2
      fi
      WS="$2"
      shift 2
      ;;
    --)
      shift
      ARGS+=("$@")
      break
      ;;
    *)
      ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ -z "${WS}" ]]; then
  echo "error: GIMP_WORKSPACE_ROOT required via --workspace or env (plugin jail)" >&2
  exit 2
fi

export GIMP_WORKSPACE_ROOT="${WS}"

if [[ ${#ARGS[@]} -gt 0 ]]; then
  exec uv run gimp-agent launch-gui --workspace "${WS}" -- "${ARGS[@]}"
fi
exec uv run gimp-agent launch-gui --workspace "${WS}"
