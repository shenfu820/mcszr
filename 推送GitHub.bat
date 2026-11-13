@echo off
chcp 65001 >nul
cd /d "%~dp0"
set KEY=%USERPROFILE%\.ssh\id_ed25519_mcszr
echo [1/2] 测试 SSH(443) 通道...
ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=12 -p 443 -T -i "%KEY%" git@ssh.github.com
echo [2/2] 推送到 GitHub...
git -c core.sshCommand="ssh -i %KEY% -p 443 -o StrictHostKeyChecking=accept-new" push -u github-ssh master
echo.
echo 退出码=%errorlevel%（0 = 成功）
pause
