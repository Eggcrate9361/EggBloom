# -*- coding: utf-8 -*-
"""protocols — 协议适配器层

统一抽象 NapCat / LLOneBot / SnowLuma 的协议接入差异，
差异全部封装在本包内；对外只暴露统一的 ProtocolAdapter 接口。
"""

from protocols.base import ProtocolAdapter
from protocols import registry
from protocols import napcat_adapter  # noqa: F401  # 导入即注册 napcat 驱动
from protocols import llonebot_adapter  # noqa: F401  # 导入即注册 llonebot 驱动（P2）
from protocols import snowluma_adapter  # noqa: F401  # 导入即注册 snowluma 驱动（P2）

__all__ = ["ProtocolAdapter", "registry"]