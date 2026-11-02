# -*- coding: utf-8 -*-
"""harness/tool_deep.py —— `deep_task`：agent 从"模式"降成"工具"（1.6.0 刀 3，2026-11-01）。

## 它解决什么

1.6.0 之前，"agent" 是一条**旁路**：命中开关就换一套大脑答完直接发出去 ——
人格、预算、闸、审计全部绕过（FIXPLAN §1.1）。用户的原话是「agent 模块其实还是没通入」。

这一刀把 `agent_service.run_chat`（多步工具循环）包成一个**普通工具**：
聊天模型继续走主链，真需要"多步动手"时自己决定调用 `deep_task(task)`，
拿回的是一段普通文本，照常过人格/预算/闸/审计再发出去 ——
**它只是一双手，不是第二个大脑。**

## 为什么带开关（默认关）

`deep_task` 会开一个能调工具的子 agent，代价与风险都高于普通工具：
`agent_as_tool`（默认 False）打开后才注入。关掉 = 模型根本看不到这个工具，
入口行为与 1.5.11 一致。

## 三道闸

1. **注入闸**：`agent_as_tool` 没开就不会出现在工具集里（见 bot_core._tools_subset_for_scene）；
2. **递归闸**：`deep_task` 里再调 `deep_task` 直接拒绝（子 agent 自己也拿得到工具清单，
   不挡的话会无限套娃）；
3. **就绪闸**：`agent_service.is_ready()` 为假（依赖没注入 / 测试环境）时返回人话错误，
   不是抛异常。
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional

#: 工具 schema（与 tool_task 同一形状：走 tool_registry.resolve）
DEEP_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "deep_task",
            "description": (
                "**要通读多个文件 / 审查代码 / 连着多步动手时，第一轮就用它** —— "
                "不要先一个个 ws_glob 列目录（工具轮次会被烧光，然后只能回「还没读完」）。"
                "它把整件活干完，回给你一段结果文字，你照常回话（别把过程念给用户听）；"
                "要读的范围写进 task。闲聊、一句话能答的别用 —— 它比直接回答慢得多。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string",
                             "description": "要它干什么，一句话说清（含必要的对象/路径）"},
                    "context": {"type": "string",
                                "description": "可选：已知的上下文（用户原话、已知信息）"},
                },
                "required": ["task"],
            },
        },
    },
]

#: 当前这一轮的场景信息（主链每轮写入；工具不该猜"现在在跟谁说话"）
_CTX: Dict[str, Any] = {"is_group": False, "group_id": "", "sender": ""}

#: 递归闸：同一时刻只允许一层 deep_task（agent 内部再调就拒）
_DEPTH = 0
_DEPTH_LOCK = threading.Lock()


def set_context(*, is_group: bool = False, group_id: str = "",
                sender: str = "") -> None:
    _CTX["is_group"] = bool(is_group)
    _CTX["group_id"] = str(group_id or "")
    _CTX["sender"] = str(sender or "")


def depth() -> int:
    """当前线程/进程内套了几层（测试用）。"""
    return _DEPTH


def _enter() -> bool:
    global _DEPTH
    with _DEPTH_LOCK:
        if _DEPTH >= 1:
            return False
        _DEPTH += 1
        return True


def _leave() -> None:
    global _DEPTH
    with _DEPTH_LOCK:
        if _DEPTH > 0:
            _DEPTH -= 1


def _scene_line() -> str:
    if _CTX.get("is_group"):
        gid = _CTX.get("group_id") or ""
        return f"【场景】群里（{gid}）有人让你办事；结果会由你转述给群里。"
    sender = _CTX.get("sender") or ""
    return f"【场景】私聊（{sender}）对方让你办事；结果会由你转述给对方。"


def deep_task(task: str = "", context: str = "", *,
              _runner: Optional[Any] = None) -> Dict[str, Any]:
    """跑一次多步任务，返回一段结果文字。

    `_runner` 只给测试用（注入假的 run_chat），生产环境不传。
    """
    t = str(task or "").strip()
    if not t:
        return {"ok": False, "error": "task 不能为空"}
    runner = _runner
    if runner is None:
        try:
            from . import agent_service as _as
        except Exception as e:                       # 依赖缺失不该炸主链
            return {"ok": False, "error": f"多步工具不可用（{type(e).__name__}）"}
        if not _as.is_ready():
            return {"ok": False, "error": "多步工具未就绪（agent 依赖未注入）"}
        runner = _as.run_chat

    if not _enter():
        return {"ok": False, "error": "已经在多步任务里了，别套娃（直接接着做）"}

    try:
        sid = ""
        try:
            from . import session_log as _slog
            tid = _slog.current_trace() or ""
            sid = f"deep-{tid}" if tid else ""
        except Exception:
            sid = ""
        hint = _scene_line()
        extra = str(context or "").strip()
        if extra:
            hint += "\n【已知】" + extra[:800]
        kwargs = {"user_input": t, "is_group": bool(_CTX.get("is_group")),
                  "context_hint": hint, "publish": False}
        if sid:
            kwargs["session_id"] = sid
        try:
            res = runner(**kwargs)
        except TypeError:
            # 老签名/测试桩可能不认 publish/session_id：退回最小参数再试一次
            kwargs.pop("publish", None)
            kwargs.pop("session_id", None)
            res = runner(**kwargs)
    except Exception as e:
        return {"ok": False, "error": f"多步任务失败（{type(e).__name__}: {e}）"}
    finally:
        _leave()

    if not isinstance(res, dict):
        return {"ok": False, "error": "多步任务没有返回结果"}
    if not res.get("ok"):
        return {"ok": False, "error": str(res.get("error") or "多步任务失败")[:200]}
    answer = str(res.get("answer") or "").strip()
    if not answer:
        return {"ok": False, "error": "多步任务跑完了但没有结论"}
    return {"ok": True, "answer": answer, "steps": int(res.get("steps") or 0)}


def register_all() -> Dict[str, Any]:
    """注册进工具注册表（`GROUP_DEEP` 组：只在 `agent_as_tool` 打开时注入）。"""
    from . import tool_registry as _tr
    fns = {"deep_task": deep_task}
    for sch in DEEP_TOOLS:
        name = sch["function"]["name"]
        _tr.register(name, sch, fn=_tr.adapt_kwargs(fns[name]),
                     groups=(_tr.GROUP_DEEP,), source="harness")
    return {k: _tr.adapt_kwargs(v) for k, v in fns.items()}


__all__ = ["DEEP_TOOLS", "set_context", "deep_task", "depth", "register_all"]
