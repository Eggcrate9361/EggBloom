#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
展示组件 - 空状态 EmptyState
"""

import tkinter as tk
from design_system import C, S, T


class EmptyState(tk.Frame):
    """空状态占位"""

    ICONS = {
        "search": ("🔍", "没有找到相关内容"),
        "empty": ("📭", "暂无数据"),
        "error": ("⚠", "加载失败"),
        "file": ("📄", "没有文件"),
        "plugin": ("🔌", "暂无插件"),
        "chat": ("💬", "暂无消息"),
        "task": ("📋", "暂无任务"),
    }

    def __init__(self, parent, icon_type="empty", title="", description="", **kwargs):
        bg = kwargs.pop("bg", C["bg_root"])
        super().__init__(parent, bg=bg, **kwargs)

        icon, default_title = self.ICONS.get(icon_type, ("📭", "暂无数据"))
        if not title:
            title = default_title

        inner = tk.Frame(self, bg=bg)
        inner.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        tk.Label(
            inner, text=icon,
            font=(T.get_font_family(), 48),
            fg=C["text_tertiary"], bg=bg,
        ).pack(pady=(0, S.get("md")))

        tk.Label(
            inner, text=title,
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            fg=C["text_secondary"], bg=bg,
        ).pack(pady=(0, 2))

        if description:
            tk.Label(
                inner, text=description,
                font=(T.get_font_family(), T.get_font_config("body_small")["size"]),
                fg=C["text_tertiary"], bg=bg,
            ).pack()


if __name__ == "__main__":
    root = tk.Tk()
    root.title("EmptyState Test")
    root.geometry("400x300")
    root.configure(bg=C["bg_root"])

    EmptyState(root, icon_type="search", description="尝试修改关键词重试").pack(
        fill=tk.BOTH, expand=True, padx=S.get("xl"), pady=S.get("xl"))

    root.mainloop()