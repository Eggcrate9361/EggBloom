# -*- coding: utf-8 -*-
"""plugins/proactive_poke —— 主动戳一戳（1.6.0 §4.1.1）

判定全在 `policy.py`（纯函数，可单测）；本文件只做三件事：

  1. **收事实**：订阅 `message.in` 记"最后一次互动"，订阅 `notice.poke` 记"被谁戳过"
  2. **心跳**：每 `poke_proactive_heartbeat_sec` 评估一次要不要戳
  3. **动作**：走 ABI 的 `api_call("send_poke")`，失败不重试、不换目标

## 为什么失败不重试

主动动作和被动回复不是一回事：回复失败对方会再问，主动戳失败再补一次就变成
"连戳两下"。宁可这次不戳，下一轮心跳还在。

## 为什么默认关闭

它会在你不在场的时候，以你的名义去打扰别人。这种能力必须由用户显式打开。
"""
from __future__ import annotations

import threading
import time

from . import policy as P

PLUGIN_SPEC = 1
PLUGIN_MANIFEST = {
    "name": "proactive_poke",
    "version": "1.0.0",
    "description": "主动戳一戳：久未互动时按闸门戳一下（默认关闭）",
    "author": "EggBloom",
    "priority": 80,
    "requires": [],
    "provides": ["proactive.poke"],
    "default_enabled": False,
}
PLUGIN_HOOKS = {}

STATE_KEY = "poke_state"

_stop = threading.Event()
_thread = None
_lock = threading.Lock()


def _state(ctx):
    return dict(ctx.get_state(STATE_KEY, {}) or {})


def _save(ctx, st):
    ctx.set_state(STATE_KEY, st)
    return st


def _targets(cfg):
    """候选目标：优先 `poke_proactive_targets`，否则用主动说话那套 `target_qqs`。"""
    raw = cfg.get("poke_proactive_targets") or cfg.get("target_qqs") or []
    if isinstance(raw, str):
        raw = [x for x in raw.replace("，", ",").split(",")]
    return [str(x).strip() for x in raw if str(x or "").strip()]


def _facts(ctx, targets, st):
    """把状态整理成 policy 要的候选列表。"""
    last_int = st.get("last_interact") or {}
    last_poke = st.get("last_poked") or {}
    ignored = st.get("ignored") or {}
    return [{
        "target": t,
        "last_interact": float(last_int.get(t) or 0),
        "last_poked": float(last_poke.get(t) or 0),
        "ignored": bool(ignored.get(t)),
    } for t in targets]


def poke_once(ctx, now=None, dry_run=False):
    """跑一次评估。返回 {ok, target, reason}。心跳与手动都走这里。"""
    now = float(now if now is not None else time.time())
    cfg = ctx.get_config() or {}
    targets = _targets(cfg)
    if not targets:
        return {"ok": False, "skipped": True, "reason": "没有配置目标（poke_proactive_targets）"}
    if not cfg.get("poke_proactive_enable", False):
        return {"ok": False, "skipped": True, "reason": "poke_proactive_enable 关闭"}

    with _lock:
        st = _state(ctx)
        picked = P.pick_target(now, _facts(ctx, targets, st), st, cfg)
        if not picked.get("target"):
            return {"ok": False, "skipped": True, "reason": picked.get("reason") or "没有可戳的对象"}
        target = picked["target"]
        if dry_run:
            return {"ok": True, "target": target, "reason": picked["reason"], "dry_run": True}

        # 先占位再发：发送有网络延迟，等回来再记就会在同一轮里被选第二次
        today = P.day_key(now)
        daily = dict(st.get("daily") or {})
        daily[today] = int(daily.get(today) or 0) + 1
        st["daily"] = daily
        st.setdefault("last_poked", {})[target] = now
        _save(ctx, st)

    try:
        r = ctx.api_call("send_poke", {"user_id": target}) or {}
    except Exception as e:
        ctx.log(f"[主动戳] 发送异常: {e}", "warn")
        return {"ok": False, "target": target, "reason": f"发送异常: {e}"}

    ok = bool(r.get("status") == "ok" or r.get("retcode") == 0 or r.get("data"))
    if not ok:
        # 失败不回滚额度：回滚会让下一轮立刻再试，变成连续戳
        ctx.log(f"[主动戳] 失败（不重试）: {str(r)[:120]}", "warn")
        return {"ok": False, "target": target, "reason": "发送失败"}
    ctx.log(f"[主动戳] {target} · {picked['reason']}")
    return {"ok": True, "target": target, "reason": picked["reason"]}


def _heartbeat_loop(ctx):
    def _wait(sec):
        _stop.wait(max(30, int(sec or 900)))
    _wait(120)                       # 启动别抢戏
    while not _stop.is_set():
        try:
            r = poke_once(ctx)
            if r.get("ok") and not r.get("skipped"):
                ctx.log(f"[主动戳] 已戳 {r.get('target')}")
            elif r.get("skipped") and "关闭" not in str(r.get("reason")):
                ctx.log(f"[主动戳/跳过] {r.get('reason')}")
        except Exception as e:
            ctx.log(f"[主动戳] 心跳异常: {e}", "warn")
        cfg = ctx.get_config() or {}
        _wait(cfg.get("poke_proactive_heartbeat_sec") or 900)


def _start_heartbeat(ctx):
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_heartbeat_loop, args=(ctx,),
                               daemon=True, name="proactive-poke")
    _thread.start()


def _stop_heartbeat():
    global _thread
    _stop.set()
    _thread = None


# ── 钩子 ────────────────────────────────────────────────────
def on_load(ctx):
    cfg = ctx.get_config() or {}
    if cfg.get("poke_proactive_enable", False):
        _start_heartbeat(ctx)
    ctx.log(f"[主动戳] 已加载（默认关闭；开启要 poke_proactive_enable=true 且配好目标）")


def on_enable(ctx):
    _start_heartbeat(ctx)


def on_disable(ctx):
    _stop_heartbeat()


def on_unload(ctx):
    _stop_heartbeat()


def on_event(ctx, event_name, payload):
    """只做两件事：记互动、记被戳。判定在心跳里。"""
    try:
        now = time.time()
        p = dict(payload or {})
        with _lock:
            st = _state(ctx)
            if event_name == "message.in":
                uid = str(p.get("user_id") or p.get("sender") or "")
                if uid:
                    st.setdefault("last_interact", {})[uid] = now
                    # 对方说话了 = 上次的戳没被无视
                    if (st.get("ignored") or {}).pop(uid, None) is not None:
                        pass
                    _save(ctx, st)
            elif event_name == "notice.poke":
                # 被戳：如果对方在 24h 内被我们戳过却没说过话 → 原来那次被无视了
                op = str(p.get("operator") or "")
                lp = float((st.get("last_poked") or {}).get(op) or 0)
                if op and lp and (now - lp) < P.RETRY_WINDOW:
                    st.setdefault("ignored", {})[op] = True
                if op:
                    st.setdefault("last_interact", {})[op] = now
                _save(ctx, st)
    except Exception:
        pass


PLUGIN_HOOKS = {
    "on_load": on_load,
    "on_enable": on_enable,
    "on_disable": on_disable,
    "on_unload": on_unload,
    "on_event": on_event,
}


def get_api_status():
    return {"ok": True, "status": "ready",
            "detail": "主动戳：默认关闭；开启需 poke_proactive_enable=true"}
