@echo off
chcp 65001 >nul
rem ASCII-only wrapper: Chinese output comes from Python (a .bat containing Chinese gets
rem torn apart by cmd on some machines -- 'xxx' is not recognized, xxx = CN fragment).
rem
rem Starts only the digital-human body: ASR 8773 -> main service 8772 -> windows.
rem   --check       full health report (no service started)
rem   --no-window   no browser windows
rem   --stop        stop body ports
setlocal EnableExtensions
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

call "%ROOT%\_tools\dh_findpy.cmd"
if not defined DH_FOUND_PY goto no_python

if not "%~1"=="" goto args_given
"%DH_FOUND_PY%" "%ROOT%\_tools\dh_all.py" --fix-bats --body-only
set "RC=%errorlevel%"
endlocal & exit /b %RC%

:args_given
if /i not "%~1"=="--check" goto pass_args
if not "%~2"=="" goto pass_args
echo ==== health report ====
"%DH_FOUND_PY%" "%ROOT%\_tools\dh_env.py" --check --next-hint
set "RC=%errorlevel%"
endlocal & exit /b %RC%

:pass_args
"%DH_FOUND_PY%" "%ROOT%\_tools\dh_all.py" --fix-bats --body-only %*
set "RC=%errorlevel%"
endlocal & exit /b %RC%

:no_python
echo [FAIL] no usable Python 3.9+ found. Run the one-click env setup script in this folder first.
echo.
pause
endlocal & exit /b 2
