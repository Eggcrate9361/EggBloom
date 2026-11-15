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

import time

from plugins.abi import PluginContext
from plugins.memewiki import wiki

PLUGIN_SPEC = 1
PLUGIN_MANIFEST = {
    "name": "memewiki",
    "version": "1.0.0",
    "api_version": 1,
    "author": "EggBloom",
    "description": "梗百科：!梗 <词> 查出处/用法，查不到就直说（接 cnmeme.wiki）",
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


def on_load(ctx: PluginContext):
    ctx.log("memewiki loaded — !梗 <词> 查梗百科")


def on_enable(ctx: PluginContext):
    ctx.log("memewiki enabled")


def on_disable(ctx: PluginContext):
    ctx.log("memewiki disabled")


def on_unload(ctx: PluginContext):
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


def query(word: str) -> str:
    """查一个词，返回可直接发出去的文本（永不抛异常）。"""
    word = (word or "").strip()
    if not word:
        return "查什么？`!梗 <词>`，比如 `!梗 yyds`。"
    if word in ("随机", "--random", "random"):
        item = wiki.random_entry()
        return wiki.format_entry(item) if item else "随机都取不到，上游大概在抽风。"

    raw = word.startswith("--raw ")
    if raw:
        word = word[6:].strip()

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
    item = wiki.random_entry()
    if not item:
        return {"ok": False, "error": "上游不可用"}
    return {"ok": True, "item": item, "text": wiki.format_entry(item)}


def act_status(params: dict):
    try:
        hits = wiki.search("yyds", limit=1)
        return {"ok": bool(hits), "base": wiki.BASE, "cached": len(wiki._CACHE)}
    except Exception as e:
        return {"ok": False, "base": wiki.BASE, "error": str(e)}


PLUGIN_HOOKS = {
    "on_load": on_load,
    "on_enable": on_enable,
    "on_disable": on_disable,
    "on_unload": on_unload,
    "on_message": on_message,
}

PLUGIN_ACTIONS = {
    "search": act_search,
    "random": act_random,
    "status": act_status,
}
