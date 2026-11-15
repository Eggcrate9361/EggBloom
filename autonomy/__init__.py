# -*- coding: utf-8 -*-
"""autonomy —— 对话驱动的能力获取 + 自主性判据（设计 §10–§17）。

七段：gap → probe → plan → acquire → verify → wire → report。
默认只在分离态由 agent 调用；产品态不自动开。

## 1.6.0 P3：自主层的四块都归位在这里

    loop.py       七步 DAG（harness.workflow 驱动）
    policy.py     权限等级表 / guard / 心跳提示词（唯一真相源）
    heartbeat.py  心跳材料 → 提示词 → 裁决，分离态由 bootstrap() 登记工具
    ledger.py     会话状态与待回复账本
    devloop.py    改已有代码 → 重启 → 回归 三段闭环（默认只预演）

`harness/proactivity/ledger.py` 与 `plugins/autonomy/policy.py` 只是转发兼容壳，
**不要在它们里面加逻辑**。`plugins/autonomy/` 故意不放 `__init__.py`，
否则会成为顶层 `autonomy` 包遮住本包（plugin_manager 把 plugins/ 插进 sys.path）。
"""
from __future__ import annotations

from .capability_gap import Gap, detect, infer_need, missing_tool, dependency_failure, self_report
from .loop import run
from . import ledger, policy, heartbeat, devloop  # noqa: F401

__all__ = ["Gap", "detect", "infer_need", "missing_tool", "dependency_failure",
           "self_report", "run", "ledger", "policy", "heartbeat", "devloop"]
