#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
首次配置向导：API Key / QQ 路径 / 模型 / Bot QQ
"""
import tkinter as tk
from tkinter import filedialog
import customtkinter as ctk

try:
    from ctk_theme import SOFT_COLORS as C, font
except ImportError:
    C = {
        "bg_root": "#1A1D23", "bg_card": "#252830", "bg_input": "#1E2127",
        "text_primary": "#EAECEF", "text_secondary": "#ABB2BF", "text_muted": "#5C6370",
        "accent": "#61AFEF", "accent_success": "#98C379", "accent_danger": "#E06C75",
        "border_light": "#3A3F4B", "hover_bg": "#2C313A",
    }
    def font(size=11, weight="normal"):
        return ("Microsoft YaHei UI", size, weight)


class FirstRunWizard(ctk.CTkToplevel):
    def __init__(self, parent, cfg: dict):
        super().__init__(parent)
        self.title("EggBloom 首次配置")
        self.geometry("620x460")
        self.minsize(520, 400)
        self.configure(fg_color=C.get("bg_root", "#1A1D23"))
        self.cfg = cfg
        self.result = None
        self._step = 0
        self._pages = []
        self._build_steps()
        self._show_step(0)
        self.transient(parent)
        self.grab_set()

    def _build_steps(self):
        # Step 0: API
        s0 = ctk.CTkFrame(self, fg_color=C.get("bg_card", "#252830"), corner_radius=8)
        ctk.CTkLabel(s0, text="配置 AI 接口", font=font(14, "bold"),
                     text_color=C.get("text_primary", "#EAECEF")).pack(anchor="w", padx=16, pady=(16, 8))
        ctk.CTkLabel(s0, text="API 供应商", font=font(11), text_color=C.get("text_secondary", "#ABB2BF")).pack(anchor="w", padx=16)
        self._provider_var = ctk.StringVar(value=self.cfg.get("api_provider", "deepseek"))
        ctk.CTkOptionMenu(s0, variable=self._provider_var,
                          values=["deepseek", "openai", "custom"],
                          font=font(10), fg_color=C.get("bg_input", "#1E2127"),
                          button_color=C.get("border_light", "#3A3F4B"),
                          dropdown_fg_color=C.get("bg_card", "#252830")).pack(fill=tk.X, padx=16, pady=(4, 10))

        ctk.CTkLabel(s0, text="API 地址", font=font(11), text_color=C.get("text_secondary", "#ABB2BF")).pack(anchor="w", padx=16)
        self._api_url = ctk.CTkEntry(s0, font=font(11), fg_color=C.get("bg_input", "#1E2127"),
                                     border_color=C.get("border_light", "#3A3F4B"), text_color=C.get("text_primary", "#EAECEF"))
        self._api_url.insert(0, self.cfg.get("api_url", "https://api.deepseek.com/chat/completions"))
        self._api_url.pack(fill=tk.X, padx=16, pady=(4, 10))

        ctk.CTkLabel(s0, text="API 密钥", font=font(11), text_color=C.get("text_secondary", "#ABB2BF")).pack(anchor="w", padx=16)
        self._api_key = ctk.CTkEntry(s0, font=font(11), fg_color=C.get("bg_input", "#1E2127"),
                                     border_color=C.get("border_light", "#3A3F4B"), text_color=C.get("text_primary", "#EAECEF"),
                                     show="*")
        self._api_key.insert(0, self.cfg.get("api_key", ""))
        self._api_key.pack(fill=tk.X, padx=16, pady=(4, 10))

        ctk.CTkLabel(s0, text="模型", font=font(11), text_color=C.get("text_secondary", "#ABB2BF")).pack(anchor="w", padx=16)
        self._model = ctk.CTkEntry(s0, font=font(11), fg_color=C.get("bg_input", "#1E2127"),
                                   border_color=C.get("border_light", "#3A3F4B"), text_color=C.get("text_primary", "#EAECEF"))
        self._model.insert(0, self.cfg.get("model_name", "deepseek-flash"))
        self._model.pack(fill=tk.X, padx=16, pady=(4, 12))
        self._pages.append(s0)

        # Step 1: QQ
        s1 = ctk.CTkFrame(self, fg_color=C.get("bg_card", "#252830"), corner_radius=8)
        ctk.CTkLabel(s1, text="配置 QQ", font=font(14, "bold"),
                     text_color=C.get("text_primary", "#EAECEF")).pack(anchor="w", padx=16, pady=(16, 8))
        ctk.CTkLabel(s1, text="QQ 可执行文件路径（可选，可在“协议”页稍后配置）", font=font(11), text_color=C.get("text_secondary", "#ABB2BF")).pack(anchor="w", padx=16)
        row = ctk.CTkFrame(s1, fg_color="transparent")
        row.pack(fill=tk.X, padx=16, pady=(4, 10))
        self._qq_path = ctk.CTkEntry(row, font=font(11), fg_color=C.get("bg_input", "#1E2127"),
                                     border_color=C.get("border_light", "#3A3F4B"), text_color=C.get("text_primary", "#EAECEF"))
        self._qq_path.insert(0, self.cfg.get("qq_path", ""))
        self._qq_path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        ctk.CTkButton(row, text="浏览", font=font(10), fg_color=C.get("accent", "#61AFEF"),
                      command=self._browse_qq, width=60, height=28, corner_radius=4).pack(side=tk.RIGHT)
        ctk.CTkLabel(s1, text="如果 QQ 已安装，请选择 QQ.exe；若 unsure 可留空，稍后在“协议”页补。",
                     font=font(10), text_color=C.get("text_muted", "#5C6370"), wraplength=560, justify="left").pack(anchor="w", padx=16)
        self._pages.append(s1)

        # Step 2: Bot QQ
        s2 = ctk.CTkFrame(self, fg_color=C.get("bg_card", "#252830"), corner_radius=8)
        ctk.CTkLabel(s2, text="配置 Bot QQ", font=font(14, "bold"),
                     text_color=C.get("text_primary", "#EAECEF")).pack(anchor="w", padx=16, pady=(16, 8))
        ctk.CTkLabel(s2, text="Bot QQ 号", font=font(11), text_color=C.get("text_secondary", "#ABB2BF")).pack(anchor="w", padx=16)
        self._bot_qq = ctk.CTkEntry(s2, font=font(11), fg_color=C.get("bg_input", "#1E2127"),
                                    border_color=C.get("border_light", "#3A3F4B"), text_color=C.get("text_primary", "#EAECEF"))
        self._bot_qq.insert(0, self.cfg.get("bot_qq", ""))
        self._bot_qq.pack(fill=tk.X, padx=16, pady=(4, 12))
        self._pages.append(s2)

        # nav
        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.pack(fill=tk.X, side=tk.BOTTOM, padx=16, pady=16)
        self._prev_btn = ctk.CTkButton(nav, text="上一步", font=font(11), fg_color=C.get("bg_card", "#252830"),
                                       hover_color=C.get("hover_bg", "#2C313A"), text_color=C.get("text_secondary", "#ABB2BF"),
                                       command=self._prev, width=100, height=32, corner_radius=6)
        self._prev_btn.pack(side=tk.LEFT)
        self._next_btn = ctk.CTkButton(nav, text="下一步", font=font(11, "bold"), fg_color=C.get("accent_success", "#98C379"),
                                       command=self._next, width=100, height=32, corner_radius=6)
        self._next_btn.pack(side=tk.RIGHT)

    def _show_step(self, idx: int):
        for p in self._pages:
            p.pack_forget()
        self._pages[idx].pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))
        self._prev_btn.configure(state=tk.NORMAL if idx > 0 else tk.DISABLED)
        self._next_btn.configure(text="完成" if idx == len(self._pages) - 1 else "下一步")

    def _prev(self):
        if self._step > 0:
            self._step -= 1
            self._show_step(self._step)

    def _next(self):
        if self._step < len(self._pages) - 1:
            self._step += 1
            self._show_step(self._step)
            return
        self.result = {
            "api_provider": self._provider_var.get(),
            "api_url": self._api_url.get().strip(),
            "api_key": self._api_key.get().strip(),
            "model_name": self._model.get().strip(),
            "qq_path": self._qq_path.get().strip(),
            "bot_qq": self._bot_qq.get().strip(),
        }
        self.destroy()

    def _browse_qq(self):
        path = filedialog.askopenfilename(title="选择 QQ.exe", filetypes=[("QQ", "QQ.exe")])
        if path:
            self._qq_path.delete(0, tk.END)
            self._qq_path.insert(0, path)
