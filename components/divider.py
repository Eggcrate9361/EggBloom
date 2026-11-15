#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
布局组件 - 分割线 Divider
"""

import tkinter as tk
from design_system import C, S


class Divider(tk.Frame):
    """分割线"""

    ORIENTATIONS = {
        "horizontal": ("fill", 1, "x"),
        "vertical": ("y", "fill", 1),
    }

    def __init__(self, parent, orientation="horizontal", color=None, thickness=1, **kwargs):
        bg_color = kwargs.pop("bg", C["bg_root"])
        super().__init__(parent, bg=bg_color, **kwargs)

        self._orientation = orientation
        self._color = color or C["border_default"]
        self._thickness = thickness

        pack_fill_h, h, pack_fill_v = self.ORIENTATIONS.get(orientation, ("fill", 1, "x"))

        line = tk.Frame(self, bg=self._color, height=self._thickness)
        line.pack(fill=pack_fill_h if orientation == "horizontal" else pack_fill_v)

        if orientation == "horizontal":
            self.configure(height=self._thickness)
        else:
            self.configure(width=self._thickness)


if __name__ == "__main__":
    root = tk.Tk()
    root.title("Divider Test")
    root.geometry("300x150")
    root.configure(bg=C["bg_root"])

    frame = tk.Frame(root, bg=C["bg_root"])
    frame.pack(fill=tk.BOTH, padx=S.get("xl"), pady=S.get("xl"))

    tk.Label(frame, text="上方内容", bg=C["bg_root"], fg=C["text_primary"]).pack(anchor=tk.W)
    Divider(frame).pack(fill=tk.X, pady=S.get("sm"))
    tk.Label(frame, text="下方内容", bg=C["bg_root"], fg=C["text_primary"]).pack(anchor=tk.W)

    root.mainloop()