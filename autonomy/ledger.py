# -*- coding: utf-8 -*-
"""autonomy/ledger.py —— 会话状态与待回复账本（1.6.0 P3 归位到自主层）。

归位说明：原文件在 `harness/proactivity/ledger.py`。账本与心跳是自主层的状态，
不该挂在 harness 观测层里；旧路径保留纯转发兼容壳，老调用点逐字可用。

## 这一层只做一件事：把「发生了什么」变成可查询的状态

它**不判断该不该说话、不调模型、不发消息**。判断在 arbitrator，发送在插件。
边界划死的理由：观测错了后面全错，而观测是最容易被"顺手加个 if"污染的地方。

## 状态是纯数据，转移是纯函数

`observe_*` 都返回**新 dict**，不改入参，不读盘、不读时钟（now 由调用方传）。
这样离线用例能把一整段对话按秒推进，不需要 sleep、不需要真 bot。

## 「我在等回复」怎么定义

    bot 真发出一条消息 → pending=True, pending_since=ts, pending_trace=trace_id
    之后同一个会话里有任何入站消息 → pending=False

多气泡是同一个 trace_id 的连续几条 `bot.replied`，由 `BUBBLE_WINDOW_S` 合并成一个
等待窗口（不然发两句话会被当成等两次，各触发一次追问）。

## 明确不做

- 不推测情绪/动机（"他是不是生气了"），只记录可观测的时间与文本；
- 不把"没回复"等同于"不理我"，那只是超时，判断交给仲裁层；
- 不自己起线程，不碰网络。
"""
from __future__ import annotations

import copy
import json
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

#: 同一 trace_id 的多气泡当一轮；超过这个间隔就当两轮
BUBBLE_WINDOW_S = 30.0

#: 「我等下给你 / 回头发你」这类**未兑现话头**的识别（纯词表，规则层）
#: 只认第一人称 + 明确延后词，避免把"你等下再来"算成自己的承诺
_PROMISE_RE = re.compile(
    r"(我|这边|等我)\s*(等下|等会|待会|一会|回头|稍后|晚点|过会|明天|等一下)"
    r".{0,12}(给|发|回|看|查|找|弄|说|补|整理)"
)


#: 语气统计用的词表 —— 只数命中，不做语义判断。
_TONE_NEG = ("滚", "烦", "闭嘴", "傻", "笨", "垃圾", "差劲", "没用", "恶心", "爬")
_TONE_POS = ("谢谢", "厉害", "可爱", "喜欢", "好棒", "牛", "爱你", "不错", "行啊")
#: 承诺识别（宽版）：允许省略主语「我」——"等下给你发"也算。
#: 只用于场景状态记账，**不改** scan 里 `_PROMISE_RE` 的判据（那会动到
#: pending_topic 的行为）。
#:
#: 2026-11-24 用 151 条真实出站语料校准过：初版把「明天」也当延后词，
#: 结果命中的唯一一条是「那我也睡了，明天再说」——误报。去掉「明天」、
#: 并要求延后词后 8 字内出现对象或动作（你/给/问/查/看看）后：
#: 语料 0 命中 0 误报；合成正例「等下给你发 / 等下发你 / 回头帮你问问」全中。
#: 已知不认「等我看看」这类太泛的说法 —— 宁可漏，不要误记。
_COMMIT_RE = re.compile(
    r"(等下|等会|待会|一会|回头|稍后|晚点|过会)"
    r".{0,8}(给你|帮你|发你|你|问|查|看看)"
)
#: 她说自己**已经做完**某事的说法（对账用：没真做就是编，见 C2）
_CLAIM_DONE_RE = re.compile(
    r"(发你了|发给你了|已经发了|传过去了|弄好了|改好了|查到了|找到了|"
    r"放群里了|给你发了|给你传了|已经放到)"
)


def key_of(scene: str, target: str) -> str:
    """统一的会话键。私聊用 QQ，群聊用群号，绝不混在一起。"""
    return "%s:%s" % (str(scene or "private"), str(target or ""))


def new_state(scene: str, target: str) -> Dict[str, Any]:
    return {
        "key": key_of(scene, target),
        "scene": str(scene or "private"),
        "target": str(target or ""),
        "last_bot_ts": 0.0,
        "last_bot_text": "",
        "last_bot_trace": "",
        "last_user_ts": 0.0,
        "last_user_text": "",
        "last_user_sender": "",
        "last_activity_ts": 0.0,     # 双方任一次活动（bot 或对方），冷场判定用
        "last_at_ts": 0.0,          # 最近一次被 @ / 点名且没等到回复
        "last_at_text": "",
        "pending": False,
        "pending_since": 0.0,
        "pending_trace": "",
        "promises": [],             # [{ts, text}]
        "notices": [],              # 最近的 notice（撤回等），只留少量
        # ★ 2026-11-07 P3：12 条行为需要的几个"小账本"
        "user_streak": 0,           # 同一个人连续说了几条（中间没人插话）
        "streak_start": 0.0,
        "streak_last_ts": 0.0,
        "streak_text": "",
        "last_other_ts": 0.0,       # 群里"别人"最后说话的时间（判断是否被晾着）
        "image_only_ts": 0.0,       # 只发图/文件、没配文字
        "image_only_n": 0,
        "named_ts": 0.0,            # 提到名字但没 @
        "named_text": "",
        "resume_gap_h": 0.0,        # 这次开口距上次互动多少小时（长时间静默后回来了）
        "events_recent": [],       # 群活跃度：最近 6 小时的消息时间戳（要看冷场前多热闹）
        "delivery_failed_ts": 0.0,  # 最近一次发送明确失败；用于区分“没回”与“没送到”
        "delivery_failed_text": "",
        "delivery_ok_ts": 0.0,      # 最近一次发送成功
        "last_skip_ts": 0.0,        # 主链明确选择不接这条
        "last_skip_reason": "",
        # ── 2026-11-24 场景状态（拟人主线第 2 步）──────────────────────
        # 全部是**可观测统计**，不是情绪推断（见模块头「明确不做」）：
        # tone 只由字数、标点、夸/骂词命中算出；relation 只做外部值的透传 +
        # 最近一次互动是夸还是骂。没有任何"她此刻开心吗"的猜测。
        "tone": 0.0,                # -1..1，最近若干条入站的语气统计（负=冲，正=软）
        "tone_n": 0,                # 参与统计的样本数
        "relation_level": None,     # 外部传入的亲密度（好感度），None = 不知道
        "last_kind": "",            # 最近一次互动性质：被夸 / 被骂 / ""
        "unresolved": [],           # [{ts, text, kind, status}] 欠着的：答应过、没做
        "facts": [],                # [{ts, text, kind}] 她说自己做过的事（对账用）
        "updated_at": 0.0,
    }


def _clone(state: Optional[Dict[str, Any]], scene: str, target: str) -> Dict[str, Any]:
    if isinstance(state, dict) and state:
        return copy.deepcopy(state)
    return new_state(scene, target)


def _pick(now: float, options: List[str]) -> str:
    """同一状态换着说法，避免简报本身变成新八股（每 10 分钟换一次）。"""
    if not options:
        return ""
    return options[int(now // 600) % len(options)]


def _update_tone(s: Dict[str, Any], text: str, ts: float) -> None:
    """按字数/标点/夸骂词更新语气统计。**只统计可观测特征**，不推断情绪。"""
    t = str(text or "")
    if not t.strip():
        return
    neg = min(2, sum(1 for w in _TONE_NEG if w in t))
    pos = min(2, sum(1 for w in _TONE_POS if w in t))
    delta = 0.6 * pos - 0.8 * neg
    if len(t) <= 2 and not re.search(r"[，。！？~…]", t):
        delta -= 0.1          # 极短回复：敷衍感
    if len(t) >= 30 and neg == 0:
        delta += 0.05         # 愿意多说：投入感
    old = float(s.get("tone") or 0.0)
    s["tone"] = max(-1.0, min(1.0, 0.7 * old + 0.3 * delta))
    s["tone_n"] = int(s.get("tone_n") or 0) + 1
    if neg:
        s["last_kind"] = "被骂"
    elif pos:
        s["last_kind"] = "被夸"


def _update_commitments(s: Dict[str, Any], bot_text: str, ts: float) -> None:
    """从**她自己的话**里记两件事：欠着的（承诺）、说做过的（对账）。

    只认明确的说法，不猜。承诺用 `_PROMISE_RE`；"已经做完"用 `_CLAIM_DONE_RE`。
    """
    t = str(bot_text or "")
    if not t:
        return
    if _PROMISE_RE.search(t) or _COMMIT_RE.search(t):
        rows = [r for r in (s.get("unresolved") or [])
                if ts - float(r.get("ts") or 0) < 86400]
        rows.append({"ts": ts, "text": t[:60], "kind": "promise", "status": "open"})
        s["unresolved"] = rows[-6:]
    if _CLAIM_DONE_RE.search(t):
        rows = [r for r in (s.get("facts") or [])
                if ts - float(r.get("ts") or 0) < 86400]
        rows.append({"ts": ts, "text": t[:60], "kind": "claim"})
        s["facts"] = rows[-8:]
        # 说了"发你了/弄好了"，就把最早一条欠着的标成已了结
        for r in (s.get("unresolved") or []):
            if r.get("status") == "open":
                r["status"] = "done"
                r["done_ts"] = ts
                break


def render_state_brief(state: Dict[str, Any], now: Optional[float] = None,
                       *, max_chars: int = 120) -> str:
    """把场景状态压成**人话**，最多三行、120 字，不出现字段名。

    给生成端当"她现在"用，不是给程序读的。没有内容就返回空串（整块不出现），
    绝不输出「无」「暂无」这类占位——那本身就是新的机器味。
    """
    s = state or {}
    if not s.get("target"):
        return ""
    now = float(now if now is not None else time.time())
    lines: List[str] = []

    kind = str(s.get("last_kind") or "")
    tone = float(s.get("tone") or 0.0)
    lvl = s.get("relation_level")
    if kind == "被骂":
        lines.append(_pick(now, ["刚被怼了一句，还没顺过来",
                                 "上一条挺冲，别当她没脾气",
                                 "刚被人呛了一下"]))
    elif kind == "被夸":
        lines.append(_pick(now, ["刚被夸了一句，心情还挂着",
                                 "刚被人捧了一下"]))
    elif tone <= -0.4:
        lines.append(_pick(now, ["这几句都挺冲", "气氛有点僵"]))
    elif lvl is not None:
        try:
            lv = int(lvl)
        except (TypeError, ValueError):
            lv = 0
        if lv >= 80:
            lines.append("跟这个人很熟")
        elif lv <= 20:
            lines.append("跟这个人不熟")

    # 「话头没接上」：她说过话、对方一直没回。这是**真实每天都有**的状态，
    # 比"答应过的事"可靠得多（后者在 151 条真实语料里 0 命中）。
    # 只在她说完超过 1 分钟后才提，否则会把正常对话节奏说成"没人理我"。
    pending_since = float(s.get("pending_since") or 0.0)
    last_bot = str(s.get("last_bot_text") or "")
    if s.get("pending") and last_bot and pending_since and now - pending_since >= 60:
        mins = int((now - pending_since) / 60)
        tail = last_bot[:12]
        lines.append(_pick(now, ["上一条「%s」还没人接", "刚说过「%s」，没人应"]) % tail
                     + ("，%d 分钟了" % mins if mins >= 5 else ""))

    open_rows = [r for r in (s.get("unresolved") or []) if r.get("status") == "open"]
    if open_rows:
        r = open_rows[0]
        mins = int(max(0.0, now - float(r.get("ts") or now)) / 60)
        tail = "，到现在没做" if mins >= 10 else "，还没做"
        lines.append("答应过「%s」%s" % (str(r.get("text") or "")[:16], tail))

    # 她是"隔了很久才回来"吗 —— 用观测时记下的间隔，不用渲染时刻现算
    # （渲染时 last_activity 已经被这条消息刷新了，现算永远是 0）。
    try:
        gap_h = float(s.get("resume_gap_h") or 0.0)
    except (TypeError, ValueError):
        gap_h = 0.0
    if gap_h >= 6:
        lines.append("隔了 %s 小时才又说话" % gap_h)

    text = "\n".join([x for x in lines if x][:3])
    return text[:max_chars]


def observe_bot_reply(state: Optional[Dict[str, Any]], event: Dict[str, Any],
                      now: float) -> Dict[str, Any]:
    """`bot.replied`：一条**真发出去**的消息。"""
    e = event or {}
    gid = str(e.get("group_id") or "")
    scene = str(e.get("scene") or ("group" if gid else "private"))
    target = gid if scene == "group" else str(e.get("user_id") or "")
    if not target:
        # 没有收件人就没法观测，宁可丢掉也不要记到一个错误的会话上
        return _clone(state, scene, target)
    s = _clone(state, scene, target)
    ts = float(e.get("ts") or now)
    trace = str(e.get("trace_id") or "")
    text = str(e.get("text") or "")

    same_turn = (trace and trace == s.get("last_bot_trace")
                 and ts - float(s.get("last_bot_ts") or 0) <= BUBBLE_WINDOW_S)
    s["last_bot_ts"] = ts
    s["last_bot_text"] = text[:200]
    s["last_bot_trace"] = trace
    s["last_activity_ts"] = max(ts, float(s.get("last_activity_ts") or 0))
    s["updated_at"] = now
    if not same_turn:
        s["pending"] = True
        s["pending_since"] = ts
        s["pending_trace"] = trace or "t%d" % int(ts)
    # 说过话就清掉「被 @ 没回」的债（不管回的是不是那句，观测层只认事实）
    if s.get("last_at_ts") and ts >= s["last_at_ts"]:
        s["last_at_ts"] = 0.0
        s["last_at_text"] = ""
    # 自己刚发出的这句话里如果出现"等下给你"，登记为未兑现话头
    if _PROMISE_RE.search(text):
        rows = [r for r in (s.get("promises") or [])
                if now - float(r.get("ts") or 0) < 86400]
        rows.append({"ts": ts, "text": text[:120]})
        s["promises"] = rows[-8:]
    # 2026-11-24：把承诺/声称做完的事记成可对账的条目（C2 世界事实）
    _update_commitments(s, text, ts)
    return s


def observe_user_message(state: Optional[Dict[str, Any]], event: Dict[str, Any],
                         now: float) -> Dict[str, Any]:
    """`message.in`：对方说了话 → 等待结束、话头兑现。"""
    e = event or {}
    gid = str(e.get("group_id") or "")
    scene = "group" if gid else "private"
    target = gid if gid else str(e.get("sender") or "")
    if not target:
        return _clone(state, scene, target)
    s = _clone(state, scene, target)
    ts = float(e.get("ts") or now)
    text = str(e.get("text") or "")
    sender = str(e.get("sender") or "")
    prev_activity = float(s.get("last_activity_ts") or 0)

    # ⑧ 长时间静默后回来：记下断了多少小时（阈值由 scan 判断）
    s["resume_gap_h"] = (round((ts - prev_activity) / 3600.0, 2)
                         if prev_activity and ts - prev_activity >= 3600 else 0.0)

    # ⑤/⑩ 连发：同一个人、5 分钟内接着说是"同一串"；换人/隔太久重新起算
    same_person = (s.get("last_user_sender") == sender and sender != "")
    quick = (ts - float(s.get("streak_last_ts") or 0)) <= 300
    if same_person and quick:
        s["user_streak"] = int(s.get("user_streak") or 0) + 1
    else:
        s["user_streak"] = 1
        s["streak_start"] = ts
        if scene == "group":
            # 换人说话：上一位（last_user_ts）就是"别人"，用来判断某人是不是被晾着
            s["last_other_ts"] = float(s.get("last_user_ts") or 0)
    s["streak_last_ts"] = ts
    s["streak_text"] = text[:200]

    s["last_user_ts"] = ts
    s["last_user_text"] = text[:200]
    s["last_user_sender"] = sender
    s["last_activity_ts"] = max(ts, prev_activity)
    s["pending"] = False
    s["pending_since"] = 0.0
    s["pending_trace"] = ""
    # 对方接话了，之前那句"等下给你"算兑现（不区分是不是同一件事，保守清理）
    s["promises"] = []
    # 2026-11-24：语气统计与关系快照（都来自可观测输入）
    _update_tone(s, text, ts)
    try:
        if e.get("affection") is not None:
            s["relation_level"] = int(e.get("affection"))
    except (TypeError, ValueError):
        pass
    s["updated_at"] = now

    # ⑥ 只发了图/文件、一个字没配
    if bool(e.get("has_image")) and not text.strip():
        s["image_only_ts"] = ts
        s["image_only_n"] = int(e.get("image_count") or 1)

    # ⑦ 提到名字但没真的 @（主链把 @ / 叫小a / 文本@ 拆开给了）
    if bool(e.get("named_bot")) and not bool(e.get("at_only")):
        s["named_ts"] = ts
        s["named_text"] = text[:120]

    # ★ 2026-11-20：只有"真的在叫我"才记 —— at_only（QQ @ 段）或 named_bot
    #   （文本里点了名字）；私聊没有 @ 别人的概念，一律算。
    #   原来用的是合并后的 at_bot，它含 text_at（文本里出现任何 @，包括 @ 别人）。
    #   于是群友 @ 别人时也被记成"被 @ 了"，主链按规矩跳过，at_dropped 再把它
    #   判成漏回复，补一句「在，刚没顾上」——群里没人叫它，它却答「我在」。
    # ★ 2026-11-24：私聊也算「被叫」。补上 `message.in` 之后，这条路径第一次
    #   在私聊真正生效。不用担心变成"没话找话"：主动选择不回的路径都会写
    #   `last_skip`（AI 决定不回复 / 冷却跳过都在 11-24 补齐），scan 见到
    #   `last_skip >= last_at` 就不会再报 at_dropped。剩下的命中都是"真漏了"。
    if (not e.get("group_id")) or bool(e.get("at_only")) or bool(e.get("named_bot")):
        s["last_at_ts"] = ts
        s["last_at_text"] = text[:200]

    # ⑪ 群活跃度：最近一小时的消息时间戳（滑动裁剪）
    if scene == "group":
        evs = [x for x in (s.get("events_recent") or [])
               if ts - float(x or 0) < 6 * 3600]
        evs.append(ts)
        s["events_recent"] = evs[-300:]
    return s


def observe_skip(state: Optional[Dict[str, Any]], event: Dict[str, Any],
                 now: float) -> Dict[str, Any]:
    """主链明确决定“这条不接”，不能被主动性误判成漏回复。"""
    e = event or {}
    gid = str(e.get("group_id") or "")
    scene = "group" if gid else "private"
    target = gid if gid else str(e.get("sender") or e.get("user_id") or "")
    s = _clone(state, scene, target)
    if not target:
        return s
    ts = float(e.get("ts") or now)
    s["last_skip_ts"] = ts
    s["last_skip_reason"] = str(e.get("reason") or "")[:120]
    # 明确不接 = 这条 @ 已经处理过，不再进入「被叫到却没回」。
    if float(s.get("last_at_ts") or 0) <= ts:
        s["last_at_ts"] = 0.0
        s["last_at_text"] = ""
    s["updated_at"] = now
    return s


def observe_notice(state: Optional[Dict[str, Any]], event: Dict[str, Any],
                   now: float) -> Dict[str, Any]:
    """`notice.recall` 等：只留痕，判断交给仲裁层。"""
    e = event or {}
    gid = str(e.get("group_id") or "")
    scene = "group" if gid else "private"
    target = gid if gid else str(e.get("user_id") or e.get("operator") or "")
    s = _clone(state, scene, target)
    rows = [r for r in (s.get("notices") or [])
            if now - float(r.get("ts") or 0) < 3600]
    rows.append({"ts": float(e.get("ts") or now),
                 "kind": str(e.get("notice_type") or e.get("event") or "notice"),
                 "text": str(e.get("text") or "")[:120],
                 "message_id": str(e.get("message_id") or "")})
    s["notices"] = rows[-6:]
    s["updated_at"] = now
    return s


def observe_delivery(state: Optional[Dict[str, Any]], event: Dict[str, Any],
                     now: float, *, ok: bool) -> Dict[str, Any]:
    """记录底层投递结果；只在消息**真的发出去**后清除失败标记。"""
    e = event or {}
    scene = str(e.get("scene") or "group")
    target = str(e.get("target") or "")
    s = _clone(state, scene, target)
    if not target:
        return s
    ts = float(e.get("ts") or now)
    if ok:
        s["delivery_ok_ts"] = ts
        s["delivery_failed_ts"] = 0.0
        s["delivery_failed_text"] = ""
    else:
        s["delivery_failed_ts"] = ts
        s["delivery_failed_text"] = str(e.get("text") or "")[:200]
    s["updated_at"] = now
    return s


def scan(state: Dict[str, Any], now: float, *, no_reply_s: float,
         at_dropped_s: float, promise_s: float, idle_s: float = 0.0,
         burst_n: int = 3, burst_s: float = 120.0, image_s: float = 120.0,
         named_s: float = 120.0, resume_s: float = 180.0,
         resume_gap_h: float = 6.0, delivery_hold_s: float = 300.0
         ) -> List[Dict[str, Any]]:
    """从状态里产出**候选**观察（不是决定）。纯函数，按时间只读。"""
    s = state or {}
    if not s.get("target"):
        return []
    out: List[Dict[str, Any]] = []
    scene, target = str(s.get("scene") or "private"), str(s.get("target"))
    k = key_of(scene, target)
    last_user = float(s.get("last_user_ts") or 0)
    last_bot = float(s.get("last_bot_ts") or 0)
    last_skip = float(s.get("last_skip_ts") or 0)
    delivery_failed = float(s.get("delivery_failed_ts") or 0)
    if delivery_failed and now - delivery_failed < max(0.0, delivery_hold_s):
        # 原文仍在 outbox 重试。此时模型生成新一句只会制造重复或“我刚没看见”。
        return []

    # ① 发出后无人回应
    if (s.get("pending") and last_bot > 0 and last_user < last_bot
            and now - last_bot >= no_reply_s):
        out.append({
            "kind": "no_reply", "scene": scene, "target": target,
            "since": float(s.get("pending_since") or last_bot),
            "elapsed_s": round(now - last_bot, 1),
            "dedupe_key": "%s:no_reply:%s" % (k, s.get("pending_trace") or int(last_bot)),
            "evidence": {"bot_text": s.get("last_bot_text", ""),
                         "last_user_s": round(now - last_user, 1) if last_user else None},
            "priority": 30,
        })

    # ② 被 @ 却没等到 bot 的回复
    at_ts = float(s.get("last_at_ts") or 0)
    if (at_ts > 0 and last_bot < at_ts and last_skip < at_ts
            and now - at_ts >= at_dropped_s):
        out.append({
            "kind": "at_dropped", "scene": scene, "target": target,
            "since": at_ts, "elapsed_s": round(now - at_ts, 1),
            "dedupe_key": "%s:at_dropped:%d" % (k, int(at_ts)),
            "evidence": {"at_text": s.get("last_at_text", "")},
            "priority": 60,
        })

    # ③ 自己许下的话头没兑现
    for i, p in enumerate(s.get("promises") or []):
        p_ts = float(p.get("ts") or 0)
        if p_ts > 0 and p_ts > last_user and now - p_ts >= promise_s:
            out.append({
                "kind": "pending_topic", "scene": scene, "target": target,
                "since": p_ts, "elapsed_s": round(now - p_ts, 1),
                "dedupe_key": "%s:promise:%d:%d" % (k, int(p_ts), i),
                "evidence": {"promised_text": p.get("text", "")},
                "priority": 50,
            })

    # ⑤/⑩ 一个人连发、bot 一直沉默。
    #      私聊 = 对方刷了好几条；群聊 = 某人连着说、期间没人接（被晾着）。
    streak = int(s.get("user_streak") or 0)
    st_start = float(s.get("streak_start") or 0)
    st_last = float(s.get("streak_last_ts") or 0)
    need = burst_n if scene == "private" else max(2, burst_n - 1)
    if (streak >= need and st_start > 0 and last_bot < st_start
            and last_skip < st_last
            and now - st_last >= burst_s):
        quiet = (scene == "private") or (float(s.get("last_other_ts") or 0) < st_start)
        if quiet:
            out.append({
                "kind": "user_burst", "scene": scene, "target": target,
                "since": st_start, "elapsed_s": round(now - st_start, 1),
                "dedupe_key": "%s:burst:%d" % (k, int(st_start)),
                "evidence": {"count": streak, "last_text": s.get("streak_text", ""),
                             "nobody_else_joined": quiet and scene == "group"},
                "priority": 40,
            })

    # ⑥ 只发了图/文件，一个字没配
    img_ts = float(s.get("image_only_ts") or 0)
    if img_ts > last_bot and img_ts > last_skip and now - img_ts >= image_s:
        out.append({
            "kind": "image_only", "scene": scene, "target": target,
            "since": img_ts, "elapsed_s": round(now - img_ts, 1),
            "dedupe_key": "%s:image:%d" % (k, int(img_ts)),
            "evidence": {"images": int(s.get("image_only_n") or 1),
                         "has_text": False},
            "priority": 45,
        })

    # ⑦ 提到了名字，但没真的 @（被当成耳旁风）
    nm_ts = float(s.get("named_ts") or 0)
    if nm_ts > last_bot and nm_ts > last_skip and now - nm_ts >= named_s:
        out.append({
            "kind": "name_dropped", "scene": scene, "target": target,
            "since": nm_ts, "elapsed_s": round(now - nm_ts, 1),
            "dedupe_key": "%s:named:%d" % (k, int(nm_ts)),
            "evidence": {"text": s.get("named_text", ""), "at_bot": False},
            "priority": 55,
        })

    # ⑧ 隔了很久没说话，回来第一句：接上次的话头
    gap = float(s.get("resume_gap_h") or 0)
    if (gap >= resume_gap_h and last_user > last_bot and last_user > last_skip
            and now - last_user >= resume_s):
        out.append({
            "kind": "resume_topic", "scene": scene, "target": target,
            "since": last_user, "elapsed_s": round(now - last_user, 1),
            "dedupe_key": "%s:resume:%d" % (k, int(last_user)),
            "evidence": {"gap_hours": gap, "text": s.get("last_user_text", ""),
                         "last_bot_text": s.get("last_bot_text", ""),
                         "last_topic": (s.get("last_user_text")
                                        or s.get("last_bot_text") or "")},
            "priority": 35,
        })

    # ⑪ 双方都静默很久（吸收 proactive_talk 与 proactive_poke）。
    #    和 ① 互斥：pending=True 说明 bot 刚发过话在等回复，那是 ① 的活。
    #    群聊按最近一小时活跃度缩放：热闹群拉长阈值，冷群缩短。
    last_act = float(s.get("last_activity_ts") or 0) or max(last_user, last_bot)
    idle_eff = idle_s
    activity = 0
    if scene == "group":
        # 看**冷场开始前**那一小时有多热闹：冷场时"最近一小时"必然是 0 条，
        # 用它判断只会把所有群都当冷群（2026-11-07 用例抓到）。
        _evs = s.get("events_recent") or s.get("events_1h") or []
        activity = len([x for x in _evs
                        if last_act - 3600 <= float(x or 0) <= last_act])
        if activity >= 20:
            idle_eff = idle_s * 1.6
        elif activity <= 3:
            # 注意 activity=0 是**最冷**的情况（这一小时一条都没有），也要缩短；
            # 写成 `0 < activity <= 3` 会把最该开口的群漏掉（2026-11-07 用例抓到）。
            idle_eff = idle_s * 0.6
    if (idle_s > 0 and not s.get("pending") and last_act > 0
            and now - last_act >= idle_eff and now - last_bot >= idle_eff):
        bucket = int(last_act // 3600)
        out.append({
            "kind": "idle_contact", "scene": scene, "target": target,
            "since": last_act, "elapsed_s": round(now - last_act, 1),
            "dedupe_key": "%s:idle:%d" % (k, bucket),
            "evidence": {"last_activity_text":
                         (s.get("last_user_text") if last_user >= last_bot
                          else s.get("last_bot_text")) or "",
                         "last_was": "user" if last_user >= last_bot else "bot",
                         "activity_1h": activity},
            "priority": 20,
        })
    return out


_ANALYZE_HINTS = ("解析", "分析", "analyze", "describe_video", "抽帧", "frames",
                  "asr", "ocr", "识别", "字幕", "转写")


def _job_mode(event: Dict[str, Any]) -> str:
    """任务类型：analyze（解析/理解）/ generate（生成产物）/ job（其它）。"""
    e = event or {}
    blob = (str(e.get("label") or "") + " " + str(e.get("tail") or "")).lower()
    if any(h in blob for h in _ANALYZE_HINTS):
        return "analyze"
    if str(e.get("artifact") or ""):
        return "generate"
    return "job"


def job_observation(event: Dict[str, Any], target: str, now: float,
                    scene: str = "private") -> Optional[Dict[str, Any]]:
    """⑨ 后台任务结束：**即时**候选（不走超时扫描）。

    target 由插件选（任务本身不属于某个会话）：挑最近还活跃的那个会话汇报。
    evidence 带 tail，模型据此说"视频好了，几帧、多长"，而不是空泛地"完成了"。
    """
    e = event or {}
    jid = str(e.get("job_id") or "")
    if not jid or not target:
        return None
    return {
        "kind": "autonomy_done", "scene": scene, "target": target,
        "since": float(e.get("ts") or now), "elapsed_s": 0.0,
        # 产物路径放**顶层**：发送口要用它决定发视频/图片，不只是给模型看
        "artifact": str(e.get("artifact") or ""),
        "dedupe_key": "%s:job:%s" % (key_of(scene, target), jid),
        "evidence": {"job": str(e.get("label") or "")[:60],
                     "ok": bool(e.get("ok")),
                     # ★ 视频链路是**解析**（理解内容），不是生成。让模型知道
                     #   该说"解析出來是…"而不是"做好了，发你"。
                     "mode": _job_mode(e),
                     "tail": str(e.get("tail") or "")[-300:]},
        "priority": 65,
    }


def notice_observation(event: Dict[str, Any], event_name: str,
                       now: float) -> Optional[Dict[str, Any]]:
    """⑫ 状态变更（改群名 / 新人进群 / 有人退群 / 加好友）：即时候选。"""
    e = event or {}
    gid = str(e.get("group_id") or "")
    scene = "group" if gid else "private"
    target = gid or str(e.get("user_id") or e.get("operator") or "")
    if not target:
        return None
    ts = float(e.get("ts") or now)
    sub = str(event_name or "").split(".", 1)[-1] or "state_change"
    return {
        "kind": "state_change", "scene": scene, "target": target,
        "since": ts, "elapsed_s": 0.0,
        "dedupe_key": "%s:state:%s:%d" % (key_of(scene, target), sub, int(ts)),
        "evidence": {"event": sub,
                     "name": str(e.get("name") or "")[:60],
                     "user_id": str(e.get("user_id") or ""),
                     "operator": str(e.get("operator") or ""),
                     "kick": bool(e.get("kick"))},
        "priority": 25,
    }


def schedule_observation(sched: Dict[str, Any], target: str, now: float,
                         scene: str = "private") -> Optional[Dict[str, Any]]:
    """⑬ 日程临近：下一条安排还剩几分钟（schedule 插件的 now_state）。"""
    st = sched or {}
    try:
        rem = int(st.get("remaining_min") or 0)
    except (TypeError, ValueError):
        rem = 0
    title = str(st.get("next_title") or "").strip()
    start = str(st.get("next_start") or "").strip()
    if not title or rem <= 0 or not target:
        return None
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    return {
        "kind": "schedule_near", "scene": scene, "target": target,
        "since": now, "elapsed_s": 0.0,
        "dedupe_key": "%s:sched:%s:%s:%s" % (key_of(scene, target), title, start, day),
        "evidence": {"title": title, "start": start, "in_minutes": rem,
                     "place": str(st.get("place") or "")},
        "priority": 58,
    }


#: ⑭ 关心的触发阈值与"群里不提"的脆弱项。
_CARE_KEYS = (("loneliness", 0.60), ("burnout", 0.60), ("insomnia", 0.60),
              ("cold", 0.60), ("fever", 0.60), ("wounded", 0.50))
_CARE_PRIVATE_ONLY = ("loneliness", "wounded", "insomnia")


def care_observation(vitals: Dict[str, Any], target: str, now: float,
                     scene: str = "private") -> Optional[Dict[str, Any]]:
    """⑭ 主动关心：读 vitals 的状态（`get_vitals` 那份），挑最重的一项。

    群聊里只说「累 / 感冒」这类**不暴露脆弱**的；孤独、受伤、失眠只私聊说 ——
    在群里公开"我有点孤独"不是关心，是表演。
    """
    if not target:
        return None
    v = (vitals or {}).get("vitals") if isinstance(vitals, dict) else {}
    v = v or (vitals if isinstance(vitals, dict) else {})
    picks = []
    for k, thr in _CARE_KEYS:
        try:
            val = float(v.get(k) or 0.0)
        except (TypeError, ValueError):
            continue
        if val >= thr:
            picks.append((k, val))
    if not picks:
        return None
    picks.sort(key=lambda x: -x[1])
    key, val = picks[0]
    if str(scene or "") == "group" and key in _CARE_PRIVATE_ONLY:
        return None
    bucket = int(now // (4 * 3600))          # 同一状态 4 小时内只说一次
    return {
        "kind": "care", "scene": scene, "target": target,
        "since": now, "elapsed_s": 0.0,
        "dedupe_key": "%s:care:%s:%d" % (key_of(scene, target), key, bucket),
        "evidence": {"feel": key, "strength": round(val, 2)},
        "priority": 45,
    }


def recall_observation(event: Dict[str, Any], now: float) -> Optional[Dict[str, Any]]:
    """④ 撤回是**即时事件**，不走超时扫描。"""
    e = event or {}
    gid = str(e.get("group_id") or "")
    scene = "group" if gid else "private"
    target = gid if gid else str(e.get("user_id") or e.get("operator") or "")
    if not target:
        return None
    mid = str(e.get("message_id") or "")
    return {
        "kind": "recall", "scene": scene, "target": target,
        "since": float(e.get("ts") or now), "elapsed_s": 0.0,
        "dedupe_key": "%s:recall:%s" % (key_of(scene, target), mid or int(now)),
        "evidence": {"notice_type": e.get("notice_type") or "",
                     "message_id": mid, "operator": e.get("operator") or ""},
        "priority": 70,
    }


class Ledger:
    """带持久化的会话状态表。重启后"我在等回复"还在 —— 不然重启就漏一次观测。"""

    def __init__(self, path: str = ""):
        self.path = path
        self._lock = threading.RLock()
        self._states: Dict[str, Dict[str, Any]] = {}
        self._fired: Dict[str, float] = {}     # dedupe_key -> 上次触发时间
        if path and os.path.isfile(path):
            try:
                d = json.loads(open(path, encoding="utf-8").read())
                if isinstance(d, dict):
                    self._states = {k: v for k, v in (d.get("states") or {}).items()
                                    if isinstance(v, dict)}
                    self._fired = dict(d.get("fired") or {})
            except Exception:
                pass

    def get(self, scene: str, target: str) -> Dict[str, Any]:
        with self._lock:
            return dict(self._states.get(key_of(scene, target)) or new_state(scene, target))

    def put(self, state: Dict[str, Any]) -> None:
        if not isinstance(state, dict) or not state.get("target"):
            return
        with self._lock:
            self._states[str(state["key"])] = dict(state)

    def update(self, scene: str, target: str, fn, now: float) -> Dict[str, Any]:
        with self._lock:
            s = fn(dict(self._states.get(key_of(scene, target)) or new_state(scene, target)), now)
            self._states[str(s["key"])] = dict(s)
            return dict(s)

    def states(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self._states.values()]

    def already_fired(self, dedupe_key: str, ttl_s: float = 3600.0,
                      now: Optional[float] = None) -> bool:
        t = float(now if now is not None else time.time())
        with self._lock:
            for k in [k for k, v in self._fired.items() if t - float(v) > ttl_s]:
                self._fired.pop(k, None)
            return dedupe_key in self._fired

    def mark_fired(self, dedupe_key: str, now: Optional[float] = None) -> None:
        with self._lock:
            self._fired[dedupe_key] = float(now if now is not None else time.time())

    def save(self) -> bool:
        if not self.path:
            return False
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with self._lock:
                data = {"states": self._states, "fired": self._fired}
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, default=str)
            os.replace(tmp, self.path)
            return True
        except Exception:
            return False
