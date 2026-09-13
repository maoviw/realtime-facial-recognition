[CmdletBinding()]
param(
    [string]$Url,
    [string]$Username = 'admin',
    [ValidateRange(4, 60)]
    [int]$ClipSeconds = 60,
    [ValidateRange(1, 86400)]
    [int]$RunSeconds = 86400,
    [ValidateRange(1, 300)]
    [int]$RestartDelaySeconds = 15,
    [ValidateRange(0, 3)]
    [int]$Retries = 1,
    [ValidateRange(33, 1048576)]
    [int]$QuotaMiB = 24576,
    [ValidateRange(0, 1048576)]
    [int]$ReserveMiB = 4096,
    [string]$TaskName = 'CognitiveFaceLive-Camera',
    [string]$StateRoot = (Join-Path $env:LOCALAPPDATA 'CognitiveFaceLive'),
    [string]$OutputRoot = (Join-Path $PSScriptRoot '../backend/data/phase0-pilots'),
    [string]$PythonPath = (Join-Path $PSScriptRoot '../backend/.venv/Scripts/python.exe'),
    [SecureString]$CameraPassword,
    [switch]$PrepareOnly,
    [switch]$StartNow
)

$ErrorActionPreference = 'Stop'

function Protect-StateDirectory([string]$Path) {
    $userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $systemSid = [Security.Principal.SecurityIdentifier]::new(
        [Security.Principal.WellKnownSidType]::LocalSystemSid,
        $null
    )
    $rights = [Security.AccessControl.FileSystemRights]::FullControl
    $inheritance = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    $propagation = [Security.AccessControl.PropagationFlags]::None
    $allow = [Security.AccessControl.AccessControlType]::Allow
    $acl = Get-Acl -LiteralPath $Path
    $acl.SetAccessRuleProtection($true, $false)
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        $userSid, $rights, $inheritance, $propagation, $allow
    ))
    $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        $systemSid, $rights, $inheritance, $propagation, $allow
    ))
    $directory = Get-Item -LiteralPath $Path
    [IO.FileSystemAclExtensions]::SetAccessControl($directory, $acl)
}

if (-not $IsWindows) { throw 'The camera task requires Windows.' }
if (-not $Url) { throw 'Url is required.' }
$cameraUri = [Uri]$Url
if ($cameraUri.Scheme -notin @('http', 'https') -or -not $cameraUri.Host -or
    $cameraUri.UserInfo -or $cameraUri.Query -or $cameraUri.Fragment) {
    throw 'Use an HTTP(S) camera URL without credentials, query or fragment.'
}
if ($TaskName -notmatch '^[A-Za-z0-9_.-]{1,80}$') { throw 'TaskName contains unsupported characters.' }

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = (Resolve-Path $PythonPath).Path
$output = [IO.Path]::GetFullPath($OutputRoot)
$state = [IO.Path]::GetFullPath($StateRoot)
$runner = (Resolve-Path (Join-Path $PSScriptRoot 'run-camera-task.ps1')).Path
$null = New-Item -ItemType Directory -Path $state -Force
Protect-StateDirectory $state
$null = New-Item -ItemType Directory -Path $output -Force
$secretPath = Join-Path $state 'camera-password.dpapi'
$configPath = Join-Path $state 'camera-task.json'
$stopPath = Join-Path $state 'camera.stop'
if (Test-Path -LiteralPath $stopPath) {
    throw "Remove the existing stop request before installation: $stopPath"
}
if (-not $CameraPassword) {
    $CameraPassword = Read-Host 'Camera password (protected for this Windows user)' -AsSecureString
}
if ($CameraPassword.Length -lt 1) { throw 'Camera password must not be empty.' }

$CameraPassword | ConvertFrom-SecureString | Set-Content -LiteralPath $secretPath -Encoding utf8NoBOM
$config = [ordered]@{
    Version = 2
    ProjectRoot = $projectRoot
    PythonPath = $python
    Url = $cameraUri.AbsoluteUri
    Username = $Username
    ClipSeconds = $ClipSeconds
    RunSeconds = $RunSeconds
    RestartDelaySeconds = $RestartDelaySeconds
    Retries = $Retries
    QuotaMiB = $QuotaMiB
    ReserveMiB = $ReserveMiB
    OutputRoot = $output
    SecretPath = $secretPath
    StopFile = $stopPath
    StatusPath = (Join-Path $state 'camera-task-status.json')
    LogPath = (Join-Path $state 'camera-task.log')
}
$config | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding utf8NoBOM

if ($PrepareOnly) {
    [pscustomobject]@{ Prepared = $true; ConfigPath = $configPath; SecretPath = $secretPath } | ConvertTo-Json
    return
}

$powershell = (Get-Process -Id $PID).Path
$actionArguments = "-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File `"$runner`" -ConfigPath `"$configPath`""
$action = New-ScheduledTaskAction -Execute $powershell -Argument $actionArguments -WorkingDirectory $projectRoot
$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew
$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description 'Local bounded IP-camera capture for Cognitive Face Live.'
Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
if ($StartNow) { Start-ScheduledTask -TaskName $TaskName }
[pscustomobject]@{
    Installed = $true
    Started = [bool]$StartNow
    TaskName = $TaskName
    ConfigPath = $configPath
    StopFile = $stopPath
} | ConvertTo-Json