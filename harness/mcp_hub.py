# -*- coding: utf-8 -*-
"""harness/mcp_hub.py —— MCP 最小实现（**惰性 + 命名空间 + 缓存**）。

设计红线（研究结论）
    - **必须惰性**：绝不在启动时枚举所有 MCP server 的所有工具（每个 tools/list 都要
      起进程 + 握手，慢且可能挂）。启动只读**配置**，工具 schema 第一次被指名时才拉。
    - **必须命名空间化**：一律 `mcp__<server>__<tool>`，避免与内置工具撞名。
    - **必须缓存 tools/list**：一个 session 生命周期内只拉一次/server。
    - **失败必须可见**：server 起不来 → 工具标记 unavailable，**不**拖垮主流程。

依赖
    官方 `mcp` 包（`pip install mcp`）。**未安装时本模块整体降级**：
    `available()` 返回 False，所有调用返回 {ok: False, error: "mcp 包未安装"}，
    绝不抛异常出模块边界。

配置来源（按优先级）
    1. explicit: mcp_hub.set_servers({...})
    2. <config_dir>/mcp.json
    3. <cwd>/.mcp.json
    4. ~/.workbuddy/mcp.json
    格式即 WorkBuddy / Claude 生态通用格式：
        {"mcpServers": {"filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"], "env": {...}}}}
"""
import json
import os
import threading
import time

_LOCK = threading.RLock()

_SERVERS_CFG = None          # {server: {command,args,env,url,type}}
_TOOL_CACHE = {}             # {server: {"tools": [...], "at": ts, "error": str|None}}
_SESSIONS = []               # 保持会话存活的弱引用容器（进程内）
_TOOL_INDEX = {}             # {mcp__server__tool: (server, tool_def)}

TOOL_CACHE_TTL = 600         # tools/list 缓存 10 分钟
_NAMESPACE = "mcp__"


# ── 依赖探测 ────────────────────────────────────────────────────────
def _mcp_available():
    try:
        import mcp  # noqa: F401
        return True
    except Exception:
        return False


def available():
    """mcp 包是否可用（UI 可据此提示用户安装）。"""
    return _mcp_available()


def unavailable_reason():
    return "" if _mcp_available() else "python 包 `mcp` 未安装（pip install mcp）"


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


def _fetch_tools_sync(server, cfg):
    """同步拉取 tools/list（stdio 优先；http/sse 暂不支持 → 明确报错）。"""
    cmd = cfg.get("command")
    if not cmd:
        url = cfg.get("url")
        if url:
            raise NotImplementedError("远程 MCP（url/sse/http）暂未支持，请用 stdio (command)")
        raise ValueError("server 配置缺少 command")
    import asyncio
    import mcp.client.stdio as _stdio
    from mcp import ClientSession

    args = cfg.get("args") or []
    env = None
    if cfg.get("env"):
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in cfg["env"].items()})

    async def _go():
        params = _stdio.StdioServerParameters(command=cmd, args=list(args), env=env)
        async with _stdio.stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                resp = await session.list_tools()
                out = []
                for t in (getattr(resp, "tools", None) or []):
                    out.append({
                        "name": getattr(t, "name", None),
                        "description": getattr(t, "description", "") or "",
                        "inputSchema": getattr(t, "inputSchema", None) or {"type": "object"},
                    })
                return out

    return _run_coro(_go())


def _run_coro(coro):
    """在**当前线程**跑协程（本机无事件循环时直接 asyncio.run）。"""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        # 已在事件循环里（少见：bot_core 是线程模型）→ 新线程跑，避免嵌套
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(asyncio.run, coro).result(timeout=60)
    return asyncio.run(coro)


# ── tools/call ──────────────────────────────────────────────────────
def call(full_name, arguments, *, timeout=60):
    """调用 MCP 工具（full_name 必须已命名空间化）。"""
    if not _mcp_available():
        return {"ok": False, "error": unavailable_reason()}
    server, tool = parse_namespaced(full_name)
    if not server:
        return {"ok": False, "error": f"非法 MCP 工具名: {full_name}"}
    cfg = servers().get(server)
    if not cfg:
        return {"ok": False, "error": f"未配置的 MCP server: {server}"}
    try:
        res = _call_sync(server, cfg, tool, arguments or {})
        return {"ok": True, "result": res}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _call_sync(server, cfg, tool, arguments):
    import mcp.client.stdio as _stdio
    from mcp import ClientSession

    cmd = cfg.get("command")
    if not cmd:
        raise ValueError("server 配置缺少 command（远程 MCP 暂未支持）")
    args = cfg.get("args") or []
    env = None
    if cfg.get("env"):
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in cfg["env"].items()})

    async def _go():
        params = _stdio.StdioServerParameters(command=cmd, args=list(args), env=env)
        async with _stdio.stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                resp = await session.call_tool(tool, arguments=arguments)
                parts = []
                for c in (getattr(resp, "content", None) or []):
                    text = getattr(c, "text", None)
                    if text is not None:
                        parts.append(text)
                    else:
                        parts.append(json.dumps(
                            {"type": getattr(c, "type", "unknown")}, ensure_ascii=False))
                if not parts:
                    return json.dumps({"isError": bool(getattr(resp, "isError", False))},
                                      ensure_ascii=False)
                return "\n".join(parts)

    return _run_coro(_go())


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
