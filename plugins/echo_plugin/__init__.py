# -*- coding: utf-8 -*-
"""示例：真正的第三方插件（ABI v1）。

遵守 ABI 三原则：
1. 不 import 主程序 / __main__ / bot_core，不直读 bot_config.json；
2. 只通过 ctx（PluginContext）访问 配置/日志/状态/AI/HTTP；
3. 声明式 PLUGIN_MANIFEST + 显式 PLUGIN_HOOKS，由管理器做版本校验。

默认关闭（default_enabled=False），在 WebUI 插件页启用后生效：
  群/私聊里发送 `!echo <内容>`，插件直接回复，不走 AI。
"""
from plugins.abi import PluginContext

PLUGIN_SPEC = 1
PLUGIN_MANIFEST = {
    "name": "echo_plugin",
    "version": "1.0.0",
    "api_version": 1,
    "author": "EggBloom",
    "description": "示例插件：回复 !echo <内容>（演示真插件 ABI）",
    "dependencies": [],
    "priority": 100,
    "default_enabled": False,
}


def on_load(ctx: PluginContext):
    ctx.log("echo plugin loaded (ABI v1)")


def on_enable(ctx: PluginContext):
    ctx.log("echo plugin enabled")


def on_disable(ctx: PluginContext):
    ctx.log("echo plugin disabled")


def on_unload(ctx: PluginContext):
    ctx.log("echo plugin unloaded")


def on_message(ctx: PluginContext, event: dict):
    """event: {"type": "group"|"private", "group_id", "sender", "text", "message_id", "time"}"""
    text = (event.get("text") or "").strip()
    if text.startswith("!echo"):
        return "echo: " + text[5:].strip()
    return None  # 不干预，走正常流程


PLUGIN_HOOKS = {
    "on_load": on_load,
    "on_enable": on_enable,
    "on_disable": on_disable,
    "on_unload": on_unload,
    "on_message": on_message,
}
