# Launch GIMP GUI with GIMP_WORKSPACE_ROOT set on the GIMP process (plugin path jail).
# Host MCP env (config.toml) does NOT set plugin env — use this launcher or set env
# on the GIMP process before Tools → MCP → Start MCP Server.
#
# Usage (from repo root or any cwd):
#   powershell -ExecutionPolicy Bypass -File .\scripts\launch-gimp.ps1 -WorkspaceRoot C:\path\to\workspace
#   # or with env already set:
#   $env:GIMP_WORKSPACE_ROOT = "C:\path\to\workspace"
#   powershell -ExecutionPolicy Bypass -File .\scripts\launch-gimp.ps1
# Optional trailing GIMP args:
#   ...\launch-gimp.ps1 -WorkspaceRoot C:\ws -- path\to\image.xcf
#
# Prefer primary CLI: uv run gimp-agent launch-gui --workspace <path>
param(
    [Parameter(Mandatory = $false)]
    [string]$WorkspaceRoot = $env:GIMP_WORKSPACE_ROOT,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$GimpArgs = @()
)

$ErrorActionPreference = 'Stop'

if (-not $WorkspaceRoot) {
    Write-Error "WorkspaceRoot required via -WorkspaceRoot or GIMP_WORKSPACE_ROOT env (plugin jail)."
    exit 2
}

Set-Location "$PSScriptRoot\.."
$env:GIMP_WORKSPACE_ROOT = $WorkspaceRoot

# Strip a lone leading "--" so callers can pass GIMP flags after --
if ($GimpArgs.Count -gt 0 -and $GimpArgs[0] -eq '--') {
    $GimpArgs = $GimpArgs[1..($GimpArgs.Count - 1)]
}

if ($GimpArgs.Count -gt 0) {
    uv run gimp-agent launch-gui --workspace $WorkspaceRoot -- @GimpArgs
} else {
    uv run gimp-agent launch-gui --workspace $WorkspaceRoot
}
exit $LASTEXITCODE
