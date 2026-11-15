@echo off
chcp 65001 >nul
setlocal EnableExtensions

rem ============================================================
rem  数字人 · 预渲染语音（改过剧本台词或换音色后跑一次）
rem  解释器不写死：项目 .venv 优先，没有就用系统 Python 打底并给提示
rem ============================================================
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

echo ============================================================
echo   数字人 · 预渲染语音
echo ============================================================
call "%ROOT%\_tools\dh_findpy.cmd"
if not defined DH_FOUND_PY goto no_python

echo.
echo 当前可用的中文音色（前 12 个）：
"%DH_FOUND_PY%" "%ROOT%\server\voice_gen.py" --list

echo.
echo 开始生成（已存在且完整的会自动跳过；加 --force 全部重来）...
"%DH_FOUND_PY%" "%ROOT%\server\voice_gen.py" %*

echo.
echo 完成。语音在 voice\ 目录，清单 voice\manifest.json
timeout /t 6 /nobreak >nul
endlocal & exit /b 0

:no_python
echo [错误] 找不到可用的 Python
echo        先双击 一键部署_新电脑.bat，或装 Python 3.10 并勾选 Add python.exe to PATH
echo.
pause
endlocal & exit /b 2
