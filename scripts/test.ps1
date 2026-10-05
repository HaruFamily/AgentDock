# Run the test suite with the same folder-local environment (Test AgentDock.cmd calls this).
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'env.ps1')
Get-LocalUv
& $UV sync --directory $Root
& $UV run --directory $Root pytest -q
Read-Host '按 Enter 關閉'
