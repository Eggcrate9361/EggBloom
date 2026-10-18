# -*- coding: utf-8 -*-
"""harness/mcp_shim.py —— MCP 子进程垫片（跑在**独立 venv** 里，主进程零 import）。

为什么存在（2026-10-10 拍板：独立 venv 隔离）
    `mcp` 包会连带升级 pydantic 2.14 / httpx 等 24 个包 —— 不能装进 bot 主环境。
    本文件用 venv 的 python 单独运行，在 stdio 上替主进程说 MCP 协议：

        <venv>/python.exe mcp_shim.py list   < stdin.json   （列工具）
        <venv>/python.exe mcp_shim.py call   < stdin.json   （调工具）
        <venv>/python.exe mcp_shim.py write                  （把自己复制到 data_dir）
        <venv>/python.exe mcp_shim.py selftest               （只验证 import mcp）

    stdin 一个 JSON：
        list : {"cfg": {"command":..., "args":[...], "env":{...}}}
        call : {"cfg": {...}, "tool": "name", "arguments": {...}}
    stdout **最后一行**一个 JSON（前面允许有杂音，调用方取末行）：
        {"ok": true, "tools": [{"name","description","inputSchema"}, ...]}
        {"ok": true, "result": "<文本>"}
        {"ok": false, "error": "原因"}

    ★ 单文件自足：不 import harness 包内任何东西，可独立复制运行。
"""
import asyncio
import json
import os
import sys


def _emit(obj):
    """stdout 只出这一行 JSON（ensure_ascii 防止管道编码炸）。"""
    sys.stdout.write(json.dumps(obj, ensure_ascii=True, default=str) + "\n")
    sys.stdout.flush()


def _err_str(e):
    """异常 → 可读原因。ExceptionGroup（mcp SDK 的 TaskGroup 壳）展平到叶子，
    否则用户只会看到 'unhandled errors in a TaskGroup' 这种无信息量的话。"""
    seen = []

    def walk(exc, depth=0):
        if depth > 6:
            return
        if isinstance(exc, BaseExceptionGroup):
            for sub in exc.exceptions:
                walk(sub, depth + 1)
            return
        seen.append("%s: %s" % (type(exc).__name__, str(exc)[:200]))
        # __cause__/__context__ 链（server 起不来时真实原因常在 cause 里）
        nxt = exc.__cause__ or exc.__context__
        if nxt is not None and nxt is not exc and depth < 4:
            walk(nxt, depth + 1)

    walk(e)
    return " <- ".join(dict.fromkeys(seen)) or ("%s: %s" % (type(e).__name__, str(e)[:200]))


async def _with_session(cfg, fn):
    import mcp.client.stdio as _stdio
    from mcp import ClientSession

    cmd = cfg.get("command")
    args = cfg.get("args") or []
    env = None
    if cfg.get("env"):
        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in cfg["env"].items()})
    init_timeout = float(cfg.get("initTimeout") or 30)

    params = _stdio.StdioServerParameters(command=cmd, args=list(args), env=env)
    async with _stdio.stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=init_timeout)
            return await fn(session)


async def _do_list(payload):
    cfg = payload.get("cfg") or {}

    async def _fn(session):
        resp = await asyncio.wait_for(session.list_tools(), timeout=60)
        out = []
        for t in (getattr(resp, "tools", None) or []):
            out.append({
                "name": getattr(t, "name", None),
                "description": getattr(t, "description", "") or "",
                "inputSchema": getattr(t, "inputSchema", None) or {"type": "object"},
            })
        return out

    try:
        tools = await _with_session(cfg, _fn)
        return {"ok": True, "tools": tools}
    except Exception as e:
        return {"ok": False, "error": _err_str(e)}


async def _do_call(payload):
    cfg = payload.get("cfg") or {}
    tool = payload.get("tool") or ""
    arguments = payload.get("arguments") or {}
    call_timeout = float(cfg.get("callTimeout") or 90)

    async def _fn(session):
        resp = await asyncio.wait_for(
            session.call_tool(tool, arguments=arguments), timeout=call_timeout)
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

    try:
        res = await _with_session(cfg, _fn)
        return {"ok": True, "result": res}
    except Exception as e:
        return {"ok": False, "error": _err_str(e)}


def _selftest():
    try:
        import mcp  # noqa: F401
        _emit({"ok": True, "mcp": True})
    except Exception as e:
        _emit({"ok": False, "error": "%s: %s" % (type(e).__name__, e)})


def _write():
    """把自己复制到 <data_dir>/mcp_shim.py（可选部署步骤）。"""
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from data_store import get_data_dir
        dst = os.path.join(str(get_data_dir()), "mcp_shim.py")
        src = os.path.abspath(__file__)
        if os.path.normcase(src) != os.path.normcase(dst):
            with open(src, "rb") as f:
                data = f.read()
            with open(dst, "wb") as f:
                f.write(data)
        _emit({"ok": True, "path": dst})
    except Exception as e:
        _emit({"ok": False, "error": "%s: %s" % (type(e).__name__, e)})


def main():
    action = (sys.argv[1] if len(sys.argv) > 1 else "list").strip().lower()
    if action == "write":
        return _write()
    if action == "selftest":
        return _selftest()
    try:
        raw = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else sys.stdin.read()
        payload = json.loads(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw)
    except Exception as e:
        return _emit({"ok": False, "error": "stdin JSON 解析失败: %s" % e})
    try:
        if action == "list":
            res = asyncio.run(_do_list(payload))
        elif action == "call":
            res = asyncio.run(_do_call(payload))
        else:
            res = {"ok": False, "error": "未知动作: %s（list/call/write/selftest）" % action}
    except Exception as e:
        res = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:400])}
    _emit(res)


if __name__ == "__main__":
    main()
