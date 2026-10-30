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
_TURN = {"images": [], "is_group": False, "sender": "", "group_id": ""}

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
            "name": "meme_save",
            "description": ("把用户这一轮发来的图片存进表情库（用户说「存/收下/留着」时用）。"
                            "不传 path 就存这一轮的全部图。"),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "可选：指定某张本地路径"},
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


def set_turn(*, images=None, is_group: bool = False, sender: str = "",
             group_id: str = "") -> None:
    """每轮由主链写入：这一轮有哪些图、在哪个场景、发给谁。"""
    _TURN["images"] = [str(x) for x in (images or []) if str(x or "").strip()]
    _TURN["is_group"] = bool(is_group)
    _TURN["sender"] = str(sender or "")
    _TURN["group_id"] = str(group_id or "")


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
    out = {"ok": True, "count": len(rows[:n]), "total": len(idx),
           "items": rows[:n]}
    if q and not rows and idx:
        # 别让模型把"没匹配到关键词"说成"库里一张都没有"（真机 20:07 就这么说的）
        _tags = sorted({tag for rec in idx.values() if isinstance(rec, dict)
                        for tag in (rec.get("tags") or [])})[:12]
        out["hint"] = (f"没匹配到「{query}」；库里共 {len(idx)} 张，"
                       f"可用标签：{'、'.join(_tags) or '无'}。可换关键词或空查询取最近收的。")
    return out


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


def meme_save(path: str = "") -> Dict[str, Any]:
    """存这一轮（或指定）的图到表情库。返回每张的结果。"""
    _p = path if isinstance(path, str) else ""
    paths = [_p] if _p.strip() else list(_TURN.get("images") or [])
    if not paths:
        return {"ok": False, "error": "这一轮没有可存的图片"}
    scene_group = bool(_TURN.get("is_group") and _TURN.get("group_id"))
    saved, failed = [], []
    for p in paths[:4]:
        try:
            import meme_collect as _mc
            rel = _mc.maybe_collect(
                p, source=("group" if scene_group else "private"),
                from_group=(_TURN.get("group_id") if scene_group else ""))
        except Exception as e:
            failed.append({"path": p, "error": f"{type(e).__name__}: {e}"})
            continue
        if rel:
            saved.append(rel)
        else:
            failed.append({"path": p, "error": "重复/过大/格式不支持"})
    return {"ok": bool(saved), "saved": saved, "failed": failed,
            "count": len(saved), "scene": "group" if scene_group else "private"}


def register_all() -> Dict[str, Any]:
    from . import tool_registry as _tr
    fns = {"meme_find": meme_find, "meme_send": meme_send, "meme_save": meme_save}
    for sch in MEME_TOOLS:
        name = sch["function"]["name"]
        # 全放 work（只读组，常驻）：真机 20:53 证明 write/proc 组要命中
        # 特定词才注入，「存」不在词表里 → 模型根本看不到 meme_save。
        groups = (_tr.GROUP_WORK,)
        _tr.register(name, sch, fn=_tr.adapt_kwargs(fns[name]), groups=groups, source="harness")
    return {k: _tr.adapt_kwargs(v) for k, v in fns.items()}


__all__ = ["MEME_TOOLS", "set_sender", "meme_find", "meme_send", "register_all"]
