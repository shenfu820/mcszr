@echo off
chcp 65001 >nul
rem ── 行尾：本文件由 .gitattributes 保证 CRLF（cmd 只认 CRLF；若被存成 LF，跑 python _tools\bat_lint.py --fix）
setlocal EnableExtensions
title 数字人 · 语音交互版

rem ============================================================
rem  数字人 · 语音交互版一键启动（跨机器版）
rem  这里不写死任何绝对路径：解释器、语音环境、GPU 环境、产线库、数据集、
rem  浏览器、端口全部由 _tools\dh_env.py 现场探测，配置在 config\paths.ini。
rem
rem  用法：
rem     启动数字人_语音版.bat            正常启动并开窗口
rem     启动数字人_语音版.bat --check    只体检，不起服务（有 FAIL 退出码 1）
rem     启动数字人_语音版.bat --no-window 起服务但不开窗口
rem     启动数字人_语音版.bat --no-asr   不拉语音识别（只打字）
rem ============================================================

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "TOOLS=%ROOT%\_tools"
set "CHECK_ONLY=0"
set "NO_WINDOW=0"
set "NO_ASR=0"
set "NO_PAUSE=0"

:args
if "%~1"=="" goto args_done
if /i "%~1"=="--check" set "CHECK_ONLY=1"
if /i "%~1"=="--no-window" set "NO_WINDOW=1"
if /i "%~1"=="--no-asr" set "NO_ASR=1"
if /i "%~1"=="--no-pause" set "NO_PAUSE=1"
if /i "%~1"=="--help" goto usage
if /i "%~1"=="-h" goto usage
shift
goto args
:args_done

echo ============================================================
echo   数字人 · 语音交互版一键启动
echo   项目根 %ROOT%
echo ============================================================

rem ---------- 0. 找解释器 ----------
call "%TOOLS%\dh_findpy.cmd"
if not defined DH_FOUND_PY goto no_python
set "PY=%DH_FOUND_PY%"
echo [i] 解释器 %PY%
echo [i] 来源   %DH_FOUND_SRC%

rem ---------- 0.5 探测路径（语音/GPU/产线库/数据集/浏览器/端口） ----------
set "ENVCMD=%TEMP%\dh_env_%RANDOM%%RANDOM%.cmd"
"%PY%" "%TOOLS%\dh_env.py" --emit-bat "%ENVCMD%" --root "%ROOT%" >nul 2>nul
if not exist "%ENVCMD%" goto env_default
call "%ENVCMD%"
del "%ENVCMD%" >nul 2>&1
goto env_ready

:env_default
echo [警告] 路径探测脚本没跑通，按默认端口继续
set "PORT=8772"
set "ASR_PORT=8773"
set "DH_ASR_OK=0"
set "DH_BROWSER_OK=0"
goto env_ready

:env_ready
if "%CHECK_ONLY%"=="1" goto do_check
if not "%DH_EDGE_TTS%"=="1" goto env_incomplete

rem ---------- 1. ASCII 软链（中文路径进不了子进程时的兜底） ----------
echo [1/4] ASCII 软链
call :ensure_junction "C:\dh_root" "%ROOT%"
if defined DH_PROD call :ensure_junction "C:\dh_prod" "%DH_PROD%"
if defined DH_DS call :ensure_junction "C:\dh_ds" "%DH_DS%"
if not exist "C:\dh_root" echo [警告] C:\dh_root 建不起来：训练类作业会因为在中文路径下跑而失败

rem ---------- 2. 语音识别 8773 ----------
echo [2/4] 语音识别 端口 %ASR_PORT%
"%PY%" "%TOOLS%\dh_spawn.py" status --port %ASR_PORT% | findstr /c:"busy" >nul
if not errorlevel 1 goto asr_skip
if "%NO_ASR%"=="1" goto asr_off
if not "%DH_ASR_OK%"=="1" goto asr_missing
"%PY%" "%TOOLS%\dh_spawn.py" start --name asr --py "%DH_ASR_PY%" --script "%ROOT%\server\asr_service.py" --port %ASR_PORT%
echo        模型常驻约 15~60 秒，首次要下模型更久；日志 logs\asr.log
goto asr_done

:asr_skip
echo        已在监听，跳过
goto asr_done

:asr_off
echo        已按 --no-asr 跳过，本轮只能打字
goto asr_done

:asr_missing
echo [警告] 本机没有装了 funasr 的环境：本轮只能打字，不能说话。
echo        要说话：pip install -r requirements-asr.txt，然后把那个环境的
echo        python.exe 写进 config\paths.ini 的 ASR_PYTHON=
goto asr_done

:asr_done

rem ---------- 3. 主服务 8772 ----------
echo [3/4] 数字人主服务 端口 %PORT%
"%PY%" "%TOOLS%\dh_spawn.py" status --port %PORT% | findstr /c:"busy" >nul
if not errorlevel 1 goto svc_skip
"%PY%" "%TOOLS%\dh_spawn.py" start --name server --py "%PY%" --script "%ROOT%\server\dh_server.py" --port %PORT%
"%PY%" "%TOOLS%\dh_spawn.py" wait --name 主服务 --url "http://127.0.0.1:%PORT%/api/health" --timeout 30 --expect digital-human
if errorlevel 1 echo [警告] 主服务 30 秒没回 /api/health，看 logs\server.log
goto svc_done

:svc_skip
echo        已在监听，跳过
goto svc_done

:svc_done

rem ---------- 4. 窗口 ----------
echo [4/4] 打开窗口
if "%NO_WINDOW%"=="1" goto no_win
if not "%DH_BROWSER_OK%"=="1" goto win_default
start "" "%DH_BROWSER%" --app="http://127.0.0.1:%PORT%/xx/xiaoxing5.html" --window-size=1400,900 --window-position=20,30 --user-data-dir="%TEMP%\dh-xiaoxing-profile" --no-first-run --use-fake-ui-for-media-stream --autoplay-policy=no-user-gesture-required
start "" "%DH_BROWSER%" --app="http://127.0.0.1:%PORT%/console/" --window-size=1280,820 --window-position=1260,40 --user-data-dir="%TEMP%\dh-console-profile" --no-first-run --use-fake-ui-for-media-stream --autoplay-policy=no-user-gesture-required
goto win_done

:win_default
echo        没找到 Chrome/Edge，用系统默认浏览器打开
start "" "http://127.0.0.1:%PORT%/xx/xiaoxing5.html"
start "" "http://127.0.0.1:%PORT%/console/"
goto win_done

:no_win
echo        已按 --no-window 跳过
goto win_done

:win_done
echo.
echo ============================================================
echo   形象窗口   http://127.0.0.1:%PORT%/xx/xiaoxing5.html
echo   运维屏     http://127.0.0.1:%PORT%/avatar/ops.html
echo   控制台     http://127.0.0.1:%PORT%/console/
echo   语音识别   http://127.0.0.1:%ASR_PORT%/health
echo.
echo   现场用法：
echo     - 按住「麦克风」说一句，松手就发；开「连续听」可以免提。
echo     - 说「让 GPU 跑一下训练」，GPU 会真转起来，跑完自己停。
echo     - 关掉全部服务：双击 停止数字人.bat
echo   首次点一下形象窗口任意位置，解锁浏览器声音。
echo ============================================================
if "%NO_PAUSE%"=="1" goto finally
pause
:finally
endlocal & exit /b 0

rem ============ 环境不全：不装作看得见 ============
:env_incomplete
echo.
echo [FAIL] 环境没装好：这个解释器里没有 edge-tts，起来也没有声音
echo        当前解释器 %PY%
echo        来源 %DH_PY_SRC%
echo.
echo        修法：双击 一键部署_新电脑.bat
echo              它会建好项目 .venv 并装上 edge-tts（约 1 分钟）
echo        或者：在 config\paths.ini 里 PYTHON= 指向已装好依赖的 python.exe
echo.
if "%NO_PAUSE%"=="0" pause
endlocal & exit /b 3

rem ============ 体检模式 ============
:do_check
echo.
"%PY%" "%TOOLS%\dh_env.py" --check --root "%ROOT%"
set "RC=%errorlevel%"
echo.
if "%RC%"=="0" echo [OK] 体检通过：可以直接双击启动数字人_语音版.bat
if not "%RC%"=="0" echo [FAIL] 体检没过：先解决上面 FAIL 行，或双击 一键部署_新电脑.bat
endlocal & exit /b %RC%

rem ============ 子过程 ============
:ensure_junction
if exist "%~1" goto :eof
if not exist "%~2" goto :eof
cmd /c mklink /J "%~1" "%~2" >nul 2>&1
if exist "%~1" echo        已建软链 %~1 指向 %~2
goto :eof

:usage
echo 用法：%~nx0 [--check] [--no-window] [--no-asr] [--no-pause]
echo   --check      只体检不起服务
echo   --no-window  起服务但不开浏览器窗口
echo   --no-asr     不拉语音识别
echo   --no-pause   结束时不等按键
endlocal & exit /b 0

:no_python
echo [错误] 这台机器上找不到可用的 Python 3.9 及以上
echo.
echo   办法一  双击 一键部署_新电脑.bat，它会带着你把 .venv 建起来
echo   办法二  装 Python 3.10 后重跑本脚本，安装时勾选 Add python.exe to PATH
echo   办法三  在 config\paths.ini 里写 PYTHON=D:\Python310\python.exe
echo.
echo   已经不写死路径了：不会再出现「找不到虚拟环境 C:\Users\你\PycharmProjects\...」这种死路
echo.
if "%NO_PAUSE%"=="0" pause
endlocal & exit /b 2
