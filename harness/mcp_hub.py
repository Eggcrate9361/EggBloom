# -*- coding: utf-8 -*-
"""harness/mcp_hub.py —— MCP 桥（**惰性 + 命名空间 + 缓存 + 独立 venv 隔离子进程**）。

设计红线（研究结论）
    - **必须惰性**：绝不在启动时枚举所有 MCP server 的所有工具（每个 tools/list 都要
      起进程 + 握手，慢且可能挂）。启动只读**配置**，工具 schema 第一次被指名时才拉。
    - **必须命名空间化**：一律 `mcp__<server>__<tool>`，避免与内置工具撞名。
    - **必须缓存 tools/list**：一个 session 生命周期内只拉一次/server。
    - **失败必须可见**：server 起不来 → 工具标记 unavailable，**不**拖垮主流程。

依赖（2026-10-10 拍板：**独立 venv 隔离**，主环境零污染）
    `mcp` 包**不装进 bot 主环境**（会连带升级 pydantic 2.14 / httpx 等 24 个包，
    威胁发行包运行环境兼容性）。它装在**专属 venv** 里；本模块只负责：
    探测 venv → 生成 shim（见 _ensure_shim）→ 用 venv 的 python 起子进程说 MCP 协议。
    bot 主进程**不 import mcp**。venv 缺失/未就绪时整体降级：
    `available()` 返回 False，所有调用返回 {ok: False, error: "..."}，
    绝不抛异常出模块边界。

venv 解析顺序（_resolve_venv_python）
    1. 环境变量 EGGBLOOM_MCP_VENV 指向的目录（或其 Scripts/bin 下的 python）
    2. <data_dir>/mcp-venv
    3. 程序目录旁 E:\\EggBloomMCP（开发期默认；随包部署时可改 1/2）
    ★ 可用 mcp_hub.configure(venv_dir=..., python_exe=...) 显式指定。

配置来源（按优先级）
    1. explicit: mcp_hub.set_servers({...})
    2. <config_dir>/mcp.json
    3. <cwd>/.mcp.json
    4. ~/.workbuddy/mcp.json
    格式即 WorkBuddy / Claude 生态通用格式：
        {"mcpServers": {"filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"], "env": {...}}}}

★ 前置：shim（harness/mcp_shim.py）随包自带、单文件自足，**无需生成步骤**；
    主进程直接用 venv 的 python 跑它，stdio 上转 MCP JSON-RPC（list / call）。
    （`<venv>/python.exe -m harness.mcp_shim write` 只是可选：把 shim 复制到
    data_dir 方便独立编辑，默认用包内文件即可。）
"""
import json
import os
import subprocess
import sys
import threading
import time

_LOCK = threading.RLock()

_SERVERS_CFG = None          # {server: {command,args,env,url,type}}
_TOOL_CACHE = {}             # {server: {"tools": [...], "at": ts, "error": str|None}}
_SESSIONS = []               # 保持会话存活的弱引用容器（进程内）
_TOOL_INDEX = {}             # {mcp__server__tool: (server, tool_def)}

TOOL_CACHE_TTL = 600         # tools/list 缓存 10 分钟
_NAMESPACE = "mcp__"
_PROBE_TTL = 30.0            # venv/mcp 可用性探测缓存（秒）

# 显式配置（configure() 写入；None=未设，走自动解析）
_MCP_VENV_DIR = None         # venv 根目录（含 Scripts/bin）
_MCP_PYTHON_EXE = None       # 直接指定 venv 里的 python 可执行
_PROBE = (False, "", None, 0.0)    # (available, reason, cached_py, ts)


# ── 依赖探测（独立 venv 隔离：只问 venv，不 import mcp）──────────────
def _candidate_venv_pys():
    """按优先级生成候选 python 可执行路径。"""
    out = []
    if _MCP_PYTHON_EXE:
        out.append(_MCP_PYTHON_EXE)
    if _MCP_VENV_DIR:
        for p in (os.path.join(_MCP_VENV_DIR, "Scripts", "python.exe"),
                  os.path.join(_MCP_VENV_DIR, "bin", "python")):
            out.append(p)
    env_dir = (os.environ.get("EGGBLOOM_MCP_VENV") or "").strip()
    if env_dir:
        if os.path.basename(env_dir).lower().startswith("python"):
            out.append(env_dir)
        else:
            out.append(os.path.join(env_dir, "Scripts", "python.exe"))
            out.append(os.path.join(env_dir, "bin", "python"))
    try:
        from data_store import get_data_dir
        d = str(get_data_dir())
        out.append(os.path.join(d, "mcp-venv", "Scripts", "python.exe"))
        out.append(os.path.join(d, "mcp-venv", "bin", "python"))
    except Exception:
        pass
    # 程序目录旁（发行结构：…/EggBloom Desktop-new/resources/app → 上溯两级 = 发行根旁）
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))          # resources/app
    for rel in (os.path.join("..", "..", "..", "EggBloomMCP"),          # 发行根旁
                os.path.join("..", "..", "EggBloomMCP")):
        v = os.path.normpath(os.path.join(root, rel))
        out.append(os.path.join(v, "Scripts", "python.exe"))
        out.append(os.path.join(v, "bin", "python"))
    # ★ 开发期遗留位置兜底（2026-10-10：E:\EggBloomMCP 是早期手建的野 venv；
    #   正规位置是上面 <data_dir>/mcp-venv，随主目录走。保留兼容存量机器。）
    out.append(r"E:\EggBloomMCP\Scripts\python.exe")
    # 去重保序
    seen, uniq = set(), []
    for p in out:
        k = os.path.normcase(os.path.normpath(p))
        if k not in seen:
            seen.add(k); uniq.append(p)
    return uniq


def _resolve_venv_python():
    """返回 (python_path, venv_root)；未找到返回 (None, None)。"""
    for p in _candidate_venv_pys():
        try:
            if os.path.isfile(p) and os.access(p, os.X_OK):
                venv_root = os.path.dirname(os.path.dirname(p))  # Scripts 的上一级 = venv 根
                return p, venv_root
        except OSError:
            continue
    return None, None


def configure(venv_dir=None, python_exe=None):
    """显式指定 MCP venv（配置中心 / 测试用）。改后探测缓存立即失效。"""
    global _MCP_VENV_DIR, _MCP_PYTHON_EXE, _PROBE
    if venv_dir is not None:
        _MCP_VENV_DIR = str(venv_dir).strip() or None
    if python_exe is not None:
        _MCP_PYTHON_EXE = str(python_exe).strip() or None
    _PROBE = (False, "", None, 0.0)


def _probe(force=False):
    """探测 venv 里的 mcp 是否可用（子进程 import，30s 缓存）。→ (ok, reason, py, root)"""
    global _PROBE
    now = time.time()
    if not force and _PROBE[3] and (now - _PROBE[3]) < _PROBE_TTL and _PROBE[2]:
        return _PROBE[0], _PROBE[1], _PROBE[2], os.path.dirname(os.path.dirname(_PROBE[2]))
    py, root = _resolve_venv_python()
    if not py:
        ok, reason = False, ("mcp 包未安装（MCP 独立 venv 未找到；候选：EGGBLOOM_MCP_VENV 环境变量 / "
                             "<data_dir>/mcp-venv / 程序目录旁 EggBloomMCP）")
    else:
        try:
            r = subprocess.run([py, "-c", "import mcp, sys; print('MCP_OK')"],
                               capture_output=True, timeout=20)
            out = (r.stdout or b"").decode("utf-8", "replace")
            if "MCP_OK" in out:
                ok, reason = True, ""
            else:
                ok, reason = False, ("mcp 包未安装（venv 已存在）：%s -m pip install mcp" % py)
        except Exception as e:
            ok, reason = False, "探测 MCP venv 失败: %s: %s" % (type(e).__name__, e)
    _PROBE = (ok, reason, py, now)
    return ok, reason, py, root


def _mcp_available():
    ok, _r, _p, _n = _probe()
    return ok


def available():
    """MCP 通道是否可用（venv+mcp 就绪；shim 随包自带，文件在即可用）。"""
    return _mcp_available() and bool(_shim_path())


def unavailable_reason():
    ok, reason, _py, _root = _probe()
    if not ok:
        return reason
    if not _shim_path():
        return "MCP shim 文件不存在（harness/mcp_shim.py 未随包？）"
    return ""


# ── 配置 ────────────────────────────────────────────────────────────
def _candidate_cfg_files():
    out = []
    try:
        from data_store import get_data_dir
        out.append(os.path.join(get_data_dir(), "mcp.json"))
    except Exception:
        pass
    out.append(os.path.join(os.getcwd(), ".mcp.json"))
    out.append(os.path.join(os.path.expanduser("~"), ".workbuddy", "mcp.json"))
    return out


def _load_cfg_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        servers = data.get("mcpServers") or data.get("servers") or {}
        return servers if isinstance(servers, dict) else {}
    except Exception:
        return {}


def set_servers(servers):
    """显式设置 MCP server 配置（覆盖文件配置）。"""
    with _LOCK:
        global _SERVERS_CFG
        _SERVERS_CFG = dict(servers or {})
        _TOOL_CACHE.clear()
        _TOOL_INDEX.clear()


def servers(*, force=False):
    """返回当前生效的 server 配置 {name: cfg}。"""
    global _SERVERS_CFG
    with _LOCK:
        if _SERVERS_CFG is not None and not force:
            return dict(_SERVERS_CFG)
    merged = {}
    for p in _candidate_cfg_files():
        if os.path.isfile(p):
            merged.update(_load_cfg_file(p))
    with _LOCK:
        if _SERVERS_CFG is None or force:
            _SERVERS_CFG = merged
        return dict(_SERVERS_CFG)


def server_names():
    return sorted(servers().keys())


def config_path():
    """可写的 mcp.json 路径（data_dir/mcp.json）—— 集成主目录的关键。"""
    try:
        from data_store import get_data_dir
        return os.path.join(str(get_data_dir()), "mcp.json")
    except Exception:
        return os.path.join(os.getcwd(), ".mcp.json")


def save_servers(servers_dict):
    """把 {name: cfg} 写入 data_dir/mcp.json（原子覆盖），并立即 reset 重读。

    校验：值必须是 dict 且带 command 或 url —— 错误直接抛 ValueError，
    由端点层兜成可见的 {ok:False,error}。
    """
    if not isinstance(servers_dict, dict):
        raise ValueError("mcpServers 必须是对象")
    for name, cfg in servers_dict.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"server「{name}」配置必须是对象")
        if not (str(cfg.get("command") or "").strip() or str(cfg.get("url") or "").strip()):
            raise ValueError(f"server「{name}」缺少 command/url")
    p = config_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"mcpServers": servers_dict}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    reset()
    return p


def forget_server(name):
    """卸载某 server：清工具缓存/索引 + 注销 registry。返回被摘掉的工具名。"""
    with _LOCK:
        _TOOL_CACHE.pop(name, None)
        drop = [k for k, v in _TOOL_INDEX.items() if v[0] == name]
        for k in drop:
            _TOOL_INDEX.pop(k, None)
    try:
        from . import tool_registry as _tr
        for k in drop:
            _tr.unregister(k)
    except Exception:
        pass
    return drop


# ── 一键安装 venv（集成主目录：<data_dir>/mcp-venv）────────────────
_INSTALL = {"running": False, "done": None, "log": []}

def venv_target_dir():
    try:
        from data_store import get_data_dir
        return os.path.join(str(get_data_dir()), "mcp-venv")
    except Exception:
        return os.path.join(os.getcwd(), "mcp-venv")


def install_status():
    with _LOCK:
        return {"running": _INSTALL["running"], "done": _INSTALL["done"],
                "log": list(_INSTALL["log"])[-20:]}


def install_venv_async(python_ver_pin="mcp==2.3.0"):
    """后台线程建/修 <data_dir>/mcp-venv 并装 mcp 包（幂等：已存在只补装）。

    用**当前进程解释器**（发行包自带 python）建 venv —— 与宿主同版本，
    用户机器不需要任何预装 Python。
    """
    with _LOCK:
        if _INSTALL["running"]:
            return {"ok": False, "error": "安装已在进行中"}
        _INSTALL["running"] = True
        _INSTALL["done"] = None
        _INSTALL["log"] = []

    def _step(msg):
        with _LOCK:
            _INSTALL["log"].append(msg)

    def _run():
        import subprocess as _sp
        import sys as _sys
        target = venv_target_dir()
        exe = _sys.executable
        py = os.path.join(target, "Scripts", "python.exe") if os.name == "nt" \
            else os.path.join(target, "bin", "python")
        try:
            if os.path.isfile(py):
                _step(f"venv 已存在：{target}（跳过创建，补装依赖）")
            else:
                _step(f"创建 venv：{target}")
                _sp.run([exe, "-m", "venv", target], capture_output=True, timeout=180)
                if not os.path.isfile(py):
                    raise RuntimeError("venv 创建失败（python 不存在）")
            _step(f"安装 {python_ver_pin} …")
            r = _sp.run([py, "-m", "pip", "install", "--quiet", python_ver_pin],
                        capture_output=True, timeout=600)
            if r.returncode != 0:
                err = (r.stderr or b"").decode("utf-8", "replace")[-400:]
                raise RuntimeError(f"pip 失败(rc={r.returncode}): {err}")
            _step("安装完成 ✓")
            _probe(force=True)
            with _LOCK:
                _INSTALL["done"] = {"ok": True}
        except Exception as e:
            _step(f"失败：{e}")
            with _LOCK:
                _INSTALL["done"] = {"ok": False, "error": str(e)[:300]}
        finally:
            with _LOCK:
                _INSTALL["running"] = False

    threading.Thread(target=_run, daemon=True, name="mcp-venv-install").start()
    return {"ok": True, "started": True, "target": venv_target_dir()}


def cached_servers():
    """返回**已成功枚举过工具**的 server → 缓存条目（未加载的不在内）。

    供 tool_names_for / agent tool_picker 自动把"用户明确加载过的" MCP 工具
    塞进每轮工具集 —— 既不用关键词去猜（MCP 是用户显式配的扩展，配了就该在），
    也不违反惰性红线（没加载的绝不拉起子进程）。
    """
    with _LOCK:
        return {s: dict(c) for s, c in _TOOL_CACHE.items() if not c.get("error")}


def namespace(server, tool):
    return f"{_NAMESPACE}{server}__{tool}"


def parse_namespaced(full_name):
    """'mcp__fs__read_file' -> ('fs', 'read_file')；不合法返回 (None, None)。"""
    if not full_name or not full_name.startswith(_NAMESPACE):
        return None, None
    rest = full_name[len(_NAMESPACE):]
    if "__" not in rest:
        return None, None
    server, tool = rest.split("__", 1)
    return server or None, tool or None


# ── shim 通道（venv 子进程说 MCP 协议，主进程零 import）──────────────
def _shim_path():
    """返回可执行 shim 路径。优先 <data_dir>/mcp_shim.py（可覆盖），
    其次本包自带的 harness/mcp_shim.py（自足单文件，随包部署）。"""
    try:
        from data_store import get_data_dir
        p = os.path.join(str(get_data_dir()), "mcp_shim.py")
        if os.path.isfile(p):
            return p
    except Exception:
        pass
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_shim.py")
    return p if os.path.isfile(p) else ""


def _run_shim(action, payload, timeout=90):
    """跑 venv python <shim> <action>，stdin 一个 JSON，stdout 取最后一个 JSON 行。"""
    ok, reason, py, _root = _probe()
    if not ok:
        raise RuntimeError(reason or "MCP venv 不可用")
    shim = _shim_path()
    if not shim:
        raise RuntimeError("MCP shim 不存在（harness/mcp_shim.py 没随包？）")
    kw = {}
    if sys.platform == "win32":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    r = subprocess.run([py, shim, action],
                       input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       capture_output=True, timeout=timeout, **kw)
    out = (r.stdout or b"").decode("utf-8", "replace").strip()
    if not out:
        err = (r.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError("shim 无输出 (rc=%s): %s" % (r.returncode, err[-400:]))
    line = out.splitlines()[-1].strip()
    try:
        return json.loads(line)
    except Exception:
        raise RuntimeError("shim 输出非 JSON: %r" % line[:200])


# ── tools/list（惰性 + 缓存）───────────────────────────────────────
def _mcp_tool_to_openai(server, tool_def):
    """MCP tool 定义 → OpenAI 工具 schema（名字加命名空间）。"""
    name = tool_def.get("name") or ""
    desc = tool_def.get("description") or ""
    schema = tool_def.get("inputSchema") or {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": namespace(server, name),
            "description": f"[MCP:{server}] {desc}".strip()[:1024],
            "parameters": schema,
        },
        "_mcp": {"server": server, "tool": name},
    }


def list_tools(server, *, force=False):
    """列出某 server 的工具（OpenAI schema）。**惰性 + 缓存 + 失败可见**。"""
    if not _mcp_available():
        return {"ok": False, "error": unavailable_reason(), "tools": []}
    with _LOCK:
        c = _TOOL_CACHE.get(server)
        if c and not force and (time.time() - c.get("at", 0)) < TOOL_CACHE_TTL:
            return {"ok": not c.get("error"), "error": c.get("error"),
                    "tools": c.get("tools") or []}

    cfg = servers().get(server)
    if not cfg:
        return {"ok": False, "error": f"未配置的 MCP server: {server}", "tools": []}

    try:
        tools = _fetch_tools_sync(server, cfg)
        out = [_mcp_tool_to_openai(server, t) for t in tools if t.get("name")]
        with _LOCK:
            _TOOL_CACHE[server] = {"tools": out, "at": time.time(), "error": None}
            for t in out:
                _TOOL_INDEX[t["function"]["name"]] = (server, t)
        return {"ok": True, "error": None, "tools": out}
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        with _LOCK:
            _TOOL_CACHE[server] = {"tools": [], "at": time.time(), "error": err}
        return {"ok": False, "error": err, "tools": []}


def _fetch_tools_sync(server, cfg, timeout=90):
    """拉取 tools/list（经 venv shim 子进程；http/sse 暂不支持 → 明确报错）。"""
    cmd = cfg.get("command")
    if not cmd:
        url = cfg.get("url")
        if url:
            raise NotImplementedError("远程 MCP（url/sse/http）暂未支持，请用 stdio (command)")
        raise ValueError("server 配置缺少 command")
    resp = _run_shim("list", {"cfg": cfg}, timeout=timeout + 30)
    if not resp.get("ok"):
        raise RuntimeError(resp.get("error") or "shim list 失败")
    return resp.get("tools") or []


# ── tools/call ──────────────────────────────────────────────────────
def call(full_name, arguments, *, timeout=60):
    """调用 MCP 工具（full_name 必须已命名空间化）。失败返回 {ok:False}，不抛。"""
    if not _mcp_available():
        return {"ok": False, "error": unavailable_reason()}
    server, tool = parse_namespaced(full_name)
    if not server:
        return {"ok": False, "error": f"非法 MCP 工具名: {full_name}"}
    cfg = servers().get(server)
    if not cfg:
        return {"ok": False, "error": f"未配置的 MCP server: {server}"}
    if not cfg.get("command"):
        return {"ok": False, "error": f"server 配置缺少 command（远程 MCP 暂未支持）: {server}"}
    try:
        resp = _run_shim("call", {"cfg": cfg, "tool": tool,
                                  "arguments": arguments or {}}, timeout=timeout + 30)
        if not resp.get("ok"):
            return {"ok": False, "error": resp.get("error") or "shim call 失败"}
        return {"ok": True, "result": resp.get("result", "")}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def tool_functions(*, lazy=True):
    """返回 {full_name: callable} 供 agent 使用。

    lazy=True 时**只**为已缓存 tools/list 的 server 生成函数（不主动拉；
    需显式 `prefetch(server)`）。这保证"没被用到就零成本"。
    """
    out = {}
    with _LOCK:
        idx = dict(_TOOL_INDEX)
    for full_name, (server, _t) in idx.items():
        out[full_name] = (lambda fn: (lambda args: call(fn, args)))(full_name)
    return out


def prefetch(server):
    """显式预取某 server 的工具（第一次真正需要 MCP 时调用）。"""
    r = list_tools(server, force=True)
    if not r.get("ok"):
        return r
    return {"ok": True, "count": len(r.get("tools") or []),
            "names": [t["function"]["name"] for t in r["tools"]]}


def register_into_tool_registry(server, *, force=False):
    """把某 server 的工具注册进 harness.tool_registry（惰性 loader）。

    这样 tool_registry.resolve 就能像内置工具一样拿到 MCP 工具，且**顺序稳定**。
    """
    from . import tool_registry as _tr
    names = []
    with _LOCK:
        idx = {k: v for k, v in _TOOL_INDEX.items() if v[0] == server}
    if not idx and not force:
        # 尚未枚举过 → 注册惰性 loader，等 resolve 时再拉
        r = prefetch(server)
        if not r.get("ok"):
            return r
        with _LOCK:
            idx = {k: v for k, v in _TOOL_INDEX.items() if v[0] == server}
    for full_name, (srv, tool_def) in idx.items():
        _tr.register(full_name, tool_def, groups=(_tr.GROUP_MCP,), source=f"mcp:{srv}")
        names.append(full_name)
    return {"ok": True, "names": names}


def stats():
    with _LOCK:
        return {
            "mcp_available": _mcp_available(),
            "servers": server_names(),
            "cached": {s: {"count": len(c.get("tools") or []), "error": c.get("error")}
                       for s, c in _TOOL_CACHE.items()},
        }


def reset():
    with _LOCK:
        global _SERVERS_CFG
        _SERVERS_CFG = None
        _TOOL_CACHE.clear()
        _TOOL_INDEX.clear()
