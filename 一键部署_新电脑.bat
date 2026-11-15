@echo off
chcp 65001 >nul
rem ── 行尾：本文件由 .gitattributes 保证 CRLF（cmd 只认 CRLF；若被存成 LF，跑 python _tools\bat_lint.py --fix）
setlocal EnableExtensions
title 数字人 · 一键部署（新电脑）

rem ============================================================
rem  新电脑上第一步：双击这个。
rem  它只用系统里的任意 Python 3.9+，把项目 .venv 建起来、装 edge-tts、
rem  探测语音/GPU/产线库/数据集写进 config\paths.ini，最后打印体检表。
rem
rem  参数：
rem     --check       只看会做什么，不动文件
rem     --no-install  只建 venv 不装包
rem     --offline     只用 wheels\ 目录离线装
rem ============================================================

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

echo ============================================================
echo   数字人 · 一键部署（新电脑）
echo   项目根 %ROOT%
echo ============================================================

call "%ROOT%\_tools\dh_findpy.cmd"
if not defined DH_FOUND_PY goto no_python

echo [i] 用这个解释器打底：%DH_FOUND_PY%
echo [i] 来源：%DH_FOUND_SRC%
echo.

"%DH_FOUND_PY%" "%ROOT%\_tools\dh_deploy.py" --root "%ROOT%" --python "%DH_FOUND_PY%" %*
set "RC=%errorlevel%"

echo.
if "%RC%"=="0" echo [OK] 部署完成：现在可以双击 启动数字人_语音版.bat
if not "%RC%"=="0" echo [FAIL] 没部署完：按上面提示处理，然后重跑本脚本
echo.
pause
endlocal & exit /b %RC%

:no_python
echo [错误] 这台机器上找不到任何 Python 3.9 及以上
echo.
echo   1. 装 Python 3.10：https://www.python.org/downloads/windows/
echo      安装时勾选 Add python.exe to PATH
echo   2. 装完重跑本脚本
echo.
pause
endlocal & exit /b 2
