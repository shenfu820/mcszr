@echo off
chcp 65001 >nul
setlocal

rem ============================================================
rem  数字人 · 语音交互版一键启动
rem  1) 语音识别服务 8773（voice 环境，模型常驻内存）
rem  2) 数字人主服务 8772（.venv）
rem  3) 形象窗口 + 运维屏 + 控制台
rem ============================================================

set ROOT=%~dp0
set PY=%ROOT%.venv\Scripts\python.exe
set ASRPY=E:\conda3\envs\voice\python.exe
set PORT=8772
set ASRPORT=8773

if not exist "%PY%" (
  echo [错误] 找不到虚拟环境：%PY%
  pause & exit /b 1
)

echo [0/3] 检查 ASCII 软链（子进程传中文路径会乱码，必须走软链）...
if not exist "C:\dh_root" cmd /c mklink /J "C:\dh_root" "%ROOT:~0,-1%" >nul 2>&1
if not exist "C:\dh_prod" cmd /c mklink /J "C:\dh_prod" "C:\Users\Administrator\Desktop\项目与竞赛\aa比赛项目\new mcjc" >nul 2>&1
if not exist "C:\dh_ds" cmd /c mklink /J "C:\dh_ds" "F:\木材缺陷数据集6类3000+" >nul 2>&1
if not exist "C:\dh_ds\images\train" echo [警告] 数据集软链不在，训练类作业会失败：C:\dh_ds ^<- F:\木材缺陷数据集6类3000+

echo [1/3] 检查语音识别服务 端口 %ASRPORT% ...
netstat -ano | findstr ":%ASRPORT% " | findstr LISTENING >nul
if errorlevel 1 (
  if exist "%ASRPY%" (
    echo        起来中，模型加载约 15~60 秒（首次下载会久一些）……
    start "数字人ASR" /min cmd /c ""%ASRPY%" "%ROOT%server\asr_service.py" --port %ASRPORT%"
  ) else (
    echo [警告] 找不到 voice 环境：%ASRPY%
    echo        没有它就只能打字，不能说话。装了 funasr 的环境都行。
  )
) else (
  echo        已在跑，跳过。
)

echo [2/3] 检查数字人主服务 端口 %PORT% ...
netstat -ano | findstr ":%PORT% " | findstr LISTENING >nul
if errorlevel 1 (
  start "数字人服务" /min cmd /c ""%PY%" "%ROOT%server\dh_server.py" --port %PORT%"
  timeout /t 3 /nobreak >nul
) else (
  echo        已在跑，跳过。
)

echo [3/3] 打开窗口……
set EDGE=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe
set CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe
set BROWSER=
if exist "%CHROME%" set BROWSER=%CHROME%
if not defined BROWSER if exist "%EDGE%" set BROWSER=%EDGE%

if defined BROWSER (
  rem 小星本体（形象 + 身边运维屏在同一个窗口里，可以直接拖到副屏）
  start "" "%BROWSER%" --app="http://127.0.0.1:%PORT%/xx/xiaoxing5.html" ^
        --window-size=1400,900 --window-position=20,30 ^
        --user-data-dir="%TEMP%\dh-xiaoxing-profile" --no-first-run ^
        --use-fake-ui-for-media-stream --autoplay-policy=no-user-gesture-required
) else (
  start "" "http://127.0.0.1:%PORT%/xx/xiaoxing5.html"
)

timeout /t 2 /nobreak >nul
rem 控制台用独立 profile + 自动放行麦克风权限，现场不用点允许弹窗
if defined BROWSER (
  start "" "%BROWSER%" --app="http://127.0.0.1:%PORT%/console/" ^
        --window-size=1280,820 --window-position=1260,40 ^
        --user-data-dir="%TEMP%\dh-console-profile" --no-first-run ^
        --use-fake-ui-for-media-stream --autoplay-policy=no-user-gesture-required
) else (
  start "" "http://127.0.0.1:%PORT%/console/"
)

echo.
echo ============================================================
echo   形象窗口   http://127.0.0.1:%PORT%/avatar/
echo   运维屏     http://127.0.0.1:%PORT%/avatar/ops.html
echo   控制台     http://127.0.0.1:%PORT%/console/
echo   语音识别   http://127.0.0.1:%ASRPORT%/health
echo.
echo   现场用法：
echo     - 按住「🎤 按住说话」说一句，松手就发；开「连续听」可以免提。
echo     - 说「让 GPU 跑一下训练」，GPU 会真转起来，跑完自己停。
echo     - 运维屏右边那块会实时画模型性能趋势和 GPU 占用。
echo   首次点一下形象窗口任意位置，解锁浏览器声音。
echo ============================================================
pause
endlocal
