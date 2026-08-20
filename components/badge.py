#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
展示组件 - 角标 Badge
"""

import tkinter as tk
from design_system import C, S, T


class Badge(tk.Canvas):
    """数字/文本角标"""

    COLORS = {
        "primary": C["accent_primary"],
        "danger": C["accent_danger"],
        "warning": C["accent_warning"],
        "success": C["accent_success"],
        "default": C["text_tertiary"],
    }

    def __init__(self, parent, text="0", color="primary", size=18, **kwargs):
        super().__init__(parent, width=size + 4, height=size + 4,
                         bg=C["bg_root"], highlightthickness=0, **kwargs)
        self._text = str(text)
        self._color = self.COLORS.get(color, C["accent_primary"])
        self._r = size // 2

        self._draw()

    def _draw(self):
        self.delete("all")
        cx = self._r + 2
        cy = self._r + 2

        # 背景圆
        if len(self._text) <= 2:
            self.create_oval(2, 2, self._r * 2 + 2, self._r * 2 + 2,
                             fill=self._color, outline="")
        else:
            # 胶囊形
            w = self._r * 2 + 4 + max(0, (len(self._text) - 2) * 6)
            self.configure(width=w + 4)
            self.create_oval(2, 2, self._r * 2 + 2, self._r * 2 + 2,
                             fill=self._color, outline="")
            self.create_rectangle(self._r + 2, 2, w - self._r + 2, self._r * 2 + 2,
                                  fill=self._color, outline="")
            self.create_oval(w - self._r * 2 + 2, 2, w + 2, self._r * 2 + 2,
                             fill=self._color, outline="")
            cx = w // 2 + 1

        fs = max(8, self._r - 1)
        self.create_text(cx, cy, text=self._text,
                         fill="white", font=(T.get_font_family(), fs, "bold"))

    def set_text(self, text):
        self._text = str(text)
        self._draw()

    def set_color(self, color):
        self._color = self.COLORS.get(color, C["accent_primary"])
        self._draw()


if __name__ == "__main__":
    root = tk.Tk()
    root.title("Badge Test")
    root.geometry("300x120")
    root.configure(bg=C["bg_root"])

    frame = tk.Frame(root, bg=C["bg_root"])
    frame.pack(padx=S.get("xl"), pady=S.get("xl"))

    Badge(frame, text="3", color="danger").pack(side=tk.LEFT, padx=8)
    Badge(frame, text="99+", color="primary").pack(side=tk.LEFT, padx=8)
    Badge(frame, text="NEW", color="success").pack(side=tk.LEFT, padx=8)
    Badge(frame, text="!", color="warning").pack(side=tk.LEFT, padx=8)

    root.mainloop()