# 用最高权限计划任务装/起 EggBloom 高权限助手（2026-11-05）
#
# 为什么不是"以管理员身份运行"：
#   这台机器 UAC 关闭（EnableLUA=0）时，右键提权不生效；计划任务用 SYSTEM 身份
#   起服务，不参与令牌过滤，能真正拿到系统权限。
#
# 用法（需要管理员权限跑一次）：
#   powershell -ExecutionPolicy Bypass -File scripts\install_privhelper.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\install_privhelper.ps1 -Uninstall
param(
    [switch]$Uninstall,
    [string]$PythonExe = "",
    [string]$TaskName = "EggBloom-PrivHelper",
    [string]$AppRoot = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if ($AppRoot) { $root = $AppRoot }
$helper = Join-Path $root "harness\privhelper.py"

if ($Uninstall) {
    schtasks /end /tn $TaskName 2>$null | Out-Null
    schtasks /delete /tn $TaskName /f 2>$null | Out-Null
    Write-Host "已卸载计划任务 $TaskName"
    exit 0
}

if (-not (Test-Path $helper)) { throw "找不到助手脚本: $helper" }

if (-not $PythonExe) {
    $candidates = @(
        (Join-Path $root "python\python.exe"),
        (Join-Path (Split-Path -Parent $root) "python\python.exe")
    )
    foreach ($c in $candidates) { if (Test-Path $c) { $PythonExe = $c; break } }
}
if (-not $PythonExe) {
    # WindowsApps 里那个 python 是应用商店占位符，跑不了脚本 —— 明确排除
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and ($cmd.Source -notmatch 'WindowsApps')) { $PythonExe = $cmd.Source }
}
if (-not $PythonExe) { throw "找不到 python.exe，请用 -PythonExe 指定" }

Write-Host "python: $PythonExe"
Write-Host "helper: $helper"

$action = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$helper`"" -WorkingDirectory $root
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 5

try {
    $h = Invoke-RestMethod "http://127.0.0.1:3007/health" -TimeoutSec 5
    Write-Host ("助手已就绪: elevated={0} pid={1} ops={2}" -f $h.elevated, $h.pid, ($h.ops -join ","))
} catch {
    Write-Warning "助手没起来，检查计划任务 $TaskName 与 data\privhelper.log"
}
