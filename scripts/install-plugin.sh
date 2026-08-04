#!/usr/bin/env bash
# Thin wrapper: deploy full EXPECTED plug-in ship set via gimp-agent install.
# Logic lives in Python (gimp_agent/install.py) — do not reimplement path math here.
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.."
uv run gimp-agent install "$@"
