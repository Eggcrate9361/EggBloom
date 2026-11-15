#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
EggBloom v3 CustomTkinter 主题
柔色色板 + CTkThemeMapper + 动效配置
"""

import customtkinter as ctk
from design_system import C, S, R


# ============================================================================
# 柔色色板
# ============================================================================

SOFT_COLORS = {
    # 背景
    "bg_root": "#181B1E",
    "bg_sidebar": "#1D2124",
    "bg_card": "#23262B",
    "bg_elevated": "#282C32",
    "bg_overlay": "#2D3138",
    "bg_input": "#262A30",
    "bg_log": "#14171A",
    # 文字
    "text_primary": "#E6E6E6",
    "text_secondary": "#A0B4C8",
    "text_tertiary": "#6B7A8A",
    "text_muted": "#4D5A68",
    # 边框
    "border_light": "#3A3F45",
    "border_medium": "#2E3238",
    "border_dark": "#1D2124",
    # 主强调色：暖金（用于 primary button、link、progress）
    "accent_primary": "#C9A96E",
    "accent": "#C9A96E",
    "accent_hover": "#D4B87E",
    "accent_pressed": "#B8985E",
    "accent_soft": "#3E3628",
    # 信息/辅助强调：slate blue
    "accent_info": "#A0B4C8",
    "accent_info_hover": "#B0C4D4",
    # 危险/暗红：仅用于 danger button、error
    "accent_danger": "#8b3a3a",
    "accent_danger_hover": "#A04848",
    # 成功
    "accent_success": "#5E9E6E",
    # 警告
    "accent_warning": "#C4A45A",
    # 状态色
    "hover_bg": "#272B30",
    "active_bg": "#2E3238",
    "selected_bg": "#3A2A1F",
    "focus_ring": "#C9A96E60",
    "chip_bg": "#272B30",
    "chip_fg": "#E6E6E6",
    "shadow": "#00000030",
    "divider": "#3A3F45",
}


# ============================================================================
# 兼容别名
# ============================================================================

SOFT_BG = {k: v for k, v in SOFT_COLORS.items() if k.startswith("bg_")}
SOFT_TEXT = {k: v for k, v in SOFT_COLORS.items() if k.startswith("text_")}
SOFT_BORDER = {k: v for k, v in SOFT_COLORS.items() if k.startswith("border_")}
SOFT_ACCENTS = {k: v for k, v in SOFT_COLORS.items()
                if k.startswith("accent") or k in ("success", "warning", "danger", "info")}
SOFT_RADIUS = {k: v for k, v in R.LEVELS.items()}
SOFT_SPACING = {k: v for k, v in S.LEVELS.items()}

# 快捷引用
C_SOFT = SOFT_COLORS
S_SOFT = S
R_SOFT = R


# ============================================================================
# CTk 主题初始化
# ============================================================================

def apply_ctk_theme():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    # 不要强制 widget_scaling=1.0，让 CTk 自动适配系统 DPI


# ============================================================================
# CTkThemeMapper - 统一颜色映射
# ============================================================================

class CTkThemeMapper:
    """将柔色色板映射为 CTk 控件参数"""

    def container(self, kind: str = "root") -> tuple:
        m = {
            "root": SOFT_COLORS["bg_root"],
            "sidebar": SOFT_COLORS["bg_sidebar"],
            "card": SOFT_COLORS["bg_card"],
            "elevated": SOFT_COLORS["bg_elevated"],
            "overlay": SOFT_COLORS["bg_overlay"],
            "input": SOFT_COLORS["bg_input"],
            "log": SOFT_COLORS["bg_log"],
        }
        c = m.get(kind, SOFT_COLORS["bg_card"])
        return (c, c)

    def text(self, kind: str = "primary") -> tuple:
        m = {
            "primary": SOFT_COLORS["text_primary"],
            "secondary": SOFT_COLORS["text_secondary"],
            "tertiary": SOFT_COLORS["text_tertiary"],
            "muted": SOFT_COLORS["text_muted"],
        }
        c = m.get(kind, SOFT_COLORS["text_primary"])
        return (c, c)

    def border(self, kind: str = "light") -> str:
        m = {
            "light": SOFT_COLORS["border_light"],
            "medium": SOFT_COLORS["border_medium"],
            "dark": SOFT_COLORS["border_dark"],
        }
        return m.get(kind, SOFT_COLORS["border_light"])

    def button(self, kind: str = "primary"):
        m = {
            "primary": SOFT_COLORS["accent"],
            "primary_hover": SOFT_COLORS["accent_hover"],
            "primary_pressed": SOFT_COLORS["accent_pressed"],
            "success": SOFT_COLORS["success"],
            "success_hover": _lerp_hex(SOFT_COLORS["success"], "#ffffff", 0.12),
            "danger": SOFT_COLORS["danger"],
            "danger_hover": _lerp_hex(SOFT_COLORS["danger"], "#ffffff", 0.12),
            "info": SOFT_COLORS["info"],
            "info_hover": _lerp_hex(SOFT_COLORS["info"], "#ffffff", 0.12),
            "ghost": "transparent",
            "ghost_hover": SOFT_COLORS["hover_bg"],
        }
        c = m.get(kind, SOFT_COLORS["accent"])
        if c == "transparent":
            return "transparent"
        return (c, c)


def _lerp_hex(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = (int(c1.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
    r2, g2, b2 = (int(c2.lstrip("#")[i:i+2], 16) for i in (0, 2, 4))
    return f"#{int(r1+(r2-r1)*t):02x}{int(g1+(g2-g1)*t):02x}{int(b1+(b2-b1)*t):02x}"


# ============================================================================
# 辅助函数
# ============================================================================

def get_ctk_color(key: str, fallback: str = None) -> str:
    return SOFT_COLORS.get(key, fallback or C.get(key, "#333333"))

def fg(color_key: str) -> tuple:
    c = get_ctk_color(color_key)
    return (c, c)

def text_fg(color_key: str = "text_primary") -> tuple:
    c = get_ctk_color(color_key)
    return (c, c)

def configure_ctk_widget(widget, **kwargs):
    """批量配置 CTk 控件"""
    try:
        widget.configure(**kwargs)
    except Exception:
        pass


# ============================================================================
# 字体快捷
# ============================================================================

FONT_FAMILY = "Microsoft YaHei UI"

def font(size: int = 14, weight: str = "normal") -> tuple:
    return (FONT_FAMILY, size, weight)

FONT_SM = font(12)
FONT_BASE = font(14)
FONT_LG = font(16, "bold")
FONT_XL = font(20, "bold")
FONT_MONO = ("Consolas", 11)


# ============================================================================
# 输入框 Focus 动效
# ============================================================================

def bind_focus_animation(entry, focus_color: str = None, blur_color: str = None):
    """为 CTkEntry 绑定 focus/blur 边框色过渡"""
    fc = focus_color or SOFT_COLORS["accent"]
    bc = blur_color or SOFT_COLORS["border_light"]

    def _on_focus_in(e):
        entry.configure(border_color=fc)

    def _on_focus_out(e):
        entry.configure(border_color=bc)

    entry.bind("<FocusIn>", _on_focus_in, add="+")
    entry.bind("<FocusOut>", _on_focus_out, add="+")
