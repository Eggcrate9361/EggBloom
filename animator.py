#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
EggBloom v3 动画系统
统一管理动画、过渡效果和交互反馈
"""

import tkinter as tk
import math
import time
from typing import Optional, Callable, Dict, Any, List, Tuple
from design_system import C


def _hex_to_rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
    return f"#{int(rgb[0]):02x}{int(rgb[1]):02x}{int(rgb[2]):02x}"


def _lerp_color(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    return _rgb_to_hex((
        int(r1 + (r2 - r1) * t),
        int(g1 + (g2 - g1) * t),
        int(b1 + (b2 - b1) * t),
    ))


class Animator:
    """统一动画管理器：共享 tick，避免 after 风暴"""

    _tick_root: Optional[tk.Misc] = None
    _tick_jobs: List[Dict[str, Any]] = []
    _tick_scheduled: bool = False

    # ==========================================================================
    # 缓动函数
    # ==========================================================================

    @staticmethod
    def ease_in(t: float) -> float:
        return t * t

    @staticmethod
    def ease_out(t: float) -> float:
        return 1 - (1 - t) * (1 - t)

    @staticmethod
    def ease_in_out(t: float) -> float:
        return 2 * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 2 / 2

    @staticmethod
    def bounce(t: float) -> float:
        n1, d1 = 7.5625, 2.75
        if t < 1 / d1:
            return n1 * t * t
        elif t < 2 / d1:
            return n1 * (t - 1.5 / d1) ** 2 + 0.75
        elif t < 2.5 / d1:
            return n1 * (t - 2.25 / d1) ** 2 + 0.9375
        return n1 * (t - 2.625 / d1) ** 2 + 0.984375

    @staticmethod
    def linear(t: float) -> float:
        return t

    EASING_MAP = {
        "linear": linear.__func__,
        "ease_in": ease_in.__func__,
        "ease_out": ease_out.__func__,
        "ease_in_out": ease_in_out.__func__,
        "bounce": bounce.__func__,
    }

    # ==========================================================================
    # 共享 tick
    # ==========================================================================

    @classmethod
    def start_tick(cls, root: tk.Misc) -> None:
        """启动全局动画心跳；只需在 MainWindow 初始化时调用一次"""
        if cls._tick_scheduled:
            return
        cls._tick_root = root
        cls._tick_scheduled = True
        cls._tick()

    @classmethod
    def stop_tick(cls) -> None:
        cls._tick_scheduled = False

    @classmethod
    def _tick(cls) -> None:
        now = time.time() * 1000
        alive: List[Dict[str, Any]] = []
        for job in cls._tick_jobs:
            elapsed = now - job["start"]
            if elapsed >= job["duration"]:
                try:
                    if job.get("vector"):
                        job["apply"](job["to"])
                    else:
                        job["apply"](job["to"])
                except Exception:
                    pass
                cb = job.get("on_complete")
                if cb:
                    try:
                        cb()
                    except Exception:
                        pass
            else:
                t = elapsed / job["duration"]
                eased_t = job["easing"](t)
                if job.get("vector"):
                    val = [from_v + (to_v - from_v) * eased_t for from_v, to_v in zip(job["from"], job["to"])]
                else:
                    val = job["from"] + (job["to"] - job["from"]) * eased_t
                try:
                    job["apply"](val)
                except Exception:
                    pass
                alive.append(job)
        cls._tick_jobs = alive
        if cls._tick_scheduled or alive:
            cls._tick_root.after(16, cls._tick)

    # ==========================================================================
    # 通用动画
    # ==========================================================================

    @classmethod
    def animate(
        cls,
        apply_fn: Callable[[Any], None],
        from_val: Any,
        to_val: Any,
        duration: int = 250,
        easing: str = "ease_out",
        on_complete: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        is_vector = isinstance(from_val, (list, tuple)) and isinstance(to_val, (list, tuple))
        if is_vector:
            if len(from_val) != len(to_val):
                raise ValueError("Vector from/to length mismatch")
            from_val = list(from_val)
            to_val = list(to_val)

        job = {
            "start": time.time() * 1000,
            "duration": duration,
            "easing": cls.EASING_MAP.get(easing, cls.ease_out),
            "from": from_val,
            "to": to_val,
            "apply": apply_fn,
            "on_complete": on_complete,
            "vector": is_vector,
        }
        cls._tick_jobs.append(job)
        return job

    @classmethod
    def animate_color(
        cls,
        widget: tk.Misc,
        from_hex: str,
        to_hex: str,
        duration: int = 250,
        easing: str = "ease_out",
        on_complete: Optional[Callable] = None,
        prop: str = "bg",
    ) -> Dict[str, Any]:
        start_rgb = _hex_to_rgb(from_hex)
        target_rgb = _hex_to_rgb(to_hex)

        def apply(rgb: Tuple[int, int, int]) -> None:
            try:
                widget.configure(**{prop: _rgb_to_hex(rgb)})
            except Exception:
                pass

        def on_done() -> None:
            if on_complete:
                on_complete()

        return cls.animate(
            apply_fn=apply,
            from_val=start_rgb,
            to_val=target_rgb,
            duration=duration,
            easing=easing,
            on_complete=on_done,
        )

    # ==========================================================================
    # 兼容旧 API
    # ==========================================================================

    @classmethod
    def animate_bg(cls, widget, from_color: str, to_color: str, duration: int = 250,
                   easing: str = "ease_out", on_complete: Optional[Callable] = None) -> None:
        cls.animate_color(widget, from_color, to_color, duration, easing, on_complete, prop="bg")

    @classmethod
    def animate_fade_in(cls, widget, duration: int = 300, on_complete: Optional[Callable] = None) -> None:
        widget.pack()
        widget.update()
        cls.animate_color(widget, f"{C['bg_root']}00", C["bg_root"], duration, "ease_out", on_complete, prop="bg")

    @classmethod
    def animate_slide(cls, widget, direction: str = "right", duration: int = 300,
                      distance: int = 50, on_complete: Optional[Callable] = None) -> None:
        easing_fn = cls.EASING_MAP.get("ease_out", cls.ease_out)
        original_x = widget.winfo_x()

        def apply(offset: float) -> None:
            if direction in ("right", "left"):
                sign = 1 if direction == "right" else -1
                try:
                    widget.place(x=original_x + sign * int(offset))
                except Exception:
                    pass

        cls.animate(apply, distance, 0, duration, "ease_out", on_complete)

    @classmethod
    def animate_pulse(cls, canvas_item, canvas: tk.Canvas, color1: str, color2: str,
                      period: float = 1.0, tag: str = "pulse") -> None:
        r1, g1, b1 = _hex_to_rgb(color1)
        r2, g2, b2 = _hex_to_rgb(color2)
        start_time = time.time()

        def apply(t: float) -> None:
            try:
                color = _rgb_to_hex((
                    int(r1 + (r2 - r1) * t),
                    int(g1 + (g2 - g1) * t),
                    int(b1 + (b2 - b1) * t),
                ))
                canvas.itemconfig(canvas_item, fill=color, outline=color)
            except tk.TclError:
                pass

        def schedule() -> None:
            t = (math.sin((time.time() - start_time) * 2 * math.pi / period) + 1) / 2
            apply(t)
            cls.animate(lambda v: None, 0, 1, int(period * 500), "linear",
                        on_complete=lambda: canvas.after(int(period * 500), schedule))

        schedule()


class TickManager:
    """兼容旧循环式动画的统一 tick"""

    @classmethod
    def loop(cls, root: tk.Misc, callback: Callable[[float], None],
             interval_ms: int = 16, phase: float = 0.0, speed: float = 1.0) -> Callable[[], None]:
        stop = {"flag": False}

        def _tick():
            nonlocal phase
            if stop["flag"]:
                return
            callback(phase)
            phase += speed * interval_ms / 1000
            root.after(interval_ms, _tick)

        root.after(0, _tick)
        return lambda: stop.__setitem__("flag", True)


class Toast:
    """Toast 通知"""

    _active_toasts: List[tk.Toplevel] = []

    @classmethod
    def show(cls, parent, message: str, type: str = "info", duration: int = 3000) -> None:
        colors = {
            "info": (C["accent_primary"], C["bg_card"]),
            "success": (C["accent_success"], C["bg_card"]),
            "warning": (C["accent_warning"], C["bg_card"]),
            "error": (C["accent_danger"], C["bg_card"]),
        }
        accent, bg = colors.get(type, colors["info"])

        toast = tk.Toplevel(parent)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.configure(bg=bg)

        frame = tk.Frame(toast, bg=bg, padx=16, pady=10)
        frame.pack()

        indicator = tk.Frame(frame, bg=accent, width=4)
        indicator.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 12))
        indicator.pack_propagate(False)

        msg_lbl = tk.Label(frame, text=message, font=("Microsoft YaHei UI", 11),
                           fg=C["text_primary"], bg=bg, wraplength=300, justify=tk.LEFT)
        msg_lbl.pack(side=tk.LEFT)

        close_btn = tk.Label(frame, text="✕", font=("Microsoft YaHei UI", 10),
                             fg=C["text_muted"], bg=bg, cursor="hand2")
        close_btn.pack(side=tk.RIGHT, padx=(12, 0))
        close_btn.bind("<Button-1>", lambda e: cls._close(toast))

        toast.update_idletasks()
        tw, th = toast.winfo_width(), toast.winfo_height()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        x = parent.winfo_rootx() + pw - tw - 20
        y_offset = 20 + len(cls._active_toasts) * (th + 8)
        y = parent.winfo_rooty() + ph - y_offset - 20
        toast.geometry(f"+{int(x)}+{int(y)}")

        cls._active_toasts.append(toast)

        if duration > 0:
            parent.after(duration, lambda: cls._close(toast))

    @classmethod
    def _close(cls, toast: tk.Toplevel) -> None:
        try:
            toast.destroy()
        except tk.TclError:
            pass
        cls._active_toasts = [t for t in cls._active_toasts if t.winfo_exists()]


class ShortcutManager:
    """键盘快捷键管理器"""

    def __init__(self, root):
        self.root = root
        self._shortcuts = {}

    def register(self, key_sequence: str, callback: Callable, description: str = ""):
        mod_map = {"ctrl": "Control", "alt": "Alt", "shift": "Shift"}
        fn_map = {
            "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4", "f5": "F5",
            "f6": "F6", "f7": "F7", "f8": "F8", "f9": "F9", "f10": "F10",
            "f11": "F11", "f12": "F12",
            "escape": "Escape", "return": "Return", "space": "Space",
            "tab": "Tab", "backspace": "BackSpace", "delete": "Delete",
            "up": "Up", "down": "Down", "left": "Left", "right": "Right",
            "home": "Home", "end": "End", "page_up": "Prior", "page_down": "Next",
        }

        keys = key_sequence.lower().split("+")
        mods = []
        key = None
        for k in keys:
            if k in mod_map:
                mods.append(mod_map[k])
            elif k in fn_map:
                key = fn_map[k]
            else:
                key = k.upper()

        if key:
            seq = "<" + "-".join(mods + [key]) + ">"
            self.root.bind(seq, lambda e, cb=callback: cb())
            self._shortcuts[key_sequence] = {
                "callback": callback,
                "description": description,
            }
