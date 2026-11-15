#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
EggBloom v3 设计系统
专业级UI设计规范，遵循主流软件设计标准
"""

import tkinter as tk
from typing import Dict, Tuple
import json

# ============================================================================
# 色彩系统 - 多主题支持
# ============================================================================

class ColorSystem:
    """专业色彩系统"""

    BASE = {
        "bg_root": "#181B1E",
        "bg_sidebar": "#1D2124",
        "bg_card": "#23262B",
        "bg_elevated": "#282C32",
        "bg_overlay": "#2D3138",
        "bg_input": "#262A30",
        "bg_log": "#14171A",
        "text_primary": "#E6E6E6",
        "text_secondary": "#A0B4C8",
        "text_tertiary": "#6B7A8A",
        "text_muted": "#4D5A68",
        "text_sub": "#A0B4C8",
        "text_disabled": "#505050",
        "border_light": "#3A3F45",
        "border_medium": "#2E3238",
        "border_dark": "#1D2124",
        "divider": "#3A3F45",
        "accent": "#C9A96E",
        "accent_primary": "#C9A96E",
        "accent_hover": "#D4B87E",
        "accent_pressed": "#B8985E",
        "accent_secondary": "#5E9E6E",
        "accent_warning": "#C4A45A",
        "accent_danger": "#8b3a3a",
        "accent_danger_hover": "#A04848",
        "accent_info": "#A0B4C8",
        "accent_info_hover": "#B0C4D4",
        "accent_success": "#5E9E6E",
        "hover_bg": "#272B30",
        "active_bg": "#2E3238",
        "selected_bg": "#3A2A1F",
        "focus_ring": "#C9A96E60",
        "accent_soft": "#3E3628",
        "chip_bg": "#272B30",
        "chip_fg": "#E6E6E6",
        "shadow": "#00000030",
        "bg_darker": "#181B1E",
        "bg_hover": "#272B30",
        "border_default": "#3A3F45",
    }

    GRADIENTS = {
        "primary": ("#61AFEF", "#7EC8FF"),
        "success": ("#98C379", "#A3D977"),
        "warning": ("#E5C07B", "#F0D399"),
        "danger": ("#E06C75", "#EB7D86"),
    }

    @staticmethod
    def with_alpha(color: str, alpha: float) -> str:
        """为HEX颜色添加透明度"""
        if color.startswith("#") and len(color) == 7:
            alpha_hex = f"{int(alpha * 255):02x}"
            return color + alpha_hex
        return color

    @staticmethod
    def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
        """HEX转RGB"""
        hex_color = hex_color.lstrip("#")
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

    @staticmethod
    def rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
        """RGB转HEX"""
        return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"

    @staticmethod
    def lerp(color1: str, color2: str, t: float) -> str:
        """颜色插值"""
        r1, g1, b1 = ColorSystem.hex_to_rgb(color1)
        r2, g2, b2 = ColorSystem.hex_to_rgb(color2)
        r = int(r1 + (r2 - r1) * t)
        g = int(g1 + (g2 - g1) * t)
        b = int(b1 + (b2 - b1) * t)
        return ColorSystem.rgb_to_hex((r, g, b))


# ============================================================================
# 间距系统 - 8px基准网格
# ============================================================================

class SpacingSystem:
    """间距系统 (8px基准网格)"""
    
    # 基础间距
    BASE_UNIT = 8
    
    # 间距等级
    LEVELS = {
        "none": 0,      # 0px
        "xs": 4,        # 4px
        "sm": 8,        # 8px
        "md": 12,       # 12px
        "lg": 16,       # 16px
        "xl": 24,       # 24px
        "xxl": 32,      # 32px
        "xxxl": 48,     # 48px
    }
    
    # 常用组合
    COMBINATIONS = {
        "card_padding": (16, 20),           # 卡片内边距
        "section_padding": (24, 32),        # 区块内边距
        "input_padding": (12, 16),          # 输入框内边距
        "button_padding": (8, 16),          # 按钮内边距
        "table_cell_padding": (8, 12),      # 表格单元格内边距
    }
    
    @staticmethod
    def px(value: int) -> int:
        """像素值"""
        return value
    
    @staticmethod
    def rem(multiplier: float = 1.0) -> int:
        """相对单位 (基于BASE_UNIT)"""
        return int(SpacingSystem.BASE_UNIT * multiplier)
    
    @staticmethod
    def get(level: str) -> int:
        """获取指定等级的间距"""
        return SpacingSystem.LEVELS.get(level, SpacingSystem.BASE_UNIT)


# ============================================================================
# 圆角系统
# ============================================================================

class RadiusSystem:
    """圆角系统"""
    
    # 圆角等级
    LEVELS = {
        "none": 0,      # 直角
        "xs": 2,        # 2px
        "sm": 4,        # 4px
        "md": 6,        # 6px
        "lg": 8,        # 8px
        "xl": 12,       # 12px
        "xxl": 16,      # 16px
        "full": 9999,   # 全圆角
    }
    
    # 应用场景
    APPLICATIONS = {
        "button": "md",         # 按钮
        "card": "md",           # 卡片
        "input": "sm",          # 输入框
        "modal": "lg",          # 模态框
        "avatar": "full",       # 头像
        "badge": "full",        # 徽章
        "tooltip": "sm",        # 工具提示
    }
    
    @staticmethod
    def get(level: str) -> int:
        """获取圆角值"""
        return RadiusSystem.LEVELS.get(level, RadiusSystem.LEVELS["md"])
    
    @staticmethod
    def get_for(context: str) -> int:
        """获取指定场景的圆角值"""
        level = RadiusSystem.APPLICATIONS.get(context, "md")
        return RadiusSystem.get(level)


# ============================================================================
# 字体系统
# ============================================================================

class TypographySystem:
    """字体系统"""
    
    # 字体家族 (系统字体优先)
    FONT_FAMILIES = {
        "sans": ["Microsoft YaHei UI", "Segoe UI", "Helvetica Neue", "Arial", "sans-serif"],
        "mono": ["Consolas", "Monaco", "Courier New", "monospace"],
        "display": ["Microsoft YaHei UI", "Segoe UI", "Helvetica Neue", "Arial", "sans-serif"],
    }
    
    # 字号等级 (px)
    FONT_SIZES = {
        "xs": 10,       # 极小文字
        "sm": 12,       # 小文字
        "base": 14,     # 基础文字
        "lg": 16,       # 大文字
        "xl": 18,       # 超大文字
        "2xl": 20,      # 2倍文字
        "3xl": 24,      # 3倍文字
        "4xl": 32,      # 4倍文字
    }
    
    # 字重
    FONT_WEIGHTS = {
        "light": "normal",
        "normal": "normal",
        "medium": "bold",
        "semibold": "bold",
        "bold": "bold",
    }
    
    # 行高 (倍数)
    LINE_HEIGHTS = {
        "tight": 1.25,      # 紧凑
        "normal": 1.5,      # 正常
        "relaxed": 1.75,    # 宽松
        "loose": 2.0,       # 松散
    }
    
    # 应用场景
    APPLICATIONS = {
        "display_large": ("4xl", "bold", "tight"),
        "display_medium": ("3xl", "bold", "tight"),
        "display_small": ("2xl", "bold", "tight"),
        "heading_large": ("xl", "bold", "tight"),
        "heading_medium": ("lg", "bold", "tight"),
        "heading_small": ("base", "bold", "tight"),
        "body_large": ("lg", "normal", "normal"),
        "body_medium": ("base", "normal", "normal"),
        "body_small": ("sm", "normal", "normal"),
        "caption": ("xs", "normal", "normal"),
        "code": ("base", "normal", "normal"),
        "button_large": ("lg", "medium", "tight"),
        "button_medium": ("base", "medium", "tight"),
        "button_small": ("sm", "medium", "tight"),
    }
    
    @staticmethod
    def get_font_family(family: str = "sans") -> str:
        """获取字体家族"""
        families = TypographySystem.FONT_FAMILIES.get(family, TypographySystem.FONT_FAMILIES["sans"])
        for font in families:
            if font.lower() in ["microsoft yahei ui", "segoe ui"]:
                return font
        return families[0]
    
    @staticmethod
    def get_font_config(context: str) -> Dict:
        """获取指定场景的字体配置"""
        size_key, weight_key, line_height_key = TypographySystem.APPLICATIONS.get(
            context, ("base", "normal", "normal")
        )
        
        return {
            "family": TypographySystem.get_font_family(),
            "size": TypographySystem.FONT_SIZES.get(size_key, 14),
            "weight": TypographySystem.FONT_WEIGHTS.get(weight_key, "normal"),
            "line_height": TypographySystem.LINE_HEIGHTS.get(line_height_key, 1.5),
        }


# ============================================================================
# 阴影系统
# ============================================================================

class ShadowSystem:
    """阴影系统"""
    
    # 阴影等级
    LEVELS = {
        "none": "0 0 0 0 transparent",
        "xs": "0 1px 2px 0 rgba(0, 0, 0, 0.05)",
        "sm": "0 1px 3px 0 rgba(0, 0, 0, 0.1), 0 1px 2px 0 rgba(0, 0, 0, 0.06)",
        "md": "0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)",
        "lg": "0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05)",
        "xl": "0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04)",
        "inner": "inset 0 2px 4px 0 rgba(0, 0, 0, 0.06)",
    }
    
    # 应用场景
    APPLICATIONS = {
        "card": "sm",           # 卡片阴影
        "modal": "lg",          # 模态框阴影
        "dropdown": "md",       # 下拉框阴影
        "tooltip": "sm",        # 工具提示阴影
        "button": "xs",         # 按钮阴影
        "button_hover": "sm",   # 按钮悬停阴影
        "elevated": "md",       # 悬浮元素阴影
    }
    
    @staticmethod
    def get(level: str) -> str:
        """获取阴影值"""
        return ShadowSystem.LEVELS.get(level, ShadowSystem.LEVELS["none"])
    
    @staticmethod
    def get_for(context: str) -> str:
        """获取指定场景的阴影值"""
        level = ShadowSystem.APPLICATIONS.get(context, "none")
        return ShadowSystem.get(level)


# ============================================================================
# 动画系统
# ============================================================================

class AnimationSystem:
    """动画系统"""
    
    # 缓动函数
    EASING = {
        "linear": "linear",
        "ease_in": "cubic-bezier(0.4, 0, 1, 1)",
        "ease_out": "cubic-bezier(0, 0, 0.2, 1)",
        "ease_in_out": "cubic-bezier(0.4, 0, 0.2, 1)",
        "bounce": "cubic-bezier(0.68, -0.55, 0.265, 1.55)",
    }
    
    # 持续时间 (毫秒)
    DURATIONS = {
        "fast": 150,    # 快速动画
        "normal": 250,  # 正常动画
        "slow": 350,    # 慢速动画
        "slower": 500,  # 更慢动画
    }
    
    # 应用场景
    APPLICATIONS = {
        "hover": ("fast", "ease_out"),
        "press": ("fast", "ease_in"),
        "focus": ("normal", "ease_out"),
        "modal": ("slow", "ease_out"),
        "toast": ("normal", "ease_out"),
        "loading": ("slower", "linear"),
    }
    
    @staticmethod
    def get_duration(context: str) -> int:
        """获取动画持续时间"""
        duration_key, _ = AnimationSystem.APPLICATIONS.get(context, ("normal", "ease_out"))
        return AnimationSystem.DURATIONS.get(duration_key, 250)
    
    @staticmethod
    def get_easing(context: str) -> str:
        """获取缓动函数"""
        _, easing_key = AnimationSystem.APPLICATIONS.get(context, ("normal", "ease_out"))
        return AnimationSystem.EASING.get(easing_key, "ease_out")


# ============================================================================
# 图标系统
# ============================================================================

class IconSystem:
    """图标系统 (使用Unicode符号)"""
    
    # 常用图标
    ICONS = {
        # 导航
        "home": "🏠",
        "dashboard": "📊",
        "settings": "⚙️",
        "plugins": "🧩",
        "logs": "📝",
        "analytics": "📈",
        "users": "👥",
        "bot": "🤖",
        
        # 操作
        "play": "▶️",
        "pause": "⏸️",
        "stop": "⏹️",
        "restart": "🔄",
        "refresh": "↻",
        "save": "💾",
        "export": "📤",
        "import": "📥",
        "download": "⬇️",
        "upload": "⬆️",
        "add": "➕",
        "remove": "➖",
        "edit": "✏️",
        "delete": "🗑️",
        "copy": "📋",
        "paste": "📋",
        "cut": "✂️",
        "search": "🔍",
        "filter": "🔍",
        "sort": "⇅",
        
        # 状态
        "success": "✅",
        "error": "❌",
        "warning": "⚠️",
        "info": "ℹ️",
        "question": "❓",
        "loading": "⏳",
        "check": "✓",
        "close": "✕",
        "menu": "☰",
        "more": "⋯",
        
        # 通信
        "message": "💬",
        "chat": "💭",
        "notification": "🔔",
        "bell": "🔔",
        "mail": "✉️",
        "phone": "📞",
        "video": "🎥",
        "audio": "🔊",
        "mute": "🔇",
        
        # 文件
        "file": "📄",
        "folder": "📁",
        "image": "🖼️",
        "video_file": "🎬",
        "audio_file": "🎵",
        "document": "📄",
        "pdf": "📕",
        "zip": "🗜️",
        
        # 网络
        "network": "🌐",
        "wifi": "📶",
        "bluetooth": "📡",
        "cloud": "☁️",
        "server": "🖥️",
        "database": "🗃️",
        
        # 时间
        "clock": "🕐",
        "calendar": "📅",
        "timer": "⏱️",
        "history": "🕰️",
        
        # 用户
        "user": "👤",
        "users": "👥",
        "group": "👥",
        "profile": "👤",
        "login": "🔑",
        "logout": "🚪",
        "shield": "🛡️",
        "key": "🔑",
        "lock": "🔒",
        "unlock": "🔓",
        
        # 其他
        "star": "⭐",
        "heart": "❤️",
        "like": "👍",
        "dislike": "👎",
        "flag": "🚩",
        "bookmark": "🔖",
        "link": "🔗",
        "external": "↗️",
        "help": "❓",
        "about": "ℹ️",
    }
    
    @staticmethod
    def get(icon_name: str, fallback: str = "●") -> str:
        """获取图标"""
        return IconSystem.ICONS.get(icon_name, fallback)


# ============================================================================
# 设计系统管理器
# ============================================================================

class DesignSystem:
    """设计系统管理器"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.colors = ColorSystem()
        self.spacing = SpacingSystem()
        self.radius = RadiusSystem()
        self.typography = TypographySystem()
        self.shadows = ShadowSystem()
        self.animations = AnimationSystem()
        self.icons = IconSystem()

        self._initialized = True

    def get_theme(self) -> Dict:
        """获取完整主题配置"""
        return {
            "colors": self.colors.BASE,
            "spacing": self.spacing.LEVELS,
            "radius": self.radius.LEVELS,
            "typography": self.typography.APPLICATIONS,
            "shadows": self.shadows.LEVELS,
            "animations": self.animations.APPLICATIONS,
            "icons": list(self.icons.ICONS.keys()),
        }
    
    def export_theme(self, filepath: str):
        """导出主题配置到文件"""
        theme = self.get_theme()
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(theme, f, ensure_ascii=False, indent=2)
    
    def create_style_sheet(self) -> str:
        """创建CSS样式表 (用于Web版本)"""
        css = []
        css.append("/* EggBloom v3 Design System */")
        css.append("")
        
        # 颜色变量
        css.append(":root {")
        for name, value in self.colors.BASE.items():
            css.append(f"  --color-{name}: {value};")
        css.append("}")
        css.append("")
        
        # 间距变量
        css.append(":root {")
        for name, value in self.spacing.LEVELS.items():
            css.append(f"  --spacing-{name}: {value}px;")
        css.append("}")
        css.append("")
        
        return "\n".join(css)


# ============================================================================
# 工具函数
# ============================================================================

def apply_style(widget, style_config: Dict):
    """应用样式到tkinter组件"""
    if "bg" in style_config:
        widget.configure(bg=style_config["bg"])
    if "fg" in style_config:
        widget.configure(fg=style_config["fg"])
    if "font" in style_config:
        widget.configure(font=style_config["font"])
    if "relief" in style_config:
        widget.configure(relief=style_config["relief"])
    if "borderwidth" in style_config:
        widget.configure(borderwidth=style_config["borderwidth"])


def create_gradient_canvas(parent, width, height, color1, color2, direction="horizontal"):
    """创建渐变画布"""
    canvas = tk.Canvas(parent, width=width, height=height, highlightthickness=0)
    
    if direction == "horizontal":
        for i in range(width):
            t = i / width
            color = ColorSystem.lerp(color1, color2, t)
            canvas.create_line(i, 0, i, height, fill=color)
    else:  # vertical
        for i in range(height):
            t = i / height
            color = ColorSystem.lerp(color1, color2, t)
            canvas.create_line(0, i, width, i, fill=color)
    
    return canvas


# ============================================================================
# 设计系统实例
# ============================================================================

# 全局设计系统实例
ds = DesignSystem()

# 快捷访问
C = ds.colors.BASE
S = ds.spacing
R = ds.radius
T = ds.typography
SH = ds.shadows
A = ds.animations
I = ds.icons

if __name__ == "__main__":
    # 测试设计系统
    print("EggBloom v3 Design System")
    print("=" * 50)
    
    print(f"主色: {C['accent_primary']}")
    print(f"基础间距: {S.get('md')}px")
    print(f"卡片圆角: {R.get_for('card')}px")
    print(f"标题字体: {T.get_font_config('heading_large')}")
    print(f"卡片阴影: {SH.get_for('card')}")
    print(f"悬停动画: {A.get_duration('hover')}ms")
    print(f"设置图标: {I.get('settings')}")
    
    # 导出主题
    ds.export_theme("design_theme.json")
    print("\n主题已导出到: design_theme.json")