# -*- coding: utf-8 -*-
"""harness/tool_self.py —— 自我检索工具（2026-10-30）。

行为账本不再"只往 prompt 里塞最近几条"：模型自己可以按关键词/类型/时间查。

    self_history(query="天王盖地虎", hours=72, limit=10)

只读、零副作用，归 `work` 组（群聊私聊都常驻）。
"""
from __future__ import annotations

from typing import Any, Dict

SELF_TOOLS = [{
    "type": "function",
    "function": {
        "name": "self_history",
        "description": ("检索你自己做过的事：说过的话（群/私聊）、发的表情包、戳人、"
                        "采集。问『我之前说过什么』『昨天干了什么』『我发过哪些图』时用。"),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "关键词，可空"},
                "kind": {"type": "string",
                         "enum": ["say", "meme", "poke", "collect", "image", "proactive"],
                         "description": "动作类型，可空"},
                "hours": {"type": "number", "description": "往前查多少小时，默认 24"},
                "limit": {"type": "number", "description": "最多几条，默认 8"},
            },
            "required": [],
        },
    },
}]


def self_history(query: str = "", kind: str = "", hours: float = 24,
                 limit: int = 8) -> Dict[str, Any]:
    """查自己的行为账本（只读）。"""
    try:
        import self_actions as _sa
        rows = _sa.search(query=query, kind=kind, hours=hours, limit=limit)
        return {"ok": True, "count": len(rows), "rows": rows}
    except Exception as e:
        return {"ok": False, "count": 0, "rows": [], "error": f"{type(e).__name__}: {e}"}


def register_all() -> Dict[str, Any]:
    """注册进 harness.tool_registry（work 组：只读、常驻）。"""
    from . import tool_registry as _tr
    fns = {"self_history": self_history}
    for sch in SELF_TOOLS:
        name = sch["function"]["name"]
        _tr.register(name, sch, fn=fns.get(name), groups=(_tr.GROUP_WORK,),
                     source="harness")
    return fns


__all__ = ["SELF_TOOLS", "self_history", "register_all"]
