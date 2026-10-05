# Shared by setup.ps1 / test.ps1: keep uv, Python, packages and caches inside the AgentDock folder.
$Root = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $Root 'runtime'
$Venv = Join-Path $Runtime 'venv'
$UV = Join-Path $Runtime 'uv\uv.exe'
$env:UV_PROJECT_ENVIRONMENT = $Venv
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Runtime 'python'
$env:UV_CACHE_DIR = Join-Path $Runtime 'cache'
$env:UV_PYTHON_PREFERENCE = 'only-managed'   # same Python on every computer (.python-version), ignore system ones
$env:UV_PYTHON_INSTALL_BIN = '0'             # no python.exe shims in ~/.local/bin
$env:UV_PYTHON_INSTALL_REGISTRY = '0'        # no Windows registry entries
$env:UV_LINK_MODE = 'copy'
$ProgressPreference = 'SilentlyContinue'

function Get-LocalUv {
    if (Test-Path $UV) { return }
    Write-Host '下載 uv…'
    $dir = Join-Path $Runtime 'uv'
    New-Item -ItemType Directory -Force $dir | Out-Null
    $zip = Join-Path $Runtime 'uv.zip'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' -OutFile $zip
    Expand-Archive -Force $zip $dir
    Remove-Item $zip
    if (-not (Test-Path $UV)) {
        Get-ChildItem $dir -Recurse -Include uv.exe, uvx.exe, uvw.exe | Move-Item -Force -Destination $dir
    }
    if (-not (Test-Path $UV)) { throw 'uv 下載內容中找不到 uv.exe' }
}
