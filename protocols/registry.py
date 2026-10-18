# -*- coding: utf-8 -*-
"""protocols/registry.py — 协议驱动注册表

新驱动（P1/P2：NapCat/LLOneBot/SnowLuma）只需：
  class NapCatAdapter(ProtocolAdapter): ...
  registry.register("napcat", NapCatAdapter)
即自动被 ProtocolManager 路由。
"""

from typing import Dict, Optional, Type

from protocols.base import ProtocolAdapter

_DRIVERS: Dict[str, Type[ProtocolAdapter]] = {}


def register(driver_id: str, adapter_cls: Type[ProtocolAdapter]):
    """注册一个协议驱动"""
    _DRIVERS[driver_id] = adapter_cls


def is_registered(driver_id: str) -> bool:
    return driver_id in _DRIVERS


def get_driver_ids() -> list:
    """已注册驱动 id 列表（P3 webui 下拉用）"""
    return sorted(_DRIVERS.keys())


def build(driver_id: str, fallback: Optional[str] = "napcat") -> ProtocolAdapter:
    """按驱动 id 构建适配器实例；未知驱动回退到 fallback。"""
    cls = _DRIVERS.get(driver_id) or _DRIVERS.get(fallback or "napcat")
    if cls is None:
        raise ValueError(f"未注册任何可用的协议驱动（已注册: {list(_DRIVERS)}）")
    return cls()