# -*- coding: utf-8 -*-
"""
protocols/onebot_std_adapter.py — OneBot v11 标准适配器基类（P2）

NapCat / LLOneBot / SnowLuma 均为 OneBot v11 兼容框架，协议差异（动作路径/
事件通道）统一封装：HTTP 动作 + WS Server 事件桥 + 自愈 watchdog。

子类只需实现差异点（进程启动/配置写入/清理），通用能力全部在此：
- OneBot v11 HTTP 动作（api_base_url / api_call / probe_ready）
- WS Server 事件桥（ws_client 订阅 → post_event 到 bot_core:3005，含 PING→PONG 心跳保活）
- 自愈 watchdog（进程死亡限流自动重启 / API 不可达仅告警）
- 通用生命周期骨架（start → wait_ready → watchdog + 事件桥；stop / get_status）

设计说明（见 docs 适配方案 §2.1/§2.3）：
- 事件通道统一走「框架开 WS Server → 驱动内置 WS 客户端订阅 → POST bot_core 3005」
  （NapCat 实测 httpClient 上报不可靠，故各框架统一用 WS Server 方案 B）
- 动作路径差异：api_url 由子类/配置决定（NapCat/SnowLuma 无前缀实测；LLOneBot 标准 /onebot/v11）
"""

import os
import re
import sys
import json
import time
import logging
import threading
import subprocess
from typing import List, Optional

from protocols.base import ProtocolAdapter
from protocols.ws_client import WebSocketClient
from protocols.login_guard import LoginGuard

logger = logging.getLogger(__name__)

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# 默认事件上报地址（bot_core 事件服务）
DEFAULT_REPORT_URL = "http://127.0.0.1:3005"
# 事件桥重连间隔（秒）
BRIDGE_RECONNECT_DELAY = 3


def _read_json(path, default=None):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                return json.load(f)
        except Exception:
            pass
    return default if default is not None else {}


class OneBotStdAdapter(ProtocolAdapter):
    """OneBot v11 标准适配器基类。

    子类实现/覆盖：
    - driver_key      : protocol_drivers.<key> 配置键（如 napcat / llonebot / snowluma）
    - display_name    : 界面显示名（如 "LLOneBot"）
    - default_api_url : 默认动作端点（含 /onebot/v11 前缀与否由框架实测决定）
    - default_ws_port : 默认 WS Server 端口（事件桥订阅）
    - default_webui_port : 默认 WebUI 端口（登录引导）
    - kill_images     : 启动/停止时清理的进程名
    - _ensure_network_config() : 预写框架 OneBot 配置（可选，默认 no-op）
    - _build_command()         : 启动命令（必实现）
    - _build_env()             : 启动环境（默认 os.environ.copy()）
    - get_webui_info()         : 登录引导信息（可选，默认空）
    """

    name = "onebot_std"
    driver_key = ""
    display_name = "OneBot"
    default_api_url = "http://127.0.0.1:3100"
    default_ws_port = 3101
    default_webui_port = 6099
    kill_images: List[str] = []

    def __init__(self):
        super().__init__()
        self.proc: Optional[subprocess.Popen] = None
        self._running = False
        self._notified_login_wait = False
        self._cfg = self._load_driver_config()
        exe = str(self._cfg.get("exe_path") or "")
        self._driver_dir = self._resolve_driver_dir(exe)
        self._watchdog_stop = threading.Event()
        self._bridge_stop = threading.Event()
        self._bridge_state = "idle"
        self._launch_env = None
        self._guard = LoginGuard(self.driver_key or self.name)

    def _resolve_driver_dir(self, exe: str) -> str:
        """确定框架工作目录（子类可重写）。默认 exe 所在目录，空时当前目录。"""
        return os.path.dirname(exe) if exe else os.getcwd()

    # ── 配置 ─────────────────────────────────────────────────
    def _load_driver_config(self) -> dict:
        """读取 protocol_drivers.<driver_key> 配置（合并默认值）。

        现有 bot_config.json 可能整体覆盖 protocol_drivers 而缺失新驱动键
        （如 llonebot/snowluma），故先从 config.DEFAULT_CONFIG 补默认。"""
        driver = {}
        try:
            from config import load_config, DEFAULT_CONFIG
            d = (load_config().get("protocol_drivers") or {}).get(self.driver_key) or {}
            driver.update(d)
            defaults = (DEFAULT_CONFIG.get("protocol_drivers") or {}).get(self.driver_key) or {}
            for k, v in defaults.items():
                if k not in driver or not driver.get(k):
                    driver[k] = v
        except Exception:
            pass
        if not driver.get("api_url"):
            driver["api_url"] = self.default_api_url
        if not driver.get("http_report_url"):
            driver["http_report_url"] = DEFAULT_REPORT_URL
        return driver

    @property
    def exe_path(self):
        return str(self._cfg.get("exe_path") or "")

    @property
    def api_base_url(self) -> str:
        """动作端点：由配置 api_url 决定（归一尾部斜杠，保留 /onebot/v11 前缀）。"""
        url = str(self._cfg.get("api_url") or "").rstrip("/")
        if not url:
            url = self.default_api_url
        return url

    @property
    def api_prefix(self) -> str:
        return "/onebot/v11" if "/onebot/v11" in self.api_base_url else ""

    @property
    def api_port(self) -> int:
        m = re.search(r":(\d+)", str(self._cfg.get("api_url") or self.api_base_url))
        return int(m.group(1)) if m else 3000

    @property
    def report_url(self) -> str:
        return str(self._cfg.get("http_report_url") or DEFAULT_REPORT_URL).rstrip("/")

    @property
    def ws_port(self) -> int:
        try:
            return int(self._cfg.get("ws_port") or self.default_ws_port)
        except Exception:
            return self.default_ws_port

    @property
    def ws_url(self) -> str:
        return str(self._cfg.get("ws_url") or f"ws://127.0.0.1:{self.ws_port}").rstrip("/")

    @property
    def webui_port(self) -> int:
        try:
            return int(self._cfg.get("webui_port") or self.default_webui_port)
        except Exception:
            return self.default_webui_port

    # ── 就绪探测 ─────────────────────────────────────────────
    def is_ready(self) -> bool:
        return self.probe_ready(timeout=3)

    def is_running(self) -> bool:
        if self.proc and self.proc.poll() is None:
            return True
        return self._running

    # ── 生命周期 ─────────────────────────────────────────────
    def start(self):
        if self.is_running():
            self._notify("already_running", f"{self.display_name} 已在运行")
            return
        # 框架可能已在外部运行（如 webui/应用重启后遗留的孤儿进程，SnowLuma 尤甚）：
        # 直接接管而不重复拉起，避免端口占用冲突；接管 = 连事件桥 + 等就绪。
        try:
            if self._attach_existing():
                return
        except Exception:
            pass
        if self._guard.seconds_since_launch() < self._guard.cooldown:
            self._notify("warning",
                         "距上次启动不足 %.0f 秒，频繁重启/扫码登录有风控冻结风险，请谨慎操作" % self._guard.cooldown)
        preflight = self._preflight()
        if not preflight.get("ok"):
            self._notify("error", preflight.get("error", f"{self.display_name} 环境检查未通过"))
            return
        self._start()

    def _preflight(self) -> dict:
        """环境检查：exe 必须存在（子类可扩展）。"""
        if not self.exe_path:
            return {"ok": False, "error": f"{self.display_name} 未配置 exe 路径"}
        if not os.path.exists(self.exe_path):
            return {"ok": False, "error": f"{self.display_name} 启动器不存在: {self.exe_path}"}
        return {"ok": True}

    def _attach_existing(self) -> bool:
        """接管已在外部运行的框架进程（API 在线即为已在运行），不重复拉起。

        场景：webui_server / 应用重启后驱动进程仍是孤儿存活（如 SnowLuma 的
        node.exe 独立进程、NapCat 注入后常驻的 QQ）。重复 start 会拉起第二个
        实例导致端口占用冲突。接管 = 启动 WS 事件桥 + 等待就绪，进程由外部持有，
        因此不启动 watchdog（proc 为 None 时 watchdog 会误判进程死亡而重启）。
        """
        if not self.probe_ready(timeout=2):
            return False
        self._running = True
        self._notify("attached",
                     f"{self.display_name} 已在外部运行（API 就绪），直接接管事件桥，不重复启动")
        self._start_event_bridge()
        threading.Thread(target=self._wait_ready, daemon=True).start()
        return True

    def _start(self):
        self._notify("starting", f"正在启动 {self.display_name}...")
        # 1. 预写/合并 OneBot 网络配置（子类按框架实现）
        try:
            self._ensure_network_config()
        except Exception as e:
            logger.warning(f"[{self.name}] _ensure_network_config 失败: {e}")
        # 2. 清理残留进程
        self._kill_stale()
        # 3. 启动进程
        try:
            cmd = self._build_command()
            env = self._build_env()
            logger.info(f"{self.display_name} 命令: {' '.join(cmd)}")
            self.proc = subprocess.Popen(
                cmd,
                cwd=self._driver_dir,
                env=env,
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception as e:
            self._notify("error", f"{self.display_name} 启动失败: {e}")
            return
        self._running = True
        logger.info(f"{self.display_name} 已启动 PID={self.proc.pid}")
        self._guard.mark_launch()
        self._notify("starting_app", f"{self.display_name} 启动中 PID={self.proc.pid}")
        # 4. 等待 OneBot API 就绪
        threading.Thread(target=self._wait_ready, daemon=True).start()
        self._start_watchdog()
        # 5. 启动 WS 事件桥
        self._start_event_bridge()

    # —— 子类实现钩子 ——
    def _ensure_network_config(self):
        """预写/合并框架 OneBot v11 网络配置（HTTP API + WS Server）。默认 no-op。"""
        pass

    def _build_command(self) -> list:
        raise NotImplementedError

    def _build_env(self) -> dict:
        env = os.environ.copy()
        env.pop("HTTP_PROXY", None)
        env.pop("HTTPS_PROXY", None)
        env.pop("http_proxy", None)
        env.pop("https_proxy", None)
        return env

    def _kill_stale(self):
        """清理残留进程（子类实现）。"""
        for name in self.kill_images:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/IM", name, "/T"],
                    capture_output=True, timeout=10,
                    creationflags=_CREATE_NO_WINDOW,
                )
            except Exception:
                pass
        time.sleep(1)

    def _wait_ready(self, timeout: int = 180):
        """等待 OneBot API 就绪（含登录等待），就绪后通知。"""
        deadline = time.time() + timeout
        logged = False
        while self._running and time.time() < deadline:
            if self.proc and self.proc.poll() is not None:
                # 启动器进程退出：若 API 已就绪则视为启动成功（部分框架注入后退出）
                if self.probe_ready(timeout=2):
                    if not logged:
                        self._notify("started", f"{self.display_name} 已就绪：API {self.api_base_url}")
                        logged = True
                    return
                self._notify("error", f"{self.display_name} 启动器进程已退出，请检查框架/QQ 兼容性")
                self._running = False
                return
            if self.probe_ready(timeout=2):
                if not logged:
                    self._notify("started", f"{self.display_name} 已就绪：API {self.api_base_url}")
                    logged = True
                return
            if not self._notified_login_wait:
                self._notify("waiting_app", f"等待 {self.display_name}/QQ 登录完成后就绪（请在登录窗口确认）...")
                self._notified_login_wait = True
            time.sleep(3)
        if not logged:
            self._notify("error", f"{self.display_name} 就绪超时，请确认已登录且框架正常")
            self._running = False

    # 自愈限流：登录风控护栏（冷却 10 分钟 + 1 小时 ≤ 2 次，见 LoginGuard）
    def _safe_restart(self):
        """自愈重启：清残留后重新走完整启动流程（由 watchdog 在进程死亡时调用）。"""
        try:
            self._notify("restarting", f"{self.display_name} 自动重启中（如登录态丢失需重新登录）...")
            self._watchdog_stop.set()
            self._stop_event_bridge()
            self._kill_stale()
            time.sleep(2)
            self._start()
        except Exception as e:
            self._notify("error", f"{self.display_name} 自动重启失败: {e}")
            self._running = False

    def _start_watchdog(self):
        """看门狗：进程死亡 → 限流自愈重启；API 不可达但进程存活 → 仅告警。"""
        self._watchdog_stop.clear()

        def _watch():
            fail_streak = 0
            while self._running and not self._watchdog_stop.wait(15):
                alive = bool(self.proc and self.proc.poll() is None)
                ready = self.probe_ready(timeout=3)
                if not alive:
                    self._notify("error", f"{self.display_name} 进程已退出，尝试自动重启...")
                    self._running = False
                    ok, reason = self._guard.allow_restart()
                    if not ok:
                        self._notify("warning", reason)
                        return
                    self._guard.mark_restart()
                    threading.Thread(target=self._safe_restart, daemon=True).start()
                    return
                if not ready:
                    fail_streak += 1
                    if fail_streak >= 8:
                        self._notify("warning", f"{self.display_name} API 持续不可达(超 2 分钟)，请检查网络连接；进程存活未自动重启")
                        fail_streak = 0
                else:
                    fail_streak = 0

        threading.Thread(target=_watch, daemon=True).start()

    # ── WebSocket 事件桥（框架 WS Server → bot_core 3005） ──
    def _start_event_bridge(self):
        self._bridge_stop.clear()
        self._bridge_state = "connecting"
        threading.Thread(target=self._bridge_loop, daemon=True, name=f"{self.name}-ws-bridge").start()

    def _bridge_loop(self):
        disconnect_notified = False
        while self._running and not self._bridge_stop.is_set():
            try:
                ws = WebSocketClient(self.ws_url, timeout=40)
                ws.connect()
                if self._bridge_state != "up":
                    self._bridge_state = "up"
                    self._notify("event_bridge", f"{self.display_name} 事件桥已连接: {self.ws_url}")
                    disconnect_notified = False
                for opcode, payload in ws.iter_frames():
                    if self._bridge_stop.is_set():
                        break
                    if opcode in (0x1, 0x2):  # text / binary
                        self._forward_event(payload)
                    elif opcode == 0x8:  # close
                        break
                ws.close()
            except Exception as e:
                if self._running and not self._bridge_stop.is_set():
                    if not disconnect_notified:
                        self._notify("event_bridge", f"{self.display_name} 事件桥断开({e})，正在自动重连...")
                        disconnect_notified = True
                    logger.debug(f"[{self.name}] 事件桥异常: {e}")
            if self._bridge_stop.is_set():
                break
            if self._running and self._bridge_state != "idle":
                self._bridge_state = "connecting"
            for _ in range(BRIDGE_RECONNECT_DELAY * 10):
                if self._bridge_stop.is_set() or not self._running:
                    break
                time.sleep(0.1)
        self._bridge_state = "idle"

    def _forward_event(self, payload: bytes):
        try:
            text = payload.decode("utf-8", "replace").strip()
            if not text:
                return
            event = json.loads(text)
        except Exception:
            logger.debug(f"[{self.name}] 事件桥收到非 JSON 帧，已忽略")
            return
        ok = self.post_event(event)
        if not ok:
            logger.warning(f"[{self.name}] 事件转发 bot_core 失败: {event.get('post_type')}")

    def _stop_event_bridge(self):
        self._bridge_stop.set()
        self._bridge_state = "idle"

    def stop(self):
        self._notify("stopping", f"正在停止 {self.display_name}...")
        self._running = False
        self._watchdog_stop.set()
        self._stop_event_bridge()
        self._kill_stale()
        self.proc = None
        # 联动关闭协议使能
        try:
            from config import load_config, save_config
            _cfg = load_config()
            if _cfg.get("protocol_enabled"):
                _cfg["protocol_enabled"] = False
                save_config(_cfg)
        except Exception:
            pass
        self._notify("stopped", f"{self.display_name} 已停止")

    # ── 状态 / 登录引导 ──────────────────────────────────────
    def get_webui_info(self) -> dict:
        """登录引导信息（子类按框架实现）。默认空。"""
        return {"found": False, "url": f"http://127.0.0.1:{self.webui_port}", "token": ""}

    def _notify_webui(self):
        info = self.get_webui_info()
        if not info.get("found"):
            return
        if info.get("token"):
            self._notify("webui_info", f"{self.display_name} WebUI 登录: {info['url']}  token: {info['token']}")
        else:
            self._notify("webui_info", f"{self.display_name} WebUI 登录: {info['url']}（无 token）")

    def get_status(self) -> dict:
        return {
            "driver": self.name,
            "running": self.is_running(),
            "api_ready": self.is_ready(),
            "pid": self.proc.pid if self.proc and self.proc.poll() is None else None,
            "api_base_url": self.api_base_url,
            "api_port": self.api_port,
            "report_url": self.report_url,
            "ws_url": self.ws_url,
            "bridge_state": self._bridge_state,
            "webui": self.get_webui_info(),
            "driver_dir": self._driver_dir,
        }
