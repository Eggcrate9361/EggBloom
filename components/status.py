#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
状态组件 - 状态徽章、状态点、进度条
"""

import tkinter as tk
from typing import Optional
from design_system import C, S, T
from animator import Animator
import math
import time


class StatusBadge(tk.Frame):
    """状态徽章"""

    STATUS_COLORS = {
        "online": C["accent_success"],
        "offline": C["text_tertiary"],
        "warning": C["accent_warning"],
        "error": C["accent_danger"],
        "info": C["accent_primary"],
        "success": C["accent_success"],
        "pending": C["accent_warning"],
    }

    def __init__(
        self,
        parent,
        text: str = "",
        status: str = "info",
        size: str = "medium",
        **kwargs
    ):
        bg = kwargs.pop("bg", C["bg_root"])
        super().__init__(parent, bg=bg, **kwargs)

        self.text = text
        self.status = status
        self.size = size

        color = self.STATUS_COLORS.get(status, C["accent_primary"])

        # 根据大小设置尺寸
        size_config = {
            "small": {
                "dot_size": 6,
                "font_size": T.get_font_config("caption")["size"],
                "padding": (S.get("xs"), S.get("xs")),
            },
            "medium": {
                "dot_size": 8,
                "font_size": T.get_font_config("body_small")["size"],
                "padding": (S.get("sm"), S.get("sm")),
            },
            "large": {
                "dot_size": 10,
                "font_size": T.get_font_config("body_medium")["size"],
                "padding": (S.get("md"), S.get("md")),
            },
        }

        config = size_config.get(size, size_config["medium"])

        # 状态点
        dot_canvas = tk.Canvas(
            self,
            width=config["dot_size"] + 4,
            height=config["dot_size"] + 4,
            bg=bg,
            highlightthickness=0,
        )
        dot_canvas.pack(side=tk.LEFT)
        dot_canvas.create_oval(2, 2, config["dot_size"] + 2, config["dot_size"] + 2,
                               fill=color, outline="")

        # 文本
        if text:
            tk.Label(
                self,
                text=text,
                font=(T.get_font_family(), config["font_size"]),
                fg=color,
                bg=bg,
            ).pack(side=tk.LEFT, padx=(S.get("xs"), 0))

    def set_status(self, status: str, text: Optional[str] = None):
        self.status = status
        color = self.STATUS_COLORS.get(status, C["accent_primary"])
        for child in self.winfo_children():
            if isinstance(child, tk.Canvas):
                child.delete("all")
                ds = child.winfo_height() - 4
                child.create_oval(2, 2, ds + 2, ds + 2, fill=color, outline="")
            elif isinstance(child, tk.Label):
                child.configure(fg=color)
                if text is not None:
                    child.configure(text=text)


class StatusDot(tk.Canvas):
    """状态点（带脉冲动画）"""

    def __init__(self, parent, size: int = 8, color: str = C["text_tertiary"], **kwargs):
        canvas_size = size + 6
        super().__init__(parent, width=canvas_size, height=canvas_size,
                         highlightthickness=0, bg=C["bg_root"], **kwargs)
        self._size = size
        self._canvas_size = canvas_size
        self._base = color
        self._pulse = False
        self._phase = 0.0
        self._redraw(color)

    def _redraw(self, color):
        self.delete("all")
        cx = self._canvas_size // 2
        cy = cx
        r = self._size // 2
        self.create_oval(cx - r - 1, cy - r - 1, cx + r + 1, cy + r + 1,
                         fill="", outline=color, width=1)
        self.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline="")

    def set_color(self, color: str):
        self._base = color
        self._redraw(color)

    def start_pulse(self):
        if self._pulse or not self.winfo_exists():
            return
        self._pulse = True
        self._phase = 0.0

        def on_tick():
            if not self._pulse or not self.winfo_exists():
                return
            self._phase += 0.08
            t = (math.sin(self._phase) + 1) / 2

            def _lerp(c1, c2, t):
                r1, g1, b1 = self._hex2rgb(c1)
                r2, g2, b2 = self._hex2rgb(c2)
                return f"#{int(r1+(r2-r1)*t):02x}{int(g1+(g2-g1)*t):02x}{int(b1+(b2-b1)*t):02x}"

            self._redraw(_lerp(C["accent_primary"], C["accent_hover"], t))

        self._tick_ref = on_tick
        Animator._tick_jobs.append({
            "start": time.time() * 1000,
            "duration": 999999,
            "easing": Animator.linear,
            "from": 0,
            "to": 1,
            "apply": lambda v: on_tick(),
            "on_complete": None,
        })

    def stop_pulse(self):
        self._pulse = False
        if self._tick_ref is not None:
            try:
                Animator._tick_jobs = [
                    j for j in Animator._tick_jobs
                    if j.get("apply") != self._tick_ref
                ]
            except Exception:
                pass
            self._tick_ref = None
        self._redraw(self._base)

    @staticmethod
    def _hex2rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


class ProgressBar(tk.Frame):
    """进度条"""

    def __init__(
        self,
        parent,
        value: float = 0.0,
        max_value: float = 100.0,
        show_label: bool = True,
        height: int = 8,
        color: str = C["accent_primary"],
        animated: bool = False,
        **kwargs
    ):
        bg = kwargs.pop("bg", C["bg_root"])
        super().__init__(parent, bg=bg, **kwargs)

        self.value = value
        self.max_value = max_value
        self.show_label = show_label
        self.bar_height = height
        self.color = color
        self.animated = animated
        self._anim_phase = 0.0

        self._create_content()

    def _create_content(self):
        # 标签行
        if self.show_label:
            label_frame = tk.Frame(self, bg=self["bg"])
            label_frame.pack(fill=tk.X, pady=(0, S.get("xs")))

            pct = int(self.value / self.max_value * 100) if self.max_value > 0 else 0
            tk.Label(
                label_frame,
                text=f"{pct}%",
                font=(T.get_font_family(), T.get_font_config("body_small")["size"]),
                fg=C["text_secondary"],
                bg=self["bg"],
            ).pack(side=tk.RIGHT)

        # 进度条背景
        self.bar_bg = tk.Canvas(
            self,
            height=self.bar_height,
            bg=C["bg_input"],
            highlightthickness=0,
        )
        self.bar_bg.pack(fill=tk.X)

        self._start_animation()

    def _draw(self):
        """绘制进度条（不自行调度，由 _start_animation 统一控制）"""
        self.bar_bg.delete("all")
        w = self.bar_bg.winfo_width()
        h = self.bar_height

        if w < 2:
            return

        pct = self.value / self.max_value if self.max_value > 0 else 0
        fill_w = int(w * pct)

        if fill_w > 0:
            self.bar_bg.create_rectangle(0, 0, fill_w, h, fill=self.color, outline="")

        if self.animated and fill_w > 0:
            self._anim_phase += 0.02
            highlight = int(fill_w * 0.3)
            offset = int((math.sin(self._anim_phase) + 1) * fill_w * 0.15)
            if highlight > 0:
                self.bar_bg.create_rectangle(
                    max(0, fill_w - highlight - offset),
                    0,
                    min(fill_w, fill_w - offset),
                    h,
                    fill=self._adjust_brightness(self.color, 1.3),
                    outline="",
                )

    def _start_animation(self):
        """共享 tick 驱动进度条高光动画，避免重复 after 循环"""
        if getattr(self, "_tick_ref", None) is not None:
            return

        def on_tick():
            if not self.winfo_exists():
                return
            self._anim_phase += 0.02
            self._draw()

        self._tick_ref = on_tick
        Animator._tick_jobs.append({
            "start": time.time() * 1000,
            "duration": 999999,
            "easing": Animator.linear,
            "from": 0,
            "to": 1,
            "apply": lambda v: on_tick(),
            "on_complete": None,
        })

    def destroy(self):
        if getattr(self, "_tick_ref", None) is not None:
            try:
                Animator._tick_jobs = [
                    j for j in Animator._tick_jobs
                    if j.get("apply") != self._tick_ref
                ]
            except Exception:
                pass
            self._tick_ref = None
        super().destroy()

    def _adjust_brightness(self, hex_color, factor):
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        r = min(255, int(r * factor))
        g = min(255, int(g * factor))
        b = min(255, int(b * factor))
        return f"#{r:02x}{g:02x}{b:02x}"

    def set_value(self, value: float):
        self.value = max(0, min(value, self.max_value))
        if self.show_label:
            for child in self.winfo_children():
                if isinstance(child, tk.Frame):
                    for sub in child.winfo_children():
                        if isinstance(sub, tk.Label):
                            pct = int(self.value / self.max_value * 100) if self.max_value > 0 else 0
                            sub.configure(text=f"{pct}%")
                            break
        # 不再直接调 _draw()，由动画循环统一处理

    def set_max_value(self, max_value: float):
        self.max_value = max_value

    def set_color(self, color: str):
        self.color = color


if __name__ == "__main__":
    root = tk.Tk()
    root.title("Status Components Test")
    root.geometry("400x300")
    root.configure(bg=C["bg_root"])

    frame = tk.Frame(root, bg=C["bg_root"])
    frame.pack(fill=tk.BOTH, expand=True, padx=S.get("xl"), pady=S.get("xl"))

    tk.Label(frame, text="状态组件测试", font=(T.get_font_family(), T.get_font_config("heading_medium")["size"], "bold"),
             fg=C["text_primary"], bg=C["bg_root"]).pack(anchor=tk.W, pady=(0, S.get("lg")))

    StatusBadge(frame, text="在线", status="online").pack(anchor=tk.W, pady=4)
    StatusBadge(frame, text="警告", status="warning").pack(anchor=tk.W, pady=4)
    StatusBadge(frame, text="错误", status="error").pack(anchor=tk.W, pady=4)
    StatusBadge(frame, text="离线", status="offline").pack(anchor=tk.W, pady=4)

    dot = StatusDot(frame, size=12, color=C["accent_success"])
    dot.pack(anchor=tk.W, pady=8)
    dot.start_pulse()

    ProgressBar(frame, value=65, show_label=True, animated=True).pack(fill=tk.X, pady=(S.get("md"), 0))

    root.mainloop()