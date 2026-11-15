@echo off
chcp 65001 >nul
rem EOL: kept CRLF by .gitattributes (cmd needs CRLF; if broken run: python _tools\bat_lint.py --fix)
setlocal EnableExtensions

rem ============================================================
rem  兼容入口：老文档/老链接里的「一键部署_新电脑.bat」。
rem  真正的活全在 一键配置环境.bat -> _tools\dh_bootstrap.py：
rem  把 main / asr / gpu 三个环境一次装完（缺什么装什么），
rem  再修软链、修行尾、打体检表。参数原样透传。
rem ============================================================

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

if not exist "%ROOT%\一键配置环境.bat" goto missing

call "%ROOT%\一键配置环境.bat" %*
set "RC=%errorlevel%"
endlocal & exit /b %RC%

:missing
echo [FAIL] 一键配置环境.bat not found next to this file - repo is incomplete (git pull?)
pause
endlocal & exit /b 2
