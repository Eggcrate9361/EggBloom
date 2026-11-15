<#>
.SYNOPSIS
    EggBloom Desktop 环境初始化与依赖安装脚本
    支持管理员权限自动提权（UAC），配置 hosts、安装 Python/Node 依赖、检查端口占用

.DESCRIPTION
    此脚本用于首次部署或环境修复，执行以下操作：
    1. 检测管理员权限，不足则通过 UAC 弹窗请求提权
    2. 写入 hosts 条目（防止 DNS 劫持/回环检测）
    3. 检查 Python 版本与 pip 可用性
    4. 安装 Python 依赖（从 pyproject.toml）
    5. 检查 Node.js 版本
    6. 检查关键端口占用情况
    7. 创建必要的目录结构
    8. 复制配置模板文件

.NOTES
    作者: EggBloom Team
    版本: 1.0.0
    要求: PowerShell 5.1+, Windows 10/11
#>

#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ──────────────────────────────────────────────────────────────────────────────
# UAC 自动提权：若非管理员则重新以管理员身份运行
# ──────────────────────────────────────────────────────────────────────────────
function Test-IsAdmin {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [System.Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdmin)) {
    Write-Host "[!] 需要管理员权限，正在请求 UAC 提权..." -ForegroundColor Yellow
    $scriptPath = $MyInvocation.MyCommand.Definition
    $params = $MyInvocation.BoundParameters.Keys.ForEach({ "-$_" + " " + $MyInvocation.BoundParameters[$_] }) -join ' '
    Start-Process powershell.exe -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" $params" -Verb RunAs
    exit
}

# ──────────────────────────────────────────────────────────────────────────────
# 全局配置
# ──────────────────────────────────────────────────────────────────────────────
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = $ScriptDir

$HostsPath = "$env:SystemRoot\System32\drivers\etc\hosts"
$HostsEntries = @(
    "127.0.0.1       localhost",
    "127.0.0.1       eggbloom.local",
    "::1             localhost"
)

$RequiredPythonVersion = [Version]"3.10.0"
$RequiredNodeVersion = [Version]"18.0.0"

$KeyPorts = @(
    @{ Port = 8080;  Name = "WebUI 管理面板" },
    @{ Port = 3000;  Name = "OneBot HTTP API (SnowLuma/LLOneBot)" },
    @{ Port = 3001;  Name = "OneBot WS 事件 (SnowLuma/LLOneBot)" },
    @{ Port = 3005;  Name = "bot_core 事件转发" },
    @{ Port = 3100;  Name = "NapCat HTTP API" },
    @{ Port = 3101;  Name = "NapCat WebSocket" },
    @{ Port = 5099;  Name = "SnowLuma WebUI" },
    @{ Port = 6099;  Name = "NapCat WebUI" },
    @{ Port = 7000;  Name = "LLOneBot WebUI" },
    @{ Port = 8081;  Name = "llama-server (本地 LLM)" },
    @{ Port = 8083;  Name = "llama-server Embedding" },
    @{ Port = 11434; Name = "Ollama API" },
    @{ Port = 1234;  Name = "LM Studio API" }
)

# ──────────────────────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────────────────────
function Write-Status {
    param([string]$Message, [string]$Color = 'White')
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] $Message" -ForegroundColor $Color
}

function Write-Success { param([string]$Msg) Write-Status "✔ $Msg" 'Green' }
function Write-Warn    { param([string]$Msg) Write-Status "⚠ $Msg" 'Yellow' }
function Write-ErrorMsg { param([string]$Msg) Write-Status "✖ $Msg" 'Red' }
function Write-Info    { param([string]$Msg) Write-Status "ℹ $Msg" 'Cyan' }

function Compare-Version {
    param([Version]$Actual, [Version]$Required, [string]$Label)
    if ($Actual -ge $Required) {
        Write-Success "$Label: v$Actual (满足要求 v$Required+)"
        return $true
    } else {
        Write-ErrorMsg "$Label: v$Actual (需要 v$Required+)"
        return $false
    }
}

function Check-Port {
    param([int]$Port, [string]$Name)
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
        $listener.Start()
        $listener.Stop()
        Write-Success "端口 $Port ($Name): 空闲"
        return $true
    } catch {
        # 尝试获取占用进程
        $proc = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
                Select-Object -First 1 -ExpandProperty OwningProcess
        if ($proc) {
            $pname = (Get-Process -Id $proc -ErrorAction SilentlyContinue).ProcessName
            Write-Warn "端口 $Port ($Name): 被占用 (PID: $proc, 进程: $pname)"
        } else {
            Write-Warn "端口 $Port ($Name): 被占用 (无法识别进程)"
        }
        return $false
    }
}

# ──────────────────────────────────────────────────────────────────────────────
# 1. 配置 hosts
# ──────────────────────────────────────────────────────────────────────────────
function Setup-Hosts {
    Write-Info "配置 hosts 文件..."
    $current = Get-Content $HostsPath -Raw -ErrorAction SilentlyContinue
    $modified = $false

    foreach ($entry in $HostsEntries) {
        $ip = $entry.Split()[0]
        $host = $entry.Split()[1]
        if ($current -notmatch [regex]::Escape("$ip`t$host") -and $current -notmatch [regex]::Escape("$ip $host")) {
            Add-Content -Path $HostsPath -Value "$ip`t$host" -Encoding utf8
            Write-Success "已添加: $ip  $host"
            $modified = $true
        }
    }

    if (-not $modified) {
        Write-Info "hosts 条目已存在，无需修改"
    }
}

# ──────────────────────────────────────────────────────────────────────────────
# 2. 检查 Python 环境
# ──────────────────────────────────────────────────────────────────────────────
function Check-Python {
    Write-Info "检查 Python 环境..."
    $pyPaths = @()

    # 查找 python.exe
    $where = & where.exe python 2>$null
    if ($where) { $pyPaths += $where.Split("`n") | Where-Object { $_ -match '\\.exe$' } }

    # 添加常见路径
    $pyPaths += @(
        "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
        "$env:ProgramFiles\Python3*\python.exe",
        "$env:ProgramFiles(x86)\Python3*\python.exe"
    ) | ForEach-Object { Resolve-Path $_ -ErrorAction SilentlyContinue } | Select-Object -Unique

    $validPython = $null
    foreach ($p in $pyPaths) {
        try {
            $ver = & $p -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
            $ver = $ver.Trim()
            if ($ver -and [Version]$ver -ge $RequiredPythonVersion) {
                $validPython = @{ Path = $p; Version = [Version]$ver }
                break
            }
        } catch { }
    }

    if (-not $validPython) {
        Write-ErrorMsg "未找到满足要求的 Python (>= $RequiredPythonVersion)"
        Write-Info "请从 https://www.python.org/downloads/ 下载安装"
        Write-Info "安装时请勾选 'Add Python to PATH'"
        return $false
    }

    Compare-Version $validPython.Version $RequiredPythonVersion "Python"

    # 检查 pip
    try {
        $pipVer = & $validPython.Path -m pip --version 2>$null
        if ($pipVer) {
            Write-Success "pip 可用: $($pipVer.Split(' ')[1])"
        } else {
            Write-Warn "pip 不可用，尝试修复..."
            & $validPython.Path -m ensurepip --upgrade 2>$null
            & $validPython.Path -m pip install --upgrade pip 2>$null
        }
    } catch {
        Write-Warn "pip 检查失败: $_"
    }

    # 导出供后续使用
    $script:PythonExe = $validPython.Path
    $script:PythonVersion = $validPython.Version
    return $true
}

# ──────────────────────────────────────────────────────────────────────────────
# 3. 安装 Python 依赖
# ──────────────────────────────────────────────────────────────────────────────
function Install-PythonDeps {
    Write-Info "安装 Python 依赖..."

    $pyproject = Join-Path $ProjectRoot "pyproject.toml"
    if (-not (Test-Path $pyproject)) {
        Write-ErrorMsg "未找到 pyproject.toml: $pyproject"
        return $false
    }

    # 解析 dependencies（简单提取，实际建议用 pip install -e .）
    Write-Info "使用 pip 安装依赖 (来自 pyproject.toml)..."
    $result = & $script:PythonExe -m pip install --upgrade pip setuptools wheel 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "pip 升级失败: $result"
    }

    # 直接安装项目（可编辑模式）
    $result = & $script:PythonExe -m pip install -e $ProjectRoot 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Python 依赖安装完成"
        return $true
    } else {
        Write-ErrorMsg "依赖安装失败: $result"
        return $false
    }
}

# ──────────────────────────────────────────────────────────────────────────────
# 4. 检查 Node.js
# ──────────────────────────────────────────────────────────────────────────────
function Check-Node {
    Write-Info "检查 Node.js 环境..."
    $node = Get-Command node.exe -ErrorAction SilentlyContinue
    $npm  = Get-Command npm.cmd -ErrorAction SilentlyContinue

    if (-not $node) {
        Write-ErrorMsg "未找到 node.exe"
        Write-Info "请从 https://nodejs.org/ 下载安装 (LTS 版本)"
        return $false
    }

    $nodeVer = & $node.Source --version
    $nodeVer = [Version]($nodeVer.Trim().TrimStart('v'))
    Compare-Version $nodeVer $RequiredNodeVersion "Node.js"

    if ($npm) {
        $npmVer = & $npm.Source --version
        Write-Success "npm: v$npmVer"
    } else {
        Write-Warn "npm 不可用"
    }

    return $true
}

# ──────────────────────────────────────────────────────────────────────────────
# 5. 检查端口占用
# ──────────────────────────────────────────────────────────────────────────────
function Check-Ports {
    Write-Info "检查关键端口占用情况..."
    $busy = 0
    foreach ($p in $KeyPorts) {
        if (-not (Check-Port $p.Port $p.Name)) {
            $busy++
        }
    }
    if ($busy -gt 0) {
        Write-Warn "共有 $busy 个关键端口被占用，可能影响服务启动"
        Write-Info "建议：关闭冲突程序，或在 WebUI 协议面板修改端口配置"
    } else {
        Write-Success "所有关键端口均空闲"
    }
}

# ──────────────────────────────────────────────────────────────────────────────
# 6. 创建目录结构与配置模板
# ──────────────────────────────────────────────────────────────────────────────
function Setup-DirectoriesAndConfigs {
    Write-Info "创建目录结构与配置模板..."

    $dirs = @(
        "chat_logs",
        "logs",
        "image_cache",
        "temp",
        "data",
        "personality",
        "personality_versions",
        "private_memory",
        "self_learning_db",
        "memes",
        "plugins\ego\super_memory\data",
        "plugins\ego\slang_learner\data",
        "plugins\web_operator\data",
        "plugins\group_chat_decider\data",
        "plugins\board_voice\data",
        "plugins\web_search\data"
    )

    foreach ($d in $dirs) {
        $full = Join-Path $ProjectRoot $d
        if (-not (Test-Path $full)) {
            New-Item -ItemType Directory -Path $full -Force | Out-Null
            Write-Success "创建目录: $d"
        }
    }

    # 复制配置模板
    $configExample = Join-Path $ProjectRoot "bot_config.json.example"
    $configTarget  = Join-Path $ProjectRoot "bot_config.json"
    $envExample    = Join-Path $ProjectRoot ".env.example"
    $envTarget     = Join-Path $ProjectRoot ".env"

    if (Test-Path $configExample -and -not (Test-Path $configTarget)) {
        Copy-Item $configExample $configTarget -Force
        Write-Success "创建配置文件: bot_config.json (请修改其中的占位符)"
    } elseif (Test-Path $configTarget) {
        Write-Info "bot_config.json 已存在，跳过"
    }

    if (Test-Path $envExample -and -not (Test-Path $envTarget)) {
        Copy-Item $envExample $envTarget -Force
        Write-Success "创建环境变量文件: .env (请填入 API Key)"
    } elseif (Test-Path $envTarget) {
        Write-Info ".env 已存在，跳过"
    }
}

# ──────────────────────────────────────────────────────────────────────────────
# 7. VC++ 运行时检查 (LiGGUF DLL 依赖)
# ──────────────────────────────────────────────────────────────────────────────
function Check-VCRedist {
    Write-Info "检查 VC++ 运行时 (LiGGUF DLL 依赖)..."
    $keys = @(
        "HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
    )
    $found = $false
    foreach ($k in $keys) {
        if (Test-Path $k) {
            $ver = Get-ItemProperty $k -Name "Version" -ErrorAction SilentlyContinue
            if ($ver) {
                Write-Success "检测到 VC++ 2015-2022 运行时: $($ver.Version)"
                $found = $true
                break
            }
        }
    }
    if (-not $found) {
        Write-Warn "未检测到 VC++ 2015-2022 运行时 (x64)"
        Write-Info "LiGGUF embedding DLL 需要此运行时，建议安装："
        Write-Info "  https://aka.ms/vs/17/release/vc_redist.x64.exe"
    }
}

# ──────────────────────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────────────────────
function Main {
    Write-Host "============================================================"
    Write-Host "  EggBloom Desktop 环境初始化脚本 (setup.ps1)"
    Write-Host "============================================================"
    Write-Host ""

    $allOk = $true

    # 1. hosts
    try { Setup-Hosts } catch { Write-ErrorMsg "hosts 配置失败: $_"; $allOk = $false }

    # 2. Python
    if (-not (Check-Python)) { $allOk = $false }
    else {
        if (-not (Install-PythonDeps)) { $allOk = $false }
    }

    # 3. Node.js
    if (-not (Check-Node)) { $allOk = $false }

    # 4. 端口
    Check-Ports

    # 5. 目录与配置
    try { Setup-DirectoriesAndConfigs } catch { Write-ErrorMsg "目录/配置创建失败: $_"; $allOk = $false }

    # 6. VC++ 运行时
    Check-VCRedist

    Write-Host ""
    Write-Host "============================================================"
    if ($allOk) {
        Write-Host "  ✔ 环境初始化完成！" -ForegroundColor Green
    } else {
        Write-Host "  ⚠ 环境初始化完成，但有部分项目需要注意（见上方警告）" -ForegroundColor Yellow
    }
    Write-Host "============================================================"
    Write-Host ""
    Write-Info "后续步骤："
    Write-Info "  1. 编辑 bot_config.json 填入你的 Bot QQ、群号等"
    Write-Info "  2. 编辑 .env 填入 API Key (或在 WebUI 设置页填写，写入凭据管理器)"
    Write-Info "  3. 启动协议驱动 (SnowLuma/NapCat/LLOneBot) 并登录 QQ"
    Write-Info "  4. 运行启动脚本: .\launch.py (或打包版 EggBloom Desktop.exe)"
    Write-Host ""
}

Main

# 保持窗口打开以便查看结果
if ($host.Name -eq 'ConsoleHost') {
    Write-Host "按任意键退出..." -NoNewline
    $null = $host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}