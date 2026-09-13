[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$TaskName = 'CognitiveFaceLive-Camera',
    [string]$StateRoot = (Join-Path $env:LOCALAPPDATA 'CognitiveFaceLive'),
    [switch]$RemoveState
)

$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'The camera task requires Windows.' }
if ($TaskName -notmatch '^[A-Za-z0-9_.-]{1,80}$') { throw 'TaskName contains unsupported characters.' }
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task -and $task.State -eq 'Running') {
    throw 'Request a graceful stop and wait for the task to stop before uninstalling.'
}
if ($task -and $PSCmdlet.ShouldProcess($TaskName, 'Unregister scheduled task')) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
$state = [IO.Path]::GetFullPath($StateRoot)
if ($RemoveState -and (Test-Path -LiteralPath $state) -and
    $PSCmdlet.ShouldProcess($state, 'Remove encrypted secret, configuration and status')) {
    Remove-Item -LiteralPath $state -Recurse -Force
}
[pscustomobject]@{
    Uninstalled = -not [bool](Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)
    StatePreserved = -not $RemoveState
    StateRoot = $state
} | ConvertTo-Json