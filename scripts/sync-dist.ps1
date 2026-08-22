# -*- coding: utf-8 -*-
"""scripts/sync-dist.ps1 — 将源码同步到发行包目录

用法（在 EggBloom 源码目录下运行）：
    powershell -ExecutionPolicy Bypass -File scripts/sync-dist.ps1

    # 指定发行包目录：
    powershell -ExecutionPolicy Bypass -File scripts/sync-dist.ps1 -DistPath "e:\EggBloomDist"

说明：
- 将源码文件复制到 {DistPath}/resources/app/
- 更新 {DistPath}/VERSION 文件（记录 commit + 日期）
- 不复制运行时数据（data/、logs/、chat_logs/ 等）
"""
param(
    [string]$DistPath = "e:\EggBloomDist"
)

$ErrorActionPreference = "Stop"
$SourceDir = Split-Path -Parent $PSScriptRoot  # scripts/ 的上级 = 项目根
$TargetDir = Join-Path $DistPath "resources\app"

Write-Host "=== EggBloom Dist Sync ===" -ForegroundColor Cyan
Write-Host "源码: $SourceDir"
Write-Host "目标: $TargetDir"
Write-Host ""

# 检查目标目录
if (-not (Test-Path $TargetDir)) {
    Write-Host "错误: 目标目录不存在: $TargetDir" -ForegroundColor Red
    exit 1
}

# 需要同步的文件/目录（白名单模式）
$syncItems = @(
    "*.py",
    "*.json",
    "*.html",
    "*.txt",
    "*.md",
    "*.toml",
    "*.yaml",
    "*.png",
    "plugins",
    "protocols",
    "webui",
    "nexsandglass",
    "components",
    "memes"
)

# 排除的运行时数据
$excludeDirs = @(
    "data", "logs", "chat_logs", "private_memory",
    "self_learning_db", "image_cache", "personality_versions",
    "__pycache__", ".git", "tests", "scripts", "docs", ".qoder"
)

$synced = 0
$skipped = 0

foreach ($item in $syncItems) {
    $srcPath = Join-Path $SourceDir $item
    if (Test-Path $srcPath) {
        if ((Get-Item $srcPath).PSIsContainer) {
            # 目录：递归复制，排除运行时数据
            $destPath = Join-Path $TargetDir $item
            Copy-Item -Path $srcPath -Destination $destPath -Recurse -Force -Exclude $excludeDirs
            $synced++
            Write-Host "  [目录] $item" -ForegroundColor Green
        } else {
            # 文件：直接复制
            Copy-Item -Path $srcPath -Destination $TargetDir -Force
            $synced++
            Write-Host "  [文件] $item" -ForegroundColor Green
        }
    } else {
        $skipped++
    }
}

# 更新 VERSION 文件
$commit = git -C $SourceDir rev-parse --short HEAD 2>$null
if (-not $commit) { $commit = "unknown" }
$now = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

$versionContent = @"
# EggBloom Desktop 发行版
# 此文件标记当前发行版对应的源码版本
# 由 scripts/sync-dist.ps1 自动更新

source_repo=$SourceDir
source_commit=$commit
sync_date=$now
version=0.7.0
"@

$versionFile = Join-Path $DistPath "VERSION"
Set-Content -Path $versionFile -Value $versionContent -Encoding UTF8

Write-Host ""
Write-Host "=== 同步完成 ===" -ForegroundColor Cyan
Write-Host "已同步: $synced 项"
Write-Host "VERSION 已更新: commit=$commit, date=$now"
