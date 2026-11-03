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
        return None

    text = policy.pick(d.get("texts") or [])
    if not text:
        return None

    gid = str(p.get("group_id") or "")
    op = str(p.get("operator") or "")
    try:
        if gid:
            ok = bool(ctx.send_text(text, group_id=gid))
        elif op:
            ok = bool(ctx.send_text(text, target=op))
        else:
            return None
    except Exception as e:
        ctx.log(f"撤回反馈发送异常: {e}", "warn")
        return None

    if not ok:
        # 发送失败不吃冷却 —— 否则一次网络抖动就让这个群沉默一分钟
        return None

    try:
        ctx.set_state(STATE_KEY, policy.remember(
            _state(ctx), d.get("key") or "", now=now,
            message_id=str(p.get("message_id") or "")))
    except Exception:
        pass
    ctx.log(f"[撤回/{d.get('kind')}] {gid or op}: {text[:30]}")
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
