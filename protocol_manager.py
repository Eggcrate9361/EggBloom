# -*- coding: utf-8 -*-
"""
protocol_manager.py — 协议层进程管理门面（多驱动路由）

NapCat / LLOneBot / SnowLuma 的具体实现位于 protocols/ 包各驱动。

本文件保留 ProtocolManager 对外接口（webui / 桌面端 / 协议面板均依赖它），
按 bot_config.json 的 protocol_driver 路由到对应适配器，行为对调用方透明。
"""

import logging
from typing import Optional, Callable

from protocols import registry  # noqa: F401
from protocols.registry import build, is_registered, get_driver_ids  # noqa: F401

logger = logging.getLogger(__name__)

# 兼容旧代码引用的常量（若未来有人 import 这些名字）
PORT_API = 3000
PORT_EVENTS = 3001
PORT_ADMIN = 3004


def _current_driver() -> str:
    """读取当前激活的协议驱动 id"""
    try:
        from config import load_config
        cfg = load_config()
        driver = cfg.get("protocol_driver", "napcat")
        return str(driver or "napcat")
    except Exception:
        return "napcat"


class ProtocolManager:
    """协议层门面：按 protocol_driver 路由到 protocols 包内的适配器。

    对外保持旧接口：set_status_callback / start / stop / restart /
    is_running / is_api_ready / get_status，以及只读属性 _running /
    node_process / _actual_api_port。
    """

    def __init__(self, driver: Optional[str] = None):
        self.driver = driver or _current_driver()
        if not is_registered(self.driver):
            # 未知/未实现驱动 → 回退到 napcat 并提示
            was = self.driver
            self.driver = "napcat"
            if self.driver != was:
                logger.warning(f"协议驱动 '{was}' 未注册或未实现，回退到 '{self.driver}'")
        self.adapter = build(self.driver)
        self._on_status_change: Optional[Callable] = None

    # ── 兼容旧接口 ──────────────────────────────────────────
    def set_status_callback(self, cb: Callable):
        self.adapter.set_status_callback(cb)

    @property
    def _running(self) -> bool:
        """兼容 webui /api/protocol/status 的 getattr(mgr, '_running', False)"""
        try:
            return self.adapter.is_running()
        except Exception:
            return False

    # 属性透传（桌面端 / 面板直接访问）
    @property
    def node_process(self):
        return getattr(self.adapter, "node_process", None)

    @property
    def _actual_api_port(self):
        return getattr(self.adapter, "_actual_api_port", PORT_API)

    # ── 生命周期 ────────────────────────────────────────────
    def start(self):
        """启动当前驱动。新驱动成功后回写 onebot_url（重启 bot_core 后生效）。"""
        self.adapter.start()
        if getattr(self.adapter, "SYNC_ONEBOT_URL", False):
            try:
                if self.adapter.ensure_effective_url():
                    logger.info(f"[{self.driver}] onebot_url 已回写：{self.adapter.api_base_url}"
                                "（重启 bot_core 后生效）")
            except Exception:
                logger.exception(f"[{self.driver}] 回写 onebot_url 失败")

    def stop(self):
        self.adapter.stop()

    def restart(self):
        self.adapter.restart()

    def is_running(self) -> bool:
        return self.adapter.is_running()

    def is_api_ready(self) -> bool:
        m = getattr(self.adapter, "is_api_ready", None)
        if m:
            return m()
        return self.adapter.is_ready()

    def get_status(self) -> dict:
        st = self.adapter.get_status()
        st["driver"] = self.driver
        return st