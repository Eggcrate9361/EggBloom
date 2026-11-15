# -*- coding: utf-8 -*-
"""wiki —— 梗百科（cnmeme.wiki）的能力层。

纯函数 + 一个 TTL 缓存，不碰 ctx、不碰主程序，方便单测。

数据源（2026-11 实测可用）：
    GET /api/v1/public/memes?q=<词>&limit=N     → {"items":[{slug,title,summary,tags,...}]}
    GET /api/v1/public/memes/<slug>             → {"item":{...,content_html,source_video}}
    GET /api/v1/toy/wiki/random                 → {"item":{...}}（条目随机）
    GET /api/v1/toy/wiki/tags?limit=N           → 标签榜

注意：memepedia.miraheze.org 被 Cloudflare 挡了（403），所以只认 cnmeme。
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request

BASE = "https://cnmeme.wiki"
UA = {"User-Agent": "EggBloom-memewiki/1.0 (+https://cnmeme.wiki)"}
TIMEOUT = 8.0

#: slug/title → (过期时间, item)；只缓存详情，搜索不缓存（结果短、易变）
_CACHE: dict = {}
_CACHE_TTL = 3600.0
_CACHE_MAX = 200


class WikiError(RuntimeError):
    pass


def _get(path: str, params: dict | None = None, timeout: float = TIMEOUT) -> dict:
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        raise WikiError(f"请求失败: {type(e).__name__}: {e}") from e
    try:
        return json.loads(raw)
    except Exception as e:
        raise WikiError(f"返回不是 JSON: {raw[:120]}") from e


# ── 抓取 ────────────────────────────────────────────────────────────
def search(q: str, limit: int = 3) -> list:
    """按关键词搜条目（模糊）。查不到返回 []。"""
    q = (q or "").strip()
    if not q:
        return []
    d = _get("/api/v1/public/memes", {"q": q, "limit": max(1, min(int(limit), 10))})
    items = d.get("items") or []
    return [x for x in items if isinstance(x, dict)]


def detail(slug: str, use_cache: bool = True) -> dict | None:
    """取条目详情（含 content_html）。slug 也可以传中文标题的 urlencode 形式。"""
    slug = (slug or "").strip()
    if not slug:
        return None
    now = time.time()
    hit = _CACHE.get(slug)
    if use_cache and hit and hit[0] > now:
        return hit[1]
    try:
        d = _get("/api/v1/public/memes/" + urllib.parse.quote(slug))
    except WikiError:
        return None
    item = d.get("item")
    if not isinstance(item, dict):
        return None
    if len(_CACHE) > _CACHE_MAX:
        _CACHE.clear()
    _CACHE[slug] = (now + _CACHE_TTL, item)
    return item


def random_entry() -> dict | None:
    try:
        d = _get("/api/v1/toy/wiki/random")
    except WikiError:
        return None
    item = d.get("item")
    return item if isinstance(item, dict) else None


def find(q: str) -> dict | None:
    """先搜再取详情：同名/最相近的那条。查不到 None。"""
    hits = search(q, limit=5)
    if not hits:
        return None
    ql = q.strip().lower()
    best = None
    for h in hits:
        title = str(h.get("title") or "").lower()
        slug = str(h.get("slug") or "").lower()
        if ql and (title == ql or slug == ql):
            best = h
            break
    best = best or hits[0]
    slug = best.get("slug") or ""
    full = detail(slug) if slug else None
    return full or best


# ── 格式化 ──────────────────────────────────────────────────────────
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\u3000]+")


def strip_html(html: str) -> str:
    """把 content_html 压成纯文本段落列表（h2 段名保留为「段名：正文」）。"""
    if not html:
        return ""
    h = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", html)
    h = re.sub(r"(?i)</(p|div|li|h[1-6])>", "\n", h)
    h = re.sub(r"(?i)<h[1-6][^>]*>", "\n##", h)
    h = re.sub(r"(?i)<br\s*/?>", "\n", h)
    h = _TAG_RE.sub("", h)
    h = (h.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
          .replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'"))
    out = []
    for line in h.splitlines():
        line = _WS_RE.sub(" ", line).strip(" *·-")
        if line:
            out.append(line)
    return "\n".join(out)


def entry_url(item: dict) -> str:
    slug = str(item.get("slug") or "")
    return f"{BASE}/entry/{urllib.parse.quote(slug)}" if slug else BASE


def format_entry(item: dict, *, body_chars: int = 220, max_tags: int = 5) -> str:
    """把一条详情压成一条 QQ 消息（不超 ~400 字）。"""
    if not item:
        return ""
    title = str(item.get("title") or item.get("slug") or "?").strip()
    lines = [f"【{title}】"]

    body = strip_html(item.get("content_html") or "")
    if body:
        # 「##来源」这种段名去掉井号，只留字
        body = body.replace("##", "")
        flat = " ".join(body.split())
        if len(flat) > body_chars:
            flat = flat[:body_chars].rstrip() + "…"
        lines.append(flat)
    else:
        s = " ".join(str(item.get("summary") or "").split())
        if s:
            lines.append(s[:body_chars].rstrip() + ("…" if len(s) > body_chars else ""))

    tags = [str(t) for t in (item.get("tags") or [])][:max_tags]
    if tags:
        lines.append("标签：" + " / ".join(tags))

    sv = item.get("source_video") or {}
    if isinstance(sv, dict) and sv.get("title"):
        up = str(sv.get("creator_name") or "").strip()
        lines.append(f"出处视频：{sv['title']}" + (f"（{up}）" if up else ""))

    lines.append(entry_url(item))
    return "\n".join(lines)


def format_hits(hits: list, *, max_n: int = 3) -> str:
    """搜索没命中唯一结果时的候选列表。"""
    if not hits:
        return "梗百科里没查到这条，别编。"
    lines = ["梗百科里没完全对上，可能是这几条："]
    for h in hits[:max_n]:
        t = str(h.get("title") or h.get("slug") or "?").strip()
        s = " ".join(str(h.get("summary") or "").split())[:60]
        lines.append(f"· {t}" + (f"：{s}…" if s else ""))
    lines.append(f"要哪条说名字，或者看 {BASE}")
    return "\n".join(lines)
