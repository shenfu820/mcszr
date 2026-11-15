@echo off
rem ============================================================
rem  Find a usable Python (shared by launch / deploy / voice bats)
rem  Caller must set "ROOT=<project root>" first.
rem  Returns: DH_FOUND_PY (full path) DH_FOUND_SRC (where it came from)
rem  ASCII-only on purpose: this file must parse under any code page.
rem ============================================================
setlocal EnableExtensions
set "DH_FOUND_PY="
set "DH_FOUND_SRC="

rem 1) explicit override
if defined DH_PY call :take "%DH_PY%" "DH_PY"
if defined DH_FOUND_PY goto done

rem 2) project venv (most reliable)
if exist "%ROOT%\.venv\Scripts\python.exe" call :take "%ROOT%\.venv\Scripts\python.exe" "project .venv"
if defined DH_FOUND_PY goto done
if exist "%ROOT%\.venv-asr\Scripts\python.exe" call :take "%ROOT%\.venv-asr\Scripts\python.exe" "project .venv-asr"
if defined DH_FOUND_PY goto done

rem 3) py launcher (syntax is "py -3.10", not "py 3.10")
for %%V in (-3.10 -3.11 -3.12 -3.9 -3.13 -3) do if not defined DH_FOUND_PY call :take_ver %%V
if defined DH_FOUND_PY goto done

rem 4) python on PATH (the 0-byte WindowsApps stub is rejected by :take)
for /f "usebackq delims=" %%P in (`where python 2^>nul`) do if not defined DH_FOUND_PY call :take "%%P" "PATH"
if defined DH_FOUND_PY goto done
for /f "usebackq delims=" %%P in (`where python3 2^>nul`) do if not defined DH_FOUND_PY call :take "%%P" "PATH"
if defined DH_FOUND_PY goto done

rem 5) common install locations
for %%P in (
  "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  "%ProgramFiles%\Python310\python.exe"
  "%ProgramFiles%\Python311\python.exe"
  "C:\Python310\python.exe"
  "C:\Python311\python.exe"
  "E:\python\python.exe"
  "D:\python\python.exe"
) do if not defined DH_FOUND_PY call :take %%P "common install dir"

:done
endlocal & set "DH_FOUND_PY=%DH_FOUND_PY%" & set "DH_FOUND_SRC=%DH_FOUND_SRC%"
exit /b 0

:take
if not exist "%~1" exit /b 1
"%~1" -c "import sys" >nul 2>&1
if errorlevel 1 exit /b 1
set "DH_FOUND_PY=%~1"
set "DH_FOUND_SRC=%~2"
exit /b 0

:take_ver
py %~1 -c "import sys" >nul 2>&1
if errorlevel 1 exit /b 1
for /f "usebackq delims=" %%E in (`py %~1 -c "import sys;print(sys.executable)"`) do set "DH_FOUND_PY=%%E"
if not defined DH_FOUND_PY exit /b 1
set "DH_FOUND_SRC=py %~1"
exit /b 0
