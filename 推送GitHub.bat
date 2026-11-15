@echo off
chcp 65001 >nul
rem ASCII-only wrapper: test the SSH-over-443 tunnel, then push to GitHub.
cd /d "%~dp0"
set KEY=%USERPROFILE%\.ssh\id_ed25519_mcszr
echo [1/2] testing SSH over 443 ...
ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=12 -p 443 -T -i "%KEY%" git@ssh.github.com
echo [2/2] pushing to GitHub ...
git -c core.sshCommand="ssh -i %KEY% -p 443 -o StrictHostKeyChecking=accept-new" push -u github-ssh master
echo.
echo exit code = %errorlevel%  (0 = ok)
pause
