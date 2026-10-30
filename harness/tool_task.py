# -*- coding: utf-8 -*-
"""harness/tool_task.py —— 聊天模型可用的「派活 / 查进度」工具（2026-10-30）。

用户的设计：QQ 里别让 agent 接管回复，而是——

    聊天模型继续聊 → 需要干活时 dispatch_task(goal) 派给后台子 agent
    → 干完了系统回报 → 用户问「干完了吗」时 task_status 查

桌面 Agent 页不走这条路（那是同步的、人在等的），所以这两个工具只在 QQ 主链注册。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

_DISPATCH = None
_CTX: Dict[str, Any] = {"sender": "", "is_group": False, "group_id": ""}

TASK_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "dispatch_task",
            "description": ("把一件需要动手/查资料的事派给后台子 agent 去做。"
                            "派完立刻继续聊天，不要等；做完系统会回报，"
                            "用户问进度时用 task_status 查。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "要干什么，一句话说清"},
                },
                "required": ["goal"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_status",
            "description": "查后台子 agent 任务的进度/结果（不传 task_id 就列最近的）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "任务号，可空"},
                },
                "required": [],
            },
        },
    },
]


def set_dispatcher(fn) -> None:
    global _DISPATCH
    _DISPATCH = fn if callable(fn) else None


def set_context(*, sender: str = "", is_group: bool = False,
                group_id: str = "") -> None:
    """每轮由主链写入（工具本身不该猜"现在在跟谁说话"）。"""
    _CTX["sender"] = str(sender or "")
    _CTX["is_group"] = bool(is_group)
    _CTX["group_id"] = str(group_id or "")


def dispatch_task(goal: str) -> Dict[str, Any]:
    g = str(goal or "").strip()
    if not g:
        return {"ok": False, "error": "goal 不能为空"}
    if not callable(_DISPATCH):
        return {"ok": False, "error": "后台任务未接线"}
    try:
        tid = str(_DISPATCH(g, dict(_CTX)) or "")
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if not tid:
        return {"ok": False, "error": "派活失败（任务队列不可用）"}
    return {"ok": True, "task_id": tid,
            "note": "已派给后台子 agent，做完会回报；你可以继续聊，用户问进度用 task_status。"}


def task_status(task_id: str = "") -> Dict[str, Any]:
    try:
        from . import subagent as _sa
        info = _sa.status(str(task_id or ""))
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if not task_id:
        return {"ok": True, "tasks": info}
    if not info:
        return {"ok": False, "error": "没有这个任务号"}
    return {"ok": True, "task": info}


def register_all() -> Dict[str, Any]:
    from . import tool_registry as _tr
    fns = {"dispatch_task": dispatch_task, "task_status": task_status}
    for sch in TASK_TOOLS:
        name = sch["function"]["name"]
        _tr.register(name, sch, fn=fns.get(name), groups=(_tr.GROUP_WORK,),
                     source="harness")
    return fns


__all__ = ["TASK_TOOLS", "set_dispatcher", "set_context", "dispatch_task",
           "task_status", "register_all"]
