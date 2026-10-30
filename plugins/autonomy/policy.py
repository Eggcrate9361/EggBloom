# -*- coding: utf-8 -*-
"""plugins/autonomy/policy.py —— 自主性插件的策略层（2026-10-30，第一步）。

## 这一层只做四个纯函数

1. `action_level()`  —— 动作 → 权限等级（未登记 = L3，故意从严）
2. `guard()`         —— 按等级裁决：allow / deny / need_confirm
3. `permission_prompt()` —— 常驻的权限声明（提示词，等级由同一处渲染）
4. `heartbeat_prompt()` / `parse_decision()` —— 心跳材料 → 提示词；模型输出 → 结构化决定

**不 import 主程序、不碰网络、不起线程** —— 所以能离线单测，也能被插件本体
和将来的主链共用（同一份判据，避免"提示词说 L2、代码放行 L3"的漂移）。

## 为什么未登记的动作按 L3

漏登记一张表里的一行，最坏结果是"多问一句"；反过来（默认 L0）最坏结果是
"它自己把视频发出去了"。所以默认从严，新增动作必须显式登记。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

#: 等级名（提示词与 UI 共用）
LEVELS: Dict[int, str] = {
    0: "只读",
    1: "本地写",
    2: "对外发",
    3: "不可逆",
}

#: 每一级**允许**的动作（含更低级）。提示词里的"你能做什么"从这里渲染。
LEVEL_ACTIONS: Dict[int, Tuple[str, ...]] = {
    0: ("memory.search", "group.list", "fs.read", "web.search", "vision.read"),
    1: ("fs.write", "config.write", "plugin.install"),
    2: ("msg.send", "image.send", "comment.post", "file.send"),
    3: ("publish.video", "pay", "delete", "group.kick", "group.rename"),
}

#: 动作 → 等级。**唯一来源**；未登记 = L3。
ACTION_LEVELS: Dict[str, int] = {}
for _lv, _acts in LEVEL_ACTIONS.items():
    for _a in _acts:
        ACTION_LEVELS[_a] = _lv

#: 未登记动作的等级
UNKNOWN_ACTION_LEVEL = 3

#: L3 动作必须人确认
CONFIRM_LEVEL = 3


def level_of(action: str) -> int:
    """动作的权限等级；没登记按 L3。"""
    return int(ACTION_LEVELS.get(str(action or "").strip(), UNKNOWN_ACTION_LEVEL))


def guard(action: str, level: int, confirmed: bool = False) -> Dict[str, Any]:
    """按等级裁决一个动作。

    返回 `{verdict, action, action_level, level, reason}`，
    verdict ∈ allow / deny / need_confirm。
    """
    a = str(action or "").strip()
    try:
        lv = int(level)
    except (TypeError, ValueError):
        lv = 0
    al = level_of(a)
    base = {"action": a, "action_level": al, "level": lv}

    if not a:
        return {**base, "verdict": "deny", "reason": "动作名为空"}
    if a not in ACTION_LEVELS:
        return {**base, "verdict": "deny",
                "reason": f"未登记的动作（按 L{UNKNOWN_ACTION_LEVEL} 处理）：{a}"}
    if al > lv:
        return {**base, "verdict": "deny",
                "reason": f"你是 L{lv}（{LEVELS.get(lv, '?')}），这个动作要 L{al}（{LEVELS.get(al, '?')}）"}
    if al >= CONFIRM_LEVEL and not confirmed:
        return {**base, "verdict": "need_confirm",
                "reason": f"L{al} 不可逆，需要主人确认"}
    return {**base, "verdict": "allow", "reason": ""}


def permission_prompt(level: int) -> str:
    """常驻权限声明（≤80 字）。等级与 `guard()` 同源，改一处两边都变。"""
    try:
        lv = max(0, min(3, int(level)))
    except (TypeError, ValueError):
        lv = 0
    can: List[str] = []
    cannot: List[str] = []
    for l, acts in LEVEL_ACTIONS.items():
        if l <= lv:
            can.extend(acts)
        else:
            cannot.extend(acts)
    return (
        f"【自主性·权限 L{lv}】你能主动做：{'、'.join(can) or '无'}；"
        f"不能做：{'、'.join(cannot) or '无'}。"
        "越界会被直接拒绝，拒绝原因会回到你这轮输入里 —— 别换个说法再试。"
    )


def heartbeat_prompt(materials: Dict[str, Any], level: int, *,
                     max_actions: int = 1) -> str:
    """心跳那一轮的材料 + 判断要求。

    `materials` 里放事实（时间/沉默时长/最近一句/惦记的事），不放结论。
    """
    m = dict(materials or {})
    when = str(m.get("now") or "").strip()
    silent = str(m.get("silent_for") or "").strip()
    last = str(m.get("last_line") or "").strip()
    pending = str(m.get("pending") or "").strip()
    bits = [f"【自主性·心跳】现在 {when}。" if when else "【自主性·心跳】"]
    if silent:
        bits.append(f"距你上次主动开口 {silent}。")
    if last:
        bits.append(f"对方最近说过「{last}」。")
    if pending:
        bits.append(f"你有 1 件惦记的事：「{pending}」。")
    bits.append(
        "你要不要动？默认不动。只回一个 JSON："
        '{"act":"none"} 或 {"act":"say","text":"…","why":"…"} '
        '或 {"act":"do","action":"…","args":{},"why":"…"}。'
        f"一次最多 {max(1, int(max_actions))} 个动作；做完把结果告诉我，别连着做第二件。"
        f"（当前等级 L{int(level)}）"
    )
    return "".join(bits)


_JSON_RE = re.compile(r"\{.*\}", re.S)


def parse_decision(text: str, *, max_actions: int = 1) -> Dict[str, Any]:
    """模型输出 → 结构化决定。解析不出来一律 none（绝不猜）。

    返回 `{act, text, action, args, why, error}`；act ∈ none / say / do。
    """
    raw = str(text or "").strip()
    out: Dict[str, Any] = {"act": "none", "text": "", "action": "",
                           "args": {}, "why": "", "error": ""}
    if not raw:
        out["error"] = "空输出"
        return out
    m = _JSON_RE.search(raw)
    if not m:
        out["error"] = "没有 JSON"
        return out
    try:
        d = json.loads(m.group(0))
    except Exception as e:
        out["error"] = f"JSON 解析失败: {type(e).__name__}"
        return out
    if not isinstance(d, dict):
        out["error"] = "不是对象"
        return out
    act = str(d.get("act") or "").strip().lower()
    if act not in ("none", "say", "do"):
        out["error"] = f"未知 act: {act or '(空)'}"
        return out
    out["act"] = act
    out["why"] = str(d.get("why") or "")[:120]
    if act == "say":
        t = str(d.get("text") or "").strip()
        if not t:
            out["act"] = "none"
            out["error"] = "say 缺 text"
            return out
        out["text"] = t[:600]
    elif act == "do":
        a = str(d.get("action") or "").strip()
        if not a:
            out["act"] = "none"
            out["error"] = "do 缺 action"
            return out
        out["action"] = a
        args = d.get("args")
        out["args"] = args if isinstance(args, dict) else {}
        if int(max_actions) <= 0:
            out["act"] = "none"
            out["error"] = "本轮不允许动作"
    return out


__all__ = ["LEVELS", "LEVEL_ACTIONS", "ACTION_LEVELS", "UNKNOWN_ACTION_LEVEL",
           "CONFIRM_LEVEL", "level_of", "guard", "permission_prompt",
           "heartbeat_prompt", "parse_decision"]
