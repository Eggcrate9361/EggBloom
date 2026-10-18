# -*- coding: utf-8 -*-
"""
EggBloom 数据目录管理

提供统一的数据目录访问，支持：
1. Electron 打包环境：使用 app.getPath('userData') (通过环境变量传递)
2. Python 独立运行：使用 ~/.local/share/EggBloom (Linux) / %LOCALAPPDATA%/EggBloom (Windows)
3. 开发模式：项目根目录下的 data/ 目录
"""
import os
import sys
import json
from pathlib import Path


def _get_electron_userdata() -> Path | None:
    """从环境变量获取 Electron 传递的 userData 路径"""
    # Electron main.js 可通过 env 传递：EGG_BLOOM_USER_DATA
    env_path = os.environ.get("EGG_BLOOM_USER_DATA")
    if env_path:
        p = Path(env_path)
        if p.exists() or p.parent.exists():
            return p
    return None


def _get_platform_data_dir() -> Path:
    """获取平台标准数据目录"""
    if sys.platform == "win32":
        # Windows: %LOCALAPPDATA%/EggBloom
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/AppData/Local")
        return Path(base) / "EggBloom"
    elif sys.platform == "darwin":
        # macOS: ~/Library/Application Support/EggBloom
        return Path.home() / "Library" / "Application Support" / "EggBloom"
    else:
        # Linux/Unix: ~/.local/share/EggBloom (XDG_DATA_HOME)
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        return Path(base) / "EggBloom"


def _get_dev_data_dir() -> Path | None:
    """开发模式：项目根目录下的 data/"""
    # 通过环境变量或 __file__ 推断
    if getattr(sys, 'frozen', False):
        # 打包后：exe 同级目录
        return Path(sys.executable).parent / "data"
    else:
        # 源码运行：从 __file__ 向上查找包含 bot_core.py 的目录
        cur = Path(__file__).resolve()
        for _ in range(6):
            if (cur / "bot_core.py").exists():
                return cur / "data"
            cur = cur.parent
    return None


def get_data_dir() -> Path:
    """
    获取统一数据目录，优先级：
    1. Electron 环境变量 EGG_BLOOM_USER_DATA
    2. 开发模式项目目录 data/
    3. 平台标准数据目录
    """
    # 1. Electron 传递的 userData
    electron_dir = _get_electron_userdata()
    if electron_dir:
        electron_dir.mkdir(parents=True, exist_ok=True)
        return electron_dir

    # 2. 开发模式
    dev_dir = _get_dev_data_dir()
    if dev_dir:
        dev_dir.mkdir(parents=True, exist_ok=True)
        return dev_dir

    # 3. 平台标准目录
    platform_dir = _get_platform_data_dir()
    platform_dir.mkdir(parents=True, exist_ok=True)
    return platform_dir


def get_config_dir() -> Path:
    """获取配置文件目录（同数据目录）"""
    return get_data_dir()


def get_log_dir() -> Path:
    """获取日志目录"""
    log_dir = get_data_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def get_cache_dir() -> Path:
    """获取缓存目录"""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/AppData/Local")
        cache_dir = Path(base) / "EggBloom" / "Cache"
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
        cache_dir = Path(base) / "EggBloom"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


# 向后兼容：原有的 _APPDATA_DIR
def get_legacy_appdata_dir() -> Path:
    """兼容旧版：%LOCALAPPDATA%/EggBloom_v3"""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/AppData/Local")
        return Path(base) / "EggBloom_v3"
    return get_data_dir()


# 配置文件路径（新版优先使用数据目录，兼容旧版）
CONFIG_FILE_NEW = get_config_dir() / "bot_config.json"
CONFIG_FILE_LEGACY = get_legacy_appdata_dir() / "bot_config.json"
ENV_FILE_NEW = get_config_dir() / ".env"
ENV_FILE_LEGACY = get_legacy_appdata_dir() / ".env"


def get_config_paths() -> tuple[Path, Path]:
    """返回 (新版配置路径, 旧版兼容配置路径)"""
    return CONFIG_FILE_NEW, CONFIG_FILE_LEGACY


def get_env_paths() -> tuple[Path, Path]:
    """返回 (新版 .env 路径, 旧版兼容 .env 路径)"""
    return ENV_FILE_NEW, ENV_FILE_LEGACY


# 导出供外部使用
__all__ = [
    "get_data_dir",
    "get_config_dir",
    "get_log_dir",
    "get_cache_dir",
    "get_legacy_appdata_dir",
    "CONFIG_FILE_NEW",
    "CONFIG_FILE_LEGACY",
    "ENV_FILE_NEW",
    "ENV_FILE_LEGACY",
    "get_config_paths",
    "get_env_paths",
    "read_json",
    "write_json",
]


# ── JSON 安全读写 ────────────────────────────────────────────────────
# 本模块原先只做「数据目录解析」（get_data_dir 等），但 bot_core.py 用
# data_store.read_json / write_json 存取状态与指令文件，而兜底类只捕获
# ImportError —— import 成功却没有 read_json，运行到 load_state() 就会
# AttributeError。两个函数原先只存在于发行/运行副本里的同名模块中，
# 导致源码库与运行副本各自只有一半：源码库 bot_core 跑不起来，运行副本
# utils.py / logger_setup.py 拿不到 get_log_dir。
# 此处合并为一个模块，两个功能并存，两边都能用。
def read_json(path, default=None):
    """安全读 JSON：文件缺失或解析失败返回 default"""
    if default is None:
        default = {}
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data, indent=2):
    """原子写 JSON：先写 tmp，再 os.replace（强制 UTF-8）"""
    tmp = str(path) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        os.replace(tmp, str(path))
        return True
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False