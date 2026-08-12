@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\chart_teacher_control.ps1" -Action Stop
set "RESULT=%ERRORLEVEL%"
if /I not "%~1"=="--no-pause" pause
exit /b %RESULT%
