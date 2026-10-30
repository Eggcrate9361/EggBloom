# -*- coding: utf-8 -*-
"""harness/tool_meme.py —— 找回并发出自己收的表情包（2026-10-30）。

用户要的：「把你偷的那张图再发一遍」——模型得能**准确调出来**，不是又随机配一张。

    meme_find(query)   → 在图库里找（路径/标签/画面描述都能匹配），返回候选
    meme_send(id)      → 把指定那张发到当前对话

只查自己收的（`memes/memes/collected/index.json`），只发图库里的文件（防目录穿越）。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEME_ROOT = os.path.join(_ROOT, "memes", "memes")
INDEX_FILE = os.path.join(MEME_ROOT, "collected", "index.json")
DESC_FILE = os.path.join(MEME_ROOT, "descriptions.json")

_SEND = None

MEME_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "meme_find",
            "description": ("在你自己收的表情包里找（按标签/画面描述/文件名）。"
                            "用户要你发「之前收的那张图」时先用它找到 id。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "关键词，可空（空=最近收的）"},
                    "limit": {"type": "number", "description": "最多几条，默认 5"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "meme_send",
            "description": "把 meme_find 找到的那张表情包发到当前对话。id 用返回里的 id。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "meme_find 返回的 id"},
                },
                "required": ["id"],
            },
        },
    },
]


def set_sender(fn) -> None:
    """宿主注入：fn(full_path, ctx) -> bool。"""
    global _SEND
    _SEND = fn if callable(fn) else None


def _load(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _desc_for(rel: str, descs: dict) -> str:
    v = descs.get(rel)
    if isinstance(v, dict):
        v = v.get("desc")
    if not v:
        v = descs.get(os.path.basename(rel))
        if isinstance(v, dict):
            v = v.get("desc")
    return str(v or "")


def meme_find(query: str = "", limit: int = 5) -> Dict[str, Any]:
    q = str(query or "").strip().lower()
    idx = _load(INDEX_FILE)
    descs = _load(DESC_FILE)
    rows: List[dict] = []
    for sha, rec in idx.items():
        if not isinstance(rec, dict):
            continue
        rel = str(rec.get("path") or "")
        if not rel:
            continue
        tags = [str(t) for t in (rec.get("tags") or [])]
        desc = _desc_for(rel, descs)
        hay = (rel + " " + "、".join(tags) + " " + desc).lower()
        if q and q not in hay:
            continue
        rows.append({
            "id": rel,
            "tags": tags,
            "desc": desc[:120],
            "collected_at": rec.get("ts"),
            "sha1": sha,
        })
    rows.sort(key=lambda r: float(r.get("collected_at") or 0), reverse=True)
    n = max(1, min(20, int(limit or 5)))
    return {"ok": True, "count": len(rows[:n]), "items": rows[:n]}


def _resolve(meme_id: str) -> str:
    mid = str(meme_id or "").strip().replace("\\", "/")
    if not mid:
        return ""
    # 允许相对（collected/x.jpg）或绝对；统一限制在图库根内
    full = mid if os.path.isabs(mid) else os.path.join(MEME_ROOT, mid)
    full = os.path.normpath(full)
    root = os.path.normpath(MEME_ROOT)
    if not (full == root or full.startswith(root + os.sep)):
        return ""
    return full if os.path.isfile(full) else ""


def meme_send(meme_id: str) -> Dict[str, Any]:
    full = _resolve(meme_id)
    if not full:
        return {"ok": False, "error": "没这张图（id 不对或不在图库里）"}
    if not callable(_SEND):
        return {"ok": False, "error": "发送未接线"}
    try:
        from . import tool_task as _tt
        ctx = dict(getattr(_tt, "_CTX", {}) or {})
    except Exception:
        ctx = {}
    if not ctx.get("sender") and not ctx.get("group_id"):
        return {"ok": False, "error": "没有对话上下文，发不出去"}
    try:
        ok = bool(_SEND(full, ctx))
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if not ok:
        return {"ok": False, "error": "发送失败（协议层拒绝）"}
    return {"ok": True, "sent": os.path.relpath(full, MEME_ROOT).replace("\\", "/")}


def register_all() -> Dict[str, Any]:
    from . import tool_registry as _tr
    fns = {"meme_find": meme_find, "meme_send": meme_send}
    for sch in MEME_TOOLS:
        name = sch["function"]["name"]
        groups = (_tr.GROUP_WORK,) if name == "meme_find" else (_tr.GROUP_PROC,)
        _tr.register(name, sch, fn=fns.get(name), groups=groups, source="harness")
    return fns


__all__ = ["MEME_TOOLS", "set_sender", "meme_find", "meme_send", "register_all"]
