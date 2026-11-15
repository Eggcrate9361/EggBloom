#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
protocol_panel.py — 协议层控制面板（CTk 兼容版）
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog
import os
import time
import threading
import urllib.request
import json

try:
    from ctk_theme import SOFT_COLORS as C
except ImportError:
    try:
        from design_system import C
    except ImportError:
        C = {
            "bg_root": "#1A1D23", "bg_card": "#252830", "bg_input": "#1E2127",
            "text_primary": "#EAECEF", "text_secondary": "#ABB2BF", "text_muted": "#5C6370",
            "accent": "#61AFEF", "accent_success": "#98C379", "accent_danger": "#E06C75",
            "border_light": "#3A3F4B", "hover_bg": "#2C313A",
        }

FONT = "Microsoft YaHei UI"


class ProtocolPanel(ctk.CTkFrame):
    """协议层控制面板"""

    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, fg_color=C.get("bg_root", "#1A1D23"), **kwargs)
        self.app = app
        self._build_ui()

    def _build_ui(self):
        bg = C.get("bg_root", "#1A1D23")
        card_bg = C.get("bg_card", "#252830")

        # 标题
        ctk.CTkLabel(self, text="协议层控制", font=(FONT, 16, "bold"),
                     text_color=C.get("text_primary", "#EAECEF")).pack(anchor="w", padx=16, pady=(16, 8))

        # 主卡片
        card = ctk.CTkFrame(self, fg_color=card_bg, corner_radius=8,
                            border_width=1, border_color=C.get("border_light", "#3A3F4B"))
        card.pack(fill=tk.X, padx=16, pady=(0, 8))

        # 第一行：开关 + 状态
        r1 = ctk.CTkFrame(card, fg_color="transparent")
        r1.pack(fill=tk.X, padx=12, pady=(10, 6))
        ctk.CTkLabel(r1, text="协议层", font=(FONT, 12),
                     text_color=C.get("text_primary", "#EAECEF")).pack(side=tk.LEFT)

        self._toggle_var = ctk.BooleanVar(value=False)
        self._toggle = ctk.CTkSwitch(r1, text="", variable=self._toggle_var,
                                     onvalue=True, offvalue=False,
                                     command=self._on_toggle,
                                     fg_color=C.get("border_light", "#3A3F4B"),
                                     progress_color=C.get("accent_success", "#98C379"),
                                     button_color=C.get("text_primary", "#EAECEF"))
        self._toggle.pack(side=tk.RIGHT)

        self._toggle_label = ctk.CTkLabel(r1, text="关闭", font=(FONT, 10),
                                          text_color=C.get("text_secondary", "#ABB2BF"))
        self._toggle_label.pack(side=tk.RIGHT, padx=(0, 8))

        # 状态指示
        self._status_dot = ctk.CTkLabel(r1, text="●", font=(FONT, 14),
                                        text_color=C.get("accent_danger", "#E06C75"))
        self._status_dot.pack(side=tk.LEFT, padx=(8, 4))
        self._status_label = ctk.CTkLabel(r1, text="未连接", font=(FONT, 10),
                                          text_color=C.get("accent_danger", "#E06C75"))
        self._status_label.pack(side=tk.LEFT)

        # 信息行
        for label, value in [("API 端口", "3000"), ("事件端口", "3005")]:
            r = ctk.CTkFrame(card, fg_color="transparent")
            r.pack(fill=tk.X, padx=12, pady=(0, 4))
            ctk.CTkLabel(r, text=label, font=(FONT, 10),
                         text_color=C.get("text_muted", "#5C6370")).pack(side=tk.LEFT)
            ctk.CTkLabel(r, text=value, font=("Consolas", 10),
                         text_color=C.get("text_primary", "#EAECEF")).pack(side=tk.RIGHT)

        # QQ 路径
        r_qq = ctk.CTkFrame(card, fg_color="transparent")
        r_qq.pack(fill=tk.X, padx=12, pady=(0, 8))
        ctk.CTkLabel(r_qq, text="QQ 路径", font=(FONT, 10),
                     text_color=C.get("text_muted", "#5C6370")).pack(side=tk.LEFT)
        self._qq_path_var = ctk.StringVar(value=self._load_qq_path())
        ctk.CTkLabel(r_qq, textvariable=self._qq_path_var, font=("Consolas", 9),
                     text_color=C.get("text_secondary", "#ABB2BF")).pack(side=tk.LEFT, padx=(8, 4))
        ctk.CTkButton(r_qq, text="浏览", font=(FONT, 9),
                      fg_color=C.get("accent", "#61AFEF"),
                      command=self._browse_qq_path, width=50, height=24, corner_radius=4).pack(side=tk.RIGHT)

        # 操作按钮
        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill=tk.X, padx=12, pady=(4, 10))
        ctk.CTkButton(btn_row, text="启动", font=(FONT, 11, "bold"),
                      fg_color=C.get("accent_success", "#98C379"),
                      command=self._start_protocol, width=70, height=30, corner_radius=6).pack(side=tk.LEFT, padx=(0, 8))
        ctk.CTkButton(btn_row, text="停止", font=(FONT, 11, "bold"),
                      fg_color=C.get("accent_danger", "#E06C75"),
                      command=self._stop_protocol, width=70, height=30, corner_radius=6).pack(side=tk.LEFT)

        # 日志
        ctk.CTkLabel(self, text="日志", font=(FONT, 11, "bold"),
                     text_color=C.get("text_secondary", "#ABB2BF")).pack(anchor="w", padx=16, pady=(12, 4))
        self._log_text = ctk.CTkTextbox(self, font=("Consolas", 10),
                                        fg_color=C.get("bg_log", "#0D0F12"),
                                        text_color=C.get("text_secondary", "#ABB2BF"),
                                        corner_radius=6, border_width=1,
                                        border_color=C.get("border_light", "#3A3F4B"))
        self._log_text.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))
        self._log_text.configure(state="disabled")

        threading.Thread(target=self._check_api, daemon=True).start()

    def _load_qq_path(self):
        try:
            with open(os.path.join(os.path.dirname(__file__), "bot_config.json"), "r") as f:
                return json.load(f).get("qq_path", "")
        except Exception:
            return ""

    def _browse_qq_path(self):
        path = filedialog.askopenfilename(title="选择 QQ.exe", filetypes=[("QQ", "QQ.exe")])
        if path:
            self._qq_path_var.set(path)
            try:
                cfg_path = os.path.join(os.path.dirname(__file__), "bot_config.json")
                cfg = {}
                if os.path.exists(cfg_path):
                    with open(cfg_path, "r") as f:
                        cfg = json.load(f)
                cfg["qq_path"] = path
                with open(cfg_path, "w") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
                self._log(f"QQ 路径已更新: {path}")
            except Exception as e:
                self._log(f"保存失败: {e}")

    def _on_toggle(self):
        if self._toggle_var.get():
            self._start_protocol()
        else:
            self._stop_protocol()

    def _start_protocol(self):
        self._toggle_label.configure(text="启动中...")
        threading.Thread(target=self._do_start, daemon=True).start()

    def _do_start(self):
        try:
            from protocol_manager import ProtocolManager
        except ImportError:
            self._log("[ERROR] protocol_manager 未安装")
            self._toggle.deselect()
            self._toggle_label.configure(text="错误")
            return
        mgr = ProtocolManager()
        root = self.winfo_toplevel()
        mgr.set_status_callback(lambda s, d: root.after(0, self._update_status, s, d))
        mgr.start()
        self.app._protocol_manager = mgr

    def _stop_protocol(self):
        mgr = getattr(self.app, '_protocol_manager', None)
        if mgr:
            def _do_stop():
                mgr.stop()
                self.winfo_toplevel().after(0, self._on_stop_complete)
            threading.Thread(target=_do_stop, daemon=True).start()
        else:
            self._toggle.deselect()
            self._update_stop_ui()

    def _on_stop_complete(self):
        self._update_stop_ui()
        self._log("[INFO] 协议层已停止")

    def _update_stop_ui(self):
        self._toggle.deselect()
        self._status_label.configure(text="已停止", text_color=C.get("accent_danger", "#E06C75"))
        self._status_dot.configure(text_color=C.get("accent_danger", "#E06C75"))
        self._toggle_label.configure(text="关闭")

    def _update_status(self, status, detail):
        if status == "started":
            self._toggle.select()
            self._toggle_label.configure(text="运行中")
            self._log(f"[INFO] {detail}")
            threading.Thread(target=self._check_api, daemon=True).start()
        elif status == "stopped":
            self._toggle.deselect()
            self._toggle_label.configure(text="关闭")
            self._log(f"[INFO] {detail}")
        elif status == "error":
            self._toggle.deselect()
            self._toggle_label.configure(text="错误")
            self._log(f"[ERROR] {detail}")
        elif status == "plugin_log":
            clean = detail[7:] if detail.startswith("[Node] ") else detail
            self._log(clean)
        else:
            self._log(f"[{status}] {detail}")

    def _check_api(self):
        try:
            from config import load_config
            base_port = load_config().get('protocol_api_port', 3000)
        except Exception:
            base_port = 3000
        while True:
            ok = False
            for port in [base_port, base_port + 1]:
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/get_status", timeout=2)
                    ok = True
                    break
                except Exception:
                    pass
            if ok:
                self.winfo_toplevel().after(0, self._set_connected)
                return
            time.sleep(10)

    def _set_connected(self):
        self._status_label.configure(text="已连接", text_color=C.get("accent_success", "#98C379"))
        self._status_dot.configure(text_color=C.get("accent_success", "#98C379"))
        self._log("[INFO] OneBot API 已连接")

    def _log(self, text):
        self._log_text.configure(state="normal")
        self._log_text.insert("end", text + "\n")
        self._log_text.see("end")
        self._log_text.configure(state="disabled")
