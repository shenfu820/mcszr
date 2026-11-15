@echo off
chcp 65001 >nul
rem ASCII-only compatibility entry. Same as the setup script next to this file:
rem both just call _tools\dh_setup.py (all Chinese output comes from Python).
rem Kept because older docs and shortcuts point at this name.
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
echo [FAIL] no usable Python 3.9+ found. Run the setup script named "one-click env setup" in this folder.
echo.
pause
endlocal & exit /b 2
