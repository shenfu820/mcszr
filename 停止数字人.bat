@echo off
chcp 65001 >nul
setlocal
set ROOT=%~dp0
set PY=%ROOT%.venv\Scripts\python.exe

echo ============================================================
echo   数字人 · 停止服务
echo ============================================================
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8772 " ^| findstr LISTENING') do (
  echo 停止 PID %%a
  taskkill /PID %%a /F >nul 2>&1
)
echo 已停止（8772 端口）。
timeout /t 3 /nobreak >nul
endlocal
