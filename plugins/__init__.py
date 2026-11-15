# -*- coding: utf-8 -*-
"""
EggBloom 插件系统 - 自发现/自管理/自开关

提供统一的插件加载器、生命周期管理、钩子派发机制。
"""

from .plugin_manager import (
    PluginManager,
    get_plugin_manager,
    init_plugin_manager,
)