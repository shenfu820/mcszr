@echo off
chcp 65001 >nul
rem ASCII-only wrapper (Chinese output comes from Python). Stops the ports listed in
rem config\paths.ini (PORT / ASR_PORT) -- nothing hard-coded here.
setlocal EnableExtensions
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

call "%ROOT%\_tools\dh_findpy.cmd"
if not defined DH_FOUND_PY goto fallback
"%DH_FOUND_PY%" "%ROOT%\_tools\dh_spawn.py" stop --config
goto end

:fallback
echo [WARN] no Python found, killing default ports 8772 / 8773
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8772 " ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8773 " ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1

:end
echo.
echo Done. External panels 8770 / 8999 / 8790 are stopped by the four-screen package stop script.
timeout /t 3 /nobreak >nul
endlocal & exit /b 0
