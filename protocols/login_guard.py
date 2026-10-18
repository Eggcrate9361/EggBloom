# -*- coding: utf-8 -*-
"""protocols/login_guard.py — QQ 登录风控护栏

背景：NapCat / LLOneBot / SnowLuma 这类非官方通道每次启动/自动重启都会
重拉 QQ、重扫登录态；腾讯风控会把频繁异常登录的账号判冻结（已发生多账号冻结）。

本护栏提供：
- 冷却：距上次实际启动 < cooldown 秒 → 禁止自动重启（防反复扫码）
- 限流：时间窗内自动重启次数上限，超限停止自动重启并强警告
- 状态持久化（data/login_guard.json），进程/网页重启不重置冷却计数
"""

import json
import os
import threading
import time

_PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATE_DIR = os.path.join(_PROJECT, "data")
_STATE_FILE = os.path.join(_STATE_DIR, "login_guard.json")

# 默认参数（可由适配器构造时覆盖）
DEFAULT_COOLDOWN = 600  # 距上次启动不足 10 分钟不自动重启
DEFAULT_WINDOW = 3600  # 限流时间窗 1 小时
DEFAULT_MAX_RESTARTS = 2  # 窗内自动重启上限 2 次（原 3 次收紧）


class LoginGuard:
    """按驱动隔离的登录风控护栏（状态落盘，跨进程有效）。"""

    def __init__(self, driver, cooldown=None, window=None, max_restarts=None):
        self.driver = driver
        self.cooldown = float(cooldown if cooldown is not None else DEFAULT_COOLDOWN)
        self.window = float(window if window is not None else DEFAULT_WINDOW)
        self.max_restarts = int(max_restarts if max_restarts is not None else DEFAULT_MAX_RESTARTS)
        self._lock = threading.Lock()
        self._state = self._load()

    def _load(self) -> dict:
        try:
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save(self):
        try:
            os.makedirs(_STATE_DIR, exist_ok=True)
            tmp = _STATE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, _STATE_FILE)
        except Exception:
            pass

    def _slot(self) -> dict:
        return self._state.setdefault(
            self.driver, {"last_launch": 0.0, "restart_count": 0, "window_start": 0.0}
        )

    def mark_launch(self):
        """每次实际拉起 QQ/协议时调用（手动启动、自动重启都会走到）。"""
        with self._lock:
            self._slot()["last_launch"] = time.time()
            self._save()

    def mark_restart(self):
        """执行一次自动重启后调用（计数 +1，窗口滚动重置）。"""
        with self._lock:
            s = self._slot()
            now = time.time()
            if now - float(s.get("window_start") or 0) > self.window:
                s["window_start"] = now
                s["restart_count"] = 0
            s["restart_count"] = int(s.get("restart_count") or 0) + 1
            self._save()

    def seconds_since_launch(self) -> float:
        return time.time() - float(self._slot().get("last_launch") or 0)

    def allow_restart(self):
        """自动重启许可：返回 (ok, reason)。

        冷却期内 / 窗内重启超限 → (False, 原因)；否则 (True, '')。
        """
        with self._lock:
            s = self._slot()
            now = time.time()
            since = now - float(s.get("last_launch") or 0)
            if since < self.cooldown:
                wait = int(self.cooldown - since)
                return False, (
                    f"风控护栏：距上次启动不足 {wait} 秒，为避免反复扫码触发冻结，已暂停自动重启"
                )
            if now - float(s.get("window_start") or 0) > self.window:
                s["window_start"] = now
                s["restart_count"] = 0
                self._save()
            if int(s.get("restart_count") or 0) >= self.max_restarts:
                return False, (
                    f"风控护栏：1 小时内自动重启已达 {self.max_restarts} 次上限，"
                    "已停止自动重启，请手动检查 QQ 登录"
                )
            return True, ""