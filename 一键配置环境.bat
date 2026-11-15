@echo off
chcp 65001 >nul
rem ASCII-only wrapper: all Chinese output comes from Python. Keep this file ASCII --
rem a .bat containing Chinese gets torn apart by cmd on some machines
rem (symptom: 'xxx' is not recognized, where xxx is a fragment of a Chinese sentence).
setlocal EnableExtensions
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

call "%ROOT%\_tools\dh_findpy.cmd"
if not defined DH_FOUND_PY goto no_python

"%DH_FOUND_PY%" "%ROOT%\_tools\dh_setup.py" %*
set "RC=%errorlevel%"
echo.
pause
endlocal & exit /b %RC%

:no_python
echo [FAIL] no usable Python 3.9+ found.
echo        1. install Python 3.10: https://www.python.org/downloads/windows/
echo           tick "Add python.exe to PATH" during setup
echo        2. rerun this script
echo        (or install uv from https://astral.sh/uv - then this script downloads a Python by itself)
echo.
pause
endlocal & exit /b 2
