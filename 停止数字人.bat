@echo off
chcp 65001 >nul
rem ── 行尾：本文件由 .gitattributes 保证 CRLF（cmd 只认 CRLF；若被存成 LF，跑 python _tools\bat_lint.py --fix）
setlocal EnableExtensions

rem ============================================================
rem  数字人 · 停止服务（端口按 config\paths.ini，不写死）
rem ============================================================
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

echo ============================================================
echo   数字人 · 停止服务
echo ============================================================

call "%ROOT%\_tools\dh_findpy.cmd"
if not defined DH_FOUND_PY goto fallback
"%DH_FOUND_PY%" "%ROOT%\_tools\dh_spawn.py" stop --config
goto end

:fallback
echo [警告] 没找到 Python，按默认端口 8772 / 8773 强杀
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8772 " ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8773 " ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1

:end
echo.
echo 完成。外部四屏那几块（8770 / 8999 / 8790）请用 板材数字人_四屏舞台 里的 停止四屏.bat
timeout /t 3 /nobreak >nul
endlocal & exit /b 0
