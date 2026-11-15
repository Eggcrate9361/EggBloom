#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
反馈组件 - 模态框、对话框、Toast通知
"""

import tkinter as tk
from design_system import C, S, T
from animator import Animator


class Modal(tk.Toplevel):
    """模态窗口基类"""

    def __init__(self, parent, title: str = "", width: int = 460, height: int = 320):
        super().__init__(parent)
        self.parent = parent
        self._result = None
        self._width = width
        self._height = height

        self.title(title)
        self.configure(bg=C["bg_root"])
        self.transient(parent)
        self.resizable(False, False)

        # 窗口居中
        self.update_idletasks()
        px = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        py = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
        self.geometry(f"{width}x{height}+{px}+{py}")

        # 遮罩效果
        self.attributes("-alpha", 0.0)
        self._fade_in()

        # 键绑定
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Escape>", lambda e: self._on_close())
        self.grab_set()

    def _fade_in(self, step: float = 0.0):
        if not self.winfo_exists():
            return
        duration = 220
        Animator.animate_color(
            widget=self,
            from_hex=f"{C['bg_root']}00",
            to_hex=C["bg_root"],
            duration=duration,
            easing="ease_out",
            on_complete=lambda: None,
            prop="bg",
        )

        def apply_alpha(progress: float) -> None:
            try:
                self.attributes("-alpha", min(progress, 1.0))
            except tk.TclError:
                pass

        Animator.animate(apply_alpha, 0.0, 1.0, duration, "ease_out")

    def _on_close(self):
        self._result = False
        self.grab_release()
        self.destroy()

    def show(self):
        self.wait_window()
        return self._result


class Dialog(Modal):
    """确认对话框"""

    def __init__(
        self,
        parent,
        title: str = "确认",
        message: str = "",
        confirm_text: str = "确认",
        cancel_text: str = "取消",
        confirm_danger: bool = False,
        icon: str = "warning",
    ):
        super().__init__(parent, title, 420, 200)

        # 图标颜色映射
        icon_colors = {
            "warning": C["accent_warning"],
            "error": C["accent_danger"],
            "success": C["accent_success"],
            "info": C["accent_primary"],
        }
        icon_color = icon_colors.get(icon, C["accent_warning"])

        # 主容器
        main = tk.Frame(self, bg=C["bg_root"])
        main.pack(fill=tk.BOTH, expand=True, padx=S.get("xl"), pady=S.get("lg"))

        # 图标 + 消息
        top = tk.Frame(main, bg=C["bg_root"])
        top.pack(fill=tk.X, pady=(0, S.get("md")))

        # 圆形图标
        icon_canvas = tk.Canvas(
            top, width=44, height=44, bg=C["bg_root"], highlightthickness=0
        )
        icon_canvas.pack(side=tk.LEFT, padx=(0, S.get("md")))
        icon_canvas.create_oval(2, 2, 42, 42, fill=icon_color, outline="")
        icon_canvas.create_text(22, 22, text="!", fill="white",
                                font=(T.get_font_family(), 18, "bold"))

        tk.Label(
            top,
            text=message,
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            fg=C["text_primary"],
            bg=C["bg_root"],
            wraplength=300,
            justify=tk.LEFT,
        ).pack(side=tk.LEFT, fill=tk.X)

        # 按钮区
        btn_frame = tk.Frame(main, bg=C["bg_root"])
        btn_frame.pack(fill=tk.X, pady=(S.get("md"), 0))

        # 取消
        cancel_btn = tk.Label(
            btn_frame,
            text=cancel_text,
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            fg=C["text_secondary"],
            bg=C["bg_input"],
            cursor="hand2",
            padx=S.get("xl"),
            pady=S.get("sm"),
        )
        cancel_btn.pack(side=tk.RIGHT, padx=(S.get("sm"), 0))
        cancel_btn.bind("<Enter>", lambda e: cancel_btn.configure(bg=C["bg_hover"]))
        cancel_btn.bind("<Leave>", lambda e: cancel_btn.configure(bg=C["bg_input"]))
        cancel_btn.bind("<Button-1>", lambda e: self._confirm_cancel())

        # 确认
        confirm_color = C["accent_danger"] if confirm_danger else C["accent_primary"]
        confirm_btn = tk.Label(
            btn_frame,
            text=confirm_text,
            font=(T.get_font_family(), T.get_font_config("body_medium")["size"]),
            fg="white",
            bg=confirm_color,
            cursor="hand2",
            padx=S.get("xl"),
            pady=S.get("sm"),
        )
        confirm_btn.pack(side=tk.RIGHT)
        confirm_btn.bind("<Enter>", lambda e: confirm_btn.configure(
            bg=C["accent_hover"] if confirm_danger else C["accent_hover"]))
        confirm_btn.bind("<Leave>", lambda e: confirm_btn.configure(bg=confirm_color))
        confirm_btn.bind("<Button-1>", lambda e: self._confirm_ok())

    def _confirm_ok(self):
        self._result = True
        self.grab_release()
        self.destroy()

    def _confirm_cancel(self):
        self._result = False
        self.grab_release()
        self.destroy()


class Toast(tk.Toplevel):
    """Toast 通知"""

    POSITIONS = {
        "top_right": "ne",
        "top_left": "nw",
        "bottom_right": "se",
        "bottom_left": "sw",
        "top_center": "n",
        "bottom_center": "s",
    }

    TYPE_COLORS = {
        "success": (C["accent_success"], "✓"),
        "error": (C["accent_danger"], "✕"),
        "warning": (C["accent_warning"], "!"),
        "info": (C["accent_primary"], "i"),
    }

    def __init__(
        self,
        parent,
        message: str,
        toast_type: str = "info",
        duration: int = 3000,
        position: str = "top_right",
    ):
        super().__init__(parent)
        self.transient(parent)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=C["bg_darker"])

        color, icon_char = self.TYPE_COLORS.get(toast_type,
                                                  (C["accent_primary"], "i"))

        # 内容区
        inner = tk.Frame(self, bg=C["bg_darker"], padx=S.get("md"), pady=S.get("sm"))
        inner.pack()

        # 图标
        icon_label = tk.Label(
            inner,
            text=icon_char,
            font=(T.get_font_family(), T.get_font_config("body_small")["size"], "bold"),
            fg=color,
            bg=C["bg_darker"],
        )
        icon_label.pack(side=tk.LEFT, padx=(0, S.get("sm")))

        # 消息
        tk.Label(
            inner,
            text=message,
            font=(T.get_font_family(), T.get_font_config("body_small")["size"]),
            fg=C["text_primary"],
            bg=C["bg_darker"],
        ).pack(side=tk.LEFT)

        # 定位
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        pw = parent.winfo_width() if parent.winfo_width() > 1 else 1200
        ph = parent.winfo_height() if parent.winfo_height() > 1 else 800
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()

        offset = 20
        pos_map = {
            "ne": (px + pw - w - offset, py + offset),
            "nw": (px + offset, py + offset),
            "se": (px + pw - w - offset, py + ph - h - offset),
            "sw": (px + offset, py + ph - h - offset),
            "n": (px + (pw - w) // 2, py + offset),
            "s": (px + (pw - w) // 2, py + ph - h - offset),
        }
        x, y = pos_map.get(self.POSITIONS.get(position, "ne"), pos_map["ne"])
        self.geometry(f"+{x}+{y}")

        # 淡入
        self.attributes("-alpha", 0.0)

        def apply_alpha(progress: float) -> None:
            try:
                self.attributes("-alpha", min(progress, 1.0))
            except tk.TclError:
                pass

        Animator.animate(apply_alpha, 0.0, 1.0, 220, "ease_out")

    def _fade_in(self, duration, step=0.0):
        if not self.winfo_exists():
            return
        def apply_alpha(progress: float) -> None:
            try:
                self.attributes("-alpha", min(progress, 1.0))
            except tk.TclError:
                pass
        Animator.animate(apply_alpha, 0.0, 1.0, 220, "ease_out",
                         on_complete=lambda: self.after(duration, self._fade_out) if self.winfo_exists() else None)

    def _fade_out(self, step=1.0):
        if not self.winfo_exists():
            return
        def apply_alpha(progress: float) -> None:
            try:
                self.attributes("-alpha", max(progress, 0.0))
            except tk.TclError:
                pass
        Animator.animate(apply_alpha, 1.0, 0.0, 200, "ease_in",
                         on_complete=lambda: self.destroy() if self.winfo_exists() else None)


if __name__ == "__main__":
    root = tk.Tk()
    root.title("Dialog Test")
    root.geometry("600x400")
    root.configure(bg=C["bg_root"])

    def show_dialog():
        dlg = Dialog(root, "删除确认", "确定要删除该协程吗？此操作不可撤销。", confirm_danger=True)
        result = dlg.show()
        if result:
            Toast(root, "已删除", "success").grab_set()

    btn = tk.Label(root, text="打开对话框", font=(T.get_font_family(), 14),
                   fg="white", bg=C["accent_primary"], cursor="hand2",
                   padx=S.get("xl"), pady=S.get("md"))
    btn.pack(padx=40, pady=40)
    btn.bind("<Button-1>", lambda e: show_dialog())

    root.mainloop()