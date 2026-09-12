[CmdletBinding()]
param(
    [string]$TaskName = 'CognitiveFaceLive-Camera',
    [string]$StateRoot = (Join-Path $env:LOCALAPPDATA 'CognitiveFaceLive')
)

$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'The camera task requires Windows.' }
if ($TaskName -notmatch '^[A-Za-z0-9_.-]{1,80}$') { throw 'TaskName contains unsupported characters.' }

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$statusPath = Join-Path ([IO.Path]::GetFullPath($StateRoot)) 'camera-task-status.json'
$runtime = if (Test-Path -LiteralPath $statusPath -PathType Leaf) {
    Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
} else {
    $null
}
[pscustomobject]@{
    Installed = $null -ne $task
    TaskName = $TaskName
    TaskState = if ($task) { [string]$task.State } else { 'NotInstalled' }
    Runtime = $runtime
} | ConvertTo-Json -Depth 4