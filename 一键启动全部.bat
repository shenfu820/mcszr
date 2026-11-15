@echo off
chcp 65001 >nul
setlocal
rem ============================================================
rem  Thin ASCII wrapper -> _tools\start_all.ps1 (PowerShell).
rem  All Chinese lives in the .ps1: a .bat saved as LF gets torn
rem  apart by cmd, which is exactly why this wrapper keeps ASCII.
rem
rem    --check      health report only
rem    --no-window  do not open browser windows
rem    --stop       stop everything
rem ============================================================
set "PS=%~dp0_tools\start_all.ps1"
if not exist "%PS%" (
  echo [FAIL] missing _tools\start_all.ps1
  pause
  exit /b 2
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS%" %*
set "RC=%errorlevel%"
if not "%RC%"=="0" pause
endlocal & exit /b %RC%
