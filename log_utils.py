# -*- coding: utf-8 -*-
"""log_utils.py — 统一日志工具

提供项目级日志规范：
- 所有模块使用 logging.getLogger(__name__) 获取 logger
- 插件使用 get_plugin_logger(plugin_name) 获取统一格式的 logger
- 禁止使用裸 print() 做日志

用法：
    # 核心模块
    import logging
    logger = logging.getLogger(__name__)

    # 插件
    from log_utils import get_plugin_logger
    logger = get_plugin_logger("my_plugin")
    logger.info("插件已加载")
"""
import logging
import sys
from typing import Optional

# 插件日志统一前缀格式
_PLUGIN_FORMAT = "[%(name)s] %(message)s"
_PLUGIN_DATE_FORMAT = "%H:%M:%S"


def get_plugin_logger(
    plugin_name: str,
    level: Optional[int] = None,
) -> logging.Logger:
    """获取插件专用 logger。

    特点：
    - 自动添加 "plugin." 前缀（便于过滤）
    - 默认 INFO 级别
    - 输出到 stderr（与主日志分流）
    - 如果 logger 已存在则直接返回（幂等）

    Args:
        plugin_name: 插件名（与目录名一致）
        level: 日志级别，默认 INFO

    Returns:
        配置好的 Logger 实例
    """
    logger_name = f"plugin.{plugin_name}"
    logger = logging.getLogger(logger_name)

    # 避免重复添加 handler
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_PLUGIN_FORMAT, _PLUGIN_DATE_FORMAT))
        logger.addHandler(handler)
        logger.setLevel(level or logging.INFO)
        logger.propagate = False  # 不向上传播，避免重复输出

    return logger


def get_module_logger(module_name: str, level: Optional[int] = None) -> logging.Logger:
    """获取模块级 logger（核心模块使用）。

    等价于 logging.getLogger(module_name)，但提供统一的级别设置。

    Args:
        module_name: 模块名（通常传 __name__）
        level: 日志级别，默认 INFO

    Returns:
        配置好的 Logger 实例
    """
    logger = logging.getLogger(module_name)
    if level is not None:
        logger.setLevel(level)
    return logger


def set_global_level(level: int) -> None:
    """设置全局日志级别（影响所有已创建的 logger）。

    通常在 config.py 的 log_level 变更后调用。
    """
    logging.getLogger().setLevel(level)
    # 同时更新所有插件 logger
    for name in list(logging.Logger.manager.loggerDict):
        if name.startswith("plugin."):
            logging.getLogger(name).setLevel(level)
