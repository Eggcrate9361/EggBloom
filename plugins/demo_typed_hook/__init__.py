# -*- coding: utf-8 -*-
"""示例插件：typed 钩子（ABI v2）—— 演示 `on_before_reply` / `on_after_reply`。

## 这个插件的用途

在此之前，`on_before_reply` / `on_after_reply` 虽然写在 ABI 里，
**bot_core 从未派发过它们**，所以照 ABI 写这两个钩子等于白写。
本插件是那个缺口被补上之后的**活样例 + 可运行验收**：装上它就能在日志里
看到「注入进了 prompt」。

## 它做什么（不打扰正常聊天）

| 钩子 | 触发条件 | 行为 |
|---|---|---|
| `on_before_reply` | 消息里出现 `#demo` | 注入一段上下文块（证明 prompt 注入生效） |
| `on_after_reply` | 回复里含 `[演示]` | 在回复末尾追加一行标记（证明回复可被改写） |

其余情况一律返回 `None`（= 不干预），所以默认不会改变任何既有行为。

## 三个钩子的返回值契约（照抄即可）

```python
def on_before_reply(ctx, event):
    # event = {"message", "group_id", "context", "scope"}
    return {"action": "inject",   "content": "要注入的文本"}   # 往 prompt 注入
    return {"action": "skip",     "reason": "为什么不该回"}    # 拦截本轮回复
    return {"action": "override", "reply": "直接给定回复"}     # 代替 AI 回复
    return None                                                # 不干预
    # 简写也认：直接 return "一段文本"（等价于 inject）

def on_after_reply(ctx, event, reply):
    # event = {"message", "group_id", "context", "scope"}；reply = 当前回复文本
    return "替换后的回复"   # 覆盖本次回复
    return ""              # ★ 空串 = 抑制发送（本轮不发）
    return None            # 不修改
```

## 设计要点（为什么这么写）

1. **默认关闭**：`default_enabled=False`。示例插件不该悄悄改线上行为。
2. **只在显式标记时动作**：靠 `#demo` / `[演示]` 触发，方便你手动验证。
3. **不 import 主程序**：符合 ABI 三原则，只用 `ctx`。
4. **返回值只用标准 action**：这是给所有 typed 插件做示范。
"""
from plugins.abi import PluginContext

PLUGIN_SPEC = 1
PLUGIN_MANIFEST = {
    "name": "demo_typed_hook",
    "version": "1.0.0",
    "api_version": 2,
    "author": "EggBloom",
    "description": "示例插件：演示 typed 钩子（on_before_reply 注入 / on_after_reply 改写）",
    "dependencies": [],
    "priority": 45,
    "default_enabled": False,
}

#: 触发注入的标记
TRIGGER_IN = "#demo"
#: 触发改写的标记（出现在 AI 回复里）
TRIGGER_OUT = "[演示]"


def on_load(ctx: PluginContext):
    ctx.log("demo_typed_hook loaded — typed 钩子已接线（on_before_reply / on_after_reply）")


def on_enable(ctx: PluginContext):
    ctx.log(f"demo_typed_hook enabled（发含 {TRIGGER_IN} 的消息可验证注入）")


def on_disable(ctx: PluginContext):
    ctx.log("demo_typed_hook disabled")


def on_unload(ctx: PluginContext):
    ctx.log("demo_typed_hook unloaded")


# ── typed 钩子（ABI v2）───────────────────────────────────────────

def on_before_reply(ctx: PluginContext, event: dict):
    """往 prompt 注入一段上下文；非触发词返回 None（不干预）。

    event = {"message": str, "group_id": str, "context": dict, "scope": "group"|"private"}
    """
    message = (event.get("message") or "")
    if TRIGGER_IN not in message:
        return None

    scope = event.get("scope") or ("group" if event.get("group_id") else "private")
    ctx.log(f"demo_typed_hook 命中触发词，向 {scope} 回复注入上下文")

    return {
        "action": "inject",
        "content": (
            "【演示插件】这条消息带 #demo 标记，说明 typed 钩子 "
            "on_before_reply 已经把这段文本注入了 prompt。"
            "请在回复里自然地提到「演示注入已生效」。"
        ),
    }


def on_after_reply(ctx: PluginContext, event: dict, reply: str):
    """改写回复：若 AI 回复里出现 [演示]，则在末尾追加标记。

    event = {"message", "group_id", "context", "scope"}；reply = 当前回复文本
    返回 str（新文本）/ ""（抑制发送）/ None（不修改）
    """
    if not reply or TRIGGER_OUT not in reply:
        return None

    new_reply = reply.rstrip() + "（演示改写已生效）"
    ctx.log(f"demo_typed_hook 改写回复: {new_reply[:60]}")
    return new_reply


def on_message(ctx: PluginContext, event: dict):
    """本插件不直接回复消息，只演示 typed 钩子。"""
    return None


PLUGIN_HOOKS = {
    "on_load": on_load,
    "on_enable": on_enable,
    "on_disable": on_disable,
    "on_unload": on_unload,
    "on_message": on_message,
    # ★ 这两行是关键：以前注册了也永不执行，现在会被 bot_core 派发
    "on_before_reply": on_before_reply,
    "on_after_reply": on_after_reply,
}
