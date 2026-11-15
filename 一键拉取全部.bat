@echo off
chcp 65001 >nul
rem EOL: kept CRLF by .gitattributes (cmd needs CRLF; if broken run: python _tools\bat_lint.py --fix)
setlocal EnableExtensions
title 数字人 - 一键拉取所有系统仓库

rem ============================================================
rem  一键拉取所有系统的仓库
rem    1) 自动遍历磁盘，把各工程的位置找出来写回 config\paths.ini
rem    2) 按 config\repos.ini 逐个 git pull --ff-only（有本地改动就跳过并报出来）
rem    3) 打印汇总
rem
rem  参数：--extra-root D:\work   额外扫一个目录
rem ============================================================

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

call "%ROOT%\_tools\dh_findpy.cmd"
if not defined DH_FOUND_PY goto no_python

echo ==== 1/3 自动遍历找系统（不写死路径）====
"%DH_FOUND_PY%" "%ROOT%\_tools\discover.py" --write --root "%ROOT%" %*
set "RC1=%errorlevel%"

echo.
echo ==== 2/3 拉取所有仓库 ====
"%DH_FOUND_PY%" "%ROOT%\_tools\repos.py" --root "%ROOT%" --pull all
set "RC2=%errorlevel%"

echo.
echo ==== 3/3 汇总 ====
"%DH_FOUND_PY%" "%ROOT%\_tools\repos.py" --root "%ROOT%" --report

echo.
if "%RC2%"=="0" echo [OK] 所有仓库都已是最新（或已跳过，原因见上）
if not "%RC2%"=="0" echo [WARN] 有仓库没拉成（本地有改动 / 分叉 / 没远端），逐条看上面
echo [i] 补建缺失的仓库：python _tools\repos.py --init-missing --yes
echo.
pause
if not "%RC2%"=="0" endlocal & exit /b %RC2%
endlocal & exit /b 0

:no_python
echo [FAIL] no usable Python found. Run 一键配置环境.bat first.
pause
endlocal & exit /b 2
