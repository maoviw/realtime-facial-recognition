[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$ConfigPath,
    [switch]$ValidateOnly
)

$ErrorActionPreference = 'Stop'

function Write-State([string]$State, [hashtable]$Details = @{}) {
    $status = [ordered]@{
        State = $State
        UpdatedAt = [DateTimeOffset]::UtcNow.ToString('o')
    }
    foreach ($entry in $Details.GetEnumerator()) { $status[$entry.Key] = $entry.Value }
    $temporary = "$($config.StatusPath).tmp"
    $status | ConvertTo-Json | Set-Content -LiteralPath $temporary -Encoding utf8NoBOM
    Move-Item -LiteralPath $temporary -Destination $config.StatusPath -Force
}

function Write-SessionLog([int]$Session, [int]$ExitCode, [string]$StandardOutput, [string]$StandardError) {
    if ((Test-Path -LiteralPath $config.LogPath) -and
        (Get-Item -LiteralPath $config.LogPath).Length -ge 10MB) {
        Move-Item -LiteralPath $config.LogPath -Destination "$($config.LogPath).previous" -Force
    }
    $header = "[$([DateTimeOffset]::UtcNow.ToString('o'))] session=$Session exit_code=$ExitCode"
    @($header, $StandardOutput.TrimEnd(), $StandardError.TrimEnd()) |
        Where-Object { $_ } |
        Add-Content -LiteralPath $config.LogPath -Encoding utf8NoBOM
}

try {
    $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    if ($config.Version -ne 2 -or -not (Test-Path -LiteralPath $config.PythonPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $config.SecretPath -PathType Leaf) -or
        -not (Test-Path -LiteralPath $config.ProjectRoot -PathType Container) -or
        $config.RunSeconds -lt 1 -or $config.RunSeconds -gt 86400 -or
        $config.RestartDelaySeconds -lt 1 -or $config.RestartDelaySeconds -gt 300 -or
        -not $config.StatusPath -or -not $config.LogPath) {
        throw 'Camera task configuration is invalid or incomplete.'
    }
    $protectedPassword = (Get-Content -LiteralPath $config.SecretPath -Raw).Trim()
    $securePassword = $protectedPassword | ConvertTo-SecureString
    $protectedPassword = $null
    if ($securePassword.Length -lt 1) { throw 'Protected camera password is empty.' }
    if ($ValidateOnly) {
        Write-State 'validated' @{ ConfigPath = [IO.Path]::GetFullPath($ConfigPath) }
        return
    }
    if (Test-Path -LiteralPath $config.StopFile) {
        Write-State 'stopped' @{ Reason = 'stop_file'; ExitCode = 0 }
        return
    }

    $session = 0
    $consecutiveFailures = 0
    while (-not (Test-Path -LiteralPath $config.StopFile)) {
        $session++
        $arguments = @(
            '-m', 'scripts.pilot_supervisor', '--url', [string]$config.Url,
            '--username', [string]$config.Username, '--password-stdin',
            '--seconds', [string]$config.ClipSeconds, '--run-seconds', [string]$config.RunSeconds,
            '--retries', [string]$config.Retries, '--metrics',
            '--quota-mib', [string]$config.QuotaMiB, '--reserve-mib', [string]$config.ReserveMiB,
            '--output-root', [string]$config.OutputRoot, '--stop-file', [string]$config.StopFile
        )
        $startInfo = [Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = $config.PythonPath
        $startInfo.WorkingDirectory = $config.ProjectRoot
        $startInfo.UseShellExecute = $false
        $startInfo.RedirectStandardInput = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        foreach ($argument in $arguments) { $null = $startInfo.ArgumentList.Add($argument) }
        $process = [Diagnostics.Process]::new()
        $process.StartInfo = $startInfo
        if (-not $process.Start()) { throw 'Camera supervisor process did not start.' }
        $standardOutput = $process.StandardOutput.ReadToEndAsync()
        $standardError = $process.StandardError.ReadToEndAsync()
        Write-State 'running' @{
            ProcessId = $process.Id
            Session = $session
            StartedAt = [DateTimeOffset]::UtcNow.ToString('o')
        }

        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
        try {
            $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
            $process.StandardInput.WriteLine($plainPassword)
            $process.StandardInput.Close()
        } finally {
            $plainPassword = $null
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        }
        $process.WaitForExit()
        $exitCode = $process.ExitCode
        Write-SessionLog $session $exitCode $standardOutput.Result $standardError.Result
        if (Test-Path -LiteralPath $config.StopFile) { break }
        if ($exitCode -ne 0) {
            $consecutiveFailures++
            Write-State 'recovering' @{
                ExitCode = $exitCode
                Session = $session
                ConsecutiveFailures = $consecutiveFailures
                RestartDelaySeconds = $config.RestartDelaySeconds
            }
        } else {
            $consecutiveFailures = 0
            Write-State 'waiting' @{ Session = $session; RestartDelaySeconds = $config.RestartDelaySeconds }
        }
        Start-Sleep -Seconds $config.RestartDelaySeconds
    }
    Write-State 'stopped' @{ Reason = 'stop_file'; ExitCode = 0; Sessions = $session }
    exit 0
} catch {
    if ($config -and $config.StatusPath) {
        Write-State 'failed' @{ ErrorType = $_.Exception.GetType().Name }
    }
    Write-Error "Camera task failed ($($_.Exception.GetType().Name))."
    exit 1
}