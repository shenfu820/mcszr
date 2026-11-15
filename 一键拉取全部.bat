@echo off
chcp 65001 >nul
rem ASCII-only wrapper: Chinese output comes from Python (a .bat with Chinese gets
rem torn apart by cmd on some machines -- 'xxx' is not recognized, xxx = CN fragment).
setlocal EnableExtensions
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

call "%ROOT%\_tools\dh_findpy.cmd"
if not defined DH_FOUND_PY goto no_python

echo ==== 1/3 discover projects on disk (no hard-coded paths) ====
"%DH_FOUND_PY%" "%ROOT%\_tools\discover.py" --write --root "%ROOT%" %*
echo.
echo ==== 2/3 git pull --ff-only (all registered repos) ====
"%DH_FOUND_PY%" "%ROOT%\_tools\repos.py" --root "%ROOT%" --pull all
set "RC2=%errorlevel%"
echo.
echo ==== 3/3 summary ====
"%DH_FOUND_PY%" "%ROOT%\_tools\repos.py" --root "%ROOT%" --report
echo.
if "%RC2%"=="0" echo [OK] all repos are up to date
if not "%RC2%"=="0" echo [WARN] some repos were skipped or failed - read the lines above
echo.
echo [i] create missing local repos: python _tools\repos.py --init-missing --yes
echo.
pause
endlocal & exit /b %RC2%

:no_python
echo [FAIL] no usable Python found. Run the one-click env setup script first.
pause
endlocal & exit /b 2
