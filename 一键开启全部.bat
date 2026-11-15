@echo off
chcp 65001 >nul
rem EOL: kept CRLF by .gitattributes (cmd needs CRLF; if broken run: python _tools\bat_lint.py --fix)
setlocal EnableExtensions
title 数字人 - 一键开启所有系统

rem ============================================================
rem  一键开启所有系统
rem    1) 自动遍历磁盘，把各工程位置找齐（不写死任何路径）
rem    2) 交给 一键启动全部.bat：语音识别 8773 -> 大屏 8770 -> 中枢 8999
rem       -> 桌面端 -> 舞台 8790 -> 主服务 8772 -> 开窗口
rem
rem  参数原样透传：--check / --no-window / --stop / --only 8772,8790
rem ============================================================

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

call "%ROOT%\_tools\dh_findpy.cmd"
if not defined DH_FOUND_PY goto no_python

rem 先把路径找齐（静默；找不到的会在后面的体检里说清）
"%DH_FOUND_PY%" "%ROOT%\_tools\discover.py" --write --root "%ROOT%" >nul 2>nul

if not exist "%ROOT%\一键启动全部.bat" goto missing
call "%ROOT%\一键启动全部.bat" %*
set "RC=%errorlevel%"
endlocal & exit /b %RC%

:missing
echo [FAIL] 一键启动全部.bat not found next to this file - repo incomplete (git pull?)
pause
endlocal & exit /b 2

:no_python
echo [FAIL] no usable Python found. Run 一键配置环境.bat first.
pause
endlocal & exit /b 2
