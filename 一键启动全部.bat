@echo off
chcp 65001 >nul
rem ASCII-only wrapper -> _tools\start_all.ps1 -> _tools\dh_all.py
rem (PowerShell/Python do not care about code pages or line endings; this file does)
rem   --check      health report only
rem   --no-window  no browser windows
rem   --stop       stop everything
setlocal EnableExtensions
set "PS=%~dp0_tools\start_all.ps1"
if not exist "%PS%" goto missing
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS%" %*
set "RC=%errorlevel%"
if not "%RC%"=="0" pause
endlocal & exit /b %RC%

:missing
echo [FAIL] missing _tools\start_all.ps1 - repo is incomplete (git pull?)
pause
endlocal & exit /b 2
