# -*- coding: utf-8 -*-
"""EggBloom harness —— 有意保留的「干净 / 顶级决策 / 超高效 / 低成本」基建。

设计依据：docs/plan/dsh-harness-design.md（§7.3 输出保留、§7.4 spill、§11.1 P5/P6）。

⚠️ 研究里有一条重要警告：「从最小工具集起步；模型变强后，复杂脚手架的收益下降
而维护成本不降」。所以本包**只放经过验证的高收益原语**，不做大而全的框架。

当前内容：
  - ItemRetainer : 保留前 N 个有序逻辑单元，**精确省略计数**
  - TextRetainer : **按字节** 保留 head/tail/headTail，内存有界，切割处修剪不完整码点
  - spill        : 超预算内容落盘 + 内联首尾预览 + 绝对路径 + 读取提示
  - session_log  : 事件溯源会话日志（任务 5.3 试点：仅追加 + 从日志派生 prompt）
  - agent_loop   : Agent 运行时内核（主程序 agent 能力：两入口共用）
"""
from .retention import ItemRetainer, TextRetainer, format_retention_notice
from .spill import spill_text, SPILL_DIR
from . import session_log
from . import agent_loop

__all__ = [
    "ItemRetainer",
    "TextRetainer",
    "format_retention_notice",
    "spill_text",
    "SPILL_DIR",
    "session_log",
    "agent_loop",
]
