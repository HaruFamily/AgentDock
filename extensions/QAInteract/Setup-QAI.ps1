param(
  [string]$PlatformDirectory,
  [string]$PlatformArchive,
  [string]$PlatformUrl,
  [ValidateSet('codex','opencode','claude-code','claude-desktop')][string]$Kind,
  [string]$ConfigPath,
  [string]$AgentName = 'My Agent'
)
$ErrorActionPreference = 'Stop'
$dependency = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'platform-dependency.json') -Raw | ConvertFrom-Json
if (-not $PlatformDirectory) {
  $hint = Join-Path $env:LOCALAPPDATA 'AgentDock\portable.json'
  if (Test-Path -LiteralPath $hint) {
    $known = (Get-Content -LiteralPath $hint -Raw | ConvertFrom-Json).directory
    if ($known -and (Test-Path -LiteralPath (Join-Path $known 'AgentDock.exe'))) { $PlatformDirectory = $known }
  }
  if (-not $PlatformDirectory) { $PlatformDirectory = Join-Path $env:LOCALAPPDATA 'AgentDock\Portable' }
}
$PlatformDirectory = [IO.Path]::GetFullPath($PlatformDirectory)
$exe = Join-Path $PlatformDirectory 'AgentDock.exe'
if (-not (Test-Path -LiteralPath $exe)) {
  if (Test-Path -LiteralPath $PlatformDirectory) {
    if (@(Get-ChildItem -LiteralPath $PlatformDirectory -Force).Count) { throw 'Destination is not empty. Choose an empty folder.' }
  }
  if (-not $PlatformArchive) {
    $nearby = Join-Path (Split-Path $PSScriptRoot -Parent) $dependency.file
    if (Test-Path -LiteralPath $nearby) { $PlatformArchive = $nearby }
  }
  if (-not $PlatformUrl -and $dependency.url) { $PlatformUrl = $dependency.url }
  $downloaded = $false
  if (-not $PlatformArchive -and $PlatformUrl) {
    $uri = [Uri]$PlatformUrl
    if ($uri.Scheme -ne 'https' -or $uri.UserInfo) { throw 'PlatformUrl must be HTTPS.' }
    $PlatformArchive = Join-Path ([IO.Path]::GetTempPath()) ('AgentDock-' + [Guid]::NewGuid() + '.zip')
    Invoke-WebRequest -Uri $uri -OutFile $PlatformArchive -UseBasicParsing
    $downloaded = $true
  }
  if (-not $PlatformArchive) { throw ('AgentDock is missing. Supply -PlatformArchive or -PlatformUrl for ' + $dependency.file) }
  try {
    $sha = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead([IO.Path]::GetFullPath($PlatformArchive))
    try { $actualHash = ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-','').ToLowerInvariant() }
    finally { $stream.Dispose(); $sha.Dispose() }
    if ($actualHash -ne $dependency.sha256) { throw 'Platform archive checksum mismatch.' }
    Expand-Archive -LiteralPath $PlatformArchive -DestinationPath $PlatformDirectory
  } finally { if ($downloaded) { Remove-Item -LiteralPath $PlatformArchive -Force } }
}
$distribution = Get-Content -LiteralPath (Join-Path $PlatformDirectory 'distribution.json') -Raw | ConvertFrom-Json
if ($distribution.version -ne $dependency.version) { throw 'This QAI package requires AgentDock 0.5.0. Use a matching release.' }
$module = Join-Path $PSScriptRoot 'QAInteract-0.5.0.admod'
$oldNode = $env:ELECTRON_RUN_AS_NODE
try {
  $env:ELECTRON_RUN_AS_NODE = '1'
  if ($Kind -and $ConfigPath) {
    & $exe (Join-Path $PlatformDirectory 'resources\app.asar\dist\platform\setup-qai.js') $PlatformDirectory $module $Kind $ConfigPath $AgentName | Out-Host
  } elseif ($Kind -or $ConfigPath) { throw 'Provide both -Kind and -ConfigPath.' }
  else {
    & $exe (Join-Path $PlatformDirectory 'resources\app.asar\dist\platform\install-module.js') $module (Join-Path $PlatformDirectory 'modules') | Out-Host
  }
  if ($LASTEXITCODE -ne 0) { throw 'QAI setup failed. See the message above.' }
} finally { $env:ELECTRON_RUN_AS_NODE = $oldNode }
Write-Output ('Ready: ' + $PlatformDirectory + '. Open AgentDock.exe and enable QAI, or reload the configured client.')
