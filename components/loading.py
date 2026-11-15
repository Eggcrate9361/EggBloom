#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
状态组件 - 加载动画 LoadingSpinner / 骨架屏 Skeleton
"""

import tkinter as tk
import math
import time
from design_system import C, S
from animator import Animator


class LoadingSpinner(tk.Canvas):
    """旋转加载指示器"""

    def __init__(self, parent, size=32, color=None, stroke=3, speed=40, **kwargs):
        super().__init__(parent, width=size, height=size,
                         bg=C["bg_root"], highlightthickness=0, **kwargs)
        self._size = size
        self._color = color or C["accent_primary"]
        self._stroke = stroke
        self._speed = speed
        self._angle = 0
        self._running = False
        self._tick_ref = None

    def _draw(self):
        self.delete("all")
        cx = self._size // 2
        cy = self._size // 2
        r = self._size // 2 - self._stroke

        arc_extent = 120
        self.create_arc(
            self._stroke, self._stroke,
            self._size - self._stroke, self._size - self._stroke,
            start=self._angle, extent=arc_extent,
            outline=self._color, width=self._stroke, style=tk.ARC,
        )

    def start(self):
        if self._running or not self.winfo_exists():
            return
        self._running = True
        self._angle = 0

        def on_tick():
            if not self._running or not self.winfo_exists():
                return
            self._angle = (self._angle + 12) % 360
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

    def stop(self):
        self._running = False
        if self._tick_ref is not None:
            try:
                Animator._tick_jobs = [
                    j for j in Animator._tick_jobs
                    if j.get("apply") != self._tick_ref
                ]
            except Exception:
                pass
            self._tick_ref = None
        self.delete("all")


class Skeleton(tk.Frame):
    """骨架屏占位"""

    def __init__(self, parent, lines=3, **kwargs):
        bg = kwargs.pop("bg", C["bg_root"])
        super().__init__(parent, bg=bg, **kwargs)
        self._lines = lines
        self._pulse_phase = 0.0
        self._bars = []
        self._running = False
        self._tick_ref = None

        self._create()
        self.start_pulse()

    def _create(self):
        for i in range(self._lines):
            w_factor = [1.0, 0.7, 0.85, 0.6][i % 4] if i > 0 else 1.0
            bar = tk.Frame(self, bg=C["bg_input"], height=12)
            bar.pack(fill=tk.X, pady=(0 if i == 0 else 3, 3))
            bar._w_factor = w_factor
            self._bars.append(bar)

    def start_pulse(self):
        if self._running or not self.winfo_exists():
            return
        self._running = True

        def on_tick():
            if not self._running or not self.winfo_exists():
                return
            self._pulse_phase += 0.03
            t = (math.sin(self._pulse_phase) + 1) / 2
            for bar in self._bars:
                bar.configure(bg=C["bg_hover"] if t > 0.5 else C["bg_input"])

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
        self._running = False
        if self._tick_ref is not None:
            try:
                Animator._tick_jobs = [
                    j for j in Animator._tick_jobs
                    if j.get("apply") != self._tick_ref
                ]
            except Exception:
                pass
            self._tick_ref = None

    def destroy(self):
        self.stop_pulse()
        super().destroy()


if __name__ == "__main__":
    root = tk.Tk()
    root.title("Loading Test")
    root.geometry("400x300")
    root.configure(bg=C["bg_root"])

    frame = tk.Frame(root, bg=C["bg_root"])
    frame.pack(fill=tk.BOTH, padx=S.get("xl"), pady=S.get("xl"))

    spinner = LoadingSpinner(frame, size=40)
    spinner.pack(pady=(0, S.get("lg")))
    spinner.start()

    Skeleton(frame, lines=4).pack(fill=tk.X)

    Animator.start_tick(root)
    root.mainloop()
