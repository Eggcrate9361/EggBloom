#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
虚拟滚动列表 - 只渲染可见区域的控件
用于大量数据的高效滚动
"""

import tkinter as tk
import customtkinter as ctk
from ctk_theme import SOFT_COLORS


class VirtualScrollList(ctk.CTkFrame):
    """
    虚拟滚动列表
    
    用法:
        vlist = VirtualScrollList(parent, height=400)
        vlist.set_items(["item1", "item2", ...])  # 设置数据
        vlist.refresh()  # 渲染可见项
        
        # 自定义渲染:
        vlist.set_renderer(lambda parent, item: create_widget(parent, item))
    """

    def __init__(self, parent, item_height=60, **kwargs):
        super().__init__(parent, fg_color=SOFT_COLORS["bg_root"], **kwargs)
        
        self._item_height = item_height
        self._items = []
        self._rendered = {}  # index -> widget
        self._renderer = self._default_renderer
        self._visible_start = 0
        self._visible_end = 0
        self._padding = 5  # 额外渲染的控件数
        
        # Canvas + scrollbar
        self._canvas = tk.Canvas(self, bg=SOFT_COLORS["bg_root"],
                                 highlightthickness=0, bd=0)
        self._scrollbar = ctk.CTkScrollbar(self, command=self._canvas.yview,
                                           width=8, button_color=SOFT_COLORS["border_light"],
                                           button_hover_color=SOFT_COLORS["accent"])
        self._canvas.configure(yscrollcommand=self._scrollbar.set)
        
        self._scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # 内部容器
        self._inner = tk.Frame(self._canvas, bg=SOFT_COLORS["bg_root"])
        self._canvas_window = self._canvas.create_window((0, 0), window=self._inner, anchor=tk.NW)
        
        # 绑定事件
        self._inner.bind("<Configure>", self._on_inner_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._canvas.bind("<MouseWheel>", self._on_mousewheel)
        self._canvas.bind("<Button-4>", lambda e: self._canvas.yview_scroll(-3, "units"))
        self._canvas.bind("<Button-5>", lambda e: self._canvas.yview_scroll(3, "units"))
        self._canvas.bind("<Configure>", self._schedule_update)
        self._canvas.bind("<MouseWheel>", self._schedule_update, add="+")
        
        self._update_pending = False

    def set_items(self, items):
        """设置数据"""
        self._items = items
        self.refresh()

    def set_renderer(self, renderer):
        """设置自定义渲染函数: renderer(parent, item) -> widget"""
        self._renderer = renderer

    def refresh(self):
        """刷新可见区域"""
        self._canvas.update_idletasks()
        self._visible_start = 0
        self._visible_end = len(self._items)
        self._update_visible()

    def _default_renderer(self, parent, item):
        """默认渲染"""
        frame = tk.Frame(parent, bg=SOFT_COLORS["bg_card"], padx=12, pady=8)
        tk.Label(frame, text=str(item), font=("Microsoft YaHei UI", 12),
                 fg=SOFT_COLORS["text_primary"], bg=SOFT_COLORS["bg_card"],
                 anchor=tk.W).pack(fill=tk.X)
        return frame

    def _on_inner_configure(self, e):
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, e):
        self._canvas.itemconfig(self._canvas_window, width=e.width)

    def _on_mousewheel(self, e):
        if e.delta:
            self._canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        elif e.num == 4:
            self._canvas.yview_scroll(-3, "units")
        elif e.num == 5:
            self._canvas.yview_scroll(3, "units")
        self._schedule_update()

    def _schedule_update(self, e=None):
        if not self._update_pending:
            self._update_pending = True
            self.after(16, self._update_visible)

    def _update_visible(self):
        """更新可见区域的控件"""
        self._update_pending = False
        
        if not self._items:
            return
            
        total_height = len(self._items) * self._item_height
        self._inner.configure(height=total_height)
        
        # 计算可见区域
        canvas_height = self._canvas.winfo_height()
        scroll_pos = self._canvas.yview()
        
        if canvas_height <= 0:
            return
            
        start = int(scroll_pos[0] * total_height / self._item_height) - self._padding
        end = int(scroll_pos[1] * total_height / self._item_height) + self._padding
        
        start = max(0, start)
        end = min(len(self._items), end)
        
        # 销毁不可见的
        for idx in list(self._rendered.keys()):
            if idx < start or idx >= end:
                try:
                    self._rendered[idx].destroy()
                except Exception:
                    pass
                del self._rendered[idx]
        
        # 创建可见的
        for idx in range(start, end):
            if idx not in self._rendered:
                widget = self._renderer(self._inner, self._items[idx])
                widget.place(x=0, y=idx * self._item_height,
                            width=self._canvas.winfo_width(), height=self._item_height)
                self._rendered[idx] = widget
