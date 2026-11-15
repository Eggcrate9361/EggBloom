#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
卡片组件 - 带阴影、圆角、悬停效果的卡片容器
"""

import tkinter as tk
from typing import Optional, Callable, Any, Tuple
from design_system import C, S, T, I

class Card(tk.Frame):
    """现代化卡片组件"""
    
    def __init__(
        self,
        parent,
        title: str = "",
        subtitle: str = "",
        icon: str = "",
        padding: Tuple[int, int] = (S.get("lg"), S.get("lg")),
        margin: Tuple[int, int] = (0, S.get("sm")),
        border: bool = True,
        shadow: bool = True,
        hover_effect: bool = True,
        clickable: bool = False,
        on_click: Optional[Callable] = None,
        **kwargs
    ):
        # 基础配置
        bg = kwargs.pop("bg", C["bg_card"])
        super().__init__(parent, bg=bg, **kwargs)
        
        # 属性
        self.title = title
        self.subtitle = subtitle
        self.icon = icon
        self.padding = padding
        self.margin = margin
        self.border = border
        self.shadow = shadow
        self.hover_effect = hover_effect
        self.clickable = clickable
        self.on_click = on_click
        
        # 状态
        self._is_hovered = False
        self._is_pressed = False
        
        # 应用外边距
        if any(margin):
            self.grid(padx=margin[0], pady=margin[1])
        
        # 创建阴影效果
        if shadow:
            self._create_shadow()
        
        # 创建边框
        if border:
            self._create_border()
        
        # 创建内容容器
        self.content_frame = tk.Frame(self, bg=bg)
        padx, pady = padding
        self.content_frame.pack(fill=tk.BOTH, expand=True, padx=padx, pady=pady)
        
        # 创建标题栏
        self.title_frame = None
        if title or icon:
            self._create_title_bar()
        
        # 创建内容区占位
        self.body_frame = tk.Frame(self.content_frame, bg=bg)
        self.body_frame.pack(fill=tk.BOTH, expand=True)
        
        # 绑定事件
        if hover_effect or clickable:
            self._bind_events()
    
    def _create_shadow(self):
        """创建阴影效果"""
        # 在tkinter中模拟阴影效果
        shadow_frame = tk.Frame(
            self.master,
            bg=C["shadow"],
            highlightthickness=0,
        )
        shadow_frame.place(
            x=self.winfo_x() + 2,
            y=self.winfo_y() + 2,
            width=self.winfo_reqwidth(),
            height=self.winfo_reqheight(),
        )
        self.shadow_frame = shadow_frame
        
        # 将阴影帧置于底层
        shadow_frame.lower(self)
    
    def _create_border(self):
        """创建边框"""
        self.configure(
            highlightbackground=C["border_light"],
            highlightcolor=C["border_light"],
            highlightthickness=1,
            bd=0,
        )
    
    def _create_title_bar(self):
        """创建标题栏"""
        self.title_frame = tk.Frame(self.content_frame, bg=self["bg"])
        self.title_frame.pack(fill=tk.X, pady=(0, S.get("md")))
        
        # 左侧：图标 + 标题
        left_frame = tk.Frame(self.title_frame, bg=self["bg"])
        left_frame.pack(side=tk.LEFT, fill=tk.Y)
        
        if self.icon:
            icon_label = tk.Label(
                left_frame,
                text=I.get(self.icon, "●"),
                font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
                fg=C["accent_primary"],
                bg=self["bg"],
            )
            icon_label.pack(side=tk.LEFT, padx=(0, S.get("sm")))
        
        if self.title:
            title_label = tk.Label(
                left_frame,
                text=self.title,
                font=(
                    T.get_font_family(),
                    T.get_font_config("heading_small")["size"],
                    T.get_font_config("heading_small")["weight"],
                ),
                fg=C["text_primary"],
                bg=self["bg"],
            )
            title_label.pack(side=tk.LEFT)
        
        # 副标题
        if self.subtitle:
            subtitle_label = tk.Label(
                self.title_frame,
                text=self.subtitle,
                font=(
                    T.get_font_family(),
                    T.get_font_config("body_small")["size"],
                ),
                fg=C["text_tertiary"],
                bg=self["bg"],
            )
            subtitle_label.pack(side=tk.RIGHT)
    
    def _bind_events(self):
        """绑定交互事件"""
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        
        # 传递给子组件
        for child in self.winfo_children():
            child.bind("<Enter>", self._on_enter)
            child.bind("<Leave>", self._on_leave)
            child.bind("<ButtonPress-1>", self._on_press)
            child.bind("<ButtonRelease-1>", self._on_release)
    
    def _on_enter(self, event=None):
        """鼠标进入"""
        self._is_hovered = True
        if self.hover_effect:
            self.configure(bg=C["hover_bg"])
            if hasattr(self, 'content_frame'):
                self.content_frame.configure(bg=C["hover_bg"])
            if hasattr(self, 'body_frame'):
                self.body_frame.configure(bg=C["hover_bg"])
            if self.title_frame:
                self.title_frame.configure(bg=C["hover_bg"])
                for child in self.title_frame.winfo_children():
                    if isinstance(child, tk.Frame):
                        child.configure(bg=C["hover_bg"])
                    elif isinstance(child, tk.Label):
                        child.configure(bg=C["hover_bg"])
        
        if self.clickable:
            self.configure(cursor="hand2")
    
    def _on_leave(self, event=None):
        """鼠标离开"""
        self._is_hovered = False
        self._is_pressed = False
        if self.hover_effect:
            original_bg = self.original_bg if hasattr(self, 'original_bg') else C["bg_card"]
            self.configure(bg=original_bg)
            if hasattr(self, 'content_frame'):
                self.content_frame.configure(bg=original_bg)
            if hasattr(self, 'body_frame'):
                self.body_frame.configure(bg=original_bg)
            if self.title_frame:
                self.title_frame.configure(bg=original_bg)
                for child in self.title_frame.winfo_children():
                    if isinstance(child, tk.Frame):
                        child.configure(bg=original_bg)
                    elif isinstance(child, tk.Label):
                        child.configure(bg=original_bg)
        
        if self.clickable:
            self.configure(cursor="")
    
    def _on_press(self, event=None):
        """鼠标按下"""
        self._is_pressed = True
        if self.clickable:
            self.configure(bg=C["active_bg"])
            if hasattr(self, 'content_frame'):
                self.content_frame.configure(bg=C["active_bg"])
            if hasattr(self, 'body_frame'):
                self.body_frame.configure(bg=C["active_bg"])
            if self.title_frame:
                self.title_frame.configure(bg=C["active_bg"])
    
    def _on_release(self, event=None):
        """鼠标释放"""
        if self._is_pressed and self.clickable and self.on_click:
            self.on_click()
        self._is_pressed = False
        self._on_enter()  # 恢复悬停状态
    
    def set_content(self, widget):
        """设置卡片内容"""
        # 清空现有内容
        for child in self.body_frame.winfo_children():
            child.destroy()
        
        # 添加新内容
        widget.pack(fill=tk.BOTH, expand=True)
    
    def add_widget(self, widget, **pack_args):
        """添加组件到卡片内容区"""
        default_pack = {"fill": tk.BOTH, "expand": True, "pady": (0, S.get("sm"))}
        default_pack.update(pack_args)
        widget.pack(**default_pack)
    
    def set_title(self, title: str, subtitle: str = ""):
        """设置标题"""
        self.title = title
        self.subtitle = subtitle
        
        # 重新创建标题栏
        if self.title_frame:
            self.title_frame.destroy()
        self._create_title_bar()
    
    def set_icon(self, icon: str):
        """设置图标"""
        self.icon = icon
        
        # 重新创建标题栏
        if self.title_frame:
            self.title_frame.destroy()
        self._create_title_bar()
    
    def set_clickable(self, clickable: bool, on_click: Optional[Callable] = None):
        """设置是否可点击"""
        self.clickable = clickable
        if on_click:
            self.on_click = on_click
        
        # 更新事件绑定
        if clickable:
            self._bind_events()
            self.configure(cursor="hand2" if self._is_hovered else "")
        else:
            self.configure(cursor="")
    
    def update_style(self, **kwargs):
        """更新样式"""
        if "bg" in kwargs:
            self.original_bg = kwargs["bg"]
            self.configure(bg=kwargs["bg"])
            if hasattr(self, 'content_frame'):
                self.content_frame.configure(bg=kwargs["bg"])
            if hasattr(self, 'body_frame'):
                self.body_frame.configure(bg=kwargs["bg"])
            if self.title_frame:
                self.title_frame.configure(bg=kwargs["bg"])
        
        if "border" in kwargs:
            self.border = kwargs["border"]
            if kwargs["border"]:
                self._create_border()
            else:
                self.configure(highlightthickness=0)
        
        if "shadow" in kwargs:
            self.shadow = kwargs["shadow"]
            if hasattr(self, 'shadow_frame'):
                self.shadow_frame.destroy()
            if kwargs["shadow"]:
                self._create_shadow()


class StatCard(Card):
    """统计卡片 - 用于显示数字统计"""
    
    def __init__(
        self,
        parent,
        title: str = "",
        value: Any = 0,
        unit: str = "",
        trend: Optional[float] = None,  # 趋势百分比，正数上升，负数下降
        icon: str = "",
        color: str = C["accent_primary"],
        **kwargs
    ):
        super().__init__(parent, title=title, icon=icon, **kwargs)
        
        self.value = value
        self.unit = unit
        self.trend = trend
        self.color = color
        
        self._create_stat_content()
    
    def _create_stat_content(self):
        """创建统计内容"""
        # 数值显示
        value_frame = tk.Frame(self.body_frame, bg=self["bg"])
        value_frame.pack(fill=tk.X, pady=(S.get("md"), S.get("sm")))
        
        # 数值
        self.value_label = tk.Label(
            value_frame,
            text=str(self.value),
            font=(
                T.get_font_family(),
                T.get_font_config("display_small")["size"],
                "bold",
            ),
            fg=self.color,
            bg=self["bg"],
        )
        self.value_label.pack(side=tk.LEFT)
        
        # 单位
        if self.unit:
            unit_label = tk.Label(
                value_frame,
                text=self.unit,
                font=(
                    T.get_font_family(),
                    T.get_font_config("body_small")["size"],
                ),
                fg=C["text_secondary"],
                bg=self["bg"],
            )
            unit_label.pack(side=tk.LEFT, padx=(S.get("xs"), 0), pady=(S.get("sm"), 0))
        
        # 趋势
        if self.trend is not None:
            trend_frame = tk.Frame(self.body_frame, bg=self["bg"])
            trend_frame.pack(fill=tk.X)
            
            trend_icon = "↗️" if self.trend > 0 else "↘️" if self.trend < 0 else "➡️"
            trend_color = C["accent_success"] if self.trend > 0 else C["accent_danger"] if self.trend < 0 else C["text_tertiary"]
            
            trend_text = f"{trend_icon} {abs(self.trend):.1f}%"
            
            trend_label = tk.Label(
                trend_frame,
                text=trend_text,
                font=(
                    T.get_font_family(),
                    T.get_font_config("caption")["size"],
                ),
                fg=trend_color,
                bg=self["bg"],
            )
            trend_label.pack(side=tk.LEFT)
    
    def set_value(self, value: Any, unit: str = ""):
        """更新数值"""
        self.value = value
        self.value_label.configure(text=str(value))
        
        if unit:
            self.unit = unit
    
    def set_trend(self, trend: float):
        """更新趋势"""
        self.trend = trend
        # 这里需要重新创建趋势显示
        for child in self.body_frame.winfo_children():
            if isinstance(child, tk.Frame) and child != self.value_label.master:
                child.destroy()
        
        if trend is not None:
            self._create_stat_content()


class ActionCard(Card):
    """操作卡片 - 带操作按钮的卡片"""
    
    def __init__(
        self,
        parent,
        title: str = "",
        description: str = "",
        icon: str = "",
        actions: Optional[list] = None,  # [(text, callback, style), ...]
        **kwargs
    ):
        super().__init__(parent, title=title, icon=icon, **kwargs)
        
        self.description = description
        self.actions = actions or []
        
        self._create_action_content()
    
    def _create_action_content(self):
        """创建操作内容"""
        # 描述
        if self.description:
            desc_label = tk.Label(
                self.body_frame,
                text=self.description,
                font=(
                    T.get_font_family(),
                    T.get_font_config("body_medium")["size"],
                ),
                fg=C["text_secondary"],
                bg=self["bg"],
                wraplength=300,
                justify=tk.LEFT,
            )
            desc_label.pack(fill=tk.X, pady=(0, S.get("lg")))
        
        # 操作按钮
        if self.actions:
            actions_frame = tk.Frame(self.body_frame, bg=self["bg"])
            actions_frame.pack(fill=tk.X)
            
            for i, (text, callback, style) in enumerate(self.actions):
                btn = tk.Label(
                    actions_frame,
                    text=text,
                    font=(
                        T.get_font_family(),
                        T.get_font_config("button_small")["size"],
                        T.get_font_config("button_small")["weight"],
                    ),
                    fg=C["accent_primary"] if style == "primary" else C["text_secondary"],
                    bg=C["bg_elevated"] if style == "primary" else C["bg_card"],
                    cursor="hand2",
                    padx=S.get("lg"),
                    pady=S.get("sm"),
                    relief=tk.FLAT,
                )
                btn.pack(side=tk.LEFT, padx=(0, S.get("sm")) if i < len(self.actions) - 1 else (0, 0))
                
                # 绑定点击事件
                btn.bind("<Button-1>", lambda e, cb=callback: cb())
                btn.bind("<Enter>", lambda e, b=btn: b.configure(
                    bg=C["hover_bg"] if style == "primary" else C["bg_elevated"]
                ))
                btn.bind("<Leave>", lambda e, b=btn, s=style: b.configure(
                    bg=C["bg_elevated"] if s == "primary" else C["bg_card"]
                ))


if __name__ == "__main__":
    # 测试卡片组件
    root = tk.Tk()
    root.title("Card Component Test")
    root.geometry("800x600")
    root.configure(bg=C["bg_root"])
    
    # 普通卡片
    card1 = Card(
        root,
        title="普通卡片",
        subtitle="这是一个示例卡片",
        icon="file",
        padding=(S.get("lg"), S.get("lg")),
        border=True,
        shadow=True,
        hover_effect=True,
    )
    card1.pack(fill=tk.X, padx=S.get("lg"), pady=S.get("sm"))
    
    label1 = tk.Label(
        card1.body_frame,
        text="这是卡片的内容区域",
        fg=C["text_secondary"],
        bg=card1["bg"],
    )
    label1.pack(pady=S.get("md"))
    
    # 统计卡片
    card2 = StatCard(
        root,
        title="消息统计",
        value=1256,
        unit="条",
        trend=12.5,
        icon="message",
        color=C["accent_success"],
    )
    card2.pack(fill=tk.X, padx=S.get("lg"), pady=S.get("sm"))
    
    # 操作卡片
    def test_action():
        print("按钮被点击")
    
    card3 = ActionCard(
        root,
        title="操作卡片",
        description="这是一个带操作按钮的卡片",
        icon="settings",
        actions=[
            ("主要操作", test_action, "primary"),
            ("次要操作", test_action, "secondary"),
        ],
    )
    card3.pack(fill=tk.X, padx=S.get("lg"), pady=S.get("sm"))
    
    # 可点击卡片
    def on_card_click():
        print("卡片被点击")
    
    card4 = Card(
        root,
        title="可点击卡片",
        subtitle="点击试试",
        icon="bot",
        clickable=True,
        on_click=on_card_click,
    )
    card4.pack(fill=tk.X, padx=S.get("lg"), pady=S.get("sm"))
    
    root.mainloop()