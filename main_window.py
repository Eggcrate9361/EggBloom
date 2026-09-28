#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
EggBloom v3 主窗口 - CustomTkinter 版本
柔色配色 + 紧凑布局 + 可靠页面切换
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import ttk
import time
import threading
import json
import os
import sys

# 路径定义
if getattr(sys, 'frozen', False):
    SCRIPT_DIR = os.path.dirname(sys.executable)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 使用统一数据目录管理
try:
    from data_store import get_data_dir, get_config_dir
    _USER_DATA_DIR = get_data_dir()
    _CONFIG_DIR = get_config_dir()
except Exception:
    _USER_DATA_DIR = os.path.join(os.path.expanduser("~"), "AppData", "Local", "EggBloom")
    _CONFIG_DIR = _USER_DATA_DIR
os.makedirs(_USER_DATA_DIR, exist_ok=True)
os.makedirs(_CONFIG_DIR, exist_ok=True)

if getattr(sys, 'frozen', False):
    _PROJECT_ROOT = os.path.dirname(sys.executable)
else:
    _PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
import subprocess
import socket
import urllib.request
import psutil
from datetime import datetime
from typing import Optional, Dict

# CTk 主题
from ctk_theme import (
    SOFT_COLORS as C, apply_ctk_theme, CTkThemeMapper,
    FONT_FAMILY, font,
)

FONT = FONT_FAMILY
S = CTkThemeMapper()

from ui_themes import get_theme, apply_theme_to_c, t as _t

def _get_webui_port():
    """读取 webui_server 实际占用的端口（webui_server 启动时会写 last_port.txt）"""
    try:
        lp = os.path.join(_PROJECT_ROOT, "last_port.txt")
        if os.path.exists(lp):
            p = open(lp, encoding="utf-8").read().strip()
            if p.isdigit():
                return int(p)
    except Exception:
        pass
    return 8080

def _apply_selected_theme(name: str):
    palette = get_theme(name or "default")
    try:
        from ctk_theme import SOFT_COLORS
        SOFT_COLORS.clear()
        SOFT_COLORS.update(palette)
    except Exception:
        pass
    try:
        from design_system import C
        C.clear()
        C.update(palette)
    except Exception:
        pass
    try:
        apply_theme_to_c()
    except Exception:
        pass

_apply_selected_theme("default")

from animator import Animator, Toast as AppToast, ShortcutManager
from components import (
    StatusDot,
)

# 初始化 CTk 主题
apply_ctk_theme()


# ============================================================================
# 渐变头部
# ============================================================================

def _build_header(parent, title_text="", subtitle_text="", height=90):
    """用 CTkFrame + CTkLabel 构建页面标题，适配 DPI 缩放"""
    f = ctk.CTkFrame(parent, fg_color=C["bg_card"], height=height, corner_radius=0)
    f.pack(fill=tk.X)
    f.pack_propagate(False)

    ctk.CTkLabel(f, text=title_text, font=font(20, "bold"),
                 text_color=C["text_primary"]).pack(expand=True, pady=(6, 0))
    ctk.CTkLabel(f, text=subtitle_text, font=font(12),
                 text_color=C["text_secondary"]).pack()
    return f


def _hex_to_rgb(hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


# ============================================================================
# DashboardPanel
# ============================================================================

class DashboardPanel(ctk.CTkFrame):
    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, fg_color=C["bg_root"], **kwargs)
        self.app = app
        self._build_ui()

    def _build_ui(self):
        f = self

        # 页面标题
        _build_header(f, "欢迎使用 EggBloom", "QQ 机器人管理面板 · 实时监控")

        # 状态卡片
        cards_row = ctk.CTkFrame(f, fg_color="transparent")
        cards_row.pack(fill=tk.X, padx=16, pady=(12, 0))

        stat_cards = [
            ("bot_status", "Bot 状态", "未启动", C["text_tertiary"]),
            ("msg_count", "今日消息", "0", C["accent"]),
            ("uptime", "运行时长", "--:--:--", C["accent_success"]),
            ("memory", "内存占用", "-- MB", C["accent_warning"]),
        ]

        self.stat_labels = {}
        cards_row.columnconfigure((0, 1, 2, 3), weight=1)
        for i, (key, title, default, color) in enumerate(stat_cards):
            card = ctk.CTkFrame(cards_row, fg_color=C["bg_card"], corner_radius=8,
                                border_width=1, border_color=C["border_light"])
            card.grid(row=0, column=i, padx=4, sticky="nsew")

            ctk.CTkLabel(card, text=title, font=font(11),
                         text_color=C["text_muted"]).pack(anchor="w", padx=12, pady=(10, 2))
            lbl = ctk.CTkLabel(card, text=default, font=font(20, "bold"),
                               text_color=color)
            lbl.pack(anchor="w", padx=12, pady=(0, 10))
            self.stat_labels[key] = lbl

        # 快捷操作
        quick = ctk.CTkFrame(f, fg_color="transparent")
        quick.pack(fill=tk.X, padx=16, pady=(16, 0))

        ctk.CTkLabel(quick, text="快捷操作", font=font(13, "bold"),
                     text_color=C["text_primary"]).pack(anchor="w")

        btn_row = ctk.CTkFrame(quick, fg_color="transparent")
        btn_row.pack(fill=tk.X, pady=(6, 0))
        btn_row.columnconfigure((0, 1, 2, 3), weight=1)

        quick_actions = [
            ("启动 Bot", self.app.start_bot, C["accent_success"]),
            ("停止 Bot", self.app.stop_bot, C["accent_danger"]),
            ("设置", lambda: self.app.switch_page("settings"), C["accent"]),
            ("日志", lambda: self.app.switch_page("log"), C["accent_info"]),
        ]

        for i, (text, cmd, color) in enumerate(quick_actions):
            btn = ctk.CTkButton(btn_row, text=text, font=font(11, "bold"),
                                fg_color=color, hover_color=_lerp(color, 0.15),
                                command=cmd, height=36, corner_radius=6)
            btn.grid(row=0, column=i, padx=4, sticky="nsew")

        # 状态栏
        status_frame = ctk.CTkFrame(f, fg_color=C["bg_card"], corner_radius=6,
                                    border_width=1, border_color=C["border_light"])
        status_frame.pack(fill=tk.X, padx=16, pady=(16, 0))

        self.bot_status_lbl = ctk.CTkLabel(status_frame, text="Bot: 未启动",
                                           font=font(11), text_color=C["text_muted"])
        self.bot_status_lbl.pack(side=tk.LEFT, padx=12, pady=8)

        self.net_status_lbl = ctk.CTkLabel(status_frame, text="网络: --",
                                           font=font(11), text_color=C["text_muted"])
        self.net_status_lbl.pack(side=tk.LEFT, padx=12, pady=8)

        # 最近日志
        log_frame = ctk.CTkFrame(f, fg_color="transparent")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(16, 0))

        ctk.CTkLabel(log_frame, text="最近日志", font=font(13, "bold"),
                     text_color=C["text_primary"]).pack(anchor="w")

        self._log_text = ctk.CTkTextbox(log_frame, font=font(10),
                                        fg_color=C["bg_log"], text_color=C["text_primary"],
                                        corner_radius=6, border_width=1,
                                        border_color=C["border_light"])
        self._log_text.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        self._log_text.configure(state="disabled")

    def update_stats(self, stats: Dict):
        for key, value in stats.items():
            if key in self.stat_labels:
                self.stat_labels[key].configure(text=value)

    def append_log(self, text, tag="INFO"):
        self._log_text.configure(state="normal")
        self._log_text.insert("end", f"[{tag}] {text}\n")
        self._log_text.see("end")
        self._log_text.configure(state="disabled")
        # 保留最近 100 行
        lines = self._log_text.get("1.0", "end").split("\n")
        if len(lines) > 120:
            self._log_text.configure(state="normal")
            self._log_text.delete("1.0", f"{len(lines)-100}.0")
            self._log_text.configure(state="disabled")


def _lerp(color, amount):
    r, g, b = _hex_to_rgb(color)
    r = min(255, int(r + (255 - r) * amount))
    g = min(255, int(g + (255 - g) * amount))
    b = min(255, int(b + (255 - b) * amount))
    return f"#{r:02x}{g:02x}{b:02x}"


# ============================================================================
# LogPanel
# ============================================================================

class LogPanel(ctk.CTkFrame):
    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, fg_color=C["bg_root"], **kwargs)
        self.app = app
        self._log_count = 0
        self._filters = {"search": "", "level": "ALL"}
        self._logs = []
        self._build_ui()

    def _build_ui(self):
        # 搜索 + 筛选
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill=tk.X, padx=12, pady=(8, 4))

        self.search_entry = ctk.CTkEntry(toolbar, placeholder_text="搜索日志...",
                                         font=font(11), fg_color=C["bg_input"],
                                         border_color=C["border_light"],
                                         text_color=C["text_primary"],
                                         corner_radius=6)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self.search_entry.bind("<KeyRelease>", lambda e: self._apply_filters())

        self.level_var = ctk.StringVar(value="ALL")
        level_dd = ctk.CTkOptionMenu(toolbar, variable=self.level_var,
                                     values=["ALL", "INFO", "WARN", "ERROR", "DEBUG", "SYSTEM"],
                                     font=font(10), fg_color=C["bg_card"],
                                     button_color=C["border_light"],
                                     dropdown_fg_color=C["bg_card"],
                                     command=lambda _: self._apply_filters(),
                                     width=100)
        level_dd.pack(side=tk.LEFT, padx=(0, 8))

        ctk.CTkButton(toolbar, text="清空", font=font(10), fg_color=C["bg_card"],
                      hover_color=C["hover_bg"], text_color=C["text_secondary"],
                      command=self._clear, width=60, height=28, corner_radius=4).pack(side=tk.LEFT)

        self.log_count_lbl = ctk.CTkLabel(toolbar, text="0 条", font=font(10),
                                          text_color=C["text_muted"])
        self.log_count_lbl.pack(side=tk.RIGHT)

        # 错误统计
        stats = ctk.CTkFrame(self, fg_color="transparent")
        stats.pack(fill=tk.X, padx=12, pady=(0, 4))
        self.err_lbl = ctk.CTkLabel(stats, text="错误: 0", font=font(9),
                                    text_color=C["accent_danger"])
        self.err_lbl.pack(side=tk.LEFT, padx=(0, 12))
        self.warn_lbl = ctk.CTkLabel(stats, text="警告: 0", font=font(9),
                                     text_color=C["accent_warning"])
        self.warn_lbl.pack(side=tk.LEFT, padx=(0, 12))
        self.info_lbl = ctk.CTkLabel(stats, text="信息: 0", font=font(9),
                                     text_color=C["accent"])
        self.info_lbl.pack(side=tk.LEFT)

        # 日志文本
        self.log_text = ctk.CTkTextbox(self, font=font(10),
                                       fg_color=C["bg_log"], text_color=C["text_primary"],
                                       corner_radius=6, border_width=1,
                                       border_color=C["border_light"])
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))
        self.log_text.configure(state="disabled")

    def append(self, text, tag="INFO"):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] [{tag}] {text}\n"
        self._logs.append((line, tag))
        if len(self._logs) > 5000:
            self._logs = self._logs[-3000:]
            self._refresh_counters()
            return
        search = self.search_entry.get().lower()
        level = self.level_var.get()
        if level != "ALL" and tag != level:
            return
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        if len(self._logs) % 5 == 0:
            self._refresh_counters()

    def _refresh_counters(self):
        err = warn = info = 0
        for _, tag in self._logs:
            if tag == "ERROR": err += 1
            elif tag == "WARN": warn += 1
            else: info += 1
        self.log_count_lbl.configure(text=f"{len(self._logs)} 条")
        self.err_lbl.configure(text=f"错误: {err}")
        self.warn_lbl.configure(text=f"警告: {warn}")
        self.info_lbl.configure(text=f"信息: {info}")

    def _apply_filters(self):
        search = self.search_entry.get().lower()
        level = self.level_var.get()
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        visible = 0
        for line, tag in self._logs:
            if level != "ALL" and tag != level:
                continue
            self.log_text.insert("end", line)
            visible += 1
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self.log_count_lbl.configure(text=f"{visible}/{len(self._logs)} 条")
        self._refresh_counters()

    def _clear(self):
        self._logs.clear()
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._log_count = 0
        self.log_count_lbl.configure(text="0 条")
        self._refresh_counters()


# ============================================================================
# ConsolePanel
# ============================================================================

class ConsolePanel(ctk.CTkFrame):
    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, fg_color=C["bg_root"], **kwargs)
        self.app = app
        self._build_ui()
        self._refresh_status()

    def _build_ui(self):
        # 状态条
        status_frame = ctk.CTkFrame(self, fg_color=C["bg_card"], corner_radius=6,
                                    border_width=1, border_color=C["border_light"])
        status_frame.pack(fill=tk.X, padx=12, pady=(8, 4))
        status_frame.columnconfigure((0, 1, 2, 3), weight=1)

        self.status_items = {}
        for i, (key, label) in enumerate([("bot", "Bot"), ("mem", "内存"), ("uptime", "运行"), ("net", "连接")]):
            f = ctk.CTkFrame(status_frame, fg_color="transparent")
            f.grid(row=0, column=i, padx=12, pady=6, sticky="w")
            ctk.CTkLabel(f, text=label, font=font(10),
                         text_color=C["text_muted"]).pack(anchor="w")
            lbl = ctk.CTkLabel(f, text="--", font=font(11, "bold"),
                               text_color=C["text_primary"])
            lbl.pack(anchor="w")
            self.status_items[key] = lbl

        ctk.CTkButton(status_frame, text="刷新", font=font(10),
                      fg_color="transparent", hover_color=C["hover_bg"],
                      text_color=C["accent"], command=self._refresh_status,
                      width=50, height=28, corner_radius=4).grid(row=0, column=4, padx=8, pady=6, sticky="e")

        # 指令输入
        cmd_frame = ctk.CTkFrame(self, fg_color="transparent")
        cmd_frame.pack(fill=tk.X, padx=12, pady=(4, 4))

        self.cmd_input = ctk.CTkEntry(cmd_frame, placeholder_text="输入指令...",
                                      font=font(11), fg_color=C["bg_input"],
                                      border_color=C["border_light"],
                                      text_color=C["text_primary"],
                                      corner_radius=6)
        self.cmd_input.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self.cmd_input.bind("<Return>", lambda e: self._exec_cmd())

        ctk.CTkButton(cmd_frame, text="执行", font=font(10, "bold"),
                      fg_color=C["accent"], hover_color=C["accent_hover"],
                      command=self._exec_cmd, width=60, height=32, corner_radius=6).pack(side=tk.RIGHT)

        # 快捷按钮
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill=tk.X, padx=12, pady=(0, 4))

        for text, cmd in [("学习状态", self._learning_status), ("群聊历史", self._view_group_history),
                          ("私聊记忆", self._view_private_memory), ("重启Bot", self._restart_bot)]:
            ctk.CTkButton(btn_frame, text=text, font=font(9),
                          fg_color=C["bg_card"], hover_color=C["hover_bg"],
                          text_color=C["text_primary"], command=cmd,
                          height=28, corner_radius=4).pack(side=tk.LEFT, padx=(0, 4))

        # 输出区
        self.output = ctk.CTkTextbox(self, font=font(10),
                                     fg_color=C["bg_log"], text_color=C["text_primary"],
                                     corner_radius=6, border_width=1,
                                     border_color=C["border_light"])
        self.output.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))
        self.output.configure(state="disabled")

        self._log("控制台就绪。输入 /help 查看可用指令。")

    def _log(self, text, tag="info"):
        self.output.configure(state="normal")
        self.output.insert("end", f"{text}\n")
        self.output.see("end")
        self.output.configure(state="disabled")

    def _refresh_status(self):
        try:
            import psutil
            proc = psutil.Process()
            mem = proc.memory_info().rss / 1024 / 1024
            uptime_sec = time.time() - proc.create_time()
            h, m = divmod(int(uptime_sec), 3600)
            self.status_items["mem"].configure(text=f"{mem:.0f} MB")
            self.status_items["uptime"].configure(text=f"{h}h{m:02d}m")
            try:
                req = urllib.request.Request("http://127.0.0.1:3001/health")
                urllib.request.urlopen(req, timeout=2)
                self.status_items["bot"].configure(text="运行中")
                self.status_items["net"].configure(text="OneBot ✓")
            except:
                self.status_items["bot"].configure(text="已停止")
                self.status_items["net"].configure(text="OneBot ✗")
        except Exception as e:
            self._log(f"状态刷新失败: {e}")

    def _exec_cmd(self):
        cmd = self.cmd_input.get().strip()
        if not cmd:
            return
        self.cmd_input.delete(0, "end")
        self._log(f"> {cmd}")
        parts = cmd.split(maxsplit=1)
        command = parts[0].lower()
        handlers = {
            "/help": self._cmd_help,
            "/status": self._cmd_status,
            "/groups": self._cmd_groups,
            "/clear_history": self._cmd_clear_history,
            "/clear_all_history": self._cmd_clear_all_history,
            "/clear_learning": self._cmd_clear_learning,
            "/clear_private": self._cmd_clear_private,
            "/learning_status": self._cmd_learning_status,
            "/restart": self._cmd_restart,
            "/reload": self._cmd_reload,
        }
        handler = handlers.get(command)
        if handler:
            try:
                handler(parts[1] if len(parts) > 1 else "")
            except Exception as e:
                self._log(f"错误: {e}")
        else:
            self._log(f"未知指令: {command}。输入 /help 查看可用指令。")

    def _cmd_help(self, _=""):
        self._log("可用指令:")
        for c, d in [("/status", "系统状态"), ("/groups", "群聊历史"),
                     ("/clear_history <群号>", "清除群历史"), ("/clear_all_history", "清除所有历史"),
                     ("/clear_learning", "重置学习DB"), ("/clear_private <QQ号>", "清除私聊记忆"),
                     ("/learning_status", "学习状态"), ("/restart", "重启Bot"), ("/reload", "重载配置")]:
            self._log(f"  {c:<28} {d}")

    def _cmd_status(self, _=""):
        import psutil
        proc = psutil.Process()
        mem = proc.memory_info()
        self._log(f"进程: PID {proc.pid}, 线程 {proc.num_threads()}")
        self._log(f"内存: RSS {mem.rss/1024/1024:.0f}MB")
        self._log(f"CPU: {proc.cpu_percent(interval=0.5):.1f}%")

    def _cmd_groups(self, _=""):
        try:
            with open(os.path.join(_USER_DATA_DIR, "qq_bot_state.json"), encoding="utf-8") as f:
                state = json.load(f)
            gh = state.get("group_chat_history", {})
            self._log("群聊历史:")
            for gid, msgs in gh.items():
                self._log(f"  群 {gid}: {len(msgs)} 条记录")
            if not gh:
                self._log("  （无群聊历史）")
        except Exception as e:
            self._log(f"读取失败: {e}")

    def _cmd_clear_history(self, gid):
        if not gid:
            self._log("用法: /clear_history <群号>")
            return
        try:
            with open(os.path.join(_USER_DATA_DIR, "qq_bot_state.json"), encoding="utf-8") as f:
                state = json.load(f)
            state.get("group_chat_history", {}).pop(gid, None)
            with open(os.path.join(_USER_DATA_DIR, "qq_bot_state.json"), "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            self._log(f"已清除群 {gid} 的历史记录")
        except Exception as e:
            self._log(f"错误: {e}")

    def _cmd_clear_all_history(self, _=""):
        try:
            with open(os.path.join(_USER_DATA_DIR, "qq_bot_state.json"), encoding="utf-8") as f:
                state = json.load(f)
            state["group_chat_history"] = {}
            with open(os.path.join(_USER_DATA_DIR, "qq_bot_state.json"), "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            self._log("已清除所有历史记录")
        except Exception as e:
            self._log(f"错误: {e}")

    def _cmd_clear_learning(self, _=""):
        db_path = os.path.join(SCRIPT_DIR, "self_learning_db", "self_learning.db")
        if os.path.exists(db_path):
            os.remove(db_path)
            self._log("自主学习数据库已删除")
        else:
            self._log("数据库不存在")

    def _cmd_clear_private(self, qq):
        if not qq:
            self._log("用法: /clear_private <QQ号>")
            return
        try:
            # T8: 私聊记忆已迁入 super_memory SQLite
            import sqlite3
            db_path = os.path.join(SCRIPT_DIR, "plugins", "ego", "super_memory", "data", "super_memory.db")
            if os.path.exists(db_path):
                conn = sqlite3.connect(db_path)
                for tbl in ("messages", "facts", "events", "persona_kernel", "affection"):
                    try:
                        conn.execute(f"DELETE FROM {tbl} WHERE scope_id=? OR scope=?", (str(qq), "private"))
                    except Exception:
                        pass
                conn.commit()
                conn.close()
                self._log(f"已清除 {qq} 的私聊记忆")
        except Exception as e:
            self._log(f"错误: {e}")

    def _cmd_learning_status(self, _=""):
        try:
            from self_learning import get_engine
            engine = get_engine()
            status = engine.get_status()
            self._log(f"总消息: {status.get('total_messages', 0)}")
            self._log(f"表达模式: {status.get('expressions', 0)}")
        except Exception as e:
            self._log(f"读取失败: {e}")

    def _cmd_restart(self, _=""):
        self._log("正在重启Bot...")
        try:
            self.app.stop_bot()
            time.sleep(2)
            self.app.start_bot()
            self._log("Bot已重启")
        except Exception as e:
            self._log(f"重启失败: {e}")

    def _cmd_reload(self, _=""):
        try:
            import urllib.request
            req = urllib.request.Request("http://127.0.0.1:3001/reload")
            urllib.request.urlopen(req, timeout=5)
            self._log("配置已重载")
        except Exception as e:
            self._log(f"重载失败: {e}")

    def _learning_status(self): self._cmd_learning_status()
    def _view_group_history(self): self._cmd_groups()
    def _view_private_memory(self):
        try:
            # T8: 私聊记忆已迁入 super_memory SQLite
            import sqlite3
            db_path = os.path.join(SCRIPT_DIR, "plugins", "ego", "super_memory", "data", "super_memory.db")
            if os.path.exists(db_path):
                conn = sqlite3.connect(db_path)
                n = conn.execute("SELECT COUNT(DISTINCT scope_id) FROM messages WHERE scope='private'").fetchone()[0]
                conn.close()
                self._log(f"私聊记忆(超级记忆): {n} 个用户")
                return
            self._log("super_memory 数据库不存在")
        except Exception as e:
            self._log(f"错误: {e}")
    def _restart_bot(self): self._cmd_restart()


# ============================================================================
# PluginPanel
# ============================================================================

class PluginPanel(ctk.CTkFrame):
    def __init__(self, parent, app, **kwargs):
        super().__init__(parent, fg_color=C["bg_root"], **kwargs)
        self.app = app
        self._plugins = []
        self._state_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins", "plugin_state.json")
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        _build_header(self, "插件库", "自识别 · 自管理 · 自开关")

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill=tk.X, padx=12, pady=(8, 4))

        self.status_lbl = ctk.CTkLabel(toolbar, text="就绪", font=font(10),
                                       text_color=C["text_muted"])
        self.status_lbl.pack(side=tk.LEFT)

        ctk.CTkButton(toolbar, text="刷新", font=font(10),
                      fg_color=C["bg_card"], hover_color=C["hover_bg"],
                      text_color=C["text_secondary"], command=self.refresh,
                      width=60, height=28, corner_radius=4).pack(side=tk.RIGHT)

        # 滚动容器
        self._scroll = ctk.CTkScrollableFrame(self, fg_color=C["bg_root"],
                                               scrollbar_fg_color=C["bg_root"],
                                               scrollbar_button_color=C["border_light"],
                                               scrollbar_button_hover_color=C["text_muted"])
        self._scroll.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))

    def refresh(self):
        for w in self._scroll.winfo_children():
            w.destroy()

        plugins = []
        try:
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from plugins.plugin_manager import init_plugin_manager
            pm = init_plugin_manager()
            pm.load_all()
            plugins = pm.list_plugins()
            self.status_lbl.configure(text=f"已发现 {len(plugins)} 个插件" if plugins else "无可用插件",
                                      text_color=C["text_secondary"] if plugins else C["text_muted"])
        except Exception as e:
            self.status_lbl.configure(text=f"插件系统未初始化: {e}", text_color=C["accent_danger"])

        if not plugins:
            ctk.CTkLabel(self._scroll, text="暂无可用插件", font=font(14),
                         text_color=C["text_muted"]).pack(pady=40)
            ctk.CTkLabel(self._scroll, text="将插件放到 plugins/ 目录后点击刷新",
                         font=font(10), text_color=C["text_muted"]).pack()
            return

        for meta in plugins:
            self._build_plugin_card(meta)

    def _build_plugin_card(self, meta):
        name = meta.get("name", "?")
        version = meta.get("version", "?")
        description = meta.get("description", "")
        enabled = meta.get("enabled", True)
        error = meta.get("_error")

        card = ctk.CTkFrame(self._scroll, fg_color=C["bg_card"], corner_radius=8,
                            border_width=1, border_color=C["border_light"])
        card.pack(fill=tk.X, pady=4)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill=tk.X, padx=12, pady=(10, 4))

        ctk.CTkLabel(top, text=name, font=font(13, "bold"),
                     text_color=C["text_primary"]).pack(side=tk.LEFT)
        ctk.CTkLabel(top, text=f"v{version}", font=font(9),
                     text_color=C["text_muted"]).pack(side=tk.LEFT, padx=(8, 0))

        # 状态 + 开关
        status_color = C["accent_danger"] if error else (C["accent_success"] if enabled else C["text_muted"])
        status_text = "错误" if error else ("启用" if enabled else "禁用")
        ctk.CTkLabel(top, text=status_text, font=font(9, "bold"),
                     text_color=status_color).pack(side=tk.LEFT, padx=(12, 0))

        toggle = ctk.CTkSwitch(top, text="", onvalue=True, offvalue=False,
                               command=lambda: self._toggle_plugin(name, enabled),
                               fg_color=C["border_light"], progress_color=C["accent"],
                               button_color=C["text_primary"], button_hover_color=C["accent_hover"])
        toggle.pack(side=tk.RIGHT)
        if enabled:
            toggle.select()

        ctk.CTkLabel(card, text=description, font=font(10),
                     text_color=C["text_secondary"], wraplength=600,
                     justify="left").pack(anchor="w", padx=12, pady=(0, 10))

        if error:
            ctk.CTkLabel(card, text=f"错误: {error[:200]}", font=font(9),
                         text_color=C["accent_danger"], wraplength=600,
                         justify="left").pack(anchor="w", padx=12, pady=(0, 8))

    def _toggle_plugin(self, name, was_enabled):
        try:
            from plugins.plugin_manager import init_plugin_manager
            pm = init_plugin_manager()
            if was_enabled:
                pm.disable(name)
            else:
                pm.enable(name)
            self.refresh()
        except Exception as e:
            print(f"[PluginPanel] toggle 失败: {e}")


# ============================================================================
# MainWindow
# ============================================================================

class MainWindow:
    def __init__(self, root):
        self.root = root
        self.root.title("EggBloom v3 - QQ Bot Manager")
        self.root.geometry("1400x900")
        self.root.minsize(1100, 700)

        # 配置 ttk
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TCombobox",
                        fieldbackground=C["bg_input"], background=C["bg_card"],
                        foreground=C["text_primary"], selectbackground=C["selected_bg"],
                        borderwidth=0, padding=4)

        # 加载配置
        from config import load_config
        self.cfg = load_config()

        _apply_selected_theme(self.cfg.get("ui_theme", "default"))
        mode = "Light" if self.cfg.get("ui_theme") == "light" else "Dark"
        try:
            ctk.set_appearance_mode(mode)
        except Exception:
            pass

        # 单实例保护
        self._acquire_single_instance()

        # 首次配置引导
        self._ensure_first_run()

        # 状态
        self.running = False
        self.process = None
        self._stopping = False
        self._current_page = "dashboard"
        self._start_time: Optional[datetime] = None
        self._last_net = None
        self._last_net_time = 0
        self._psutil_proc = None

        try:
            import psutil
            psutil.cpu_percent(interval=None)
        except ImportError:
            pass

        self._build_layout()
        self._init_shortcuts()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(1000, self._init_tray)
        self.root.after(1000, self._bot_watchdog)
        self.root.after(5000, self._dashboard_tick)
        Animator.start_tick(self.root)

    def _init_shortcuts(self):
        self.shortcuts = ShortcutManager(self.root)
        self.shortcuts.register("ctrl+s", self._save_settings, "保存配置")
        self.shortcuts.register("ctrl+l", lambda: self.log_panel._clear(), "清空日志")
        self.shortcuts.register("ctrl+1", lambda: self.switch_page("dashboard"), "仪表盘")
        self.shortcuts.register("ctrl+2", lambda: self.switch_page("settings"), "设置")
        self.shortcuts.register("ctrl+3", lambda: self.switch_page("log"), "日志")
        self.shortcuts.register("ctrl+4", lambda: self.switch_page("chat"), "控制台")
        self.shortcuts.register("ctrl+5", lambda: self.switch_page("plugins"), "插件库")
        self.shortcuts.register("f5", lambda: self._dashboard_tick(), "刷新")

    def _on_close(self):
        try:
            mgr = getattr(self, '_protocol_manager', None)
            if mgr:
                mgr.stop()
        except Exception:
            pass
        if self.running:
            self._stop_bot()
        self.root.destroy()

    def _init_tray(self):
        try:
            from pystray import Icon, Menu, MenuItem
            from PIL import Image, ImageDraw

            image = Image.new('RGB', (64, 64), color='#2d2d2d')
            draw = ImageDraw.Draw(image)
            draw.ellipse([16, 16, 48, 48], fill='#98C379')

            def on_show(icon, item):
                self.root.after(0, self._restore_window)

            def on_start(icon, item):
                self.root.after(0, self.start_bot)

            def on_stop(icon, item):
                self.root.after(0, self.stop_bot)

            def on_quit(icon, item):
                self.root.after(0, self._quit_from_tray)

            menu = Menu(
                MenuItem('显示窗口', on_show),
                MenuItem('启动 Bot', on_start),
                MenuItem('停止 Bot', on_stop),
                MenuItem('退出', on_quit)
            )

            icon = Icon('EggBloom', image, 'EggBloom Desktop', menu)
            threading.Thread(target=icon.run, daemon=True).start()
            self._tray_icon = icon
        except Exception as e:
            self._append_log(f"托盘初始化失败: {e}", "WARN")

    def _restore_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _quit_from_tray(self):
        try:
            if self.running:
                self._stop_bot()
        except Exception:
            pass
        try:
            mgr = getattr(self, '_protocol_manager', None)
            if mgr:
                mgr.stop()
        except Exception:
            pass
        try:
            if getattr(self, '_tray_icon', None):
                self._tray_icon.stop()
        except Exception:
            pass
        self.root.destroy()

    def _acquire_single_instance(self):
        try:
            self._lock_path = os.path.join(_USER_DATA_DIR, "eggbloom.lock")
            self._lock_pid = str(os.getpid())
            if os.path.exists(self._lock_path):
                try:
                    with open(self._lock_path, "r", encoding="utf-8") as f:
                        existing_pid = f.read().strip()
                    if existing_pid and psutil.pid_exists(int(existing_pid)):
                        AppToast.show(self.root, "EggBloom Desktop 已在运行中", "warning")
                        raise SystemExit(0)
                except Exception:
                    pass
            with open(self._lock_path, "w", encoding="utf-8") as f:
                f.write(self._lock_pid)
        except Exception:
            raise SystemExit(0)

    def _release_single_instance(self):
        try:
            if getattr(self, "_lock_path", None):
                try:
                    if os.path.exists(self._lock_path):
                        os.remove(self._lock_path)
                except Exception:
                    pass
        except Exception:
            pass
    def _ensure_first_run(self):
        required = ["api_key", "api_url", "model_name", "bot_qq"]
        missing = [k for k in required if not str(self.cfg.get(k, "")).strip()]
        if not missing:
            return
        try:
            from wizard import FirstRunWizard
            wiz = FirstRunWizard(self.root, self.cfg)
            self.root.wait_window(wiz)
            data = getattr(wiz, "result", None) or {}
        except Exception:
            return
        if not data:
            return
        self.cfg.update(data)
        try:
            from config import save_config, save_env
            save_config(self.cfg, save_api_key=False)
            save_env(api_key=data.get("api_key", ""), api_url=data.get("api_url", ""), model=data.get("model_name", ""))
        except Exception:
            pass

    def _build_layout(self):
        # 顶部导航
        top_bar = ctk.CTkFrame(self.root, fg_color=C["bg_sidebar"], height=50, corner_radius=0)
        top_bar.pack(fill=tk.X)
        top_bar.pack_propagate(False)

        logo_frame = ctk.CTkFrame(top_bar, fg_color="transparent")
        logo_frame.pack(side=tk.LEFT, padx=16)
        ctk.CTkLabel(logo_frame, text="EggBloom", font=font(18, "bold"),
                     text_color=C["text_primary"]).pack(side=tk.LEFT)
        ctk.CTkLabel(logo_frame, text=" v3", font=font(12),
                     text_color=C["text_muted"]).pack(side=tk.LEFT, padx=(4, 0))

        self.clock_lbl = ctk.CTkLabel(top_bar, text="", font=font(11),
                                      text_color=C["text_muted"])
        self.clock_lbl.pack(side=tk.RIGHT, padx=16)
        self._tick()

        # 状态栏
        status_bar = ctk.CTkFrame(self.root, fg_color=C["bg_root"], height=36, corner_radius=0)
        status_bar.pack(fill=tk.X)
        status_bar.pack_propagate(False)

        # 状态指示灯
        self.dot = StatusDot(status_bar, size=8)
        self.dot.pack(side=tk.LEFT, padx=(16, 8), pady=11)

        self.status_lbl = ctk.CTkLabel(status_bar, text="就绪", font=font(11),
                                       text_color=C["text_muted"])
        self.status_lbl.pack(side=tk.LEFT, pady=8)

        self.bot_info_lbl = ctk.CTkLabel(status_bar, text="Bot: --", font=font(10),
                                         text_color=C["text_muted"])
        self.bot_info_lbl.pack(side=tk.LEFT, padx=(16, 0), pady=8)

        self.toggle_btn = ctk.CTkButton(status_bar, text="启动", font=font(11, "bold"),
                                        fg_color=C["accent_success"],
                                        hover_color=_lerp(C["accent_success"], 0.15),
                                        command=self._toggle_bot, width=80, height=28,
                                        corner_radius=6)
        self.toggle_btn.pack(side=tk.RIGHT, padx=16, pady=4)

        # 主体
        body = ctk.CTkFrame(self.root, fg_color=C["bg_root"], corner_radius=0)
        body.pack(fill=tk.BOTH, expand=True)

        # 左侧导航
        sidebar = ctk.CTkFrame(body, fg_color=C["bg_sidebar"], width=120, corner_radius=0)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)

        self._nav_widgets = {}
        nav_items = [
            ("dashboard", "首页"),
            ("settings", "设置"),
            ("log", "日志"),
            ("chat", "控制台"),
            ("protocol", "协议"),
            ("plugins", "插件"),
            ("imagegen", "生图"),
            ("webui", "WebUI"),
            ("help", "帮助"),
        ]

        for key, label in nav_items:
            row = ctk.CTkFrame(sidebar, fg_color="transparent", height=40)
            row.pack(fill=tk.X, padx=8, pady=1)
            row.pack_propagate(False)
            indicator = ctk.CTkFrame(row, fg_color="transparent", width=4, height=24)
            indicator.place(x=0, rely=0.5, anchor="w")
            btn = ctk.CTkButton(row, text=label, font=font(12),
                                fg_color="transparent", hover_color=C["hover_bg"],
                                text_color=C["text_secondary"],
                                command=lambda k=key: self.switch_page(k),
                                height=40, corner_radius=6, anchor="w")
            btn.pack(fill=tk.BOTH, expand=True)
            self._nav_widgets[key] = (btn, indicator)

        # 内容区
        self.content = ctk.CTkFrame(body, fg_color=C["bg_root"], corner_radius=0)
        self.content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.pages = {}
        self._create_pages()
        self.switch_page("dashboard")

    def _create_pages(self):
        self.dashboard = DashboardPanel(self.content, self)
        self.pages["dashboard"] = self.dashboard

        # 设置页面
        self.settings_page = ctk.CTkFrame(self.content, fg_color=C["bg_root"])
        self._build_settings_page()
        self.pages["settings"] = self.settings_page

        # 日志
        self.log_panel = LogPanel(self.content, self)
        self.pages["log"] = self.log_panel

        # 控制台
        self.console_panel = ConsolePanel(self.content, self)
        self.pages["chat"] = self.console_panel

        # 插件
        self.plugin_panel = PluginPanel(self.content, self)
        self.pages["plugins"] = self.plugin_panel

        # 协议层
        try:
            from protocol_panel import ProtocolPanel
            self.protocol_panel = ProtocolPanel(self.content, self)
            self.pages["protocol"] = self.protocol_panel
        except Exception as e:
            self._append_log(f"协议面板加载失败: {e}", "WARN")

        # 生图
        try:
            from image_gen_panel import ImageGenPanel
            self.imagegen_panel = ImageGenPanel(self.content, self)
            self.pages["imagegen"] = self.imagegen_panel
        except Exception as e:
            self._append_log(f"生图面板加载失败: {e}", "WARN")

        # WebUI
        self.webui_page = ctk.CTkFrame(self.content, fg_color=C["bg_root"])
        self._build_webui_page()
        self.pages["webui"] = self.webui_page

        # 帮助
        help_page = ctk.CTkFrame(self.content, fg_color=C["bg_root"])
        scroll = ctk.CTkScrollableFrame(help_page, fg_color=C["bg_root"],
                                        scrollbar_fg_color=C["bg_root"],
                                        scrollbar_button_color=C["border_light"])
        scroll.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        try:
            base = os.path.dirname(os.path.abspath(__file__))
            lang = self.cfg.get("ui_language", "zh")
            candidate = os.path.join(base, "docs", f"USAGE_{lang}.md")
            if not os.path.exists(candidate):
                candidate = os.path.join(base, "docs", "USAGE.md")
            with open(candidate, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            text = _t("help") + "\n\n" + "使用说明文档缺失"
        label = ctk.CTkLabel(scroll, text=text, font=font(11),
                             text_color=C["text_primary"], anchor="nw", justify="left")
        label.pack(fill=tk.BOTH, expand=True)
        self.pages["help"] = help_page

    def _build_settings_page(self):
        f = self.settings_page

        # 滚动容器
        scroll = ctk.CTkScrollableFrame(f, fg_color=C["bg_root"],
                                        scrollbar_fg_color=C["bg_root"],
                                        scrollbar_button_color=C["border_light"])
        scroll.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        def _card(title, rows):
            c = ctk.CTkFrame(scroll, fg_color=C["bg_card"], corner_radius=8,
                             border_width=1, border_color=C["border_light"])
            c.pack(fill=tk.X, pady=(0, 8))
            ctk.CTkLabel(c, text=title, font=font(12, "bold"),
                         text_color=C["text_primary"]).pack(anchor="w", padx=12, pady=(10, 6))
            for label, widget_factory in rows:
                row = ctk.CTkFrame(c, fg_color="transparent")
                row.pack(fill=tk.X, padx=12, pady=2)
                ctk.CTkLabel(row, text=label, font=font(11),
                             text_color=C["text_secondary"], width=120,
                             anchor="w").pack(side=tk.LEFT)
                widget_factory(row)

        def _entry(attr, show=None):
            def factory(parent):
                w = ctk.CTkEntry(parent, font=font(11), fg_color=C["bg_input"],
                                 border_color=C["border_light"], text_color=C["text_primary"],
                                 corner_radius=6, show=show or "")
                w.pack(side=tk.LEFT, fill=tk.X, expand=True)
                setattr(self, f"_ent_{attr}", w)
            return factory

        def _spin(attr, frm, to):
            def factory(parent):
                v = tk.IntVar()
                setattr(self, f"_var_{attr}", v)
                w = ctk.CTkEntry(parent, font=font(11), fg_color=C["bg_input"],
                                 border_color=C["border_light"], text_color=C["text_primary"],
                                 corner_radius=6, width=80)
                w.insert(0, str(frm))
                w.pack(side=tk.LEFT)
                # 用 CTkEntry 模拟 Spinbox（CTk 没有原生 Spinbox）
                setattr(self, f"_spin_{attr}", w)
            return factory

        def _textarea(attr, h=3):
            def factory(parent):
                t = ctk.CTkTextbox(parent, font=font(10), fg_color=C["bg_input"],
                                   text_color=C["text_primary"], height=h * 20,
                                   corner_radius=6, border_width=1,
                                   border_color=C["border_light"])
                t.pack(side=tk.LEFT, fill=tk.X, expand=True)
                setattr(self, f"_ent_{attr}", t)
            return factory

        # API 供应商
        API_PROVIDERS = {
            "deepseek": "DeepSeek", "openai": "OpenAI", "custom": "自定义",
        }

        def _provider_dd(parent):
            self._provider_var = ctk.StringVar(value=self.cfg.get("api_provider", "deepseek"))
            w = ctk.CTkOptionMenu(parent, variable=self._provider_var,
                                  values=list(API_PROVIDERS.keys()),
                                  font=font(10), fg_color=C["bg_input"],
                                  button_color=C["border_light"],
                                  dropdown_fg_color=C["bg_card"],
                                  width=160)
            w.pack(side=tk.LEFT, fill=tk.X, expand=True)

        def _model_dd(parent):
            self._model_var = ctk.StringVar(value=self.cfg.get("model_name", ""))
            w = ctk.CTkOptionMenu(parent, variable=self._model_var,
                                  values=["deepseek-flash", "deepseek-v4-pro"],
                                  font=font(10), fg_color=C["bg_input"],
                                  button_color=C["border_light"],
                                  dropdown_fg_color=C["bg_card"],
                                  width=160)
            w.pack(side=tk.LEFT, fill=tk.X, expand=True)

        _card("OneBot 连接", [
            ("地址", _entry("onebot")),
            ("Bot QQ", _entry("bot_qq")),
        ])
        _card("AI 供应商", [
            ("供应商", _provider_dd),
            ("API 地址", _entry("api_url")),
            ("模型", _model_dd),
            ("API 密钥", _entry("api_key", show="*")),
        ])
        _card("监听设置", [
            ("目标 QQ", _textarea("target_qqs", 3)),
            ("群号", _textarea("group_ids", 2)),
        ])
        _card("行为设置", [
            ("群昵称", _entry("bot_nick")),
            ("开机冷却(s)", _entry("cooldown")),
            ("分割长度", _entry("split_len")),
            ("会话超时(s)", _entry("session_ttl")),
            ("最大轮数", _entry("session_max_rounds")),
            ("表情冷却(s)", _entry("meme_cooldown")),
            ("AI 温度", _entry("ai_temperature")),
            ("最大 Token", _entry("ai_max_tokens")),
        ])

        # 操作按钮
        btn_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        btn_frame.pack(fill=tk.X, pady=(8, 0))
        ctk.CTkButton(btn_frame, text="保存配置", font=font(12, "bold"),
                      fg_color=C["accent_success"], hover_color=_lerp(C["accent_success"], 0.15),
                      command=self._save_settings, height=36, corner_radius=6).pack(side=tk.LEFT, padx=(0, 8))
        ctk.CTkButton(btn_frame, text="重置默认", font=font(11),
                      fg_color=C["bg_card"], hover_color=C["hover_bg"],
                      text_color=C["text_secondary"],
                      command=self._reset_settings, height=36, corner_radius=6).pack(side=tk.LEFT)

        # 界面设置
        ui_card = ctk.CTkFrame(scroll, fg_color=C["bg_card"], corner_radius=8,
                               border_width=1, border_color=C["border_light"])
        ui_card.pack(fill=tk.X, pady=(0, 8))
        ctk.CTkLabel(ui_card, text="界面", font=font(12, "bold"),
                     text_color=C["text_primary"]).pack(anchor="w", padx=12, pady=(10, 6))

        theme_row = ctk.CTkFrame(ui_card, fg_color="transparent")
        theme_row.pack(fill=tk.X, padx=12, pady=2)
        ctk.CTkLabel(theme_row, text="主题", font=font(11),
                     text_color=C["text_secondary"], width=120, anchor="w").pack(side=tk.LEFT)
        self._theme_var = ctk.StringVar(value=self.cfg.get("ui_theme", "default"))
        theme_dd = ctk.CTkOptionMenu(theme_row, variable=self._theme_var,
                                     values=["default", "light", "warm"],
                                     font=font(10), fg_color=C["bg_input"],
                                     button_color=C["border_light"],
                                     dropdown_fg_color=C["bg_card"], width=160)
        theme_dd.pack(side=tk.LEFT, fill=tk.X, expand=True)

        lang_row = ctk.CTkFrame(ui_card, fg_color="transparent")
        lang_row.pack(fill=tk.X, padx=12, pady=(2, 10))
        ctk.CTkLabel(lang_row, text="语言", font=font(11),
                     text_color=C["text_secondary"], width=120, anchor="w").pack(side=tk.LEFT)
        self._lang_var = ctk.StringVar(value=self.cfg.get("ui_language", "zh"))
        lang_dd = ctk.CTkOptionMenu(lang_row, variable=self._lang_var,
                                    values=["zh", "en"],
                                    font=font(10), fg_color=C["bg_input"],
                                    button_color=C["border_light"],
                                    dropdown_fg_color=C["bg_card"], width=160)
        lang_dd.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._load_to_ui()

    def _load_to_ui(self):
        c = self.cfg
        for attr, cfg_key in [("onebot", "onebot_url"), ("bot_qq", "bot_qq"),
                              ("api_url", "api_url"), ("api_key", "api_key"),
                              ("bot_nick", "bot_group_nick")]:
            ent = getattr(self, f"_ent_{attr}", None)
            if ent:
                ent.delete(0, "end")
                ent.insert(0, c.get(cfg_key, ""))

        for attr in ["target_qqs", "group_ids"]:
            t = getattr(self, f"_ent_{attr}", None)
            if t:
                t.configure(state="normal")
                t.delete("1.0", "end")
                for item in c.get(attr, []):
                    t.insert("end", str(item) + "\n")

        defaults = {"startup_cooldown": 30, "msg_split_len": 2000,
                    "session_ttl": 300, "session_max_rounds": 30,
                    "meme_cooldown": 60, "ai_max_tokens": 800, "ai_temperature": 0.7}
        for attr, cfg_key in [("cooldown", "startup_cooldown"), ("split_len", "msg_split_len"),
                              ("session_ttl", "session_ttl"), ("session_max_rounds", "session_max_rounds"),
                              ("meme_cooldown", "meme_cooldown"), ("ai_temperature", "ai_temperature"),
                              ("ai_max_tokens", "ai_max_tokens")]:
            ent = getattr(self, f"_ent_{attr}", None)
            if ent:
                ent.delete(0, "end")
                ent.insert(0, str(c.get(cfg_key, defaults.get(cfg_key, 0))))

        self.bot_info_lbl.configure(text=f"Bot: {c.get('bot_qq', '---')}")

    def _save_settings(self):
        from config import save_config, save_env

        tqq = []
        t = getattr(self, "_ent_target_qqs", None)
        if t:
            tqq = [l.strip() for l in t.get("1.0", "end").splitlines() if l.strip()]

        gid = []
        t = getattr(self, "_ent_group_ids", None)
        if t:
            gid = [l.strip() for l in t.get("1.0", "end").splitlines() if l.strip()]

        def _get_val(attr, default=""):
            ent = getattr(self, f"_ent_{attr}", None)
            if ent:
                return ent.get().strip()
            return default

        self.cfg = {
            "onebot_url": _get_val("onebot"),
            "bot_qq": _get_val("bot_qq"),
            "target_qqs": tqq, "group_ids": gid,
            "api_provider": self._provider_var.get() if hasattr(self, '_provider_var') else "deepseek",
            "api_url": _get_val("api_url"),
            "api_key": _get_val("api_key"),
            "model_name": self._model_var.get() if hasattr(self, '_model_var') else "",
            "bot_group_nick": _get_val("bot_nick"),
            "startup_cooldown": int(_get_val("cooldown", "30")),
            "msg_split_len": int(_get_val("split_len", "2000")),
            "session_ttl": int(_get_val("session_ttl", "300")),
            "session_max_rounds": int(_get_val("session_max_rounds", "30")),
            "meme_cooldown": int(_get_val("meme_cooldown", "60")),
            "ai_temperature": float(_get_val("ai_temperature", "0.7")),
            "ai_max_tokens": int(_get_val("ai_max_tokens", "800")),
            "protocol_enabled": self.cfg.get("protocol_enabled", False),
            "ui_theme": self._theme_var.get() if hasattr(self, '_theme_var') else self.cfg.get("ui_theme", "default"),
            "ui_language": self._lang_var.get() if hasattr(self, '_lang_var') else self.cfg.get("ui_language", "zh"),
        }

        save_config(self.cfg, save_api_key=False)
        save_env(api_key=self.cfg["api_key"], api_url=self.cfg["api_url"], model=self.cfg["model_name"])
        self.bot_info_lbl.configure(text=f"Bot: {self.cfg.get('bot_qq', '---')}")
        _apply_selected_theme(self.cfg.get("ui_theme", "default"))
        mode = "Light" if self.cfg.get("ui_theme") == "light" else "Dark"
        try:
            ctk.set_appearance_mode(mode)
        except Exception:
            pass
        self._append_log("设置已保存", "SUCCESS")

        if self.running:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(3)
                try:
                    s.connect(("127.0.0.1", 3001))
                    s.sendall(b"POST /reload_config HTTP/1.1\r\nHost: 127.0.0.1:3001\r\nContent-Type: application/json\r\nContent-Length: 0\r\n\r\n")
                    resp = s.recv(4096).decode("utf-8", errors="replace")
                    if "200" in resp:
                        self._append_log("配置已热更新", "SUCCESS")
                        return
                finally:
                    s.close()
            except Exception:
                pass
            # bot_core 事件端口(3005)为新式热重载通道；旧 3001 通道为占位未实现
            try:
                s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s2.settimeout(3)
                try:
                    s2.connect(("127.0.0.1", 3005))
                    s2.sendall(b"POST /reload_config HTTP/1.1\r\nHost: 127.0.0.1:3005\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}")
                    resp2 = s2.recv(4096).decode("utf-8", errors="replace")
                    if '"ok":true' in resp2.replace(' ', '') or '"ok": true' in resp2:
                        self._append_log("配置已热更新", "SUCCESS")
                        return
                finally:
                    s2.close()
            except Exception:
                pass
            self._append_log("热更新失败，正在重启 Bot...", "SYSTEM")
            self._stop_bot()
            self._stopping = False
            self.start_bot()

    def _reset_settings(self):
        from config import DEFAULT_CONFIG, save_config
        self.cfg = DEFAULT_CONFIG.copy()
        self._load_to_ui()
        save_config(self.cfg)
        self._append_log("设置已重置", "SYSTEM")

    def _build_webui_page(self):
        f = self.webui_page
        scroll = ctk.CTkScrollableFrame(f, fg_color=C["bg_root"],
                                         scrollbar_fg_color=C["bg_root"],
                                         scrollbar_button_color=C["border_light"])
        scroll.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        # 标题
        ctk.CTkLabel(scroll, text="WebUI 管理后台", font=font(16, "bold"),
                     text_color=C["text_primary"]).pack(anchor="w", padx=12, pady=(0, 8))
        ctk.CTkLabel(scroll, text="基于浏览器的现代化管理界面，提供仪表盘、监控、日志、插件管理等功能",
                     font=font(11), text_color=C["text_secondary"]).pack(anchor="w", padx=12, pady=(0, 16))

        # 状态卡片
        status_card = ctk.CTkFrame(scroll, fg_color=C["bg_card"], corner_radius=8,
                                   border_width=1, border_color=C["border_light"])
        status_card.pack(fill=tk.X, padx=12, pady=(0, 12))
        self._webui_status_lbl = ctk.CTkLabel(status_card, text="状态: 未启动",
                                               font=font(11), text_color=C["text_muted"])
        self._webui_status_lbl.pack(anchor="w", padx=12, pady=(10, 4))
        self._webui_port_lbl = ctk.CTkLabel(status_card, text=f"端口: {_get_webui_port()}",
                                              font=font(10), text_color=C["text_muted"])
        self._webui_port_lbl.pack(anchor="w", padx=12, pady=(0, 10))

        # 操作按钮
        btn_frame = ctk.CTkFrame(status_card, fg_color="transparent")
        btn_frame.pack(fill=tk.X, padx=12, pady=(0, 10))

        self._webui_start_btn = ctk.CTkButton(btn_frame, text="启动 WebUI",
                                                font=font(12, "bold"),
                                                fg_color=C["accent"],
                                                hover_color=C["accent_hover"],
                                                text_color=C["bg_root"],
                                                width=160, height=36,
                                                command=self._start_webui)
        self._webui_start_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._webui_open_btn = ctk.CTkButton(btn_frame, text="打开浏览器",
                                               font=font(12, "bold"),
                                               fg_color=C["accent_info"],
                                               hover_color="#1a5276",
                                               text_color="#FFFFFF",
                                               width=160, height=36,
                                               command=self._open_webui)
        self._webui_open_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._webui_stop_btn = ctk.CTkButton(btn_frame, text="停止 WebUI",
                                               font=font(12, "bold"),
                                               fg_color=C["accent_danger"],
                                               hover_color="#922B21",
                                               text_color="#FFFFFF",
                                               width=160, height=36,
                                               command=self._stop_webui)
        self._webui_stop_btn.pack(side=tk.LEFT)

        # 功能说明
        info_card = ctk.CTkFrame(scroll, fg_color=C["bg_card"], corner_radius=8,
                                  border_width=1, border_color=C["border_light"])
        info_card.pack(fill=tk.X, padx=12, pady=(0, 12))
        ctk.CTkLabel(info_card, text="功能", font=font(12, "bold"),
                     text_color=C["text_primary"]).pack(anchor="w", padx=12, pady=(10, 6))
        features = [
            "仪表盘 - 消息统计、AI调用、图片生成趋势",
            "系统监控 - CPU/Memory/GPU 实时图表",
            "消息日志 - 实时聊天日志查看",
            "图像库 - 生成图片浏览",
            "记忆引擎 - 自学习数据可视化",
            "插件管理 - 启用/禁用/查看状态",
            "配置中心 - 在线编辑机器人参数",
            "远程控制 - 指令执行终端",
        ]
        for feat in features:
            ctk.CTkLabel(info_card, text=f"• {feat}", font=font(10),
                         text_color=C["text_secondary"]).pack(anchor="w", padx=16, pady=1)
        ctk.CTkLabel(info_card, text="", height=6).pack()

        # WebUI 进程引用
        self._webui_process = None

    def _start_webui(self):
        if self._webui_process and self._webui_process.poll() is None:
            self._append_log("WebUI 已在运行", "WARN")
            return
        webui_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui")
        server_py = os.path.join(webui_dir, "webui_server.py")
        if not os.path.exists(server_py):
            self._append_log(f"WebUI 服务器文件不存在: {server_py}", "ERROR")
            return
        try:
            self._webui_process = subprocess.Popen(
                [sys.executable, server_py],
                cwd=webui_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            self._webui_status_lbl.configure(text="状态: 启动中...", text_color=C["accent_warning"])
            self._append_log(f"WebUI 服务器已启动 (http://localhost:{_get_webui_port()})", "SUCCESS")
            # 延迟检查状态
            self.root.after(2000, self._check_webui_status)
        except Exception as e:
            self._append_log(f"WebUI 启动失败: {e}", "ERROR")

    def _check_webui_status(self):
        if self._webui_process and self._webui_process.poll() is None:
            self._webui_status_lbl.configure(text="状态: 运行中", text_color=C["accent_success"])
        else:
            self._webui_status_lbl.configure(text="状态: 已停止", text_color=C["accent_danger"])

    def _open_webui(self):
        import webbrowser
        webbrowser.open(f"http://localhost:{_get_webui_port()}")

    def _stop_webui(self):
        if self._webui_process and self._webui_process.poll() is None:
            self._webui_process.terminate()
            self._webui_process.wait(timeout=5)
            self._webui_process = None
            self._webui_status_lbl.configure(text="状态: 已停止", text_color=C["text_muted"])
            self._append_log("WebUI 服务器已停止", "SYSTEM")
        else:
            self._append_log("WebUI 未在运行", "WARN")

    def switch_page(self, key):
        if self._current_page == key:
            return
        self._current_page = key
        for k, (btn, indicator) in self._nav_widgets.items():
            if k == key:
                btn.configure(fg_color="#2A3A4A", text_color="#FFFFFF")
                indicator.configure(fg_color=C["accent"])
            else:
                btn.configure(fg_color="transparent", text_color=C["text_muted"])
                indicator.configure(fg_color="transparent")

        for page_key, page in self.pages.items():
            if page_key == key:
                page.pack(fill=tk.BOTH, expand=True)
                if page_key == "chat" and hasattr(page, 'cmd_input'):
                    self.root.after(120, lambda p=page: p.cmd_input.focus_force())
            else:
                page.pack_forget()
        self.content.update_idletasks()

    def _bot_watchdog(self):
        try:
            if self.running and self.process and self.process.poll() is not None:
                ec = self.process.returncode
                self.running = False
                self.process = None
                self._append_log(f"Bot 异常退出 (code={ec})，正在重启...", "ERROR")
                self.dashboard.update_stats({"bot_status": "异常退出"})
                try:
                    self.start_bot()
                except Exception as e:
                    self._append_log(f"自动重启失败: {e}", "ERROR")
        except Exception:
            pass
        self.root.after(1000, self._bot_watchdog)

    def _toggle_bot(self):
        if self.running:
            self._stop_bot()
        else:
            self.start_bot()

    def start_bot(self):
        if self.running or self._stopping or getattr(self, "_starting", False):
            return
        self._starting = True
        self._save_settings()

        try:
            if not self.cfg.get("api_key"):
                AppToast.show(self.root, "API Key 不能为空", "error")
                return
            if not self.cfg.get("bot_qq"):
                AppToast.show(self.root, "Bot QQ 不能为空", "error")
                return

            if getattr(sys, "frozen", False):
                exe_dir = os.path.dirname(sys.executable)
                core_exe = os.path.join(exe_dir, "EggBloom_v3_Core.exe")
                if not os.path.exists(core_exe):
                    core_exe = os.path.join(exe_dir, "_internal", "EggBloom_v3_Core.exe")
                if not os.path.exists(core_exe):
                    AppToast.show(self.root, "未找到 Core 引擎", "error")
                    return
                script_dir = exe_dir
                cmd = [core_exe]
            else:
                script_dir = os.path.dirname(os.path.abspath(__file__))
                cmd = [sys.executable, os.path.join(script_dir, "bot_core.py")]

            if not os.path.exists(cmd[-1]):
                AppToast.show(self.root, f"Bot脚本不存在: {cmd[-1]}", "error")
                return

            self._append_log("正在启动 Bot...", "SYSTEM")
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"

            si = None
            if sys.platform == "win32":
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.SW_HIDE

            self.process = subprocess.Popen(
                cmd, cwd=script_dir, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )

            self.running = True
            self._start_time = datetime.now()
            self.toggle_btn.configure(text="停止", fg_color=C["accent_danger"])
            self.status_lbl.configure(text="运行中", text_color=C["accent_success"])
            self.dot.start_pulse()
            self._append_log("Bot 已启动", "SUCCESS")
            self.dashboard.update_stats({"bot_status": "运行中"})

            reader = threading.Thread(target=self._read_output, daemon=True)
            reader.start()
            self.root.after(1000, self._monitor)
            self.root.after(1000, self._dashboard_tick)

        except Exception as e:
            self._append_log(f"启动失败: {e}", "ERROR")
        finally:
            self._starting = False

    def _read_output(self):
        pending_logs = []
        pending_chats = []
        last_flush = time.time()
        self._pending_logs_ref = pending_logs
        self._pending_chats_ref = pending_chats
        self._last_flush_ref = [last_flush]

        try:
            for raw in iter(self.process.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip("\n\r")
                if not line:
                    continue

                if line.startswith("[CHAT]"):
                    try:
                        payload = json.loads(line[6:])
                        pending_chats.append(payload)
                        direction = payload.get("direction", "?")
                        chat_type = payload.get("type", "?")
                        text = payload.get("text", "")
                        sender = payload.get("sender", "")
                        pending_logs.append((f"[{direction}] [{chat_type}] {sender}: {text}", "INFO"))
                    except json.JSONDecodeError:
                        pass
                else:
                    upper = line.upper()
                    if "ERROR" in upper or "失败" in line:
                        tag = "ERROR"
                    elif "WARN" in upper or "冷却" in line:
                        tag = "WARN"
                    elif "群回复" in line or "AI 回复" in line:
                        tag = "SUCCESS"
                    elif "启动" in line or "连接" in line or "OneBot" in line:
                        tag = "SYSTEM"
                    else:
                        tag = "INFO"
                    pending_logs.append((line, tag))

                if len(pending_logs) + len(pending_chats) >= 10 or time.time() - self._last_flush_ref[0] > 0.5:
                    logs_batch = pending_logs[:]
                    chats_batch = pending_chats[:]
                    pending_logs.clear()
                    pending_chats.clear()
                    self._last_flush_ref[0] = time.time()
                    self.root.after(0, self._batch_update, logs_batch, chats_batch)

            if pending_logs or pending_chats:
                self.root.after(0, self._batch_update, pending_logs[:], pending_chats[:])
        except (ValueError, OSError):
            pass

    def _batch_update(self, logs_batch, chats_batch):
        if logs_batch:
            self.log_panel.log_text.configure(state="normal")
            for line, tag in logs_batch:
                formatted = f"[{datetime.now().strftime('%H:%M:%S')}] [{tag}] {line}\n"
                self.log_panel._logs.append((formatted, tag))
                if len(self.log_panel._logs) > 5000:
                    self.log_panel._logs = self.log_panel._logs[-3000:]
                self.log_panel.log_text.insert("end", formatted)
            self.log_panel.log_text.see("end")
            self.log_panel.log_text.configure(state="disabled")
            self.log_panel._refresh_counters()

            # 同步到 Dashboard
            for line, tag in logs_batch[-3:]:
                self.dashboard.append_log(line, tag)

    def _monitor(self):
        if hasattr(self, '_pending_logs_ref') and hasattr(self, '_last_flush_ref'):
            pending = self._pending_logs_ref
            chats = self._pending_chats_ref
            if pending or chats:
                logs_batch = pending[:]
                chats_batch = chats[:]
                pending.clear()
                chats.clear()
                self._last_flush_ref[0] = time.time()
                self.root.after(0, self._batch_update, logs_batch, chats_batch)

        if not self.running or self._stopping:
            return
        if self.process and self.process.poll() is not None:
            ec = self.process.returncode
            self.running = False
            self.process = None
            self.toggle_btn.configure(text="启动", fg_color=C["accent_success"])
            self.status_lbl.configure(text=f"已退出 (code={ec})", text_color=C["text_muted"])
            self.dot.stop_pulse()
            self._append_log(f"Bot 已退出 (code={ec})", "SYSTEM" if ec == 0 else "ERROR")
            self.dashboard.update_stats({"bot_status": "已停止"})
        else:
            self.root.after(1000, self._monitor)

    def _dashboard_tick(self):
        try:
            import psutil
            if self.running and self.process and self.process.poll() is None:
                if self._psutil_proc is None or self._psutil_proc.pid != self.process.pid:
                    self._psutil_proc = psutil.Process(self.process.pid)
                p = self._psutil_proc
                cpu = p.cpu_percent(interval=0.1)
                mem_mb = p.memory_info().rss / 1024 / 1024
                if self._start_time:
                    elapsed = datetime.now() - self._start_time
                    h, rem = divmod(int(elapsed.total_seconds()), 3600)
                    m, s = divmod(rem, 60)
                    self.dashboard.update_stats({"uptime": f"{h:02d}:{m:02d}:{s:02d}"})
                self.dashboard.update_stats({"memory": f"{mem_mb:.0f} MB"})

            now = time.time()
            net = psutil.net_io_counters()
            if self._last_net is not None and now - self._last_net_time > 0:
                dt = now - self._last_net_time
                rx = (net.bytes_recv - self._last_net.bytes_recv) / dt / 1024
                tx = (net.bytes_sent - self._last_net.bytes_sent) / dt / 1024
                self.dashboard.net_status_lbl.configure(text=f"网络: ↑{tx:.1f} ↓{rx:.1f} KB/s")
            self._last_net = net
            self._last_net_time = now
        except Exception:
            pass

        if not self.running:
            try:
                import urllib.request
                req = urllib.request.Request("http://127.0.0.1:3000/get_status")
                resp = urllib.request.urlopen(req, timeout=2)
                data = json.loads(resp.read().decode())
                if data.get("online"):
                    self.status_lbl.configure(text="Bot 已连接", text_color=C["accent_success"])
                    self.dot.set_color(C["accent_success"])
                else:
                    self.status_lbl.configure(text="Bot 已启动", text_color=C["accent_warning"])
                    self.dot.set_color(C["accent_warning"])
            except Exception:
                self.status_lbl.configure(text="未连接", text_color=C["text_muted"])
                self.dot.set_color(C["text_muted"])

        self.root.after(5000, self._dashboard_tick)

    def stop_bot(self):
        self._stop_bot()

    def _stop_bot(self):
        if self._stopping:
            return
        self._stopping = True

        try:
            if not self.process:
                self.running = False
                self._psutil_proc = None
                return

            self._append_log("正在停止 Bot...", "SYSTEM")
            graceful = False
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(3)
                try:
                    s.connect(("127.0.0.1", 3001))
                    s.sendall(b"POST /shutdown HTTP/1.1\r\nHost: 127.0.0.1:3001\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}")
                    s.recv(1024)
                finally:
                    s.close()
                try:
                    self.process.wait(timeout=8)
                    graceful = True
                    self._append_log("Bot 已优雅退出", "SUCCESS")
                except subprocess.TimeoutExpired:
                    self._append_log("优雅关闭超时，强制终止", "WARN")
            except Exception:
                pass

            if not graceful:
                try:
                    self.process.terminate()
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
                self._append_log("Bot 已强制终止", "WARN")

            self.process = None
            self.running = False

            try:
                subprocess.run(["taskkill", "/F", "/IM", "EggBloom_v3_Core.exe"],
                               capture_output=True, timeout=3)
                time.sleep(0.3)
            except Exception:
                pass

            self.toggle_btn.configure(text="启动", fg_color=C["accent_success"])
            self.status_lbl.configure(text="已停止", text_color=C["text_muted"])
            self.dot.stop_pulse()
            self.dashboard.update_stats({"bot_status": "已停止"})

        except Exception as e:
            self._append_log(f"停止时出错: {e}", "ERROR")
        finally:
            self._stopping = False

    def _append_log(self, text, tag="INFO"):
        self.log_panel.append(text, tag)

    def _tick(self):
        self.clock_lbl.configure(text=datetime.now().strftime("%H:%M:%S"))
        self.root.after(1000, self._tick)


if __name__ == "__main__":
    root = ctk.CTk()
    app = MainWindow(root)
    root.mainloop()
