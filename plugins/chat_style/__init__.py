# -*- coding: utf-8 -*-
"""
chat_style 适配层：转发到 EGO 子模块 plugins.ego.chat_style。
前端仍以 plugin_id='chat_style' 请求，保证独立页面可用。
"""
from plugins.ego.chat_style import (
    PLUGIN_META,
    ChatStyleLearner,
    get_config as _get_config,
    set_config as _set_config,
    learn_from_user,
    learn_from_memory,
    auto_learn,
    auto_learn_all,
    before_reply,
    on_load,
    on_unload,
    _learner,
)

PLUGIN_META["page"] = "plugins/chat_style/ui.html"


def _do_toggle(params=None):
    cfg = _get_config()
    enabled = not bool(cfg.get("enabled", True))
    _set_config({"enabled": enabled})
    return {"enabled": enabled, "config": _get_config()}


def _do_config(params=None):
    params = params or {}
    key = (params.get("key") or "").strip()
    if key:
        return _set_config({key: params.get("value")})
    return _get_config()


def _do_custom(params=None):
    params = params or {}
    method = params.get("method") or ""
    scope_id = str(params.get("scope_id") or "")
    user_id = str(params.get("user_id") or "")
    if method == "learn_from_user":
        messages = params.get("messages") or []
        if not isinstance(messages, list):
            messages = [str(messages)]
        learn_from_user(scope_id, user_id, messages)
        return {"ok": True, "count": len(messages)}
    if method == "learn_from_memory":
        try:
            count = learn_from_memory(scope_id, user_id)
            return {"ok": True, "count": int(count or 0)}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": False, "error": "unknown method: " + method}


def _do_features(params=None):
    params = params or {}
    scope_id = (params.get("scope_id") or "").strip() or "default"
    user_id = (params.get("user_id") or "").strip()
    try:
        feats = _learner.get_features(scope_id=scope_id, user_id=user_id)
        return {"features": feats.get("features", []) if isinstance(feats, dict) else feats}
    except Exception as e:
        return {"features": [], "error": str(e)}


PLUGIN_ACTIONS = {
    "toggle": _do_toggle,
    "config": _do_config,
    "custom": _do_custom,
    "get_features": _do_features,
    "get_config": lambda params: _get_config(),
    "set_config": lambda params: _set_config(params or {}),
}
