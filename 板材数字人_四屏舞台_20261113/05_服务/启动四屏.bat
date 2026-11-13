@echo off
chcp 65001 >nul
setlocal
title 板材数字人 - 四屏舞台

set "PKG=%~dp0.."
set "PROJ=C:\Users\Administrator\Desktop\项目与竞赛\aa比赛项目"
set "PORT=8790"
set "PY=E:\conda3\envs\yoloces\python.exe"
if not exist "%PY%" set "PY=python"

echo === 1/5 大屏 8770 ===
netstat -ano | findstr "LISTENING" | findstr ":8770" >nul
if errorlevel 1 (
  echo   启动 screen_service.py
  start "mcjc-screen-8770" /min /d "%PROJ%\new mcjc" "%PY%" screen_service.py --port 8770
) else (
  echo   已在运行
)

echo === 2/5 对话中枢 8999 ===
netstat -ano | findstr "LISTENING" | findstr ":8999" >nul
if errorlevel 1 (
  echo   启动 agent_service.py
  start "angent-8999" /min /d "%PROJ%\angent" "%PY%" agent_service.py --port 8999
) else (
  echo   已在运行
)

echo === 3/5 桌面端 run.py ===
tasklist /fi "imagename eq python.exe" | findstr /i "python" >nul
if errorlevel 1 (
  echo   启动 run.py
  start "mcjc-desktop" /min /d "%PROJ%\new mcjc" "%PY%" run.py
) else (
  echo   已有 python 进程，若桌面端没开请手动运行 run.py
)

echo === 4/5 舞台服务 8790 ===
netstat -ano | findstr "LISTENING" | findstr ":8790" >nul
if errorlevel 1 (
  echo   启动 stage_server.py
  start "bancai-stage-8790" /min /d "%PKG%" "%PY%" "%PKG%\05_服务\stage_server.py" --port %PORT%
) else (
  echo   已在运行
)

echo === 5/5 等 6 秒后打开形象窗口 ===
ping -n 7 127.0.0.1 >nul

set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"

if exist "%CHROME%" (
  start "" "%CHROME%" --app="http://127.0.0.1:%PORT%/" --window-size=1600,900 --window-position=40,20
) else (
  start "" "http://127.0.0.1:%PORT%/"
)

echo.
echo 地址: http://127.0.0.1:%PORT%/
echo 键位: 1/2/3/4 切屏讲解   空格 下一条   A 自动轮播   (网址加 ?still=1 用静态人物)
echo 停止: 双击 停止四屏.bat
echo.
endlocal