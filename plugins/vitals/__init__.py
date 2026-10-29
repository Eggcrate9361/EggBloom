# -*- coding: utf-8 -*-
"""vitals —— 身体与心理状态插件（1.5.10 G；设计见 1.5.9 §16）。

为什么是插件（G1）：主链已经 1.5 万行，这是"状态机 + 病表 + 表达映射"，
塞进主链就是第二份人格文件级的债；而且它必须能**一键关掉**（关掉 = 不订阅、
不拉天气、不注入、不影响任何其他插件）。

本版范围（G2）：
  · 8 个 0–1 状态（设计正文写"七个"，以 JSON 的 8 个键为准）落盘 data/vitals.json；
  · 事件驱动（message.in / bot.replied + 戳），确定性规则，不经 LLM；
  · 天气只负责感冒（wttr.in，bot_city 未配就不拉）；
  · 表达只走"更短/更少"方向（form_params），以及每天最多一次、≤80 字的"发作"；
  · 三条硬边界：不哭闹（受伤=话少）、不攻击（辱骂/自伤威胁→短路不回）、可重置（求助信号清零）。
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request

from plugins.vitals import core

PLUGIN_SPEC = 1
PLUGIN_MANIFEST = {
    "name": "vitals",
    "version": "1.0.0",
    "api_version": 2,
    "author": "EggBloom",
    "description": "身体与心理状态：互动有后果（感冒/失眠/孤独/受伤/倦怠），只改形式不动人格",
    "dependencies": [],
    "priority": 40,
    # 默认关：这套东西会被玩坏，不配就别先"生病"（用户可在插件页一键开）。
    "default_enabled": False,
}

PLUGIN_HOOKS = {}  # 在文件底部填充

_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(_DIR, "data")
_STATE_PATH = os.path.join(_DATA, "vitals.json")
_DISEASE_PATH = os.path.join(_DATA, "vitals_diseases.json")

_LOCK = threading.RLock()
_STATE = None
_DISEASES = []
_TIMERS = {}
_LAST_GROUP = ""
_LAST_REPLY_AT = {}      # 群 → 最近一次 bot 发言时间
_LAST_MSG_AT = {}        # 群 → 最近一次有人说话时间


# ── 状态读写 ─────────────────────────────────────────────────────

def load_state() -> dict:
    global _STATE
    with _LOCK:
        if _STATE is not None:
            return dict(_STATE)
        try:
            raw = json.load(open(_STATE_PATH, encoding="utf-8"))
        except Exception:
            raw = {}
        st = core.normalize(raw)
        # 隔了很久才启动：先按天衰减，别让"上次关机"变成"病一直没好"
        try:
            hours = max(0.0, (time.time() - float(raw.get("updated") or time.time())) / 3600.0)
        except Exception:
            hours = 0.0
        if hours >= 1:
            st = core.decay(st, hours)
        _STATE = st
        return dict(st)


def save_state(st: dict) -> None:
    global _STATE
    with _LOCK:
        _STATE = core.normalize(st)
        try:
            os.makedirs(_DATA, exist_ok=True)
            tmp = _STATE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(_STATE, f, ensure_ascii=False, indent=1)
            os.replace(tmp, _STATE_PATH)
        except Exception as e:
            print(f"[vitals] 落盘失败: {e}")


def _cfg(key: str, default=None):
    try:
        from plugins.abi import PluginContext  # noqa: F401
        return _CTX.get_config(key, default) if _CTX else default
    except Exception:
        return default


def enabled() -> bool:
    return bool(_cfg("vitals_enable", PLUGIN_MANIFEST.get("default_enabled", False)))


# ── 疾病表 ───────────────────────────────────────────────────────

def load_diseases():
    global _DISEASES
    try:
        _DISEASES = core.load_diseases(_DISEASE_PATH)
        print(f"[vitals] 病表已加载 {len(_DISEASES)} 条")
    except Exception as e:
        _DISEASES = []
        print(f"[vitals] 病表加载失败（不静默跳过）: {e}")
    return list(_DISEASES)


# ── 事件 ─────────────────────────────────────────────────────────

def _on_event(ctx, event_name: str = "", payload: dict = None, **kwargs):
    """插件总线回调（PluginManager.emit_event → on_event）。"""
    if not enabled():
        return None
    p = payload or {}
    gid = str(p.get("group_id") or "")
    if gid:
        _LAST_GROUP = gid
    st = load_state()

    if event_name == "message.in":
        if gid:
            _LAST_MSG_AT[gid] = time.time()
            _cancel_timer(gid)          # 有人说话 = 不是"无人回应"
        text = str(p.get("text") or "")
        hour = time.localtime(float(p.get("ts") or time.time())).tm_hour
        st = core.apply_event(st, "message.in",
                              {"text": text, "at_bot": bool(p.get("at_bot")), "hour": hour})
    elif event_name == "bot.replied":
        if gid:
            _LAST_REPLY_AT[gid] = time.time()
            _schedule_timer(gid)
        st = load_state()
    elif event_name == "vitals.poke":
        st = core.apply_event(st, "poke", {})
    else:
        return None

    save_state(st)
    return None


def _schedule_timer(gid: str) -> None:
    """bot 发言后 10 分钟无人回应 → loneliness（§16.3）。"""
    _cancel_timer(gid)

    def _fire(g=gid):
        try:
            if _LAST_MSG_AT.get(g, 0) > _LAST_REPLY_AT.get(g, 0):
                return              # 期间有人说话，不算冷落
            st = load_state()
            st = core.apply_event(st, "no_reply_10min", {})
            save_state(st)
            print(f"[vitals] 群[{g}] 发言后 10 分钟无人回应 → loneliness 上升")
        except Exception as e:
            print(f"[vitals] 孤独计时失败: {e}")

    t = threading.Timer(600.0, _fire)
    t.daemon = True
    _TIMERS[gid] = t
    t.start()


def _cancel_timer(gid: str) -> None:
    t = _TIMERS.pop(gid, None)
    if t:
        try:
            t.cancel()
        except Exception:
            pass


# ── before_reply：边界 + 一行状态 ────────────────────────────────

def _before_reply(ctx, message: str = "", group_id: str = "", context: dict = None, **kwargs):
    if not enabled():
        return None
    text = str(message or "")
    # ① 可重置：真实求助信号 → 全部清零，不玩梗、不延展（§16.6）
    if bool(_cfg("vitals_reset_on_help", True)) and core.HELP_RE.search(text):
        save_state(core.reset_all(load_state()))
        print("[vitals] 命中求助信号 → 状态清零")
        return None
    # ② 不攻击：辱骂/自伤威胁 → 短路成"不回"（代码边界，不靠 prompt）
    if bool(_cfg("vitals_guard_enabled", True)) and core.ABUSE_RE.search(text):
        st = load_state()
        st = core.apply_event(st, "message.in", {"text": text})
        save_state(st)
        return {"action": "skip", "reason": "vitals: 辱骂/威胁不进入表达"}
    # ③ 有症状才注入一行（无症状 = 零注入）
    line = core.symptom_line(load_state())
    if line:
        return {"action": "inject", "content": line}
    return None


# ── 天气（只负责感冒；bot_city 未配不启用）────────────────────────

def _weather_cold_once() -> float:
    city = str(_cfg("bot_city", "") or "").strip()
    if not city or not bool(_cfg("vitals_weather_enable", True)):
        return 0.0
    try:
        url = f"https://wttr.in/{urllib.parse.quote(city)}?format=j1"
        with urllib.request.urlopen(url, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        cur = (data.get("current_condition") or [{}])[0]
        feels = float(cur.get("FeelsLikeC") or 0)
        w = (data.get("weather") or [{}])[0]
        hi = float(w.get("maxtempC") or feels)
        lo = float(w.get("mintempC") or feels)
        humid = float(cur.get("humidity") or 50)
        precip = bool(int(cur.get("chanceofrain") or 0) >= 60)
        risk = core.cold_risk(feels, hi - lo, humid, precip, time.localtime().tm_mon)
        if risk > 0:
            st = load_state()
            st["cold"] = core.clamp(st["cold"] + 0.3 * risk)
            save_state(st)
        print(f"[vitals] 天气 {city}: 体感{feels:.0f}° 温差{hi-lo:.0f}° 湿度{humid:.0f}% → cold_risk={risk}")
        return risk
    except Exception as e:
        print(f"[vitals] 天气拉取失败（跳过）: {e}")
        return 0.0


def _start_weather_timer(ctx) -> None:
    def _daily():
        while True:
            _weather_cold_once()
            time.sleep(6 * 3600)

    t = threading.Thread(target=_daily, daemon=True)
    t.start()


# ── 生命周期 ─────────────────────────────────────────────────────

_CTX = None


def _on_load(ctx):
    global _CTX
    _CTX = ctx
    load_state()
    load_diseases()
    if enabled():
        _start_weather_timer(ctx)
    else:
        print("[vitals] 未启用（vitals_enable / 插件开关关闭）—— 不拉天气、不订阅事件")


def _on_unload(ctx=None):
    for gid in list(_TIMERS):
        _cancel_timer(gid)


PLUGIN_HOOKS = {
    "on_load": _on_load,
    "on_unload": _on_unload,
    "on_event": _on_event,
    "before_reply": _before_reply,
}


# ── 对外只读接口（给未来的预算层/配置页用）──────────────────────

def get_vitals() -> dict:
    return dict(load_state())


def get_form_params() -> dict:
    return core.form_params(load_state())


def get_diseases() -> list:
    return list(_DISEASES)
