# -*- coding: utf-8 -*-
"""harness/proactivity —— 主动性行为的观测层与仲裁层（1.6.0 P2）

三层分工，谁都不越界：

    事件层（bot_core 已有）  message.in / bot.replied / notice.*
    观测层（本包 ledger）    状态转移、待回复账本、超时扫描 → Observation
    仲裁层（本包 arbitrator） 规则闸门 → 模型判断 → 预算/冷却/优先级
    动作层（插件 + 出站队列） ctx.send_text → 事实围栏 → 行为留痕

新增一个主动性行为 = 在 ledger.scan()/arbitrator.BEHAVIORS 里加一条，
**不再新写一个插件**。所有行为共享同一份预算与冷却，不会叠加成轰炸。

纯函数优先：ledger.observe_* / scan、arbitrator.gate / rank / parse_model_decision
都不碰 IO，离线用例能把整段对话按秒推进。
"""
from . import ledger, arbitrator  # noqa: F401

__all__ = ["ledger", "arbitrator"]
