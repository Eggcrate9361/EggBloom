# -*- coding: utf-8 -*-
"""harness/source_kind.py —— 来源形状 SourceKind：`scope` / `channel` / `threadContext`（2026-11-03）

执行总表 §7.2 第 4 条 + §8.1 #2：**先定形状，实现仍是 QQ / PC / 浏览器**。

## 要解决的是哪件事

现在主链认识的是**驱动名**，而且"这个驱动支不支持某个动作"是靠 try/except 试出来的。
最典型的一处是 `bot_core._send_poke_back()`：里面写死了一张

    SnowLuma: send_poke（群/私自动路由）/ friend_poke（好友拍一拍）
    NapCat:   send_poke（群）/ friend_nudge（私聊）
    LLOneBot: send_poke（群）

的候选表，逐个试到 retcode=0 为止。这张表本身没错（是实测踩出来的），
错在它**长在主链里**：再加一个协议就得回去改主链。

这里把它变成数据：主链以后只问 `channel.can("poke")`，
不支持就按 `DEGRADE` 表降级（不发、记日志），**核心一行不用改**。

## 三个字段

```
scope          这个源服务哪些场景：group / private / desktop
channel        allowed(能力集) + can(cap) + send(cap, target, payload) + link(事件链路)
threadContext  这一轮在跟谁说话：scene / thread_id / chat_key / group_id / user_id / reply_to
```

`chat_key` 用的是 `"g:<目标>"` / `"p:<目标>"`，**与 `harness/outbound.py` 同一把钥匙**
（它按这把钥匙分会话 FIFO 与去重），别在这里另起一套命名。

## 纪律

- 纯函数 + 鸭子类型：只要求适配器有 `api_call(endpoint, data)` 和 `name`，
  **不 import `protocols.base`**（那会把 config 拖起来，离线测不了）；
- 现有驱动**不改一行就是全能力**（`DEFAULT_CAPABILITIES` 兼容层，与 `PLUGIN_META` 同一思路）；
- 新协议只要声明 `CAPABILITIES = {...}` 就够，不用动主链；
- 能力缺失**不静默吞**：返回 `degraded=True` 并带原因，调用方想记日志就记。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

# ── 场景 ──────────────────────────────────────────────────
SCENE_GROUP = "group"
SCENE_PRIVATE = "private"
SCENE_DESKTOP = "desktop"

SCENES = (SCENE_GROUP, SCENE_PRIVATE, SCENE_DESKTOP)

# ── 能力（主链只认这些名字，不认驱动名）────────────────────
CAP_TEXT = "text"          # 发文字
CAP_IMAGE = "image"        # 发图片 / 表情包
CAP_RECORD = "record"      # 发语音
CAP_FILE = "file"          # 发文件
CAP_POKE = "poke"          # 戳一戳
CAP_AT = "at"              # 群里 @ 人
CAP_RECALL = "recall"      # 撤回
CAP_HISTORY = "history"    # 拉历史消息
CAP_MEMBERS = "member_list"  # 拉群成员

CAPABILITIES = (CAP_TEXT, CAP_IMAGE, CAP_RECORD, CAP_FILE, CAP_POKE,
                CAP_AT, CAP_RECALL, CAP_HISTORY, CAP_MEMBERS)

#: 现有四个驱动不改一行就当全能力（兼容层）。
DEFAULT_CAPABILITIES = frozenset(CAPABILITIES)

#: 一个源至少得能发文字，否则它在主链里没有意义。
MIN_CAPABILITIES = frozenset({CAP_TEXT})

#: 能力缺失时怎么办。**策略在这里定，不在调用方散落**。
#:   skip    不发，记日志（表情 / 戳 这类"有最好、没有也行"的）
#:   empty   返回空集（拉历史 / 拉成员）
#:   fallback 有等价的文字替代就发文字
#:   fail    致命（连文字都发不了）
DEGRADE: Dict[str, Dict[str, str]] = {
    CAP_TEXT: {"action": "fail", "note": "连文字都发不了，这个源不可用"},
    CAP_IMAGE: {"action": "skip", "note": "本协议不支持发图，跳过图片/表情包"},
    CAP_RECORD: {"action": "skip", "note": "本协议不支持发语音，跳过"},
    CAP_FILE: {"action": "skip", "note": "本协议不支持发文件，跳过"},
    CAP_POKE: {"action": "skip", "note": "本协议不支持戳一戳，跳过"},
    CAP_RECALL: {"action": "skip", "note": "本协议不支持撤回，跳过"},
    CAP_HISTORY: {"action": "empty", "note": "本协议不支持拉历史，按空处理"},
    CAP_MEMBERS: {"action": "empty", "note": "本协议不支持拉成员，按空处理"},
    CAP_AT: {"action": "fallback", "note": "本协议不支持 @，退化成文字点名"},
}

#: 能力 → 不同场景下的候选动作（按顺序试，第一个 retcode=0 的算成）。
#: 私聊戳一戳那段抄的是 `bot_core._send_poke_back` 的实测结论，
#: 差别只在于：那张表现在住在数据里，不再住在主链代码里。
ACTIONS: Dict[str, Dict[str, List[str]]] = {
    CAP_TEXT: {SCENE_GROUP: ["send_group_msg"], SCENE_PRIVATE: ["send_private_msg"]},
    CAP_IMAGE: {SCENE_GROUP: ["send_group_msg"], SCENE_PRIVATE: ["send_private_msg"]},
    CAP_RECORD: {SCENE_GROUP: ["send_group_msg"], SCENE_PRIVATE: ["send_private_msg"]},
    CAP_AT: {SCENE_GROUP: ["send_group_msg"], SCENE_PRIVATE: []},
    CAP_POKE: {
        SCENE_GROUP: ["send_poke"],
        SCENE_PRIVATE: ["send_poke", "friend_poke", "friend_nudge"],
    },
    CAP_RECALL: {SCENE_GROUP: ["delete_msg"], SCENE_PRIVATE: ["delete_msg"]},
    CAP_HISTORY: {
        SCENE_GROUP: ["get_group_msg_history"],
        SCENE_PRIVATE: ["get_friend_msg_history"],
    },
    CAP_MEMBERS: {SCENE_GROUP: ["get_group_member_list"], SCENE_PRIVATE: []},
    CAP_FILE: {
        SCENE_GROUP: ["upload_group_file"],
        SCENE_PRIVATE: ["upload_private_file"],
    },
}


# ── threadContext ─────────────────────────────────────────


def thread_context(scene: str, *, group_id: Any = "", user_id: Any = "",
                   message_id: Any = "", day: str = "") -> dict:
    """这一轮在跟谁说话。

    `chat_key` 与 `harness/outbound.py` 的会话钥匙**同一把**（`g:` / `p:` 前缀），
    否则队列会按两种口径分会话，同一条私聊排进两个 FIFO。
    """
    s = str(scene or "").strip().lower()
    if s not in (SCENE_GROUP, SCENE_PRIVATE):
        return {"scene": s or "?", "thread_id": "", "chat_key": "",
                "group_id": str(group_id or ""), "user_id": str(user_id or ""),
                "reply_to": str(message_id or ""), "day": str(day or ""),
                "ok": False, "error": f"场景只能是 group / private，收到 {scene!r}"}
    gid = str(group_id or "").strip()
    uid = str(user_id or "").strip()
    if s == SCENE_GROUP:
        if not gid:
            return {"scene": s, "thread_id": "", "chat_key": "", "group_id": "",
                    "user_id": uid, "reply_to": str(message_id or ""), "day": str(day or ""),
                    "ok": False, "error": "群场景缺 group_id"}
        target = gid
        prefix = "g:"
    else:
        if not uid:
            return {"scene": s, "thread_id": "", "chat_key": "", "group_id": "",
                    "user_id": "", "reply_to": str(message_id or ""), "day": str(day or ""),
                    "ok": False, "error": "私聊场景缺 user_id"}
        target = uid
        prefix = "p:"
    return {
        "scene": s,
        "thread_id": f"{s}:{target}",
        "chat_key": prefix + target,
        "group_id": gid,
        "user_id": uid,
        "reply_to": str(message_id or ""),
        "day": str(day or ""),
        "ok": True,
        "error": "",
    }


# ── 能力解析 ──────────────────────────────────────────────


def declared_capabilities(adapter) -> frozenset:
    """读适配器的能力声明。

    优先级：`capabilities()` 方法 → `CAPABILITIES` 属性 → **全能力**（兼容层）。
    三种都认，是因为库外面已经有四个驱动在跑，不能要求它们同一天改完。
    声明里出现不认识的名字**不报错**（新能力可以先行声明），只是 `can()` 永远为假。
    """
    try:
        fn = getattr(adapter, "capabilities", None)
        if callable(fn):
            got = fn()
            if got is not None:
                return frozenset(str(x).strip() for x in got if str(x).strip())
    except Exception:
        pass
    for attr in ("CAPABILITIES", "capabilities_"):
        try:
            got = getattr(adapter, attr, None)
            if got is not None and not callable(got):
                return frozenset(str(x).strip() for x in got if str(x).strip())
        except Exception:
            continue
    return DEFAULT_CAPABILITIES


def declared_scopes(adapter, caps: Optional[Iterable[str]] = None) -> frozenset:
    """这个源服务哪些场景。默认群 + 私聊；能力不足时按能力算。"""
    for attr in ("SCOPES", "scopes_"):
        try:
            got = getattr(adapter, attr, None)
            if got is not None and not callable(got):
                return frozenset(str(x).strip() for x in got if str(x).strip())
        except Exception:
            continue
    c = frozenset(caps if caps is not None else declared_capabilities(adapter))
    # 只要能发文字，两个场景都算"能用"：@ 能力只影响"怎么点名"，
    # 不影响"能不能在这个场景说话" —— 别把两件事混成一个判据。
    out = []
    if CAP_TEXT in c:
        out.append(SCENE_PRIVATE)
        out.append(SCENE_GROUP)
    return frozenset(out)


# ── 发消息 ────────────────────────────────────────────────


def _segments_to_message(payload: dict, cap: str) -> str:
    """payload → OneBot `message` 字段。这里只拼最小形状，复杂段由调用方给 `message`。"""
    if isinstance(payload, dict) and payload.get("message") is not None:
        return payload["message"]
    if cap in (CAP_TEXT, CAP_AT):
        return str((payload or {}).get("text") or "")
    if cap == CAP_IMAGE:
        f = str((payload or {}).get("file") or "")
        return f"[CQ:image,file={f}]" if f else ""
    if cap == CAP_RECORD:
        f = str((payload or {}).get("file") or "")
        return f"[CQ:record,file={f}]" if f else ""
    return ""


def _params_for(cap: str, scene: str, target: str, payload: dict) -> dict:
    p = dict(payload or {})
    if cap == CAP_POKE:
        if scene == SCENE_GROUP:
            return {"group_id": p.get("group_id") or target, "user_id": p.get("user_id") or ""}
        return {"user_id": p.get("user_id") or target}
    if cap == CAP_RECALL:
        return {"message_id": p.get("message_id") or p.get("id") or ""}
    if cap == CAP_MEMBERS:
        return {"group_id": target}
    if cap == CAP_HISTORY:
        if scene == SCENE_GROUP:
            return {"group_id": target, "count": int(p.get("count") or 20)}
        return {"user_id": target, "count": int(p.get("count") or 20)}
    if cap == CAP_FILE:
        p.setdefault("file", p.get("path") or "")
        if scene == SCENE_GROUP:
            p.setdefault("group_id", target)
        else:
            p.setdefault("user_id", target)
        return p
    if cap == CAP_AT:
        # @ 人只有群里有意义：拼成 [CQ:at,qq=…] + 正文，别让它退化成空消息
        qq = str(p.get("at_qq") or p.get("at") or "")
        body = str(p.get("text") or "")
        p["message"] = ((f"[CQ:at,qq={qq}] " if qq else "") + body)
        p.setdefault("group_id", target)
        return p
    # 文本类：群给 group_id，私聊给 user_id
    p["message"] = _segments_to_message(p, cap)
    if scene == SCENE_GROUP:
        p.setdefault("group_id", target)
    else:
        p.setdefault("user_id", target)
    return p


def send(source: dict, cap: str, target: str, payload: Optional[dict] = None, *,
         scene: str = "", timeout: int = 15) -> dict:
    """按能力发一次。能力不够就**降级**，不抛异常、不让调用方 try/except。

    返回：`{ok, cap, via, degraded, skipped, reason, data}`
    """
    src = source if isinstance(source, dict) else {}
    ch = src.get("channel") or {}
    adapter = ch.get("_adapter")
    c = str(cap or "").strip()
    if c not in CAPABILITIES:
        return {"ok": False, "cap": c, "degraded": False, "skipped": False,
                "reason": f"不认识的能力：{c or '(空)'}", "via": ""}
    if adapter is None:
        return {"ok": False, "cap": c, "degraded": False, "skipped": False,
                "reason": "这个源没有绑定适配器", "via": ""}

    sn = str(scene or src.get("scene") or SCENE_PRIVATE).strip().lower()
    if not ch.get("can", lambda _c: False)(c):
        d = DEGRADE.get(c, {"action": "skip", "note": f"{c} 不支持"})
        act = d.get("action")
        base = {"cap": c, "degraded": True, "via": "", "reason": d.get("note", "")}
        if act == "empty":
            return {**base, "ok": True, "skipped": False, "data": []}
        if act == "fallback":
            text = str((payload or {}).get("text") or "")
            if text:
                res = send(source, CAP_TEXT, target, {"text": text}, scene=sn, timeout=timeout)
                return {**res, "cap": c, "fallback_to": CAP_TEXT,
                        "degraded": True, "reason": d.get("note", "")}
            return {**base, "ok": False, "skipped": True}
        if act == "fail":
            return {**base, "ok": False, "skipped": False}
        return {**base, "ok": False, "skipped": True}      # skip

    actions = (ACTIONS.get(c) or {}).get(sn) or []
    if not actions:
        return {"ok": False, "cap": c, "degraded": False, "skipped": True, "via": "",
                "reason": f"{c} 在 {sn} 场景没有可用动作"}

    params = _params_for(c, sn, str(target or ""), payload or {})
    last = ""
    for action in actions:
        try:
            resp = adapter.api_call(action, params, timeout=timeout)
        except TypeError:
            resp = adapter.api_call(action, params)
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            continue
        code = resp.get("retcode") if isinstance(resp, dict) else None
        if code == 0:
            return {"ok": True, "cap": c, "via": action, "degraded": False,
                    "skipped": False, "reason": "",
                    "data": (resp or {}).get("data") if isinstance(resp, dict) else None}
        last = f"{action} retcode={code}"
    return {"ok": False, "cap": c, "via": "", "degraded": False, "skipped": False,
            "reason": f"全部候选动作都失败：{last}"}


# ── 构造 SourceKind ───────────────────────────────────────


def build(adapter, *, driver_id: str = "", caps: Optional[Iterable[str]] = None) -> dict:
    """把适配器包成 SourceKind 描述对象。

    包里带 `_adapter` 是**故意**的：`send()` 需要它，但那是私有字段，
    调用方只该用 `channel.can` / `channel.send` / `threadContext` 三样。
    """
    if adapter is None:
        raise ValueError("adapter 不能为空")
    c = frozenset(caps) if caps is not None else declared_capabilities(adapter)
    scopes = declared_scopes(adapter, c)
    # 每个源一个私有中转：`channel.send` 要拿到"自己这个 dict"，
    # 用模块级变量会在建第二个源时把第一个串掉（并发下更糟）。
    box: Dict[str, Any] = {}

    def _can(cap: str) -> bool:
        return str(cap or "").strip() in c

    def _send(cap: str, target: str, payload: Optional[dict] = None, **kw) -> dict:
        return send(box.get("src") or {}, cap, target, payload, **kw)

    src: Dict[str, Any] = {
        "kind": "qq",
        "driver": str(driver_id or getattr(adapter, "name", "") or ""),
        "usable": bool(c >= MIN_CAPABILITIES),
        "scope": {
            SCENE_GROUP: SCENE_GROUP in scopes,
            SCENE_PRIVATE: SCENE_PRIVATE in scopes,
            SCENE_DESKTOP: False,
        },
        "channel": {
            "allowed": sorted(c),
            "can": _can,
            "send": _send,
            "link": {
                "events": "onebot-v11",
                "post_event": getattr(adapter, "post_event", None),
            },
            "_adapter": adapter,
        },
        "threadContext": thread_context,
    }
    box["src"] = src
    return src


def describe(source: dict) -> dict:
    """给日志/页面看的一句话（不含可调用对象，能直接 json 化）。"""
    src = source if isinstance(source, dict) else {}
    ch = src.get("channel") or {}
    return {
        "kind": src.get("kind", ""),
        "driver": src.get("driver", ""),
        "scope": {k: bool(v) for k, v in (src.get("scope") or {}).items()},
        "allowed": list(ch.get("allowed") or []),
    }


__all__ = [
    "SCENE_GROUP", "SCENE_PRIVATE", "SCENE_DESKTOP", "SCENES",
    "CAP_TEXT", "CAP_IMAGE", "CAP_RECORD", "CAP_FILE", "CAP_POKE", "CAP_AT",
    "CAP_RECALL", "CAP_HISTORY", "CAP_MEMBERS", "CAPABILITIES",
    "DEFAULT_CAPABILITIES", "MIN_CAPABILITIES", "DEGRADE", "ACTIONS",
    "thread_context", "declared_capabilities", "declared_scopes",
    "send", "build", "describe",
]
