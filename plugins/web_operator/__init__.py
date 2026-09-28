# -*- coding: utf-8 -*-
"""
Web Operator / 全能操作插件
======================
统一封装：网页自动化、CLI、文件系统、软件控制、系统能力、任务编排。
前端 UI：plugins/web_operator/ui.html
后端 Action：/api/plugins/action -> plugin_id="web_operator"
"""

import sys
import os
import json
import time
import ctypes
import threading
import base64
import mimetypes
import queue as _queue
from ctypes import wintypes
from pathlib import Path

_WEB_SESSION = None
_WEB_SESSION_KEY = None
_WEB_QUEUE = None
_WEB_WORKER = None
_WEB_WORKER_LOCK = threading.Lock()


def _start_web_worker():
    """Playwright sync 实例不是线程安全的：浏览器必须始终在同一个线程里使用。
    这里用一个专用 worker 线程 + 队列串行执行所有 web.* 任务，保证跨 HTTP 请求复用会话。"""
    global _WEB_WORKER, _WEB_QUEUE
    with _WEB_WORKER_LOCK:
        if _WEB_WORKER is not None and _WEB_WORKER.is_alive():
            return
        if _WEB_QUEUE is None:
            _WEB_QUEUE = _queue.Queue()
        t = threading.Thread(target=_web_worker_loop, name="web_operator_worker", daemon=True)
        t.start()
        _WEB_WORKER = t


def _web_worker_loop():
    while True:
        fn, box, ev = _WEB_QUEUE.get()
        try:
            box["result"] = fn()
        except BaseException as e:
            box["err"] = e
        finally:
            ev.set()


def _run_on_worker(fn, timeout=600):
    _start_web_worker()
    box = {}
    ev = threading.Event()
    _WEB_QUEUE.put((fn, box, ev))
    ev.wait(timeout)
    if "err" in box:
        raise box["err"]
    return box.get("result")


def _reset_web_session():
    def _do():
        global _WEB_SESSION, _WEB_SESSION_KEY
        if _WEB_SESSION is not None:
            try:
                _WEB_SESSION._close_browser()
            except Exception:
                pass
        _WEB_SESSION = None
        _WEB_SESSION_KEY = None
    try:
        _run_on_worker(_do, timeout=60)
    except Exception:
        pass

PLUGIN_DIR = Path(__file__).resolve().parent
DATA_DIR = PLUGIN_DIR / "data"
HISTORY_FILE = DATA_DIR / "history.json"
TASKS_FILE = DATA_DIR / "tasks.json"

PLUGIN_META = {
    "name": "web_operator",
    "version": "2.0.0",
    "description": "全能操作插件 v2：CDP 无障碍树结构化感知 + ref 精确定位 + 自愈 + 视觉兜底 + 自主 Agent。",
    "author": "EggBloom",
    "group_only": False,
    "enabled": True,
    "dependencies": [],
    "hooks": [],
    "priority": 50,
    "estimated_tokens": 0,
    "tags": ["工具", "网页自动化", "系统", "API可接入"],
    "category": "工具",
    "page": "plugins/web_operator/ui.html",
}


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[web_operator] save failed {path}: {e}")


def _system_blocked_paths():
    """系统级禁读写目录，全部由环境变量推导，不写死本机路径。"""
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or ""
    tempdir = os.environ.get("TEMP") or os.environ.get("TMP") or ""
    out = []
    if windir:
        out.extend([os.path.join(windir, "System32"), windir])
    if tempdir:
        out.append(tempdir)
    return out


DEFAULT_CONFIG = {
    "enabled": True,
    "default_timeout": 30,
    "default_retries": 1,
    "history_limit": 200,
    "max_file_size_mb": 10,
    "web": {
        "engine": "playwright",
        "channel": "",
        "headless": True,
        "auto_screenshot": True,
        "screenshot_on_error": True,
        "screenshot_on_step": False,
        "max_screenshot_bytes": 2 * 1024 * 1024,
        "snapshot_enabled": True,
        "self_heal": True,
        "self_heal_retries": 1,
        "vision_grounding": True,
        "action_cache": True,
        "storage_state": True,
        "stealth": True,
        "humanize": {
            "enabled": True,
            "delay_min_ms": 800,
            "delay_max_ms": 1800,
            "jitter_scroll": True,
        },
    },
    "autonomous": {
        "enabled": True,
        "max_steps": 25,
        "timeout_per_step": 30,
    },
    "cli": {
        "allow_any_command": False,
        "allowed_commands": ["git", "npm", "pip", "python", "node", "dir", "ls", "echo", "type", "powershell", "cmd"],
        "blocked_commands": [
            "rm -rf /",
            "format",
            "shutdown",
            "rd /s /q c:\\",
            "del /f /s /q c:\\",
            "rmdir /s /q c:\\",
        ],
        "default_shell": "powershell",
        "env": {},
    },
    "fs": {
        "allowed_paths": [str(Path.home()), str(PLUGIN_DIR.parent.parent)],
        "blocked_paths": _system_blocked_paths(),
        "max_file_size_mb": 10,
    },
    "app": {
        "allow_launch": True,
        "blocked_apps": [],
    },
    "vision_assist": {
        "enabled": False,
        "provider": "zhipu",
        "model": "glm-4.6v-flash",
        "api_key": "",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "fallback_only": True,
        "timeout": 30,
        "max_calls_per_task": 20,
        "thinking": True,
    },
    "security": {
        "require_confirm_for_dangerous": True,
        "log_everything": True,
    },
}


def get_config():
    cfg = _load_json(DATA_DIR / "config.json", {})
    out = dict(DEFAULT_CONFIG)
    _deep_update(out, cfg)
    # 系统级禁读写目录始终强制（随环境变量推导），防止旧配置把本机路径固化进来
    try:
        sys_blocked = _system_blocked_paths()
        cur = (out.get("fs") or {}).get("blocked_paths") or []
        merged = list(dict.fromkeys(sys_blocked + list(cur)))
        out.setdefault("fs", {})["blocked_paths"] = merged
    except Exception:
        pass
    # 允许目录始终含 用户主目录 + 插件项目根（动态推导），旧配置里的本机绝对路径不固化
    try:
        base_allowed = [str(Path.home()), str(PLUGIN_DIR.parent.parent)]
        cur_allowed = (out.get("fs") or {}).get("allowed_paths") or []
        merged_allowed = list(dict.fromkeys(base_allowed + list(cur_allowed)))
        out.setdefault("fs", {})["allowed_paths"] = merged_allowed
    except Exception:
        pass
    # 敏感信息不回显真实值；返回是否存在
    _mask_secrets(out)
    return out


def _mask_secrets(cfg):
    try:
        va = (cfg.get("vision_assist") or {})
        if not isinstance(va, dict):
            return
        has_secret = bool(_load_secret()) or bool((va.get("api_key") or "").strip())
        va["api_key"] = ""
        va["_has_api_key"] = has_secret
    except Exception:
        pass


CRED_TARGET = "EggBloom.WebOperator.APIKey"
CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2

class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", wintypes.LPVOID),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", wintypes.LPVOID),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _win32adv():
    if os.name != "nt":
        return None
    try:
        return ctypes.windll.advapi32
    except Exception:
        return None


def _cred_write(key_value):
    adv = _win32adv()
    if adv is None:
        return False
    blob = key_value.encode("utf-16-le")
    comment = "web_operator vision assist api key"
    class _CREDENTIALW_BODY(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", wintypes.LPVOID),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", wintypes.LPVOID),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]
    blob_buf = ctypes.create_string_buffer(blob)
    cred_struct = _CREDENTIALW(
        Flags=0,
        Type=CRED_TYPE_GENERIC,
        TargetName=ctypes.c_wchar_p(CRED_TARGET),
        Comment=ctypes.c_wchar_p(comment),
        CredentialBlobSize=len(blob),
        CredentialBlob=ctypes.cast(blob_buf, ctypes.c_void_p),
        Persist=CRED_PERSIST_LOCAL_MACHINE,
        AttributeCount=0,
        Attributes=None,
        TargetAlias=None,
        UserName=ctypes.c_wchar_p("EggBloom"),
    )
    ok = adv.CredWriteW(ctypes.byref(cred_struct), 0)
    return bool(ok)


def _cred_exists():
    adv = _win32adv()
    if adv is None:
        return False
    try:
        pcred = ctypes.POINTER(_CREDENTIALW)()
        ok = adv.CredReadW(ctypes.c_wchar_p(CRED_TARGET), CRED_TYPE_GENERIC, 0, ctypes.byref(pcred))
        if ok:
            adv.CredFree(pcred)
            return True
        return False
    except Exception:
        return False


def _cred_read():
    adv = _win32adv()
    if adv is None:
        return None
    try:
        pcred = ctypes.POINTER(_CREDENTIALW)()
        ok = adv.CredReadW(ctypes.c_wchar_p(CRED_TARGET), CRED_TYPE_GENERIC, 0, ctypes.byref(pcred))
        if not ok or not pcred:
            return None
        try:
            cred = pcred.contents
            size = int(cred.CredentialBlobSize or 0)
            if not size or not cred.CredentialBlob:
                return None
            raw = ctypes.string_at(cred.CredentialBlob, size)
            return raw.decode("utf-16-le", errors="ignore").strip()
        finally:
            adv.CredFree(pcred)
    except Exception:
        return None


def _cred_delete():
    adv = _win32adv()
    if adv is None:
        return False
    try:
        return bool(adv.CredDeleteW(ctypes.c_wchar_p(CRED_TARGET), CRED_TYPE_GENERIC, 0))
    except Exception:
        return False


def _migrate_legacy_secret():
    """若仍有旧版明文 secret.json，将其中的 Key 迁移进凭据管理器后删除文件。"""
    try:
        f = DATA_DIR / "secret.json"
        if not f.exists():
            return
        legacy = _load_json(f, {})
        key = (legacy.get("api_key") or "").strip()
        if key and not _cred_read():
            _cred_write(key)
        f.unlink(missing_ok=True)
    except Exception:
        pass


def _save_secret(key_value):
    """把 API Key 存入 Windows 凭据管理器（系统 DPAPI 加密），绝不落盘明文。"""
    key_value = (key_value or "").strip()
    if not key_value or "YOUR_" in key_value:
        return False
    try:
        return _cred_write(key_value)
    except Exception:
        return False


def _load_secret():
    """从凭据管理器读取 API Key（仅内存中使用，不会返回前端）。"""
    try:
        return _cred_read() or ""
    except Exception:
        return ""


def set_config(params):
    if not isinstance(params, dict):
        return {"ok": False, "error": "params must be dict"}
    # 剥离 key 存入 Windows 凭据管理器，避免真实 key 落进 config.json / 本地文件
    va_updates = params.get("vision_assist")
    if isinstance(va_updates, dict) and "api_key" in va_updates:
        saved = _save_secret(va_updates.pop("api_key"))
        if not saved:
            return {"ok": False, "error": "API Key 未能安全保存（Windows 凭据管理器不可用），已拒绝保存"}
        if not va_updates:
            params.pop("vision_assist", None)
    cfg = _load_json(DATA_DIR / "config.json", {})
    _deep_update(cfg, params)
    _save_json(DATA_DIR / "config.json", cfg)
    _reset_web_session()
    return {"ok": True, "config": get_config()}


# ---------------------------------------------------------------------------
# Dependency bootstrap（自动安装到主环境）
# ---------------------------------------------------------------------------

_REQUIRED_PACKAGES = {
    "playwright": "playwright",
    "pyperclip": "pyperclip",
    "winotify": "winotify",
}


def check_dependencies():
    cfg = get_config()
    result = {
        "ok": True,
        "missing": [],
        "installed": [],
        "playwright_chromium_ready": False,
        "python_executable": sys.executable,
    }
    for module_name, package_name in _REQUIRED_PACKAGES.items():
        try:
            __import__(module_name)
            result["installed"].append(package_name)
        except ImportError:
            result["missing"].append(package_name)
            result["ok"] = False
    # 依次尝试默认 Chromium / 系统 Edge，与环境实际可用引擎保持一致
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            last_err = None
            for kw in ({}, {"channel": "msedge"}):
                try:
                    browser = pw.chromium.launch(headless=True, **kw)
                    browser.close()
                    result["playwright_chromium_ready"] = True
                    break
                except Exception as e:
                    last_err = e
            result["launch_error"] = None if result["playwright_chromium_ready"] else str(last_err or "")
    except Exception as e:
        result["playwright_chromium_ready"] = False
        result["launch_error"] = str(e)
    return result


def install_dependencies():
    dep_status = check_dependencies()
    logs = []
    missing = dep_status.get("missing") or []
    if not missing and dep_status.get("playwright_chromium_ready"):
        return {"ok": True, "logs": [{"level": "success", "text": "依赖已齐全，无需安装"}], "dependencies": dep_status}

    logs.append({"level": "info", "text": f"需要安装: {', '.join(missing) or 'chromium'}"})
    install_failed = []
    for package_name in missing:
        logs.append({"level": "info", "text": f"正在安装 {package_name}..."})
        try:
            _run_pip(["install", package_name])
            logs.append({"level": "success", "text": f"{package_name} 安装完成"})
        except Exception as e:
            logs.append({"level": "error", "text": f"{package_name} 安装失败: {e}"})
            install_failed.append(package_name)

    # Playwright 特殊处理：下载 Chromium
    if not install_failed and "playwright" in missing or not dep_status.get("playwright_chromium_ready"):
        logs.append({"level": "info", "text": "正在下载 Chromium..."})
        try:
            _run_python(["-m", "playwright", "install", "chromium"])
            logs.append({"level": "success", "text": "Chromium 下载完成"})
        except Exception as e:
            logs.append({"level": "error", "text": f"Chromium 下载失败: {e}"})
            install_failed.append("playwright-chromium")

    final_status = check_dependencies()
    ok = not (final_status.get("missing") or install_failed)
    if ok:
        logs.append({"level": "success", "text": "依赖安装完成，建议重启插件后使用网页功能"})
    else:
        logs.append({"level": "warn", "text": "部分依赖安装失败，请手动安装后重试"})
    return {"ok": ok, "logs": logs, "dependencies": final_status, "failed": install_failed}


def _run_pip(args):
    import subprocess
    cmd = [sys.executable, "-m", "pip", *args]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:] or proc.stdout[-1000:])
    return proc


def _run_python(args):
    import subprocess
    cmd = [sys.executable, *args]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1000:] or proc.stdout[-1000:])
    return proc


def _deep_update(base, override):
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v


# ---------------------------------------------------------------------------
# History / tasks
# ---------------------------------------------------------------------------

def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def get_history(limit=50):
    items = _load_json(HISTORY_FILE, [])
    if not isinstance(items, list):
        items = []
    items = items[-limit:]
    return {"ok": True, "history": items}


def append_history(entry):
    items = _load_json(HISTORY_FILE, [])
    if not isinstance(items, list):
        items = []
    items.append(entry)
    cfg = get_config()
    limit = int(cfg.get("history_limit", 200) or 200)
    if len(items) > limit:
        items = items[-limit:]
    _save_json(HISTORY_FILE, items)
    return {"ok": True}


def clear_history():
    _save_json(HISTORY_FILE, [])
    return {"ok": True}


def get_tasks():
    items = _load_json(TASKS_FILE, {})
    if not isinstance(items, dict):
        items = {}
    return {"ok": True, "tasks": items}


def save_tasks(name, chain):
    items = _load_json(TASKS_FILE, {})
    if not isinstance(items, dict):
        items = {}
    items[name] = {
        "name": name,
        "chain": chain,
        "updated_at": _now(),
    }
    _save_json(TASKS_FILE, items)
    return {"ok": True}


def delete_task(name):
    items = _load_json(TASKS_FILE, {})
    if not isinstance(items, dict):
        items = {}
    items.pop(name, None)
    _save_json(TASKS_FILE, items)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def execute_task(task_type, params, context=None):
    context = context or {}
    cfg = get_config()
    if not cfg.get("enabled", True):
        return {"ok": False, "error": "plugin disabled", "logs": []}

    task = {
        "type": task_type,
        "params": params or {},
        "context": context,
        "config": cfg,
    }

    # Defer heavy imports to keep plugin load cheap
    try:
        from .operators.web import WebOperator
        from .operators.cli import CliOperator
        from .operators.fs import FsOperator
        from .operators.app import AppOperator
        from .operators.system import SystemOperator
    except Exception as e:
        return {"ok": False, "error": f"operator import failed: {e}", "logs": [{"level": "error", "text": str(e)}]}

    if task_type == "chain.run":
        from .runner import run_chain
        return run_chain(params or {}, cfg)

    if task_type == "vision.analyze":
        from .operators.vision import execute as _vision_execute
        return _vision_execute(params or {}, cfg)

    # web.* 任务复用同一个浏览器会话（避免 UI 多步操作反复重启浏览器）。
    # 所有 web.* 任务都在单一 worker 线程里串行执行，避免 Playwright 跨线程使用。
    if task_type.startswith("web."):
        def _run_web_task():
            global _WEB_SESSION, _WEB_SESSION_KEY
            if task_type == "web.close":
                if _WEB_SESSION is not None:
                    try:
                        _WEB_SESSION._close_browser()
                    except Exception:
                        pass
                _WEB_SESSION = None
                _WEB_SESSION_KEY = None
                return {"ok": True, "logs": [{"level": "info", "text": "浏览器会话已关闭"}]}
            key = json.dumps(cfg.get("web") or {}, ensure_ascii=False, sort_keys=True)
            if _WEB_SESSION is not None and _WEB_SESSION_KEY != key:
                try:
                    _WEB_SESSION._close_browser()
                except Exception:
                    pass
                _WEB_SESSION = None
            if _WEB_SESSION is None:
                _WEB_SESSION = WebOperator(cfg)
                _WEB_SESSION_KEY = key
            return _WEB_SESSION.execute(task, context)

        try:
            if task_type == "web.autonomous":
                auto_cfg = cfg.get("autonomous") or {}
                steps = int((params or {}).get("max_steps") or auto_cfg.get("max_steps", 25) or 25)
                per = int(auto_cfg.get("timeout_per_step", 30) or 30)
                worker_timeout = max(180, steps * (per + 15))
            else:
                worker_timeout = max(60, int(cfg.get("default_timeout", 30) or 30) * 4)
            return _run_on_worker(_run_web_task, timeout=worker_timeout)
        except Exception as e:
            return {"ok": False, "error": str(e), "logs": [{"level": "error", "text": str(e)}]}

    mapping = {
        "cli.run": CliOperator(cfg),
        "fs.read": FsOperator(cfg),
        "fs.write": FsOperator(cfg),
        "fs.append": FsOperator(cfg),
        "fs.list": FsOperator(cfg),
        "fs.move": FsOperator(cfg),
        "fs.copy": FsOperator(cfg),
        "fs.delete": FsOperator(cfg),
        "fs.search": FsOperator(cfg),
        "app.launch": AppOperator(cfg),
        "app.kill": AppOperator(cfg),
        "app.clipboard_read": AppOperator(cfg),
        "app.clipboard_write": AppOperator(cfg),
        "app.notify": AppOperator(cfg),
        "system.info": SystemOperator(cfg),
        "system.port_check": SystemOperator(cfg),
        "system.processes": SystemOperator(cfg),
        "system.open": SystemOperator(cfg),
        "chain.run": None,
    }

    if task_type == "chain.run":
        from .runner import run_chain
        return run_chain(params or {}, cfg)

    if task_type == "vision.analyze":
        from .operators.vision import execute as _vision_execute
        return _vision_execute(params or {}, cfg)

    op = mapping.get(task_type)
    if op is None:
        return {"ok": False, "error": f"unsupported task type: {task_type}", "logs": []}

    try:
        return op.execute(task, context)
    except Exception as e:
        return {"ok": False, "error": str(e), "logs": [{"level": "error", "text": str(e)}]}


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def on_load():
    print("[web_operator] 全能操作插件已启用")
    _migrate_legacy_secret()


def get_api_status():
    """连接监测：检测依赖与浏览器引擎是否就绪（结果由 plugin_manager 缓存）。"""
    try:
        dep = check_dependencies()
        ok = bool(dep.get("ok")) and bool(dep.get("playwright_chromium_ready"))
        if ok:
            return {"ok": True, "status": "依赖齐全", "detail": "playwright + chromium 就绪"}
        missing = dep.get("missing") or []
        detail = "缺依赖: " + ", ".join(missing) if missing else (dep.get("launch_error") or "chromium 不可用")
        return {"ok": False, "status": "未就绪", "detail": detail}
    except Exception as e:
        return {"ok": False, "status": f"检测异常: {e}", "detail": str(e)}


def on_unload():
    print("[web_operator] 已卸载")


PLUGIN_ACTIONS = {
    "get_config": lambda params: {"config": get_config()},
    "set_config": set_config,
    "toggle": lambda params: _wo_toggle(),
    "get_history": lambda params: get_history(int((params or {}).get("limit") or 50)),
    "clear_history": lambda params: clear_history(),
    "get_tasks": lambda params: get_tasks(),
    "save_tasks": lambda params: save_tasks((params or {}).get("name"), (params or {}).get("chain") or []),
    "delete_task": lambda params: delete_task((params or {}).get("name")),
    "check_dependencies": lambda params: {"dependencies": check_dependencies()},
    "install_dependencies": lambda params: install_dependencies(),
}


def _wo_toggle():
    cfg = get_config()
    enabled = not bool(cfg.get("enabled", True))
    set_config({"enabled": enabled})
    return {"enabled": enabled}


def _wo_execute(action, params):
    return execute_task(action, params or {})


__all__ = [
    "PLUGIN_META",
    "PLUGIN_ACTIONS",
    "on_load",
    "on_unload",
    "get_config",
    "set_config",
    "get_history",
    "append_history",
    "clear_history",
    "get_tasks",
    "save_tasks",
    "delete_task",
    "execute_task",
    "check_dependencies",
    "install_dependencies",
    "get_api_status",
]
