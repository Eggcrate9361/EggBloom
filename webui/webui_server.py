#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
EggBloom WebUI Server - Modern Management Dashboard
Design: LobeChat(glass sidebar) + Linear(dark premium) + Vercel(minimal gradients)
Tech: ThreadedHTTP + Single-file SPA (Chart.js + CSS Glassmorphism + Vanilla JS)

Start: python webui_server.py
Visit: http://localhost:8080
"""
import json, os, sys, time, threading, sqlite3, subprocess, socket
import datetime, re
from pathlib import Path
from collections import deque
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs, unquote
import urllib.request

# Windows console fix
if sys.stdout is not None and sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr is not None and sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")

# 统一日志轮转（stdout 由上层重定向到 webui_r*.log，此处再落一份 webui.log 供追溯）
try:
    import logger_setup
except ImportError:
    logger_setup = None

def _uni_log(msg):
    """走统一日志(轮转)，print 行为保持不变"""
    try:
        if logger_setup is not None:
            logger_setup.get_logger("webui").info(msg)
    except Exception:
        pass

# Paths
HERE = Path(__file__).parent
if getattr(sys, 'frozen', False):
    _APP_DIR = Path(sys.executable).resolve().parent
else:
    _APP_DIR = HERE.resolve()
_PROJECT_CANDIDATES = [
    _APP_DIR,
    _APP_DIR.parent,
    _APP_DIR.parent.parent,
    _APP_DIR.parent.parent.parent,
    _APP_DIR.parent.parent.parent.parent,
    Path.home() / "AppData/Local/EggBloom_v3",
]
PROJECT = None
for _c in _PROJECT_CANDIDATES:
    if (_c / "bot_config.json").exists() or (_c / "bot_core.py").exists():
        PROJECT = _c
        break
if PROJECT is None:
    PROJECT = _APP_DIR
sys.path.insert(0, str(PROJECT))
# 统一配置层：读程序目录 bot_config.json(优先) + AppData(补缺) + env + 凭据管理器
from config import load_config as load_config  # noqa: E402
import log_levels  # noqa: E402  # 与终端着色同一套分级规则


# 静态文件服务允许的扩展名（杜绝 .log/.py/.db/.env 等敏感文件被静态读出）
_STATIC_EXTS = {".html", ".htm", ".css", ".js", ".mjs", ".json", ".map",
                ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
                ".woff", ".woff2", ".ttf", ".txt", ".md"}


def _is_redacted_key(k):
    """判断 API Key 是否为前端回传的占位符/掩码（不应写回凭据管理器）。"""
    k = (k or "").strip()
    if not k:
        return True
    if k.startswith("«redacted"):
        return True
    if k.endswith("****"):
        return True
    return False


def _mask_secret(k, keep=6):
    """把敏感值掩码成 «redacted:xxxxx…» 占位符（保留前缀便于识别），绝不回传全量明文。"""
    k = (k or "").strip()
    if not k:
        return ""
    if len(k) <= keep:
        return "«redacted»"
    return "«redacted:" + k[:keep] + "…»"


def _detect_protocol_paths():
    """通用探测：扫描常见安装区（各盘根 + 用户下载/桌面目录，深度<=3），
    不依赖任何固定安装路径——通过「目录名模式」和「可执行文件名」双通道识别驱动。

    按文件名反查（适配任意安装目录名）：
      NapCat   : NapCatWinBootMain.exe / napcat.exe
      LLOneBot : llbot.exe / LLBot.exe / LLOneBot.exe / LLOneBot_WebUI.exe
      SnowLuma : SnowLuma.exe / Lagrange.OneBot.exe（运行时：同目录 node.exe + index.mjs）
      QQ       : QQ.exe / QQNT.exe（目录名须含 qq/tencent/imqq，避免误判）
    """
    import os as _os
    found_qq, found_nap, found_llo, found_snw = [], [], [], []
    _skip = {"Windows", "Program Files", "Program Files (x86)", "ProgramData",
             "$Recycle.Bin", "System Volume Information", "node_modules",
             "AppData", "Application Data", "MicrosoftEdgeBackups", "WpSystem",
             "WindowsApps", "PerfLogs", "Recovery", "Documents and Settings", ".git"}

    EXE_NAP = ("NapCatWinBootMain.exe", "napcat.exe")
    EXE_LLO = ("llbot.exe", "LLBot.exe", "LLOneBot.exe", "LLOneBot_WebUI.exe")
    EXE_SNW = ("SnowLuma.exe", "Lagrange.OneBot.exe")

    def _add(driver, exe):
        lst = {"qq": found_qq, "napcat": found_nap,
               "llonebot": found_llo, "snowluma": found_snw}[driver]
        if exe and _os.path.exists(exe) and exe not in lst:
            lst.append(exe)

    def _dir_kind(name):
        low = name.lower()
        if "napcat" in low:
            return "napcat"
        if "snowluma" in low or "lagrange" in low:
            return "snowluma"
        if "llbot" in low or "llonebot" in low:
            return "llonebot"
        if "qq" in low or "tencent" in low or "imqq" in low:
            return "qq"
        return None

    def _is_runtime_snowluma(directory):
        return (_os.path.exists(_os.path.join(directory, "node.exe"))
                and _os.path.exists(_os.path.join(directory, "index.mjs")))

    home = _os.path.expanduser("~")
    import string as _string
    roots = [d + ":\\" for d in _string.ascii_uppercase if _os.path.exists(d + ":\\")]
    roots = list(dict.fromkeys(roots + [home]))
    scan_dirs = list(roots)
    for extra in ("Downloads", "Desktop", "下载", "桌面"):
        p = _os.path.join(home, extra)
        if _os.path.isdir(p):
            scan_dirs.append(p)

    for root in scan_dirs:
        if not _os.path.isdir(root):
            continue
        try:
            for base, dirs, files in _os.walk(root):
                depth = base[len(root):].count(_os.sep)
                if depth >= 3:
                    dirs[:] = []
                    continue
                dirs[:] = [d for d in dirs if d not in _skip and not d.startswith(".")]
                kind = _dir_kind(_os.path.basename(base))
                if kind == "qq":
                    for n in ("QQ.exe", "QQNT.exe"):
                        _add("qq", _os.path.join(base, n))
                elif kind == "napcat":
                    for n in EXE_NAP:
                        _add("napcat", _os.path.join(base, n))
                elif kind == "llonebot":
                    for n in EXE_LLO:
                        _add("llonebot", _os.path.join(base, n))
                elif kind == "snowluma":
                    for n in EXE_SNW:
                        _add("snowluma", _os.path.join(base, n))
                    if _is_runtime_snowluma(base):
                        _add("snowluma", _os.path.join(base, "node.exe"))
                for n in EXE_NAP:
                    if n in files:
                        _add("napcat", _os.path.join(base, n))
                for n in EXE_LLO:
                    if n in files:
                        _add("llonebot", _os.path.join(base, n))
                for n in EXE_SNW:
                    if n in files:
                        _add("snowluma", _os.path.join(base, n))
        except Exception:
            pass

    return {"ok": True, "qq_paths": found_qq, "napcat_paths": found_nap,
            "llonebot_paths": found_llo, "snowluma_paths": found_snw}


def _classify_driver_path(path):
    """根据用户选择的文件路径判断它属于哪个协议驱动（文件名 + 目录名启发式）。

    返回 {driver: napcat|llonebot|snowluma|qq|unknown, abspath}。
    """
    import os as _os
    abspath = _os.path.abspath(_os.path.expanduser((path or "").strip())) if (path or "").strip() else ""
    if not abspath or not _os.path.exists(abspath):
        return {"driver": "unknown", "abspath": abspath}
    name = _os.path.basename(abspath).lower()
    dirname = _os.path.dirname(abspath).lower()
    if name in ("qq.exe", "qqnt.exe"):
        return {"driver": "qq", "abspath": abspath}
    if "napcat" in name or "napcat" in dirname:
        return {"driver": "napcat", "abspath": abspath}
    if "llbot" in name or "llonebot" in name or "llbot" in dirname or "llonebot" in dirname:
        return {"driver": "llonebot", "abspath": abspath}
    if "snowluma" in name or "snowluma" in dirname or "lagrange" in name or "lagrange" in dirname:
        return {"driver": "snowluma", "abspath": abspath}
    return {"driver": "unknown", "abspath": abspath}


# 各驱动风险说明（前端风险面板 / 切换确认 / 自动识别共用）
# 统一为「风险提示」：均属非官方登录通道，不做高低风险分级，
# 只陈述各自登录行为差异，供用户自行判断。
PROTO_RISK_INFO = {
    "napcat": {
        "level": "notice", "label": "风险提示",
        "text": "NapCat BootMain 注入真实 QQ，重启会重扫登录态。非官方通道，频繁登录易触发腾讯风控",
    },
    "llonebot": {
        "level": "warning", "label": "未测试 · 不保证兼容",
        "text": "LLOneBot 为 QQNT 插件，注入真实 QQ。非官方通道，频繁重启/重扫登录态易触发腾讯风控。本驱动未在本项目实测，事件桥/反戳/群管理等功能不保证兼容，仅供探索",
    },
    "snowluma": {
        "level": "notice", "label": "风险提示",
        "text": "SnowLuma 需手动打开 QQ 客户端保持登录，再通过 WebUI 密码登录+手动加载账号，登录态由你主动控制，通常不会造成频繁登录；属非官方通道，仍建议勿在风控期内反复重启",
    },
}


def _auto_detect_protocol_drivers():
    """自动识别本机已安装 / 运行中的协议驱动，返回候选列表与推荐驱动。

    返回 {ok, candidates: [{driver, name, exe, found, running, desc}], recommended}。
    检测逻辑：
    - 运行中：按各驱动默认端口探测（snowluma:5099 / napcat:3100 / llonebot:7000）
    - 已安装：SnowLuma 查运行时目录（node.exe + index.mjs）；NapCat/LLOneBot/QQ 由 _detect_protocol_paths 泛化扫描
    推荐优先级：正在运行 > 已安装；同状态按 napcat > llonebot > snowluma(无头)。
    """
    import os as _os
    import socket as _sock

    def _port_open(port):
        try:
            s = _sock.socket()
            s.settimeout(0.4)
            s.connect(("127.0.0.1", port))
            s.close()
            return True
        except Exception:
            return False

    det = _detect_protocol_paths()

    # SnowLuma：运行时目录 node.exe + index.mjs（探测结果优先）
    snow_exe = (det.get("snowluma_paths") or [None])[0] or ""
    snow_found = bool(snow_exe) and _os.path.exists(snow_exe)
    cands = [{
        "driver": "snowluma", "name": "SnowLuma", "exe": snow_exe if snow_found else "",
        "found": snow_found, "running": _port_open(5099),
        "desc": "OneBot v11 动作面最广（130+ 动作）",
    }]

    # NapCat / LLOneBot：复用探测结果
    nap_exe = (det.get("napcat_paths") or [None])[0] or ""
    cands.append({
        "driver": "napcat", "name": "NapCat", "exe": nap_exe,
        "found": bool(nap_exe), "running": _port_open(3100),
        "desc": "常用稳定",
    })
    llo_exe = (det.get("llonebot_paths") or [None])[0] or ""
    cands.append({
        "driver": "llonebot", "name": "LLOneBot", "exe": llo_exe,
        "found": bool(llo_exe), "running": _port_open(7000),
        "desc": "QQNT 插件型",
    })

    # 当前配置驱动若在运行 → 优先推荐
    # 推荐顺序：snowluma(手动登录，登录态主动控制，最不易触发频繁登录) > napcat > llonebot
    order = {"snowluma": 0, "napcat": 1, "llonebot": 2}
    for c in cands:
        _r = PROTO_RISK_INFO.get(c["driver"]) or {}
        c["risk"] = _r.get("level", "notice")
        c["risk_label"] = _r.get("label", "风险提示")
        c["risk_text"] = _r.get("text", "")
    active = [c for c in cands if c["running"] or c["found"]]
    active.sort(key=lambda c: (0 if c["running"] else 1, order.get(c["driver"], 9)))
    recommended = active[0]["driver"] if active else ""
    return {"ok": True, "candidates": cands, "recommended": recommended}


def _protocol_check_items(driver: str = "") -> dict:
    """启动前自检：当前驱动必填配置逐项检测（只读，不启动任何进程）。

    返回 {ok, driver, items: [{key, label, ok, hint}]}，ok=False 表示存在未就绪项。
    文件型配置（exe / QQ / dll / 清单文件）会同时校验路径是否存在，
    与各驱动适配器 _preflight 的判定保持一致，避免启动失败后才提示。
    """
    import os as _os
    try:
        from protocols import registry
        from protocol_manager import _current_driver
    except Exception as e:
        return {"ok": False, "driver": driver or "", "items": [], "error": f"协议层模块不可用: {e}"}

    drv = str(driver or "").strip() or _current_driver()
    if not registry.is_registered(drv):
        return {"ok": False, "driver": drv, "items": [], "error": f"协议驱动 {drv} 未注册"}

    try:
        adapter = registry.build(drv)
    except Exception as e:
        return {"ok": False, "driver": drv, "items": [], "error": f"构建适配器失败: {e}"}

    items = []

    def _add(key, label, val, must_exist=False):
        v = str(val or "").strip().strip('"').strip()
        if not v:
            items.append({"key": key, "label": label, "ok": False, "hint": "未配置"})
            return
        if must_exist and not _os.path.exists(v):
            items.append({"key": key, "label": label, "ok": False, "hint": f"路径不存在: {v}"})
            return
        items.append({"key": key, "label": label, "ok": True, "hint": v})

    if drv == "napcat":
        nap_dir = getattr(adapter, "_napcat_dir", "")
        _add("exe_path", "NapCat 启动器 exe", adapter.exe_path, must_exist=True)
        _add("hook_dll", "NapCatWinBootHook.dll", _os.path.join(nap_dir, "NapCatWinBootHook.dll") if nap_dir else "", must_exist=True)
        _add("qq_path", "QQ.exe 路径", adapter.qq_path, must_exist=True)
        _add("qqnt_json", "qqnt.json（NapCat 补丁清单）", _os.path.join(nap_dir, "qqnt.json") if nap_dir else "", must_exist=True)
        _add("napcat_mjs", "napcat.mjs（NapCat 主程序）", _os.path.join(nap_dir, "napcat.mjs") if nap_dir else "", must_exist=True)
        _add("api_url", "OneBot API 地址", adapter.api_base_url)
        _add("ws_port", "WS 事件端口", adapter.ws_port)
        _add("report_url", "事件上报地址", adapter.report_url)
    elif drv == "llonebot":
        _add("exe_path", "QQ.exe 或 LLOneBot 启动器", adapter.exe_path, must_exist=True)
        _add("api_url", "OneBot API 地址", adapter.api_base_url)
        _add("ws_port", "WS 事件端口", adapter.ws_port)
        _add("report_url", "事件上报地址", adapter.report_url)
    elif drv == "snowluma":
        try:
            from protocols.snowluma_adapter import _resolve_runtime
            node = _resolve_runtime(str(adapter.exe_path or ""))
        except Exception:
            node = str(adapter.exe_path or "")
        runtime_dir = getattr(adapter, "runtime_dir", "")
        _add("exe_path", "node.exe（SnowLuma 运行时）", node, must_exist=True)
        _add("index_mjs", "index.mjs（SnowLuma 入口）", _os.path.join(runtime_dir, "index.mjs") if runtime_dir else "", must_exist=True)
        _add("api_url", "OneBot API 地址", adapter.api_base_url)
        _add("ws_port", "WS 事件端口", adapter.ws_port)
        _add("report_url", "事件上报地址", adapter.report_url)
    else:
        _add("exe_path", "启动器路径", getattr(adapter, "exe_path", ""), must_exist=True)

    # 事件桥（WS 事件通道）实时状态：驱动已在运行但事件桥未连接时，戳一戳/通知类
    # 事件会被丢弃（消息收发走 HTTP 轮询不受影响）——正是「连上了但没反应」的常见原因。
    try:
        import socket as _sock
        _S = globals().get("S")
        _mgr = getattr(_S, "_protocol_manager", None) if _S is not None else None
        _mgr_running = bool(_mgr and getattr(_mgr, "_running", False))
        _bridge = ""
        if _mgr_running:
            try:
                _bridge = str((_mgr.get_status() or {}).get("bridge_state", ""))
            except Exception:
                pass
        _ws_port = 0
        try:
            _ws_port = int(getattr(adapter, "ws_port", 0) or 0)
        except Exception:
            _ws_port = 0
        _ws_open = False
        if _ws_port and _ws_port > 0:
            try:
                _sk = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
                _sk.settimeout(0.4)
                _ws_open = _sk.connect_ex(("127.0.0.1", _ws_port)) == 0
                _sk.close()
            except Exception:
                pass
        if _mgr_running and _bridge == "up":
            _add("event_bridge", "事件桥（WS 事件通道）", "已连接，戳一戳/通知类事件可正常上报")
        elif _ws_open:
            items.append({"key": "event_bridge", "label": "事件桥（WS 事件通道）", "ok": False,
                          "hint": f"驱动已在运行(:{_ws_port})但事件桥未连接 → 戳一戳/通知类事件不会上报（消息轮询不受影响）；点「启动协议层」将直接接管并连接事件桥"})
        else:
            _add("event_bridge", "事件桥（WS 事件通道）", "未运行（启动协议层后自动连接）")
    except Exception:
        pass

    missing = [it for it in items if not it.get("ok")]
    return {"ok": not missing, "driver": drv, "items": items, "missing": missing}


# 本地 embedding 服务守护：llama-server 跑 BGE-small-zh，端口 8083
# 未启动时自动拉起（失败静默，super_memory 会自动降级回传统检索）
# 路径来源：super_memory data/config.json > bot_config.json(embed_*) > 内置默认（本机 LiGGUF/BGE 模型）
_EMBED_DEFAULT_EXE = Path(str(load_config().get("embed_exe") or "")).resolve()
_EMBED_DEFAULT_MODEL = Path(str(load_config().get("embed_model_path") or "")).resolve()
# 内置默认：模型随 EGO super_memory 打包分发（体量小）；llama-server 为 HTTP 兜底
_EMBED_BUILTIN_MODEL = PROJECT / "plugins" / "ego" / "super_memory" / "models" / "bge-small-zh-v1.5-f16.gguf"
_EMBED_BUILTIN_EXE = Path(r"D:\llama-b10068\llama-server.exe")
_EMBED_PORT = 8083
_embed_proc = None


def _embed_config():
    """embedding 服务路径配置：读取 super_memory 插件的 data/config.json（use_vector_recall/embed_url/embed_exe/embed_model_path）"""
    try:
        p = PROJECT / "plugins" / "ego" / "super_memory" / "data" / "config.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    try:
        p2 = PROJECT / "plugins" / "super_memory" / "data" / "config.json"
        if p2.exists():
            return json.loads(p2.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _ensure_embed_server():
    """确保 8083 embedding 服务在运行（路径可配置，见 super_memory 配置）"""
    global _embed_proc
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.3)
        alive = s.connect_ex(("127.0.0.1", _EMBED_PORT)) == 0
        s.close()
        if alive:
            return True
    except Exception:
        pass
    # 已拉起过但退出的，清引用
    if _embed_proc and _embed_proc.poll() is not None:
        _embed_proc = None
    cfg = _embed_config()
    if cfg.get("use_vector_recall") is False:
        return False  # 显式关闭向量检索时不拉起
    _sm_plugin = PROJECT / "plugins" / "ego" / "super_memory"

    def _ascii_mirror(path, cache_dir):
        """非 ASCII 路径镜像到 llama-server 同目录 models/（真实 ASCII 持久路径），返回 Path。

        llama-server.exe / LiGGUF DLL 均无法读取含非 ASCII 字符的路径（水星目录实测：
        路径在「水星」处截断）。且 MSIX python 的 ~/AppData/Local 被重定向到 WpSystem
        虚拟路径，原生 llama-server 读不到 → 必须镜像到 llama-server 所在目录的 models/。
        """
        p = os.path.abspath(str(path or ""))
        if not p or not os.path.exists(p) or all(ord(c) < 128 for c in p):
            return Path(p) if p else path
        try:
            target_dir = os.path.join(str(cache_dir), "models")
            os.makedirs(target_dir, exist_ok=True)
            target = os.path.join(target_dir, os.path.basename(p))
            if not os.path.exists(target) or os.path.getsize(target) != os.path.getsize(p):
                import shutil
                shutil.copyfile(p, target)
            return Path(target)
        except Exception:
            return path

    def _resolve(p, fallback):
        p = str(p or "")
        if p and not os.path.isabs(p):
            p = str(_sm_plugin / p)
        return Path(p or str(fallback))

    exe = _resolve(cfg.get("embed_exe"), _EMBED_DEFAULT_EXE or _EMBED_BUILTIN_EXE)
    model = _resolve(cfg.get("embed_model_path"), _EMBED_DEFAULT_MODEL or _EMBED_BUILTIN_MODEL)
    model = _ascii_mirror(model, exe.parent)
    if not (exe.exists() and model.exists()):
        print(f"[embed] 服务未启动且找不到 embedding 程序: {exe} / {model}")
        return False
    try:
        log_dir = exe.parent
        logp = log_dir / f"embed-{_EMBED_PORT}.log"
        stdout = open(logp, "a", encoding="utf-8", errors="replace")
        stderr = open(logp, "a", encoding="utf-8", errors="replace")
        import subprocess as _sp

        _embed_proc = _sp.Popen(
            [str(exe), "-m", str(model), "--host", "127.0.0.1", "--port", str(_EMBED_PORT),
             "--embeddings", "-c", "8192", "--log-file", str(logp)],
            stdout=stdout, stderr=stderr,
            creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0),
        )
        print(f"[embed] 已拉起 llama-server embedding -> {exe}")
        return True
    except Exception as e:
        print(f"[embed] 拉起失败: {e}")
        return False


def _watch_embed_server():
    """embedding 存活监控：每 30s 检查一次，挂了自动重启"""
    while True:
        time.sleep(30)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            alive = s.connect_ex(("127.0.0.1", _EMBED_PORT)) == 0
            s.close()
            if not alive:
                print("[embed] 8083 掉线，尝试自动重启")
                _ensure_embed_server()
        except Exception:
            pass


try:
    _ensure_embed_server()
except Exception:
    pass
# 守护线程：embedding 挂掉自动拉起（后台，不阻塞主流程）
try:
    t = threading.Thread(target=_watch_embed_server, daemon=True)
    t.start()
except Exception:
    pass


def _invoke_generic_plugin(plugin_id, action, params):
    """通用插件通道：按统一标准调用任意插件，不修改主程序。

    标准约定（插件 __init__.py 内声明，全部可选）：
      1. PLUGIN_ACTIONS = { "action_name": fn(params)->jsonable }   优先匹配
      2. 兜底：模块导出 get_config / set_config / toggle
      3. 兜底：模块导出 execute_action(action, params) -> jsonable
    只有声明了 PLUGIN_ACTIONS 或 execute_action 的新标准插件会走此通道；
    其余插件返回 None，继续交由硬编码分支/旧 fallback 处理，保持完全兼容。
    """
    try:
        mod = __import__(f"plugins.{plugin_id}", fromlist=["PLUGIN_ACTIONS", "execute_action"])
    except Exception:
        return None
    # 非标准插件的兜底动作由下方 fallback 分支处理，同步：
    acts = getattr(mod, "PLUGIN_ACTIONS", None)
    exec_fn = getattr(mod, "execute_action", None)
    supports_standard = isinstance(acts, dict) or callable(exec_fn)
    if not supports_standard:
        return None
    # 1. PLUGIN_ACTIONS 映射
    try:
        if isinstance(acts, dict):
            fn = acts.get(action)
            if callable(fn):
                try:
                    return fn(params or {})
                except Exception as e:
                    return {"error": f"[{plugin_id}] {action} failed: {e}"}
    except Exception:
        pass
    # 2. 标准动作兜底：get_config / set_config / toggle
    try:
        if action == "get_config":
            fn = getattr(mod, "get_config", None)
            if callable(fn):
                return fn()
        elif action == "set_config":
            fn = getattr(mod, "set_config", None)
            if callable(fn):
                return fn(params or {})
        elif action == "toggle":
            fn = getattr(mod, "toggle", None)
            if callable(fn):
                return fn()
    except Exception as e:
        return {"error": f"[{plugin_id}] {action} failed: {e}"}
    # 3. execute_action 兜底
    if callable(exec_fn):
        try:
            return exec_fn(action, params or {})
        except Exception as e:
            return {"error": f"[{plugin_id}] {action} failed: {e}"}
    return None


# 执行time State
STATE_LOCK = threading.Lock()
BOT_CORE_URL = "http://127.0.0.1:3005"


# Performance cache
class _Cache:
    def __init__(self, ttl=5):
        self._data = None
        self._time = 0
        self._ttl = ttl
    def get(self):
        if self._data is not None and time.time() - self._time < self._ttl:
            return self._data
        return None
    def set(self, val):
        self._data = val
        self._time = time.time()


# bot_core connection cache (5s)
_BOT_CORE_CACHE = _Cache(ttl=5)

def _is_bot_core_alive():
    cached = _BOT_CORE_CACHE.get()
    if cached is not None:
        return cached
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.15)
        alive = s.connect_ex(('127.0.0.1', 3005)) == 0
        s.close()
    except Exception:
        alive = False
    _BOT_CORE_CACHE.set(alive)
    return alive


def fetch_from_bot_core(path, timeout=0.5):
    if not _is_bot_core_alive():
        return None
    try:
        req = urllib.request.Request(f"{BOT_CORE_URL}{path}", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def push_bot_reload():
    """通知 bot_core 热重载配置（POST /reload_config）。bot 未运行时静默跳过。"""
    if not _is_bot_core_alive():
        return None
    try:
        req = urllib.request.Request(
            f"{BOT_CORE_URL}/reload_config",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


# System stats cache (2s)
_SYS_CACHE = _Cache(ttl=2)

# Protocol log buffer
PROTO_LOG_MAX = 600
PROTO_LOG = deque(maxlen=PROTO_LOG_MAX)
PROTO_LOG_LOCK = threading.Lock()
_LOG_CLEARED = False

def push_proto_log(text: str):
    global _LOG_CLEARED
    with PROTO_LOG_LOCK:
        PROTO_LOG.append({"time": datetime.datetime.now().strftime("%H:%M:%S"), "text": text})
        _LOG_CLEARED = False


def _protocol_status_callback(status: str, detail: str = ""):
    """协议驱动状态回调：更新 S.protocol_running 并写入协议日志"""
    try:
        with STATE_LOCK:
            if status == "started":
                S.protocol_running = True
            elif status == "stopped":
                S.protocol_running = False
        if detail:
            push_proto_log(f"[{status}] {detail}")
        elif status:
            push_proto_log(status)
    except Exception:
        pass


class State:
    def __init__(self):
        self.bot_running = False
        self.bot_start_time = time.time()
        self.messages_total = 0
        self.groups_active = 0
        self.images_generated = 0
        self.memes_sent = 0
        self.plugin_count = 0
        self.plugin_active = 0
        self.llm_status = "unknown"
        self.llm_model = "deepseek-flash"
        self.memory_total = 0
        self.session_count = 0
        self.api_calls_today = 0
        self.tokens_used = 0
        self.activities = deque(maxlen=30)
        self._init_acts()
    def _init_acts(self):
        now = datetime.datetime.now()
        items = [
            ("emerald", "系统已启动"),
            ("violet", "AI 引擎就绪"),
            ("cyan", "ComfyUI 已连接"),
            ("amber", "表情引擎就绪"),
            ("rose", "插件已加载"),
            ("violet", "DeepSeek API 已配置"),
            ("emerald", "Self-learning DB loaded"),
            ("cyan", "OneBot 协议就绪"),
        ]
        for i, (c, t) in enumerate(items):
            ts = now - datetime.timedelta(minutes=(len(items) - i) * 3)
            self.activities.append({"color": c, "text": t, "time": ts.strftime("%H:%M")})
    def add_activity(self, color, text):
        self.activities.appendleft({"color": color, "text": text, "time": datetime.datetime.now().strftime("%H:%M")})
    def merge_bot_core_data(self, data):
        """用 bot_core 返回的数据更新 State"""
        if not data:
            return
        with STATE_LOCK:
            for key in ["messages_total", "groups_active", "images_generated",
                        "memes_sent", "plugin_count", "plugin_active",
                        "llm_status", "llm_model", "memory_total",
                        "session_count", "api_calls_today", "tokens_used"]:
                if key in data:
                    setattr(self, key, data[key])
            if data.get("bot_start_time"):
                self.bot_start_time = data["bot_start_time"]
                self.bot_running = True


S = State()
_bot_process = None
_bot_lock = threading.Lock()


def _detect_provider_type(base_url, provider_id):
    pid = (provider_id or '').lower()
    # 所有 OpenAI 兼容服务商(国产官方/聚合)直接按其 id 返回,模型探测统一走 /v1/models
    if pid in {'deepseek', 'openai', 'custom', 'aliyun', 'zhipu', 'kimi', 'step',
               'minimax', 'volcengine', 'qianfan', 'hunyuan', 'siliconflow', 'openrouter',
               'groq', 'together'}:
        return pid
    if pid in {'anthropic', 'claude'}:
        return 'anthropic'
    if pid in {'google', 'gemini'}:
        return 'gemini'
    if pid == 'ollama':
        return 'ollama'
    if pid == 'lmstudio':
        return 'lmstudio'
    url = (base_url or '').lower()
    if 'deepseek' in url:
        return 'deepseek'
    if 'openai' in url or 'openai.com' in url:
        return 'openai'
    if 'anthropic' in url or '/messages' in url:
        return 'anthropic'
    if 'generativelanguage' in url:
        return 'gemini'
    if 'ollama' in url or '11434' in url:
        return 'ollama'
    if 'lmstudio' in url or '1234' in url:
        return 'lmstudio'
    return 'custom'


def _model_candidate_urls(base_url, provider_type):
    if not base_url:
        return []
    try:
        p = urllib.parse.urlsplit(base_url)
        path = (p.path or '').rstrip('/')
        for suffix in ['/v1/chat/completions', '/chat/completions', '/v1/messages', '/messages']:
            if path.endswith(suffix):
                path = path[:-len(suffix)]
                break
        root = urllib.parse.urlunsplit((p.scheme, p.netloc, path, p.query, p.fragment)).rstrip('/')
    except Exception:
        root = base_url.rstrip('/')

    if provider_type in ('anthropic', 'claude'):
        # Claude: GET /v1/models，需 x-api-key + anthropic-version
        if root.endswith('/v1'):
            return [root + '/models']
        return [root + '/v1/models', root + '/models']
    if provider_type in ('gemini', 'google'):
        # Gemini: GET /v1beta/models，需 ?key= 查询参数
        return [root + '/models', root + '/v1beta/models']
    if provider_type in ('custom',):
        # 自定义端点也可能是标准 OpenAI 兼容服务：仍尝试 /v1/models（服务端用短超时快速失败）；
        # 失败时前端回落已保存的模型列表 + 手动输入。
        return [
            root + '/v1/models',
            root + '/models',
        ]
    candidates = [
        root + '/v1/models',
        root + '/models',
    ]
    return candidates


def _provider_api_key(request_key, provider_entry):
    """解析模型列表拉取用的 API Key：请求 Key > 提供商专属凭据 > 全局凭据。"""
    k = (request_key or '').strip()
    if k and not _is_redacted_key(k):
        return k
    if provider_entry:
        pk = (provider_entry.get('api_key') or '').strip()
        if pk and not _is_redacted_key(pk):
            return pk
    try:
        from config import get_provider_api_key
        pid = (provider_entry or {}).get('id') or ''
        return get_provider_api_key(pid) or ''
    except Exception:
        return ''


def _make_autonomous_chat():
    """为 web_operator 自主 Agent 构造宿主文本模型回调。
    签名：chat(messages, tools=None, max_tokens=...) -> (content, tool_calls, usage)。
    复用主模型配置（api_provider / model_name / api_url + 凭据管理器里的 Key）。"""
    from providers import make_provider
    cfg = load_config()
    pid = (cfg.get('api_provider') or '').strip()
    api_url = (cfg.get('api_url') or '').strip()
    model = (cfg.get('model_name') or cfg.get('model') or '').strip()
    if not api_url:
        try:
            from config import API_PROVIDERS
            api_url = (API_PROVIDERS.get(pid) or {}).get('url') or 'https://api.deepseek.com/chat/completions'
        except Exception:
            api_url = 'https://api.deepseek.com/chat/completions'
    api_key = ''
    try:
        from config import get_provider_api_key, get_api_key
        api_key = (get_provider_api_key(pid) if pid else get_api_key()) or ''
    except Exception:
        api_key = ''
    if not api_key:
        raise RuntimeError('未检测到可用的模型 API Key（请在设置里配置提供商与密钥）')
    prov = make_provider(api_url=api_url, api_key=api_key,
                         model=model or 'deepseek-flash', provider=pid or 'openai', max_retries=2)

    def _chat(messages, tools=None, max_tokens=1500):
        return prov.chat(messages, tools=tools, max_tokens=max_tokens)
    return _chat


def _fetch_remote_models(url, api_key, provider_type):
    """按提供商类型用正确的鉴权方式请求模型列表，返回 (models, error)。"""
    pt = (provider_type or 'openai').lower()
    headers = {'Accept': 'application/json'}
    final_url = url
    if pt in ('anthropic', 'claude'):
        headers.update({'x-api-key': api_key or '', 'anthropic-version': '2023-06-01'})
    elif pt in ('gemini', 'google'):
        sep = '&' if '?' in url else '?'
        final_url = url + sep + 'key=' + urllib.parse.quote(api_key or '')
    else:
        headers['Authorization'] = 'Bearer ' + (api_key or '')
    req = urllib.request.Request(final_url, headers=headers)
    with urllib.request.urlopen(req, timeout=6) as r:
        data = json.loads(r.read().decode('utf-8'))

    def _mid(m):
        if pt in ('gemini', 'google'):
            n = m.get('name') or m.get('id') or m.get('model') or ''
            return n.split('/')[-1] if '/' in n else n
        return m.get('id') or m.get('name') or m.get('model') or ''

    raw = data.get('data') or data.get('models') or []
    models = []
    for m in raw:
        mid = _mid(m)
        if mid:
            models.append({'id': mid, 'name': m.get('name', mid)})
    models.sort(key=lambda x: x['id'])
    return models, None


def _classify_error(err):
    text = str(err)
    if '地址不可达' in text or 'Name or service not known' in text or 'getaddrinfo' in text or 'Connection refused' in text or 'timed out' in text:
        return '地址不可达'
    if 'HTTP' in text or '403' in text or '401' in text or '404' in text or 'JSON' in text:
        return '接口返回异常'
    return text


def _start_bot_log_reader(proc):
    def _reader():
        try:
            for line in iter(proc.stdout.readline, ''):
                if not line:
                    break
                push_proto_log(f'[Bot] {line.rstrip()}')
        except Exception:
            pass
        push_proto_log('[Bot] 进程已退出')
    threading.Thread(target=_reader, daemon=True).start()

# Pre-computed status cache (background refresh)
_STATUS_CACHE = {"data": None, "time": 0}

# 协议驱动 → 展示名（HUD 顶部状态条用）
_PROTO_DRIVER_LABEL = {"napcat": "NapCat", "llonebot": "LLOneBot", "snowluma": "SnowLuma"}


def _protocol_hud():
    """HUD 用：当前协议驱动 + 是否在运行。

    轻量红线：只读弱缓存配置 + 内存标志 —— **不做** WebUI 地址探测、不读驱动
    日志（那些在 /api/protocol/status 里做）。/api/status 被前端按秒轮询且调用方
    超时只有 0.5s，重活会把状态接口拖死、HUD 直接冻结。
    此前 /api/status 载荷里**根本没有** protocol_driver 字段 → 前端恒显示
    「未设置」（用户截图），这里补上。"""
    driver = ""
    try:
        driver = str((load_config() or {}).get("protocol_driver") or "").strip()
    except Exception:
        driver = ""
    running = False
    mgr = getattr(S, "_protocol_manager", None)
    if mgr is not None:
        running = bool(getattr(mgr, "_running", False))
    else:
        running = bool(getattr(S, "protocol_running", False))
    return {
        "protocol_driver": driver,
        "protocol_driver_label": _PROTO_DRIVER_LABEL.get(driver.lower(), driver),
        "protocol_running": running,
    }


def _refresh_status():
    while True:
        try:
            bc = fetch_from_bot_core("/api/status")
            if bc:
                S.merge_bot_core_data(bc)
            with STATE_LOCK:
                stats = {
                    "bot_running": S.bot_running,
                    "bot_uptime": int(time.time() - S.bot_start_time),
                    "messages_total": S.messages_total,
                    "groups_active": S.groups_active,
                    "images_generated": S.images_generated,
                    "memes_sent": S.memes_sent,
                    "plugin_count": S.plugin_count,
                    "plugin_active": S.plugin_active,
                    "llm_status": S.llm_status,
                    "llm_model": S.llm_model,
                    "memory_total": S.memory_total,
                    "session_count": S.session_count,
                    "api_calls_today": S.api_calls_today,
                    "tokens_used": S.tokens_used,
                }
            sys_stats = get_system_stats()
            stats["cpu_usage"] = sys_stats["cpu"]
            stats["mem_usage"] = sys_stats["memory"]
            stats.update(_protocol_hud())      # 协议驱动/运行态（HUD 顶部状态条）
            _STATUS_CACHE["data"] = {"data": stats}
            _STATUS_CACHE["time"] = time.time()
        except Exception:
            pass
        time.sleep(1)

# Start background refresh thread
_thread = threading.Thread(target=_refresh_status, daemon=True)
_thread.start()

# Config: load_config 统一走 config.py（程序目录优先 + AppData 补缺 + env + 凭据管理器）
def save_config(cfg):
    p = PROJECT / "bot_config.json"
    to_save = {k: v for k, v in cfg.items() if k != "api_key"}
    try:
        with open(str(p), 'w', encoding='utf-8') as f:
            json.dump(to_save, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

CONFIG_CACHE = load_config()

# Self-learning / Memory
_SL_CACHE = _Cache(ttl=30)

def get_self_learning_stats():
    cached = _SL_CACHE.get()
    if cached is not None:
        return cached
    try:
        db_paths = [
            PROJECT / "self_learning_db" / "self_learning.db",
            Path.home() / "AppData/Local/EggBloom_v3/self_learning_db/self_learning.db",
        ]
        for db in db_paths:
            if not db.exists():
                continue
            conn = sqlite3.connect(str(db))
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM messages")
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(DISTINCT group_id) FROM messages")
            groups = cur.fetchone()[0]
            conn.close()
            result = {"total_messages": total, "groups": groups}
            _SL_CACHE.set(result)
            return result
        result = {"total_messages": 0, "groups": 0}
    except Exception:
        result = {"total_messages": 0, "groups": 0}
    _SL_CACHE.set(result)
    return result


def get_memory_stats():
    # T8: 私聊记忆已迁入 super_memory SQLite，统计改为查库
    try:
        import sqlite3
        db_path = PROJECT / "plugins" / "ego" / "super_memory" / "data" / "super_memory.db"
        if not db_path.exists():
            return {"total": 0, "size_kb": 0}
        conn = sqlite3.connect(str(db_path))
        total = conn.execute("SELECT COUNT(DISTINCT scope_id) FROM messages WHERE scope='private'").fetchone()[0]
        conn.close()
        size_kb = db_path.stat().st_size // 1024
        return {"total": total, "size_kb": size_kb}
    except Exception:
        return {"total": 0, "size_kb": 0}

_EXPR_CACHE = _Cache(ttl=30)

def get_expressions():
    cached = _EXPR_CACHE.get()
    if cached is not None:
        return cached
    try:
        db = PROJECT / "self_learning_db" / "self_learning.db"
        if not db.exists():
            result = []
        else:
            conn = sqlite3.connect(str(db))
            cur = conn.cursor()
            cur.execute("SELECT expression, scene, quality, count FROM expressions ORDER BY count DESC LIMIT 20")
            rows = cur.fetchall()
            conn.close()
            result = [{"text": r[0], "scene": r[1], "weight": r[2], "score": int(r[3] * 20)} for r in rows]
    except Exception:
        result = []
    _EXPR_CACHE.set(result)
    return result

# Chat Logs
def _collect_logs(limit=200):
    """优先返回内存协议日志；为空时回退到磁盘日志文件，保证 bot 非 webui 启动时也有数据可看。
    若用户主动清空过（_LOG_CLEARED=True 且内存仍空），保持为空，不回退到磁盘。

    每条都补一个 `level` 字段（err/warn/ai/send/sys/info），供前端做色彩分级。
    规则来自 log_levels.py，与终端着色同源。"""
    global _LOG_CLEARED
    with PROTO_LOG_LOCK:
        proto = list(PROTO_LOG)
        cleared = _LOG_CLEARED
    if proto:
        out = [dict(e) for e in proto[-max(1, min(limit, PROTO_LOG_MAX)):]]
    elif cleared:
        out = []
    else:
        out = get_chat_logs(limit)
    for e in out:
        if isinstance(e, dict) and "level" not in e:
            e["level"] = log_levels.classify(e.get("text") or e.get("msg") or "")
    return out

def get_chat_logs(limit=200):
    logs = []
    try:
        log_paths = [
            PROJECT / "core_log.txt",
            PROJECT / "qq_bot.log",
            Path.home() / "AppData/Local/EggBloom_v3/qq_bot.log",
            Path.home() / "AppData/Local/Temp/botcore_final.log",
            Path.home() / "AppData/Local/Temp/eggbloom_bot.log",
        ]
        for lf in log_paths:
            if not lf.exists():
                continue
            for line in lf.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                if "[CHAT]" in line:
                    try:
                        data = json.loads(line.split("[CHAT]", 1)[1].strip())
                        logs.append(data)
                    except Exception:
                        pass
                elif len(line) > 8 and line[2] == ':':
                    logs.append({"text": line, "time": line[:8], "direction": "log"})
                elif line.startswith('[') and len(line) > 10 and line[3] == ':' and line[6] == ':' and line[1:3].isdigit() and line[4:6].isdigit() and line[7:9].isdigit():
                    logs.append({"text": line[10:].strip(), "time": line[1:9], "direction": "log"})
    except Exception:
        pass
    return logs[-limit:]

# Tools catalog (群聊工具白名单用)
_TOOL_CATALOG = [
    {"name": "search_web", "label": "联网搜索", "desc": "搜索实时信息补充回答", "group_default": False},
    {"name": "open_app", "label": "打开应用/文件", "desc": "打开本机应用、文件或链接", "group_default": False},
    {"name": "take_screenshot", "label": "截图", "desc": "截取屏幕画面", "group_default": False},
    {"name": "get_system_info", "label": "系统信息", "desc": "查询 CPU/内存/磁盘等系统状态", "group_default": False},
    {"name": "control_volume", "label": "音量控制", "desc": "调节本机音量", "group_default": False},
    {"name": "control_power", "label": "电源控制", "desc": "睡眠/锁屏/重启等电源操作", "group_default": False},
    {"name": "manage_process", "label": "进程管理", "desc": "查看/结束本机进程", "group_default": False},
    {"name": "search_files", "label": "文件搜索", "desc": "在本地磁盘查找文件", "group_default": False},
    {"name": "send_user_file", "label": "发送文件", "desc": "把本地文件发送给用户", "group_default": False},
    {"name": "web_operate", "label": "网页操作", "desc": "打开网页、填表单、点击、截图、答题", "group_default": False},
    {"name": "get_group_members", "label": "群成员列表", "desc": "获取当前群成员，用于选人/@人", "group_default": True},
    {"name": "generate_image", "label": "生成图片", "desc": "根据描述生成图片", "group_default": True},
]

def _tools_catalog():
    """返回工具目录；group_default 为默认群聊允许的工具。"""
    return list(_TOOL_CATALOG)

# Gallery
def get_image_gallery():
    images = []
    try:
        img_dirs = [
            PROJECT / "image_cache",
            PROJECT / "memes" / "memes",
            Path.home() / "AppData/Local/EggBloom_v3/image_cache",
        ]
        for d in img_dirs:
            if not d.exists():
                continue
            for f in sorted(d.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
                if f.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.gif'):
                    images.append({
                        "name": f.name,
                        "size_kb": f.stat().st_size // 1024,
                        "time": datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%H:%M"),
                        "path": str(f),
                    })
    except Exception:
        pass
    return images[:50]

# System Stats
def get_system_stats():
    cached = _SYS_CACHE.get()
    if cached is not None:
        return cached
    try:
        import psutil
        result = {"cpu": round(psutil.cpu_percent(interval=0.05), 1), "memory": round(psutil.virtual_memory().percent, 1)}
    except ImportError:
        import random
        result = {"cpu": round(random.uniform(15, 35), 1), "memory": round(random.uniform(35, 55), 1)}
    _SYS_CACHE.set(result)
    return result

# 插件Discovery
# 
#  HTML TEMPLATE - Single File SPA
# 
# Load frontend from external HTML file to avoid triple-quote issues
# ── 前端版本（T8）───────────────────────────────────────────────────
#  v3 = 旧版，**逐字节原样保留** —— 用户要拿它对照接口（引导/向导等细节怕漏）
#  v4 = T8 重做版
#  切换按钮由**服务端注入**，所以两个前端文件都不必改动。
FRONTENDS = {
    "v3": HERE / "webui_artistic_v3.html",
    "v4": HERE / "webui_artistic_v4.html",
}
_UI_VERSION_FILE = HERE / "ui_version.txt"
UI_DEFAULT_VERSION = "v4"      # v4 已完工（功能对等 + 新视觉），默认走新版
                               # 想回旧版：写 webui/ui_version.txt 内容 v3，
                               # 或点界面右上角的切换按钮（两者都会写这个文件）


def _ui_version() -> str:
    """当前生效的前端版本。配置缺失/无效时回退默认。"""
    try:
        v = _UI_VERSION_FILE.read_text(encoding="utf-8").strip().lower()
        if v in FRONTENDS and FRONTENDS[v].is_file():
            return v
    except Exception:
        pass
    return UI_DEFAULT_VERSION


def _frontend_file(v):
    p = FRONTENDS.get(v)
    return p if (p and p.is_file()) else None


def _frontend_path():
    return FRONTENDS[_ui_version()]


_UI_SWITCH_TPL = """<div id="__uiSwitch" data-other="__OTHER__"></div>
<style>#__uiSwitchBtn{position:fixed;right:16px;bottom:16px;z-index:2147483000;
font:12px/1.5 'JetBrains Mono','Consolas',monospace;letter-spacing:.06em;
color:#d8d4cc;background:rgba(18,18,22,.94);border:1px solid #3d3d47;
padding:7px 13px;cursor:pointer;border-radius:0;box-shadow:0 6px 20px rgba(0,0,0,.55)}
#__uiSwitchBtn:hover{color:#f2efe8;border-color:#6a6a78}
#__uiSwitchBtn[disabled]{cursor:default;color:#6e6c69}</style>
<script>(function(){var d=document.getElementById('__uiSwitch');if(!d)return;
var b=document.createElement('button');b.id='__uiSwitchBtn';
b.textContent='\\u21c4 \\u5207\\u5230'+(d.dataset.other==='v4'?'\\u65b0\\u7248 v4':'\\u65e7\\u7248 v3');
b.title='\\u65b0\\u7248\\u89c6\\u89c9\\u672a\\u5b9a\\u7a3f\\u524d\\u968f\\u65f6\\u53ef\\u5207\\u56de';
b.onclick=function(){b.disabled=true;b.textContent='\\u5207\\u6362\\u4e2d\\u2026';
fetch('/api/ui/version',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({version:d.dataset.other})}).then(function(r){return r.json()})
.then(function(j){if(j&&j.ok){location.reload();}else{b.disabled=false;
b.textContent='\\u5207\\u6362\\u5931\\u8d25';}})
.catch(function(){b.disabled=false;b.textContent='\\u5207\\u6362\\u5931\\u8d25';});};
d.appendChild(b);})();</script>"""


def _inject_ui_switch(html: str, other: str) -> str:
    snippet = _UI_SWITCH_TPL.replace("__OTHER__", other)
    i = html.rfind("</body>")
    return (html[:i] + snippet + html[i:]) if i != -1 else (html + snippet)


def _read_frontend(v: str) -> str:
    """读前端文件并在服务端注入切换按钮（另一版本存在时才注入，免得点了必失败）。"""
    with open(FRONTENDS[v], "r", encoding="utf-8") as f:
        html = f.read().rstrip()
    other = "v4" if v == "v3" else "v3"
    if _frontend_file(other) is None:
        return html
    return _inject_ui_switch(html, other)


try:
    HTML = _read_frontend(_ui_version())
except Exception as _e:
    HTML = "<!DOCTYPE html><html><body><h1>EggBloom WebUI</h1><p>Failed to load frontend: " + str(_e) + "</p></body></html>"
_html_mtime = 0

# Meme image in-memory cache: rel-path -> (bytes, content_type, mtime_ms)
_meme_cache = {}
_MEME_CACHE_MAX = 1200

def _meme_dirs():
    return [PROJECT / 'memes' / 'memes', PROJECT.parent / 'memes' / 'memes'] if PROJECT else []


def _within(child, base) -> bool:
    """child 是否位于 base 之内（realpath + 大小写归一后比较）。

    P0-4：用于表情包路径校验。此前 `_serve_meme` 是裸的 `meme_dir / rel`，
    `/memes/../../bot_config.json` 这类上溯可读出 meme 目录之外的任意文件
    （含 bot_config.json 里的 onebot_token）。
    """
    try:
        c = os.path.normcase(os.path.abspath(str(child)))
        b = os.path.normcase(os.path.abspath(str(base)))
    except Exception:
        return False
    return c == b or c.startswith(b + os.sep)


def _safe_meme_rel(rel):
    """把 URL 里的相对路径规整成安全的相对路径；不安全则返回 None。

    拒绝：空、绝对路径、含 `..` 的任一段、含盘符/UNC 前缀。
    """
    if not rel:
        return None
    s = str(rel).replace('\\', '/')
    if s.startswith('/') or s.startswith('~'):
        return None
    parts = [p for p in s.split('/') if p not in ('', '.')]
    if not parts or any(p == '..' for p in parts):
        return None
    if ':' in parts[0]:                 # C:xxx / UNC 之类
        return None
    return '/'.join(parts)


def _serve_meme(self, rel):
    """Serve a meme file with in-memory caching. Returns True if served.

    安全（P0-4）：rel 来自 URL，必须先规整 + 做 realpath 容器校验，
    双重把关后再读盘。校验失败**不返回任何内容**（调用方走 404）。
    """
    safe_rel = _safe_meme_rel(rel)
    if safe_rel is None:
        return False
    for meme_dir in _meme_dirs():
        try:
            base = meme_dir.resolve()
            file_path = (base / safe_rel).resolve()
        except Exception:
            continue
        if not _within(file_path, base):
            continue                    # 软链接/拼接绕过 → 直接跳过
        if file_path.is_file():
            key = str(file_path)
            st = file_path.stat()
            cached = _meme_cache.get(key)
            if cached and cached[2] == st.st_mtime_ns and cached[3] == st.st_size:
                data, ct, _, _ = cached
            else:
                data = file_path.read_bytes()
                ext = file_path.suffix.lower()
                ct = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif', 'webp': 'image/webp'}.get(ext[1:], 'application/octet-stream')
                _meme_cache[key] = (data, ct, st.st_mtime_ns, st.st_size)
                if len(_meme_cache) > _MEME_CACHE_MAX:
                    _meme_cache.clear()
            self.send_response(200)
            self.send_header('Content-Type', ct)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'public, max-age=86400')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return True
    # Fallback: search by filename across meme dirs (slower path, no caching)
    try:
        base = Path(safe_rel).name            # 只取文件名，天然不含上溯
        if base:
            for meme_dir in _meme_dirs():
                if not meme_dir.exists():
                    continue
                for candidate in meme_dir.rglob(base):
                    if not candidate.is_file():
                        continue
                    try:
                        if not _within(candidate.resolve(), meme_dir.resolve()):
                            continue              # 符号链接可能指向目录外，跳过
                    except Exception:
                        continue
                    ext = candidate.suffix.lower()
                    ct = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif', 'webp': 'image/webp'}.get(ext[1:], 'application/octet-stream')
                    data = candidate.read_bytes()
                    self.send_response(200)
                    self.send_header('Content-Type', ct)
                    self.send_header('Content-Length', str(len(data)))
                    self.send_header('Cache-Control', 'public, max-age=86400')
                    self.end_headers()
                    try:
                        self.wfile.write(data)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return True
    except Exception:
        pass
    return False

def _get_html_mtime():
    try:
        m = os.path.getmtime(_frontend_path())
        try:                       # 版本偏好变了也要触发重载
            m = max(m, os.path.getmtime(_UI_VERSION_FILE))
        except Exception:
            pass
        return m
    except Exception:
        return 0

def _load_html():
    global HTML, _html_mtime
    try:
        HTML = _read_frontend(_ui_version())
        _html_mtime = _get_html_mtime()
    except Exception:
        pass


# 
#  HTTP Handler
# 
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _allowed_origin(self):
        """只允许本机来源读取 API（防跨源窃取）：localhost / 127.0.0.1 / ::1 / file://(null)。
        外部网站 origin 一律不返回 ACAO → 浏览器阻止其读取响应。"""
        origin = self.headers.get("Origin", "") or ""
        if not origin:
            return "*"  # 无 Origin（同源 / curl 等）放行
        try:
            o = urlparse(origin)
            host = (o.hostname or "").lower()
            if host in ("localhost", "127.0.0.1", "::1") or origin == "null":
                return origin
        except Exception:
            pass
        return ""

    def _host_allowed(self):
        """DNS rebinding 防护：Host 头必须是本机。"""
        host = (self.headers.get("Host") or "").strip().lower()
        bare = host.split(":")[0]
        return bare in ("localhost", "127.0.0.1", "::1", "")

    def _proxy_agent_post(self, path, data):
        """把 /api/agent/* 的 POST 转发给 bot_core（agent 逻辑都在那边）。

        为什么不做成"webui 自己实现 agent"：设计文档 §3 定了**折中**方案 ——
        逻辑放 harness，bot_core 加薄路由，webui 只转发。这样：
          · agent 只有一份实现（不会两处漂移）；
          · 前端始终只跟一个端口说话（不必知道 3005）；
          · bot 没起来时给出人话错误，而不是超时。
        """
        payload = json.dumps(data or {}, ensure_ascii=False).encode("utf-8")
        # 转发前先探测一次（避免 0.5s 超时把正常的慢请求也判死）
        if not _is_bot_core_alive():
            self._send_json(200, {
                "ok": False,
                "error": "Bot 未运行：Agent 需要 bot_core 提供模型与工具。请先在「Bot 控制」页启动。",
                "hint": "bot_not_running",
            })
            return
        try:
            req = urllib.request.Request(
                f"{BOT_CORE_URL}{path}",
                data=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            # agent 任务可能跑很久（多步工具调用）→ 给足超时
            with urllib.request.urlopen(req, timeout=180) as r:
                raw = r.read()
            try:
                out = json.loads(raw.decode("utf-8"))
            except Exception:
                out = {"ok": False, "error": "agent 返回了非 JSON 数据",
                       "raw": raw[:500].decode("utf-8", "replace")}
            self._send_json(200, out)
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", "replace")[:500]
            except Exception:
                detail = ""
            self._send_json(200, {"ok": False, "error": f"agent 端点返回 {e.code}", "detail": detail})
        except Exception as e:
            self._send_json(200, {"ok": False, "error": f"{type(e).__name__}: {e}"})

    def _proxy_bot_core(self, path, data=None, timeout=30.0):
        """把请求转发给 bot_core（3005）。data=None → GET，否则 POST。

        为什么要有这层：群管理那套接口本来在 3005，旧前端是**跨域直连**
        （`fetch('http://127.0.0.1:3005/...')`）。让 v4 直连有两个问题：
          ① 依赖 bot_core 的 CORS 头，脆弱；
          ② 绕过了 webui 的 Origin/Host 闸门（P0-5 刚收紧过的地方）。
        统一走这里转发，前端只跟 8080 说话 —— 与 Agent 端点同一套模式
        （设计文档 §3「不新增独立进程/端口，复用 webui_server 转发」）。

        ⚠️ 超时必须给足：`GET /api/groups` 会**为每个群调一次成员列表**，
        群里多时很慢。旧 helper `fetch_from_bot_core` 的 0.5s 是为存活探测设的，
        拿来做业务转发必然超时。
        """
        try:
            url = f"{BOT_CORE_URL}{path}"
            if data is None:
                req = urllib.request.Request(url, headers={"Accept": "application/json"})
            else:
                req = urllib.request.Request(
                    url, data=json.dumps(data, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", "replace")
            self._send_json(200, json.loads(body) if body.strip() else {"ok": True})
        except Exception as e:
            # bot 未运行时给可读原因，不静默失败
            self._send_json(200, {"ok": False,
                                  "error": "bot_core 不可达或返回异常: " + str(e),
                                  "bot_core": BOT_CORE_URL,
                                  "hint": "请先在 Bot 页启动机器人"})

    def _proxy_agent_stream(self, raw_path):
        """把 /api/agent/stream（ndjson 长连接）原样透传给 bot_core。

        必须**流式复制**，不能先 read() 再转发 —— 否则前端拿不到实时进度，
        "流式"就退化成"等全部跑完再一次性给"。
        """
        if not _is_bot_core_alive():
            self._send_json(200, {"ok": False,
                                  "error": "Bot 未运行：Agent 流需要 bot_core。",
                                  "hint": "bot_not_running"})
            return
        try:
            req = urllib.request.Request(f"{BOT_CORE_URL}{raw_path}",
                                         headers={"Accept": "application/x-ndjson"})
            with urllib.request.urlopen(req, timeout=330) as up:
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                while True:
                    chunk = up.read(2048)
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except (ConnectionAbortedError, BrokenPipeError):
                        break
        except (ConnectionAbortedError, BrokenPipeError):
            pass
        except Exception as e:
            try:
                self.wfile.write((json.dumps({"type": "error",
                                              "error": f"{type(e).__name__}: {e}"},
                                             ensure_ascii=False) + "\n").encode("utf-8"))
            except Exception:
                pass

    def _send_json(self, status, data):
        try:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            ao = self._allowed_origin()
            if ao:
                self.send_header("Access-Control-Allow-Origin", ao)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Connection", "close")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, BrokenPipeError):
            pass

    def _send_html(self, html):
        try:
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            ao = self._allowed_origin()
            if ao:
                self.send_header("Access-Control-Allow-Origin", ao)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (ConnectionAbortedError, BrokenPipeError):
            pass

    def do_OPTIONS(self):
        if not self._host_allowed():
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(204)
        ao = self._allowed_origin()
        if ao:
            self.send_header("Access-Control-Allow-Origin", ao)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._host_allowed():
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        path = urlparse(self.path).path

        # ── Agent 事件流：长连接透传（放在最前，避免被静态文件/HTML 分支截走）──
        if path == "/api/agent/stream":
            self._proxy_agent_stream(self.path)
            return

        # ── 只读 Agent 端点：GET 也转发（前端刷新时可直读，不必都走 POST）──
        #  /api/agent/status | sessions | tools | memory | history?
        if path in ("/api/agent/status", "/api/agent/sessions", "/api/agent/tools",
                    "/api/agent/memory"):
            bc = fetch_from_bot_core(path)
            if bc is None:
                # bot 没起 → 给一个"能力未知"的占位，前端能正常渲染并提示
                self._send_json(200, {
                    "ok": True,
                    "ready": False,
                    "bot_running": False,
                    "memory": {"available": False, "reason": "Bot 未运行"},
                    "tools": {"total": 0, "by_group": {}},
                    "sessions": [],
                })
            else:
                self._send_json(200, bc)
            return

        if path == "/" or path == "/index.html":
            try:
                mtime = _get_html_mtime()
                if mtime != _html_mtime:
                    _load_html()
                data = HTML
                self._send_html(data)
            except Exception:
                mtime = _get_html_mtime()
                if mtime != _html_mtime:
                    _load_html()
                self._send_html(HTML)
            return

        if path == "/desktop":
            try:
                data = (PROJECT / "desktop-ui" / "index.html").read_text(encoding="utf-8")
                self._send_html(data)
            except Exception:
                self._send_html("<h1>Desktop UI missing</h1><p>Create desktop-ui/index.html</p>")
            return

        # Serve static files from webui directory
        if path.startswith("/assets/") or (path.startswith("/") and not path.startswith("/api/")):
            # 防目录穿越/越权读文件：unquote + realpath 后必须仍位于 webui 目录内，且只放行静态扩展名
            # （此前 `HERE / path.lstrip("/")` 不过滤 `..`，本机任意进程可读 ../bot_config.json 与 model-debug.log）
            rel = unquote(path.lstrip("/"))
            file_path = (HERE / rel).resolve()
            _root = str(HERE.resolve())
            _real = str(file_path)
            in_dir = (_real == _root) or _real.startswith(_root + os.sep)
            if in_dir and file_path.is_file() and file_path.suffix.lower() in _STATIC_EXTS:
                try:
                    data = file_path.read_bytes()
                    ct = "application/javascript"
                    if path.endswith(".css"):
                        ct = "text/css"
                    elif path.endswith(".png"):
                        ct = "image/png"
                    elif path.endswith(".svg"):
                        ct = "image/svg+xml"
                    self.send_response(200)
                    self.send_header("Content-Type", ct)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                except (ConnectionAbortedError, BrokenPipeError):
                    pass

        if path == "/api/status":
            if _STATUS_CACHE["data"]:
                self._send_json(200, _STATUS_CACHE["data"])
                return
            # 优先从 bot_core.py 获取实时数据
            bc = fetch_from_bot_core("/api/status")
            if bc:
                S.merge_bot_core_data(bc)
            with STATE_LOCK:
                stats = {
                    "bot_running": S.bot_running,
                    "bot_uptime": int(time.time() - S.bot_start_time),
                    "messages_total": S.messages_total,
                    "groups_active": S.groups_active,
                    "images_generated": S.images_generated,
                    "memes_sent": S.memes_sent,
                    "plugin_count": S.plugin_count,
                    "plugin_active": S.plugin_active,
                    "llm_status": S.llm_status,
                    "llm_model": S.llm_model,
                    "memory_total": S.memory_total,
                    "session_count": S.session_count,
                    "api_calls_today": S.api_calls_today,
                    "tokens_used": S.tokens_used,
                }
            sys_stats = get_system_stats()
            stats["cpu_usage"] = sys_stats["cpu"]
            stats["mem_usage"] = sys_stats["memory"]
            stats.update(_protocol_hud())      # 协议驱动/运行态（与缓存路径同一份口径）
            self._send_json(200, {"data": stats})
            return

        if path == "/api/gallery":
            images = get_image_gallery()
            self._send_json(200, {"images": images})
            return

        if path == "/api/memory":
            sl = get_self_learning_stats()
            ms = get_memory_stats()
            exprs = get_expressions()
            data = {
                "memory_total": sl["total_messages"],
                "learned_messages": sl["total_messages"],
                "active_groups": sl["groups"],
                "storage_kb": ms["size_kb"],
                "expressions": exprs or [
                    {"text": "666 太强了", "scene": "praise", "score": 85},
                    {"text": "笑死我了", "scene": "funny", "score": 80},
                    {"text": "确实", "scene": "chat", "score": 75},
                ]
            }
            self._send_json(200, {"data": data})
            return

        if path == "/api/memory/list":
            # T8: 私聊记忆已迁入 super_memory SQLite
            try:
                import sqlite3
                db_path = PROJECT / "plugins" / "ego" / "super_memory" / "data" / "super_memory.db"
                items = []
                if db_path.exists():
                    conn = sqlite3.connect(str(db_path))
                    rows = conn.execute(
                        "SELECT scope_id, COUNT(*), MAX(timestamp) FROM messages WHERE scope='private' GROUP BY scope_id"
                    ).fetchall()
                    conn.close()
                    for scope_id, cnt, last_ts in rows:
                        last = ""
                        if last_ts:
                            last = datetime.datetime.fromtimestamp(last_ts).strftime("%Y-%m-%d %H:%M")
                        items.append({"qq": scope_id, "summary": f"{cnt} 轮 · 最后 {last}", "rounds": cnt})
                items.sort(key=lambda x: x["qq"])
            except Exception:
                items = []
            self._send_json(200, {"items": items})
            return

        if path == "/api/memory/detail":
            qq = ""
            try:
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query)
                qq = (qs.get("qq") or [""])[0]
            except Exception:
                qq = ""
            if not qq:
                self._send_json(200, {"error": "missing qq"})
                return
            # T8: 私聊记忆已迁入 super_memory SQLite
            try:
                import sqlite3
                db_path = PROJECT / "plugins" / "ego" / "super_memory" / "data" / "super_memory.db"
                rounds = []
                if db_path.exists():
                    conn = sqlite3.connect(str(db_path))
                    rows = conn.execute(
                        "SELECT user_msg, bot_reply, timestamp FROM messages WHERE scope='private' AND scope_id=? ORDER BY timestamp ASC",
                        (str(qq),),
                    ).fetchall()
                    conn.close()
                    for user_msg, bot_reply, ts in rows:
                        time_str = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""
                        rounds.append({"time": time_str, "user": user_msg, "bot": bot_reply})
                self._send_json(200, {"qq": qq, "rounds": rounds})
            except Exception as e:
                self._send_json(200, {"qq": qq, "rounds": [], "error": str(e)})
            return

        if path == "/page":
            plugin_id = ""
            try:
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query)
                plugin_id = (qs.get("plugin_id") or [""])[0]
            except Exception:
                plugin_id = ""
            if not plugin_id:
                self._send_json(200, {"error": "missing plugin_id"})
                return
            ui_path = PROJECT / "plugins" / plugin_id / "ui.html"
            if not ui_path.exists():
                self._send_json(200, {"error": "plugin ui not found", "plugin_id": plugin_id})
                return
            try:
                html = ui_path.read_text(encoding="utf-8")
                self._send_html(html)
            except Exception as e:
                self._send_json(200, {"error": str(e)})
            return

        # ── 群/好友管理转发（T8）：这套接口在 bot_core(3005)，统一从 8080 转发 ──
        #  MCP 端点（T2）同样在 bot_core(3005)；GET 用 self.path 保 query。
        if path.startswith("/api/groups") or path.startswith("/api/friends") \
                or path.startswith("/api/admin/notify_log") \
                or path.startswith("/api/mcp/"):
            self._proxy_bot_core(self.path)
            return

        # ── 前端版本（只读）──
        if path == "/api/ui/version":
            self._send_json(200, {"version": _ui_version(),
                                  "available": [k for k in FRONTENDS if FRONTENDS[k].is_file()]})
            return

        if path == "/api/plugins/action":
            # P0-5：state-changing 端点收紧为 POST-only。
            #
            # 此前 do_GET 与 do_POST 各有一份**逐行重复**的实现（476 vs 470 行），
            # 而 do_POST 才带 Origin 闸门（防 CSRF），do_GET 只挡了 Host（防 DNS rebinding）
            # → 恶意网页可用 fetch/XHR 的 GET 绕过闸门触发副作用。
            #
            # 处理动作而不是打补丁，理由：
            #   1. 修 HTTP 语义错误本身 —— GET 不应有副作用（RFC 7231 语义）；
            #   2. 逐条比对后确认 do_POST 的实现是 do_GET 的**严格超集**，
            #      唯一差异是 action="style_features"，已移植到 do_POST（能力不丢）；
            #   3. 全库 121 个调用点**全部**用 POST（无 method GET、无 query 拼装），
            #      故不存在破坏现有前端的风险；
            #   4. 顺带消掉 476 行重复实现（原来两处会各自漂移）。
            self.send_response(405)
            self.send_header("Allow", "POST")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/api/plugins":
            bc = fetch_from_bot_core("/api/plugins")
            if bc and bc.get("plugins"):
                # 过滤适配层壳（chat_style/slang_learner/super_memory 转发到 ego，不在列表单独显示）
                self._send_json(200, {"plugins": [p for p in bc["plugins"] if not (p or {}).get("adapter")]})
                return
            _plugins = []
            try:
                plugin_dir = PROJECT / "plugins"
                if plugin_dir.exists():
                    state_file = plugin_dir / "plugin_state.json"
                    state = {}
                    if state_file.exists():
                        try:
                            state = json.loads(state_file.read_text(encoding="utf-8"))
                        except Exception:
                            pass
                    for entry in sorted(plugin_dir.iterdir()):
                        if not entry.is_dir() or entry.name.startswith('_') or entry.name.startswith('.'):
                            continue
                        if entry.name == "plugin_manager.py":
                            continue
                        init_file = entry / "__init__.py"
                        if not init_file.exists():
                            continue
                        try:
                            meta = {"name": entry.name, "version": "1.0.0", "description": "", "author": "", "hooks": [], "priority": 50, "enabled": False}
                            content_py = init_file.read_text(encoding="utf-8", errors="replace")
                            # 老式插件读 PLUGIN_META，真插件（ABI）读 PLUGIN_MANIFEST
                            for _kw in ("PLUGIN_META = ", "PLUGIN_MANIFEST = "):
                                idx = content_py.find(_kw)
                                if idx < 0:
                                    continue
                                bp = content_py.find("{", idx)
                                if bp < 0:
                                    continue
                                depth = 0
                                for ci in range(bp, len(content_py)):
                                    if content_py[ci] == '{': depth += 1
                                    elif content_py[ci] == '}': depth -= 1
                                    if depth == 0:
                                        dstr = content_py[bp:ci+1]
                                        try:
                                            ns = {"__builtins__": {}, "enabled": False}
                                            exec("__pm__ = " + dstr, ns)
                                            parsed = ns.get("__pm__", {})
                                            if isinstance(parsed, dict):
                                                meta.update(parsed)
                                        except Exception:
                                            pass
                                        break
                                break
                            if not meta.get("description"):
                                dm = re.search(r'"""(.*?)"""', content_py, re.DOTALL)
                                if dm:
                                    desc = dm.group(1).strip().split(chr(10))[0]
                                    if len(desc) > 5:
                                        meta["description"] = desc[:120]
                            # 适配层壳标记：PLUGIN_META 可能是 `dict(...)` 写法（文本解析器读不到字面量），
                            # 直接按文件里是否出现 adapter 键判定
                            if 'adapter' in content_py:
                                meta["adapter"] = True
                        except Exception:
                            pass
                        em = state.get("enabled", state.get("已启用", {})) if isinstance(state, dict) else {}
                        enabled = em.get(meta.get("name", entry.name), meta.get("enabled", meta.get("已启用", False)))
                        # 适配层壳（转发到 ego 子模块）不在插件列表单独显示
                        if meta.get("adapter"):
                            continue
                        _plugins.append({
                            "name": meta.get("name", entry.name),
                            "version": meta.get("version", "1.0.0"),
                            "description": meta.get("description", ""),
                            "author": meta.get("author", ""),
                            "hooks": meta.get("hooks", []),
                            "priority": meta.get("priority", 50),
                            "page": meta.get("page", ""),
                            "plugin_page": meta.get("plugin_page", ""),
                            "ui": meta.get("ui", ""),
                            "tags": meta.get("tags", []) if isinstance(meta.get("tags", []), list) else [],
                            "category": meta.get("category", ""),
                            "enabled": enabled,
                        })
            except Exception:
                pass
            with STATE_LOCK:
                S.plugin_count = len(_plugins)
                S.plugin_active = sum(1 for p in _plugins if p["enabled"])
            self._send_json(200, {"plugins": _plugins})
            return

        if path == "/api/scope-policy":
            # P4 · S6：作用域能力档位 —— 只读查询（预置/词汇/统计/审计留痕）。
            # 写入端点留给 S5（那一半要等前端风格定了一起做）。
            try:
                sys.path.insert(0, str(PROJECT))
                from harness.scope_registry import ScopeRegistry
                reg = ScopeRegistry()
                out = {
                    "default_preset": reg.default_preset(),
                    "presets": reg.presets(),
                    "capabilities": reg.capabilities(),
                    "stats": reg.stats(),
                    "audit": reg.audit(50),
                }
                _q = parse_qs(urlparse(self.path).query)
                _sc = (_q.get("scope") or [""])[0].strip()
                if _sc and ":" in _sc:
                    _kind, _sid = _sc.split(":", 1)
                    out["resolved"] = {
                        "scope": _sc,
                        "preset": reg.resolve(_kind, _sid).get("preset"),
                        "named_preset": reg.resolve(_kind, _sid).get("named_preset"),
                        "overrides": reg.overrides(_kind, _sid),
                        "allowed": sorted(reg.resolve(_kind, _sid).get("capabilities") or []),
                    }
                self._send_json(200, out)
            except Exception as e:
                self._send_json(200, {"error": str(e), "presets": {}, "audit": []})
            return

        if path == "/api/token-stats":
            # ★ 会话级计数（群聊/私聊/网页）归 **bot_core** —— token_stats.record()
            #   只在 bot_core 里被调用；本进程（webui_server）自己的 _cumulative 恒 0。
            #   以前直接读本进程 → Token 页三行永远 0（用户截图："token 页空了"）。
            #   先问 bot_core（超时放宽到 3s：它要读 jsonl 做日/窗口聚合），
            #   拿不到（Bot 没起）才退回本地 —— 本地仍有按日聚合，页面不会全空。
            bc = fetch_from_bot_core("/api/token-stats", timeout=3.0)
            if bc and isinstance(bc.get("snapshot"), dict) and bc["snapshot"]:
                self._send_json(200, {
                    "snapshot": bc.get("snapshot") or {},
                    "recent": bc.get("recent") or [],
                    "context_pressure": bc.get("context_pressure") or [],
                    "source": "bot_core",
                })
                return
            try:
                sys.path.insert(0, str(PROJECT))
                from token_stats import snapshot as _ts_snapshot, history as _ts_history
                # P23：一并回上下文压力（锚点式测量）。按压力降序 → 前端可一眼看出"谁快满了"。
                try:
                    from token_stats import context_pressure as _ts_pressure
                    _ctx = _ts_pressure()
                except Exception:
                    _ctx = []
                self._send_json(200, {"snapshot": _ts_snapshot(), "recent": _ts_history(50),
                                      "context_pressure": _ctx, "source": "local"})
            except Exception as e:
                self._send_json(200, {"error": str(e), "snapshot": {}, "recent": [],
                                      "context_pressure": [], "source": "local"})
            return

        if path == "/api/providers/catalog":
            # 快速切模型下拉数据源：providers（含模型目录 + last_model）+ 当前 provider/model；
            # 不回传任何 Key。模型目录由 /api/providers/models 拉取成功后自动持久化，
            # 配置里没有就为空（用户到设置页拉一次即永久记住），不做静态目录兑底。
            cfg = load_config()
            provs = []
            for _p in (cfg.get("providers") or []):
                if not isinstance(_p, dict):
                    continue
                provs.append({
                    "id": str(_p.get("id") or ""),
                    "name": str(_p.get("name") or _p.get("id") or ""),
                    "models": [m for m in (_p.get("models") or []) if m],
                    "last_model": str(_p.get("last_model") or ""),
                })
            self._send_json(200, {
                "providers": provs,
                "api_provider": cfg.get("api_provider") or "",
                "model_name": cfg.get("model_name") or "",
            })
            return

        if path == "/api/config":
            cfg = load_config()
            display = dict(cfg)
            providers = []
            try:
                # 优先回传 bot_config.json 中已保存的提供商（用户在「多提供商管理」里的编辑），
                # 其次才是静态目录 webui/providers.json —— 否则用户新增/改动的提供商刷新即丢失
                saved = cfg.get("providers") or []
                if saved and isinstance(saved, list):
                    providers = [dict(p) for p in saved if isinstance(p, dict)]
                if not providers:
                    providers_path = PROJECT / "webui" / "providers.json"
                    if providers_path.exists():
                        providers = json.loads(providers_path.read_text(encoding="utf-8"))
            except Exception:
                providers = [
                    {"id": "deepseek", "name": "DeepSeek", "models": [{"id": "deepseek-flash", "name": "DeepSeek Chat"}]},
                    {"id": "openai", "name": "OpenAI", "models": [{"id": "gpt-4o", "name": "GPT-4o"}]},
                    {"id": "custom", "name": "Custom", "models": [{"id": "", "name": "Custom Endpoint"}]},
                ]
            display["providers"] = providers
            for _p in display.get("providers", []) or []:
                if isinstance(_p, dict):
                    # models 目录随 providers 回传（仅模型 id/name，非敏感）：
                    # 前端保存时整组写回，避免“显示层剥掉 → 保存写空 → 目录永久丢失”的链条
                    if _p.get("api_key"):
                        _p["api_key"] = _mask_secret(_p["api_key"])
                    # 标记该提供商是否已在凭据管理器保存专属 Key（前端据此显示"清除 Key"入口）
                    try:
                        from config import provider_cred_name
                        from cred_store import exists_cred
                        _p["has_api_key"] = bool(_p.get("api_key")) or exists_cred(provider_cred_name(_p.get("id") or ""))
                    except Exception:
                        _p["has_api_key"] = False
            # 敏感值掩码：api_key / onebot_token 不回传明文（凭据管理器持有真实值）
            if display.get("api_key"):
                display["api_key"] = _mask_secret(display["api_key"])
            if display.get("onebot_token"):
                display["onebot_token"] = _mask_secret(display["onebot_token"])
            self._send_json(200, {"config": display})
            return

        if path == "/api/tools":
            self._send_json(200, {"tools": _tools_catalog()})
            return

        if path == "/api/bot/status":
            self._send_json(200, {"data": get_bot_status()})
            return

        if path == "/api/protocol/status":
            mgr = getattr(S, '_protocol_manager', None)
            running = bool(mgr and getattr(mgr, '_running', False))
            try:
                from protocols import registry
                drivers = registry.get_driver_ids()
            except Exception:
                drivers = ["napcat"]
            cfg = load_config()
            driver = cfg.get("protocol_driver", "napcat") or "napcat"
            status = {}
            if mgr:
                try:
                    status = mgr.get_status() or {}
                except Exception:
                    status = {}
            napcat_cfg = (cfg.get("protocol_drivers") or {}).get("napcat") or {}
            llonebot_cfg = (cfg.get("protocol_drivers") or {}).get("llonebot") or {}
            snowluma_cfg = (cfg.get("protocol_drivers") or {}).get("snowluma") or {}
            # ★ 协议 WebUI 地址必须【抓取】，不能写死：
            #   ① 先问当前适配器 —— 各驱动的 get_webui_info() 会去读它自己的
            #      config/webui.json 与启动日志（NapCat/LLOneBot 取 token、
            #      SnowLuma 从日志抓初始密码），返回 {found,host,port,url,token,user?}
            #   ② 取不到再退回该驱动配置里的 webui_port（config.py 三份 drivers 都有）
            #   ⚠ 绝不能拿 api_port（OneBot API，默认 3000）当 WebUI 端口 ——
            #     那是两回事：SnowLuma WebUI 在 5099、NapCat 在 6099、LLOneBot 在 7000。
            webui_info = {}
            try:
                _ad = getattr(mgr, "adapter", None) if mgr else None
                if _ad is not None and hasattr(_ad, "get_webui_info"):
                    webui_info = _ad.get_webui_info() or {}
            except Exception:
                webui_info = {}
            if not webui_info.get("url"):
                _drv_cfg = (cfg.get("protocol_drivers") or {}).get(driver) or {}
                _wp = _drv_cfg.get("webui_port")
                if _wp:
                    webui_info = {"found": False, "host": "127.0.0.1", "port": int(_wp),
                                  "url": f"http://127.0.0.1:{int(_wp)}", "token": "",
                                  "source": "config"}
            # 真实在线状态（2026-10-19）：`protocol_running` 只表示"有没有从本界面
            # 点过启动"，外部启动的 NapCat/LLOneBot 不经过它 → 会误报"已停止"。
            # 这里补一个真实探测结果，前端应优先用它。
            _p_ok, _p_url = probe_protocol_online()
            self._send_json(200, {
                "protocol_running": running,
                "protocol_online": _p_ok,
                "protocol_url": _p_url,
                "protocol_driver": driver,
                "drivers": drivers,
                "api_port": cfg.get('protocol_api_port', 3000),
                "event_port": cfg.get('protocol_event_port', 3005),
                "webui": webui_info,
                "qq_path": _get_qq_path(),
                "status": status,
                "napcat_config": napcat_cfg,
                "llonebot_config": llonebot_cfg,
                "snowluma_config": snowluma_cfg,
                "risks": PROTO_RISK_INFO,
            })
            return

        if path == "/api/protocol/config":
            cfg = load_config()
            drivers = (cfg.get("protocol_drivers") or {})
            self._send_json(200, {
                "protocol_driver": cfg.get("protocol_driver", "napcat"),
                "protocol_drivers": drivers,
            })
            return

        if path == "/api/protocol/logs":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = {}
                if length > 0:
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                body = {}
            limit = int(body.get("limit", 200)) if isinstance(body, dict) else 200
            self._send_json(200, {"logs": _collect_logs(limit)})
            return

        if path == "/api/imagegen/status":
            cfg = load_config()
            comfyui_running = False
            comfyui_host = cfg.get("comfyui_host", "127.0.0.1")
            comfyui_port = cfg.get("comfyui_port", 8000)
            try:
                req = urllib.request.Request(f"http://{comfyui_host}:{comfyui_port}/system_stats", headers={"Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=3) as r:
                    comfyui_running = True
            except Exception:
                comfyui_running = False
            self._send_json(200, {
                "comfyui_running": comfyui_running,
                "comfyui_host": comfyui_host,
                "comfyui_port": comfyui_port,
                "comfyui_timeout": cfg.get("comfyui_timeout", 120),
                "comfyui_default_width": cfg.get("comfyui_default_width", 1024),
                "comfyui_default_height": cfg.get("comfyui_default_height", 1536),
                "comfyui_checkpoint": cfg.get("comfyui_checkpoint", ""),
                "workflow_path": cfg.get("workflow_path", ""),
                "prompt_node_id": cfg.get("prompt_node_id", "353"),
                "llm_model": "local" if cfg.get("use_local_image_llm") else "deepseek",
                "llm_url": cfg.get("llm_url", cfg.get("api_url", "https://api.deepseek.com/chat/completions")),
                "nsfw_enabled": cfg.get("nsfw_enabled", True),
            })
            return

        # --- Meme APIs ---
        if path == "/api/memes":
            try:
                memes = []
                tag_db = {}
                for meme_dir in [PROJECT / 'memes' / 'memes', PROJECT.parent / 'memes' / 'memes']:
                    if not meme_dir.exists():
                        continue
                    tags_path = meme_dir / 'tags.json'
                    if tags_path.exists():
                        try:
                            raw = json.loads(tags_path.read_text(encoding='utf-8'))
                            if isinstance(raw, dict):
                                for k, v in raw.items():
                                    if isinstance(v, dict):
                                        v = v.get('tags', [])
                                    tag_db[str(k)] = [str(t) for t in (v or []) if t is not None]
                        except Exception:
                            pass
                    for fpath in meme_dir.rglob('*'):
                        if not fpath.is_file() or fpath.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
                            continue
                        rel = fpath.relative_to(meme_dir).as_posix()
                        tags = []
                        tag_file = fpath.with_suffix('.json')
                        if tag_file.exists():
                            try:
                                tags = json.loads(tag_file.read_text(encoding='utf-8')).get('tags', [])
                            except Exception:
                                pass
                        if not tags:
                            tags = tag_db.get(rel, [])
                        memes.append({'name': rel, 'url': rel, 'tags': tags})
                self._send_json(200, {'memes': memes})
            except Exception as e:
                self._send_json(200, {'error': str(e), 'memes': []})
            return

        if path == "/api/memes/emotions":
            emotions = set()
            for meme_dir in [PROJECT / 'memes' / 'memes', PROJECT.parent / 'memes' / 'memes']:
                if not meme_dir.exists():
                    continue
                # Read tags from individual .json sidecar files
                for root_dir, dirs, files in os.walk(meme_dir):
                    for fname in files:
                        if fname.lower().endswith('.json') and fname.lower() != 'tags.json':
                            try:
                                with open(os.path.join(root_dir, fname), 'r', encoding='utf-8') as tf:
                                    data = json.load(tf)
                                    for t in (data.get('tags') or []):
                                        emotions.add(str(t))
                            except Exception: pass
                # Also read from main tags.json database
                tags_path = meme_dir / 'tags.json'
                if tags_path.exists():
                    try:
                        with open(tags_path, 'r', encoding='utf-8') as tf:
                            tag_db = json.load(tf)
                            for tags in tag_db.values():
                                if isinstance(tags, dict):
                                    tags = tags.get('tags', [])
                                for t in (tags or []):
                                    emotions.add(str(t))
                    except Exception: pass
            self._send_json(200, {'emotions': sorted(emotions)})
            return

        # Serve meme images statically
        if path.startswith('/memes/'):
            rel = path[len('/memes/'):]
            # Normalize accidental backslashes from any client-side path handling
            rel = rel.replace('\\\\', '/')
            # Decode percent-encoded UTF-8 paths
            try:
                rel = unquote(rel)
            except Exception:
                pass
            # On Windows the request path may arrive as raw UTF-8 bytes decoded to str
            # or as latin1 mojibake; normalize to proper filesystem path.
            try:
                if isinstance(rel, str):
                    rel = rel.encode('latin1').decode('utf8')
            except Exception:
                pass
            if _serve_meme(self, rel):
                return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if not self._host_allowed():
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        # 防 CSRF：浏览器跨站页面（恶意网页）发起的请求会带非本机 Origin，一律拒绝。
        # 无 Origin 的本地进程/curl 不受影响（本机进程本身即可读写本机文件，威胁模型一致）。
        _origin = self.headers.get("Origin") or ""
        if _origin:
            try:
                from urllib.parse import urlsplit
                _oh = urlsplit(_origin).hostname or ""
            except Exception:
                _oh = ""
            if _oh and _oh not in ("127.0.0.1", "localhost", "::1"):
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            with open(str(HERE / 'model-debug.log'), 'a', encoding='utf-8') as df:
                df.write('[POST] path=' + str(path) + '\n')
        except Exception:
            pass
        data = {}
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 0:
                body = self.rfile.read(length).decode("utf-8")
                data = json.loads(body)
        except Exception:
            pass

        # ── Agent 端点转发（前端只跟 webui_server 说话，agent 逻辑在 bot_core）──
        #  设计文档 §3 明确：「不新增独立进程/端口（复用 webui_server 转发）」。
        #  bot 未运行时返回一个可读的错误（前端能提示"先启动 Bot"），而不是静默失败。
        if path.startswith("/api/agent/"):
            self._proxy_agent_post(path, data)
            return

        if path == "/api/imagegen/generate":
            payload = dict(data or {})
            prompt = (payload.get("prompt") or "").strip()
            negative_prompt = (payload.get("negative_prompt") or "").strip()
            width = int(payload.get("width") or 1024)
            height = int(payload.get("height") or 1536)
            steps = int(payload.get("steps") or 24)
            cfg = float(payload.get("cfg") or 7)
            seed = int(payload.get("seed") or 42)
            style = (payload.get("style") or "default").strip()
            llm_model = (payload.get("llm_model") or "deepseek").strip()
            llm_url = (payload.get("llm_url") or "").strip()
            comfyui_host = (payload.get("comfyui_host") or "127.0.0.1").strip()
            comfyui_port = int(payload.get("comfyui_port") or 8000)
            comfyui_timeout = int(payload.get("comfyui_timeout") or 120)
            comfyui_checkpoint = (payload.get("comfyui_checkpoint") or "").strip()
            workflow_path = (payload.get("workflow_path") or "").strip()
            prompt_node_id = (payload.get("prompt_node_id") or "353").strip()
            nsfw_enabled = bool(payload.get("nsfw_enabled", True))
            if not prompt:
                self._send_json(200, {"success": False, "error": "prompt 为空"})
                return
            try:
                from comfyui_client import (
                    get_comfyui_client,
                    load_workflow,
                    set_prompt_in_workflow,
                )
            except Exception as e:
                self._send_json(200, {"success": False, "error": f"ComfyUI 模块未找到: {e}"})
                return
            try:
                client = get_comfyui_client({
                    "comfyui_host": comfyui_host,
                    "comfyui_port": comfyui_port,
                    "comfyui_timeout": comfyui_timeout,
                })
                if not client.is_connected():
                    self._send_json(200, {"success": False, "error": "ComfyUI 未连接"})
                    return
                api_workflow = {}
                if workflow_path and Path(workflow_path).exists():
                    api_workflow = load_workflow(workflow_path)
                    api_workflow = set_prompt_in_workflow(api_workflow, prompt, prompt_node_id)
                else:
                    api_workflow = {
                        "1": {"class_type": "LoadImage", "inputs": {"image": "example.png", "upload": "image"}},
                        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": comfyui_checkpoint or "anima_baseV10.safetensors", "weight_dtype": "default"}},
                        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_06b_base.safetensors", "type": "stable_diffusion", "device": "default"}},
                        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["3", 0]}},
                        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": (negative_prompt or "lowres, bad anatomy, bad hands, text, error, worst quality, low quality, jpeg artifacts, signature, watermark"), "clip": ["3", 0]}},
                        "6": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}},
                        "7": {"class_type": "VAEEncode", "inputs": {"pixels": ["1", 0], "vae": ["6", 0]}},
                        "8": {"class_type": "KSampler", "inputs": {"model": ["2", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["7", 0], "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": "normal", "denoise": 0.6 if style == "img2img" else 1.0}},
                        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["6", 0]}},
                        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": "EggBloom"}},
                    }
                prompt_id = client.queue_prompt(api_workflow)
                try:
                    local_paths = client.wait_and_get_images(prompt_id, timeout=comfyui_timeout)
                    image_url = ""
                    if local_paths:
                        first = local_paths[0]
                        if first.startswith("http://"):
                            image_url = first
                        else:
                            image_url = f"file:///{first.replace(chr(92), '/')}"
                    self._send_json(200, {"success": True, "prompt_id": prompt_id, "image_url": image_url, "prompt": prompt, "width": width, "height": height, "workflow_path": workflow_path})
                    return
                except Exception as e:
                    self._send_json(200, {"success": True, "prompt_id": prompt_id, "image_url": "", "error": f"队列已提交，下载失败: {e}"})
                    return
            except Exception as e:
                self._send_json(200, {"success": False, "error": str(e)})
                return

        if path == "/api/imagegen/test":
            payload = dict(data or {})
            host = (payload.get("host") or "127.0.0.1").strip()
            port = int(payload.get("port") or 8000)
            timeout = int(payload.get("timeout") or 3)
            try:
                url = f"http://{host}:{port}/system_stats"
                req = urllib.request.Request(url, headers={"Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    resp_data = json.loads(r.read().decode("utf-8"))
                self._send_json(200, {"success": True, "comfyui_version": resp_data.get("system", {}).get("comfyui_version", "")})
                return
            except Exception as e:
                self._send_json(200, {"success": False, "output": str(e)})
                return

        if path == "/api/imagegen/workflow":
            payload = dict(data or {})
            filename = (payload.get("filename") or "").strip()
            content = payload.get("content")
            save_dir = PROJECT / "webui" / "uploaded_workflows"
            try:
                save_dir.mkdir(parents=True, exist_ok=True)
                safe_name = re.sub(r'[^A-Za-z0-9_.-]+', '_', filename) or "workflow.json"
                save_path = save_dir / safe_name
                if isinstance(content, str):
                    open(str(save_path), "w", encoding="utf-8").write(content)
                else:
                    save_path.write_bytes(content)
                self._send_json(200, {"success": True, "path": str(save_path)})
                return
            except Exception as e:
                self._send_json(200, {"success": False, "output": str(e)})
                return

        if path == "/api/config/reload":
            result = push_bot_reload()
            self._send_json(200, {"ok": True, "bot": result})
            return

        if path == "/api/config":
            cfg = load_config()
            update = {k: v for k, v in data.items() if k not in ("api_key", "api_key_clear")}
            # 清除 Key：api_key_clear 传提供商 id（清专属凭据）或 "_global_"（清全局 Key）
            clear_target = str(data.get("api_key_clear") or "").strip()
            if clear_target:
                try:
                    from config import clear_provider_api_key
                    if clear_target == "_global_":
                        from cred_store import MODEL_API_KEY_CRED, delete_cred
                        delete_cred(MODEL_API_KEY_CRED)
                    else:
                        clear_provider_api_key(clear_target)
                except Exception:
                    pass
            if "group_tool_allowlist" in update and not update["group_tool_allowlist"]:
                # 空名单 = 恢复默认群聊工具（删除配置键，回到 GROUP_TOOLS）
                cfg.pop("group_tool_allowlist", None)
            # 空闲退场静默阈值下限保护：避免前端误填 0 导致 Bot 刚回复完就触发“退场”
            if "group_idle_exit_min_silence" in update:
                try:
                    sil = int(update["group_idle_exit_min_silence"] or 0)
                except (TypeError, ValueError):
                    sil = 0
                update["group_idle_exit_min_silence"] = max(sil, 30)
            # 每提供商 API Key：写入凭据管理器专属条目（EggBloom.MODEL_API_KEY_{id}），
            # providers 数组只存 id/URL/模型，不落盘明文 Key；旧明文 Key 在此自动迁移。
            if isinstance(update.get("providers"), list):
                try:
                    from config import save_provider_api_key, clear_provider_api_key
                except Exception:
                    save_provider_api_key = clear_provider_api_key = None
                old_provider_ids = [str(p.get("id")) for p in (cfg.get("providers") or []) if isinstance(p, dict)]
                cleaned_providers = []
                for _p in update["providers"]:
                    if not isinstance(_p, dict):
                        continue
                    _p = dict(_p)
                    _pid = str(_p.get("id") or "")
                    _pk = _p.get("api_key") or ""
                    _clear = bool(_p.pop("_clear_key", False))
                    _p.pop("has_api_key", None)  # 展示用标记，不落盘
                    if save_provider_api_key is not None:
                        if _clear:
                            clear_provider_api_key(_pid)
                        elif _pk and not _is_redacted_key(_pk):
                            save_provider_api_key(_pid, _pk)
                        elif _pk:
                            # 前端只回传掩码占位符：若旧配置里存有该提供商明文 Key，迁移进凭据管理器
                            _old_entry = next((p for p in (cfg.get("providers") or [])
                                               if isinstance(p, dict) and str(p.get("id")) == _pid), None)
                            _old_plain = (_old_entry or {}).get("api_key") or ""
                            if _old_plain and not _is_redacted_key(_old_plain):
                                save_provider_api_key(_pid, _old_plain)
                    _p.pop("api_key", None)  # 不落盘明文
                    cleaned_providers.append(_p)
                # 被删除的提供商：清理其专属凭据，避免残留 Key 占位
                if save_provider_api_key is not None:
                    kept_ids = [str(x.get("id")) for x in cleaned_providers]
                    for _oid in old_provider_ids:
                        if _oid and _oid not in kept_ids:
                            try:
                                clear_provider_api_key(_oid)
                            except Exception:
                                pass
                update["providers"] = cleaned_providers
            cfg.update(update)
            if data.get("api_key") and not _is_redacted_key(data.get("api_key")):
                # 新 key 写入凭据管理器，不落盘明文
                try:
                    from cred_store import MODEL_API_KEY_CRED, write_cred, read_cred
                    new_key = str(data["api_key"]).strip()
                    if new_key and "YOUR" not in new_key:
                        write_cred(MODEL_API_KEY_CRED, new_key)
                        cfg["api_key"] = read_cred(MODEL_API_KEY_CRED) or new_key
                except Exception:
                    pass
            cfg.pop("api_key", None)  # save_config 时不写明文
            success = save_config(cfg)
            reload_result = None
            if success:
                CONFIG_CACHE.update(cfg)
                reload_result = push_bot_reload()  # 配置变更后通知 bot_core 热重载
            self._send_json(200, {"success": success, "reload": reload_result})
            return

        if path == "/api/llama/test":
            body = dict(data or {})
            driver = (body.get("driver") or "").strip().lower()
            model_path = (body.get("model_path") or "").strip()
            port = int(body.get("port") or 8081)
            model_name = (body.get("model_name") or "").strip()
            use_local = bool(body.get("use_local_llm"))
            try:
                if not use_local:
                    self._send_json(400, {"ok": False, "error": "未启用本地 LLM"})
                    return
                if driver == "ollama":
                    base_url = f"http://127.0.0.1:{port}/v1/chat/completions"
                    req = urllib.request.Request(base_url, data=json.dumps({"model": model_name or "llama3", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 4}).encode("utf-8"), headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=15) as r:
                        resp = json.loads(r.read().decode("utf-8"))
                    self._send_json(200, {"ok": True, "driver": driver, "api_url": base_url, "model_name": model_name or "llama3", "error": None})
                    return
                if driver == "lmstudio":
                    base_url = f"http://127.0.0.1:{port}/v1/chat/completions"
                    req = urllib.request.Request(base_url, data=json.dumps({"model": model_name or "local-model", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 4}).encode("utf-8"), headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=15) as r:
                        resp = json.loads(r.read().decode("utf-8"))
                    self._send_json(200, {"ok": True, "driver": driver, "api_url": base_url, "model_name": model_name or "local-model", "error": None})
                    return
                if driver == "llama-server":
                    _LLAMA_SERVER_EXE = (body.get("exe_path") or "").strip()
                    _LLAMA_MODEL = model_path or ""
                    _LLAMA_PORT = port or 8081
                    if not _LLAMA_SERVER_EXE:
                        self._send_json(400, {"ok": False, "driver": driver, "error": "未填写 llama-server 可执行文件路径"})
                        return
                    if not os.path.exists(_LLAMA_SERVER_EXE):
                        self._send_json(400, {"ok": False, "driver": driver, "error": f"llama-server 不存在: {_LLAMA_SERVER_EXE}"})
                        return
                    if not _LLAMA_MODEL:
                        self._send_json(400, {"ok": False, "driver": driver, "error": "未填写模型 .gguf 路径"})
                        return
                    if not os.path.exists(_LLAMA_MODEL):
                        self._send_json(400, {"ok": False, "driver": driver, "error": f"模型文件不存在: {_LLAMA_MODEL}"})
                        return
                    ready = False
                    try:
                        req = urllib.request.Request(f"http://127.0.0.1:{_LLAMA_PORT}/health")
                        with urllib.request.urlopen(req, timeout=5) as resp:
                            ready = resp.status == 200
                    except Exception:
                        ready = False
                    if not ready:
                        stderr_log = HERE / "llama-server-stderr.log"
                        try:
                            with open(stderr_log, "w", encoding="utf-8", errors="replace") as sf:
                                p = subprocess.Popen(
                                    [_LLAMA_SERVER_EXE, "--model", _LLAMA_MODEL, "--host", "127.0.0.1", "--port", str(_LLAMA_PORT), "-ngl", "999", "--cpu-moe", "--no-mmap", "--ctx-size", "32768"],
                                    stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=sf,
                                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                                )
                        except Exception as e:
                            self._send_json(400, {"ok": False, "driver": driver, "error": f"llama-server 启动失败: {e}"})
                            return
                        for i in range(90):
                            time.sleep(1)
                            try:
                                req = urllib.request.Request(f"http://127.0.0.1:{_LLAMA_PORT}/health")
                                with urllib.request.urlopen(req, timeout=3) as resp:
                                    if resp.status == 200:
                                        ready = True
                                        break
                            except Exception as e:
                                try:
                                    with open(str(HERE / "llama-health-debug.log"), "a", encoding="utf-8") as db:
                                        db.write(f"[{time.strftime('%H:%M:%S')}] iter {i} health err: {type(e).__name__}: {e}\n")
                                except Exception:
                                    pass
                            if p.poll() is not None:
                                break
                        if not ready:
                            tail = ""
                            try:
                                if stderr_log.exists():
                                    lines = open(stderr_log, encoding="utf-8", errors="replace").read().splitlines()
                                    tail = "\n".join(lines[-10:])
                            except Exception:
                                pass
                            self._send_json(400, {"ok": False, "driver": driver, "api_url": f"http://127.0.0.1:{_LLAMA_PORT}/v1/chat/completions", "model_name": model_name or "", "error": f"llama-server 未就绪，请查看输出日志。\n{tail}"})
                            return
                    if ready:
                        self._send_json(200, {"ok": True, "driver": driver, "api_url": f"http://127.0.0.1:{_LLAMA_PORT}/v1/chat/completions", "model_name": model_name or "qwen3.6", "error": None})
                        return

                self._send_json(400, {"ok": False, "error": f"不支持的驱动程序: {driver}"})
            except Exception as e:
                self._send_json(400, {"ok": False, "driver": driver, "error": str(e)})
            return

        if path == "/api/llama/start":
            body = dict(data or {})
            driver = (body.get("driver") or "").strip().lower()
            model_path = (body.get("model_path") or "").strip()
            port = int(body.get("port") or 8081)
            model_name = (body.get("model_name") or "").strip()
            use_local = bool(body.get("use_local_llm"))
            try:
                if not use_local:
                    self._send_json(400, {"ok": False, "error": "未启用本地 LLM"})
                    return
                if driver != "llama-server":
                    self._send_json(400, {"ok": False, "error": "仅支持 llama-server 手动启停"})
                    return
                _LLAMA_SERVER_EXE = (body.get("exe_path") or "").strip()
                _LLAMA_MODEL = model_path or ""
                _LLAMA_PORT = port or 8081
                if not _LLAMA_SERVER_EXE:
                    self._send_json(400, {"ok": False, "driver": driver, "error": "未填写 llama-server 可执行文件路径"})
                    return
                if not os.path.exists(_LLAMA_SERVER_EXE):
                    self._send_json(400, {"ok": False, "driver": driver, "error": f"llama-server 不存在: {_LLAMA_SERVER_EXE}"})
                    return
                if not _LLAMA_MODEL:
                    self._send_json(400, {"ok": False, "driver": driver, "error": "未填写模型 .gguf 路径"})
                    return
                if not os.path.exists(_LLAMA_MODEL):
                    self._send_json(400, {"ok": False, "driver": driver, "error": f"模型文件不存在: {_LLAMA_MODEL}"})
                    return
                ready = False
                try:
                    req = urllib.request.Request(f"http://127.0.0.1:{_LLAMA_PORT}/health")
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        ready = resp.status == 200
                except Exception:
                    ready = False
                if ready:
                    self._send_json(200, {"ok": True, "driver": driver, "api_url": f"http://127.0.0.1:{_LLAMA_PORT}/v1/chat/completions", "model_name": model_name or "qwen3.6", "error": None, "started": False})
                    return
                stderr_log = HERE / "llama-server-stderr.log"
                try:
                    with open(stderr_log, "w", encoding="utf-8", errors="replace") as sf:
                        p = subprocess.Popen(
                            [_LLAMA_SERVER_EXE, "--model", _LLAMA_MODEL, "--host", "127.0.0.1", "--port", str(_LLAMA_PORT), "-ngl", "999", "--cpu-moe", "--no-mmap", "--ctx-size", "32768"],
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=sf,
                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                        )
                except Exception as e:
                    self._send_json(400, {"ok": False, "driver": driver, "error": f"llama-server 启动失败: {e}"})
                    return
                for i in range(120):
                    time.sleep(1)
                    try:
                        req = urllib.request.Request(f"http://127.0.0.1:{_LLAMA_PORT}/health")
                        with urllib.request.urlopen(req, timeout=3) as resp:
                            if resp.status == 200:
                                ready = True
                                break
                    except Exception:
                        pass
                    if p.poll() is not None:
                        break
                if ready:
                    self._send_json(200, {"ok": True, "driver": driver, "api_url": f"http://127.0.0.1:{_LLAMA_PORT}/v1/chat/completions", "model_name": model_name or "qwen3.6", "error": None, "started": True})
                    return
                tail = ""
                try:
                    if stderr_log.exists():
                        lines = open(stderr_log, encoding="utf-8", errors="replace").read().splitlines()
                        tail = "\n".join(lines[-10:])
                except Exception:
                    pass
                self._send_json(400, {"ok": False, "driver": driver, "api_url": f"http://127.0.0.1:{_LLAMA_PORT}/v1/chat/completions", "model_name": model_name or "qwen3.6", "error": f"llama-server 启动后未就绪，请查看输出日志。\n{tail}"})
                return
            except Exception as e:
                self._send_json(400, {"ok": False, "driver": driver, "error": str(e)})
            return

        if path == "/api/llama/scan":
            try:
                body2 = dict(data or {})
                user_root = (body2.get("root") or "").strip()
                exes, models = [], []
                seen_exe, seen_model = set(), set()
                # 用户指定目录优先；留空则全盘常见位置
                if user_root:
                    if os.path.isdir(user_root):
                        search_roots = [user_root]
                    else:
                        self._send_json(400, {"ok": False, "error": f"目录不存在: {user_root}"})
                        return
                else:
                    search_roots = [HERE]
                    import string as _string
                    for ch in _string.ascii_uppercase:
                        root = f"{ch}:\\"
                        if os.path.exists(root):
                            search_roots.append(root)
                    for base in (os.path.expanduser("~"),
                                 os.environ.get("ProgramFiles") or os.environ.get("ProgramW6432") or "",
                                 os.environ.get("ProgramFiles(x86)") or ""):
                        if os.path.isdir(base):
                            search_roots.append(base)
                for p in search_roots:
                    for part in os.listdir(p) if os.path.isdir(p) else []:
                        full = os.path.join(p, part)
                        name = part.lower()
                        if os.path.isfile(full) and name in ("llama-server.exe", "llama-server", "ollama.exe", "ollama"):
                            if full not in seen_exe:
                                seen_exe.add(full)
                                exes.append(full)
                        elif os.path.isfile(full) and name.endswith(".gguf"):
                            if full not in seen_model:
                                seen_model.add(full)
                                models.append(full)
                        elif os.path.isdir(full) and any(k in name for k in ("llama", "qwen", "model", "gguf")):
                            try:
                                for root2, dirs2, files2 in os.walk(full):
                                    dirs2[:] = [d for d in dirs2 if not d.startswith(".__")]
                                    for fn in files2:
                                        fl = fn.lower()
                                        if fl in ("llama-server.exe",) or fl.endswith(".gguf"):
                                            fp = os.path.join(root2, fn)
                                            cap = 400
                                            if fl.endswith(".gguf") and len(models) >= cap:
                                                continue
                                            if fl == "llama-server.exe" and len(exes) >= cap:
                                                continue
                                            if fl.endswith(".gguf"):
                                                if fp not in seen_model:
                                                    seen_model.add(fp)
                                                    models.append(fp)
                                            else:
                                                if fp not in seen_exe:
                                                    seen_exe.add(fp)
                                                    exes.append(fp)
                            except Exception:
                                pass
                exes = list(dict.fromkeys(exes))
                models = list(dict.fromkeys(models))
                def _fmt_model(m):
                    try:
                        return {"path": m, "size": os.path.getsize(m)}
                    except Exception:
                        return {"path": m, "size": 0}
                self._send_json(200, {
                    "ok": True,
                    "llama_servers": exes[:10],
                    "models": [_fmt_model(m) for m in models[:60]],
                })
            except Exception as e:
                self._send_json(500, {"ok": False, "error": str(e)})
            return

        if path == "/api/llama/stop":
            body = dict(data or {})
            port = int(body.get("port") or 8081)
            try:
                url = f"http://127.0.0.1:{port}/shutdown"
                req = urllib.request.Request(url, method="POST")
                with urllib.request.urlopen(req, timeout=10) as r:
                    self._send_json(200, {"ok": True})
                    return
            except Exception as e:
                self._send_json(200, {"ok": True, "note": "shutdown request failed: " + str(e)})
            return

        if path == "/api/llama/open":
            body = dict(data or {})
            port = int(body.get("port") or 8081)
            url = f"http://127.0.0.1:{port}"
            try:
                if sys.platform == "win32":
                    os.startfile(url)
                else:
                    import webbrowser
                    webbrowser.open(url)
            except Exception:
                pass
            self._send_json(200, {"ok": True, "url": url})
            return

        # ── 群/好友管理转发（T8）──
        #  MCP 写端点（T2）：load/test 会起 MCP 子进程握手（mcp_hub 内部可等 90~120s），
        #  代理超时单独放宽，否则 webui 先断、前端看不到真实错误。
        if path.startswith("/api/groups") or path.startswith("/api/friends") \
                or path.startswith("/api/admin/") or path.startswith("/api/mcp/"):
            _pto = 150.0 if path in ("/api/mcp/load", "/api/mcp/test") else 30.0
            self._proxy_bot_core(path, dict(data or {}), timeout=_pto)
            return

        # ── 前端版本切换（T8）──────────────────────────────────────────
        #  写操作，故只接受 POST（且已在 do_POST 顶部过 Origin 闸门）。
        #  设计意图：新版视觉未定稿前，必须能一键切回旧版对照接口，
        #  所以这不是临时调试口，而是交付物的一部分。
        if path == "/api/ui/version":
            v = str((data or {}).get("version") or "").strip().lower()
            if _frontend_file(v) is None:
                avail = [k for k in FRONTENDS if FRONTENDS[k].is_file()]
                self._send_json(200, {"ok": False,
                                      "error": "版本不存在: " + (v or "(空)"),
                                      "available": avail})
                return
            try:
                _UI_VERSION_FILE.write_text(v, encoding="utf-8")
                _load_html()
                self._send_json(200, {"ok": True, "version": v})
            except Exception as e:
                self._send_json(200, {"ok": False, "error": str(e)})
            return

        if path == "/api/providers/models":
            body = dict(data or {})
            # 参数别名：向导/卡片历史上用 provider/api_url 传参，处理器读的是
            # provider_id/base_url —— 两套都认，否则向导"拉取模型"一直是空转
            provider_id = (body.get("provider_id") or body.get("provider") or "").strip()
            base_url = (body.get("base_url") or body.get("api_url") or "").strip().rstrip("/")
            api_key = (body.get("api_key") or "").strip()
            api_key_label = "set" if api_key else "empty"
            try:
                with open(str(HERE / 'model-debug.log'), 'a', encoding='utf-8') as df:
                    df.write(f'[RUNTIME] module={Path(__file__).resolve()} api_key={api_key_label}\n')
            except Exception:
                pass
            models = []
            provider_type = _detect_provider_type(base_url, provider_id)
            # 前端显式声明的协议优先（提供商编辑弹窗可选），避免中转/网关被 URL 误判
            proto_override = (body.get("protocol") or "").strip().lower()
            if proto_override and proto_override != 'auto' and proto_override in (
                    'openai', 'anthropic', 'gemini', 'ollama', 'lmstudio', 'deepseek', 'custom'):
                provider_type = proto_override
            candidate_urls = _model_candidate_urls(base_url, provider_type)
            local_models = []
            try:
                providers_path = PROJECT / "webui" / "providers.json"
                if providers_path.exists():
                    all_providers = json.loads(providers_path.read_text(encoding="utf-8"))
                else:
                    all_providers = []
                if provider_id:
                    found = next((p for p in all_providers if p.get("id") == provider_id), None)
                    if found:
                        local_models = [{"id": m.get("id", ""), "name": m.get("name", m.get("id", ""))} for m in found.get("models", []) if m.get("id")]
                        local_models.sort(key=lambda x: x["id"])
            except Exception as e:
                local_models = []
                try:
                    with open(str(HERE / 'model-debug.log'), 'a', encoding='utf-8') as df:
                        df.write('[LOCAL PARSE ERROR] ' + str(e) + '\n')
                except Exception:
                    pass
            models = local_models
            if candidate_urls and (not api_key or _is_redacted_key(api_key)):
                # 前端不回传明文 key：服务端按「提供商专属凭据 → 全局 Key」顺序解析
                try:
                    from config import get_provider_api_key
                    api_key = get_provider_api_key(provider_id) or ""
                except Exception:
                    api_key = ""
                if not api_key:
                    try:
                        from config import get_api_key as _gak
                        api_key = _gak() or ""
                    except Exception:
                        api_key = ""
                if not api_key:
                    try:
                        with open(str(HERE / 'model-debug.log'), 'a', encoding='utf-8') as df:
                            df.write('[SKIP] api_key empty\n')
                    except Exception:
                        pass
                    self._send_json(400, {"models": models, "provider_type": provider_type, "error": "未配置 API Key"})
                    return
            last_err = None
            per_timeout = 6 if provider_type == 'custom' else 15
            if candidate_urls:
                for url in candidate_urls:
                    try:
                        with open(str(HERE / 'model-debug.log'), 'a', encoding='utf-8') as df:
                            df.write('[TRY] ' + url + '\n')
                        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + api_key, "Accept": "application/json"})
                        with urllib.request.urlopen(req, timeout=per_timeout) as r:
                            data = json.loads(r.read().decode("utf-8"))
                            raw = data.get("data") or data.get("models") or []
                            remote = []
                            for m in raw:
                                mid = m.get("id") or m.get("name") or ""
                                if mid:
                                    remote.append({"id": mid, "name": m.get("name", mid)})
                            remote.sort(key=lambda x: x["id"])
                            if remote:
                                models = remote
                                last_err = None
                                break
                    except Exception as e:
                        last_err = _classify_error(e)
                        try:
                            with open(str(HERE / 'model-debug.log'), 'a', encoding='utf-8') as df:
                                df.write('[FAIL] ' + url + ' -> ' + str(e) + '\n')
                        except Exception:
                            pass
                        continue
            status = 200
            error = None
            if not models and last_err:
                status = 400 if "地址不可达" in last_err else 500
                error = last_err
            # 拉取成功即自动持久化到该 provider 的 models：
            # 一次拉取永久记住，topbar / catalog / 设置页共享同一数据源，无需静态兑底
            if models and provider_id:
                try:
                    _cfg = load_config()
                    _changed = False
                    for _p in (_cfg.get("providers") or []):
                        if isinstance(_p, dict) and str(_p.get("id") or "") == provider_id and _p.get("models") != models:
                            _p["models"] = models
                            _changed = True
                    if _changed:
                        if save_config(_cfg):
                            CONFIG_CACHE.update(_cfg)
                except Exception:
                    pass
            try:
                with open(str(HERE / 'model-debug.log'), 'a', encoding='utf-8') as df:
                    df.write(f'[RESPONSE] type={provider_type} models={len(models)} status={status} error={error}\n')
            except Exception:
                pass
            self._send_json(status, {"models": models, "provider_type": provider_type, "error": error,
                                     "manual": provider_type == 'custom'})
            return

        
        # --- Meme POST APIs ---
        if path == "/api/memes/upload":
            try:
                import base64
                data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
                # 文件名必须取 basename 并限图片扩展名，防止 `..\..\evil.png` 向任意目录写文件
                raw_name = str(data.get('name', 'unknown.jpg'))
                name = Path(raw_name).name.strip()
                if not name or name in ('.', '..') or Path(name).suffix.lower() not in {'.jpg', '.jpeg', '.png', '.gif', '.webp'}:
                    self._send_json(200, {'success': False, 'error': '非法文件名或类型'})
                    return
                b64 = data.get('data', '')
                tags = data.get('tags', [])
                meme_dir = PROJECT / 'memes' / 'memes'
                meme_dir.mkdir(parents=True, exist_ok=True)
                dest = meme_dir / name
                if b64 and ',' in b64:
                    b64 = b64.split(',', 1)[1]
                dest.write_bytes(base64.b64decode(b64))
                tag_file = dest.with_suffix('.json')
                tag_file.write_text(json.dumps({'tags': tags}, ensure_ascii=False), encoding='utf-8')
                self._send_json(200, {'success': True, 'name': name})
            except Exception as e:
                self._send_json(200, {'success': False, 'error': str(e)})
            return

        if path == "/api/memes/delete":
            try:
                data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
                # 只允许删除 meme 目录内的图片（basename + 扩展名校验），防止删除任意文件
                raw_name = str(data.get('name', ''))
                name = Path(raw_name).name.strip()
                if not name or Path(name).suffix.lower() not in {'.jpg', '.jpeg', '.png', '.gif', '.webp'}:
                    self._send_json(200, {'success': False, 'error': '非法文件名或类型'})
                    return
                meme_dir = PROJECT / 'memes' / 'memes'
                target = meme_dir / name
                if target.exists() and target.is_file():
                    target.unlink()
                tag_file = target.with_suffix('.json')
                if tag_file.exists() and tag_file.is_file():
                    tag_file.unlink()
                self._send_json(200, {'success': True})
            except Exception as e:
                self._send_json(200, {'success': False, 'error': str(e)})
            return

        if path == "/api/memes/tags":
            try:
                data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
                name = data.get('name', '')
                tags = data.get('tags', [])
                # P0-4：name 来自请求体且**用于写盘**。此前是裸的 `meme_dir / name`，
                # name="../../bot_config" → with_suffix('.json') 会**覆盖 bot_config.json**
                # （任意 .json 写的路径穿越，比读更严重）。这里双重校验后再写。
                safe = _safe_meme_rel(name)
                d_base = (PROJECT / 'memes' / 'memes').resolve()
                target = (d_base / safe).resolve() if safe else None
                if not safe or not _within(target, d_base):
                    self._send_json(200, {'success': False, 'error': '非法的表情包名'})
                    return
                tag_file = target.with_suffix('.json')
                if not _within(tag_file.resolve(), d_base):
                    self._send_json(200, {'success': False, 'error': '非法的表情包名'})
                    return
                tag_file.write_text(json.dumps({'tags': tags}, ensure_ascii=False), encoding='utf-8')
                self._send_json(200, {'success': True})
            except Exception as e:
                self._send_json(200, {'success': False, 'error': str(e)})
            return

        if path == "/api/memes/import":
            try:
                data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
                src = Path(data.get('directory', '').strip())
                meme_dir = PROJECT / 'memes' / 'memes'
                imported = 0
                if src.exists():
                    for fpath in src.rglob('*'):
                        if fpath.suffix.lower() in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
                            rel_name = str(fpath.relative_to(src))
                            dest = meme_dir / rel_name
                            # P0-4：rel_name 由 relative_to 产出、理论安全，但仍做一次
                            # 容器校验作为纵深防御（若将来改成别的取名方式不至于失守）。
                            if not _within(dest.resolve(), meme_dir.resolve()):
                                continue
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            if not dest.exists():
                                dest.write_bytes(fpath.read_bytes())
                                imported += 1
                self._send_json(200, {'success': True, 'imported': imported})
            except Exception as e:
                self._send_json(200, {'success': False, 'error': str(e)})
            return

        if path == "/api/bot/start":
            result = start_bot()
            self._send_json(200, result)
            return

        if path == "/api/bot/stop":
            result = stop_bot()
            self._send_json(200, result)
            return

        if path == "/api/plugins/toggle":
            body = dict(data or {})
            name = (body.get("name") or "").strip()
            enabled = body.get("enabled", body.get("已启用", False))
            if not name:
                self._send_json(200, {"error": "missing name"})
                return
            state = load_plugin_state()
            if not isinstance(state, dict):
                state = {}
            if "已启用" not in state or not isinstance(state["已启用"], dict):
                state["已启用"] = {}
            if "enabled" not in state or not isinstance(state["enabled"], dict):
                state["enabled"] = {}
            state["已启用"][name] = bool(enabled)
            state["enabled"][name] = bool(enabled)
            # Normalize to lowercase to avoid duplicate keys like "EGO"/"ego"
            state["已启用"][name.lower()] = bool(enabled)
            state["enabled"][name.lower()] = bool(enabled)
            save_plugin_state(state)
            try:
                sys.path.insert(0, str(PROJECT))
                from plugins.plugin_manager import init_plugin_manager, get_plugin_manager
                init_plugin_manager()
                pm = get_plugin_manager()
                if pm:
                    if enabled:
                        pm.enable(name)
                    else:
                        pm.disable(name)
            except Exception as e:
                push_proto_log(f"[error] Plugin hook error: {e}")
            with STATE_LOCK:
                if enabled:
                    S.plugin_active += 1
                    S.add_activity("emerald", f"插件<b>{name}</b> 已启用")
                else:
                    S.plugin_active = max(0, S.plugin_active - 1)
                    S.add_activity("rose", f"插件<b>{name}</b> 已停用")
            self._send_json(200, {"success": True, "enabled": bool(enabled)})
            return

        if path == "/api/config/model":
            # 快速切换模型：一次性更新 provider/url/model 三个字段并热重载（不碰其它配置）
            body = dict(data or {})
            m = str(body.get("model_name") or "").strip()
            pid = str(body.get("api_provider") or "").strip()
            if not m or not pid:
                self._send_json(200, {"success": False, "error": "missing api_provider/model_name"})
                return
            cfg = load_config()
            provs = cfg.get("providers") or []
            prov = next((p for p in provs if isinstance(p, dict) and str(p.get("id")) == pid), None)
            if not prov:
                self._send_json(200, {"success": False, "error": "provider not found"})
                return
            cfg["api_provider"] = pid
            cfg["api_url"] = str(prov.get("base_url") or "")
            cfg["model_name"] = m
            # 记住该提供商最近使用的模型（providers 随 save_config 落盘）
            try:
                prov["last_model"] = m
            except Exception:
                pass
            success = save_config(cfg)
            reload_result = None
            if success:
                CONFIG_CACHE.update(cfg)
                reload_result = push_bot_reload()
            self._send_json(200, {"success": success, "reload": reload_result, "model_name": m})
            return

        if path == "/api/llm/test":
            # 真实连通性测试：优先用前端当前表单值（未保存也可测），缺省回落已保存配置；
            # Key 掩码占位符视为无效，回退每提供商专属凭据 → 全局配置
            cfg = load_config()
            body_cfg = dict(data or {})
            provider_id = str(body_cfg.get("api_provider") or cfg.get("api_provider") or "")
            try:
                from config import get_provider_api_key
                api_key = get_provider_api_key(provider_id) or ""
            except Exception:
                api_key = ""
            form_key = str(body_cfg.get("api_key") or "").strip()
            if form_key and not _is_redacted_key(form_key):
                api_key = form_key
            if not api_key:
                api_key = cfg.get("api_key") or ""
            form_url = str(body_cfg.get("api_url") or "").strip()
            api_url = form_url or (cfg.get("api_url") or "").strip()
            form_model = str(body_cfg.get("model_name") or "").strip()
            model = form_model or (cfg.get("model_name") or "").strip()
            if not api_url or not api_key:
                self._send_json(200, {"ok": False, "error": "未配置 API URL 或 API Key（请在 LLM 配置页填写）"})
                return
            # 注意：do_POST 是巨型函数，禁止在函数内局部 import urllib.request——
            # 那会让 urllib 变成整个函数的局部变量，导致其它分支（如模型列表拉取）
            # 报 UnboundLocalError。模块顶部已有全局 import。
            # 推理模型会先消耗 reasoning tokens：预算给足 64，避免 content 空返回被误判为失败
            payload = {"model": model, "messages": [{"role": "user", "content": "你好"}], "max_tokens": 64}
            req = urllib.request.Request(api_url, data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    resp = json.loads(r.read().decode("utf-8"))
                content = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
                self._send_json(200, {"ok": True, "model": model, "url": api_url, "reply": str(content)[:80]})
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="replace")[:300]
                except Exception:
                    pass
                self._send_json(200, {"ok": False, "error": f"HTTP {e.code}: {body}"})
            except Exception as e:
                self._send_json(200, {"ok": False, "error": str(e)})
            return

        if path == "/api/command":
            cmd = data.get("command", "").strip().lower()
            if cmd.startswith("/"):
                cmd = cmd[1:]
            output = ""
            if cmd == "status":
                uptime = int(time.time() - S.bot_start_time)
                h, rem = divmod(uptime, 3600)
                m, s = divmod(rem, 60)
                uptime_str = f"{h:02d}:{m:02d}:{s:02d}"
                sl = get_self_learning_stats()
                output = f"状态: {'运行中' if S.bot_running else '已停止'} | 运行时长: {uptime_str} | 消息数: {S.messages_total} | 活跃群组: {S.groups_active} | 图片生成: {S.images_generated} | AI 调用: {S.api_calls_today} | 内存: {sl['total_messages']} entries"
            elif cmd == "restart":
                # 真实重启 bot_core（先前是模拟输出）
                stop_result = stop_bot()
                time.sleep(2)
                start_result = start_bot()
                output = f"重启完成: 停止={stop_result.get('output')} | 启动={start_result.get('output')}"
            elif cmd == "config_reload":
                reload_result = push_bot_reload()
                output = "配置已热重载" if reload_result and reload_result.get("ok") else ("Bot 未运行，无法热重载" if reload_result is None else "热重载失败")
            elif cmd == "help":
                output = "可用指令: status | restart | config_reload"
            elif cmd in ("memory_reload", "self_learn", "clear_sessions", "personality_reload"):
                output = f"未实现: {cmd}（没有对应的后端动作，避免假装成功）"
            else:
                output = f"Unknown: {cmd}. Type 'help' for commands."
            self._send_json(200, {"output": output})
            return

        if path == "/api/qq-path":
            qq_path = data.get("qq_path", "")
            cfg_path = PROJECT / "bot_config.json"
            cfg = {}
            if cfg_path.exists():
                try:
                    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                except Exception:
                    cfg = {}
            cfg["qq_path"] = qq_path
            try:
                open(str(cfg_path), "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2))
                CONFIG_CACHE.update(cfg)
                self._send_json(200, {"success": True, "qq_path": qq_path})
            except Exception as e:
                self._send_json(200, {"success": False, "output": f"保存失败: {e}"})
            return

        if path == "/api/protocol/start":
            try:
                from protocol_manager import ProtocolManager
                mgr = ProtocolManager()

                mgr.set_status_callback(_protocol_status_callback)
                mgr.start()
                S._protocol_manager = mgr
                self._send_json(200, {"success": True, "output": "协议层已启动"})
            except Exception as e:
                self._send_json(200, {"success": False, "output": f"启动失败: {e}"})
            return

        if path == "/api/protocol/stop":
            mgr = getattr(S, '_protocol_manager', None)
            if mgr:
                try:
                    mgr.stop()
                    self._send_json(200, {"success": True, "output": "协议层已停止"})
                except Exception as e:
                    self._send_json(200, {"success": False, "output": f"停止失败: {e}"})
            else:
                self._send_json(200, {"success": True, "output": "协议层未运行"})
            return

        if path == "/api/protocol/check":
            try:
                self._send_json(200, _protocol_check_items(str((data or {}).get("driver") or "")))
            except Exception as e:
                self._send_json(200, {"ok": False, "driver": "", "items": [], "error": f"自检失败: {e}"})
            return

        if path == "/api/protocol/switch":
            driver = str(data.get("driver") or "").strip()
            if not driver:
                self._send_json(200, {"success": False, "output": "缺少 driver 参数"})
                return
            try:
                from protocols import registry
                if not registry.is_registered(driver):
                    self._send_json(200, {"success": False, "output": f"协议驱动 {driver} 未注册"})
                    return
                # 风控：三个驱动均属非官方登录通道；SnowLuma 为手动登录（登录态主动控制），
                # NapCat/LLOneBot 重启会重扫 QQ 登录态更易触发风控，切换需二次确认
                if driver in ("napcat", "llonebot", "snowluma") and not data.get("confirm"):
                    _warn = ("SnowLuma 由你手动打开 QQ+WebUI 登录，登录态主动控制，风控风险低；" if driver == "snowluma"
                             else f"「{driver}」重启会重扫 QQ 登录态，频繁登录易被腾讯风控判定。")
                    self._send_json(200, {
                        "success": False,
                        "need_confirm": True,
                        "output": (
                            f"「{driver}」属非官方登录通道。{_warn}如需继续请二次确认。"
                        ),
                    })
                    return
                old = getattr(S, '_protocol_manager', None)
                if old:
                    try:
                        old.stop()
                    except Exception as e:
                        push_proto_log(f"[switch] 停止旧驱动失败: {e}")
                    S._protocol_manager = None
                    with STATE_LOCK:
                        S.protocol_running = False
                # 持久化 protocol_driver
                cfg = load_config()
                cfg["protocol_driver"] = driver
                cfg["protocol_enabled"] = True
                save_config(cfg)
                push_proto_log(f"[switch] 已切换协议驱动: {driver}（未启动，请配置后手动启动）")
                self._send_json(200, {"success": True, "output": f"已切换协议驱动「{driver}」，请填写驱动配置后点「启动协议层」"})
            except Exception as e:
                self._send_json(200, {"success": False, "output": f"切换失败: {e}"})
            return

        if path == "/api/protocol/detect":
            self._send_json(200, _detect_protocol_paths())
            return

        if path == "/api/protocol/classify":
            # 用户选中一个路径后，判断它属于哪个驱动（文件名/目录名启发式）
            fp = str((data.get("path") or "")).strip()
            self._send_json(200, _classify_driver_path(fp))
            return

        if path == "/api/protocol/open-site":
            # 在系统默认浏览器打开站点（解决 Electron 窗口内 target=_blank 被 deny 打不开的问题）
            url = str((data.get("url") or "")).strip()
            allow = ("napneko.github.io", "llonebot.github.io", "snowluma.github.io",
                     "github.com", "github.io", "localhost", "127.0.0.1", "::1")
            bad = True
            try:
                if url.startswith(("http://", "https://")):
                    host = (urlparse(url).hostname or "").lower()
                    bad = not any(host == a or host.endswith("." + a) for a in allow)
            except Exception:
                bad = True
            if bad:
                self._send_json(200, {"ok": False, "error": "域名不在允许列表"})
                return
            try:
                import webbrowser as _wb
                threading.Thread(target=lambda: _wb.open(url), daemon=True).start()
                self._send_json(200, {"ok": True})
            except Exception as e:
                self._send_json(200, {"ok": False, "error": str(e)})
            return

        if path == "/api/protocol/auto-detect":
            try:
                self._send_json(200, _auto_detect_protocol_drivers())
            except Exception as e:
                self._send_json(200, {"ok": False, "error": str(e)})
            return

        if path == "/api/protocol/fs":
            p = str((data.get("path") or "")).strip()
            if not p:
                p = "C:\\"
            try:
                import os as _os
                ap = _os.path.abspath(p)
                if not _os.path.isdir(ap):
                    self._send_json(200, {"ok": False, "error": f"不是有效目录: {p}"})
                    return
                entries = []
                for name in sorted(_os.listdir(ap)):
                    full = _os.path.join(ap, name)
                    try:
                        is_dir = _os.path.isdir(full)
                        sz = 0 if is_dir else _os.path.getsize(full)
                    except Exception:
                        is_dir, sz = False, 0
                    entries.append({"name": name, "path": full, "dir": is_dir, "size": sz})
                dp = _os.path.dirname(ap)
                parent = dp if dp != ap else ""
                self._send_json(200, {"ok": True, "path": ap, "parent": parent, "entries": entries})
            except Exception as e:
                self._send_json(200, {"ok": False, "error": str(e)})
            return

        if path == "/api/protocol/config":
            driver = str(data.get("driver") or "").strip()
            config = data.get("config") or {}
            if not driver:
                self._send_json(200, {"success": False, "output": "缺少 driver 参数"})
                return
            if not isinstance(config, dict):
                self._send_json(200, {"success": False, "output": "config 必须是对象"})
                return
            try:
                cfg = load_config()
                drivers = cfg.setdefault("protocol_drivers", {})
                cur = drivers.setdefault(driver, {})
                cur.update({k: v for k, v in config.items() if k not in ("qq_path",)})
                # qq_path 存顶层，兼容旧面板逻辑
                if config.get("qq_path"):
                    cfg["qq_path"] = config["qq_path"]
                    cur["qq_path"] = config["qq_path"]
                save_config(cfg)
                push_proto_log(f"[config] 已保存协议驱动配置: {driver}")
                self._send_json(200, {"success": True, "output": f"{driver} 配置已保存"})
            except Exception as e:
                self._send_json(200, {"success": False, "output": f"保存失败: {e}"})
            return

        if path == "/api/protocol/logs":
            limit = int(data.get("limit", 200)) if isinstance(data, dict) else 200
            self._send_json(200, {"logs": _collect_logs(limit)})
            return

        if path == "/api/protocol/logs/clear":
            global _LOG_CLEARED
            with PROTO_LOG_LOCK:
                PROTO_LOG.clear()
                _LOG_CLEARED = True
            self._send_json(200, {"ok": True})
            return

        if path == "/api/plugins/action":
            body = dict(data or {})
            plugin_id = (body.get("plugin_id") or "").strip()
            action = (body.get("action") or "").strip()
            params = body.get("params") or {}
            debug = {'path': path, 'plugin_id': plugin_id, 'action': action}
            try:
                with open(str(HERE / 'model-debug.log'), 'a', encoding='utf-8') as df:
                    df.write('[ACTION DEBUG] ' + repr(debug) + '\n')
            except Exception:
                pass
            if not plugin_id or not action:
                self._send_json(200, {"error": "missing plugin_id or action"})
                return
            _generic = _invoke_generic_plugin(plugin_id, action, body.get("params") or {})
            if _generic is not None:
                self._send_json(200, _generic)
                return
            if plugin_id == "slang_learner":
                try:
                    sys.path.insert(0, str(PROJECT))
                    from plugins.ego.slang_learner import (
                        start_learning, get_pending, get_injected, get_config as slang_config,
                        set_config as slang_set_config, inject_selected, reject_selected, reject_all,
                        inject_all, _learner, web_enrich, web_status as slang_web_status, add_manual as slang_add_manual,
                        get_reply_templates, set_reply_template, delete_reply_template, import_reply_templates,
                        get_prompt_templates as slang_get_templates,
                        set_prompt_template as slang_set_template,
                        delete_prompt_template as slang_del_template,
                    )
                    if action == "learn":
                        topics = params.get("topics") or ["游戏圈", "直播圈", "抽象圈"]
                        self._send_json(200, start_learning(topics))
                        return
                    if action == "llm_enrich":
                        games = params.get("topics") or params.get("games") or ["明日方舟"]
                        batch = int(params.get("batch") or 3)
                        template = params.get("template") or ""
                        try:
                            result = web_enrich(games=games, batch=batch, fallback=True, template=template)
                        except Exception as e:
                            result = {"ok": False, "added": 0, "items": [], "errors": [str(e)]}
                        self._send_json(200, result)
                        return
                    if action == "get_templates":
                        self._send_json(200, slang_get_templates())
                        return
                    if action == "set_template":
                        self._send_json(200, slang_set_template(
                            (params.get("key") or "").strip(),
                            (params.get("name") or "").strip(),
                            (params.get("template") or ""),
                        ))
                        return
                    if action == "delete_template":
                        self._send_json(200, slang_del_template((params.get("key") or "").strip()))
                        return
                    if action == "web_status":
                        self._send_json(200, slang_web_status())
                        return
                    if action == "add_manual":
                        self._send_json(200, slang_add_manual(
                            (params.get("term") or "").strip(),
                            (params.get("meaning") or "").strip(),
                            (params.get("source") or "手动输入").strip(),
                            float(params.get("quality") or 0.9),
                        ))
                        return
                    if action == "get_replies":
                        self._send_json(200, get_reply_templates((params.get("term") or "").strip()))
                        return
                    if action == "set_reply":
                        self._send_json(200, set_reply_template((params.get("term") or "").strip(), params.get("replies") or []))
                        return
                    if action == "delete_reply":
                        self._send_json(200, delete_reply_template((params.get("term") or "").strip()))
                        return
                    if action == "import_replies":
                        self._send_json(200, import_reply_templates(params.get("mapping") or {}))
                        return
                    if action == "pending":
                        result = get_pending(
                            (params.get("search") or "").strip(),
                            (params.get("source") or "").strip(),
                            float(params.get("min_quality") or 0),
                        )
                        self._send_json(200, {"pending": result})
                        return
                    if action == "injected":
                        self._send_json(200, {"injected": get_injected(int(params.get("limit") or 100))})
                        return
                    if action == "history":
                        self._send_json(200, {"history": getattr(_learner, "history", [])})
                        return
                    if action == "inject":
                        self._send_json(200, inject_selected(list(params.get("ids") or [])))
                        return
                    if action == "inject_all":
                        self._send_json(200, inject_all(float(params.get("min_quality") or 0)))
                        return
                    if action == "reject":
                        self._send_json(200, reject_selected(list(params.get("ids") or [])))
                        return
                    if action == "reject_all":
                        self._send_json(200, reject_all(float(params.get("min_quality") or 0)))
                        return
                    if action == "get_config":
                        self._send_json(200, {"config": slang_config()})
                        return
                    if action == "set_config":
                        self._send_json(200, {"config": slang_set_config(params.get("config") or {})})
                        return
                    if action == "clear_injected":
                        _learner.injected = []
                        with open(_learner.INJECTED_FILE, "w", encoding="utf-8") as f:
                            import json as _json
                            _json.dump(_learner.injected, f, ensure_ascii=False, indent=2)
                        self._send_json(200, {"ok": True})
                        return
                    self._send_json(200, {"error": "unknown slang action: " + action})
                    return
                except Exception as e:
                    self._send_json(200, {"error": str(e)})
                    return
            if plugin_id == "personality_engine":
                try:
                    sys.path.insert(0, str(PROJECT))
                    from plugins.personality_engine import get_personality, save_personality, get_config, set_config, get_examples, save_examples
                    if action == "get_personality":
                        self._send_json(200, get_personality())
                        return
                    if action == "save_personality":
                        params = body.get("params") or {}
                        self._send_json(200, save_personality(params.get("sections") or {}, params.get("config") or {}))
                        return
                    if action == "get_config":
                        self._send_json(200, get_config())
                        return
                    if action == "set_config":
                        params = body.get("params") or {}
                        set_config(params)
                        self._send_json(200, {"ok": True, "config": get_config()})
                        return
                    if action == "get_examples":
                        params = body.get("params") or {}
                        self._send_json(200, get_examples((params.get("scene") or "").strip()))
                        return
                    if action == "save_examples":
                        params = body.get("params") or {}
                        self._send_json(200, save_examples(params.get("examples") or []))
                        return
                except Exception as e:
                    self._send_json(200, {"error": str(e)})
                    return
            if plugin_id == "ego":
                try:
                    sys.path.insert(0, str(PROJECT))
                    from plugins.ego import (
                        get_personality, save_personality, get_config, set_config,
                        get_examples, save_examples, memory_stats, memory_vector_stats,
                        slang_start_learning, slang_get_pending,
                        slang_inject_selected, slang_reject_selected, slang_get_stats,
                        mood_get_config, mood_set_config, mood_get_moods, mood_reset_mood,
                    )
                    if action == "get_personality":
                        self._send_json(200, get_personality())
                        return
                    if action == "save_personality":
                        params = body.get("params") or {}
                        self._send_json(200, save_personality(params.get("sections") or {}, params.get("config") or {}))
                        return
                    if action == "get_config":
                        self._send_json(200, get_config())
                        return
                    if action == "set_config":
                        params = body.get("params") or {}
                        set_config(params)
                        self._send_json(200, {"ok": True, "config": get_config()})
                        return
                    if action == "get_examples":
                        params = body.get("params") or {}
                        self._send_json(200, get_examples((params.get("scene") or "").strip()))
                        return
                    if action == "save_examples":
                        params = body.get("params") or {}
                        self._send_json(200, save_examples(params))
                        return
                    if action == "memory_stats":
                        params = body.get("params") or {}
                        stats = memory_stats((params.get("scope_id") or "default").strip())
                        self._send_json(200, {"stats": stats})
                        return
                    if action == "memory_vector_stats":
                        self._send_json(200, {"stats": memory_vector_stats()})
                        return
                    if action == "memory_persona":
                        params = body.get("params") or {}
                        persona = {}
                        try:
                            persona = __import__("plugins.ego", fromlist=["ego"])._memory.get_persona_export((params.get("scope_id") or "default").strip())
                        except Exception:
                            pass
                        self._send_json(200, {"persona": persona})
                        return
                    if action == "memory_clear":
                        params = body.get("params") or {}
                        scope_id = (params.get("scope_id") or "default").strip()
                        result = {"success": False}
                        try:
                            ego = __import__("plugins.ego", fromlist=["ego"])
                            mem = ego._memory
                            if hasattr(mem, "_init_db"):
                                db_path = getattr(mem, "DB_PATH", None)
                                if db_path and os.path.exists(db_path):
                                    conn = sqlite3.connect(db_path)
                                    conn.execute("DELETE FROM messages WHERE scope_id=?", (scope_id,))
                                    conn.execute("DELETE FROM facts WHERE scope_id=?", (scope_id,))
                                    conn.execute("DELETE FROM events WHERE scope_id=?", (scope_id,))
                                    conn.commit()
                                    conn.close()
                                    result["success"] = True
                        except Exception as e:
                            result["error"] = str(e)
                        self._send_json(200, result)
                        return
                    if action == "slang_clear_injected":
                        result = {"success": False}
                        try:
                            ego = __import__("plugins.ego", fromlist=["ego"])
                            slang = ego._slang
                            if hasattr(slang, "_save_json"):
                                slang._save_json(getattr(slang, "INJECTED_FILE", None) or str(PROJECT / "plugins" / "ego" / "slang_learner" / "injected.json"), [])
                                result["success"] = True
                        except Exception as e:
                            result["error"] = str(e)
                        self._send_json(200, result)
                        return
                    if action == "preview_prompt":
                        params = body.get("params") or {}
                        user_msg = (params.get("user_msg") or "").strip()
                        scope_id = (params.get("scope_id") or "default").strip()
                        prompt = ""
                        try:
                            ego = __import__("plugins.ego", fromlist=["ego"])
                            state = ego.personality_engine.ConversationState()
                            examples = []
                            memory_ctx = ""
                            try:
                                personality = ego.personality_engine.get_personality()
                                raw = ((personality.get("raw") or "") if isinstance(personality, dict) else "") or ""
                                if raw.strip():
                                    memory_ctx = "当前人格设定：\n" + raw.strip()
                            except Exception:
                                pass
                            try:
                                ex_data = ego.personality_engine.get_examples("")
                                examples = (ex_data.get("examples") or []) if isinstance(ex_data, dict) else []
                            except Exception:
                                examples = []
                            if hasattr(ego.personality_engine, "build_personality_prompt"):
                                prompt = ego.personality_engine.build_personality_prompt(
                                    state=state,
                                    memory_ctx=memory_ctx,
                                    examples=examples,
                                    user_msg=user_msg,
                                    user_id="preview",
                                )
                        except Exception as e:
                            prompt = "preview error: " + str(e)
                        self._send_json(200, {"prompt": prompt})
                        return
                    if action == "reload":
                        result = {"success": False}
                        try:
                            pm = __import__("plugins.plugin_manager", fromlist=["plugin_manager"])
                            if hasattr(pm, "get_plugin_manager"):
                                manager = pm.get_plugin_manager()
                                if manager and hasattr(manager, "reload"):
                                    manager.reload("ego")
                                    result["success"] = True
                                elif hasattr(pm, "init_plugin_manager"):
                                    pm.init_plugin_manager()
                                    result["success"] = True
                        except Exception as e:
                            result["error"] = str(e)
                        self._send_json(200, result)
                        return
                    # P0-5：本分支原只存在于 do_GET 的那份重复实现里（GET 侧已按
                    # HTTP 语义收紧为 405），这里是**唯一**保留的处理点，确保能力不丢。
                    if action == "style_features":
                        params = body.get("params") or {}
                        from plugins.ego import style_features as _style_features
                        features = _style_features((params.get("scope_id") or "default").strip(), (params.get("user_id") or "").strip())
                        self._send_json(200, {"features": features})
                        return
                    if action == "health":
                        from plugins.ego import health_check
                        self._send_json(200, health_check())
                        return
                    if action == "wizard_status":
                        from plugins.ego import is_wizard_done
                        self._send_json(200, {"done": is_wizard_done()})
                        return
                    if action == "wizard_done":
                        from plugins.ego import set_wizard_done
                        self._send_json(200, set_wizard_done())
                        return
                    if action == "wizard_reset":
                        from plugins.ego import reset_wizard
                        self._send_json(200, reset_wizard())
                        return
                    if action == "slang_learn":
                        topics = body.get("topics") or ["游戏圈", "直播圈", "抽象圈"]
                        if isinstance(topics, str):
                            topics = [t.strip() for t in topics.split(",") if t.strip()]
                        self._send_json(200, slang_start_learning(topics))
                        return
                    if action == "slang_pending":
                        params = body.get("params") or {}
                        result = slang_get_pending(
                            search=(params.get("search") or "").strip(),
                            source=(params.get("source") or "").strip(),
                            min_quality=float(params.get("min_quality") or 0),
                        )
                        if isinstance(result, dict):
                            result.setdefault("pending", result.get("pending", []))
                        else:
                            result = {"pending": list(result or [])}
                        self._send_json(200, result)
                        return
                    if action == "slang_injected":
                        injected = []
                        try:
                            injected = ego._slang.get_injected()
                        except Exception:
                            pass
                        self._send_json(200, {"injected": injected})
                        return
                    if action == "slang_stats":
                        self._send_json(200, {"stats": slang_get_stats()})
                        return
                    if action == "slang_inject":
                        params = body.get("params") or {}
                        ids = params.get("ids") or []
                        self._send_json(200, slang_inject_selected([str(x) for x in ids]))
                        return
                    if action == "slang_inject_all":
                        params = body.get("params") or {}
                        min_quality = float(params.get("min_quality") or 0)
                        self._send_json(200, __import__("plugins.ego", fromlist=["ego"])._slang.inject_all(min_quality))
                        return
                    if action == "slang_reject":
                        params = body.get("params") or {}
                        ids = params.get("ids") or []
                        self._send_json(200, slang_reject_selected([str(x) for x in ids]))
                        return
                    if action == "slang_reject_all":
                        params = body.get("params") or {}
                        min_quality = float(params.get("min_quality") or 0)
                        self._send_json(200, __import__("plugins.ego", fromlist=["ego"])._slang.reject_all(min_quality))
                        return
                    if action == "mood_config":
                        self._send_json(200, {"config": mood_get_config()})
                        return
                    if action == "mood_set_config":
                        params = body.get("params") or {}
                        self._send_json(200, {"ok": True, "config": mood_set_config(params)})
                        return
                    if action == "mood_moods":
                        self._send_json(200, {"moods": mood_get_moods()})
                        return
                    if action == "mood_reset":
                        params = body.get("params") or {}
                        self._send_json(200, mood_reset_mood((params.get("group_id") or "").strip()))
                        return
                except Exception as e:
                    self._send_json(200, {"error": str(e)})
                    return
            if plugin_id == "web_operator":
                try:
                    sys.path.insert(0, str(PROJECT))
                    import plugins.web_operator as wo
                    params = body.get("params") or {}
                    if action == "get_config":
                        self._send_json(200, {"config": wo.get_config()})
                        return
                    if action == "set_config":
                        self._send_json(200, wo.set_config(params))
                        return
                    if action == "toggle":
                        cfg = wo.get_config()
                        enabled = not bool(cfg.get("enabled", True))
                        wo.set_config({"enabled": enabled})
                        self._send_json(200, {"enabled": enabled})
                        return
                    if action == "get_history":
                        limit = int(params.get("limit") or 50)
                        self._send_json(200, wo.get_history(limit))
                        return
                    if action == "clear_history":
                        self._send_json(200, wo.clear_history())
                        return
                    if action == "get_tasks":
                        self._send_json(200, wo.get_tasks())
                        return
                    if action == "save_tasks":
                        self._send_json(200, wo.save_tasks(params.get("name"), params.get("chain") or []))
                        return
                    if action == "delete_task":
                        self._send_json(200, wo.delete_task(params.get("name")))
                        return
                    if action == "check_dependencies":
                        self._send_json(200, {"dependencies": wo.check_dependencies()})
                        return
                    if action == "install_dependencies":
                        self._send_json(200, wo.install_dependencies())
                        return
                    if action == "chain_run":
                        self._send_json(200, wo.execute_task("chain.run", params))
                        return
                    if action == "web.autonomous":
                        try:
                            ctx = {"chat": _make_autonomous_chat()}
                        except Exception as e:
                            self._send_json(200, {"ok": False, "error": f"宿主模型未就绪: {e}"})
                            return
                        self._send_json(200, wo.execute_task(action, params, context=ctx))
                        return
                    self._send_json(200, wo.execute_task(action, params))
                    return
                except Exception as e:
                    self._send_json(200, {"error": str(e)})
                    return
            # Generic plugin config fallback for plugins exposing get_config/set_config/toggle
            try:
                sys.path.insert(0, str(PROJECT))
                mod = __import__(f"plugins.{plugin_id}", fromlist=["get_config"])
                if action == "get_config":
                    fn = getattr(mod, "get_config", None)
                    if callable(fn):
                        self._send_json(200, fn())
                    else:
                        self._send_json(200, {"error": "plugin has no get_config"})
                    return
                if action == "set_config":
                    params = body.get("params") or {}
                    fn = getattr(mod, "set_config", None)
                    if callable(fn):
                        self._send_json(200, fn(params))
                    else:
                        self._send_json(200, {"error": "plugin has no set_config"})
                    return
                if action == "toggle":
                    fn = getattr(mod, "toggle", None)
                    if callable(fn):
                        self._send_json(200, fn())
                    else:
                        self._send_json(200, {"error": "plugin has no toggle"})
                    return
            except Exception as e:
                try:
                    with open(str(HERE / 'model-debug.log'), 'a', encoding='utf-8') as df:
                        df.write('[ACTION FALLBACK ERROR] ' + str(e) + '\n')
                except Exception:
                    pass
            self._send_json(404, {"error": "unknown plugin_id or action"})
            return

        self.send_response(404)
        self.end_headers()


# 
#  Bot Control
# 
def _get_qq_path():
    try:
        p = PROJECT / 'bot_config.json'
        if p.exists():
            return json.loads(p.read_text(encoding='utf-8-sig')).get('qq_path', '')
    except Exception:
        pass
    return ''

def _get_bot_cmd():
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        core_exe = os.path.join(exe_dir, 'EggBloom_v3_Core.exe')
        if not os.path.exists(core_exe):
            core_exe = os.path.join(exe_dir, '_internal', 'EggBloom_v3_Core.exe')
        if os.path.exists(core_exe):
            return [core_exe], os.path.dirname(core_exe)
        return None, None
    else:
        script = PROJECT / 'bot_core.py'
        if script.exists():
            return [sys.executable, str(script)], str(PROJECT)
        return None, None

def start_bot():
    global _bot_process
    with _bot_lock:
        if _bot_process and _bot_process.poll() is None:
            return {'success': False, 'output': 'Bot 已在运行'}
        cfg = load_config()
        if not cfg.get('api_key'):
            pass  # Allow start without api_key, bot_core will handle
        if not cfg.get('bot_qq'):
            return {'success': False, 'output': '请先到配置中心填入 Bot QQ 号喵~'}
        cmd, cwd = _get_bot_cmd()
        if not cmd:
            return {'success': False, 'output': '未找到 Bot 启动入口'}
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'
        env['PYTHONIOENCODING'] = 'utf-8'
        try:
            _bot_process = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True, encoding='utf-8', errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
            )
            with STATE_LOCK:
                S.bot_running = True
                S.bot_start_time = time.time()
            # Start log reader thread
            _start_bot_log_reader(_bot_process)
            return {'success': True, 'output': 'Bot 已启动', 'pid': _bot_process.pid}
        except Exception as e:
            return {'success': False, 'output': f'启动失败: {e}'}

def stop_bot():
    global _bot_process
    with _bot_lock:
        if not _bot_process or _bot_process.poll() is not None:
            _bot_process = None
            with STATE_LOCK:
                S.bot_running = False
            return {'success': True, 'output': 'Bot 未运行'}
        try:
            _bot_process.terminate()
            try:
                _bot_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _bot_process.kill()
        except Exception as e:
            return {'success': False, 'output': f'停止失败: {e}'}
        _bot_process = None
        with STATE_LOCK:
            S.bot_running = False
        return {'success': True, 'output': 'Bot 已停止'}

# ── 协议层在线探测（2026-10-19 修「协议开关显示未启用」）───────────────
# 背景：前端那张卡片读 `protocol_enabled`（配置文件里的标记），而这个字段**只在
# 「切换协议驱动」时**才被写成 True；点「启动协议层」不写它。
# 于是用户明明启动了 NapCat（OneBot 3000 在线），界面却永远显示「未启用」。
# 现改为**真实探测** OneBot API 是否可达 —— 这才是"协议有没有在工作"。
_PROTO_PROBE = {"at": 0.0, "ok": False, "url": ""}


def probe_protocol_online(timeout=0.4, ttl=3.0):
    """协议层是否真的在线（OneBot /get_status 返回 online）。带 3s 缓存。

    为什么缓存：`/api/bot/status` 被前端按秒轮询，探测虽快（本机 ~1ms），
    但没必要每秒打三次。
    为什么不用 `mgr._running`：那是"有没有从本界面点过启动"，外部启动的
    NapCat 不经过它 —— 会误判成未启用。
    """
    now = time.time()
    if now - _PROTO_PROBE["at"] < ttl:
        return _PROTO_PROBE["ok"], _PROTO_PROBE["url"]
    ok, url = False, ""
    try:
        cands = []
        try:
            ob = str((load_config() or {}).get("onebot_url") or "").strip()
            if ob:
                cands.append(ob.rstrip("/"))
        except Exception:
            pass
        for p in (3000, 3001, 3002, 3003):
            u = f"http://127.0.0.1:{p}"
            if u not in cands:
                cands.append(u)
        for u in cands:
            try:
                with urllib.request.urlopen(u + "/get_status", timeout=timeout) as r:
                    d = json.loads(r.read())
                if (d.get("data") or {}).get("online"):
                    ok, url = True, u
                    break
            except Exception:
                continue
    except Exception:
        pass
    _PROTO_PROBE.update({"at": now, "ok": ok, "url": url})
    return ok, url


def get_bot_status():
    alive = False
    pid = None
    with _bot_lock:
        if _bot_process and _bot_process.poll() is None:
            alive = True
            pid = _bot_process.pid
    proto_ok, proto_url = probe_protocol_online()
    with STATE_LOCK:
        return {
            'bot_running': alive if S.bot_running else False,
            'pid': pid,
            'uptime': int(time.time() - S.bot_start_time) if alive else 0,
            'qq_path': _get_qq_path(),
            'protocol_enabled': load_config().get('protocol_enabled', False),
            # 真实在线状态（2026-10-19）：前端应优先用这个，而不是 protocol_enabled
            'protocol_online': proto_ok,
            'protocol_url': proto_url,
        }

def load_plugin_state():
    try:
        p = PROJECT / "plugins" / "plugin_state.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def save_plugin_state(state):
    try:
        p = PROJECT / "plugins" / "plugin_state.json"
        open(str(p), "w", encoding="utf-8").write(json.dumps(state, ensure_ascii=False, indent=2))
    except Exception:
        pass


# 
#  Server Entry
# 
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

def _port_free(p):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", p))
        s.close()
        return True
    except OSError:
        return False

def pick_port(preferred=8080, tries=20):
    for p in range(preferred, preferred + tries):
        if _port_free(p):
            return p
    return preferred

def run(host="127.0.0.1", port=8080):
    global _LOG_CLEARED
    # 端口冲突自适应：preferred 被占用则顺延，最多试探 tries 个端口
    actual_port = pick_port(preferred=port)
    if actual_port != port:
        print(f"[WebUI] 端口 {port} 被占用，改用 {actual_port}")
    # 记录实际端口，供 main_window / 前端查询
    try:
        last_port_dir = PROJECT / "webui"
        last_port_dir.mkdir(exist_ok=True)
        (PROJECT / "last_port.txt").write_text(str(actual_port), encoding="utf-8")
    except Exception:
        pass
    # 每次服务器启动视为新会话：内存日志为空时显示空，而非回退到磁盘旧日志
    with PROTO_LOG_LOCK:
        PROTO_LOG.clear()
        _LOG_CLEARED = True
    server = ThreadedHTTPServer((host, actual_port), Handler)
    print(f"EggBloom WebUI running on http://{host}:{actual_port}")
    _uni_log(f"EggBloom WebUI running on http://{host}:{actual_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == "__main__":
    run()