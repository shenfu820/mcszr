@echo off
chcp 65001 >nul
title 停止四屏舞台服务
set "PORT=8790"
echo 查找占用 %PORT% 的进程 ...
set "FOUND="
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
  echo   结束 pid=%%p
  taskkill /f /pid %%p >nul 2>&1
  set "FOUND=1"
)
if not defined FOUND echo   没有进程在监听 %PORT%
echo 完成。
timeout /t 2 /nobreak >nul