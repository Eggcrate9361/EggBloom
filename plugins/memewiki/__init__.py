# -*- coding: utf-8 -*-
"""memewiki —— 接梗百科（cnmeme.wiki），让 AI 不靠瞎猜解释梗。

## 为什么要这个插件

模型遇到新词/缩写/黑话时，常见两种烂结果：硬编（编出个像样的出处），
或者客服腔科普（"这是一个网络流行语……"）。这个插件给的是**查得到的事实**：
出处、用法、标签、原视频，查不到就明说查不到。

## 触发（都走 on_message，不打扰正常聊天）

    !梗 yyds          查一条，直接回条目
    梗百科 赛博朋克     同上（不带感叹号的别名）
    !梗随机            随机来一条
    !梗 --raw yyds     回原始 JSON 片段（排查用）

## 能力层

抓取/清洗/格式化全在 `wiki.py`（纯函数 + TTL 缓存，可单测）。
本文件只做：命令解析、限流、异常兜底。

## 数据源

    https://cnmeme.wiki/api/v1/public/memes?q=...
    https://cnmeme.wiki/api/v1/public/memes/<slug>

memepedia.miraheze.org 走 Cloudflare，脚本请求 403，所以不接。
"""
from __future__ import annotations

import re
import threading
import time

from plugins.abi import PluginContext
from plugins.memewiki import store, wiki

PLUGIN_SPEC = 1
PLUGIN_MANIFEST = {
    "name": "memewiki",
    "version": "2.0.0",
    "api_version": 2,
    "author": "EggBloom",
    "description": "梗理解层：本地检索、人格/关系过滤、本轮注入和反馈学习；联网只作兜底",
    "dependencies": [],
    "priority": 60,
    "default_enabled": True,
}

#: 触发前缀（顺序即优先级）
_PREFIXES = ("!梗百科", "!梗", "梗百科")

#: 同一个人 60 秒内最多查几次（防刷爆上游）
_RATE_WINDOW = 60.0
_RATE_MAX = 5
_last: dict = {}
_STATE_LOCK = threading.RLock()
_STATE_TTL = 3600.0
_pending: dict = {}
_delivery: dict = {}
_awaiting: dict = {}


def on_load(ctx: PluginContext):
    try:
        count = store.default().reload()
    except Exception as e:
        count = 0
        ctx.log(f"memewiki 本地词库不可用: {e}", "warn")
    ctx.log(f"memewiki loaded — 本地梗库 {count} 条；!梗 <词> 仍可联网兜底")


def on_enable(ctx: PluginContext):
    ctx.log("memewiki enabled")


def on_disable(ctx: PluginContext):
    with _STATE_LOCK:
        _pending.clear()
        _delivery.clear()
        _awaiting.clear()
    ctx.log("memewiki disabled")


def on_unload(ctx: PluginContext):
    with _STATE_LOCK:
        _pending.clear()
        _delivery.clear()
        _awaiting.clear()
    ctx.log("memewiki unloaded")


def _parse(text: str):
    """返回要查的词；不是本插件的命令就返回 None。"""
    t = (text or "").strip()
    for p in _PREFIXES:
        if t.startswith(p):
            arg = t[len(p):].strip(" :：\t")
            return arg
    return None


def _rate_ok(sender: str) -> bool:
    now = time.time()
    ts = [x for x in _last.get(sender, []) if now - x < _RATE_WINDOW]
    if len(ts) >= _RATE_MAX:
        _last[sender] = ts
        return False
    ts.append(now)
    _last[sender] = ts
    return True


def _scope_id(event: dict) -> str:
    gid = str(event.get("group_id") or "")
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    return gid or str(
        event.get("sender")
        or event.get("user_id")
        or context.get("sender")
        or context.get("user_id")
        or "")


def _prune_state(now: float = 0.0) -> None:
    now = float(now or time.time())
    with _STATE_LOCK:
        for state in (_pending, _delivery, _awaiting):
            stale = [key for key, value in state.items()
                     if now - float((value or {}).get("ts") or 0) > _STATE_TTL]
            for key in stale:
                state.pop(key, None)


def _is_question(text: str) -> bool:
    return bool(re.search(
        r"(?:是什么梗|什么梗|啥梗|什么意思|啥意思|怎么用|怎么来的|出自哪|哪来的|解释一下)",
        str(text or ""), re.I))


def _question_word(text: str) -> str:
    m = re.search(
        r"(?P<word>[A-Za-z0-9\u4e00-\u9fff]{1,18})\s*"
        r"(?:是什么梗|什么梗|啥梗|什么意思|啥意思|怎么用|怎么来的|出自哪|哪来的|解释一下)",
        str(text or ""), re.I)
    return str(m.group("word") if m else "").strip()


def _format_local(item: dict) -> str:
    """One-message dictionary-style answer for explicit commands/questions."""
    lines = [f"【{item.get('word')}】{item.get('meaning')}"]
    origin = str(item.get("origin") or "").strip()
    if origin:
        lines.append(f"出处：{origin}")
    tone = {
        "self_deprecate": "适合自嘲",
        "mock": "适合熟人之间损一句",
        "praise": "适合夸人",
        "vent": "偏情绪宣泄",
        "neutral": "普通接话",
    }.get(str(item.get("tone") or "neutral"), "普通接话")
    lines.append(f"用法：{tone}")
    if int(item.get("risk") or 0) >= 1:
        lines.append("提醒：带攻击性或圈层门槛，不熟别主动用")
    return "\n".join(lines)[:500]


def _risk_allowed(item: dict, *, relation: str, user_used: bool,
                  private: bool, question: bool = False) -> bool:
    risk = int(item.get("risk") or 0)
    if question:
        return True
    if risk <= 0:
        return True
    if not user_used:
        return False
    if risk == 1:
        # A direct private conversation is close enough for a word the sender
        # used first; group use still needs an established relation.
        return private or relation in ("好", "特别好")
    if risk == 2:
        return relation == "特别好"
    return False


def _packet(matches, *, question: bool, relation: str = "",
            dense: int = 50, private: bool = False,
            text: str = "", explanation: bool = False):
    if not matches:
        return "", []
    lines = ["【这个梗·本轮参考】"]
    selected = []
    used_n = 0
    for m in matches:
        item = m.entry
        user_used = bool(m.surface and store.normalize(m.surface) in store.normalize(text))
        if not _risk_allowed(item, relation=relation, user_used=user_used,
                             private=private, question=question):
            continue
        tags = f"{item.get('circle') or '通用'}；"
        if int(item.get("risk") or 0) >= 1:
            tags += "有攻击性，只有对方先用或关系很熟才跟；"
        if not question and int(getattr(m, "usage", 0) or 0) > 0:
            tags += f"本群用过{int(m.usage)}次；"
        if not question and int(getattr(m, "feedback", 0) or 0) >= 2:
            tags += "群友反应不错；"
        elif not question and int(getattr(m, "feedback", 0) or 0) <= -2:
            tags += "本群反馈一般，少用；"
        if question:
            guidance = "对方在问意思，可以用大白话直接答；不要编出处。"
        elif explanation:
            guidance = "对方在解释这个词，你懂了就行，不用重复科普。"
        else:
            guidance = "顺着接话即可，不要科普这个词；最多带一个梗。"
            if dense < 30:
                guidance += "本轮梗密度低，能不用就不用。"
        line = (f"- {item.get('word')}={item.get('meaning')}（{tags}）"
                f"\n  你（小a）可以怎么用：{guidance}")
        if len("\n".join(lines + [line])) > 300:
            break
        lines.append(line)
        selected.append(m)
        used_n += 1
        if used_n >= (2 if dense >= 60 else 1):
            break
    if used_n == 0:
        return "", []
    if question:
        lines.append("只回答问到的词。没把握时直接说不知道。")
    else:
        lines.append("不要把这行标题或内部说明复述给群友。")
    return "\n".join(lines)[:300], selected


def on_before_reply(ctx: PluginContext, event: dict):
    """Inject at most two local meme hints for the current generation only."""
    message = str((event or {}).get("message") or "")
    if not message.strip():
        return None
    scope = _scope_id(event)
    if scope:
        # A new generation supersedes any unconfirmed text from an older turn.
        with _STATE_LOCK:
            _delivery.pop(scope, None)
    cfg = ctx.get_config() or {}
    question = _is_question(message)
    explanation = bool(_LEARN_RE.search(message))
    try:
        density = max(0, min(100, int(cfg.get("meme_density", 50))))
    except (TypeError, ValueError):
        density = 50
    if density <= 0 and not question:
        return None
    try:
        memes = store.default()
        matches = memes.match(message, question=question, limit=2,
                              scope_id=scope)
    except Exception as e:
        ctx.log(f"本地梗匹配失败: {e}", "warn")
        return None
    if not matches:
        return None
    context = (event or {}).get("context") or {}
    relation = str(context.get("relation_tier") or "")
    private = str((event or {}).get("scope") or "") == "private" or not (event or {}).get("group_id")
    mood = context.get("mood") if isinstance(context.get("mood"), dict) else {}
    try:
        if float(mood.get("budget_shift") or 0) < 0 and not question:
            density = min(density, 25)
    except (TypeError, ValueError):
        pass
    content, selected = _packet(matches, question=question, relation=relation,
                                dense=density, private=private, text=message,
                                explanation=explanation)
    if not content or not selected:
        return None
    _prune_state()
    if scope:
        with _STATE_LOCK:
            _pending[scope] = {
                "items": [m.as_dict() for m in selected],
                "message": store.normalize(message),
                "ts": time.time(),
            }
    ctx.log(f"梗理解注入 {len(selected)} 条（relation={relation or '未知'} density={density}）")
    return {"action": "inject", "content": content, "decision": False}


def _surface_in_reply(surface: str, reply: str) -> bool:
    surface_c = store.compact(surface)
    if not surface_c:
        return False
    return reply == surface_c or (len(surface_c) >= 2 and surface_c in reply)


def on_after_reply(ctx: PluginContext, event: dict, reply: str):
    """Stage confirmed-use detection until the outbound send succeeds."""
    scope = _scope_id(event)
    with _STATE_LOCK:
        pending = _pending.pop(scope, None)
    if not scope or not pending or not reply:
        return None
    pending_message = str(pending.get("message") or "")
    current_message = store.normalize(event.get("message"))
    if pending_message and current_message and pending_message != current_message:
        return None
    reply_c = store.compact(reply)
    items = []
    for row in pending.get("items") or []:
        entry = row.get("entry") or {}
        word = str(entry.get("word") or row.get("word") or "")
        surfaces = [word] + list(entry.get("aliases") or [])
        if any(_surface_in_reply(x, reply_c) for x in surfaces):
            items.append({"word": word,
                          "surfaces": [str(x) for x in surfaces if str(x)]})
    if not items:
        return None
    _prune_state()
    with _STATE_LOCK:
        _delivery[scope] = {
            "items": items,
            "counted": [],
            "ts": time.time(),
        }
    return None


_LEARN_RE = re.compile(
    r"(?P<word>[A-Za-z0-9\u4e00-\u9fff]{2,12})\s*"
    r"(?:就是|指的是|意思是)\s*(?P<meaning>[^。！？!?\n]{2,80})")


def _confirmed_delivery(payload: dict) -> None:
    scope = _scope_id(payload)
    text_c = store.compact(payload.get("text"))
    if not scope or not text_c:
        return
    _prune_state()
    confirmed = []
    with _STATE_LOCK:
        staged = _delivery.get(scope)
        if not staged:
            return
        counted = set(str(x) for x in (staged.get("counted") or []))
        remaining = []
        for item in staged.get("items") or []:
            word = str(item.get("word") or "")
            if not word or word in counted:
                continue
            if any(_surface_in_reply(x, text_c)
                   for x in (item.get("surfaces") or [word])):
                counted.add(word)
                confirmed.append(word)
            else:
                remaining.append(item)
        if remaining:
            staged["items"] = remaining
            staged["counted"] = sorted(counted)
            if confirmed:
                staged["ts"] = time.time()
        else:
            _delivery.pop(scope, None)
    if not confirmed:
        return
    memes = store.default()
    for word in confirmed:
        memes.record_use(word, scope)
    with _STATE_LOCK:
        await_state = _awaiting.get(scope) or {
            "words": [], "seen": 0, "bad": 0,
        }
        words = list(await_state.get("words") or [])
        words.extend(x for x in confirmed if x not in words)
        await_state.update({"words": words, "ts": time.time()})
        _awaiting[scope] = await_state


def on_event(ctx: PluginContext, event_name: str = "", payload: dict = None,
             **kwargs):
    """Observe explanations and reactions; never send from this callback."""
    name = str(event_name or "")
    p = payload if isinstance(payload, dict) else {}
    if name == "bot.replied":
        try:
            _confirmed_delivery(p)
        except Exception:
            pass
        return None
    if name != "message.in":
        return None
    _prune_state()
    text = str(p.get("text") or "")
    scope = _scope_id(p)
    memes = None
    try:
        memes = store.default()
        m = _LEARN_RE.search(text)
        if m:
            memes.add_learn(m.group("word"), m.group("meaning"), text, scope)
    except Exception:
        pass
    if not scope:
        return None
    negative = bool(re.search(r"(?:别用|好尬|土了|什么玩意|听?不懂|没用过)", text))
    positive = bool(not negative and re.search(
        r"(?:哈哈|笑死|绷不住|乐死|确实|接住了|有梗|6+"
        r"|(?:^|[\s，。！？!?])(?:懂(?:了|得)?|草|乐)"
        r"(?:$|[\s，。！？!?]))",
        text))
    feedback = 0
    with _STATE_LOCK:
        awaiting = _awaiting.get(scope)
        if not awaiting:
            return None
        awaiting["seen"] = int(awaiting.get("seen") or 0) + 1
        if positive:
            feedback = 1
        elif negative:
            awaiting["bad"] = int(awaiting.get("bad") or 0) + 1
            if awaiting["bad"] >= 2:
                feedback = -1
        if int(awaiting["seen"]) >= 5 and not feedback:
            feedback = -1
        done = feedback or int(awaiting["seen"]) >= 5
        if done:
            _awaiting.pop(scope, None)
        else:
            _awaiting[scope] = awaiting
    if not feedback or memes is None:
        return None
    try:
        for word in awaiting.get("words") or []:
            memes.update_feedback(word, scope, feedback)
    except Exception:
        pass
    return None


def query(word: str) -> str:
    """查一个词，返回可直接发出去的文本（永不抛异常）。"""
    word = (word or "").strip()
    if not word:
        return "查什么？`!梗 <词>`，比如 `!梗 yyds`。"
    if word in ("随机", "--random", "random"):
        try:
            item = store.default().random()
        except Exception:
            item = None
        if item:
            return _format_local(item)
        item = wiki.random_entry()
        return wiki.format_entry(item) if item else "本地和上游都没有取到条目。"

    raw = word.startswith("--raw ")
    if raw:
        word = word[6:].strip()

    try:
        local_hits = store.default().match(word, question=True, limit=1)
    except Exception:
        local_hits = []
    if local_hits:
        item = local_hits[0].entry
        if raw:
            return str({k: item.get(k) for k in
                        ("word", "aliases", "meaning", "origin", "tone",
                         "circle", "risk", "used", "good")})[:600]
        return _format_local(item)
    try:
        hits = wiki.search(word, limit=5)
        if not hits:
            return f"「{word}」梗百科里没有，别编。\n可以自己补：{wiki.BASE}"
        ql = word.lower()
        exact = [h for h in hits
                 if str(h.get("title") or "").lower() == ql
                 or str(h.get("slug") or "").lower() == ql]
        pick = exact[0] if exact else hits[0]
        slug = str(pick.get("slug") or "")
        item = wiki.detail(slug) or pick
        if raw:
            return str({k: item.get(k) for k in
                        ("slug", "title", "summary", "tags")})[:600]
        if not exact and len(hits) > 1 and not _looks_like_one(word, pick):
            return wiki.format_hits(hits)
        return wiki.format_entry(item)
    except wiki.WikiError as e:
        return f"梗百科连不上（{e}），这次不瞎猜。"
    except Exception as e:  # 兜底：插件不能把主链带崩
        return f"查的时候出错了：{type(e).__name__}: {e}"


def _looks_like_one(word: str, pick: dict) -> bool:
    """搜出来的第一条和查询词关系够近吗（避免"绝绝子"回一条不相干的）。"""
    w = word.lower()
    t = str(pick.get("title") or "").lower()
    s = str(pick.get("summary") or "").lower()
    return bool(w) and (w in t or t in w or w in s[:80])


def on_message(ctx: PluginContext, event: dict):
    text = event.get("text") or ""
    arg = _parse(text)
    if arg is None:
        return None
    sender = str(event.get("sender") or "")
    if not _rate_ok(sender):
        return "查太勤了，歇一分钟。"
    return query(arg)


def act_search(params: dict):
    p = params or {}
    return {"ok": True, "text": query(str(p.get("word") or p.get("q") or ""))}


def act_random(params: dict):
    try:
        item = store.default().random()
        if item:
            return {"ok": True, "item": item, "text": _format_local(item)}
    except Exception:
        pass
    item = wiki.random_entry()
    if not item:
        return {"ok": False, "error": "上游不可用"}
    return {"ok": True, "item": item, "text": wiki.format_entry(item)}


def act_status(params: dict):
    memes = None
    local_error = ""
    try:
        memes = store.default()
    except Exception as e:
        local_error = str(e)
    network_hit = False
    network_error = ""
    try:
        network_hit = bool(wiki.search("yyds", limit=1))
    except Exception as e:
        network_error = str(e)
    local_count = len(memes._entries) if memes is not None else 0
    local_entries = 0
    if memes is not None:
        try:
            local_entries = len(memes.match("yyds", question=True, limit=1))
        except Exception as e:
            local_error = local_error or str(e)
    result = {
        "ok": bool(local_count or network_hit),
        "base": wiki.BASE,
        "cached": len(wiki._CACHE),
        "local_entries": local_entries,
        "local_count": local_count,
        "network_hit": network_hit,
    }
    if local_error:
        result["local_error"] = local_error
    if network_error:
        result["network_error"] = network_error
    return result


def act_learn_list(params: dict):
    p = params or {}
    status = str(p.get("status") or "pending")
    if status not in {"pending", "accepted", "rejected"}:
        status = "pending"
    try:
        limit = max(1, min(500, int(p.get("limit") or 100)))
    except (TypeError, ValueError):
        limit = 100
    items = store.default().list_learn(status, limit)
    return {"ok": True, "items": items, "count": len(items)}


def act_learn_review(params: dict):
    p = params or {}
    try:
        item_id = int(p.get("id") or 0)
    except (TypeError, ValueError):
        item_id = 0
    ok = store.default().review_learn(
        item_id, str(p.get("action") or ""),
        word=str(p.get("word") or ""), meaning=str(p.get("meaning") or ""))
    return {"ok": ok, "id": p.get("id"), "action": p.get("action")}


PLUGIN_HOOKS = {
    "on_load": on_load,
    "on_enable": on_enable,
    "on_disable": on_disable,
    "on_unload": on_unload,
    "on_message": on_message,
    "on_before_reply": on_before_reply,
    "on_after_reply": on_after_reply,
    "on_event": on_event,
}

PLUGIN_ACTIONS = {
    "search": act_search,
    "random": act_random,
    "status": act_status,
    "learn_list": act_learn_list,
    "learn_review": act_learn_review,
}
