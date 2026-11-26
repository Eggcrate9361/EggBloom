# -*- coding: utf-8 -*-
"""harness/action_policy.py —— 动作词汇表（拟人主线第 3 步的地基）

现在这个仓库里，"她要不要说话、用什么方式说"散在三处，各说各的词：

| 位置 | 取值 | 谁在用 |
|---|---|---|
| `bot_core._decide()` | `reply` / `skip` / `observe` | 群聊主链 |
| `plugins/group_chat_decider` | 同上（打分后给建议） | 群聊决策插件 |
| `harness/proactivity/arbitrator` | `skip` / `say` / `poke` / `meme` | 主动层 |
| 私聊分支（散在 main 里） | 无枚举，靠 `continue` | 私聊 |

四个地方、四套词，于是"同一事件两个结论"根本没法比较 —— 连名字都不一样。

这一层只做两件事，不做判断：

1. 定义**唯一的动作词表**（`Action`）；
2. 提供旧词 → 新词的映射（`from_group_act` / `from_proactive_kind`），
   让各处先能翻译成同一种说法，再谈合并。

## 为什么不用裸字符串

裸字符串没有拼写检查：`"observe"` 写成 `"observ"` 不会报错，只会静默走 else。
`Action` 是 `str` 子类，老的 `== "reply"` 比较和字典键用法**照旧可用**，
`str()` 出来仍是 `reply` 而不是 `Action.TEXT`。

## 明确不做

- 不判断该不该说话（那是第 3 步后续的 policy 层）；
- 不改任何调用点的行为（本文件只是词表 + 映射）。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional


class Action(str, Enum):
    """她可以做的事，一次只做一件。"""

    SKIP = "skip"          # 不回，也不安排后续
    OBSERVE = "observe"    # 先看着，到点再判断
    WAIT = "wait"          # 等对方补完（合批窗口内）
    TEXT = "reply"         # 说话（群聊历史里叫 reply，保留原值避免改日志）
    MEME = "meme"          # 只发表情包
    POKE = "poke"          # 戳一戳
    TOOL = "tool"          # 调工具/走 Agent，结果由工具决定说不说

    #: 让 `str(Action.TEXT)` 输出 `reply`，而不是 `Action.TEXT`。
    #   str 混入的 Enum 默认 __str__ 是 "Action.TEXT"，日志会全变样。
    __str__ = str.__str__

    @classmethod
    def of(cls, value: Any, default: Optional["Action"] = None):
        """把任意旧写法翻译成动作；认不出就用 default（默认 None，不猜）。"""
        if isinstance(value, Action):
            return value
        s = str(value or "").strip().lower()
        if s in _ALIASES:
            return _ALIASES[s]
        return default


#: 旧词 → 新词。**只加这里**，不要在调用点各写一份 if。
_ALIASES: Dict[str, Action] = {
    # 群聊主链 / group_chat_decider
    "reply": Action.TEXT,
    "text": Action.TEXT,
    "say": Action.TEXT,
    "speak": Action.TEXT,
    "skip": Action.SKIP,
    "observe": Action.OBSERVE,
    "wait": Action.WAIT,
    # 主动层的 kind
    "poke": Action.POKE,
    "meme": Action.MEME,
    "tool": Action.TOOL,
    "task": Action.TOOL,
    "admin": Action.TOOL,
    "command": Action.TOOL,
    # self_actions 里已有的两种：发图 = 表情/图片动作；提示语 = 说话
    "image": Action.MEME,
    "notice": Action.TEXT,
}


def from_group_act(act: Any) -> Action:
    """群聊 `_decide()` 的返回值 → 动作。认不出当 skip（保守：不说话比说错好）。"""
    return Action.of(act, Action.SKIP)


def from_proactive_kind(kind: Any) -> Action:
    """主动层的行为名 → 动作。主动层没有"reply"，`say` 就是说话。"""
    return Action.of(kind, Action.SKIP)


def from_proactive_act(act: Any) -> Action:
    """主动层的 `act` → 动作。

    注意 `image` / `video`：在主动层它们是「任务产物直发」，不是发表情包，
    所以归 TOOL；self_actions 里同名的 `image` 才是"发了张图"（归 MEME）。
    同一个词在不同层含义不同，映射必须分开写，不能塞进同一张别名表。
    """
    a = str(act or "").strip().lower()
    if a in ("image", "video"):
        return Action.TOOL
    return Action.of(a, Action.SKIP)


def describe(action: Action) -> str:
    """给日志用的一句话。**不要**把这张表塞进 prompt。"""
    return {
        Action.SKIP: "不回",
        Action.OBSERVE: "先看着",
        Action.WAIT: "等一等",
        Action.TEXT: "说话",
        Action.MEME: "发表情",
        Action.POKE: "戳一下",
        Action.TOOL: "走工具",
    }.get(action, str(action))
