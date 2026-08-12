[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonWPath = Join-Path $ProjectDir ".venv\Scripts\pythonw.exe"
$RunnerPath = Join-Path $ProjectDir "chart_teacher_runner.py"
$TaskName = "ChartTeacherBot"

if (-not (Test-Path -LiteralPath $PythonWPath)) {
    throw ".venv\Scripts\pythonw.exe was not found. Run setup_windows.bat first."
}
if (-not (Test-Path -LiteralPath $RunnerPath)) {
    throw "chart_teacher_runner.py was not found."
}

$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Action = New-ScheduledTaskAction `
    -Execute $PythonWPath `
    -Argument "`"$RunnerPath`"" `
    -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $CurrentUser
$Principal = New-ScheduledTaskPrincipal `
    -UserId $CurrentUser `
    -LogonType Interactive `
    -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Principal $Principal `
        -Settings $Settings `
        -Description "Runs Chart Teacher Bot in the background and restarts it after failures." `
        -Force | Out-Null
}
catch [System.UnauthorizedAccessException] {
    Write-Host "ERROR - Windows denied task registration for the current user." -ForegroundColor Red
    Write-Host "This normally works without administrator rights. If company policy blocks it, ask IT to allow a per-user scheduled task named $TaskName."
    exit 1
}

$task = Get-ScheduledTask -TaskName $TaskName
Write-Host "INSTALLED - $TaskName will start when $CurrentUser logs in." -ForegroundColor Green
Write-Host "Failure restart: every 1 minute, up to 999 attempts."
Write-Host "Run level: $($task.Principal.RunLevel) (administrator rights are not requested)."
exit 0
