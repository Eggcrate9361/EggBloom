# -*- coding: utf-8 -*-
"""autonomy/heartbeat.py —— 心跳的规范入口（1.6.0 P3 归位到自主层）。

## 为什么单独一层

心跳的**驱动**（每 900 秒醒一次）在 `plugins/proactive_talk` /
`proactive_poke` 的线程里，改不动也不该改；心跳的**判据**却散在两处：
材料组装写在插件里、动作裁决写在当时的 `plugins/autonomy/policy.py`
（1.6.0 P3 后归位到 `autonomy/policy.py`）。
结果是「提示词说 L2、代码放行 L3」这类漂移没人兜。

本模块把「心跳那一轮」收成一条纯链路：

    账本状态 → materials_from_state() → build_prompt() → 模型输出
             → evaluate() → policy.guard() 裁决 → {act, ...}

全部是纯函数：不读时钟（now 由调用方传）、不发消息、不起线程、不碰盘。
`evaluate()` 只返回**决定**，真正发送仍在出站队列，绝不因为解析成功就自动发。

## 与 `harness/proactive_gate.py` 的分工

proactive_gate 管「该不该、能不能」开口（间隔/冷却/静默/预算）；
本模块管「模型说要做的动作在不在权限等级里」。两者是串联的两道闸，
不是替代关系 —— 主链先过 gate，再过本模块的 `evaluate()`。

## 接主链

`register()` 把心跳裁决登记成 agent 工具，`separation.bootstrap()` 在分离态
调用它（与 `autonomy.tools.register()` 同一处、同一套幂等约定）。产品态不注册，
行为逐字不变。
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from . import policy

#: 心跳里 `say` 对应的权限动作（对外发 = L2）
SAY_ACTION = "msg.send"
#: 默认等级：允许 say（L2），但 L3 不可逆动作仍需显式确认
DEFAULT_LEVEL = 2
#: 一次心跳最多允许的动作数
DEFAULT_MAX_ACTIONS = 1

__all__ = [
    "SAY_ACTION", "DEFAULT_LEVEL", "DEFAULT_MAX_ACTIONS",
    "materials_from_state", "build_prompt", "parse", "evaluate", "register",
]


def materials_from_state(state: Optional[Dict[str, Any]], target: str = "", *,
                         now: Optional[float] = None,
                         pending: str = "", last_line: str = "",
                         silent_for: str = "") -> Dict[str, Any]:
    """账本纯数据 → 心跳材料。**只放事实，不放结论**。

    材料里不写「应该开口」「他是不是生气了」——那属于模型/闸门，
    写进材料就成了自我暗示。字段与 `policy.heartbeat_prompt()` 一一对应。
    """
    st = state if isinstance(state, dict) else {}
    now = float(time.time() if now is None else now)
    t = str(target or "").strip()

    # 惦记的事：pending_topics[target]，账本里按人存
    topic = ""
    topics = st.get("pending_topics") or {}
    if isinstance(topics, dict) and t:
        topic = str(topics.get(t) or "").strip()
    if pending:                       # 调用方显式给的优先（人工触发用）
        topic = str(pending).strip()

    # 最近一句：只取账本里已有的文本，不读聊天记录文件
    line = str(last_line or "").strip()
    if not line:
        last = st.get("last_user_text") or ""
        line = str(last).strip()[:80]

    # 沉默时长：没给就按账本里的 last_user_ts_by / last_user_ts 算成人话
    silent = str(silent_for or "").strip()
    if not silent:
        by = st.get("last_user_ts_by") or {}
        try:
            lu = float((by.get(t) if isinstance(by, dict) else 0) or 0)
        except (TypeError, ValueError):
            lu = 0.0
        if not lu:
            try:
                lu = float(st.get("last_user_ts") or 0)
            except (TypeError, ValueError):
                lu = 0.0
        if lu and now > lu:
            hours = (now - lu) / 3600.0
            silent = f"{hours:.1f} 小时" if hours >= 1 else f"{int((now - lu) // 60)} 分钟"

    return {
        "now": time.strftime("%Y-%m-%d %H:%M", time.localtime(now)),
        "silent_for": silent,
        "last_line": line,
        "pending": topic,
    }


def build_prompt(materials: Dict[str, Any], level: int = DEFAULT_LEVEL, *,
                 max_actions: int = DEFAULT_MAX_ACTIONS) -> str:
    """材料 → 心跳提示词。等级与 `guard()` 同源，由 `policy` 渲染。"""
    return policy.heartbeat_prompt(materials or {}, level, max_actions=max_actions)


def parse(text: str, *, max_actions: int = DEFAULT_MAX_ACTIONS) -> Dict[str, Any]:
    """模型输出 → 结构化决定。解析不了就是 none，绝不猜。"""
    return policy.parse_decision(text, max_actions=max_actions)


def evaluate(text: str, *, materials: Optional[Dict[str, Any]] = None,
             level: int = DEFAULT_LEVEL, confirmed: bool = False,
             max_actions: int = DEFAULT_MAX_ACTIONS) -> Dict[str, Any]:
    """模型输出 → 决定 + 权限裁决。

    返回 `{act, text, action, args, why, verdict, reason, action_level,
    error, materials}`。`act` 取值：

    - `none`     —— 模型说不动、解析失败、或裁决 deny；
    - `confirm`  —— L3 不可逆动作，要主人确认后才能升级成 do；
    - `say`      —— 裁决通过的一句话（**仍是决定，不是发送**）；
    - `do`       —— 裁决通过的登记动作。

    未登记动作按 L3 从严，由 `policy.guard()` 直接 deny。
    """
    out: Dict[str, Any] = {
        "act": "none", "text": "", "action": "", "args": {}, "why": "",
        "verdict": "deny", "reason": "", "action_level": 0,
        "error": "", "materials": dict(materials or {}),
    }
    d = parse(text, max_actions=max_actions)
    out["why"] = d.get("why", "")
    out["error"] = d.get("error", "")
    if d.get("act") == "none":
        out["reason"] = d.get("error") or "模型决定不动"
        return out

    if d.get("act") == "say":
        action = SAY_ACTION
    else:
        action = str(d.get("action") or "")

    g = policy.guard(action, level, confirmed=bool(confirmed))
    out["verdict"] = g.get("verdict", "deny")
    out["reason"] = g.get("reason", "")
    out["action_level"] = g.get("action_level", 0)
    out["action"] = action
    out["args"] = d.get("args") or {}
    out["text"] = d.get("text", "")

    if g.get("verdict") == "need_confirm":
        out["act"] = "confirm"
        return out
    if g.get("verdict") != "allow":
        return out
    out["act"] = "say" if d.get("act") == "say" else "do"
    return out


# ── 分离态工具登记（接主链）──────────────────────────────────────
_TOOL_NAME = "autonomy_heartbeat_decide"
_SCHEMA = {
    "type": "function", "function": {
        "name": _TOOL_NAME,
        "description": "把心跳那轮的模型输出解析成结构化决定并过权限裁决。"
                       "只返回决定，不发送、不写盘。L3 动作返回 act=confirm。",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "模型那一轮的原始输出（含 JSON）"},
            "level": {"type": "integer", "description": "当前权限等级 0..3，默认 2"},
            "confirmed": {"type": "boolean", "description": "主人是否已确认不可逆动作"}},
            "required": ["text"]}},
}


def _tool(args):
    a = args if isinstance(args, dict) else {}
    return evaluate(a.get("text", ""), level=int(a.get("level") or DEFAULT_LEVEL),
                    confirmed=bool(a.get("confirmed", False)))


def register() -> int:
    """登记心跳裁决工具；重复调用幂等，未就绪时静默跳过。返回注册数。"""
    try:
        from harness import tool_registry as tr
        tr.register(_TOOL_NAME, _SCHEMA, fn=_tool,
                    groups=(tr.GROUP_AUTONOMY,), source="autonomy")
        return 1
    except Exception:
        return 0
