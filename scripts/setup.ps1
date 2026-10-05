# AgentDock.exe runs this on the first start and whenever uv.lock changed.
param([Parameter(ValueFromRemainingArguments = $true)] $Rest)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'env.ps1')
$Host.UI.RawUI.WindowTitle = 'AgentDock 準備中'
Write-Host '正在把 Python 與套件準備到 AgentDock\runtime（第一次約需 1–2 分鐘，之後不會再出現）…'
Write-Host ''
try {
    Get-LocalUv
    & $UV sync --directory $Root
    if ($LASTEXITCODE -ne 0) { throw "套件安裝失敗（uv 結束碼 $LASTEXITCODE）" }
    Copy-Item -Force (Join-Path $Root 'uv.lock') (Join-Path $Venv 'agentdock-synced.lock')
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $Root 'Start AgentDock.cmd')  # replaced by AgentDock.exe
    & (Join-Path $Venv 'Scripts\python.exe') -m agentdock.launch @Rest
    Write-Host ''
    Write-Host '完成，AgentDock 已啟動。這個視窗會自動關閉。'
    Start-Sleep -Seconds 2
} catch {
    Write-Host ''
    Write-Host "失敗：$_" -ForegroundColor Red
    Read-Host '按 Enter 關閉'
    exit 1
}
