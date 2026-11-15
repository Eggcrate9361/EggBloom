# -*- coding: utf-8 -*-
"""hotrestart —— 重载插件 / 让 bot 自己重启，不用去关 GUI 再开。

## 为什么要这个

新装一个插件（比如 memewiki）要进 **bot 进程** 才注册得上 on_message 钩子。
以前只能关掉 main_window 再开一遍：整个 QQ 链路断一次，群里的消息全漏。
这里补两个动作：

    reload    单个插件热重载 —— 走 pm.reload()，fail-closed：
              有在途钩子未结束就**拒绝**，不硬摘模块（见 plugin_manager 的 A4/A14）。
    restart   bot 自己重启 —— 用 systemd-run 起一个**独立**瞬态 timer，
              延迟几秒再 systemctl restart。

## restart 为什么不能直接 subprocess

bot 跑在 `eggbloom-bot.service` 里，KillMode=control-group。
在**本进程里** fork 一个 `sleep 3 && systemctl restart` 的话，重启一开始
systemd 就把整个 cgroup 端了 —— 那个 sleep 连同 systemctl 一起被杀，
重启命令自己死在自己触发的重启里。所以必须扔到 cgroup 外面：
`systemd-run --on-active=N` 建的是独立瞬态 timer 单元，杀本服务不影响它。

## 触发

    !重载 memewiki      热重载单个插件（已在注册表里的）
    !装 memewiki        把一个还没进注册表的插件装进当前进程（不用重启）
    !重启bot            3 秒后重启（先让回复发出去）
    !hotrestart         看状态

全部只认管理员（owner_qqs → admin_qqs → target_qqs）。

**平台**：`reload` / `install` 任何平台都能用；`restart` 只支持 Linux + systemd
（见下方 `_RESTART_SUPPORTED`），其余平台直接返回原因，不抛栈。

## 动作（/api/plugins/action → plugin_id=hotrestart）

    install   把本插件注册进 bot 进程（首次装载用，不需要重启）
    status    服务状态 + 插件计数
    reload    params={"name": "<插件名>"}
    restart   params={"delay": 3}
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time

PLUGIN_SPEC = 1
PLUGIN_MANIFEST = {
    "name": "hotrestart",
    "version": "1.0.0",
    "api_version": 2,
    "author": "EggBloom",
    "description": "热重载插件 / bot 自己重启（systemd-run 瞬态 timer，不自杀）",
    "dependencies": [],
    "priority": 20,
    "default_enabled": True,
}

#: 要重启的服务名（按顺序探测）
SERVICES = ("eggbloom-bot",)

#: 默认延迟：留够时间把回复发出去
DEFAULT_DELAY = 3

# ── 平台闸 ──────────────────────────────────────────────────
# `reload` / `install` 走插件管理器，任何平台都能用；`restart` 整条推理都建立在
# 「bot 跑在 systemd 具名服务里、KillMode=control-group」上 —— 换到 Windows（bot 是
# GUI 的子进程）或 macOS（launchd）就没有 systemctl，硬跑只会在点下去的瞬间抛
# FileNotFoundError。所以按平台开闸，把「为什么不行」直接说给人听，而不是让它报栈。
_RESTART_SUPPORTED = sys.platform.startswith("linux") and bool(shutil.which("systemctl"))
_RESTART_HINT = ("自重启只支持 Linux + systemd（bot 跑在 systemd 具名服务里）。"
                 "当前平台请用 GUI 或服务管理器重启；"
                 "`!重载 <插件>` / `!装 <插件>` 不受影响，照常可用。")


# ── 基础设施 ────────────────────────────────────────────────

def _pm():
    """取当前进程的插件管理器；取不到返回 None。"""
    try:
        from plugins.plugin_manager import get_plugin_manager
        return get_plugin_manager()
    except Exception:
        return None


def _run(argv, timeout: float = 15.0):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except Exception as e:
        return -1, "", f"{type(e).__name__}: {e}"


def _active_service() -> str:
    """返回当前活着的服务名；都不活返回空串。"""
    for s in SERVICES:
        rc, out, _ = _run(["systemctl", "is-active", s], timeout=5)
        if out.strip() == "active":
            return s
    return ""


def _since(service: str) -> str:
    """服务启动时间（给状态用）。"""
    rc, out, _ = _run(["systemctl", "show", service,
                       "-p", "ActiveEnterTimestamp"], timeout=5)
    return out.split("=", 1)[1].strip() if "=" in out else ""


def _admins(ctx) -> set:
    """管理员名单：owner_qqs → admin_qqs → target_qqs。"""
    cfg = ctx.get_config() or {}
    for key in ("owner_qqs", "admin_qqs", "target_qqs"):
        v = cfg.get(key)
        if isinstance(v, (list, tuple)) and v:
            return {str(x).strip() for x in v if str(x).strip()}
        if isinstance(v, str) and v.strip():
            return {v.strip()}
    return set()


def _is_admin(ctx, sender) -> bool:
    s = str(sender or "").strip()
    if not s:
        return False
    cfg = ctx.get_config() or {}
    if s == str(cfg.get("bot_qq") or "").strip():
        return True
    return s in _admins(ctx)


# ── 两个真动作 ──────────────────────────────────────────────

def do_reload(name: str) -> dict:
    """热重载一个插件。fail-closed：有在途钩子就拒绝，不硬来。"""
    name = str(name or "").strip()
    if not name:
        return {"ok": False, "error": "没给插件名"}
    pm = _pm()
    if pm is None:
        return {"ok": False, "error": "插件系统未初始化"}
    if not pm.is_loaded(name):
        return {"ok": False, "error": f"{name} 没在注册表里（拼错？还是没装？）"}
    inflight = pm.inflight_count(name) if hasattr(pm, "inflight_count") else 0
    ok = bool(pm.reload(name))
    err = "" if ok else (pm.get_error(name) or "")
    if not ok and not err:
        err = f"卸载被拒（{inflight} 个在途钩子未结束），fail-closed 不动它"
    # 2026-11-15 全补：热重载插件是"我换了自己的零件"，此前只有一行 stdout。
    # 重载后模型的人格/工具集变了，却被问"你刚才动什么了"答不上来。
    try:
        import self_actions as _sa
        _sa.record("plugin", detail=f"{'重载' if ok else '重载失败'}插件 {name}")
    except Exception:
        pass
    return {"ok": ok, "name": name, "inflight": inflight,
            "enabled": bool(pm.is_enabled(name)), "error": err}


def do_restart(delay: int = DEFAULT_DELAY, target: str = "",
               is_group: bool = False) -> dict:
    """延迟重启 bot。命令扔到 cgroup 外面，否则会把自己杀掉。

    给了 target 就先写一张便条（harness/restart_notice），起来后 bot_core
    会朝这个会话报一句「我回来了」。at 记的是**真断掉的时刻**（now+delay），
    不是现在 —— 否则启动时算出来的离线时长是负数。
    """
    if not _RESTART_SUPPORTED:
        # 先拦再写便条：写了一半失败会在 data/ 里留一张永远等不到重启的便条，
        # 下次真重启时冒出一句莫名其妙的「我回来了」。
        return {"ok": False, "platform": sys.platform, "error": _RESTART_HINT}
    try:
        delay = max(1, min(600, int(delay)))
    except (TypeError, ValueError):
        delay = DEFAULT_DELAY
    if str(target or "").strip():
        try:
            from harness import restart_notice as _rn
            _rn.write_marker(target, is_group=bool(is_group),
                             kind="restart", at=time.time() + delay)
        except Exception:
            pass
    service = _active_service()
    if not service:
        return {"ok": False,
                "error": f"没找到活着的服务（{'/'.join(SERVICES)}），"
                         f"不是 systemd 起的就只能手动重启"}
    unit = f"eggbloom-selfrestart-{int(time.time())}"
    argv = ["sudo", "-n", "systemd-run", "--collect",
            f"--on-active={delay}", f"--unit={unit}",
            "/bin/systemctl", "restart", service]
    rc, out, err = _run(argv)
    ok = rc == 0
    return {"ok": ok, "service": service, "delay": delay, "unit": unit,
            "cmd": " ".join(argv),
            "error": "" if ok else (err or out or f"systemd-run 退出码 {rc}")[:400]}


# ── 动作表 ──────────────────────────────────────────────────

def act_install(params=None):
    """把一个还没进注册表的插件装进当前进程（不用重启）。默认装自己。

    这就是「新插件要重启才生效」的正解：`pm.load()` 走完整校验（清单 / 直连
    发送扫描 / ABI），装上的钩子立刻参与派发 —— 不需要重启 bot，更不需要
    关 GUI。已经在注册表里的走 `reload` 才有意义（load 会直接复用旧模块）。
    """
    pm = _pm()
    if pm is None:
        return {"ok": False, "error": "插件系统未初始化"}
    name = str((params or {}).get("name") or (params or {}).get("plugin_id")
               or "hotrestart").strip()
    if name not in pm.discover():
        return {"ok": False, "name": name,
                "error": f"plugins/{name} 里没有可加载的插件（目录或 __init__.py 不存在）"}
    already = pm.is_loaded(name)
    ok = bool(pm.load(name)) if not already else True
    err = "" if ok else (pm.get_error(name) or "")
    if err:
        err = err.strip().splitlines()[-1][:200]   # 只留最后一行，别把 traceback 甩出去
        # 加载失败会在注册表里留一条 error 占位，装不上就别让它挂在那儿冒充插件
        try:
            pm._plugins.pop(name, None)
        except Exception:
            pass
    return {"ok": ok, "name": name, "already_loaded": already,
            "loaded": bool(pm.is_loaded(name)),
            "enabled": bool(pm.is_enabled(name)),
            "error": err}


def act_status(params=None):
    service = _active_service()
    pm = _pm()
    plugins = []
    if pm is not None:
        try:
            plugins = [p.get("name") for p in pm.list_plugins()]
        except Exception:
            plugins = []
    return {"ok": bool(service), "service": service or "(未探测到)",
            "since": _since(service) if service else "",
            "restart_supported": _RESTART_SUPPORTED,
            "plugins_loaded": len(plugins),
            "self_loaded": bool(pm and pm.is_loaded("hotrestart"))}


def act_reload(params=None):
    p = params or {}
    return do_reload(p.get("name") or p.get("plugin") or p.get("plugin_id") or "")


def act_restart(params=None):
    p = params or {}
    return do_restart(p.get("delay") or DEFAULT_DELAY,
                      target=str(p.get("target") or p.get("user_id")
                                 or p.get("group_id") or ""),
                      is_group=bool(p.get("is_group") or p.get("group_id")))


def act_probe(params=None):
    """拿一句话去问所有 on_message 插件，**只回文本不发出去**（排查用）。

    新插件装上后到底有没有接上钩子，光看"已加载"是看不出来的 —— 这里直接
    走一遍派发，返回谁认领了、回了什么。
    """
    pm = _pm()
    if pm is None:
        return {"ok": False, "error": "插件系统未初始化"}
    text = str((params or {}).get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "没给 text"}
    ev = {"type": str((params or {}).get("type") or "private"),
          "sender": str((params or {}).get("sender") or ""),
          "group_id": str((params or {}).get("group_id") or ""),
          "text": text, "message_id": 0, "time": 0}
    r = pm.execute_typed_hooks_first("on_message", event=ev)
    return {"ok": True, "text": text, "reply": str(r or "")[:600],
            "handled": bool(r)}


PLUGIN_ACTIONS = {
    "install": act_install,
    "status": act_status,
    "reload": act_reload,
    "restart": act_restart,
    "probe": act_probe,
}


# ── 钩子 ────────────────────────────────────────────────────

def on_load(ctx):
    ctx.log("hotrestart loaded — !重载 <插件> / !重启bot")


def on_unload(ctx):
    ctx.log("hotrestart unloaded")


def _say(ok: bool, name: str, r: dict) -> str:
    if ok:
        return f"{name} 好了。"
    why = (r or {}).get("error") or (r or {}).get("reason") or "原因不明"
    return f"{name} 没成：{why}"


def on_message(ctx, event):
    text = (event.get("text") or "").strip()
    if not text.startswith("!"):
        return None
    head, _, arg = text[1:].partition(" ")
    head, arg = head.strip(), arg.strip()

    if head in ("重载", "reload"):
        if not _is_admin(ctx, event.get("sender")):
            return "这得主人发话。"
        if not arg:
            return "重载哪个？`!重载 memewiki`"
        r = do_reload(arg)
        return _say(r.get("ok"), f"重载 {arg}", r)

    if head in ("装", "load", "install"):
        if not _is_admin(ctx, event.get("sender")):
            return "这得主人发话。"
        if not arg:
            return "装哪个？`!装 memewiki`"
        r = act_install({"name": arg})
        if r.get("ok"):
            return (f"{arg} 已经在线上了（{r['loaded'] and '已加载'}"
                    f"{'，本来就装着' if r.get('already_loaded') else '，没重启'}）。")
        return f"{arg} 装不上：{r.get('error')}"

    if head in ("重启bot", "selfrestart", "hotrestart"):
        if not _is_admin(ctx, event.get("sender")):
            return "这得主人发话。"
        if head == "hotrestart":
            s = act_status({})
            return (f"服务 {s['service']}｜起于 {s['since'] or '?'}｜"
                    f"插件 {s['plugins_loaded']} 个")
        # 通报回原会话：群里戳的就回群，私聊戳的就回私聊
        r = do_restart(DEFAULT_DELAY,
                       target=str(event.get("group_id") or event.get("sender") or ""),
                       is_group=bool(event.get("group_id")))
        if r.get("ok"):
            return f"{r['delay']} 秒后重启（timer {r['unit']}）。别急，回复先发出去。"
        return f"重启没排上：{r.get('error')}"

    return None


PLUGIN_HOOKS = {
    "on_load": on_load,
    "on_unload": on_unload,
    "on_message": on_message,
}
