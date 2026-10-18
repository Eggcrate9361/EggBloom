#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
EggBloom 统一日志轮转工具
- 标准 logging + RotatingFileHandler（5MB x 5 轮转）
- 统一时间戳格式，便于跨进程时间线串联
用法:
    import logger_setup
    logger = logger_setup.get_logger("bot_core")
    logger.info("...")
"""
import logging
import os
from logging.handlers import RotatingFileHandler

try:
    from data_store import get_log_dir
except Exception:
    def get_log_dir():
        here = os.path.dirname(os.path.abspath(__file__))
        logs_dir = os.path.join(here, "logs")
        os.makedirs(logs_dir, exist_ok=True)
        return logs_dir

_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_configured = set()


def get_logger(name="eggbloom", log_dir=None, max_bytes=5 * 1024 * 1024, backup_count=5):
    """获取（或创建）带轮转的统一日志 logger"""
    if name in _configured:
        return logging.getLogger(name)

    logger = logging.getLogger(name)
    if logger.handlers:
        _configured.add(name)
        return logger

    # 从配置获取日志级别
    try:
        from config import get_log_level
        level = get_log_level()
    except Exception:
        level = logging.INFO

    logger.setLevel(level)

    fmt = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    # 文件 handler（轮转）
    log_dir = log_dir or get_log_dir()
    log_file = os.path.join(log_dir, f"{name}.log")
    try:
        fh = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except Exception:
        pass

    # 控制台 handler（存在 stdout 时）
    if os.sys.stdout is not None:
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        logger.addHandler(ch)

    _configured.add(name)
    return logger