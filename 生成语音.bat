@echo off
chcp 65001 >nul
setlocal
set ROOT=%~dp0
set PY=%ROOT%.venv\Scripts\python.exe

echo ============================================================
echo   数字人 · 预渲染语音（改过剧本台词或换音色后跑一次）
echo ============================================================
if not exist "%PY%" (
  echo [错误] 找不到虚拟环境：%PY%
  pause
  exit /b 1
)

echo.
echo 当前可用的中文音色（前 12 个）：
"%PY%" "%ROOT%server\voice_gen.py" --list 2>nul | more +0 | findstr /n "^" | findstr /b /r "[1-9]: [0-9]*: \|^[1-9][0-9]*: " >nul
"%PY%" "%ROOT%server\voice_gen.py" --list

echo.
echo 开始生成（已存在且完整的会自动跳过；加 --force 全部重来）...
"%PY%" "%ROOT%server\voice_gen.py" %*

echo.
echo 完成。语音在 voice\ 目录，清单 voice\manifest.json
timeout /t 6 /nobreak >nul
endlocal
