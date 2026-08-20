#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
按钮组件 - 现代化按钮，支持多种样式和状态
"""

import tkinter as tk
from typing import Optional, Callable
from design_system import C, S, R, T, I
from animator import Animator

class Button(tk.Frame):
    """现代化按钮组件"""
    
    STYLES = {
        "primary": {
            "bg": C["accent_primary"],
            "fg": C["text_primary"],
            "hover_bg": C["accent_hover"],
            "active_bg": C["accent_pressed"],
            "border": "none",
        },
        "secondary": {
            "bg": C["bg_elevated"],
            "fg": C["text_secondary"],
            "hover_bg": C["hover_bg"],
            "active_bg": C["active_bg"],
            "border": f"1px solid {C['border_light']}",
        },
        "ghost": {
            "bg": "transparent",
            "fg": C["text_secondary"],
            "hover_bg": C["hover_bg"],
            "active_bg": C["active_bg"],
            "border": "none",
        },
        "danger": {
            "bg": C["accent_danger"],
            "fg": C["text_primary"],
            "hover_bg": C["accent_danger_hover"],
            "active_bg": C["accent_danger"],
            "border": "none",
        },
        "success": {
            "bg": C["accent_success"],
            "fg": C["text_primary"],
            "hover_bg": C["accent_success"],
            "active_bg": C["accent_success"],
            "border": "none",
        },
    }
    
    SIZES = {
        "sm": {"padding": (S.get("sm"), S.get("xs")), "font": "button_small"},
        "md": {"padding": (S.get("md"), S.get("sm")), "font": "button_medium"},
        "lg": {"padding": (S.get("lg"), S.get("md")), "font": "button_large"},
    }
    
    def __init__(
        self,
        parent,
        text: str = "",
        icon: str = "",
        style: str = "primary",
        size: str = "md",
        rounded: bool = True,
        loading: bool = False,
        disabled: bool = False,
        full_width: bool = False,
        on_click: Optional[Callable] = None,
        **kwargs
    ):
        # 基础配置
        bg = kwargs.pop("bg", "transparent")
        super().__init__(parent, bg=bg, **kwargs)
        
        # 属性
        self.text = text
        self.icon = icon
        self.style = style
        self.size = size
        self.rounded = rounded
        self.loading = loading
        self.disabled = disabled
        self.full_width = full_width
        self.on_click = on_click
        
        # 状态
        self._is_hovered = False
        self._is_pressed = False
        self._is_loading = loading
        
        # 获取样式配置
        self.style_config = self.STYLES.get(style, self.STYLES["primary"])
        self.size_config = self.SIZES.get(size, self.SIZES["md"])
        
        # 创建按钮标签
        self._create_button_label()
        
        # 绑定事件
        if not disabled:
            self._bind_events()
        
        # 应用初始样式
        self._update_appearance()
    
    def _create_button_label(self):
        """创建按钮标签"""
        # 按钮内容
        content = ""
        if self.icon:
            content += f"{I.get(self.icon)} "
        if self.text:
            content += self.text
        
        # 字体配置
        font_config = T.get_font_config(self.size_config["font"])
        font = (T.get_font_family(), font_config["size"], font_config["weight"])
        
        # 创建标签
        self.label = tk.Label(
            self,
            text=content,
            font=font,
            cursor="hand2" if not self.disabled else "",
            padx=self.size_config["padding"][0],
            pady=self.size_config["padding"][1],
        )
        
        # 圆角处理
        if self.rounded:
            radius = R.get_for("button")
            self.label.configure(bd=0, highlightthickness=0)
            # 在tkinter中，我们通过配置背景色和边框来模拟圆角
        
        self.label.pack(fill=tk.BOTH if self.full_width else tk.NONE, expand=self.full_width)
    
    def _bind_events(self):
        """绑定交互事件"""
        self.label.bind("<Enter>", self._on_enter)
        self.label.bind("<Leave>", self._on_leave)
        self.label.bind("<ButtonPress-1>", self._on_press)
        self.label.bind("<ButtonRelease-1>", self._on_release)
    
    def _on_enter(self, event=None):
        """鼠标进入"""
        if self.disabled or self._is_loading:
            return
        
        self._is_hovered = True
        self._update_appearance()
    
    def _on_leave(self, event=None):
        """鼠标离开"""
        self._is_hovered = False
        self._is_pressed = False
        self._update_appearance()
    
    def _on_press(self, event=None):
        """鼠标按下"""
        if self.disabled or self._is_loading:
            return
        
        self._is_pressed = True
        self._update_appearance()
    
    def _on_release(self, event=None):
        """鼠标释放"""
        if self.disabled or self._is_loading:
            return
        
        self._is_pressed = False
        self._update_appearance()
        
        # 触发点击事件
        if self.on_click and self._is_hovered:
            self.on_click()
    
    def _update_appearance(self):
        """更新外观"""
        if self.disabled:
            target_bg = C["bg_input"]
            target_fg = C["text_disabled"]
            cursor = ""
        elif self._is_loading:
            target_bg = self.style_config["bg"]
            target_fg = self.style_config["fg"]
            cursor = "wait"
        elif self._is_pressed:
            target_bg = self.style_config.get("active_bg", self.style_config["bg"])
            target_fg = self.style_config["fg"]
            cursor = "hand2"
        elif self._is_hovered:
            target_bg = self.style_config.get("hover_bg", self.style_config["bg"])
            target_fg = self.style_config["fg"]
            cursor = "hand2"
        else:
            target_bg = self.style_config["bg"]
            target_fg = self.style_config["fg"]
            cursor = "hand2"

        # 平滑颜色过渡
        self._animate_color(target_bg, target_fg, cursor)

    def _animate_color(self, target_bg, target_fg, cursor):
        """平滑颜色过渡动画，走共享 tick，避免 after 风暴"""
        try:
            current_bg = self.label.cget("bg")
            if current_bg in ("transparent", "", None):
                current_bg = self.style_config["bg"]
        except Exception:
            current_bg = target_bg

        if current_bg == target_bg:
            self.label.configure(bg=target_bg, fg=target_fg, cursor=cursor)
            self.configure(bg=target_bg)
            return

        def apply(hex_color):
            self.label.configure(bg=hex_color, fg=target_fg, cursor=cursor)
            self.configure(bg=hex_color)

        Animator.animate_color(
            widget=self,
            from_hex=current_bg,
            to_hex=target_bg,
            duration=120,
            easing="ease_out",
            on_complete=lambda: self.label.configure(bg=target_bg, fg=target_fg, cursor=cursor) or self.configure(bg=target_bg),
            prop="bg",
        )

    def set_icon(self, icon: str):
        """设置按钮图标"""
        self.icon = icon
        content = ""
        if self.icon:
            content += f"{I.get(self.icon)} "
        if self.text:
            content += self.text
        self.label.configure(text=content)
    
    def set_loading(self, loading: bool):
        """设置加载状态"""
        self._is_loading = loading
        self._update_appearance()
        
        if loading:
            # 可以在这里添加加载动画
            pass
    
    def set_disabled(self, disabled: bool):
        """设置禁用状态"""
        self.disabled = disabled
        
        if disabled:
            self.label.unbind("<Enter>")
            self.label.unbind("<Leave>")
            self.label.unbind("<ButtonPress-1>")
            self.label.unbind("<ButtonRelease-1>")
        else:
            self._bind_events()
        
        self._update_appearance()
    
    def set_style(self, style: str):
        """设置按钮样式"""
        self.style = style
        self.style_config = self.STYLES.get(style, self.STYLES["primary"])
        self._update_appearance()
    
    def set_on_click(self, callback: Callable):
        """设置点击回调"""
        self.on_click = callback


class IconButton(Button):
    """图标按钮 - 只有图标的按钮"""
    
    def __init__(
        self,
        parent,
        icon: str,
        tooltip: str = "",
        **kwargs
    ):
        # 强制设置图标
        kwargs["icon"] = icon
        kwargs["text"] = ""
        
        # 默认使用ghost样式
        if "style" not in kwargs:
            kwargs["style"] = "ghost"
        
        # 默认使用小尺寸
        if "size" not in kwargs:
            kwargs["size"] = "sm"
        
        super().__init__(parent, **kwargs)
        
        self.tooltip_text = tooltip
        
        # 调整内边距
        self.label.configure(padx=S.get("sm"), pady=S.get("sm"))
    
    def set_tooltip(self, text: str):
        """设置工具提示"""
        self.tooltip_text = text


class ToggleButton(Button):
    """切换按钮 - 支持选中状态"""
    
    def __init__(
        self,
        parent,
        text: str = "",
        icon: str = "",
        checked: bool = False,
        on_change: Optional[Callable[[bool], None]] = None,
        **kwargs
    ):
        # 默认使用secondary样式
        if "style" not in kwargs:
            kwargs["style"] = "secondary"
        
        super().__init__(parent, text=text, icon=icon, **kwargs)
        
        self.checked = checked
        self.on_change = on_change
        
        # 覆盖点击事件
        self.on_click = self._toggle
        
        # 更新初始状态
        self._update_toggle_appearance()
    
    def _toggle(self):
        """切换选中状态"""
        self.checked = not self.checked
        self._update_toggle_appearance()
        
        if self.on_change:
            self.on_change(self.checked)
    
    def _update_toggle_appearance(self):
        """更新切换按钮外观"""
        if self.checked:
            # 选中状态
            self.style_config = {
                "bg": C["selected_bg"],
                "fg": C["accent_primary"],
                "hover_bg": C["selected_bg"],
                "active_bg": C["selected_bg"],
                "border": f"1px solid {C['accent_primary']}",
            }
        else:
            # 未选中状态
            self.style_config = self.STYLES.get(self.style, self.STYLES["secondary"])
        
        self._update_appearance()
    
    def set_checked(self, checked: bool):
        """设置选中状态"""
        if self.checked != checked:
            self.checked = checked
            self._update_toggle_appearance()
    
    def get_checked(self) -> bool:
        """获取选中状态"""
        return self.checked


class ButtonGroup(tk.Frame):
    """按钮组 - 将多个按钮组合在一起"""
    
    def __init__(
        self,
        parent,
        buttons: Optional[list] = None,  # [(text, callback, style), ...]
        vertical: bool = False,
        spacing: int = S.get("xs"),
        **kwargs
    ):
        bg = kwargs.pop("bg", "transparent")
        super().__init__(parent, bg=bg, **kwargs)
        
        self.buttons = []
        self.vertical = vertical
        
        if buttons:
            for btn_config in buttons:
                self.add_button(*btn_config)
        
        self._layout_buttons(spacing)
    
    def _layout_buttons(self, spacing: int):
        """布局按钮"""
        pack_args = {
            "side": tk.TOP if self.vertical else tk.LEFT,
            "padx": 0 if self.vertical else (0, spacing),
            "pady": (0, spacing) if self.vertical else 0,
            "fill": tk.X if self.vertical else tk.NONE,
        }
        
        for btn in self.buttons:
            btn.pack(**pack_args)
        
        # 最后一个按钮不需要间距
        if self.buttons:
            if self.vertical:
                self.buttons[-1].pack_configure(pady=0)
            else:
                self.buttons[-1].pack_configure(padx=0)
    
    def add_button(self, text: str, callback: Callable, style: str = "secondary", **kwargs):
        """添加按钮"""
        btn = Button(self, text=text, style=style, on_click=callback, **kwargs)
        self.buttons.append(btn)
        return btn
    
    def add_icon_button(self, icon: str, callback: Callable, tooltip: str = "", **kwargs):
        """添加图标按钮"""
        btn = IconButton(self, icon=icon, on_click=callback, **kwargs)
        self.buttons.append(btn)
        return btn
    
    def add_toggle_button(self, text: str, checked: bool = False, on_change: Optional[Callable] = None, **kwargs):
        """添加切换按钮"""
        btn = ToggleButton(self, text=text, checked=checked, on_change=on_change, **kwargs)
        self.buttons.append(btn)
        return btn
    
    def clear(self):
        """清空所有按钮"""
        for btn in self.buttons:
            btn.destroy()
        self.buttons = []


if __name__ == "__main__":
    # 测试按钮组件
    root = tk.Tk()
    root.title("Button Component Test")
    root.geometry("800x600")
    root.configure(bg=C["bg_root"])
    
    # 标题
    title = tk.Label(
        root,
        text="按钮组件测试",
        font=(T.get_font_family(), T.get_font_config("heading_large")["size"], "bold"),
        fg=C["text_primary"],
        bg=C["bg_root"],
    )
    title.pack(pady=S.get("xl"))
    
    # 不同样式的按钮
    styles_frame = tk.Frame(root, bg=C["bg_root"])
    styles_frame.pack(pady=S.get("lg"))
    
    styles = ["primary", "secondary", "ghost", "danger", "success"]
    
    for i, style in enumerate(styles):
        frame = tk.Frame(styles_frame, bg=C["bg_root"])
        frame.pack(side=tk.LEFT, padx=S.get("md"))
        
        label = tk.Label(
            frame,
            text=style.capitalize(),
            font=(T.get_font_family(), T.get_font_config("body_small")["size"]),
            fg=C["text_secondary"],
            bg=C["bg_root"],
        )
        label.pack(pady=(0, S.get("sm")))
        
        def make_callback(s):
            def callback():
                print(f"{s}按钮被点击")
            return callback
        
        btn = Button(
            frame,
            text=f"{style}按钮",
            style=style,
            on_click=make_callback(style),
        )
        btn.pack()
    
    # 不同尺寸的按钮
    sizes_frame = tk.Frame(root, bg=C["bg_root"])
    sizes_frame.pack(pady=S.get("lg"))
    
    sizes = ["sm", "md", "lg"]
    
    for i, size in enumerate(sizes):
        frame = tk.Frame(sizes_frame, bg=C["bg_root"])
        frame.pack(side=tk.LEFT, padx=S.get("md"))
        
        label = tk.Label(
            frame,
            text=f"Size: {size}",
            font=(T.get_font_family(), T.get_font_config("body_small")["size"]),
            fg=C["text_secondary"],
            bg=C["bg_root"],
        )
        label.pack(pady=(0, S.get("sm")))
        
        btn = Button(
            frame,
            text=f"{size}按钮",
            size=size,
            on_click=lambda s=size: print(f"{s}按钮被点击"),
        )
        btn.pack()
    
    # 图标按钮
    icons_frame = tk.Frame(root, bg=C["bg_root"])
    icons_frame.pack(pady=S.get("lg"))
    
    icons = ["settings", "save", "delete", "refresh", "add"]
    
    for icon in icons:
        btn = IconButton(
            icons_frame,
            icon=icon,
            tooltip=f"{icon}按钮",
            on_click=lambda i=icon: print(f"{i}图标按钮被点击"),
        )
        btn.pack(side=tk.LEFT, padx=S.get("sm"))
    
    # 切换按钮
    toggle_frame = tk.Frame(root, bg=C["bg_root"])
    toggle_frame.pack(pady=S.get("lg"))
    
    toggle_btn = ToggleButton(
        toggle_frame,
        text="切换按钮",
        on_change=lambda state: print(f"切换状态: {state}"),
    )
    toggle_btn.pack()
    
    # 按钮组
    group_frame = tk.Frame(root, bg=C["bg_root"])
    group_frame.pack(pady=S.get("lg"))
    
    group_label = tk.Label(
        group_frame,
        text="按钮组:",
        font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
        fg=C["text_secondary"],
        bg=C["bg_root"],
    )
    group_label.pack(pady=(0, S.get("sm")))
    
    button_group = ButtonGroup(group_frame)
    button_group.pack()
    
    button_group.add_button("按钮1", lambda: print("按钮1"))
    button_group.add_button("按钮2", lambda: print("按钮2"))
    button_group.add_button("按钮3", lambda: print("按钮3"))
    
    # 禁用按钮
    disabled_frame = tk.Frame(root, bg=C["bg_root"])
    disabled_frame.pack(pady=S.get("lg"))
    
    disabled_btn = Button(
        disabled_frame,
        text="禁用按钮",
        disabled=True,
    )
    disabled_btn.pack(side=tk.LEFT, padx=S.get("md"))
    
    # 启用按钮
    enabled_btn = Button(
        disabled_frame,
        text="启用按钮",
        on_click=lambda: disabled_btn.set_disabled(not disabled_btn.disabled),
    )
    enabled_btn.pack(side=tk.LEFT, padx=S.get("md"))
    
    root.mainloop()