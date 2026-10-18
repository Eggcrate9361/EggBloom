#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
导航栏组件 - 现代化顶部导航栏
"""

import tkinter as tk
from typing import Optional, Callable
from design_system import C, S, T, I

class Navbar(tk.Frame):
    """现代化导航栏"""
    
    def __init__(
        self,
        parent,
        title: str = "EggBloom",
        subtitle: str = "v3",
        logo: str = "🤖",
        show_search: bool = True,
        show_user_menu: bool = True,
        show_notifications: bool = True,
        on_search: Optional[Callable[[str], None]] = None,
        on_notification_click: Optional[Callable] = None,
        on_user_menu_click: Optional[Callable] = None,
        **kwargs
    ):
        # 基础配置
        bg = kwargs.pop("bg", C["bg_root"])
        height = kwargs.pop("height", 56)
        super().__init__(parent, bg=bg, height=height, **kwargs)
        
        # 禁止自动调整大小
        self.pack_propagate(False)
        
        # 属性
        self.title = title
        self.subtitle = subtitle
        self.logo = logo
        self.show_search = show_search
        self.show_user_menu = show_user_menu
        self.show_notifications = show_notifications
        self.on_search = on_search
        self.on_notification_click = on_notification_click
        self.on_user_menu_click = on_user_menu_click
        
        # 状态
        self.notification_count = 0
        self.user_avatar = None
        self.user_name = "用户"
        
        # 子组件
        self.left_frame = None
        self.center_frame = None
        self.right_frame = None
        self.search_input = None
        self.notification_badge = None
        self.user_menu_button = None
        
        # 创建布局
        self._create_layout()
    
    def _create_layout(self):
        """创建布局"""
        # 内边距
        self.configure(padx=S.get("xl"), pady=S.get("md"))
        
        # 左侧：Logo + 标题
        self.left_frame = tk.Frame(self, bg=self["bg"])
        self.left_frame.pack(side=tk.LEFT, fill=tk.Y)
        
        self._create_logo_section()
        
        # 中间：搜索框 (可选)
        if self.show_search:
            self.center_frame = tk.Frame(self, bg=self["bg"])
            self.center_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=S.get("xl"))
            
            self._create_search_section()
        
        # 右侧：通知 + 用户菜单
        self.right_frame = tk.Frame(self, bg=self["bg"])
        self.right_frame.pack(side=tk.RIGHT, fill=tk.Y)
        
        if self.show_notifications:
            self._create_notification_section()
        
        if self.show_user_menu:
            self._create_user_menu_section()
    
    def _create_logo_section(self):
        """创建Logo区域"""
        # Logo容器
        logo_container = tk.Frame(self.left_frame, bg=self["bg"])
        logo_container.pack(side=tk.LEFT, fill=tk.Y)
        
        # Logo图标
        logo_label = tk.Label(
            logo_container,
            text=self.logo,
            font=(T.get_font_family(), 24),
            fg=C["accent_primary"],
            bg=self["bg"],
            cursor="hand2",
        )
        logo_label.pack(side=tk.LEFT)
        logo_label.bind("<Button-1>", lambda e: self._on_logo_click())
        
        # 标题
        title_frame = tk.Frame(logo_container, bg=self["bg"])
        title_frame.pack(side=tk.LEFT, padx=(S.get("sm"), 0))
        
        title_label = tk.Label(
            title_frame,
            text=self.title,
            font=(
                T.get_font_family(),
                T.get_font_config("heading_medium")["size"],
                "bold",
            ),
            fg=C["text_primary"],
            bg=self["bg"],
            cursor="hand2",
        )
        title_label.pack(anchor=tk.W)
        title_label.bind("<Button-1>", lambda e: self._on_logo_click())
        
        # 副标题
        if self.subtitle:
            subtitle_label = tk.Label(
                title_frame,
                text=self.subtitle,
                font=(
                    T.get_font_family(),
                    T.get_font_config("caption")["size"],
                ),
                fg=C["text_tertiary"],
                bg=self["bg"],
                cursor="hand2",
            )
            subtitle_label.pack(anchor=tk.W)
            subtitle_label.bind("<Button-1>", lambda e: self._on_logo_click())
    
    def _create_search_section(self):
        """创建搜索区域"""
        # 搜索容器
        search_container = tk.Frame(self.center_frame, bg=self["bg"])
        search_container.pack(fill=tk.X, expand=True)
        
        # 搜索框
        search_bg = C["bg_input"]
        search_frame = tk.Frame(search_container, bg=search_bg, relief=tk.FLAT)
        search_frame.pack(fill=tk.X)
        
        # 搜索图标
        search_icon = tk.Label(
            search_frame,
            text=I.get("search"),
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            fg=C["text_tertiary"],
            bg=search_bg,
            padx=S.get("sm"),
        )
        search_icon.pack(side=tk.LEFT)
        
        # 搜索输入框
        self.search_input = tk.Entry(
            search_frame,
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            bg=search_bg,
            fg=C["text_primary"],
            insertbackground=C["accent_primary"],
            relief=tk.FLAT,
            bd=0,
        )
        self.search_input.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, S.get("sm")))
        self.search_input.insert(0, "搜索...")
        
        # 绑定事件
        self.search_input.bind("<FocusIn>", self._on_search_focus_in)
        self.search_input.bind("<FocusOut>", self._on_search_focus_out)
        self.search_input.bind("<Return>", self._on_search_enter)
        
        # 搜索按钮
        search_button = tk.Label(
            search_frame,
            text=I.get("search"),
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            fg=C["text_tertiary"],
            bg=search_bg,
            cursor="hand2",
            padx=S.get("sm"),
        )
        search_button.pack(side=tk.RIGHT)
        search_button.bind("<Button-1>", self._on_search_click)
        
        # 初始样式
        self._update_search_style()
    
    def _create_notification_section(self):
        """创建通知区域"""
        # 通知容器
        notification_container = tk.Frame(self.right_frame, bg=self["bg"])
        notification_container.pack(side=tk.LEFT, padx=(0, S.get("lg")))
        
        # 通知按钮
        notification_button = tk.Label(
            notification_container,
            text=I.get("notification"),
            font=(T.get_font_family(), T.get_font_config("body_large")["size"]),
            fg=C["text_secondary"],
            bg=self["bg"],
            cursor="hand2",
            padx=S.get("sm"),
        )
        notification_button.pack()
        notification_button.bind("<Button-1>", self._on_notification_click)
        
        # 通知徽章
        if self.notification_count > 0:
            self._update_notification_badge()
    
    def _create_user_menu_section(self):
        """创建用户菜单区域"""
        # 用户菜单容器
        user_container = tk.Frame(self.right_frame, bg=self["bg"])
        user_container.pack(side=tk.LEFT)
        
        # 用户头像
        if self.user_avatar:
            avatar_label = tk.Label(
                user_container,
                text=self.user_avatar,
                font=(T.get_font_family(), T.get_font_config("body_large")["size"]),
                fg=C["text_primary"],
                bg=self["bg"],
                cursor="hand2",
                padx=(0, S.get("sm")),
            )
            avatar_label.pack(side=tk.LEFT)
            avatar_label.bind("<Button-1>", self._on_user_menu_click)
        else:
            # 默认头像
            avatar_circle = tk.Canvas(
                user_container,
                width=32,
                height=32,
                bg=self["bg"],
                highlightthickness=0,
            )
            avatar_circle.pack(side=tk.LEFT, padx=(0, S.get("sm")))
            avatar_circle.create_oval(2, 2, 30, 30, fill=C["accent_primary"], outline="")
            
            # 用户首字母
            initial = self.user_name[0].upper() if self.user_name else "U"
            avatar_circle.create_text(
                16, 16,
                text=initial,
                font=(T.get_font_family(), 12, "bold"),
                fill=C["text_primary"],
            )
            avatar_circle.bind("<Button-1>", self._on_user_menu_click)
            avatar_circle.configure(cursor="hand2")
        
        # 用户名
        user_name_label = tk.Label(
            user_container,
            text=self.user_name,
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            fg=C["text_primary"],
            bg=self["bg"],
            cursor="hand2",
        )
        user_name_label.pack(side=tk.LEFT)
        user_name_label.bind("<Button-1>", self._on_user_menu_click)
        
        # 下拉箭头
        arrow_label = tk.Label(
            user_container,
            text="▾",
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            fg=C["text_tertiary"],
            bg=self["bg"],
            cursor="hand2",
            padx=(S.get("sm"), 0),
        )
        arrow_label.pack(side=tk.LEFT)
        arrow_label.bind("<Button-1>", self._on_user_menu_click)
        
        self.user_menu_button = user_container
    
    def _on_logo_click(self):
        """Logo点击事件"""
        print("Logo被点击")
        # 这里可以触发回到主页等操作
    
    def _on_search_focus_in(self, event):
        """搜索框获得焦点"""
        if self.search_input.get() == "搜索...":
            self.search_input.delete(0, tk.END)
            self.search_input.configure(fg=C["text_primary"])
        
        self._update_search_style(focused=True)
    
    def _on_search_focus_out(self, event):
        """搜索框失去焦点"""
        if not self.search_input.get():
            self.search_input.insert(0, "搜索...")
            self.search_input.configure(fg=C["text_tertiary"])
        
        self._update_search_style(focused=False)
    
    def _on_search_enter(self, event):
        """搜索框回车事件"""
        query = self.search_input.get()
        if query and query != "搜索..." and self.on_search:
            self.on_search(query)
    
    def _on_search_click(self, event):
        """搜索按钮点击事件"""
        query = self.search_input.get()
        if query and query != "搜索..." and self.on_search:
            self.on_search(query)
        else:
            self.search_input.focus()
    
    def _on_notification_click(self, event):
        """通知按钮点击事件"""
        if self.on_notification_click:
            self.on_notification_click()
        else:
            print(f"通知被点击，有{self.notification_count}条未读")
    
    def _on_user_menu_click(self, event):
        """用户菜单点击事件"""
        if self.on_user_menu_click:
            self.on_user_menu_click()
        else:
            print("用户菜单被点击")
    
    def _update_search_style(self, focused: bool = False):
        """更新搜索框样式"""
        if not self.search_input:
            return
        
        search_bg = C["bg_elevated"] if focused else C["bg_input"]
        border_color = C["accent_primary"] if focused else C["border_light"]
        
        # 更新搜索框父容器
        search_frame = self.search_input.master
        search_frame.configure(bg=search_bg)
        
        # 更新所有子组件
        for child in search_frame.winfo_children():
            if isinstance(child, (tk.Label, tk.Entry)):
                child.configure(bg=search_bg)
        
        # 更新边框
        if focused:
            search_frame.configure(highlightbackground=border_color, highlightthickness=1)
        else:
            search_frame.configure(highlightbackground=border_color, highlightthickness=1)
    
    def _update_notification_badge(self):
        """更新通知徽章"""
        if not self.show_notifications or self.notification_count <= 0:
            return
        
        # 查找通知按钮容器
        notification_container = None
        for child in self.right_frame.winfo_children():
            if isinstance(child, tk.Frame) and child != self.user_menu_button:
                notification_container = child
                break
        
        if not notification_container:
            return
        
        # 创建或更新徽章
        if hasattr(self, 'notification_badge') and self.notification_badge:
            self.notification_badge.destroy()
        
        # 创建徽章
        badge_bg = C["accent_danger"]
        badge_fg = C["text_primary"]
        
        self.notification_badge = tk.Canvas(
            notification_container,
            width=18,
            height=18,
            bg=self["bg"],
            highlightthickness=0,
        )
        
        # 绘制圆形背景
        self.notification_badge.create_oval(1, 1, 17, 17, fill=badge_bg, outline="")
        
        # 绘制数字
        count_text = str(self.notification_count) if self.notification_count <= 99 else "99+"
        self.notification_badge.create_text(
            9, 9,
            text=count_text,
            font=(T.get_font_family(), 9, "bold"),
            fill=badge_fg,
        )
        
        # 定位到通知按钮右上角
        self.notification_badge.place(x=20, y=5)
    
    def set_title(self, title: str, subtitle: str = ""):
        """设置标题"""
        self.title = title
        self.subtitle = subtitle
        
        # 更新标题显示
        if self.left_frame:
            for child in self.left_frame.winfo_children():
                child.destroy()
            self._create_logo_section()
    
    def set_logo(self, logo: str):
        """设置Logo"""
        self.logo = logo
        
        # 更新Logo显示
        if self.left_frame:
            for child in self.left_frame.winfo_children():
                child.destroy()
            self._create_logo_section()
    
    def set_notification_count(self, count: int):
        """设置通知数量"""
        self.notification_count = max(0, count)
        self._update_notification_badge()
    
    def set_user_info(self, name: str, avatar: str = None):
        """设置用户信息"""
        self.user_name = name
        self.user_avatar = avatar
        
        # 更新用户菜单显示
        if self.user_menu_button:
            self.user_menu_button.destroy()
            self._create_user_menu_section()
    
    def set_search_callback(self, callback: Callable[[str], None]):
        """设置搜索回调"""
        self.on_search = callback
    
    def set_notification_callback(self, callback: Callable):
        """设置通知回调"""
        self.on_notification_click = callback
    
    def set_user_menu_callback(self, callback: Callable):
        """设置用户菜单回调"""
        self.on_user_menu_click = callback
    
    def get_search_query(self) -> str:
        """获取搜索查询"""
        if self.search_input:
            query = self.search_input.get()
            return query if query != "搜索..." else ""
        return ""
    
    def clear_search(self):
        """清空搜索框"""
        if self.search_input:
            self.search_input.delete(0, tk.END)
            self.search_input.insert(0, "搜索...")
            self.search_input.configure(fg=C["text_tertiary"])
            self._update_search_style(focused=False)


class NavItem(tk.Frame):
    """导航项 - 用于侧边栏导航"""
    
    def __init__(
        self,
        parent,
        text: str,
        icon: str = "",
        active: bool = False,
        badge: Optional[str] = None,
        on_click: Optional[Callable] = None,
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
        self.configure(padx=S.get("lg"), pady=S.get("sm"))
        
        # 左侧图标
        if self.icon:
            self.icon_label = tk.Label(
                self,
                text=I.get(self.icon),
                font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
                bg=self["bg"],
            )
            self.icon_label.pack(side=tk.LEFT, padx=(0, S.get("sm")))
        else:
            self.icon_label = None
        
        # 文本
        self.text_label = tk.Label(
            self,
            text=self.text,
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            bg=self["bg"],
            anchor=tk.W,
        )
        self.text_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
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
        else:
            self.badge_label = None
    
    def _bind_events(self):
        """绑定事件"""
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        
        for child in self.winfo_children():
            child.bind("<Enter>", self._on_enter)
            child.bind("<Leave>", self._on_leave)
            child.bind("<Button-1>", self._on_click)
    
    def _on_enter(self, event):
        """鼠标进入"""
        self._is_hovered = True
        self._update_appearance()
    
    def _on_leave(self, event):
        """鼠标离开"""
        self._is_hovered = False
        self._update_appearance()
    
    def _on_click(self, event):
        """点击事件"""
        if self.on_click:
            self.on_click()
    
    def _update_appearance(self):
        """更新外观"""
        if self.active:
            bg = C["selected_bg"]
            fg = C["accent_primary"]
            cursor = "hand2"
        elif self._is_hovered:
            bg = C["hover_bg"]
            fg = C["text_primary"]
            cursor = "hand2"
        else:
            bg = self.master["bg"] if self.master else C["bg_sidebar"]
            fg = C["text_secondary"]
            cursor = ""
        
        # 应用样式
        self.configure(bg=bg)
        self.text_label.configure(bg=bg, fg=fg, cursor=cursor)
        
        if self.icon_label:
            self.icon_label.configure(bg=bg, fg=fg, cursor=cursor)
        
        if self.badge_label:
            self.badge_label.configure(bg=C["chip_bg"], fg=C["chip_fg"])
    
    def set_active(self, active: bool):
        """设置激活状态"""
        self.active = active
        self._update_appearance()
    
    def set_badge(self, badge: Optional[str]):
        """设置徽章"""
        self.badge = badge
        
        if self.badge_label:
            if badge:
                self.badge_label.configure(text=badge)
                self.badge_label.pack(side=tk.RIGHT)
            else:
                self.badge_label.pack_forget()
        elif badge:
            self._create_content()
    
    def set_on_click(self, callback: Callable):
        """设置点击回调"""
        self.on_click = callback


if __name__ == "__main__":
    # 测试导航栏组件
    root = tk.Tk()
    root.title("Navbar Component Test")
    root.geometry("1000x200")
    root.configure(bg=C["bg_root"])
    
    # 创建导航栏
    def on_search(query):
        print(f"搜索: {query}")
    
    def on_notification_click():
        print("通知被点击")
    
    def on_user_menu_click():
        print("用户菜单被点击")
    
    navbar = Navbar(
        root,
        title="EggBloom",
        subtitle="v3.0",
        logo="🤖",
        show_search=True,
        show_notifications=True,
        show_user_menu=True,
        on_search=on_search,
        on_notification_click=on_notification_click,
        on_user_menu_click=on_user_menu_click,
    )
    navbar.pack(fill=tk.X)
    
    # 设置通知数量
    navbar.set_notification_count(5)
    
    # 设置用户信息
    navbar.set_user_info("管理员", "👤")
    
    # 测试导航项
    nav_frame = tk.Frame(root, bg=C["bg_root"])
    nav_frame.pack(pady=S.get("lg"))
    
    def nav_click(text):
        def callback():
            print(f"{text}被点击")
        return callback
    
    items = [
        ("仪表盘", "dashboard", True, "3"),
        ("消息", "message", False, None),
        ("设置", "settings", False, "new"),
        ("日志", "logs", False, None),
        ("插件", "plugins", False, "5"),
    ]
    
    for text, icon, active, badge in items:
        item = NavItem(
            nav_frame,
            text=text,
            icon=icon,
            active=active,
            badge=badge,
            on_click=nav_click(text),
        )
        item.pack(fill=tk.X, pady=2)
    
    root.mainloop()