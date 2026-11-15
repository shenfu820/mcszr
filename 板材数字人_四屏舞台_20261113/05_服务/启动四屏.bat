@echo off
chcp 65001 >nul
rem ── 行尾：本文件由 .gitattributes 保证 CRLF（cmd 只认 CRLF；若被存成 LF，跑 python _tools\bat_lint.py --fix）
setlocal EnableExtensions
title 板材数字人 - 四屏舞台

rem ============================================================
rem  四屏舞台一键起（跨机器版）
rem  解释器与产线目录不再写死：
rem    解释器   _tools\dh_findpy.cmd + dh_env.py 探测（优先带 CUDA 的那个）
rem    产线目录 config\paths.ini 的 PROD_ROOT（指向 new mcjc 本身）
rem  本机没有产线（另一台电脑只放屏）也能起：
rem    ①②③ 三块屏会各自显示"未连接"，舞台、形象、自学习闭环照常；
rem    要把屏接到产线机，在 config\paths.ini 里写
rem      SCREEN_URL=http://产线机IP:8770
rem      AGENT_URL=http://产线机IP:8999
rem    （产线机那边的 screen_service / agent_service 要监听 0.0.0.0，不是 127.0.0.1）
rem  只体检不起服务：本脚本加 --check
rem ============================================================

set "PKG=%~dp0.."
set "PROJROOT=%~dp0..\.."
set "PORT=8790"

rem ---- 产线目录：config\paths.ini 里读，读不到就用 dh_env 探测到的 ----
set "PROJ="
if exist "%PROJROOT%\config\paths.ini" for /f "usebackq tokens=1,* delims==" %%A in ("%PROJROOT%\config\paths.ini") do if /i "%%A"=="PROD_ROOT" set "PROJ=%%B"
if defined PROJ if not exist "%PROJ%" set "PROJ="

rem ---- 解释器 ----
set "PY="
if exist "%PROJROOT%\_tools\dh_findpy.cmd" call "%PROJROOT%\_tools\dh_findpy.cmd"
if defined DH_FOUND_PY set "PY=%DH_FOUND_PY%"
if not defined PY goto use_path_python
set "ENVCMD=%TEMP%\dh_env4_%RANDOM%%RANDOM%.cmd"
"%PY%" "%PROJROOT%\_tools\dh_env.py" --emit-bat "%ENVCMD%" --root "%PROJROOT%" >nul 2>nul
if not exist "%ENVCMD%" goto after_env
call "%ENVCMD%"
del "%ENVCMD%" >nul 2>&1
rem 探测脚本会顺带设 PORT（主服务端口），这里要抢回来
set "PORT=8790"
if not defined PROJ if defined DH_PROD set "PROJ=%DH_PROD%"
if defined DH_GPU_PY set "PY=%DH_GPU_PY%"
goto after_env

:use_path_python
set "PY=python"

:after_env
echo 解释器 %PY%
echo 产线目录 %PROJ%
if not defined PROJ echo [警告] 没找到产线目录：在 config\paths.ini 里写 PROD_ROOT=...\new mcjc
echo.
if /i "%~1"=="--check" goto check_only

echo === 1/5 大屏 8770 ===
netstat -ano | findstr "LISTENING" | findstr ":8770" >nul
if errorlevel 1 goto start_screen
echo   已在运行
goto step2

:start_screen
if not defined PROJ goto no_proj
if not exist "%PROJ%\screen_service.py" goto no_proj
echo   启动 screen_service.py
start "mcjc-screen-8770" /min /d "%PROJ%" "%PY%" screen_service.py --port 8770
goto step2

:step2
echo === 2/5 对话中枢 8999 ===
netstat -ano | findstr "LISTENING" | findstr ":8999" >nul
if errorlevel 1 goto start_agent
echo   已在运行
goto step3

:start_agent
if not defined PROJ goto no_proj
if not exist "%PROJ%\..\angent\agent_service.py" goto no_agent
echo   启动 agent_service.py
start "angent-8999" /min /d "%PROJ%\..\angent" "%PY%" agent_service.py --port 8999
goto step3

:no_agent
echo   跳过：没找到 angent\agent_service.py
goto step3

:step3
echo === 3/5 桌面端 run.py ===
tasklist /fi "imagename eq python.exe" | findstr /i "python" >nul
if errorlevel 1 goto start_desktop
echo   已有 python 进程，若桌面端没开请手动运行 run.py
goto step4

:start_desktop
if not defined PROJ goto no_proj
if not exist "%PROJ%\run.py" goto no_proj
echo   启动 run.py
start "mcjc-desktop" /min /d "%PROJ%" "%PY%" run.py
goto step4

:step4
echo === 4/5 舞台服务 %PORT% ===
netstat -ano | findstr "LISTENING" | findstr ":%PORT%" >nul
if errorlevel 1 goto start_stage
echo   已在运行
goto step5

:start_stage
echo   启动 stage_server.py
start "bancai-stage-%PORT%" /min /d "%PKG%" "%PY%" "%PKG%\05_服务\stage_server.py" --port %PORT%
goto step5

:step5
echo === 5/5 等 6 秒后打开形象窗口 ===
ping -n 7 127.0.0.1 >nul

set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%CHROME%" set "CHROME=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"

if not exist "%CHROME%" goto open_default
start "" "%CHROME%" --app="http://127.0.0.1:%PORT%/" --window-size=1600,900 --window-position=40,20
goto hello

:open_default
start "" "http://127.0.0.1:%PORT%/"

:hello
echo.
echo 地址: http://127.0.0.1:%PORT%/
echo 键位: 1/2/3/4 切屏讲解   空格 下一条   A 自动轮播   (网址加 ?still=1 用静态人物)
echo 停止: 双击 停止四屏.bat
echo.
goto eof

:check_only
echo [check] 解释器   %PY%
echo [check] 产线目录 %PROJ%
echo [check] 包目录   %PKG%
echo [check] 舞台端口 %PORT%
set "SURL="
set "AURL="
if exist "%PROJROOT%\config\paths.ini" for /f "usebackq tokens=1,* delims==" %%A in ("%PROJROOT%\config\paths.ini") do if /i "%%A"=="SCREEN_URL" set "SURL=%%B"
if exist "%PROJROOT%\config\paths.ini" for /f "usebackq tokens=1,* delims==" %%A in ("%PROJROOT%\config\paths.ini") do if /i "%%A"=="AGENT_URL" set "AURL=%%B"
if defined SURL echo [check] 大屏地址 %SURL%
if defined AURL echo [check] 中枢地址 %AURL%
if not exist "%PKG%\xiaoxing5.html" echo [FAIL] 缺 xiaoxing5.html：五屏页面打不开
if not exist "%PKG%\05_服务\stage_server.py" echo [FAIL] 缺 stage_server.py：舞台服务起不来
if not exist "%PROJ%\screen_service.py" echo [WARN] 缺产线 screen_service.py：大屏 8770 起不来（屏上会显示"未连接"）
if not exist "%PROJ%\..\angent\agent_service.py" echo [WARN] 缺 angent\agent_service.py：对话中枢 8999 起不来（屏上会显示"未连接"）
if not defined PROJ echo [WARN] 本机没有产线目录：①②③ 屏显示"未连接"，舞台与自学习闭环照常（远端接法见本文件头注释）
endlocal & exit /b 0

:no_proj
echo   跳过：产线目录或其中的脚本不存在（见上面提示）
goto step2

:eof
endlocal & exit /b 0
