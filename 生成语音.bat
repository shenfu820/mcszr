@echo off
chcp 65001 >nul
rem ASCII-only wrapper (Chinese output comes from Python). Re-renders the scripted
rem voice lines -- run it after editing script\script.json or changing the voice.
setlocal EnableExtensions
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

call "%ROOT%\_tools\dh_findpy.cmd"
if not defined DH_FOUND_PY goto no_python

echo.
echo Available Chinese voices (first 12):
"%DH_FOUND_PY%" "%ROOT%\server\voice_gen.py" --list

echo.
echo Rendering ... existing complete files are skipped (use --force to redo all)
"%DH_FOUND_PY%" "%ROOT%\server\voice_gen.py" %*

echo.
echo Done. Audio is in voice\ , manifest in voice\manifest.json
timeout /t 6 /nobreak >nul
endlocal & exit /b 0

:no_python
echo [FAIL] no usable Python found. Run the one-click env setup script first.
echo.
pause
endlocal & exit /b 2
