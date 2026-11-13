@echo off
chcp 65001 >nul
setlocal

rem ============================================================
rem  数字人 · 一键启动（服务 + 形象窗口 + 控制台）
rem ============================================================

set ROOT=%~dp0
set PY=%ROOT%.venv\Scripts\python.exe
set PORT=8772

if not exist "%PY%" (
  echo [错误] 找不到虚拟环境：%PY%
  echo        先跑一次：python -m venv .venv ^&^& .venv\Scripts\pip install edge-tts
  pause
  exit /b 1
)

rem 端口占用检查：已经在跑就不再起第二个
netstat -ano | findstr ":%PORT% " | findstr LISTENING >nul
if %errorlevel%==0 (
  echo [提示] 端口 %PORT% 已在监听，跳过启动服务，直接开窗口。
  goto OPEN
)

echo [1/3] 启动数字人服务...
start "数字人服务" /min cmd /c ""%PY%" "%ROOT%server\dh_server.py" --port %PORT%"
timeout /t 3 /nobreak >nul

:OPEN
echo [2/3] 打开形象窗口...
set EDGE=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe
set CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe

if exist "%CHROME%" (
  start "" "%CHROME%" --app="http://127.0.0.1:%PORT%/avatar/" ^
        --window-size=960,940 --window-position=40,40 ^
        --user-data-dir="%TEMP%\dh-avatar-profile" --no-first-run
) else if exist "%EDGE%" (
  start "" "%EDGE%" --app="http://127.0.0.1:%PORT%/avatar/" ^
        --window-size=960,940 --window-position=40,40 ^
        --user-data-dir="%TEMP%\dh-avatar-profile" --no-first-run
) else (
  echo [警告] 没找到 Chrome/Edge，用默认浏览器打开
  start "" "http://127.0.0.1:%PORT%/avatar/"
)

echo [3/3] 打开控制台...
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:%PORT%/console/"

echo.
echo ============================================================
echo   形象窗口  http://127.0.0.1:%PORT%/avatar/
echo   控制台    http://127.0.0.1:%PORT%/console/
echo.
echo   现场操作：控制台左侧点 cue 播放，空格键推进下一条。
echo   首次点一下形象窗口任意位置，解锁浏览器语音播放。
echo ============================================================
echo.
echo 本窗口可以关掉，服务在另一个最小化窗口里继续跑。
timeout /t 8 /nobreak >nul
endlocal
