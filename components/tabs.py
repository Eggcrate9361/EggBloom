#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
唐页组件 - 现代化多标签页容器
"""

import tkinter as tk
from typing import Optional, Callable, Tuple
from design_system import C, S, T, I


class TabContainer(tk.Frame):
    """现代化标签页容器"""
    
    def __init__(
        self,
        parent,
        closable: bool = True,
        reorderable: bool = False,
        on_tab_change: Optional[Callable[[int, str], None]] = None,
        on_tab_close: Optional[Callable[[int, str], None]] = None,
        **kwargs
    ):
        # 基础配置
        bg = kwargs.pop("bg", C["bg_root"])
        super().__init__(parent, bg=bg, **kwargs)
        
        # 属性
        self.closable = closable
        self.reorderable = reorderable
        self.on_tab_change = on_tab_change
        self.on_tab_close = on_tab_close
        
        # 标签页数据
        self.tabs = []  # [(id, text, icon, frame)]
        self.active_index = -1
        
        # 子组件
        self.tab_bar = None
        self.tab_content = None
        self.tab_buttons = []  # 标签页按钮
        
        # 创建布局
        self._create_layout()
    
    def _create_layout(self):
        """创建布局"""
        # 标签栏
        self.tab_bar = TabBar(
            self,
            bg=C["bg_root"],
            on_tab_click=self._on_tab_click,
            on_tab_close=self._on_tab_close_button,
        )
        self.tab_bar.pack(fill=tk.X)
        
        # 内容区域
        self.tab_content = tk.Frame(
            self,
            bg=C["bg_root"],
        )
        self.tab_content.pack(fill=tk.BOTH, expand=True)
    
    def add_tab(
        self,
        text: str,
        frame: Optional[tk.Frame] = None,
        icon: str = "",
        closable: Optional[bool] = None,
    ) -> Tuple[int, str]:
        """添加标签页"""
        tab_id = f"tab_{len(self.tabs)}"
        
        # 创建内容框架（如果未提供）
        if frame is None:
            frame = tk.Frame(self.tab_content, bg=C["bg_root"])
        
        # 添加到列表
        self.tabs.append((tab_id, text, icon, frame))
        
        # 添加标签页按钮
        can_close = closable if closable is not None else self.closable
        self.tab_bar.add_tab(text, icon=icon, closable=can_close, tab_id=tab_id)
        
        # 如果是第一个标签页，激活它
        if len(self.tabs) == 1:
            self.set_active_tab(0)
        
        return (len(self.tabs) - 1, tab_id)
    
    def remove_tab(self, index: int):
        """移除标签页"""
        if 0 <= index < len(self.tabs):
            tab_id, text, icon, frame = self.tabs[index]
            
            # 销毁内容框架
            if frame and frame.winfo_exists():
                frame.destroy()
            
            # 移除标签页按钮
            self.tab_bar.remove_tab(index)
            
            # 从列表中移除
            del self.tabs[index]
            
            # 如果被移除的是当前激活的标签页，切换到相邻标签页
            if index == self.active_index:
                if len(self.tabs) > 0:
                    new_index = min(index, len(self.tabs) - 1)
                    self.set_active_tab(new_index)
                else:
                    self.active_index = -1
    
    def set_active_tab(self, index: int):
        """设置激活的标签页"""
        if 0 <= index < len(self.tabs) and index != self.active_index:
            # 隐藏当前激活的内容框架
            if self.active_index >= 0 and self.active_index < len(self.tabs):
                old_frame = self.tabs[self.active_index][3]
                if old_frame and old_frame.winfo_exists():
                    old_frame.pack_forget()
            
            # 显示新的内容框架
            self.active_index = index
            new_frame = self.tabs[index][3]
            if new_frame and new_frame.winfo_exists():
                new_frame.pack(fill=tk.BOTH, expand=True)
            
            # 更新标签栏状态
            self.tab_bar.set_active_tab(index)
            
            # 触发回调
            if self.on_tab_change:
                tab_id = self.tabs[index][0]
                tab_text = self.tabs[index][1]
                self.on_tab_change(index, tab_text)
    
    def _on_tab_click(self, index: int):
        """标签页点击事件"""
        self.set_active_tab(index)
    
    def _on_tab_close_button(self, index: int):
        """标签页关闭按钮事件"""
        if 0 <= index < len(self.tabs):
            tab_id = self.tabs[index][0]
            tab_text = self.tabs[index][1]
            
            # 触发关闭回调
            if self.on_tab_close:
                self.on_tab_close(index, tab_text)
            
            # 移除标签页
            self.remove_tab(index)
    
    def get_active_tab(self) -> Optional[Tuple[int, str, tk.Frame]]:
        """获取当前激活的标签页"""
        if 0 <= self.active_index < len(self.tabs):
            return (
                self.active_index,
                self.tabs[self.active_index][1],
                self.tabs[self.active_index][3],
            )
        return None
    
    def get_tab_frame(self, index: int) -> Optional[tk.Frame]:
        """获取标签页的内容框架"""
        if 0 <= index < len(self.tabs):
            return self.tabs[index][3]
        return None
    
    def get_tab_count(self) -> int:
        """获取标签页数量"""
        return len(self.tabs)
    
    def set_tab_text(self, index: int, text: str):
        """设置标签页文本"""
        if 0 <= index < len(self.tabs):
            self.tabs[index] = (self.tabs[index][0], text, self.tabs[index][2], self.tabs[index][3])
            self.tab_bar.set_tab_text(index, text)
    
    def set_tab_icon(self, index: int, icon: str):
        """设置标签页图标"""
        if 0 <= index < len(self.tabs):
            self.tabs[index] = (self.tabs[index][0], self.tabs[index][1], icon, self.tabs[index][3])
            self.tab_bar.set_tab_icon(index, icon)


class TabBar(tk.Frame):
    """标签栏"""
    
    def __init__(
        self,
        parent,
        on_tab_click: Optional[Callable[[int], None]] = None,
        on_tab_close: Optional[Callable[[int], None]] = None,
        **kwargs
    ):
        bg = kwargs.pop("bg", C["bg_root"])
        height = kwargs.pop("height", 40)
        super().__init__(parent, bg=bg, height=height, **kwargs)
        
        self.pack_propagate(False)
        
        # 属性
        self.on_tab_click = on_tab_click
        self.on_tab_close = on_tab_close
        
        # 标签数据
        self.tabs = []  # [(text, icon, closable, tab_id)]
        self.active_index = -1
        
        # 子组件
        self.tab_container = None
        self.tab_buttons = []  # 标签按钮
        
        # 创建布局
        self._create_layout()
    
    def _create_layout(self):
        """创建布局"""
        # 标签按钮容器
        self.tab_container = tk.Frame(self, bg=self["bg"])
        self.tab_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # 右侧空白区域（用于填充）
        filler = tk.Frame(self, bg=self["bg"])
        filler.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    
    def add_tab(self, text: str, icon: str = "", closable: bool = True, tab_id: str = ""):
        """添加标签页"""
        # 创建标签页按钮
        tab_btn = TabButton(
            self.tab_container,
            text=text,
            icon=icon,
            closable=closable,
            index=len(self.tabs),
            on_click=lambda i=len(self.tabs): self._on_tab_click(i),
            on_close=lambda i=len(self.tabs): self._on_tab_close(i),
        )
        tab_btn.pack(side=tk.LEFT)
        
        # 添加到列表
        self.tabs.append((text, icon, closable, tab_id))
        self.tab_buttons.append(tab_btn)
    
    def remove_tab(self, index: int):
        """移除标签页"""
        if 0 <= index < len(self.tabs):
            # 销毁按钮
            if index < len(self.tab_buttons):
                self.tab_buttons[index].destroy()
                del self.tab_buttons[index]
            
            del self.tabs[index]
            
            # 更新所有按钮的索引
            for i, btn in enumerate(self.tab_buttons):
                btn.index = i
                btn.on_click = lambda idx=i: self._on_tab_click(idx)
                btn.on_close = lambda idx=i: self._on_tab_close(idx)
    
    def set_active_tab(self, index: int):
        """设置激活的标签页"""
        if 0 <= index < len(self.tab_buttons):
            # 取消上一个激活状态
            if self.active_index >= 0 and self.active_index < len(self.tab_buttons):
                self.tab_buttons[self.active_index].set_active(False)
            
            # 设置新的激活状态
            self.active_index = index
            self.tab_buttons[index].set_active(True)
    
    def _on_tab_click(self, index: int):
        """标签页点击事件"""
        if self.on_tab_click:
            self.on_tab_click(index)
    
    def _on_tab_close(self, index: int):
        """标签页关闭事件"""
        if self.on_tab_close:
            self.on_tab_close(index)
    
    def set_tab_text(self, index: int, text: str):
        """设置标签页文本"""
        if 0 <= index < len(self.tab_buttons):
            self.tab_buttons[index].set_text(text)
    
    def set_tab_icon(self, index: int, icon: str):
        """设置标签页图标"""
        if 0 <= index < len(self.tab_buttons):
            self.tab_buttons[index].set_icon(icon)


class TabButton(tk.Frame):
    """标签按钮"""
    
    def __init__(
        self,
        parent,
        text: str,
        icon: str = "",
        closable: bool = True,
        index: int = 0,
        on_click: Optional[Callable] = None,
        on_close: Optional[Callable] = None,
        **kwargs
    ):
        bg = kwargs.pop("bg", C["bg_root"])
        super().__init__(parent, bg=bg, **kwargs)
        
        # 属性
        self.text = text
        self.icon = icon
        self.closable = closable
        self.index = index
        self.on_click = on_click
        self.on_close = on_close
        
        # 状态
        self.active = False
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
        
        # 图标
        if self.icon:
            self.icon_label = tk.Label(
                self,
                text=I.get(self.icon),
                font=(T.get_font_family(), T.get_font_config("body_small")["size"]),
                bg=self["bg"],
            )
            self.icon_label.pack(side=tk.LEFT, padx=(0, S.get("xs")))
        
        # 文本
        self.text_label = tk.Label(
            self,
            text=self.text,
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            bg=self["bg"],
        )
        self.text_label.pack(side=tk.LEFT)
        
        # 关闭按钮
        if self.closable:
            self.close_btn = tk.Label(
                self,
                text="×",
                font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
                bg=self["bg"],
                cursor="hand2",
                padx=S.get("xs"),
            )
            self.close_btn.pack(side=tk.LEFT, padx=(S.get("xs"), 0))
    
    def _bind_events(self):
        """绑定事件"""
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click_event)
        
        self.text_label.bind("<Enter>", self._on_enter)
        self.text_label.bind("<Leave>", self._on_leave)
        self.text_label.bind("<Button-1>", self._on_click_event)
        
        if hasattr(self, 'close_btn'):
            self.close_btn.bind("<Enter>", self._on_close_enter)
            self.close_btn.bind("<Leave>", self._on_close_leave)
            self.close_btn.bind("<Button-1>", self._on_close_event)
    
    def _on_enter(self, event):
        """鼠标进入"""
        self._is_hovered = True
        self._update_appearance()
    
    def _on_leave(self, event):
        """鼠标离开"""
        self._is_hovered = False
        self._update_appearance()
    
    def _on_close_enter(self, event):
        """关闭按钮鼠标进入"""
        self.close_btn.configure(fg=C["accent_danger"])
    
    def _on_close_leave(self, event):
        """关闭按钮鼠标离开"""
        self.close_btn.configure(fg=C["text_tertiary"])
    
    def _on_click_event(self, event):
        """点击事件"""
        if self.on_click:
            self.on_click()
    
    def _on_close_event(self, event):
        """关闭事件"""
        if self.on_close:
            self.on_close()
    
    def _update_appearance(self):
        """更新外观"""
        if self.active:
            bg = C["bg_elevated"]
            fg = C["text_primary"]
            border_bottom_color = C["accent_primary"]
        elif self._is_hovered:
            bg = C["hover_bg"]
            fg = C["text_primary"]
            border_bottom_color = C["border_light"]
        else:
            bg = C["bg_root"]
            fg = C["text_secondary"]
            border_bottom_color = C["bg_root"]
        
        # 应用样式
        self.configure(bg=bg)
        self.text_label.configure(bg=bg, fg=fg)
        
        if hasattr(self, 'icon_label'):
            self.icon_label.configure(bg=bg)
        
        if hasattr(self, 'close_btn'):
            self.close_btn.configure(bg=bg, fg=C["text_tertiary"])
        
        # 底部边框
        if self.active:
            self.configure(highlightbackground=border_bottom_color, highlightthickness=2)
        else:
            self.configure(highlightbackground=self["bg"], highlightthickness=2)
    
    def set_active(self, active: bool):
        """设置激活状态"""
        self.active = active
        self._update_appearance()
    
    def set_text(self, text: str):
        """设置文本"""
        self.text = text
        self.text_label.configure(text=text)
    
    def set_icon(self, icon: str):
        """设置图标"""
        self.icon = icon
        if hasattr(self, 'icon_label'):
            self.icon_label.configure(text=I.get(icon))


if __name__ == "__main__":
    # 测试标签页组件
    root = tk.Tk()
    root.title("Tab Container Test")
    root.geometry("800x500")
    root.configure(bg=C["bg_root"])
    
    # 创建标签页容器
    def on_tab_change(index, text):
        print(f"切换到标签页 {index}: {text}")
    
    def on_tab_close(index, text):
        print(f"关闭标签页 {index}: {text}")
        return True  # 允许关闭
    
    tab_container = TabContainer(
        root,
        closable=True,
        on_tab_change=on_tab_change,
        on_tab_close=on_tab_close,
    )
    tab_container.pack(fill=tk.BOTH, expand=True, padx=S.get("lg"), pady=S.get("lg"))
    
    # 添加测试标签页
    for i, (text, icon) in enumerate([
        ("仪表盘", "dashboard"),
        ("消息", "message"),
        ("设置", "settings"),
        ("日志", "logs"),
        ("插件", "plugins"),
    ]):
        # 创建内容框架
        frame = tk.Frame(tab_container.tab_content, bg=C["bg_card"])
        
        # 添加一些内容
        content_label = tk.Label(
            frame,
            text=f"{text}页面",
            font=(T.get_font_family(), T.get_font_config("heading_large")["size"], "bold"),
            fg=C["text_primary"],
            bg=C["bg_card"],
        )
        content_label.pack(padx=S.get("xl"), pady=S.get("xl"))
        
        # 添加标签页
        tab_container.add_tab(
            text=text,
            frame=frame,
            icon=icon,
            closable=True,
        )
    
    # 控制按钮
    control_frame = tk.Frame(root, bg=C["bg_root"])
    control_frame.pack(fill=tk.X, padx=S.get("lg"), pady=(0, S.get("lg")))
    
    def add_new_tab():
        import random
        texts = ["分析", "报告", "工具", "帮助", "数据"]
        text = random.choice(texts)
        
        frame = tk.Frame(tab_container.tab_content, bg=C["bg_card"])
        
        content = tk.Label(
            frame,
            text=f"{text}页面",
            font=(T.get_font_family(), T.get_font_config("heading_large")["size"], "bold"),
            fg=C["text_primary"],
            bg=C["bg_card"],
        )
        content.pack(padx=S.get("xl"), pady=S.get("xl"))
        
        index, tab_id = tab_container.add_tab(text, frame=frame, closable=True)
        tab_container.set_active_tab(index)
    
    add_btn = tk.Label(
        control_frame,
        text="+ 新建标签",
        font=(T.get_font_family(), T.get_font_config("button_medium")["size"]),
        fg=C["accent_primary"],
        bg=C["bg_elevated"],
        cursor="hand2",
        padx=S.get("md"),
        pady=S.get("sm"),
        relief=tk.FLAT,
    )
    add_btn.pack(side=tk.LEFT)
    add_btn.bind("<Button-1>", lambda e: add_new_tab())
    
    root.mainloop()