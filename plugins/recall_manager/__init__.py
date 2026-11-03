# -*- coding: utf-8 -*-
"""recall_manager —— 撤回反馈插件（2026-11-03，刀 2 接线）

订阅 `notice.recall`，用 `policy.decide()` 判要不要回、回哪一类，再走
`ctx.send_text`（插件唯一出口 → 出站队列）回一句。

## 为什么默认关

`default_enabled: False`。主链自己的撤回反馈还在跑，两边同时开会各回一句。
切换要**两个动作同时做**：

    bot_config.json  → "recall_feedback_by": "plugin"
    插件页           → 启用 recall_manager

只做一半都是错的：
  · 只开插件、不改配置 → 主链照回 + 插件也回 = **双回**
  · 只改配置、不开插件 → 主链让位了、没人接 = **谁都不回**

## 状态

冷却与去重走 ABI 的 `get_state/set_state`（`plugins/state/recall_manager.json`），
不自己另立门户。这样重启后冷却还在 —— 原来是主链内存版，重启即清零，会连回两句。
"""
from __future__ import annotations

import threading
import time

PLUGIN_SPEC = 1
PLUGIN_MANIFEST = {
    "name": "recall_manager",
    "version": "0.1.0",
    "api_version": 1,
    "author": "EggBloom",
    "description": "撤回反馈：订阅 notice.recall，按策略层判定后再回一句（默认关闭）",
    "dependencies": [],
    "priority": 85,
    # 2026-11-03：默认关。开之前先把 bot_config 的 recall_feedback_by 改成 "plugin"，
    # 否则主链和本插件会各回一句。
    "default_enabled": False,
}

#: get_state/set_state 的键名
STATE_KEY = "recall_state"

#: ★ 2026-11-03：内存里的"刚处理过"占位。
#   为什么需要：发送改成后台线程之后，"读状态 → 判冷却 → 写状态"全在线程里，
#   两个重复事件（协议层会把同一条撤回推两遍）会在同毫秒都读到旧状态、
#   都判定该回 → 各发一条（实测 17:31 连发两条）。
#   占位必须**同步**做（主线程里、加锁），只有真正的投递进线程。
_INST_LOCK = threading.Lock()
_INST_RECENT: dict = {}
_INST_TTL = 180.0

_ctx = None


def on_load(ctx):
    global _ctx
    _ctx = ctx
    ctx.log("recall_manager loaded（默认关闭；开启前把 recall_feedback_by 改成 plugin）")


def on_enable(ctx):
    global _ctx
    _ctx = ctx
    ctx.log("recall_manager enabled —— 主链的撤回反馈必须已让位（recall_feedback_by=plugin）")


def on_disable(ctx):
    ctx.log("recall_manager disabled —— 记得把 recall_feedback_by 改回 core，否则撤回没人回")


def on_unload(ctx):
    ctx.log("recall_manager unloaded")


def _cfg(ctx) -> dict:
    try:
        c = ctx.get_config()
        return c if isinstance(c, dict) else {}
    except Exception:
        return {}


def _state(ctx) -> dict:
    try:
        s = ctx.get_state(STATE_KEY, {})
        return s if isinstance(s, dict) else {}
    except Exception:
        return {}


def on_event(ctx, event_name: str = "", payload: dict = None, **kwargs):
    """只接 `notice.recall`，其余一律不管（事件总线是广播的）。"""
    if str(event_name or "") != "notice.recall":
        return None
    from plugins.recall_manager import policy

    p = payload if isinstance(payload, dict) else {}
    # 兜底：老载荷里没有 notice_type 时按 group_id 是否为空推断 —— 不让"字段缺失"
    # 变成"整个插件静默失灵"。
    if not p.get("notice_type"):
        p = dict(p, notice_type="group_recall" if p.get("group_id") else "friend_recall")
    now = time.time()
    d = policy.decide(p, cfg=_cfg(ctx), now=now, state=_state(ctx))
    if d.get("act") != policy.ACT_SAY:
        # 跳过也要留痕：静默跳过等于把"为什么不回"变成不可观测，
        # 2026-11-03 真机就是因为插件一声不吭，排查方向被带偏了三轮。
        try:
            ctx.log(f"[撤回/跳过] {d.get('reason') or '未给原因'}")
        except Exception:
            pass
        return None

    text = policy.pick(d.get("texts") or [])
    if not text:
        return None

    gid = str(p.get("group_id") or "")
    op = str(p.get("operator") or "")
    # 兜底：万一上游给的是"昵称(QQ号)"这种展示形态，发送前必须取纯号 ——
    # 拿展示串当 target 是发不出去的（2026-11-03 真机踩到）。
    if "(" in op and ")" in op:
        import re as _re
        _m = _re.search(r"\((\d+)\)", op)
        if _m:
            op = _m.group(1)
    if not gid and not op:
        return None

    # 同步占位：同一条撤回（含协议层重复推送）只允许一个线程去投递。
    _dedupe = "%s:%s" % (d.get("key") or "", str(p.get("message_id") or ""))
    with _INST_LOCK:
        _now_chk = time.time()
        for _k in [k for k, t in _INST_RECENT.items() if _now_chk - t > _INST_TTL]:
            _INST_RECENT.pop(_k, None)
        if _dedupe in _INST_RECENT:
            try:
                ctx.log(f"[撤回/跳过] 重复事件（同一条撤回已处理）")
            except Exception:
                pass
            return None
        _INST_RECENT[_dedupe] = _now_chk

    # ★ 2026-11-03（真机教训）：发送**绝不能阻塞主循环**。
    #   on_event 是在 bot_core 的主循环线程里被调用的，而 ctx.send_text 要
    #   走出站队列（限频/HTTP），实测阻塞 6 秒。那 6 秒里同一条消息的"入缓冲"
    #   被推迟，等它进缓冲时撤回事件早已处理完（取消的是空缓冲）→
    #   bot 照样回复了一条用户已撤回的消息。
    #   判定照旧同步做（要立刻决定要不要回），投递丢后台线程。
    def _deliver():
        try:
            ok = (bool(ctx.send_text(text, group_id=gid)) if gid
                  else bool(ctx.send_text(text, target=op)))
        except Exception as e:
            try:
                ctx.log(f"撤回反馈发送异常: {e}", "warn")
            except Exception:
                pass
            return
        if not ok:
            # 发送失败不吃冷却 —— 否则一次网络抖动就让这个群沉默一分钟
            return
        try:
            ctx.set_state(STATE_KEY, policy.remember(
                _state(ctx), d.get("key") or "", now=now,
                message_id=str(p.get("message_id") or "")))
        except Exception:
            pass
        try:
            ctx.log(f"[撤回/{d.get('kind')}] {gid or op}: {text[:30]}")
        except Exception:
            pass

    threading.Thread(target=_deliver, daemon=True, name="recall-deliver").start()
    return None


def get_api_status():
    """插件页的连通状态：主链是否已让位、当前冷却表里有什么。

    返回形状恒定（`ok` / `status` / `detail` 三键始终在）——
    调用方不该因为"插件还没加载"就要写一套额外的分支。
    """
    ctx = _ctx
    if ctx is None:
        return {"ok": False, "status": "unloaded", "detail": "未加载",
                "cooldown_keys": 0, "seen": 0}
    by = str(_cfg(ctx).get("recall_feedback_by", "core")).lower()
    st = _state(ctx)
    return {
        "ok": True,
        "status": "ready" if by == "plugin" else "idle",
        "detail": ("主链已让位" if by == "plugin"
                   else "主链仍在自己回（recall_feedback_by=core）—— 开启本插件前请先改配置"),
        "cooldown_keys": len((st.get("last_fire") or {})),
        "seen": len((st.get("seen") or {})),
    }


#: ★ ABI v1 要求：钩子必须显式登记在 PLUGIN_HOOKS 里，光定义函数不算。
#:   漏了这一步的表现很隐蔽 —— 插件"加载成功"（_loaded: true），但 `_hooks` 是空表，
#:   事件一条也收不到，面板上也不报错。2026-11-03 真机验证时踩到。
PLUGIN_HOOKS = {
    "on_load": on_load,
    "on_enable": on_enable,
    "on_disable": on_disable,
    "on_unload": on_unload,
    "on_event": on_event,
}
