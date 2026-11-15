# -*- coding: utf-8 -*-
"""
这样脆弱的东西 —— 人格蒸馏插件

从聊天记录、对话记忆、落沙数据中提取人格模型，
按数据量分级蒸馏（L0-L4），生成通用人格文件（Persona-File v1.0），
蒸馏结果经人工审核后保存，全程审计可追踪。

真插件 ABI v1：PLUGIN_SPEC=1 + PLUGIN_MANIFEST + PLUGIN_HOOKS
"""
from . import api_server
from .config import get_config, set_config

PLUGIN_SPEC = 1
PLUGIN_MANIFEST = {
    "name": "这样脆弱的东西",          # 显示名 = 目录名 = plugin_id（全对齐）
    "version": "2.0.0",
    "api_version": 1,
    "author": "EggBloom",
    "description": "人格蒸馏：从多数据源提取人格模型，按数据量分级（L0-L4），人工审核后保存，伦理合规",
    "dependencies": [],
    "priority": 30,
    "default_enabled": False,
    "page": "plugins/这样脆弱的东西/ui.html",
}

# 通用插件通道动作表（WebUI /api/plugins/action → _invoke_generic_plugin）
PLUGIN_ACTIONS = api_server.ACTIONS


def execute_action(action, params=None):
    """execute_action 兜底：未在 ACTIONS 中的动作返回错误。"""
    return {"error": f"未知动作: {action}"}


def on_load(ctx):
    ctx.log("这样脆弱的东西（人格蒸馏）已加载")


def on_enable(ctx):
    ctx.log("这样脆弱的东西 已启用")


def on_disable(ctx):
    ctx.log("这样脆弱的东西 已禁用")


def on_unload(ctx):
    ctx.log("这样脆弱的东西 已卸载")


PLUGIN_HOOKS = {
    "on_load": on_load,
    "on_enable": on_enable,
    "on_disable": on_disable,
    "on_unload": on_unload,
}


def get_api_status():
    """供插件管理器 API 状态检测。"""
    return api_server.get_api_status()


def set_ai_client(client):
    """插件管理器传播 AI 客户端（bot_core 启动时经 pm.set_ai_client 注入）。"""
    api_server.set_ai_client(client)


__all__ = [
    "PLUGIN_SPEC",
    "PLUGIN_MANIFEST",
    "PLUGIN_ACTIONS",
    "PLUGIN_HOOKS",
    "execute_action",
    "get_config",
    "set_config",
    "get_api_status",
    "set_ai_client",
]
