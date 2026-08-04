# Thin wrapper: deploy full EXPECTED plug-in ship set via gimp-agent install.
# Logic lives in Python (gimp_agent/install.py) — do not reimplement path math here.
$ErrorActionPreference = 'Stop'
Set-Location "$PSScriptRoot\.."
uv run gimp-agent install @args
exit $LASTEXITCODE
