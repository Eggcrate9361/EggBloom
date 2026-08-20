#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
侧边栏组件 - 现代化侧边导航栏
"""

import tkinter as tk
from typing import Optional, Callable, Any
from design_system import C, S, T, I
from animator import Animator

class Sidebar(tk.Frame):
    """现代化侧边栏"""
    
    def __init__(
        self,
        parent,
        width: int = 240,
        collapsed: bool = False,
        show_logo: bool = True,
        show_user_info: bool = True,
        show_footer: bool = True,
        on_toggle: Optional[Callable[[bool], None]] = None,
        **kwargs
    ):
        # 基础配置
        bg = kwargs.pop("bg", C["bg_sidebar"])
        super().__init__(parent, bg=bg, width=width, **kwargs)
        
        # 禁止自动调整大小
        self.pack_propagate(False)
        
        # 属性
        self.width = width
        self.collapsed = collapsed
        self.show_logo = show_logo
        self.show_user_info = show_user_info
        self.show_footer = show_footer
        self.on_toggle = on_toggle
        
        # 导航项
        self.nav_items = []
        self.active_item = None
        
        # 子组件
        self.logo_frame = None
        self.nav_frame = None
        self.user_frame = None
        self.footer_frame = None
        self.toggle_button = None
        
        # 创建布局
        self._create_layout()
        
        # 应用初始状态
        if self.collapsed:
            self._collapse()
    
    def _create_layout(self):
        """创建布局"""
        # 主容器
        main_container = tk.Frame(self, bg=self["bg"])
        main_container.pack(fill=tk.BOTH, expand=True)
        
        # 顶部：Logo和切换按钮
        if self.show_logo:
            self._create_logo_section(main_container)
        
        # 中间：导航项
        self._create_nav_section(main_container)
        
        # 底部：用户信息和页脚
        bottom_container = tk.Frame(main_container, bg=self["bg"])
        bottom_container.pack(side=tk.BOTTOM, fill=tk.X)
        
        if self.show_user_info:
            self._create_user_section(bottom_container)
        
        if self.show_footer:
            self._create_footer_section(bottom_container)
    
    def _create_logo_section(self, parent):
        """创建Logo区域"""
        self.logo_frame = tk.Frame(parent, bg=self["bg"], height=64)
        self.logo_frame.pack(fill=tk.X, pady=(S.get("md"), S.get("lg")))
        self.logo_frame.pack_propagate(False)
        
        # Logo容器
        logo_container = tk.Frame(self.logo_frame, bg=self["bg"])
        logo_container.pack(fill=tk.BOTH, expand=True, padx=S.get("lg"))
        
        # Logo图标
        logo_icon = tk.Label(
            logo_container,
            text="🤖",
            font=(T.get_font_family(), 24),
            fg=C["accent_primary"],
            bg=self["bg"],
            cursor="hand2",
        )
        logo_icon.pack(side=tk.LEFT)
        
        # Logo文字
        if not self.collapsed:
            logo_text = tk.Label(
                logo_container,
                text="EggBloom",
                font=(
                    T.get_font_family(),
                    T.get_font_config("heading_small")["size"],
                    "bold",
                ),
                fg=C["text_primary"],
                bg=self["bg"],
                cursor="hand2",
            )
            logo_text.pack(side=tk.LEFT, padx=(S.get("sm"), 0))
        
        # 切换按钮
        self.toggle_button = tk.Label(
            logo_container,
            text="◀" if not self.collapsed else "▶",
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            fg=C["text_tertiary"],
            bg=self["bg"],
            cursor="hand2",
        )
        self.toggle_button.pack(side=tk.RIGHT)
        self.toggle_button.bind("<Button-1>", self._on_toggle)
    
    def _create_nav_section(self, parent):
        """创建导航区域"""
        # 导航容器
        self.nav_frame = tk.Frame(parent, bg=self["bg"])
        self.nav_frame.pack(fill=tk.BOTH, expand=True, padx=S.get("lg"))
        
        # 导航项容器
        self.nav_items_container = tk.Frame(self.nav_frame, bg=self["bg"])
        self.nav_items_container.pack(fill=tk.BOTH, expand=True)
    
    def _create_user_section(self, parent):
        """创建用户区域"""
        self.user_frame = tk.Frame(parent, bg=self["bg"], height=80)
        self.user_frame.pack(fill=tk.X, pady=(0, S.get("md")))
        self.user_frame.pack_propagate(False)
        
        # 用户容器
        user_container = tk.Frame(self.user_frame, bg=self["bg"])
        user_container.pack(fill=tk.BOTH, expand=True, padx=S.get("lg"), pady=S.get("md"))
        
        # 用户头像
        avatar_frame = tk.Frame(user_container, bg=self["bg"])
        avatar_frame.pack(side=tk.LEFT)
        
        avatar_canvas = tk.Canvas(
            avatar_frame,
            width=40,
            height=40,
            bg=self["bg"],
            highlightthickness=0,
        )
        avatar_canvas.pack()
        avatar_canvas.create_oval(2, 2, 38, 38, fill=C["accent_primary"], outline="")
        avatar_canvas.create_text(
            20, 20,
            text="A",
            font=(T.get_font_family(), 16, "bold"),
            fill=C["text_primary"],
        )
        
        # 用户信息
        if not self.collapsed:
            info_frame = tk.Frame(user_container, bg=self["bg"])
            info_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(S.get("md"), 0))
            
            # 用户名
            user_name = tk.Label(
                info_frame,
                text="管理员",
                font=(
                    T.get_font_family(),
                    T.get_font_config("body_medium")["size"],
                    "bold",
                ),
                fg=C["text_primary"],
                bg=self["bg"],
                anchor=tk.W,
            )
            user_name.pack(fill=tk.X)
            
            # 用户角色
            user_role = tk.Label(
                info_frame,
                text="系统管理员",
                font=(T.get_font_family(), T.get_font_config("caption")["size"]),
                fg=C["text_tertiary"],
                bg=self["bg"],
                anchor=tk.W,
            )
            user_role.pack(fill=tk.X)
        
        # 设置按钮
        settings_btn = tk.Label(
            user_container,
            text=I.get("settings"),
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            fg=C["text_tertiary"],
            bg=self["bg"],
            cursor="hand2",
        )
        settings_btn.pack(side=tk.RIGHT)
        settings_btn.bind("<Button-1>", lambda e: print("设置被点击"))
    
    def _create_footer_section(self, parent):
        """创建页脚区域"""
        self.footer_frame = tk.Frame(parent, bg=self["bg"], height=40)
        self.footer_frame.pack(fill=tk.X, pady=(0, S.get("md")))
        self.footer_frame.pack_propagate(False)
        
        # 页脚容器
        footer_container = tk.Frame(self.footer_frame, bg=self["bg"])
        footer_container.pack(fill=tk.BOTH, expand=True, padx=S.get("lg"))
        
        if not self.collapsed:
            # 版本信息
            version_label = tk.Label(
                footer_container,
                text="v3.0.0",
                font=(T.get_font_family(), T.get_font_config("caption")["size"]),
                fg=C["text_tertiary"],
                bg=self["bg"],
            )
            version_label.pack(side=tk.LEFT)
            
            # 状态指示器
            status_frame = tk.Frame(footer_container, bg=self["bg"])
            status_frame.pack(side=tk.RIGHT)
            
            status_dot = tk.Canvas(
                status_frame,
                width=8,
                height=8,
                bg=self["bg"],
                highlightthickness=0,
            )
            status_dot.pack(side=tk.LEFT, padx=(0, S.get("xs")))
            status_dot.create_oval(0, 0, 8, 8, fill=C["accent_success"], outline="")
            
            status_text = tk.Label(
                status_frame,
                text="在线",
                font=(T.get_font_family(), T.get_font_config("caption")["size"]),
                fg=C["text_tertiary"],
                bg=self["bg"],
            )
            status_text.pack(side=tk.LEFT)
        else:
            # 折叠状态下的简化页脚
            version_label = tk.Label(
                footer_container,
                text="v3",
                font=(T.get_font_family(), T.get_font_config("caption")["size"]),
                fg=C["text_tertiary"],
                bg=self["bg"],
            )
            version_label.pack()
    
    def _on_toggle(self, event):
        """切换折叠状态"""
        self.collapsed = not self.collapsed
        
        if self.collapsed:
            self._collapse()
        else:
            self._expand()
        
        if self.on_toggle:
            self.on_toggle(self.collapsed)
    
    def _collapse(self):
        """折叠侧边栏"""
        # 更新宽度
        self.configure(width=64)
        
        # 更新切换按钮
        if self.toggle_button:
            self.toggle_button.configure(text="▶")
        
        # 隐藏文字内容
        if self.logo_frame:
            for child in self.logo_frame.winfo_children()[0].winfo_children():
                if isinstance(child, tk.Label) and child["text"] == "EggBloom":
                    child.pack_forget()
        
        # 隐藏导航项文字
        for item in self.nav_items:
            if hasattr(item, 'text_label'):
                item.text_label.pack_forget()
        
        # 隐藏用户信息文字
        if self.user_frame:
            for child in self.user_frame.winfo_children()[0].winfo_children():
                if isinstance(child, tk.Frame) and len(child.winfo_children()) > 1:
                    # 隐藏用户信息框架
                    child.winfo_children()[1].pack_forget()
        
        # 更新页脚
        if self.footer_frame:
            for child in self.footer_frame.winfo_children()[0].winfo_children():
                child.destroy()
            
            # 创建简化页脚
            version_label = tk.Label(
                self.footer_frame.winfo_children()[0],
                text="v3",
                font=(T.get_font_family(), T.get_font_config("caption")["size"]),
                fg=C["text_tertiary"],
                bg=self["bg"],
            )
            version_label.pack()
    
    def _expand(self):
        """展开侧边栏"""
        # 更新宽度
        self.configure(width=self.width)
        
        # 更新切换按钮
        if self.toggle_button:
            self.toggle_button.configure(text="◀")
        
        # 显示文字内容
        if self.logo_frame:
            logo_container = self.logo_frame.winfo_children()[0]
            
            # 查找Logo图标
            logo_icon = None
            for child in logo_container.winfo_children():
                if isinstance(child, tk.Label) and child["text"] == "🤖":
                    logo_icon = child
                    break
            
            if logo_icon:
                # 添加Logo文字
                logo_text = tk.Label(
                    logo_container,
                    text="EggBloom",
                    font=(
                        T.get_font_family(),
                        T.get_font_config("heading_small")["size"],
                        "bold",
                    ),
                    fg=C["text_primary"],
                    bg=self["bg"],
                    cursor="hand2",
                )
                logo_text.pack(side=tk.LEFT, padx=(S.get("sm"), 0), before=self.toggle_button)
        
        # 显示导航项文字
        for item in self.nav_items:
            if hasattr(item, 'text_label'):
                item.text_label.pack(side=tk.LEFT, fill=tk.X, expand=True, before=item.badge_label if hasattr(item, 'badge_label') else None)
        
        # 显示用户信息文字
        if self.user_frame:
            user_container = self.user_frame.winfo_children()[0]
            
            # 查找头像容器
            avatar_frame = None
            for child in user_container.winfo_children():
                if isinstance(child, tk.Frame) and len(child.winfo_children()) > 0:
                    if isinstance(child.winfo_children()[0], tk.Canvas):
                        avatar_frame = child
                        break
            
            if avatar_frame:
                # 创建用户信息框架
                info_frame = tk.Frame(user_container, bg=self["bg"])
                info_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(S.get("md"), 0), before=user_container.winfo_children()[-1])
                
                # 用户名
                user_name = tk.Label(
                    info_frame,
                    text="管理员",
                    font=(
                        T.get_font_family(),
                        T.get_font_config("body_medium")["size"],
                        "bold",
                    ),
                    fg=C["text_primary"],
                    bg=self["bg"],
                    anchor=tk.W,
                )
                user_name.pack(fill=tk.X)
                
                # 用户角色
                user_role = tk.Label(
                    info_frame,
                    text="系统管理员",
                    font=(T.get_font_family(), T.get_font_config("caption")["size"]),
                    fg=C["text_tertiary"],
                    bg=self["bg"],
                    anchor=tk.W,
                )
                user_role.pack(fill=tk.X)
        
        # 更新页脚
        if self.footer_frame:
            for child in self.footer_frame.winfo_children()[0].winfo_children():
                child.destroy()
            
            # 创建完整页脚
            footer_container = self.footer_frame.winfo_children()[0]
            
            # 版本信息
            version_label = tk.Label(
                footer_container,
                text="v3.0.0",
                font=(T.get_font_family(), T.get_font_config("caption")["size"]),
                fg=C["text_tertiary"],
                bg=self["bg"],
            )
            version_label.pack(side=tk.LEFT)
            
            # 状态指示器
            status_frame = tk.Frame(footer_container, bg=self["bg"])
            status_frame.pack(side=tk.RIGHT)
            
            status_dot = tk.Canvas(
                status_frame,
                width=8,
                height=8,
                bg=self["bg"],
                highlightthickness=0,
            )
            status_dot.pack(side=tk.LEFT, padx=(0, S.get("xs")))
            status_dot.create_oval(0, 0, 8, 8, fill=C["accent_success"], outline="")
            
            status_text = tk.Label(
                status_frame,
                text="在线",
                font=(T.get_font_family(), T.get_font_config("caption")["size"]),
                fg=C["text_tertiary"],
                bg=self["bg"],
            )
            status_text.pack(side=tk.LEFT)
    
    def add_nav_item(
        self,
        text: str,
        icon: str,
        active: bool = False,
        badge: Optional[str] = None,
        on_click: Optional[Callable] = None,
        data: Optional[Any] = None,
    ) -> 'NavItem':
        """添加导航项"""
        item = NavItem(
            self.nav_items_container,
            text=text,
            icon=icon,
            active=active,
            badge=badge,
            on_click=lambda i=len(self.nav_items): self._on_nav_item_click(i, on_click),
            data=data,
        )
        item.pack(fill=tk.X, pady=2)
        
        self.nav_items.append(item)
        
        if active:
            self.active_item = len(self.nav_items) - 1
        
        # 如果侧边栏是折叠的，隐藏文字
        if self.collapsed and hasattr(item, 'text_label'):
            item.text_label.pack_forget()
        
        return item
    
    def _on_nav_item_click(self, index: int, original_callback: Optional[Callable]):
        """导航项点击事件"""
        # 更新激活状态
        if self.active_item is not None and 0 <= self.active_item < len(self.nav_items):
            self.nav_items[self.active_item].set_active(False)
        
        self.active_item = index
        self.nav_items[index].set_active(True)
        
        # 调用原始回调
        if original_callback:
            original_callback()
    
    def set_active_item(self, index: int):
        """设置激活的导航项"""
        if 0 <= index < len(self.nav_items):
            if self.active_item is not None and 0 <= self.active_item < len(self.nav_items):
                self.nav_items[self.active_item].set_active(False)
            
            self.active_item = index
            self.nav_items[index].set_active(True)
    
    def get_active_item(self) -> Optional['NavItem']:
        """获取激活的导航项"""
        if self.active_item is not None and 0 <= self.active_item < len(self.nav_items):
            return self.nav_items[self.active_item]
        return None
    
    def clear_nav_items(self):
        """清空导航项"""
        for item in self.nav_items:
            item.destroy()
        
        self.nav_items = []
        self.active_item = None
    
    def set_user_info(self, name: str, role: str = "", avatar: str = None):
        """设置用户信息"""
        # 这里可以扩展以支持自定义头像
        pass
    
    def set_version(self, version: str):
        """设置版本信息"""
        if self.footer_frame:
            for child in self.footer_frame.winfo_children()[0].winfo_children():
                if isinstance(child, tk.Label) and child["text"].startswith("v"):
                    child.configure(text=version)
    
    def set_status(self, status: str, color: str = C["accent_success"]):
        """设置状态"""
        if self.footer_frame and not self.collapsed:
            for child in self.footer_frame.winfo_children()[0].winfo_children():
                if isinstance(child, tk.Frame) and len(child.winfo_children()) == 2:
                    # 更新状态点颜色
                    canvas = child.winfo_children()[0]
                    canvas.delete("all")
                    canvas.create_oval(0, 0, 8, 8, fill=color, outline="")
                    
                    # 更新状态文字
                    label = child.winfo_children()[1]
                    label.configure(text=status)


class NavItem(tk.Frame):
    """导航项"""
    
    def __init__(
        self,
        parent,
        text: str,
        icon: str,
        active: bool = False,
        badge: Optional[str] = None,
        on_click: Optional[Callable] = None,
        data: Optional[Any] = None,
        **kwargs
    ):
        # 基础配置
        bg = kwargs.pop("bg", C["bg_sidebar"])
        super().__init__(parent, bg=bg, **kwargs)
        
        # 属性
        self.text = text
        self.icon = icon
        self.active = active
        self.badge = badge
        self.on_click = on_click
        self.data = data
        
        # 状态
        self._is_hovered = False
        
        # 创建内容
        self._create_content()
        
        # 绑定事件
        self._bind_events()
        
        # 应用初始样式
        self._update_appearance()
    
    def _create_content(self):
        """创建内容"""
        # 内边距
        self.configure(padx=S.get("md"), pady=S.get("sm"))

        # active 指示条（默认隐藏）
        self._indicator = tk.Canvas(
            self,
            width=3,
            height=24,
            bg=self["bg"],
            highlightthickness=0,
            bd=0,
        )
        self._indicator.place(x=0, y=8)

        # 图标
        self.icon_label = tk.Label(
            self,
            text=I.get(self.icon),
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            bg=self["bg"],
            width=2,  # 固定宽度以对齐
        )
        self.icon_label.pack(side=tk.LEFT)

        # 文本
        self.text_label = tk.Label(
            self,
            text=self.text,
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            bg=self["bg"],
            anchor=tk.W,
        )
        self.text_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(S.get("sm"), 0))

        # 徽章
        if self.badge:
            self.badge_label = tk.Label(
                self,
                text=self.badge,
                font=(T.get_font_family(), T.get_font_config("caption")["size"]),
                bg=C["chip_bg"],
                fg=C["chip_fg"],
                padx=S.get("xs"),
                pady=1,
            )
            self.badge_label.pack(side=tk.RIGHT)

    def _bind_events(self):
        """绑定事件"""
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click_event)

        for child in self.winfo_children():
            if child == getattr(self, '_indicator', None):
                continue
            child.bind("<Enter>", self._on_enter)
            child.bind("<Leave>", self._on_leave)
            child.bind("<Button-1>", self._on_click_event)

    def _on_enter(self, event):
        """鼠标进入"""
        self._is_hovered = True
        self._update_appearance()

    def _on_leave(self, event):
        """鼠标离开"""
        self._is_hovered = False
        self._update_appearance()

    def _on_click_event(self, event):
        """点击事件"""
        if self.on_click:
            self.on_click()

    def _update_appearance(self):
        """更新外观"""
        if self.active:
            target_bg = C["selected_bg"]
            fg = C["accent_primary"]
            cursor = "hand2"
            self._animate_indicator(target_height=16, target_y=4, visible=True)
        elif self._is_hovered:
            target_bg = C["hover_bg"]
            fg = C["text_primary"]
            cursor = "hand2"
            self._animate_indicator(target_height=4, target_y=10, visible=False)
        else:
            target_bg = self.master["bg"] if self.master else C["bg_sidebar"]
            fg = C["text_secondary"]
            cursor = ""
            self._animate_indicator(target_height=0, target_y=12, visible=False)

        # 应用样式
        self.configure(bg=target_bg)
        self.icon_label.configure(bg=target_bg, fg=fg, cursor=cursor)
        self.text_label.configure(bg=target_bg, fg=fg, cursor=cursor)

        if hasattr(self, 'badge_label'):
            self.badge_label.configure(bg=C["chip_bg"], fg=C["chip_fg"])

    def _animate_indicator(self, target_height, target_y, visible):
        """平滑指示条动画，走共享 tick"""
        try:
            coords = self._indicator.coords("indicator")
            if coords:
                x1, y1, x2, y2 = coords
                current_height = max(0, y2 - y1)
                current_y = y1
            else:
                current_height = 0 if not visible else 16
                current_y = target_y
        except Exception:
            current_height = 0
            current_y = target_y

        if current_height == target_height and current_y == target_y:
            if visible and target_height > 0:
                self._indicator.delete("all")
                self._indicator.create_rectangle(
                    0, target_y, 3, target_y + target_height,
                    fill=C["accent_primary"], outline="", tags="indicator"
                )
            else:
                self._indicator.delete("all")
            return

        def apply(values):
            h, y = values
            h = max(0, int(h))
            self._indicator.delete("all")
            if visible and h > 0:
                self._indicator.create_rectangle(
                    0, int(y), 3, int(y) + h,
                    fill=C["accent_primary"], outline="", tags="indicator"
                )

        Animator.animate(
            apply_fn=apply,
            from_val=[current_height, current_y],
            to_val=[target_height, target_y],
            duration=140,
            easing="ease_out",
            on_complete=lambda: self._indicator.delete("all") if not visible or target_height == 0 else None,
        )
    
    def set_active(self, active: bool):
        """设置激活状态"""
        self.active = active
        self._update_appearance()
    
    def set_badge(self, badge: Optional[str]):
        """设置徽章"""
        self.badge = badge
        
        if hasattr(self, 'badge_label'):
            if badge:
                self.badge_label.configure(text=badge)
                self.badge_label.pack(side=tk.RIGHT)
            else:
                self.badge_label.pack_forget()
        elif badge:
            # 创建徽章
            self.badge_label = tk.Label(
                self,
                text=badge,
                font=(T.get_font_family(), T.get_font_config("caption")["size"]),
                bg=C["chip_bg"],
                fg=C["chip_fg"],
                padx=S.get("xs"),
                pady=1,
            )
            self.badge_label.pack(side=tk.RIGHT)
    
    def set_on_click(self, callback: Callable):
        """设置点击回调"""
        self.on_click = callback
    
    def get_data(self) -> Any:
        """获取附加数据"""
        return self.data
    
    def set_data(self, data: Any):
        """设置附加数据"""
        self.data = data


if __name__ == "__main__":
    # 测试侧边栏组件
    root = tk.Tk()
    root.title("Sidebar Component Test")
    root.geometry("300x600")
    root.configure(bg=C["bg_root"])
    
    # 创建侧边栏
    def on_toggle(collapsed):
        print(f"侧边栏状态: {'折叠' if collapsed else '展开'}")
    
    def on_dashboard_click():
        print("仪表盘被点击")
    
    def on_messages_click():
        print("消息被点击")
    
    def on_settings_click():
        print("设置被点击")
    
    def on_logs_click():
        print("日志被点击")
    
    def on_plugins_click():
        print("插件被点击")
    
    sidebar = Sidebar(
        root,
        width=240,
        collapsed=False,
        show_logo=True,
        show_user_info=True,
        show_footer=True,
        on_toggle=on_toggle,
    )
    sidebar.pack(side=tk.LEFT, fill=tk.Y)
    
    # 添加导航项
    sidebar.add_nav_item("仪表盘", "dashboard", active=True, on_click=on_dashboard_click)
    sidebar.add_nav_item("消息", "message", badge="3", on_click=on_messages_click)
    sidebar.add_nav_item("设置", "settings", on_click=on_settings_click)
    sidebar.add_nav_item("日志", "logs", badge="new", on_click=on_logs_click)
    sidebar.add_nav_item("插件", "plugins", on_click=on_plugins_click)
    
    # 内容区域
    content_frame = tk.Frame(root, bg=C["bg_root"])
    content_frame.pack(fill=tk.BOTH, expand=True, padx=S.get("lg"), pady=S.get("lg"))
    
    content_label = tk.Label(
        content_frame,
        text="主内容区域\n\n点击侧边栏的导航项或切换按钮进行测试",
        font=(T.get_font_family(), T.get_font_config("body_large")["size"]),
        fg=C["text_secondary"],
        bg=C["bg_root"],
        justify=tk.CENTER,
    )
    content_label.pack(fill=tk.BOTH, expand=True)
    
    # 控制按钮
    control_frame = tk.Frame(content_frame, bg=C["bg_root"])
    control_frame.pack(pady=S.get("lg"))
    
    def toggle_sidebar():
        sidebar.collapsed = not sidebar.collapsed
        if sidebar.collapsed:
            sidebar._collapse()
        else:
            sidebar._expand()
        
        if sidebar.on_toggle:
            sidebar.on_toggle(sidebar.collapsed)
    
    toggle_btn = tk.Label(
        control_frame,
        text="切换侧边栏",
        font=(T.get_font_family(), T.get_font_config("button_medium")["size"]),
        fg=C["accent_primary"],
        bg=C["bg_elevated"],
        cursor="hand2",
        padx=S.get("md"),
        pady=S.get("sm"),
        relief=tk.FLAT,
    )
    toggle_btn.pack()
    toggle_btn.bind("<Button-1>", lambda e: toggle_sidebar())
    
    def set_active():
        sidebar.set_active_item(2)  # 激活第三个项
    
    active_btn = tk.Label(
        control_frame,
        text="激活设置项",
        font=(T.get_font_family(), T.get_font_config("button_medium")["size"]),
        fg=C["accent_primary"],
        bg=C["bg_elevated"],
        cursor="hand2",
        padx=S.get("md"),
        pady=S.get("sm"),
        relief=tk.FLAT,
    )
    active_btn.pack(pady=(S.get("sm"), 0))
    active_btn.bind("<Button-1>", lambda e: set_active())
    
    root.mainloop()