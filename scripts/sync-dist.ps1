# scripts/sync-dist.ps1 — 将源码同步到发行包目录
#
# 用法（在 EggBloom 源码目录下运行）：
#     powershell -ExecutionPolicy Bypass -File scripts/sync-dist.ps1
#     powershell -ExecutionPolicy Bypass -File scripts/sync-dist.ps1 -DistPath "E:\Somewhere\EggBloom"
#     powershell -ExecutionPolicy Bypass -File scripts/sync-dist.ps1 -DryRun      # 只列出会改什么，不写盘
#
# ── 2026-10-03 修复记录 ────────────────────────────────────────────────
# 旧版有三个问题，都会造成实际损坏，本次一并修掉：
#
#  1) 套娃：旧版用 `Copy-Item -Recurse -Destination <已存在的目录>`。
#     PowerShell 在目标目录已存在时，会把「源目录本身」塞进目标里当子目录，
#     于是产生 webui\webui\、plugins\web_operator\web_operator\、memes\memes\
#     这类目录，跑一次多一层。现在改用 robocopy（语义是「合并」）。
#
#  2) 默认路径写死 e:\EggBloomDist，与实际安装位置不符，且从未生成过 VERSION
#     （说明从没成功跑过）。现在改为自动探测：在 C:\ D:\ E:\ 下按两层深度
#     查找含 resources\app\webui\webui_server.py 的目录。
#
#  3) 只用 /E，绝不用 /MIR —— robocopy 不会删除目标端任何文件。
#     用户数据（bot_config.json / .env / .neurobase / chat_logs / data /
#     plugin_state.json / last_port.txt 等）不会被覆盖或删除。
#
# 另：旧版会让同名的 data_store.py 在两边打架（源码库是数据目录解析器，
#     发行副本是 JSON 读写工具）。该冲突已在源码侧合并修复（提交 da8f2b0），
#     现在两边用同一份「两者兼备」的模块，同步这个文件是安全的。

param(
    [string]$DistPath = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$SourceDir = Split-Path -Parent $PSScriptRoot   # scripts\ 的上级 = 项目根

# ── 目标目录探测 ──────────────────────────────────────────────────────
function Test-DistRoot([string]$p) {
    if (-not $p) { return $false }
    return (Test-Path (Join-Path $p "resources\app\webui\webui_server.py"))
}

function Find-DistRoot {
    $found = @()

    if ($env:EGGBLOOM_DIST) { $found += $env:EGGBLOOM_DIST }

    foreach ($drive in @("C:\", "D:\", "E:\", "F:\")) {
        if (-not (Test-Path $drive)) { continue }
        foreach ($d1 in (Get-ChildItem -Path $drive -Directory -Force -ErrorAction SilentlyContinue)) {
            if (Test-DistRoot $d1.FullName) { $found += $d1.FullName; continue }
            foreach ($d2 in (Get-ChildItem -Path $d1.FullName -Directory -Force -ErrorAction SilentlyContinue)) {
                if (Test-DistRoot $d2.FullName) { $found += $d2.FullName }
            }
        }
    }
    return @($found | Where-Object { Test-DistRoot $_ } | Select-Object -Unique)
}

if (-not $DistPath) {
    Write-Host "未指定 -DistPath，正在自动探测发行包目录..." -ForegroundColor Cyan
    $cands = @(Find-DistRoot)
    if ($cands.Count -eq 1) {
        $DistPath = $cands[0]
        Write-Host "  唯一候选: $DistPath" -ForegroundColor Green
    } elseif ($cands.Count -eq 0) {
        $DistPath = ""
    } else {
        # 有多个候选时**绝不擅自挑选**。
        # 血泪教训（2026-10-03）：本机同时存在 E:\EggBloomDist（8 月的旧副本）与
        # E:\EggBloom水星\EggBloom Desktop-new（在用的那份），旧逻辑取"第一个匹配"
        # 把文件同步进了废弃副本。同步会覆盖目标文件，猜错 = 改错项目。
        Write-Host ""
        Write-Host "错误：发现多个发行包目录，无法自动确定同步目标。" -ForegroundColor Red
        Write-Host "  同步会覆盖目标文件，猜错等于改错项目，因此这里不做猜测。" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  候选列表（按该副本内文件最近改动时间排序）：" -ForegroundColor Cyan
        $rows = foreach ($c in $cands) {
            $appd = Join-Path $c "resources\app"
            $newest = Get-ChildItem $appd -Recurse -File -Force -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -notmatch '__pycache__' } |
                Sort-Object LastWriteTime -Descending | Select-Object -First 1
            [pscustomobject]@{
                Path     = $c
                最近改动 = if ($newest) { $newest.LastWriteTime.ToString('yyyy-MM-dd HH:mm') } else { '-' }
            }
        }
        $sorted = @($rows | Sort-Object 最近改动 -Descending)
        ($sorted | Format-Table -AutoSize | Out-String -Width 200) | Write-Host
        Write-Host "请用 -DistPath 明确指定其一（上面第一行通常就是你在用的那份），例如:" -ForegroundColor Yellow
        Write-Host ('  powershell -ExecutionPolicy Bypass -File scripts/sync-dist.ps1 -DistPath "' + $sorted[0].Path + '"') -ForegroundColor Yellow
        Write-Host "（要长期固定，可设环境变量 EGGBLOOM_DIST 指向目标）" -ForegroundColor DarkGray
        exit 1
    }
}

if (-not (Test-DistRoot $DistPath)) {
    Write-Host ""
    Write-Host "错误：没找到发行包目录。" -ForegroundColor Red
    Write-Host "  给出的路径: '$DistPath'"
    Write-Host "  判定标准  : 该目录下存在 resources\app\webui\webui_server.py"
    Write-Host ""
    Write-Host "请用 -DistPath 明确指定，例如:" -ForegroundColor Yellow
    Write-Host '  powershell -ExecutionPolicy Bypass -File scripts/sync-dist.ps1 -DistPath "E:\EggBloom水星\EggBloom Desktop-new"'
    exit 1
}

$DistPath  = (Resolve-Path $DistPath).Path
$TargetDir = Join-Path $DistPath "resources\app"

Write-Host "=== EggBloom Dist Sync ===" -ForegroundColor Cyan
Write-Host "源码: $SourceDir"
Write-Host "目标: $TargetDir"
if ($DryRun) { Write-Host "模式: DRY RUN（只看不改）" -ForegroundColor Yellow }
Write-Host ""

# ── 同步范围 ──────────────────────────────────────────────────────────
# 根目录按扩展名白名单复制
$rootExts = @(".py", ".json", ".html", ".txt", ".md", ".toml", ".yaml", ".yml", ".png")

# 整目录复制（docs 也带上：发行副本里能直接看到权威文档，不必回源码库翻）
$dirItems = @("plugins", "protocols", "webui", "nexsandglass", "components", "memes", "docs")

# 排除的运行时数据（任意层级同名目录）
$excludeDirs = @(
    "data", "logs", "chat_logs", "private_memory", "self_learning_db",
    "image_cache", "personality_versions", "__pycache__", ".git", "tests",
    "scripts", ".qoder", ".neurobase", "node_modules", ".ruff_cache",
    ".venv", "venv", "build", "dist"
)

# 排除的文件（防止覆盖用户状态 / 防止把重复副本又搬回去）
$excludeFiles = @(
    "last_port.txt", "bot_config.json", "plugin_state.json",
    "secret.json", "local_config.json", "machine_config.json",
    # 真实前端只有 webui\webui_artistic_v3.html（_frontend_path = HERE/"webui_artistic_v3.html"）。
    # 根目录若出现同名文件即为重复副本——同步时不要把它搬到发行包根目录，
    # 否则又会出现"改错文件"的坑（2026-10-03 已被此脚本搬回去过一次）。
    "webui_artistic_v3.html"
)

# 保留作业汇总表（要用它统计 Copied 数量），但去掉逐文件列表
$roboArgs = @("/E", "/NFL", "/NDL", "/NJH", "/NP", "/R:1", "/W:1")
if ($DryRun) { $roboArgs += "/L" }
foreach ($d in $excludeDirs)  { $roboArgs += @("/XD", $d) }
foreach ($f in $excludeFiles) { $roboArgs += @("/XF", $f) }

$result = [ordered]@{ files = 0; dirs = 0; failed = 0 }

function Invoke-Robo([string]$src, [string]$dst) {
    # robocopy 成功退出码为 0-7，>=8 才算失败
    $out = & robocopy $src $dst @roboArgs 2>&1
    $code = $LASTEXITCODE
    if ($code -ge 8) {
        Write-Host "  [失败] $src  (robocopy code=$code)" -ForegroundColor Red
        $out | Select-Object -Last 5 | ForEach-Object { Write-Host "         $_" -ForegroundColor DarkGray }
        $script:result.failed++
        return
    }
    # 解析汇总行：形如
    #   Files :       123        120         3         0         0         0
    #            Total    Copied   Skipped  Mismatch   FAILED    Extras
    # 取第 2 个数字（Copied）。若拿不到就跳过统计，不影响同步本身。
    $filesLine = $out | Where-Object { $_ -match '^\s*Files\s*:' } | Select-Object -First 1
    $dirsLine  = $out | Where-Object { $_ -match '^\s*Dirs\s*:' }  | Select-Object -First 1
    if ($filesLine) {
        $nums = @($filesLine -split '\s+' | Where-Object { $_ -match '^\d+$' })
        if ($nums.Count -ge 2) { $script:result.files += [int]$nums[1] }
    }
    if ($dirsLine) {
        $nums = @($dirsLine -split '\s+' | Where-Object { $_ -match '^\d+$' })
        if ($nums.Count -ge 2) { $script:result.dirs += [int]$nums[1] }
    }
}

# ── 1) 根目录文件 ─────────────────────────────────────────────────────
Write-Host "[根目录文件]" -ForegroundColor Green
$rootFiles = Get-ChildItem -Path $SourceDir -File -Force |
    Where-Object { $rootExts -contains $_.Extension.ToLower() -and $excludeFiles -notcontains $_.Name }
$rootChanged = 0
$rootSame = 0
foreach ($f in $rootFiles) {
    $dstFile = Join-Path $TargetDir $f.Name
    $exists = Test-Path $dstFile
    if ($exists) {
        $same = ((Get-FileHash $f.FullName -Algorithm MD5).Hash -eq (Get-FileHash $dstFile -Algorithm MD5).Hash)
        if ($same) { $rootSame++; continue }
    }
    $rootChanged++
    $tag = if ($exists) { "更新" } else { "新增" }
    if ($DryRun) {
        Write-Host ("  (将{0}) {1}" -f $tag, $f.Name) -ForegroundColor Yellow
    } else {
        Copy-Item -Path $f.FullName -Destination $TargetDir -Force
        Write-Host ("  ({0}) {1}" -f $tag, $f.Name)
    }
}
Write-Host ("  需处理 {0} 个，已一致 {1} 个（共 {2}）" -f $rootChanged, $rootSame, $rootFiles.Count)
Write-Host ""

# ── 2) 目录 ───────────────────────────────────────────────────────────
foreach ($item in $dirItems) {
    $srcPath = Join-Path $SourceDir $item
    if (-not (Test-Path $srcPath)) {
        Write-Host "  [跳过] $item （源码中不存在）" -ForegroundColor DarkGray
        continue
    }
    $dstPath = Join-Path $TargetDir $item
    Write-Host ("[{0}] -> {1}" -f $item, $dstPath) -ForegroundColor Green
    $beforeF = $result.files; $beforeD = $result.dirs
    Invoke-Robo $srcPath $dstPath
    Write-Host ("  {0}复制文件 {1} / 目录 {2}" -f $(if ($DryRun) { "将" } else { "已" }),
                ($result.files - $beforeF), ($result.dirs - $beforeD))
}
Write-Host ""

# ── 3) VERSION ────────────────────────────────────────────────────────
$commit = ""
try { $commit = (git -C $SourceDir rev-parse --short HEAD 2>$null) } catch { }
if (-not $commit) { $commit = "unknown" }
$now = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

$versionLines = @(
    "# EggBloom Desktop 发行版",
    "# 此文件标记当前发行版对应的源码版本",
    "# 由 scripts/sync-dist.ps1 自动更新",
    "",
    "source_repo=$SourceDir",
    "source_commit=$commit",
    "sync_date=$now"
)
$versionFile = Join-Path $DistPath "VERSION"

Write-Host "=== 汇总 ===" -ForegroundColor Cyan
Write-Host ("  根目录文件: 需处理 {0} / 已一致 {1}" -f $rootChanged, $rootSame)
Write-Host ("  目录内容  : {0}复制文件 {1} / 目录 {2}" -f $(if ($DryRun) { "将" } else { "已" }),
            $result.files, $result.dirs)
Write-Host ("  失败项    : {0}" -f $result.failed) -ForegroundColor $(if ($result.failed) { "Red" } else { "Gray" })

if ($DryRun) {
    Write-Host ""
    Write-Host "=== DRY RUN 结束：以上是「会发生什么」，未写入任何文件 ===" -ForegroundColor Yellow
} else {
    Set-Content -Path $versionFile -Value $versionLines -Encoding UTF8
    Write-Host ""
    Write-Host "=== 同步完成 ===" -ForegroundColor Cyan
    Write-Host "  VERSION: $versionFile  (commit=$commit)"
    Write-Host ""
    Write-Host "提示：本脚本只增不删。未使用 /MIR —— 目标端多出来的文件不会被删除。" -ForegroundColor DarkGray
}
if ($result.failed -gt 0) { exit 1 }
