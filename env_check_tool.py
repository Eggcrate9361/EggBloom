# -*- coding: utf-8 -*-
"""
EggBloom Desktop 环境自检工具
用于检查 EggBloom 运行环境是否正常

运行方式：
  python env_check_tool.py

打包为 exe（需要安装 PyInstaller）：
  pyinstaller --onefile --console --name EggBloomEnvCheck --add-data "env_check_tool.py;." env_check_tool.py
  （实际打包时以主模块方式执行即可）
"""
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import datetime
from pathlib import Path
from typing import Optional, Tuple

# ---------- 安装指引 ----------
PYTHON_URL = 'https://www.python.org/downloads/'
NODE_URL = 'https://nodejs.org/zh-cn/download/'
NPM_MSG = 'npm 随 Node.js 一起安装；若需单独启用：在管理员 PowerShell 执行 `npm install -g npm@latest`'

FIX_ENABLED = '--fix' in sys.argv or '-fix' in sys.argv
if FIX_ENABLED:
    sys.argv = [a for a in sys.argv if a not in ('--fix', '-fix')]

# ---------- 需求版本 ----------
REQUIRED_PYTHON = (3, 10, 0)
REQUIRED_NODE = (18, 0, 0)


def find_system_python() -> Optional[str]:
    """定位运行应用的系统 python（打包后 exe 自身无 pip/第三方模块，必须探测真正可执行的 python）"""
    frozen_exe = Path(sys.executable).resolve() if getattr(sys, 'frozen', False) else None
    candidates = []
    # 1) PATH 中的 python（WindowsApps stub 也能真实执行）
    try:
        out = subprocess.run(['where', 'python'], capture_output=True, text=True, timeout=5)
        candidates.extend(p.strip() for p in out.stdout.splitlines() if p.strip())
    except Exception:
        pass
    # 2) 回退: sys.executable（非打包时正常）
    if not getattr(sys, 'frozen', False):
        candidates.append(sys.executable)
    # 验证候选：能跑 -c 'import sys'，且不是自身 exe
    for c in candidates:
        try:
            if frozen_exe and Path(c).resolve() == frozen_exe:
                continue
            out = subprocess.run([c, '-c', 'import sys;print("ok")'], capture_output=True, text=True, timeout=10)
            if out.returncode == 0:
                return c
        except Exception:
            continue
    return None

# ---------- 应用结构常量 ----------
# exe 打包后放在应用根目录（与 resources/ 同级）
APP_ROOT = Path(os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__)))
APP_SUB = Path('resources') / 'app'          # Electron 应用目录
WEBUI_REL = APP_SUB / 'webui' / 'webui_server.py'
HOME = Path.home()
REPORT_DIR = HOME / 'Desktop' / 'EggBloomEnvReports'

# 需要检查的第三方依赖（对应 requirements.txt）
REQUIRED_MODULES = [
    ('psutil', 'psutil>=5.9.0'),
    ('requests', 'requests>=2.28.0'),
    ('pystray', 'pystray>=0.19.5'),
    ('PIL', 'Pillow>=9.0.0'),
]

# ---------- 小工具 ----------
def parse_version(ver: str):
    """'3.13.14' -> (3,13,14)"""
    return tuple(int(x) for x in ver.strip('vV').split('.'))


def version_ok(actual: Tuple[int, ...], required: Tuple[int, ...]) -> bool:
    return actual >= required


def status_mark(ok: bool) -> str:
    return '[OK]' if ok else '[FAIL]'


# ---------- 检查项 ----------
def check_python() -> Tuple[bool, str]:
    v = sys.version_info
    detail = f"{v.major}.{v.minor}.{v.micro}"
    ok = version_ok((v.major, v.minor, v.micro), REQUIRED_PYTHON)
    return ok, detail


def check_pip() -> Tuple[bool, str]:
    """用运行应用的 python 检查 pip（打包后避免用自身 exe 误报）"""
    py = find_system_python()
    if not py:
        return False, '未找到系统 python'
    try:
        out = subprocess.run(
            [py, '-m', 'pip', '--version'],
            capture_output=True, text=True, timeout=20,
        )
        if out.returncode == 0:
            return True, out.stdout.strip().split(' ')[1]
        return False, (out.stderr or 'pip 不可用').strip()
    except Exception as e:
        return False, str(e)


def check_node_npm() -> Tuple[bool, str]:
    node_ok, node_ver = False, ''
    npm_ok, npm_ver = False, ''
    node_cmd = 'node'
    npm_cmd = 'npm'
    # Windows 控制台无 PATH 查找 .ps1，需显式用 .cmd
    if os.name == 'nt':
        node_dir = shutil.which('node')
        if node_dir:
            cur = Path(node_dir).parent
            npm_cmd = str(cur / 'npm.cmd')
        npm_cmd = shutil.which('npm.cmd') or shutil.which('npm') or npm_cmd
    try:
        nv = subprocess.run([node_cmd, '--version'], capture_output=True, text=True, timeout=15)
        if nv.returncode == 0:
            node_ok = True
            node_ver = nv.stdout.strip()
    except Exception:
        pass
    try:
        nv = subprocess.run([npm_cmd, '--version'], capture_output=True, text=True, timeout=15)
        if nv.returncode == 0:
            npm_ok = True
            npm_ver = nv.stdout.strip()
    except Exception:
        pass
    if node_ok and npm_ok:
        return True, f'node {node_ver}, npm {npm_ver}'
    return False, f'node {node_ver or "not found"}, npm {npm_ver or "not found"}'


def find_missing_modules() -> list:
    """返回缺失的依赖模块名列表"""
    py = find_system_python()
    if not py:
        return [m for m, _ in REQUIRED_MODULES]
    missing = []
    for mod, _ in REQUIRED_MODULES:
        try:
            code = f"import importlib.util;s=importlib.util.find_spec('{mod}');print('ok' if s else 'NO')"
            out = subprocess.run([py, '-c', code], capture_output=True, text=True, timeout=30)
            if out.returncode != 0 or out.stdout.strip() != 'ok':
                missing.append(mod)
        except Exception:
            missing.append(mod)
    return missing


def check_python_modules() -> Tuple[bool, str]:
    """用运行应用的 Python 实测能否导入关键依赖"""
    missing = find_missing_modules()
    if not missing:
        return True, f'核心依赖可用 ({len(REQUIRED_MODULES)} 个模块)'
    return False, '缺少模块: ' + ', '.join(missing)


def prompt_yes_no(prompt: str) -> bool:
    """交互式确认 y/n"""
    try:
        while True:
            ans = input(prompt + ' (y/n): ').strip().lower()
            if ans in ('y', 'yes'):
                return True
            if ans in ('n', 'no'):
                return False
    except (EOFError, KeyboardInterrupt):
        return False


def install_missing_modules(missing: list) -> Tuple[bool, str]:
    """用系统 python 的 pip 自动安装缺失依赖（需用户确认）"""
    py = find_system_python()
    if not py:
        return False, '未找到 python 解释器，无法自动安装'
    pkgs = [spec for mod, spec in REQUIRED_MODULES if mod in missing]
    if not pkgs:
        return True, '无需安装'
    print()
    print('  检测到缺失依赖，准备自动安装：')
    for p in pkgs:
        print(f'    - {p}')
    print(f'  将执行: {py} -m pip install ' + ' '.join(pkgs))
    if not prompt_yes_no('  是否继续安装？'):
        return False, '用户取消安装'
    try:
        out = subprocess.run(
            [py, '-m', 'pip', 'install'] + pkgs,
            capture_output=True, text=True, timeout=300,
        )
        if out.returncode == 0:
            return True, '依赖安装成功'
        tail = (out.stderr or out.stdout or '').strip().splitlines()
        return False, '安装失败: ' + (tail[-1][:200] if tail else '未知错误')
    except Exception as e:
        return False, f'安装异常: {e}'


def check_port(port: int, host: str = '127.0.0.1') -> Tuple[bool, Optional[str]]:
    """端口是否被占用。返回 (是否占用, 占用 PID)"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            result = s.connect_ex((host, port))
            if result != 0:
                return False, None
    except Exception:
        return False, None
    # 端口被占用，尝试取 PID
    try:
        out = subprocess.run(['netstat', '-ano'], capture_output=True, text=True, timeout=10)
        for line in out.stdout.splitlines():
            if f':{port}' in line and 'LISTENING' in line:
                parts = line.split()
                if len(parts) >= 5:
                    try:
                        return True, str(int(parts[-1]))
                    except ValueError:
                        return True, None
        return True, None
    except Exception:
        return True, None



def check_file_exists(path: Path, label: str) -> Tuple[bool, str]:
    if path.exists():
        size = path.stat().st_size
        return True, f'{label} ({size} bytes)'
    return False, f'{label} 缺失: {path}'


def find_project_root() -> Optional[Path]:
    """定位项目根目录（Electron 应用目录 resources/app）"""
    candidates = []
    # 1) exe 同目录/resources/app（打包后）
    if APP_ROOT.name.lower() == 'app':
        candidates.append(APP_ROOT.parent.parent)  # .../resources/app 的上一级上一级
        candidates.append(APP_ROOT.parent.parent / 'resources' / 'app')
    candidates.append(APP_ROOT / APP_SUB)          # <exe>/resources/app
    candidates.append(APP_ROOT)                    # <exe> 本身
    # 2) 当前工作目录
    candidates.append(Path.cwd())
    # 3) __file__ 上溯
    if not getattr(sys, 'frozen', False):
        candidates.append(Path(__file__).resolve().parent.parent)
        candidates.append(Path(__file__).resolve().parent.parent.parent)

    seen = set()
    for c in candidates:
        try:
            c = c.resolve()
        except Exception:
            continue
        if c in seen:
            continue
        seen.add(c)
        # 匹配 criteria: <c>/webui_server.py 或 <c>/webui/webui_server.py
        if (c / 'webui_server.py').exists() or (c / WEBUI_REL).exists():
            return c
    # 最后：从 cwd 向上搜索
    cur = Path.cwd()
    for _ in range(8):
        if (cur / 'webui_server.py').exists() or (cur / 'resources' / 'app' / 'webui' / 'webui_server.py').exists():
            return cur
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return None


def check_project_files(project_root: Optional[Path]) -> Tuple[bool, str]:
    if not project_root:
        return False, '无法定位项目根目录（缺少 webui_server.py）'
    app_dir = project_root if (project_root / 'bot_config.json').exists() else project_root / 'resources' / 'app'
    if not (app_dir / 'bot_config.json').exists():
        app_dir = project_root / 'resources' / 'app'

    checks = []
    # 核心入口
    webui = project_root / 'webui_server.py' if (project_root / 'webui_server.py').exists() else app_dir / 'webui' / 'webui_server.py'
    checks.append(check_file_exists(webui, 'webui_server.py'))
    # 内置插件 ego
    checks.append(check_file_exists(app_dir / 'plugins' / 'ego' / '__init__.py', 'plugins/ego/__init__.py'))

    ok = True
    failed = []
    for is_ok, msg in checks:
        if not is_ok:
            ok = False
            failed.append(msg)
    if not ok:
        return False, '; '.join(failed)
    return True, '核心文件齐全'


def check_config_json(project_root: Optional[Path]) -> Tuple[bool, str]:
    if not project_root:
        return False, '无项目根目录'
    app_dir = project_root if (project_root / 'bot_config.json').exists() else project_root / 'resources' / 'app'
    cfg_path = app_dir / 'bot_config.json'
    if not cfg_path.exists():
        return False, 'bot_config.json 不存在'
    try:
        data = json.loads(cfg_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as e:
        return False, f'bot_config.json JSON 格式错误: {e}'
    except Exception as e:
        return False, f'bot_config.json 读取失败: {e}'
    required = ['onebot_url', 'bot_qq', 'api_provider', 'api_url', 'model_name']
    missing = [k for k in required if k not in data]
    if missing:
        return False, 'bot_config.json 缺少字段: ' + ', '.join(missing)
    return True, f'bot_config.json 正常 (keys: {len(data)})'


def check_plugin_state(project_root: Optional[Path]) -> Tuple[bool, str]:
    if not project_root:
        return False, '无项目根目录'
    app_dir = project_root if (project_root / 'plugins').exists() else project_root / 'resources' / 'app'
    state_path = app_dir / 'plugins' / 'plugin_state.json'
    if not state_path.exists():
        return True, 'plugin_state.json 不存在（首次运行会生成）'
    try:
        data = json.loads(state_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as e:
        return False, f'plugin_state.json JSON 错误: {e}'
    except Exception as e:
        return False, f'plugin_state.json 读取失败: {e}'
    if not isinstance(data, dict):
        return False, 'plugin_state.json 不是对象格式'
    return True, f'plugin_state.json 正常 (plugins: {len(data)})'


def check_api_endpoint(url: str, timeout: int = 5) -> Tuple[bool, str]:
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, f'HTTP {resp.status}'
    except urllib.error.HTTPError as e:
        return True, f'HTTP {e.code}'
    except Exception as e:
        return False, str(e)


def check_webui_api(port: int) -> Tuple[bool, str]:
    try:
        r = check_api_endpoint(f'http://127.0.0.1:{port}/api/status')
        if r[0]:
            return True, f'WebUI API 正常 ({r[1]})'
        return False, f'WebUI API 不可用: {r[1]}'
    except Exception as e:
        return False, f'WebUI API 检查失败: {e}'


def check_disk_space(path: Path, warn_gb: float = 1.0) -> Tuple[bool, str]:
    """检查应用所在盘剩余空间"""
    try:
        usage = shutil.disk_usage(path)
        free_gb = usage.free / (1024 ** 3)
        if free_gb >= warn_gb:
            return True, f'{free_gb:.1f} GB 可用 (盘: {usage.total / (1024**3):.0f} GB)'
        return False, f'磁盘空间不足，仅剩 {free_gb:.1f} GB'
    except Exception as e:
        return False, f'磁盘检查失败: {e}'


def get_process_list():
    procs = []
    names = ('QQ.exe', 'node', 'python')
    try:
        out = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True, text=True, timeout=10)
        for line in out.stdout.splitlines():
            if any(k in line for k in names):
                parts = line.strip('"').split('","')
                if len(parts) >= 3:
                    procs.append({'name': parts[0].strip('"'), 'pid': parts[1].strip('"'), 'mem': parts[2].strip('"')})
    except Exception:
        pass
    # 用 psutil 补充真实内存占用
    try:
        import psutil
        for p in procs:
            try:
                proc = psutil.Process(int(p['pid']))
                p['mem'] = f'{proc.memory_info().rss / 1024 / 1024:.0f} MB'
            except Exception:
                pass
    except ImportError:
        pass
    return procs


def ensure_report_dir() -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    return REPORT_DIR


def write_report(lines) -> Path:
    report_dir = ensure_report_dir()
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    report_path = report_dir / f'env_check_{stamp}.txt'
    report_path.write_text('\n'.join(lines), encoding='utf-8')
    return report_path


# ---------- 主流程 ----------
def main() -> int:
    project_root = find_project_root()
    lines = []

    def log(msg: str) -> None:
        lines.append(msg)
        print(msg)

    log('============================================================')
    log('EggBloom Desktop 环境自检工具')
    log('============================================================')
    log('')

    # 系统信息
    log('[系统信息]')
    log(f'  操作系统: {platform.system()} {platform.release()} ({platform.version()})')
    log(f'  机器架构: {platform.machine()}')
    log(f'  主机名: {platform.node()}')
    log('')

    # Python
    log('[Python 环境]')
    py_ok, py_detail = check_python()
    log(f'  {status_mark(py_ok)} Python: {py_detail}')
    if not py_ok:
        log(f'      要求: Python {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}+')
        log(f'      下载: {PYTHON_URL}')
    pip_ok, pip_detail = check_pip()
    log(f'  {status_mark(pip_ok)} pip: {pip_detail}')
    if not pip_ok and py_ok:
        log('      处理: 执行  python -m ensurepip --upgrade 或 pip install -U pip')
    mod_ok, mod_detail = check_python_modules()
    log(f'  {status_mark(mod_ok)} 依赖模块: {mod_detail}')
    if not mod_ok:
        missing = find_missing_modules()
        if FIX_ENABLED:
            log('      检测到缺失依赖，运行 --fix 自动安装流程：')
            ok_fix, fix_detail = install_missing_modules(missing)
            log(f'      {status_mark(ok_fix)} 依赖安装: {fix_detail}')
            if ok_fix:
                mod_ok, mod_detail = check_python_modules()
                log(f'  {status_mark(mod_ok)} 依赖模块(复查): {mod_detail}')
        else:
            log('      处理: 运行  EggBloomEnvCheck.exe --fix  将自动通过 pip 安装缺失依赖')
            log('      或手动: pip install psutil requests pystray Pillow')
    log('')

    # Node
    log('[Node.js 环境]')
    node_ok, node_detail = check_node_npm()
    log(f'  {status_mark(node_ok)} Node.js: {node_detail}')
    if not node_ok:
        log(f'      要求: Node.js {REQUIRED_NODE[0]}.{REQUIRED_NODE[1]}.{REQUIRED_NODE[2]}+, npm')
        log(f'      下载: {NODE_URL}')
        if 'npm' in node_detail and 'not found' in node_detail:
            log(f'      说明: {NPM_MSG}')
    log('')

    # 项目文件
    log('[项目文件]')
    files_ok, files_detail = check_project_files(project_root)
    log(f'  {status_mark(files_ok)} 核心文件: {files_detail}')
    log(f'    项目根目录: {project_root if project_root else "未知"}')
    log('')

    # 配置文件
    log('[配置文件]')
    cfg_ok, cfg_detail = check_config_json(project_root)
    log(f'  {status_mark(cfg_ok)} bot_config.json: {cfg_detail}')
    state_ok, state_detail = check_plugin_state(project_root)
    log(f'  {status_mark(state_ok)} plugin_state.json: {state_detail}')
    log('')

    # 端口占用
    log('[端口占用]')
    ports = {
        8080: 'WebUI 后端',
        3000: 'OneBot HTTP API',
        3005: 'OneBot 事件转发',
    }
    for port, name in ports.items():
        ok, pid = check_port(port)
        if ok:
            pid_str = f'(PID: {pid})' if pid else '(PID: 未知)'
            log(f'  [BUSY] {port}: {name} 已被占用 {pid_str}')
        else:
            log(f'  [FREE] {port}: {name} 未占用')
    log('')

    # 系统配置
    log('[系统配置]')
    disk_ok, disk_detail = check_disk_space(APP_ROOT)
    log(f'  {status_mark(disk_ok)} 磁盘空间: {disk_detail}')
    log('')

    # 相关进程
    log('[相关进程]')
    procs = get_process_list()
    if procs:
        for p in procs:
            log(f'  - {p["name"]} (PID: {p["pid"]}) 内存: {p["mem"]}')
    else:
        log('  [FREE] 无 EggBloom / QQ / Node / Python 相关进程在运行')
    log('')

    # API 就绪检查
    log('[API 就绪检查]')
    webui_ok, _ = check_port(8080)
    if webui_ok:
        ok, detail = check_webui_api(8080)
        log(f'  {status_mark(ok)} WebUI API: {detail}')
    else:
        log('  [SKIP] WebUI 未启动（端口 8080 未监听）')

    log('')
    log('============================================================')
    log('检查完成')
    log('============================================================')
    log('')

    # 建议
    log('[建议]')
    if not webui_ok:
        log('  环境空闲，可以启动服务')
        log('  启动命令: cd <项目目录> && python resources/app/webui/webui_server.py')
    else:
        log('  WebUI 已启动，检查上方端口占用情况')

    log('')
    report_path = write_report(lines)
    log('')
    log(f'[报告] 已保存到: {report_path}')
    return 0


if __name__ == '__main__':
    _crashed = False
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        _crashed = True
        import traceback as _tb
        print()
        print('============================================================')
        print('[CRASH] 环境自检工具发生异常，完整错误如下：')
        print('============================================================')
        _tb.print_exc()
        print('============================================================')
    finally:
        if _crashed:
            try:
                input('按回车键退出...')
            except Exception:
                pass
