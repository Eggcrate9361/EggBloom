# -*- coding: utf-8 -*-
"""schedule —— 周日程表插件（1.5.10；用户点名「上课/上班要有依据」）。

为什么是插件：日程是**可配置的事实**，不是人格；它必须能一键关、
换一份表不用动主链。与 vitals 同属"数字生命呈现"组。

行为：
  · 配置 plugins/schedule/data/config.json（周表 + 例外日期 + 自由时状态）；
  · before_reply 注入**一行**当前日程事实（entries 为空时零注入）；
  · 只给事实，不改语气、不改长度、不编造（模型照表说）。
"""
from __future__ import annotations

import json
import os

from plugins.schedule import core

PLUGIN_SPEC = 1
PLUGIN_MANIFEST = {
    "name": "schedule",
    "version": "1.0.0",
    "api_version": 2,
    "author": "EggBloom",
    "description": "周日程表：上课/上班/实验等真实安排进 prompt，让'刚下课'有依据",
    "dependencies": [],
    "priority": 42,
    "default_enabled": True,   # 默认开但**空表零注入**，配了才生效
}

_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(_DIR, "data")
_CONFIG_PATH = os.path.join(_DATA, "config.json")

DEFAULT_CONFIG = {
    "timezone": "",                # 空 = 服务器本地时区
    "state_when_free": "在家",
    "entries": [],                 # 周表，见 core.py 顶部格式
    "exceptions": [],              # 例外日期（放假/调休）YYYY-MM-DD
}

_CTX = None
_CACHE = {"key": None, "entries": [], "exceptions": set(), "free": "在家"}


def _cfg(key, default=None):
    try:
        return _CTX.get_config(key, default) if _CTX else default
    except Exception:
        return default


def load_config() -> dict:
    try:
        cfg = json.load(open(_CONFIG_PATH, encoding="utf-8"))
        if isinstance(cfg, dict):
            merged = dict(DEFAULT_CONFIG)
            merged.update(cfg)
            return merged
    except Exception:
        pass
    return dict(DEFAULT_CONFIG)


def _parsed():
    """解析并缓存（按文件 mtime）。坏配置 → 不注入（并在日志里报错，不静默）。"""
    try:
        mtime = os.path.getmtime(_CONFIG_PATH)
    except OSError:
        mtime = 0
    if _CACHE["key"] == mtime:
        return _CACHE
    cfg = load_config()
    try:
        entries, exc, free = core.parse_config(cfg)
    except Exception as e:
        print(f"[schedule] 配置无效（本次不注入）: {e}")
        entries, exc, free = [], set(), "在家"
    _CACHE.update({"key": mtime, "entries": entries, "exceptions": exc, "free": free,
                   "tz": cfg.get("timezone") or ""})
    return _CACHE


def now_state(ts=None) -> dict:
    p = _parsed()
    return core.current(p["entries"], p["exceptions"], ts=ts, tz=p.get("tz") or None)


def _before_reply(ctx, message: str = "", group_id: str = "", context: dict = None, **kwargs):
    global _CTX
    _CTX = ctx
    if not bool(_cfg("schedule_enable", True)):
        return None
    p = _parsed()
    if not p["entries"]:
        return None                      # 空表 = 零注入（不编"没安排"占 token）
    state = core.current(p["entries"], p["exceptions"], tz=p.get("tz") or None)
    return {"action": "inject", "content": core.prompt_line(state, p["free"])}


def _on_load(ctx):
    global _CTX
    _CTX = ctx
    p = _parsed()
    print(f"[schedule] 已加载：{len(p['entries'])} 条日程"
          + (f"，例外 {len(p['exceptions'])} 天" if p["exceptions"] else ""))


PLUGIN_HOOKS = {"on_load": _on_load, "before_reply": _before_reply}


# ── 只读接口（配置页/API 用）────────────────────────────────────

def get_today() -> dict:
    st = now_state()
    return {"state": st}


def get_config() -> dict:
    return load_config()


def save_config(params: dict) -> dict:
    cfg = load_config()
    if isinstance(params, dict):
        cfg.update(params)
    os.makedirs(_DATA, exist_ok=True)
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    _CACHE["key"] = None
    return cfg


#: 插件页/动作分发用的别名（与 get_config 对称）
def set_config(params: dict) -> dict:
    return save_config(params)
