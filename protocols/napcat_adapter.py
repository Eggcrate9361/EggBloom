# -*- coding: utf-8 -*-
"""
protocols/napcat_adapter.py — NapCat 协议驱动（OneBot v11）

- 进程：NapCatWinBootMain.exe 注入 QQ（与 launcher-user.bat 一致，仅更精确指定 QQ 路径）
- API：NapCat「HTTP Server」模式 → http://127.0.0.1:<port>/{action}
       （本版本 NapCat.Shell 实测动作为「无前缀」根路径，见 api_base_url 注释）
- 事件：NapCat「WebSocket Server」→ 内置 WS 事件桥 → POST bot_core(3005)。
       该版本 httpClient（HTTP 上报）实测只发空 body 连接探测、不推送事件，
       故事件通道改用 WebSocket Server + 驱动内建 WS 客户端桥接（纯标准库）。
- 配置：预写 config/onebot11.json（或合并已存在的 onebot11_<uin>.json），
        与 WebUI 布局一致，首次登录即可用
"""

import os
import re
import sys
import json
import time
import logging
import threading
import subprocess
from typing import Optional

from protocols.base import ProtocolAdapter
from protocols import registry
from protocols.ws_client import WebSocketClient, default_ws_url
from protocols.login_guard import LoginGuard

logger = logging.getLogger(__name__)

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# 默认事件上报地址（bot_core 事件服务）
DEFAULT_REPORT_URL = "http://127.0.0.1:3005"
# 兜底 QQ 路径（程序 bot_config.json 的 qq_path 优先）
DEFAULT_QQ_PATH = ""
# NapCat WebSocket Server 端口（事件桥订阅端）
DEFAULT_WS_PORT = 3101
# NapCat WebUI 默认端口（登录引导）
DEFAULT_WEBUI_PORT = 6099
# 事件桥重连间隔（秒）
BRIDGE_RECONNECT_DELAY = 3

# 需要清理的进程名（与其它驱动策略一致，切换时全清）
_KILL_IMAGES = ["NapCatWinBootMain.exe", "QQ.exe", "QQEX.exe"]


def _read_json(path, default=None):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                return json.load(f)
        except Exception:
            pass
    return default if default is not None else {}


def _write_json(path, data):
    tmp = str(path) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(path))
        return True
    except Exception:
        return False


class NapCatAdapter(ProtocolAdapter):
    """NapCat 协议驱动：NapCatWinBootMain 注入 QQ + OneBot v11 HTTP"""

    name = "napcat"
    SYNC_ONEBOT_URL = True  # 启动成功后回写 onebot_url，需重启 bot_core 生效

    def __init__(self):
        super().__init__()
        self.proc: Optional[subprocess.Popen] = None
        self._running = False
        self._notified_login_wait = False
        self._cfg = self._load_driver_config()
        self._napcat_dir = os.path.dirname(str(self._cfg.get("exe_path")))
        self._watchdog_stop = threading.Event()
        self._bridge_stop = threading.Event()
        self._bridge_state = "idle"
        self._guard = LoginGuard("napcat")

    # ── 配置 ─────────────────────────────────────────────────
    def _load_driver_config(self) -> dict:
        """读取 protocol_drivers.napcat 配置（合并默认值）"""
        driver = {}
        try:
            from config import load_config
            d = (load_config().get("protocol_drivers") or {}).get("napcat") or {}
            driver.update(d)
        except Exception:
            pass
        # 从程序 bot_config.json 兜底 qq_path
        if not driver.get("qq_path"):
            try:
                qp = _read_json(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bot_config.json")).get("qq_path")
                if qp:
                    driver["qq_path"] = qp
            except Exception:
                pass
        if not driver.get("api_url"):
            driver["api_url"] = f"http://127.0.0.1:{self.api_port}"
        if not driver.get("http_report_url"):
            driver["http_report_url"] = DEFAULT_REPORT_URL
        if not driver.get("qq_path"):
            driver["qq_path"] = DEFAULT_QQ_PATH
        return driver

    @property
    def exe_path(self):
        return str(self._cfg.get("exe_path") or "")

    @property
    def qq_path(self):
        return str(self._cfg.get("qq_path") or "")

    @property
    def api_base_url(self) -> str:
        """动作端点：http://127.0.0.1:<port>

        实测本版本 NapCat.Shell 的 OneBot v11 动作路径为「无前缀」根路径
        （POST /get_status、/get_login_info 可直接调用），与 bot_core 现有
        {ONE_BOT_URL}/{action} 拼接完全一致，无需 /onebot/v11 前缀。
        若 WebUI 侧配置了动作前缀，可通过 protocol_drivers.napcat.api_base_url 覆盖。
        """
        url = str(self._cfg.get("api_url") or "").rstrip("/")
        if not url:
            url = f"http://127.0.0.1:{self.api_port}"
        # 归一：去掉误配的 /onebot/v11 前缀尾缀（本实现走无前缀路径）
        return url.replace("/onebot/v11", "")

    @property
    def api_prefix(self) -> str:
        return ""

    @property
    def api_port(self) -> int:
        m = re.search(r":(\d+)", str(self._cfg.get("api_url") or ""))
        return int(m.group(1)) if m else 3100

    @property
    def report_url(self) -> str:
        return str(self._cfg.get("http_report_url") or DEFAULT_REPORT_URL).rstrip("/")

    @property
    def ws_port(self) -> int:
        try:
            return int(self._cfg.get("ws_port") or DEFAULT_WS_PORT)
        except Exception:
            return DEFAULT_WS_PORT

    @property
    def ws_url(self) -> str:
        return str(self._cfg.get("ws_url") or default_ws_url(self.ws_port)).rstrip("/")

    @property
    def webui_port(self) -> int:
        try:
            return int(self._cfg.get("webui_port") or DEFAULT_WEBUI_PORT)
        except Exception:
            return DEFAULT_WEBUI_PORT

    def get_webui_info(self) -> dict:
        """读取 NapCat WebUI 登录信息（config/webui.json），用于自动获取 token。

        返回 {found, url, host, port, token}。优先读 NapCat 实际配置文件；
        若协议驱动配置里显式写了 webui_token/webui_port 则以此覆盖。
        """
        info = {
            "found": False,
            "host": "127.0.0.1",
            "port": self.webui_port,
            "url": f"http://127.0.0.1:{self.webui_port}",
            "token": "",
        }
        try:
            cfg_token = str(self._cfg.get("webui_token") or "").strip()
            cfg_port = self.webui_port
            wj = _read_json(os.path.join(self._napcat_dir, "config", "webui.json"), {})
            if wj:
                host = str(wj.get("host") or "127.0.0.1")
                # 通配监听地址不便于登录访问，统一显示为 127.0.0.1
                if host in ("::", "0.0.0.0", "", "None", "none"):
                    host = "127.0.0.1"
                try:
                    port = int(wj.get("port") or cfg_port)
                except Exception:
                    port = cfg_port
                info.update({
                    "found": True,
                    "host": host,
                    "port": port,
                    "url": f"http://{host}:{port}",
                    "token": str(wj.get("token") or ""),
                })
            if cfg_token:
                info["token"] = cfg_token
            if not info.get("found") and cfg_token:
                info["found"] = True
        except Exception:
            pass
        return info

    def _notify_webui(self):
        """把 NapCat WebUI 登录地址 + token 推送到启动日志（登录引导）"""
        info = self.get_webui_info()
        if not info.get("found"):
            self._notify("napcat_webui", "NapCat WebUI 配置未找到，无法自动获取登录 token")
            return
        if info.get("token"):
            self._notify("napcat_webui",
                         f"NapCat WebUI 登录: {info['url']}  token: {info['token']}")
        else:
            self._notify("napcat_webui", f"NapCat WebUI 登录: {info['url']}（无 token）")

    # ── 就绪探测 ─────────────────────────────────────────────
    def is_ready(self) -> bool:
        return self.probe_ready(timeout=3)

    # ── 生命周期 ─────────────────────────────────────────────
    def is_running(self) -> bool:
        if self.proc and self.proc.poll() is None:
            return True
        return self._running

    def start(self):
        if self.is_running():
            self._notify("already_running", "NapCat 已在运行")
            return
        if self._guard.seconds_since_launch() < self._guard.cooldown:
            self._notify("warning",
                         "距上次启动不足 %.0f 秒，频繁重启/扫码登录有风控冻结风险，请谨慎操作" % self._guard.cooldown)
        preflight = self._preflight()
        if not preflight.get("ok"):
            self._notify("error", preflight.get("error", "NapCat 环境检查未通过"))
            return
        self._start()

    def _preflight(self) -> dict:
        for label, path in [
            ("NapCat 启动器", self.exe_path),
            ("Hook DLL", os.path.join(self._napcat_dir, "NapCatWinBootHook.dll")),
            ("QQ", self.qq_path),
            ("qqnt.json", os.path.join(self._napcat_dir, "qqnt.json")),
            ("napcat.mjs", os.path.join(self._napcat_dir, "napcat.mjs")),
        ]:
            if not path:
                return {"ok": False, "error": f"{label}未配置"}
            if not os.path.exists(path):
                return {"ok": False, "error": f"{label}不存在: {path}"}
        return {"ok": True}

    def _start(self):
        self._notify("starting", "正在启动 NapCat...")
        # 1. 预写 OneBot v11 配置（HTTP API + 事件上报）
        self._ensure_onebot11_config()
        # 2. 重建 loadNapCat.js（与 launcher.bat 一致）
        self._prepare_launcher_env()
        # 3. 清理残留进程
        self._kill_stale()
        # 4. 启动注入器
        cmd = [self.exe_path, self.qq_path, os.path.join(self._napcat_dir, "NapCatWinBootHook.dll")]
        logger.info(f"NapCat 命令: {' '.join(cmd)}")
        try:
            self.proc = subprocess.Popen(
                cmd,
                cwd=self._napcat_dir,
                env=self._launch_env,
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception as e:
            self._notify("error", f"NapCat 启动失败: {e}")
            return
        self._running = True
        logger.info(f"NapCat 已启动 PID={self.proc.pid}")
        self._guard.mark_launch()
        self._notify("starting_napcat", f"NapCat 注入 QQ 中 PID={self.proc.pid}")
        # 4.5 自动获取 NapCat WebUI 登录 token，写入启动日志
        self._notify_webui()

        # 5. 等待 OneBot API 就绪（含登录等待）
        threading.Thread(target=self._wait_ready, daemon=True).start()
        self._start_watchdog()
        # 6. 启动 WebSocket 事件桥（NapCat WS Server → bot_core 3005）
        self._start_event_bridge()

    def _ensure_onebot11_config(self):
        """预写/合并 OneBot v11 网络配置。

        NapCat 读取 order：config/onebot11_<uin>.json > config/onebot11.json
        首次登录前无 per-uin 文件，写入 base；已有 per-uin 文件则合并。
        """
        port = self.api_port
        api_cfg = {
            "name": "eggbloom-http",
            "enable": True,
            "host": "127.0.0.1",
            "port": port,
            "enableCors": True,
            "messagePostFormat": "array",
            "token": "",
            "debug": False,
        }
        report_cfg = {
            "name": "eggbloom-report",
            "enable": True,
            "url": self.report_url,
            "messagePostFormat": "array",
            "reportSelfMessage": False,
            "token": "",
            "debug": False,
        }
        # 事件主通道：WebSocket Server（本版本 httpClient 推送事件不可靠）
        ws_cfg = {
            "name": "eggbloom-ws",
            "enable": True,
            "host": "127.0.0.1",
            "port": self.ws_port,
            "messagePostFormat": "array",
            "reportSelfMessage": False,
            "token": "",
            "enableForcePushEvent": True,
            "debug": False,
            "heartInterval": 30000,
        }

        def _merge(cfg: dict):
            network = cfg.setdefault("network", {})
            servers = network.setdefault("httpServers", [])
            servers[:] = [s for s in servers if not (s.get("port") == port and s.get("host", "127.0.0.1") == "127.0.0.1")]
            servers.append(api_cfg)
            clients = network.setdefault("httpClients", [])
            clients[:] = [c for c in clients if c.get("url") not in (self.report_url,)]
            clients.append(report_cfg)
            wss = network.setdefault("websocketServers", [])
            wss[:] = [w for w in wss if not (w.get("port") == self.ws_port and w.get("host", "127.0.0.1") == "127.0.0.1")]
            wss.append(ws_cfg)
            return cfg

        config_dir = os.path.join(self._napcat_dir, "config")
        try:
            os.makedirs(config_dir, exist_ok=True)
        except Exception as e:
            self._notify("error", f"NapCat 配置目录创建失败: {e}")
            return
        # 合并已存在的 per-uin 文件（WebUI 可能已生成）
        merged_any = False
        for name in os.listdir(config_dir):
            if re.fullmatch(r"onebot11_\d+\.json", name):
                p = os.path.join(config_dir, name)
                _write_json(p, _merge(_read_json(p)))
                merged_any = True
        # base 文件
        base_path = os.path.join(config_dir, "onebot11.json")
        _write_json(base_path, _merge(_read_json(base_path)))
        if merged_any:
            self._notify("napcat_config", "已合并 NapCat OneBot v11 配置（含已登录账号配置）")
        else:
            self._notify("napcat_config", f"已写入 NapCat OneBot v11 配置：HTTP {port} / WS {self.ws_port} / 上报 {self.report_url}")

    def _prepare_launcher_env(self):
        """重建 loadNapCat.js 并准备启动环境变量（复刻 launcher-user.bat）"""
        main_mjs = os.path.join(self._napcat_dir, "napcat.mjs")
        load_js = os.path.join(self._napcat_dir, "loadNapCat.js")
        hook = os.path.join(self._napcat_dir, "NapCatWinBootHook.dll")
        main_url = main_mjs.replace("\\", "/")
        try:
            with open(load_js, "w", encoding="utf-8") as f:
                f.write(f'(async () => {{await import("file:///{main_url}")}})()')
        except Exception as e:
            self._notify("error", f"loadNapCat.js 写入失败: {e}")
        env = os.environ.copy()
        env.pop("HTTP_PROXY", None)
        env.pop("HTTPS_PROXY", None)
        env.pop("http_proxy", None)
        env.pop("https_proxy", None)
        env["NAPCAT_PATCH_PACKAGE"] = os.path.join(self._napcat_dir, "qqnt.json")
        env["NAPCAT_LOAD_PATH"] = load_js
        env["NAPCAT_INJECT_PATH"] = hook
        env["NAPCAT_LAUNCHER_PATH"] = self.exe_path
        env["NAPCAT_MAIN_PATH"] = main_url
        self._launch_env = env

    def _kill_stale(self):
        """清理残留 NapCat / QQ 进程"""
        for name in _KILL_IMAGES:
            subprocess.run(
                ["taskkill", "/F", "/IM", name, "/T"],
                capture_output=True, timeout=10,
                creationflags=_CREATE_NO_WINDOW,
            )
        # 杀运行 napcat.mjs 的 node 进程（不杀其它 node）
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | Where-Object { $_.CommandLine -like '*napcat*mjs*' } | ForEach-Object { taskkill /F /PID $_.ProcessId /T }"],
            capture_output=True, timeout=10,
            creationflags=_CREATE_NO_WINDOW,
        )
        time.sleep(1)

    def _wait_ready(self, timeout: int = 180):
        """等待 OneBot API 就绪（含本次登录等待），就绪后通知"""
        deadline = time.time() + timeout
        logged = False
        while self._running and time.time() < deadline:
            if self.proc and self.proc.poll() is not None:
                # 启动器进程退出：若 API 已就绪则视为注入成功（部分版本 bootmain 注入完成后退出）
                if self.probe_ready(timeout=2):
                    if not logged:
                        self._notify("started", f"NapCat 已就绪：API {self.api_base_url}")
                        logged = True
                    self._notify_webui()
                    return
                self._notify("error", "NapCat 启动器进程已退出，请检查 QQ 版本兼容性")
                self._running = False
                return
            if self.probe_ready(timeout=2):
                if not logged:
                    self._notify("started", f"NapCat 已就绪：API {self.api_base_url}")
                    logged = True
                # 就绪后再推送一次 WebUI 登录信息（覆盖启动早期 webui.json 未生成的情况）
                self._notify_webui()
                return
            if not self._notified_login_wait:
                self._notify("waiting_napcat", "等待 NapCat/QQ 登录完成后就绪（请在 QQ 窗口确认登录）...")
                self._notified_login_wait = True
            time.sleep(3)
        if not logged:
            self._notify("error", "NapCat 就绪超时，请确认 QQ 已登录且 NapCat 正常")
            self._running = False

    # 自愈限流：登录风控护栏（冷却 10 分钟 + 1 小时 ≤ 2 次，见 LoginGuard）
    def _safe_restart(self):
        """自愈重启：杀残留进程后重新走完整启动流程。

        由 watchdog 在进程死亡时调用；重启会重拉 QQ，若登录态丢失
        用户需重新扫码（已在通知中提示）。
        """
        try:
            self._notify("restarting", "NapCat 自动重启中（如 QQ 登录态丢失需重新扫码）...")
            self._watchdog_stop.set()
            self._stop_event_bridge()
            self._kill_stale()
            time.sleep(2)
            self._start()
        except Exception as e:
            self._notify("error", f"NapCat 自动重启失败: {e}")
            self._running = False

    def _start_watchdog(self):
        """看门狗：监控进程存活 + API 可达性。

        进程死亡 → 限流自愈重启（1 小时 ≤ 3 次，防循环爆拉）；
        API 不可达但进程存活 → 只告警（不重启，避免误杀 QQ 导致重新扫码）。
        """
        self._watchdog_stop.clear()

        def _watch():
            fail_streak = 0
            while self._running and not self._watchdog_stop.wait(15):
                alive = bool(self.proc and self.proc.poll() is None)
                ready = self.probe_ready(timeout=3)
                if not alive:
                    # 进程死亡 → 自愈重启（登录风控护栏限流）
                    self._notify("error", "NapCat 进程已退出，尝试自动重启...")
                    self._running = False
                    ok, reason = self._guard.allow_restart()
                    if not ok:
                        self._notify("warning", reason)
                        return
                    self._guard.mark_restart()
                    threading.Thread(target=self._safe_restart, daemon=True).start()
                    return
                if not ready:
                    # 进程存活但 API 不可达（含 NapCat 偶发 15s 超时）→ 仅告警，不重启
                    fail_streak += 1
                    if fail_streak >= 8:
                        self._notify("warning", "NapCat API 持续不可达(超 2 分钟)，请检查网络连接；进程存活未自动重启")
                        fail_streak = 0
                else:
                    fail_streak = 0

        threading.Thread(target=_watch, daemon=True).start()

    # ── WebSocket 事件桥（NapCat WS Server → bot_core 3005） ──
    def _start_event_bridge(self):
        """启动事件桥线程：订阅 NapCat WebSocket Server，事件转发到 bot_core。"""
        self._bridge_stop.clear()
        self._bridge_state = "connecting"
        threading.Thread(target=self._bridge_loop, daemon=True, name="napcat-ws-bridge").start()

    def _bridge_loop(self):
        disconnect_notified = False
        while self._running and not self._bridge_stop.is_set():
            try:
                ws = WebSocketClient(self.ws_url, timeout=40)
                ws.connect()
                if self._bridge_state != "up":
                    self._bridge_state = "up"
                    self._notify("event_bridge", f"NapCat 事件桥已连接: {self.ws_url}")
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
                    # 断开首次可见通知，避免静默丢事件
                    if not disconnect_notified:
                        self._notify("event_bridge", f"NapCat 事件桥断开({e})，正在自动重连...")
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
        self._notify("stopping", "正在停止 NapCat...")
        self._running = False
        self._watchdog_stop.set()
        self._stop_event_bridge()
        # 关闭 QQ → 注入器 → node 进程
        for name in _KILL_IMAGES:
            subprocess.run(
                ["taskkill", "/F", "/IM", name, "/T"],
                capture_output=True, timeout=10,
                creationflags=_CREATE_NO_WINDOW,
            )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | Where-Object { $_.CommandLine -like '*napcat*mjs*' } | ForEach-Object { taskkill /F /PID $_.ProcessId /T }"],
            capture_output=True, timeout=10,
            creationflags=_CREATE_NO_WINDOW,
        )
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
        self._notify("stopped", "NapCat 已停止（QQ 已关闭）")

    # ── 状态 ─────────────────────────────────────────────────
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
            "qq_path": self.qq_path,
            "napcat_dir": self._napcat_dir,
        }


registry.register("napcat", NapCatAdapter)