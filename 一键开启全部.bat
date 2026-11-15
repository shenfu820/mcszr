@echo off
chcp 65001 >nul
rem ASCII-only wrapper: Chinese output comes from Python (a .bat with Chinese gets
rem torn apart by cmd on some machines -- 'xxx' is not recognized, xxx = CN fragment).
rem Calls _tools\dh_all.py directly (same thing the old launcher did via PowerShell).
rem Args are passed through: --check / --no-window / --stop / --only 8772,8790
setlocal EnableExtensions
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

call "%ROOT%\_tools\dh_findpy.cmd"
if not defined DH_FOUND_PY goto no_python

rem find every project first (silent; missing ones are reported by the health lines later)
"%DH_FOUND_PY%" "%ROOT%\_tools\discover.py" --write --root "%ROOT%" >nul 2>nul

"%DH_FOUND_PY%" "%ROOT%\_tools\dh_all.py" --fix-bats %*
set "RC=%errorlevel%"
endlocal & exit /b %RC%

:no_python
echo [FAIL] no usable Python found. Run the one-click env setup script first.
pause
endlocal & exit /b 2
