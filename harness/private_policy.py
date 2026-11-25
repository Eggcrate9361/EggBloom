# -*- coding: utf-8 -*-
"""harness/private_policy.py —— 私聊的动作判定（拟人第 3 步 S2b-1）

## 为什么分阶段而不是一次判完

私聊现在是一条 if 串下来的流水线，顺序是多年堆出来的：

    表情包回敬 → 图片缓存 → 合批缓冲 → 撤回冷却 → 控制指令 → 开机冷却 → 速率限制 → 生成

「控制指令」夹在中间：它**不受**开机/速率冷却约束（指令照执行），
却**受**合批缓冲影响（会被攒 8 秒）。把判定一次算完就必须重排这三处，
那是行为变更，不属于"收口"。

所以这里把**规则**收进一个函数，用 `stage` 说明"现在走到哪一步"，
调用点只负责取 flag、执行结果。三处调用、一张判定表。

## 只做三件事

- `WAIT`：先攒着（合批）
- `SKIP`：这次不回（冷却）
- `TEXT`：继续生成

meme / poke / tool 都是旁路动作（有副作用，且执行点在流水线前后），
不在这一层判定 —— 见施工单 §8.6。
"""
from __future__ import annotations

from dataclasses import dataclass

from .action_policy import Action


@dataclass(frozen=True)
class PrivateDecision:
    action: Action
    reason: str

    @property
    def is_skip(self) -> bool:
        return self.action is Action.SKIP

    @property
    def is_wait(self) -> bool:
        return self.action is Action.WAIT


#: 判定表：每个阶段只有一条规则，顺序写死在函数里（不要改成数据驱动——
#:  私聊的分支差异在"什么时候问"，不在"规则本身"）。
def decide_private(*, stage: str, should_buffer: bool = False,
                   recall_left: float = 0.0, startup_left: float = 0.0,
                   rate_left: float = 0.0) -> PrivateDecision:
    """按阶段给出动作。纯函数：不读时钟、不碰全局。

    stage:
      "buffer"  —— 合批窗口（在控制指令之前）
      "recall"  —— 撤回反馈冷却（在控制指令之前）
      "startup" —— 开机冷却（在控制指令之后）
      "rate"    —— 速率限制（在控制指令之后）
    """
    st = str(stage or "")
    if st == "buffer":
        if should_buffer:
            return PrivateDecision(Action.WAIT, "合批窗口")
        return PrivateDecision(Action.TEXT, "")
    if st == "recall":
        if float(recall_left or 0) > 0:
            return PrivateDecision(Action.SKIP, "冷却：撤回反馈 %.1fs" % float(recall_left))
        return PrivateDecision(Action.TEXT, "")
    if st == "startup":
        if float(startup_left or 0) > 0:
            return PrivateDecision(Action.SKIP, "冷却：开机 %ds" % int(startup_left))
        return PrivateDecision(Action.TEXT, "")
    if st == "rate":
        if float(rate_left or 0) > 0:
            return PrivateDecision(Action.SKIP, "冷却：私聊速率 %.1fs" % float(rate_left))
        return PrivateDecision(Action.TEXT, "")
    # 未知阶段不猜：当作可以继续，调用方原有逻辑兜底
    return PrivateDecision(Action.TEXT, "")
