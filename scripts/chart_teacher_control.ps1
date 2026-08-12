[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Start", "Stop", "Status", "Restart")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$RunnerPath = Join-Path $ProjectDir "chart_teacher_runner.py"
$ServerPath = Join-Path $ProjectDir "chart_teacher_server.py"
$PythonWPath = Join-Path $ProjectDir ".venv\Scripts\pythonw.exe"
$ControlFile = Join-Path $ProjectDir "runtime\control.json"
$HealthUrl = "http://127.0.0.1:8787/health"
$ShutdownUrl = "http://127.0.0.1:8787/internal/shutdown"
$TaskName = "ChartTeacherBot"

function Get-Health {
    try {
        $result = Invoke-RestMethod -Uri $HealthUrl -Method Get -TimeoutSec 2
        if ($result.ok -eq $true -and $result.service -eq "chart-teacher-bot") {
            return $result
        }
    }
    catch {
        return $null
    }
    return $null
}

function Get-ControlInfo {
    if (-not (Test-Path -LiteralPath $ControlFile)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $ControlFile -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Get-OwnedProcess([object]$ControlInfo) {
    if ($null -eq $ControlInfo -or $null -eq $ControlInfo.pid) {
        return $null
    }
    try {
        $process = Get-CimInstance Win32_Process -Filter ("ProcessId = " + [int]$ControlInfo.pid)
        if ($null -eq $process) {
            return $null
        }
        if ($process.Name -notin @("python.exe", "pythonw.exe")) {
            return $null
        }
        if ([string]::IsNullOrWhiteSpace($process.CommandLine)) {
            return $null
        }
        if ($process.CommandLine.IndexOf($ServerPath, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
            return $null
        }
        return $process
    }
    catch {
        return $null
    }
}

function Get-ChartTeacherTask {
    return Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

function Wait-ForHealth([bool]$ShouldBeRunning, [int]$Attempts = 30) {
    for ($index = 0; $index -lt $Attempts; $index++) {
        $isRunning = $null -ne (Get-Health)
        if ($isRunning -eq $ShouldBeRunning) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Start-ChartTeacher {
    $health = Get-Health
    if ($null -ne $health) {
        $control = Get-ControlInfo
        $pidText = if ($null -ne $control) { " (PID $($control.pid))" } else { "" }
        Write-Host "RUNNING$pidText - Chart Teacher is already running." -ForegroundColor Green
        return 0
    }

    $listener = Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue
    if ($null -ne $listener) {
        Write-Host "ERROR - Port 8787 is being used by another process." -ForegroundColor Red
        return 1
    }

    if (-not (Test-Path -LiteralPath $PythonWPath)) {
        Write-Host "ERROR - .venv\Scripts\pythonw.exe was not found." -ForegroundColor Red
        Write-Host "Run setup_windows.bat first."
        return 1
    }

    if (-not (Test-Path -LiteralPath $RunnerPath)) {
        Write-Host "ERROR - chart_teacher_runner.py was not found." -ForegroundColor Red
        return 1
    }

    $staleControl = Get-ControlInfo
    if ($null -ne $staleControl -and $null -eq (Get-OwnedProcess $staleControl)) {
        Remove-Item -LiteralPath $ControlFile -Force -ErrorAction SilentlyContinue
    }

    $task = Get-ChartTeacherTask
    if ($null -ne $task) {
        if ($task.State -eq "Disabled") {
            Write-Host "ERROR - The $TaskName scheduled task is disabled." -ForegroundColor Red
            return 1
        }
        for ($index = 0; $index -lt 10 -and $task.State -eq "Running"; $index++) {
            Start-Sleep -Milliseconds 500
            $task = Get-ChartTeacherTask
        }
        Start-ScheduledTask -TaskName $TaskName
    }
    else {
        Start-Process -FilePath $PythonWPath `
            -ArgumentList @("`"$RunnerPath`"") `
            -WorkingDirectory $ProjectDir `
            -WindowStyle Hidden | Out-Null
    }

    if (Wait-ForHealth $true) {
        $control = Get-ControlInfo
        $pidText = if ($null -ne $control) { " (PID $($control.pid))" } else { "" }
        Write-Host "RUNNING$pidText - Chart Teacher started successfully." -ForegroundColor Green
        return 0
    }

    Write-Host "ERROR - Chart Teacher did not become healthy." -ForegroundColor Red
    Write-Host "Check logs\error.log and logs\chart_teacher.log."
    return 1
}

function Stop-ChartTeacher {
    $control = Get-ControlInfo
    $ownedProcess = Get-OwnedProcess $control
    $health = Get-Health

    if ($null -ne $health -and $null -ne $control -and $null -ne $ownedProcess) {
        try {
            $headers = @{ "X-Chart-Teacher-Control" = [string]$control.control_token }
            Invoke-RestMethod -Uri $ShutdownUrl -Method Post -Headers $headers -TimeoutSec 3 | Out-Null
            if (Wait-ForHealth $false 20) {
                Write-Host "STOPPED - Chart Teacher stopped safely." -ForegroundColor Yellow
                return 0
            }
        }
        catch {
            # Fall through to the ownership-checked emergency stop.
        }
    }

    $control = Get-ControlInfo
    $ownedProcess = Get-OwnedProcess $control
    if ($null -ne $ownedProcess) {
        $task = Get-ChartTeacherTask
        if ($null -ne $task -and $task.State -eq "Running") {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        }
        Stop-Process -Id ([int]$ownedProcess.ProcessId) -Force
        if (Wait-ForHealth $false 20) {
            Remove-Item -LiteralPath $ControlFile -Force -ErrorAction SilentlyContinue
            Write-Host "STOPPED - The unresponsive Chart Teacher process was stopped." -ForegroundColor Yellow
            return 0
        }
        Write-Host "ERROR - Chart Teacher did not stop." -ForegroundColor Red
        return 1
    }

    if ($null -ne $health) {
        Write-Host "ERROR - A Chart Teacher endpoint answered, but process ownership could not be verified." -ForegroundColor Red
        Write-Host "No Python process was terminated."
        return 1
    }

    Write-Host "STOPPED - Chart Teacher is not running." -ForegroundColor Yellow
    return 0
}

function Show-ChartTeacherStatus {
    $health = Get-Health
    if ($null -ne $health) {
        $control = Get-ControlInfo
        $ownedProcess = Get-OwnedProcess $control
        if ($null -ne $ownedProcess) {
            Write-Host "RUNNING - Chart Teacher is healthy (PID $($ownedProcess.ProcessId))." -ForegroundColor Green
        }
        else {
            Write-Host "RUNNING - Chart Teacher health check passed." -ForegroundColor Green
        }
        return 0
    }

    Write-Host "STOPPED - Chart Teacher is not responding at $HealthUrl." -ForegroundColor Yellow
    return 1
}

$exitCode = switch ($Action) {
    "Start" { Start-ChartTeacher }
    "Stop" { Stop-ChartTeacher }
    "Status" { Show-ChartTeacherStatus }
    "Restart" {
        $stopCode = Stop-ChartTeacher
        if ($stopCode -ne 0) { $stopCode } else { Start-ChartTeacher }
    }
}

exit $exitCode
