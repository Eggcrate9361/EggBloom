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
# ★ .js 必须在列：main.js（Electron 主进程，frame:false / 关闭确认框）与 preload.js
#   都在根目录 —— 缺了它俩，源码里改的窗口外壳永远同步不到发行包
#   （2026-10-08 实测：确认框换皮后发行包 main.js 还停在 2026-09-05）。
$rootExts = @(".py", ".json", ".html", ".txt", ".md", ".toml", ".yaml", ".yml", ".png", ".js")

# 整目录复制（docs 也带上：发行副本里能直接看到权威文档，不必回源码库翻）
$dirItems = @("plugins", "protocols", "webui", "components", "memes", "docs", "harness", "skills",
              # README 的主视觉图在 assets/ 下；不同步的话包里的 README 就是一张破图
              "assets")
# 注：nexsandglass/ 已于 2026-10-19 移除（沙漏改走自研 sandglass_lite.py，在根目录、
#     由 $rootExts 的 .py 覆盖）。若将来从 git 历史恢复该目录，需把它加回本列表。

# 排除的运行时数据（任意层级同名目录）
$excludeDirs = @(
    "data", "logs", "chat_logs", "private_memory", "self_learning_db",
    # ★ 运行时会话状态：源码库里是被 git 忽略的运行态，带个人 QQ 号命名，
    #   robocopy 按目录整体搬 → 2026-10-18 差点把它打进公开包。
    "conversation_state",
    # ★ plugins/state：插件运行态（capabilities.json / qzone.json / scheduler.json）。
    #   **这是用户数据**：scheduler.json 里可能是用户自己配的定时任务。
    #   2026-10-22 修：此前它没被排除，于是每次同步都「把源码库的测试态复制过去，
    #   再在收尾删掉目标端整个目录」—— 既做了无用功，又会在用户真有任务时把它删掉。
    #   现在从源头不同步，收尾也不再删目标端（见文件末尾说明）。
    "state",
    "image_cache", "personality_versions", "__pycache__", ".git", "tests",
    "scripts", ".qoder", ".neurobase", "node_modules", ".ruff_cache",
    ".venv", "venv", "build", "dist"
)

# 排除的文件（防止覆盖用户状态 / 防止把重复副本又搬回去）
$excludeFiles = @(
    "last_port.txt", "bot_config.json", "plugin_state.json",
    "secret.json", "local_config.json", "machine_config.json",
    # ★ chart.min.js：205KB、**全项目零引用**（项目明令不引图表库），
    #   它只存在于旧发行树里，别继续搬进新包。
    "chart.min.js",
    # ★ 单文件版是 scripts/embed_fonts.py 的产物（1.2MB，git 忽略）——
    #   不进发行包，用户按需自己生成。
    "webui_v4_standalone.html",
    # ★ README 本地预览产物（render_readme.py 生成，.gitignore 忽略）。
    #   2026-10-22 发现：$rootExts 含 .html/.png，这两个每次都被同步进暂存树，
    #   然后被 pack_all.py 的 stale 审计判成「暂存树有、源码库没有」→ **拒绝出包**。
    #   它们是本机看 README 效果的中间物，不该进包。
    "README_preview.html", "README_preview_full.png",
    # ★ webui_artistic_v3.html **不再排除**（2026-10-18 修）。
    #   此前按"文件名"排除（robocopy /XF 对任意层级同名文件生效），结果
    #   webui\ 下的 v3 也同步不过去 → 暂存树里一直躺着 8 月 16 日那份，
    #   里面带着真实 QQ 号与昵称，打包时被原样带进 zip（2026-10-18 才发现）。
    #   根目录重复副本的问题改由打包前的「陈旧文件审计」兜底。
    # ★ ui_version.txt 是**本机的前端版本选择**（v3/v4）。
    #   它一旦被同步到发行包，就会把发行版钉死在某一套外观上
    #   （2026-10-08：v4 已完工、默认切到 v4，但旧包里的 v3 标记会覆盖默认值）。
    #   正确做法：发行包**不带**这个文件，让后端用 UI_DEFAULT_VERSION。
    "ui_version.txt",
    # ★ 黑话词典：这是**运行时数据**（由 slang_learner 在运行中写入），
    #   源码库里那份只是空壳（{"entries":{}}）。
    #   2026-10-03 事故：根目录 .json 在白名单里，同步时空壳把发行包里的
    #   105 条真实黑话覆盖成 21 字节 —— 学习成果被一次同步抹掉。
    #   凡"运行时会增长"的根目录 json，一律不得同步（只增不减 ≠ 不覆盖同名）。
    "slang_dict.json", "slang_replies.json",
    # ★ 调试日志不入库也不同步进发行包（2026-10 反复出现：webui/meme-debug.log、
    #   model-debug.log 被 git 跟踪或留在暂存树 → 包里带调试痕迹）。运行时自会再生成。
    "*.log",
    # ★ 审计脚本的产物（scripts/audit_fonts_v4.py / audit_page_parity.py 生成在项目根）。
    #   它们是"跑审计时的中间物"，不是发行内容。原先靠收尾阶段 Remove-Item 补删 ——
    #   但那要先把它们复制进包再删掉，多一次无谓写入，且 DryRun 时看着像"要新增"。
    #   2026-10-22 直接在这里排除，从源头不同步。
    "_font_audit.png", "_parity_report.json"
)

# 运行时数据目录（根目录下），整目录不同步
$excludeRootDirs = @("data", "logs", "chat_logs", ".neurobase", "memory")

# 保留作业汇总表（要用它统计 Copied 数量），但去掉逐文件列表
$roboArgs = @("/E", "/NFL", "/NDL", "/NJH", "/NP", "/R:1", "/W:1")
if ($DryRun) { $roboArgs += "/L" }
foreach ($d in $excludeDirs)  { $roboArgs += @("/XD", $d) }
foreach ($f in $excludeFiles) { $roboArgs += @("/XF", $f) }
# ★ 运行时数据：黑话词典可能出现在任意层级（历史上分裂成 plugins/ego/ 与 app 根），
#   一律按**文件名**排除，避免它再次被空壳覆盖。
foreach ($f in @("slang_dict.json", "slang_replies.json", "slang_conflicts.json")) {
    $roboArgs += @("/XF", $f)
}

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
    # 解析汇总行。robocopy 的**输出语言跟随系统**，这里两种都要认：
    #   英文:  Files :  123  120  3  0  0  0   /  Dirs :  6  0  6  0  0  1
    #   中文:  文件:   123  120  3  0  0  0   /  目录:  6  0  6  0  0  1
    # ★ 2026-10-22 修：原来只匹配英文 `Files :` / `Dirs :`，而本机 robocopy 输出中文，
    #   于是统计**恒为 0** —— 干了活却报「复制文件 0」，看日志的人会以为同步没生效
    #   （实测 webui 明明有 3 个文件要复制，脚本报 0）。这属于"输出骗人"，比报错更糟。
    # 取第 2 个数字（Copied）。若拿不到就跳过统计，不影响同步本身。
    $filesLine = $out | Where-Object { $_ -match '^\s*(Files|文件)\s*[:：]' } | Select-Object -First 1
    $dirsLine  = $out | Where-Object { $_ -match '^\s*(Dirs|目录)\s*[:：]' }  | Select-Object -First 1
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

# ★ 发行版本号来源：package.json 的 version（唯一真相源）。
#   写进 VERSION 的 release= 行 —— 否则每次同步都会把这个信息冲掉。
$release = "unknown"
try {
    $pkg = Get-Content (Join-Path $SourceDir "package.json") -Raw | ConvertFrom-Json
    if ($pkg.version) { $release = $pkg.version }
} catch { }

$versionLines = @(
    "# EggBloom Desktop 发行版",
    "# 此文件标记当前发行版对应的源码版本",
    "# 由 scripts/sync-dist.ps1 自动更新",
    "",
    "release=$release",
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
    # ★ LICENSE 没有扩展名，$rootExts 那一轮匹配不到它 —— 但 MIT 要求
    #   "随所有副本分发"，所以必须显式拷一次。
    #   （2026-10-18 发现 Standard 包里只有 nexsandglass/LICENSE，没有根 LICENSE）
    $lic = Join-Path $SourceDir "LICENSE"
    if (Test-Path $lic) {
        Copy-Item -LiteralPath $lic -Destination (Join-Path $TargetDir "LICENSE") -Force
        Write-Host "  + LICENSE（根许可，显式拷贝）"
    }

    # ── 收尾：不再删除目标端的任何运行态目录 ─────────────────────────────
    #   2026-10-22 之前这里会 Remove-Item 掉 $TargetDir\plugins\state 与
    #   \plugins\conversation_state。这两个都是**用户数据**：
    #     · scheduler.json = 用户自己配的定时任务
    #     · conversation_state = 会话状态
    #   同步一次就把它们删掉，等于用户配好的东西无声消失。
    #
    #   真正该做的是**从源头不同步**，已经做了：
    #     · plugins/state → $excludeDirs 里的 "state"
    #     · conversation_state → $excludeDirs 里本来就有
    #     · _font_audit.png / _parity_report.json → $excludeFiles
    #   所以这里无事可做。保留段注释而不留代码，免得后来人以为"这里本该删点东西"。

    Write-Host "=== 同步完成 ===" -ForegroundColor Cyan
    Write-Host "  VERSION: $versionFile  (commit=$commit)"
    Write-Host ""
    Write-Host "提示：本脚本只增不删。未使用 /MIR —— 目标端多出来的文件不会被删除。" -ForegroundColor DarkGray
}
if ($result.failed -gt 0) { exit 1 }
