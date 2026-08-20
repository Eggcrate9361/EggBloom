#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
提示组件 - Tooltip（悬浮提示）
"""

import tkinter as tk
from design_system import C, S, T
from animator import Animator


class Tooltip:
    """悬浮提示"""

    def __init__(self, widget, text, delay=500, max_width=240):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.max_width = max_width
        self._tip_window = None
        self._after_id = None

        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self._hide)
        widget.bind("<Button-1>", self._hide)
        widget.bind("<Destroy>", self._on_destroy)

    def _schedule(self, event=None):
        self._after_id = self.widget.after(self.delay, self._show)

    def _show(self):
        if self._tip_window:
            return

        x = self.widget.winfo_pointerx() + 12
        y = self.widget.winfo_pointery() + 16

        self._tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_attributes("-topmost", True)
        tw.configure(bg=C["bg_darker"])

        inner = tk.Frame(tw, bg=C["bg_darker"], padx=S.get("sm"), pady=S.get("xs"))
        inner.pack()

        tk.Label(
            inner, text=self.text,
            font=(T.get_font_family(), T.get_font_config("caption")["size"]),
            fg=C["text_secondary"], bg=C["bg_darker"],
            wraplength=self.max_width, justify=tk.LEFT,
        ).pack()

        tw.update_idletasks()
        w, h = tw.winfo_width(), tw.winfo_height()
        sw = tw.winfo_screenwidth()
        sh = tw.winfo_screenheight()

        if x + w > sw:
            x = sw - w - 8
        if y + h > sh:
            y = y - h - 24

        tw.wm_geometry(f"+{x}+{y}")
        tw.attributes("-alpha", 0.0)
        self._fade_in(tw)

    def _fade_in(self, tw, step=0.0):
        if not tw.winfo_exists():
            return
        duration = 160

        def apply_alpha(progress: float) -> None:
            try:
                tw.attributes("-alpha", min(progress, 0.95))
            except tk.TclError:
                pass

        Animator.animate(apply_alpha, 0.0, 0.95, duration, "ease_out")

    def _hide(self, event=None):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
        if self._tip_window:
            self._tip_window.destroy()
            self._tip_window = None

    def _on_destroy(self, event=None):
        self._hide()


if __name__ == "__main__":
    root = tk.Tk()
    root.title("Tooltip Test")
    root.geometry("400x300")
    root.configure(bg=C["bg_root"])

    btn = tk.Label(root, text="悬停查看提示", font=(T.get_font_family(), 14),
                   fg="white", bg=C["accent_primary"], cursor="hand2",
                   padx=S.get("xl"), pady=S.get("md"))
    btn.pack(padx=40, pady=40)
    Tooltip(btn, "专业下拉面板\n1. 协程配置\n2. 实时监控\n3. 一键启停")
    root.mainloop()