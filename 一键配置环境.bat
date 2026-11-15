@echo off
chcp 65001 >nul
rem EOL: kept CRLF by .gitattributes (cmd needs CRLF; if broken run: python _tools\bat_lint.py --fix)
setlocal EnableExtensions
title dh env bootstrap

rem ============================================================
rem  Digital human / one-command environment setup
rem  Thin ASCII wrapper -> _tools\dh_bootstrap.py
rem  (all Chinese output comes from Python, so this file parses
rem   under any code page and can never be torn apart by cmd)
rem
rem  No argument      -> install all three envs (main + asr + gpu)
rem  --only main      -> just the main .venv
rem  --only asr       -> just the ASR env (funasr)
rem  --only gpu       -> just the GPU env (torch + ultralytics)
rem  --check          -> print the plan, change nothing
rem  --force          -> rebuild even if an env already works
rem  --offline        -> install from wheels\ only
rem  --warm-asr       -> also download ASR models now (~2 GB)
rem ============================================================

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

rem --check means "plan only, change nothing" - so skip the hygiene steps below.
rem (remember whether the caller passed any argument, because "shift" eats them)
set "ARGS_GIVEN=0"
if not "%~1"=="" set "ARGS_GIVEN=1"
set "CHECKMODE=0"
:scan_args
if "%~1"=="" goto scanned
if /i "%~1"=="--check" set "CHECKMODE=1"
shift
goto scan_args
:scanned

call "%ROOT%\_tools\dh_findpy.cmd"
if not defined DH_FOUND_PY goto no_python

if "%ARGS_GIVEN%"=="0" goto default_all
"%DH_FOUND_PY%" "%ROOT%\_tools\dh_bootstrap.py" --root "%ROOT%" --no-report %*
set "RC=%errorlevel%"
goto report

:default_all
echo [i] no argument given: installing main + asr + gpu
"%DH_FOUND_PY%" "%ROOT%\_tools\dh_bootstrap.py" --root "%ROOT%" --no-report --all
set "RC=%errorlevel%"

:report
echo.
if "%CHECKMODE%"=="1" goto skip_hygiene
echo ==== link + line-ending hygiene ====
"%DH_FOUND_PY%" "%ROOT%\_tools\dh_env.py" --fix --root "%ROOT%" >nul 2>nul
"%DH_FOUND_PY%" "%ROOT%\_tools\bat_lint.py" --fix --root "%ROOT%" >nul 2>nul

:skip_hygiene

echo.
echo ==== health report ====
"%DH_FOUND_PY%" "%ROOT%\_tools\dh_env.py" --check --root "%ROOT%"
set "CHK=%errorlevel%"

echo.
if "%RC%"=="0" goto env_ok
echo [FAIL] some environments are not installed. Fix the FAIL lines above, then rerun this script.
echo [i] also worth a look: python _tools\repos.py --report   (dependency projects and their repos)
echo.
pause
endlocal & exit /b %RC%

:env_ok
echo [OK] environments ready. Next: 一键启动全部.bat
echo [i] also worth a look: python _tools\repos.py --report   (dependency projects and their repos)
echo.
pause
endlocal & exit /b %CHK%

:no_python
echo [FAIL] no usable Python 3.9+ found on this machine.
echo.
echo   1. install Python 3.10: https://www.python.org/downloads/windows/
echo      tick "Add python.exe to PATH" during setup
echo   2. rerun this script
echo.
echo   (or install uv from https://astral.sh/uv first - this script will
echo    then download a private Python by itself)
echo.
pause
endlocal & exit /b 2
