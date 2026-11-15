# 用最高权限计划任务装/起 EggBloom 高权限助手（2026-11-05）
#
# 为什么不是"以管理员身份运行"：
#   这台机器 UAC 关闭（EnableLUA=0）时，右键提权不生效；计划任务用 SYSTEM 身份
#   起服务，不参与令牌过滤，能真正拿到系统权限。
#
# 安装后开机 / 登录自动拉起（AtStartup + AtLogOn），不必再手工 Start-ScheduledTask。
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

# ★ 2026-11-05 P0-1：开机 / 登录自动拉起。
#   此前 Register-ScheduledTask 没传 -Trigger，任务 Triggers 为空 —— 今天只能手工
#   Start-ScheduledTask 才起得来。重启后助手缺席 → 协议页「以高权限启动」退回 UAC
#   兜底，本机 EnableLUA=0 又提不了权 → SnowLuma 注入不了 QQ → 机器人收发全断。
#   · AtStartup：开机即起，不依赖用户是否已登录（验收：重启后直接 curl :3007/health）
#   · AtLogOn ：登录后补一次触发；MultipleInstancesPolicy=IgnoreNew 不会起第二份
$triggers = @(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -AtLogOn)
)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
    -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 5

try {
    $h = Invoke-RestMethod "http://127.0.0.1:3007/health" -TimeoutSec 5
    Write-Host ("助手已就绪: elevated={0} pid={1} ops={2}" -f $h.elevated, $h.pid, ($h.ops -join ","))
} catch {
    Write-Warning "助手没起来，检查计划任务 $TaskName 与 data\privhelper.log"
}
