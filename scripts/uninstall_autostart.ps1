[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$TaskName = "ChartTeacherBot"
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

if ($null -eq $task) {
    Write-Host "NOT INSTALLED - $TaskName does not exist." -ForegroundColor Yellow
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "REMOVED - Only the $TaskName scheduled task was deleted." -ForegroundColor Green
Write-Host "The Chart Teacher server was not stopped."
exit 0
