#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ui_themes.py — 主题方案

3 组撞色主方案:
- crimson: #E61A23 + #0A090C
- navy_teal: #002CB0 + #66D4C8
- sunset_paper: #E7E1D3 + #12233A + #E85327
"""
from __future__ import annotations

from typing import Dict

THEMES: Dict[str, Dict[str, str]] = {
    "crimson": {
        "bg_root": "#0A090C",
        "bg_sidebar": "#110808",
        "bg_card": "#180B0B",
        "bg_elevated": "#1F1010",
        "bg_overlay": "#261414",
        "bg_input": "#221111",
        "bg_log": "#0F0808",
        "text_primary": "#F2E1E1",
        "text_secondary": "#C49A9A",
        "text_tertiary": "#8A6E6E",
        "text_muted": "#6E4F4F",
        "border_light": "#3D2525",
        "border_medium": "#331B1B",
        "border_dark": "#180B0B",
        "divider": "#3D2525",
        "accent": "#E61A23",
        "accent_primary": "#E61A23",
        "accent_hover": "#F03A42",
        "accent_pressed": "#C8151D",
        "accent_secondary": "#B86E6E",
        "accent_warning": "#C4A45A",
        "accent_danger": "#E61A23",
        "accent_danger_hover": "#F03A42",
        "accent_info": "#C49A9A",
        "accent_info_hover": "#D4AAAA",
        "accent_success": "#8FAE6A",
        "hover_bg": "#261414",
        "active_bg": "#2B1818",
        "selected_bg": "#3A1F1F",
        "focus_ring": "#E61A2360",
        "accent_soft": "#3A1C1C",
        "chip_bg": "#261414",
        "chip_fg": "#F2E1E1",
        "shadow": "#00000030",
        "bg_darker": "#0A090C",
        "bg_hover": "#261414",
        "border_default": "#3D2525",
    },
    "navy_teal": {
        "bg_root": "#00112B",
        "bg_sidebar": "#001630",
        "bg_card": "#00224A",
        "bg_elevated": "#002C66",
        "bg_overlay": "#003680",
        "bg_input": "#00244C",
        "bg_log": "#00102A",
        "text_primary": "#E8F4F8",
        "text_secondary": "#9CC8D4",
        "text_tertiary": "#5A94A8",
        "text_muted": "#2E6A7A",
        "border_light": "#163050",
        "border_medium": "#0F2844",
        "border_dark": "#081420",
        "divider": "#163050",
        "accent": "#66D4C8",
        "accent_primary": "#66D4C8",
        "accent_hover": "#7FE0D4",
        "accent_pressed": "#4CB8AC",
        "accent_secondary": "#002CB0",
        "accent_warning": "#C4A45A",
        "accent_danger": "#E61A23",
        "accent_danger_hover": "#F03A42",
        "accent_info": "#9CC8D4",
        "accent_info_hover": "#B0D8E4",
        "accent_success": "#66D4C8",
        "hover_bg": "#122238",
        "active_bg": "#162844",
        "selected_bg": "#14303C",
        "focus_ring": "#66D4C860",
        "accent_soft": "#122830",
        "chip_bg": "#122238",
        "chip_fg": "#E8F4F8",
        "shadow": "#00000030",
        "bg_darker": "#00112B",
        "bg_hover": "#122238",
        "border_default": "#163050",
    },
    "sunset_paper": {
        "bg_root": "#E7E1D3",
        "bg_sidebar": "#DDD6C6",
        "bg_card": "#FFFFFF",
        "bg_elevated": "#FFFFFF",
        "bg_overlay": "#D4CFC2",
        "bg_input": "#E8E2D6",
        "bg_log": "#F2EEE4",
        "text_primary": "#12233A",
        "text_secondary": "#3E5468",
        "text_tertiary": "#5A6E84",
        "text_muted": "#7A8A9A",
        "border_light": "#C8C0B0",
        "border_medium": "#D8D0C2",
        "border_dark": "#DDD6C6",
        "divider": "#C8C0B0",
        "accent": "#E85327",
        "accent_primary": "#E85327",
        "accent_hover": "#F06A44",
        "accent_pressed": "#C2431E",
        "accent_secondary": "#002CB0",
        "accent_warning": "#A07820",
        "accent_danger": "#B83A24",
        "accent_danger_hover": "#98301C",
        "accent_info": "#4A6278",
        "accent_info_hover": "#3A5268",
        "accent_success": "#3E7848",
        "hover_bg": "#ECE5D9",
        "active_bg": "#E2DACE",
        "selected_bg": "#F2E0D0",
        "focus_ring": "#E8532760",
        "accent_soft": "#F2E0D0",
        "chip_bg": "#ECE5D9",
        "chip_fg": "#12233A",
        "shadow": "#00000014",
        "bg_darker": "#E7E1D3",
        "bg_hover": "#ECE5D9",
        "border_default": "#C8C0B0",
    },
}


def get_theme(name: str = "crimson") -> Dict[str, str]:
    return THEMES.get(name, THEMES["crimson"])


def apply_theme_to_c():
    try:
        from ctk_theme import SOFT_COLORS

        SOFT_COLORS.clear()
        SOFT_COLORS.update(get_theme("crimson"))
    except Exception:
        pass


TRANSLATIONS = {
    "zh": {
        "welcome": "欢迎使用 EggBloom",
        "subtitle": "QQ 机器人管理面板 · 实时监控",
        "bot_status": "Bot 状态",
        "today_msgs": "今日消息",
        "uptime": "运行时长",
        "memory": "内存占用",
        "quick_actions": "快捷操作",
        "recent_logs": "最近日志",
        "settings": "设置",
        "logs": "日志",
        "console": "控制台",
        "plugins": "插件",
        "webui": "WebUI",
        "help": "帮助",
        "theme": "主题",
        "language": "语言",
    },
    "en": {
        "welcome": "Welcome to EggBloom",
        "subtitle": "QQ Bot Manager · Live Dashboard",
        "bot_status": "Bot Status",
        "today_msgs": "Messages",
        "uptime": "Uptime",
        "memory": "Memory",
        "quick_actions": "Quick Actions",
        "recent_logs": "Recent Logs",
        "settings": "Settings",
        "logs": "Logs",
        "console": "Console",
        "plugins": "Plugins",
        "webui": "WebUI",
        "help": "Help",
        "theme": "Theme",
        "language": "Language",
    },
}


def t(key: str, lang: str = "zh") -> str:
    pack = TRANSLATIONS.get(lang, TRANSLATIONS["zh"])
    return pack.get(key, key)
