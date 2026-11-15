# -*- coding: utf-8 -*-
"""protocols/base.py — 协议适配器抽象基类

每个驱动（napcat / llonebot / snowluma）实现 ProtocolAdapter：
- 进程生命周期（start / stop / is_running）
- 就绪检测（is_ready）
- 动作端点（api_base_url / api_call）
- 事件上报（post_event → bot_core 3005）

bot_core 与 webui_server 业务处理零改动；唯一生效通道是
「改写 onebot_url + 重启 bot_core」（见 ensure_effective_url）。
"""

import json
import os
import logging
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger("protocols")

# bot_core 事件接收端（转发的事件客户端，路径与 start_event_server 一致）
# 统一收敛到一个常量，避免各驱动硬编码
EVENT_BOT_CORE = "http://127.0.0.1:3005"


class ProtocolAdapter(ABC):
    """统一协议适配器接口"""

    name: str = "base"

    # 是否在启动成功后回写 onebot_url（驱动置 True 后，配合 bot_core
    # 重启按新 URL 生效）
    SYNC_ONEBOT_URL: bool = False

    def __init__(self):
        self._on_status_change: Optional[callable] = None
        self._running = False

    # ── 生命周期 ─────────────────────────────────────────────
    @abstractmethod
    def start(self):
        ...

    def stop(self):
        self._running = False

    def restart(self):
        self.stop()
        self.start()

    def is_running(self) -> bool:
        return self._running

    def is_ready(self) -> bool:
        """动作端点 + 事件链路是否就绪（默认未实现，驱动按需覆盖）"""
        return False

    # ── 事件回调（webui / 桌面端展示启动日志） ────────────────
    def set_status_callback(self, cb):
        self._on_status_change = cb

    def _notify(self, status: str, detail: str = ""):
        if self._on_status_change:
            try:
                self._on_status_change(status, detail)
            except Exception:
                pass
        logger.info(f"[状态] {status}: {detail}")

    # ── 动作端 ──────────────────────────────────────────────
    @property
    def api_base_url(self) -> str:
        """动作端点基址：bot_core 用 {api_base_url}/{action} 拼接。

        需包含协议前缀（若有）：
        - NapCat/LLOneBot/SnowLuma → http://127.0.0.1:<port>/onebot/v11
        """
        return ""

    @property
    def api_prefix(self) -> str:
        """动作路径前缀（仅信息用途）：标准 OneBot v11 = "/onebot/v11"。"""
        return ""

    def api_call(self, endpoint: str, data: Optional[dict] = None, timeout: int = 15) -> dict:
        """统一 OneBot 动作调用（自动带 token），返回 {retcode,data,...}"""
        base = self.api_base_url
        if not base:
            return {"retcode": -1}
        url = f"{base}/{endpoint}"
        body = json.dumps(data or {}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        token = self._effective_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            import urllib.request
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            logger.warning(f"[{self.name}] API [{endpoint}] 调用失败: {e}")
            return {"retcode": -1}

    def probe_ready(self, timeout: int = 3) -> bool:
        """通过 {api_base_url}/get_status 探测驱动就绪（在线）"""
        try:
            import urllib.request
            url = f"{self.api_base_url}/get_status"
            req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            return bool(data.get("data", {}).get("online", False))
        except Exception:
            return False

    def _effective_token(self) -> str:
        try:
            from config import load_config as _lc
            _cfg = _lc()
            return str(_cfg.get("onebot_token", "") or "")
        except Exception:
            return ""

    def ensure_effective_url(self) -> bool:
        """把本驱动动作端点回写 onebot_url（程序目录优先 + AppData 兜底），bot_core 重启后生效。

        现网 bot_core 只在启动时解析一次 onebot_url（见 bot_core.py），
        因此切换驱动 = 改写配置 + 重启 bot_core，不做进程内热切换。
        """
        base = self.api_base_url
        if not base:
            return False
        try:
            import config as _cfgmod
            written = False
            for path in [_cfgmod.APP_CONFIG_FILE, _cfgmod.CONFIG_FILE]:
                if not path:
                    continue
                try:
                    cur = {}
                    if os.path.exists(path):
                        with open(path, "r", encoding="utf-8-sig") as f:
                            cur = json.load(f)
                    if cur.get("onebot_url") == base:
                        written = True
                        continue
                    cur["onebot_url"] = base
                    tmp = path + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(cur, f, ensure_ascii=False, indent=2)
                    os.replace(tmp, path)
                    written = True
                except Exception:
                    pass
            return written
        except Exception:
            return False

    # ── 事件上报 bot_core(3005) ──────────────────────────────
    def post_event(self, event) -> bool:
        """把事件 POST 到 bot_core 事件服务（3005）。"""
        try:
            import urllib.request
            payload = json.dumps(event).encode("utf-8")
            req = urllib.request.Request(
                f"{EVENT_BOT_CORE}/", data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=3) as r:
                return r.status == 200
        except Exception:
            return False

    # ── 状态 ────────────────────────────────────────────────
    def get_status(self) -> dict:
        return {
            "driver": self.name,
            "running": self.is_running(),
            "api_ready": self.is_ready(),
            "api_base_url": self.api_base_url,
        }