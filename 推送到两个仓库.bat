@echo off
chcp 65001 >nul
rem ── 行尾：本文件由 .gitattributes 保证 CRLF（cmd 只认 CRLF；若被存成 LF，跑 python _tools\bat_lint.py --fix）
cd /d "%~dp0"

rem ============================================================
rem  一键把代码推到两个远端
rem    Gitee  : origin  (https)
rem    GitHub : github  (ssh over 443，走 ~/.ssh/config 里的 github-mcszr)
rem ============================================================

echo [1/2] 推送 Gitee ...
git push origin master
if errorlevel 1 (
  echo [失败] Gitee 推送失败，先检查网络或凭据。
) else (
  echo [OK] Gitee 完成
)

echo.
echo [2/2] 推送 GitHub（SSH 443）...
git push github master
if errorlevel 1 (
  echo [失败] GitHub 推送失败。排查顺序：
  echo   1\) ssh -T -p 443 -i %%USERPROFILE%%\.ssh\id_ed25519_mcszr git@ssh.github.com
  echo   2\) 确认 ~/.ssh/config 里有 Host github-mcszr 且文件无 BOM
) else (
  echo [OK] GitHub 完成
)

echo.
git rev-parse --short HEAD
echo 远端核对：
git ls-remote origin refs/heads/master
git ls-remote github refs/heads/master
pause
