[CmdletBinding()]
param(
    [string]$StateRoot = (Join-Path $env:LOCALAPPDATA 'CognitiveFaceLive')
)

$ErrorActionPreference = 'Stop'
$state = [IO.Path]::GetFullPath($StateRoot)
$configPath = Join-Path $state 'camera-task.json'
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Camera task configuration not found: $configPath"
}
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$stopPath = [IO.Path]::GetFullPath([string]$config.StopFile)
if ([IO.Path]::GetDirectoryName($stopPath) -ne $state) {
    throw 'Camera task stop path is outside the state directory.'
}
$null = New-Item -ItemType File -Path $stopPath -Force
[pscustomobject]@{ StopRequested = $true; StopFile = $stopPath } | ConvertTo-Json