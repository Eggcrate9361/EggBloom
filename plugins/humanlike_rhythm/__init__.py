# -*- coding: utf-8 -*-
"""
humanlike_rhythm 适配层：转发到 EGO 子模块 plugins.ego.humanlike_rhythm。
前端以 plugin_id='humanlike_rhythm' 请求，保证独立完整面板可用。
"""
from plugins.ego.humanlike_rhythm import (
    PLUGIN_META,
    get_config as _get_config,
    set_config as _set_config,
    get_stats as _get_stats,
)

# 拷贝 ego 的 meta（避免原地改写共享 dict），标记为"适配层壳"：不在插件列表单独显示
PLUGIN_META = dict(PLUGIN_META)
PLUGIN_META["adapter"] = True
PLUGIN_META["page"] = "plugins/humanlike_rhythm/ui.html"


PLUGIN_ACTIONS = {
    "get_config": lambda params: _get_config(),
    "set_config": lambda params: _set_config(params or {}),
    "get_stats": lambda params: _get_stats(),
}
