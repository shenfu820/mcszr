@echo off
chcp 65001 >nul
rem ASCII-only wrapper: pushes master to both remotes.
rem   Gitee  : origin  (https, credential helper already configured on this machine)
rem   GitHub : github  (ssh over 443 via ~/.ssh/config Host github-mcszr)
cd /d "%~dp0"

echo [1/2] push to Gitee (origin) ...
git push origin master
if errorlevel 1 (
  echo [FAIL] Gitee push failed - check network or credentials.
) else (
  echo [OK] Gitee done
)

echo.
echo [2/2] push to GitHub (ssh over 443) ...
git push github master
if errorlevel 1 (
  echo [FAIL] GitHub push failed. Check in this order:
  echo   1^) ssh -T -p 443 -i %%USERPROFILE%%\.ssh\id_ed25519_mcszr git@ssh.github.com
  echo   2^) ~/.ssh/config must have Host github-mcszr, file must not have a BOM
) else (
  echo [OK] GitHub done
)

echo.
git rev-parse --short HEAD
echo remote heads:
git ls-remote origin refs/heads/master
git ls-remote github refs/heads/master
pause
