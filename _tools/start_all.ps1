# 数字人 · 一键启动全部（PowerShell 入口）
# ------------------------------------------------------------------
# 为什么要有这个 PS1：.bat 一旦被存成 LF，cmd 会把中文拆成残字节，
# 报一堆 `'项目根' is not recognized`。PowerShell 对行尾不敏感，所以把
# 真正的启动逻辑放这里，.bat 只留一层 ASCII 薄壳去调它。
#
# 用法：
#   .\一键启动全部.bat              起全部（8770/8999/桌面端/8790/8772）
#   .\一键启动全部.bat --check      只体检
#   .\一键启动全部.bat --no-window  不开浏览器
#   .\一键启动全部.bat --stop       全停
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest)

$ErrorActionPreference = 'Stop'
$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)   # _tools 的上一级

Write-Host '============================================================'
Write-Host '  数字人 · 一键启动全部'
Write-Host "  项目根 $ROOT"
Write-Host '============================================================'

$cands = @()
$cands += (Join-Path $ROOT '.venv\Scripts\python.exe')
if ($env:DH_PY) { $cands += $env:DH_PY }
$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pyCmd) { $cands += $pyCmd.Source }

$py = $null
foreach ($c in $cands) {
    if ($c -and (Test-Path $c)) { $py = $c; break }
}
if (-not $py) {
    Write-Host '[FAIL] 找不到 Python：先双击 一键部署_新电脑.bat，或在 config\paths.ini 里写 PYTHON='
    exit 2
}
Write-Host "[i] 解释器 $py"

$all = Join-Path $ROOT '_tools\dh_all.py'
if (-not (Test-Path $all)) {
    Write-Host "[FAIL] 缺 $all ：仓库不完整，重新 clone 或 git pull"
    exit 2
}

& $py $all --fix-bats @Rest
exit $LASTEXITCODE
