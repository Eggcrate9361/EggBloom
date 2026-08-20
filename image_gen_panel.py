#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
image_gen_panel.py — 生图配置面板（CTk 兼容版）
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog
import os
import json
import threading

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

# 使用统一配置加载器
try:
    from config import load_config, save_config
except ImportError:
    def load_config():
        return {}
    def save_config(cfg, save_api_key=False):
        return False


class ImageGenPanel(ctk.CTkFrame):
    """生图配置面板"""

    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, fg_color=C.get("bg_root", "#1A1D23"), **kwargs)
        self.app = app
        self.config = self._load_config()
        self._build_ui()

    def _load_config(self):
        try:
            return load_config()
        except Exception:
            return {}

    def _save_config(self):
        try:
            cfg = load_config()
            cfg.update({
                "llm_model": self.llm_model_var.get(),
                "use_local_llm": self.llm_model_var.get() == "local",
                "llm_url": self.llm_url_var.get(),
                "comfyui_host": self.comfyui_host_var.get(),
                "comfyui_port": int(self.comfyui_port_var.get()),
                "comfyui_timeout": int(self.comfyui_timeout_var.get()),
                "comfyui_default_width": int(self.width_var.get()),
                "comfyui_default_height": int(self.height_var.get()),
                "comfyui_checkpoint": self.ckpt_var.get(),
                "nsfw_enabled": self.nsfw_var.get(),
                "workflow_path": self.workflow_var.get(),
                "prompt_node_id": self.node_id_var.get(),
            })
            if save_config(cfg):
                self._status("已保存")
            else:
                self._status("保存失败")
        except Exception as e:
            self._status(f"保存失败: {e}")

    def _build_ui(self):
        # 滚动容器（CTkScrollableFrame 自适应宽度）
        scroll = ctk.CTkScrollableFrame(self, fg_color=C.get("bg_root", "#1A1D23"),
                                        scrollbar_fg_color=C.get("bg_root", "#1A1D23"),
                                        scrollbar_button_color=C.get("border_light", "#3A3F4B"))
        scroll.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        # 标题
        ctk.CTkLabel(scroll, text="生图配置", font=(FONT, 16, "bold"),
                     text_color=C.get("text_primary", "#EAECEF")).pack(anchor="w", pady=(0, 10))

        # ═══ 提示词模型 ═══
        card = self._card(scroll, "提示词模型")
        row_model = ctk.CTkFrame(card, fg_color="transparent")
        row_model.pack(fill=tk.X, pady=(2, 0))
        ctk.CTkLabel(row_model, text="模型", font=(FONT, 11),
                     text_color=C.get("text_muted", "#5C6370"), width=80,
                     anchor="w").pack(side=tk.LEFT)
        self.llm_model_var = ctk.StringVar(value=self.config.get("llm_model", "deepseek"))
        model_dd = ctk.CTkOptionMenu(row_model, variable=self.llm_model_var,
                                     values=["local", "deepseek"],
                                     font=(FONT, 11), fg_color=C.get("bg_input", "#1E2127"),
                                     button_color=C.get("border_light", "#3A3F4B"),
                                     dropdown_fg_color=C.get("bg_card", "#252830"),
                                     width=140)
        model_dd.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
        ctk.CTkLabel(row_model, text="local=本地llama / deepseek=API",
                     font=(FONT, 9), text_color=C.get("text_muted", "#5C6370")).pack(side=tk.LEFT, padx=(8, 0))

        self.llm_url_var = ctk.StringVar(value=self.config.get("llm_url", "http://127.0.0.1:8081/v1/chat/completions"))
        self._row_entry(card, "API 地址", self.llm_url_var)

        # ═══ ComfyUI ═══
        card = self._card(scroll, "ComfyUI")
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill=tk.X, pady=(0, 4))
        self.comfyui_host_var = ctk.StringVar(value=self.config.get("comfyui_host", "127.0.0.1"))
        self._row_entry(row, "地址", self.comfyui_host_var, side="left")
        self.comfyui_port_var = ctk.StringVar(value=str(self.config.get("comfyui_port", 8000)))
        self._row_entry(row, "端口", self.comfyui_port_var, side="left")
        self.comfyui_timeout_var = ctk.StringVar(value=str(self.config.get("comfyui_timeout", 120)))
        self._row_entry(row, "超时s", self.comfyui_timeout_var, side="left")

        row2 = ctk.CTkFrame(card, fg_color="transparent")
        row2.pack(fill=tk.X, pady=(0, 4))
        self.width_var = ctk.StringVar(value=str(self.config.get("comfyui_default_width", 1024)))
        self._row_entry(row2, "宽度", self.width_var, side="left")
        self.height_var = ctk.StringVar(value=str(self.config.get("comfyui_default_height", 1536)))
        self._row_entry(row2, "高度", self.height_var, side="left")
        self.ckpt_var = ctk.StringVar(value=self.config.get("comfyui_checkpoint", ""))
        self._row_entry(row2, "模型", self.ckpt_var, side="left")

        ctk.CTkButton(card, text="测试 ComfyUI", font=(FONT, 11),
                      fg_color=C.get("accent", "#61AFEF"),
                      hover_color=C.get("accent_hover", "#1A8CD8"),
                      command=self._test_comfyui, height=32, corner_radius=6).pack(anchor="w", pady=(6, 0))

        # ═══ 工作流 ═══
        card = self._card(scroll, "工作流")
        self.workflow_var = ctk.StringVar(value=self.config.get("workflow_path", ""))
        self._row_file(card, "工作流路径", self.workflow_var)
        self.node_id_var = tk.StringVar(value=self.config.get("prompt_node_id", "353"))
        self._row_entry(card, "Prompt 节点 ID", self.node_id_var)

        # ═══ 内容 ═══
        card = self._card(scroll, "内容设置")
        self.nsfw_var = ctk.BooleanVar(value=self.config.get("nsfw_enabled", True))
        row_nsfw = ctk.CTkFrame(card, fg_color="transparent")
        row_nsfw.pack(fill=tk.X, pady=(2, 0))
        ctk.CTkLabel(row_nsfw, text="允许 NSFW", font=(FONT, 11),
                     text_color=C.get("text_secondary", "#ABB2BF")).pack(side=tk.LEFT)
        nsfw_switch = ctk.CTkSwitch(row_nsfw, text="", variable=self.nsfw_var,
                                    fg_color=C.get("border_light", "#3A3F4B"),
                                    progress_color=C.get("accent", "#61AFEF"),
                                    button_color=C.get("text_primary", "#EAECEF"))
        nsfw_switch.pack(side=tk.RIGHT)
        ctk.CTkLabel(card, text="开启后 AI 不会拒绝任何画图请求",
                     font=(FONT, 9), text_color=C.get("text_muted", "#5C6370")).pack(anchor="w", pady=(2, 0))

        # ═══ 保存 ═══
        btn_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        btn_frame.pack(fill=tk.X, pady=(12, 0))
        ctk.CTkButton(btn_frame, text="保存配置", font=(FONT, 12, "bold"),
                      fg_color=C.get("accent_success", "#98C379"),
                      hover_color="#7ab86a",
                      command=self._save_config, height=36, corner_radius=6).pack(side=tk.LEFT)

        self.status_label = ctk.CTkLabel(scroll, text="", font=(FONT, 10),
                                         text_color=C.get("text_muted", "#5C6370"))
        self.status_label.pack(anchor="w", pady=(4, 0))

    def _card(self, parent, title):
        card = ctk.CTkFrame(parent, fg_color=C.get("bg_card", "#252830"), corner_radius=8,
                            border_width=1, border_color=C.get("border_light", "#3A3F4B"))
        card.pack(fill=tk.X, pady=(0, 8))
        ctk.CTkLabel(card, text=title, font=(FONT, 12, "bold"),
                     text_color=C.get("text_primary", "#EAECEF")).pack(anchor="w", padx=12, pady=(10, 6))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill=tk.X, padx=12, pady=(0, 8))
        return inner

    def _row_entry(self, parent, label, var, side="top"):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill=tk.X, pady=(2, 0))
        ctk.CTkLabel(row, text=label, font=(FONT, 10),
                     text_color=C.get("text_muted", "#5C6370"), width=100,
                     anchor="w").pack(side=tk.LEFT if side == "left" else "top")
        e = ctk.CTkEntry(row, textvariable=var, font=(FONT, 10),
                         fg_color=C.get("bg_input", "#1E2127"),
                         border_color=C.get("border_light", "#3A3F4B"),
                         text_color=C.get("text_primary", "#EAECEF"),
                         corner_radius=6)
        e.pack(side=tk.LEFT if side == "left" else "top", fill=tk.X, expand=True,
               padx=(4, 0) if side == "left" else (0, 0), pady=(0, 0))
        return row

    def _row_file(self, parent, label, var):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill=tk.X, pady=(2, 0))
        ctk.CTkLabel(row, text=label, font=(FONT, 10),
                     text_color=C.get("text_muted", "#5C6370"), anchor="w").pack(anchor="w")
        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill=tk.X, pady=(2, 0))
        ctk.CTkEntry(inner, textvariable=var, font=(FONT, 10),
                     fg_color=C.get("bg_input", "#1E2127"),
                     border_color=C.get("border_light", "#3A3F4B"),
                     text_color=C.get("text_primary", "#EAECEF"),
                     corner_radius=6).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ctk.CTkButton(inner, text="...", font=(FONT, 10, "bold"),
                      fg_color=C.get("accent", "#61AFEF"),
                      command=lambda: self._browse(var),
                      width=36, height=28, corner_radius=4).pack(side=tk.RIGHT, padx=(4, 0))

    def _browse(self, var):
        path = filedialog.askopenfilename(title="选择工作流", filetypes=[("JSON", "*.json")])
        if path:
            var.set(path)

    def _test_comfyui(self):
        def _do():
            try:
                import urllib.request
                url = f"http://{self.comfyui_host_var.get()}:{self.comfyui_port_var.get()}/system_stats"
                resp = urllib.request.urlopen(url, timeout=5)
                data = json.loads(resp.read().decode())
                v = data.get("system", {}).get("comfyui_version", "?")
                self._status(f"ComfyUI v{v} 已连接")
            except Exception as e:
                self._status(f"连接失败: {e}")
        threading.Thread(target=_do, daemon=True).start()

    def _status(self, msg):
        self.status_label.configure(text=msg)
