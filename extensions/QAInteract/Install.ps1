param(
  [string]$PlatformDirectory,
  [ValidateSet('codex','opencode','claude-code','claude-desktop')][string]$Kind,
  [string]$ConfigPath,
  [string]$AgentName = 'My Agent',
  [string]$ReleaseDirectory
)
$ErrorActionPreference = 'Stop'
$version = '0.5.0'
$baseUrl = 'https://github.com/HaruFamily/AgentDock/releases/download/v' + $version
if (($Kind -and -not $ConfigPath) -or ($ConfigPath -and -not $Kind)) { throw 'Provide both -Kind and -ConfigPath.' }
$parent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$work = Join-Path $parent ('AgentDock-QAI-' + [Guid]::NewGuid())
New-Item -ItemType Directory -Path $work | Out-Null
function Get-Asset([string]$Name) {
  $path = Join-Path $work $Name
  if ($ReleaseDirectory) { Copy-Item -LiteralPath (Join-Path $ReleaseDirectory $Name) -Destination $path }
  else { Invoke-WebRequest -Uri ($baseUrl + '/' + $Name) -OutFile $path -UseBasicParsing -TimeoutSec 120 }
  return $path
}
try {
  $sums = Get-Asset 'SHA256SUMS.txt'
  $name = 'QAInteract-' + $version + '.zip'
  $zip = Get-Asset $name
  $matches = @(Get-Content -LiteralPath $sums | Where-Object { $_ -match ('^[a-fA-F0-9]{64}  ' + [Regex]::Escape($name) + '$') })
  if ($matches.Count -ne 1) { throw 'Missing or ambiguous QAI checksum.' }
  $expected = $matches[0].Substring(0,64).ToLowerInvariant()
  $sha = [Security.Cryptography.SHA256]::Create()
  $stream = [IO.File]::OpenRead($zip)
  try { $actual = ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-','').ToLowerInvariant() }
  finally { $stream.Dispose(); $sha.Dispose() }
  if ($expected -ne $actual) { throw 'QAI archive checksum mismatch.' }
  $package = Join-Path $work 'package'
  Expand-Archive -LiteralPath $zip -DestinationPath $package
  $options = @{ AgentName = $AgentName }
  if ($PlatformDirectory) { $options.PlatformDirectory = $PlatformDirectory }
  if ($Kind) { $options.Kind = $Kind; $options.ConfigPath = $ConfigPath }
  if ($ReleaseDirectory) { $options.PlatformArchive = Join-Path ([IO.Path]::GetFullPath($ReleaseDirectory)) ('AgentDock-' + $version + '-win-x64.zip') }
  & (Join-Path $package 'Setup-QAI.ps1') @options
} finally {
  $resolved = [IO.Path]::GetFullPath($work)
  if ($resolved.StartsWith($parent.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -and (Split-Path $resolved -Leaf) -match '^AgentDock-QAI-[a-f0-9-]{36}$') {
    Remove-Item -LiteralPath $resolved -Recurse -Force
  } else { throw 'Refused cleanup outside temporary installer directory.' }
}
