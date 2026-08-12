# Backward-compatible entry point. The maintained installer lives in scripts/.
$Installer = Join-Path $PSScriptRoot "scripts\install_autostart.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Installer
exit $LASTEXITCODE
