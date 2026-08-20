#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
展示组件 - 头像 Avatar
"""

import tkinter as tk
from design_system import C, S, T


class Avatar(tk.Canvas):
    """彩色圆形文字头像"""

    PALETTE = [
        "#6366F1", "#8B5CF6", "#EC4899", "#F43F5E",
        "#F97316", "#EAB308", "#22C55E", "#14B8A6",
        "#06B6D4", "#3B82F6",
    ]

    def __init__(self, parent, name="?", size=36, color=None, **kwargs):
        super().__init__(parent, width=size, height=size,
                         bg=C["bg_root"], highlightthickness=0, **kwargs)
        self._name = name
        self._size = size
        self._color = color or self._pick_color(name)

        self._draw()

    def _pick_color(self, name):
        h = sum(ord(c) for c in name)
        return self.PALETTE[h % len(self.PALETTE)]

    def _draw(self):
        self.delete("all")
        r = self._size // 2
        cx, cy = r, r

        self.create_oval(0, 0, self._size, self._size,
                         fill=self._color, outline="")

        initial = self._name[0].upper() if self._name else "?"
        font_size = int(self._size * 0.4)
        self.create_text(cx, cy, text=initial,
                         fill="white", font=(T.get_font_family(), font_size, "bold"))

    def resize(self, size):
        self._size = size
        self.configure(width=size, height=size)
        self._draw()


if __name__ == "__main__":
    root = tk.Tk()
    root.title("Avatar Test")
    root.geometry("300x150")
    root.configure(bg=C["bg_root"])

    frame = tk.Frame(root, bg=C["bg_root"])
    frame.pack(padx=S.get("xl"), pady=S.get("xl"))

    for name in ["Alice", "Bob", "Charlie", "Diana", "Eve"]:
        Avatar(frame, name=name, size=44).pack(side=tk.LEFT, padx=6)

    root.mainloop()