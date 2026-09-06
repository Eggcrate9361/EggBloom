# -*- coding: utf-8 -*-
"""
mood_engine 适配层：转发到 EGO 子模块 plugins.ego.mood_engine。
前端以 plugin_id='mood_engine' 请求，保证独立完整面板可用。
"""
from plugins.ego.mood_engine import (
    PLUGIN_META,
    get_config as _get_config,
    set_config as _set_config,
    get_moods as _get_moods,
    reset_mood as _reset_mood,
)

# 拷贝 ego 的 meta（避免原地改写共享 dict），标记为"适配层壳"：不在插件列表单独显示
PLUGIN_META = dict(PLUGIN_META)
PLUGIN_META["adapter"] = True
PLUGIN_META["page"] = "plugins/mood_engine/ui.html"


def _do_reset(params=None):
    params = params or {}
    scope_id = str(params.get("scope_id") or "").strip()
    return _reset_mood(scope_id or None)


PLUGIN_ACTIONS = {
    "get_config": lambda params: _get_config(),
    "set_config": lambda params: _set_config(params or {}),
    "get_moods": lambda params: _get_moods(),
    "reset": _do_reset,
}
