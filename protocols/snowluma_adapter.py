# -*- coding: utf-8 -*-
"""
protocols/snowluma_adapter.py — SnowLuma 协议驱动（OneBot v11）

SnowLuma 是独立进程 QQ hook 框架（Node 运行时 + 内嵌 QQ 协议，自带 WebUI :5099
引导扫码登录）。SnowLuma v1.9.13 支持完整 OneBot v11 动作面：set_group_kick/ban/admin/card、
get_group_member_list、get_friend_list、群文件/公告/精华等 130+ 动作。

实际运行形态（v1.9.13 实机确认）：
- 入口：`node index.mjs`（node.exe 随包分发，非独立 exe）
- 配置：运行时目录 `config/runtime.json` + `config/onebot.json`（全局）+ `config/onebot_<uin>.json`（每账号）
- 默认端口：HTTP OneBot API :3000（路径 /，标准 v11 POST body）、WS Server :3001（role Universal）、WebUI :5099
- 鉴权：配置里 accessToken 为空串 → 免鉴权（parseBase 把空串转 undefined，运行时放行）
- 事件通道：WS Server :3001 Universal → 适配器内置 WS 客户端订阅 → POST bot_core:3005
  （与 NapCat 一致走方案 B，复用 OneBotStdAdapter 事件桥 + PING→PONG 保活）
- WebUI 认证：密码制（config/webui.json 存 passwordHash 不可逆）。初始密码由 SnowLuma
  首次启动时随机生成并打印到 logs/snowluma-*.log（initial credentials: user=admin
  password=xxxx）——get_webui_info 自动从日志抓取最近一次凭据，免手动翻日志

配置（bot_config.json protocol_drivers.snowluma）：
- exe_path : SnowLuma 运行时入口——node.exe 路径、运行时目录、或 index.mjs 路径（默认空，需用户配置）
- api_url  : http://127.0.0.1:3000（默认）
- ws_port  : 3001（默认，WS Universal）
- webui_port : 5099（默认）
- http_report_url : http://127.0.0.1:3005（bot_core 事件服务，默认）

首次接入：协议面板填 exe_path（浏览选择 node.exe）→ 启动 →
浏览器打开 WebUI :5099 扫码登录 → 登录后 HTTP :3000 就绪、事件桥自动连 :3001。
"""

import os
import sys
import json
import time
import logging
import subprocess

from protocols.onebot_std_adapter import OneBotStdAdapter
from protocols import registry

logger = logging.getLogger(__name__)

# SnowLuma v1.9.13 实际默认端口（实测包内默认配置）
DEFAULT_API_URL = "http://127.0.0.1:3000"
DEFAULT_WS_PORT = 3001
DEFAULT_WEBUI_PORT = 5099
DEFAULT_NODE = ""

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# ── 初始凭据抓取的扫描预算（2026-10-27）──────────────────────────────
# 背景：get_webui_info() 被 /api/protocol/status 按次调用，原来每次整文件读完
# logs/ 下所有 snowluma-*.log。本机实测 23 个文件 / 1024 MB / 4.5 秒，且**一条
# 都没命中**（密码早改过）→ 每次请求都全量扫完，协议页直接卡死。
# 凭据是启动时打印的，必然在文件头部；所以只读头，并且把结果（含"没有"）缓存住。
_HEAD_BYTES = 256 * 1024   # 每个文件只读头部 256KB
_MAX_FILES = 6             # 最多看最近 6 个日志文件
_INFO_TTL = 600.0          # 同一 logs_dir 的结果缓存 10 分钟（未命中同样缓存）
_WEBUI_INFO_CACHE = {}     # {logs_dir: {"at": ts, "hit": {"user":..,"token":..}}}


def _resolve_runtime(exe_path: str) -> str:
    """把 exe_path 解析为 node.exe 绝对路径。

    支持三种填法：
    1. 直接指向 node.exe（推荐，面板浏览选文件）
    2. 指向运行时目录（含 index.mjs）
    3. 指向 index.mjs 本身
    """
    exe = (exe_path or "").strip().strip('"')
    if not exe:
        return DEFAULT_NODE
    if os.path.isdir(exe):
        return os.path.join(exe, "node.exe")
    if os.path.basename(exe).lower() == "index.mjs":
        return os.path.join(os.path.dirname(exe), "node.exe")
    return exe


class SnowLumaAdapter(OneBotStdAdapter):
    """SnowLuma 协议驱动：独立 Node 运行时 + OneBot v11（HTTP :3000 + WS Universal :3001）"""

    name = "snowluma"
    driver_key = "snowluma"
    display_name = "SnowLuma"
    default_api_url = DEFAULT_API_URL
    default_ws_port = DEFAULT_WS_PORT
    default_webui_port = DEFAULT_WEBUI_PORT
    # 不以进程名清理：SnowLuma 由 node.exe 运行，按 /IM node.exe 会误杀其他 Node 进程
    kill_images = []

    def _resolve_driver_dir(self, exe: str) -> str:
        # 空 exe_path 时回退到运行时目录，避免 cwd 落在 webui 工作目录导致 index.mjs 找不到
        node = _resolve_runtime(exe)
        return os.path.dirname(node)

    @property
    def runtime_dir(self) -> str:
        node = _resolve_runtime(self.exe_path)
        return os.path.dirname(node)

    def _preflight(self) -> dict:
        node = _resolve_runtime(self.exe_path)
        if not os.path.exists(node):
            return {"ok": False,
                    "error": f"SnowLuma 运行时不存在: {node or '(未配置)'}（请在协议面板 SnowLuma 配置里浏览选择 node.exe）"}
        if not os.path.exists(os.path.join(os.path.dirname(node), "index.mjs")):
            return {"ok": False,
                    "error": f"index.mjs 不存在: {os.path.dirname(node)}（不是有效的 SnowLuma 运行时目录）"}
        return {"ok": True}

    def _build_command(self) -> list:
        """启动命令：node index.mjs（cwd = 运行时目录，config/ 在其中生成）。"""
        node = _resolve_runtime(self.exe_path)
        return [node, "index.mjs"]

    def _build_env(self) -> dict:
        env = super()._build_env()
        # 禁止自动注入所有已发现 QQ（避免误注入其他 QQ），由用户从 WebUI 手动选择登录
        env["SNOWLUMA_HOOK_AUTOLOAD"] = "0"
        return env

    def _kill_stale(self):
        """只清理自己拉起的进程树（taskkill /PID /T），绝不按 node.exe 全杀。"""
        if self.proc and self.proc.poll() is None:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                    capture_output=True, timeout=10,
                    creationflags=_CREATE_NO_WINDOW,
                )
            except Exception:
                pass
        time.sleep(1)

    def _ensure_network_config(self):
        """预写 config/onebot.json：HTTP api_port + WS Universal ws_port，accessToken 空串免鉴权。

        accessToken 写空串：SnowLuma parseBase 会把空串转 undefined → 运行时免鉴权，
        bot_core 轮询 HTTP 与事件桥 WS 都无需带 token。
        per-uin 配置（config/onebot_<uin>.json，旧随机 token 会按 name 覆盖全局配置）
        改为逐账号合并写入 networks（保留账号登录态等其余字段），不再删除——
        避免多账号批量场景下清空全部 per-uin 登录态造成重复扫码/风控。
        """
        runtime = self.runtime_dir
        cfg_dir = os.path.join(runtime, "config")
        os.makedirs(cfg_dir, exist_ok=True)

        config_path = os.path.join(cfg_dir, "onebot.json")
        desired = {
            "networks": {
                "httpServers": [{
                    "name": "http-default",
                    "host": "0.0.0.0",
                    "port": int(self.api_port),
                    "path": "/",
                    "accessToken": "",
                    "messageFormat": "array",
                    "reportSelfMessage": False,
                }],
                "httpClients": [],
                "wsServers": [{
                    "name": "ws-default",
                    "host": "0.0.0.0",
                    "port": int(self.ws_port),
                    "path": "/",
                    "role": "Universal",
                    "accessToken": "",
                    "messageFormat": "array",
                    "reportSelfMessage": False,
                }],
                "wsClients": [],
            },
            "musicSignUrl": "",
            "statusCommand": {"enabled": True, "swallow": False, "cooldownSeconds": 60},
            "notifications": {"channelIds": []},
        }

        changed = True
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                changed = existing != desired
            except Exception:
                changed = True
        if changed:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(desired, f, ensure_ascii=False, indent=2)
            logger.info("SnowLuma 已写入 OneBot 配置: %s", config_path)

        # 合并 per-uin 旧配置：只覆盖 networks（端口/鉴权跟随全局），保留账号登录态等其余字段
        for name in os.listdir(cfg_dir):
            if not (name.startswith("onebot_") and name.endswith(".json")):
                continue
            path = os.path.join(cfg_dir, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    continue
                if data.get("networks") != desired["networks"]:
                    data["networks"] = json.loads(json.dumps(desired["networks"]))
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                    logger.info("SnowLuma 已合并 per-uin 网络配置: %s", name)
            except Exception:
                logger.warning("SnowLuma 跳过 per-uin 配置 %s（格式异常）", name)

    def get_webui_info(self) -> dict:
        """SnowLuma WebUI 登录引导（5099）：自动从日志抓取初始登录密码（跟 NapCat 自动取 token 一致）。

        SnowLuma WebUI 用密码认证（config/webui.json 存 passwordHash，不可逆）。
        初始密码在 SnowLuma 首次启动时随机生成并打印到 logs/snowluma-*.log：
        「initial credentials: user=admin password=xxxx」——此处按文件新→旧、行后→前
        抓取最近一次凭据。若用户已在 WebUI 手动改密则失效（以 SnowLuma 日志为准）。
        返回 {found, url, host, port, user, token}。

        ★ 2026-10-27 修（真机：协议页卡在「读取中…」，切页也动不了）
          这个函数原来**每次调用都把 logs/ 下所有 snowluma-*.log 整文件读完**。
          本机实测：23 个文件 / 1024 MB / 4.5 秒，而且一条凭据都没命中（密码
          早被改过，标记不在日志里）→ 每次都是全量扫完再返回。
          `/api/protocol/status` 还叠了 is_ready() 最多 3 秒的探测，总耗时 8 秒起；
          前端 API() 没有超时、go() 的 busy 也不会在异常时释放，于是**一页卡住
          就锁死整个控制台的导航**。

          现在两条约束：
            ① 只读每个文件的**头部** _HEAD_BYTES —— 初始凭据是启动时打印的，
               位于文件开头；没有理由为此读完整盘。
            ② 结果按 logs_dir 缓存 _INFO_TTL 秒（未命中同样缓存）—— 密码要么
               早就在，要么根本不在，10 秒内重复问一次毫无意义。
          冷调用从 4.5 秒降到毫秒级。
        """
        import re
        info = {
            "found": True,
            "host": "127.0.0.1",
            "port": self.webui_port,
            "url": f"http://127.0.0.1:{self.webui_port}",
            "user": "admin",
            "token": "",
        }
        try:
            logs_dir = os.path.join(self.runtime_dir, "logs")
            if not os.path.isdir(logs_dir):
                return info
            _cached = _WEBUI_INFO_CACHE.get(logs_dir)
            if _cached and (time.time() - _cached["at"]) < _INFO_TTL:
                info.update(_cached["hit"])
                return info
            files = sorted(
                (os.path.join(logs_dir, f) for f in os.listdir(logs_dir)
                 if f.startswith("snowluma-") and f.endswith(".log")),
                key=os.path.getmtime, reverse=True,
            )
            pat = re.compile(r"initial credentials:\s*user=(\S+)\s+password=(\S+)")
            hit = {}
            for f in files[:_MAX_FILES]:
                last = None
                try:
                    with open(f, "r", encoding="utf-8", errors="replace") as fh:
                        # 只读头部：凭据是启动时打的，在文件最前面
                        chunk = fh.read(_HEAD_BYTES)
                    for line in chunk.splitlines():
                        m = pat.search(line)
                        if m:
                            last = (m.group(1), m.group(2))
                except Exception:
                    continue
                if last:
                    hit = {"user": last[0], "token": last[1]}
                    break
            _WEBUI_INFO_CACHE[logs_dir] = {"at": time.time(), "hit": hit}
            info.update(hit)
        except Exception:
            pass
        return info

    def _notify_webui(self):
        """把 SnowLuma WebUI 登录地址 + 初始密码推送到启动日志（登录引导）。"""
        info = self.get_webui_info()
        url = info.get("url") or f"http://127.0.0.1:{self.webui_port}"
        if info.get("token"):
            self._notify("snowluma_webui",
                         f"SnowLuma WebUI 登录: {url}  user: {info.get('user')}  密码: {info.get('token')}")
        else:
            self._notify("snowluma_webui",
                         f"SnowLuma WebUI 登录: {url}（未找到初始密码，请查看 SnowLuma 日志 logs/snowluma-*.log）")

    def _start(self):
        super()._start()
        # 启动后推送 WebUI 登录引导（自动从日志抓初始密码）
        try:
            self._notify_webui()
        except Exception:
            pass


registry.register("snowluma", SnowLumaAdapter)
