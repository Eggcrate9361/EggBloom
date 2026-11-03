# -*- coding: utf-8 -*-
"""plugins/recall_manager/policy.py —— 撤回反馈的判定链（2026-11-03，刀 1：纯新增）

执行总表 §4.1.2：`recall_feedback_*` 那套逻辑现在整块长在 `bot_core` 主链里
（`_handle_recall_event` 里 244 行、9 个配置键、3 处模块级状态）。
这一刀先把**判定链**抽出来，做成能离线单测的纯函数；接线（含 `__init__.py`
与主链开关）留到刀 2。

## 与主链逐条对齐（顺序即优先级，改动会改行为）

群聊：
    1. `recall_feedback_enable`（默认 True）关掉 → 不回
    2. 冷却：同 key 距上次反馈 < `recall_feedback_cooldown`（默认 60s）→ 不回
    3. 值不值得回：`buffer_cancelled`（撤回前 bot 正憋着回复）→ 必回；
       否则要 bot 最近 `recall_feedback_context_window`（默认 300s）在这个群说过话
       —— 用户原话「不要什么撤回都回」
    4. 文案类别：截断反馈（truncated）/ 已读反馈（seen）

私聊：
    1. 必须**有活跃会话**（无人会话的随手撤回不回）
    2. 会话空闲超过 `PRIVATE_SESSION_IDLE_TIMEOUT` → 不回
    3. `recall_private_enable`（默认 True）关掉 → 不回
    4. 冷却：`recall_private_cooldown`（默认 60s，与群聊分开算）

## 比现在的实现多一条（文档要求、原来没有）

**同一条 message_id 只反馈一次**。原来的冷却按会话算，二次撤回（群管理批量撤回、
或协议重复推送）在冷却窗口外仍会再回一句；这里用 `state["seen"]` 去重。

## 纪律

纯函数：不 import 主程序、不碰网络、不读盘、不随机。
时间、配置、状态、`buffer_cancelled` / `last_bot_msg` / 会话状态全部由调用方传入 ——
这些在主链里是现成的，插件侧要靠刀 2 把它们塞进 `notice.recall` 的载荷。
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

#: 场景
SCENE_GROUP = "group"
SCENE_PRIVATE = "private"

#: 动作
ACT_SAY = "say"
ACT_SKIP = "skip"

#: 文案类别
KIND_TRUNCATED = "truncated"   # 撤回前 bot 正憋着回复 → 截断反馈
KIND_SEEN = "seen"             # bot 最近说过话 → 已读反馈
KIND_PRIVATE = "private"       # 私聊撤回

#: 默认会话空闲上限（与 bot_core.PRIVATE_SESSION_IDLE_TIMEOUT 同值）
DEFAULT_IDLE_TIMEOUT = 1800.0

#: 预置文案（与主链内置的那几组一致；自定义配置为空时用它）
PRESET_TRUNCATED = (
    "？",
    "你撤回了一条消息",
    "还好你撤了 我差点就回了",
    "撤回了？行 那我也不用回了",
    "还没看呢 你就撤了",
    "手速可以啊 我还没来得及回",
)
PRESET_SEEN = (
    "看到了 晚了",
    "看到了哦 不说",
    "太慢了 我已经看到了",
    "来不及了 嘿嘿",
    "撤回慢了 已截获",
    "晚了 我看到了",
    "撤回无效 本人已阅",
)
PRESET_PRIVATE = (
    "撤回了？我还没看到",
    "什么情况 手滑了？",
    "你撤回的速度比打字快多了",
    "心虚了吧",
    "好家伙 说撤就撤",
)


def _num(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _texts(raw) -> List[str]:
    """把配置里的文案列表规整成非空字符串列表（空/非列表 → []）。"""
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for x in raw:
        s = str(x or "").strip()
        if s:
            out.append(s)
    return out


def fire_key(scene: str, ident: Any) -> str:
    """冷却/去重用的键。与主链的 `group_<gid>` / `private_<uid>` 完全一致。"""
    return f"{SCENE_GROUP}_{ident}" if scene == SCENE_GROUP else f"{SCENE_PRIVATE}_{ident}"


def should_fire(*, buffer_cancelled: bool, last_bot_msg: float,
                now: float, window: float = 300.0) -> bool:
    """撤回反馈值不值得回：只看"跟 bot 有没有关系"。

    与 `bot_core._recall_should_fire` 同一判据（窗口下限夹在 10 秒）。
    """
    if buffer_cancelled:
        return True
    try:
        return bool(last_bot_msg) and (_num(now, 0) - _num(last_bot_msg, 0)) <= max(10.0, _num(window, 300))
    except Exception:
        return False


def decide(event: dict, *, cfg: dict, now: float, state: Optional[dict] = None) -> Dict[str, Any]:
    """判定一条撤回事件要不要回、回哪一类。

    `event` 需要（主链里都是现成的，刀 2 会塞进 `notice.recall` 载荷）：
        notice_type       group_recall / friend_recall
        group_id          群号（私聊为空）
        operator          撤回者
        message_id        被撤回的消息 id（去重用）
        buffer_cancelled  撤回前 bot 是否正憋着一条回复（仅群聊）
        last_bot_msg      bot 上次在该群说话的时间戳（仅群聊）
        session_active    私聊是否有活跃会话
        idle_seconds      私聊会话空闲秒数

    `state`：
        {"last_fire": {key: ts}, "seen": {"<key>:<msg_id>": ts}}

    返回：
        {act, reason, scene, key, kind, texts}
        `act="say"` 时 `texts` 是候选文案池（调用方自己挑一条，或换成模型生成）。
    """
    ev = event if isinstance(event, dict) else {}
    c = cfg if isinstance(cfg, dict) else {}
    st = state if isinstance(state, dict) else {}
    notice = str(ev.get("notice_type") or "")
    gid = str(ev.get("group_id") or "").strip()
    op = str(ev.get("operator") or ev.get("user_id") or "").strip()
    msg_id = str(ev.get("message_id") or "").strip()

    out = {"act": ACT_SKIP, "reason": "", "scene": "", "key": "",
           "kind": "", "texts": []}

    # ── 群聊 ──
    if notice == "group_recall" and gid:
        scene, key = SCENE_GROUP, fire_key(SCENE_GROUP, gid)
        out.update(scene=scene, key=key)

        if not c.get("recall_feedback_enable", True):
            out["reason"] = "群撤回反馈已关闭"
            return out

        cooldown = _num(c.get("recall_feedback_cooldown"), 60)
        last = _num((st.get("last_fire") or {}).get(key), 0)
        if last and (_num(now, 0) - last) < cooldown:
            out["reason"] = f"群撤回反馈冷却中（还需 {int(cooldown - (_num(now, 0) - last))}s）"
            return out

        seen_key = f"{key}:{msg_id}" if msg_id else ""
        if seen_key and seen_key in (st.get("seen") or {}):
            out["reason"] = "同一条撤回已经反馈过"
            return out

        if not should_fire(buffer_cancelled=bool(ev.get("buffer_cancelled")),
                           last_bot_msg=_num(ev.get("last_bot_msg"), 0),
                           now=now,
                           window=_num(c.get("recall_feedback_context_window"), 300)):
            out["reason"] = "与 bot 无关（没被截断、最近没在这个群说过话）"
            return out

        kind = KIND_TRUNCATED if ev.get("buffer_cancelled") else KIND_SEEN
        if kind == KIND_SEEN and not ev.get("user_recent", False):
            # 主链的"已读反馈"要求真的在最近消息里看到过撤回者 ——
            # 没看到就只是"某人撤了条我没见过的消息"，不该接话。
            out["reason"] = "没看到用户最近的消息，不接话"
            return out
        out.update(act=ACT_SAY, reason="", kind=kind, texts=texts_for(kind, c))
        return out

    # ── 私聊 ──
    if notice == "friend_recall" or (notice and not gid):
        scene, key = SCENE_PRIVATE, fire_key(SCENE_PRIVATE, op)
        out.update(scene=scene, key=key)

        if not op or op in ("None", "0"):
            out["reason"] = f"撤回人无效（{op or '空'}）"
            return out
        if not ev.get("session_active"):
            out["reason"] = "无活跃私聊会话"
            return out
        idle = _num(ev.get("idle_seconds"), 0)
        if idle > _num(ev.get("idle_timeout"), DEFAULT_IDLE_TIMEOUT):
            out["reason"] = f"私聊会话已超时（{int(idle)}s）"
            return out
        if not c.get("recall_private_enable", True):
            out["reason"] = "私聊撤回反馈已关闭"
            return out

        cooldown = _num(c.get("recall_private_cooldown"), 60)
        last = _num((st.get("last_fire") or {}).get(key), 0)
        if last and (_num(now, 0) - last) < cooldown:
            out["reason"] = f"私聊撤回反馈冷却中（还需 {int(cooldown - (_num(now, 0) - last))}s）"
            return out

        seen_key = f"{key}:{msg_id}" if msg_id else ""
        if seen_key and seen_key in (st.get("seen") or {}):
            out["reason"] = "同一条撤回已经反馈过"
            return out

        out.update(act=ACT_SAY, reason="", kind=KIND_PRIVATE,
                   texts=texts_for(KIND_PRIVATE, c))
        return out

    out["reason"] = f"不是撤回事件：{notice or '(空)'}"
    return out


def texts_for(kind: str, cfg: Optional[dict] = None) -> List[str]:
    """按类别取候选文案池：自定义配置优先，空则用预置。"""
    c = cfg if isinstance(cfg, dict) else {}
    if kind == KIND_TRUNCATED:
        return _texts(c.get("recall_feedback_pre_messages")) or list(PRESET_TRUNCATED)
    if kind == KIND_SEEN:
        return _texts(c.get("recall_feedback_post_messages")) or list(PRESET_SEEN)
    if kind == KIND_PRIVATE:
        return _texts(c.get("recall_private_post_messages")) or list(PRESET_PRIVATE)
    return []


def remember(state: dict, key: str, *, now: float, message_id: str = "",
             keep_seen: int = 200) -> dict:
    """把"已反馈"写回状态（纯函数：返回新 dict，不改入参）。

    调用方在**发送成功之后**才调它 —— 发失败不该吃冷却。
    """
    st = dict(state or {})
    lf = dict(st.get("last_fire") or {})
    lf[str(key)] = _num(now, 0)
    st["last_fire"] = lf
    if message_id:
        seen = dict(st.get("seen") or {})
        seen[f"{key}:{message_id}"] = _num(now, 0)
        if len(seen) > keep_seen:                      # 有界，别让状态无限长
            for k in sorted(seen, key=lambda x: seen[x])[:len(seen) - keep_seen]:
                seen.pop(k, None)
        st["seen"] = seen
    return st


def pick(texts: List[str], *, rng=None) -> str:
    """从文案池里挑一条；空池返回空串。`rng` 可注入以便用例确定。"""
    pool = [str(t) for t in (texts or []) if str(t).strip()]
    if not pool:
        return ""
    return (rng or random.choice)(pool)


__all__ = [
    "SCENE_GROUP", "SCENE_PRIVATE", "ACT_SAY", "ACT_SKIP",
    "KIND_TRUNCATED", "KIND_SEEN", "KIND_PRIVATE", "DEFAULT_IDLE_TIMEOUT",
    "PRESET_TRUNCATED", "PRESET_SEEN", "PRESET_PRIVATE",
    "fire_key", "should_fire", "decide", "texts_for", "remember", "pick",
]
