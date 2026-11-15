# -*- coding: utf-8 -*-
"""
personality_engine 适配层：转发到 EGO 子模块 plugins.ego.personality_engine。
前端以 plugin_id='personality_engine' 请求，保证独立完整面板可用。
"""
from plugins.ego.personality_engine import (
    PLUGIN_META,
    get_config as _get_config,
    set_config as _set_config,
    get_personality as _get_personality,
    save_personality as _save_personality,
    get_examples as _get_examples,
    save_examples as _save_examples,
    get_state as _get_state,
)

# 拷贝 ego 的 meta（避免原地改写共享 dict），标记为"适配层壳"：不在插件列表单独显示
PLUGIN_META = dict(PLUGIN_META)
PLUGIN_META["adapter"] = True
PLUGIN_META["page"] = "plugins/personality_engine/ui.html"


def _do_save_personality(params=None):
    params = params or {}
    sections = params.get("sections") or params.get("personality") or {}
    return _save_personality(sections)


def _do_save_examples(params=None):
    params = params or {}
    return _save_examples(params.get("examples") or [])


PLUGIN_ACTIONS = {
    "get_config": lambda params: _get_config(),
    "set_config": lambda params: _set_config(params or {}),
    "get_personality": lambda params: _get_personality(),
    "save_personality": _do_save_personality,
    "get_examples": lambda params: _get_examples(params.get("scene") or ""),
    "save_examples": _do_save_examples,
    "get_state": lambda params: _get_state(),
}
