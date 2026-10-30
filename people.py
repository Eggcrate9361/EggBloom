# -*- coding: utf-8 -*-
"""people.py —— 身份档案层（「分得清谁是谁」，2026-10-30）。

## 现在的问题

记忆里能证明"谁说的"的只有 QQ 号；名字是**属性**，却被当成**键**在用：

- `people.json` 只有 `{uid: {name, last_seen, groups}}` —— 没有别名、没有"同一个人
  的几个号"、没有"你记得他什么"；
- 群名片 / 全局昵称 / 你让他用的称呼，三者混在一处，重启就只剩最后写进去的那个；
- 同群重名（两个不同的 QQ 都叫「小明」）没有判据，模型只能猜；
- 私聊里说的事和群里说的事，没法按"这个人"汇总。

## 这一层做什么

**把人当主语**：QQ 号是主键，名字是属性，事实挂在人身上。

    uid ──┬── names: {global, 群:gid, alias}
          ├── accounts: [其他号]            # 只能由本人明说，绝不猜
          ├── facts:  [{k, v, scope, ts}]   # scope=private 的只在私聊提
          └── notes:  [自由文本]

对外只暴露三个函数：`card()`（喂给模型的档案块）、`add_fact()`、`link_account()`。

## 存储

- 名字/群归属仍读 `data/people.json`（sandglass 在维护，不动它）；
- 本层新增的字段写 `data/people_extra.json`（同一 uid 键，读时合并）。
  分离是为了**不打扰在跑的主链**：sandglass 每 10 秒落一次 people.json，
  两边同时写同一个文件会互相覆盖。

## 红线

- 不猜"这两个号是同一个人"。只有用户明说（"我小号是…""这是我另一个号"）才关联；
- `scope=private` 的事实只在私聊注入；群里只给 `scope=global` 的。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PEOPLE_FILE = os.path.join(DATA_DIR, "people.json")
EXTRA_FILE = os.path.join(DATA_DIR, "people_extra.json")
MAX_FACTS = 40
MAX_NOTE_LEN = 200

_EXTRA: Optional[Dict[str, dict]] = None
_BASE: Optional[Dict[str, dict]] = None


def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _extra_store() -> Dict[str, dict]:
    global _EXTRA
    if _EXTRA is None:
        raw = _load_json(EXTRA_FILE)
        out = {}
        for uid, rec in raw.items():
            if isinstance(rec, dict):
                out[str(uid)] = rec
        _EXTRA = out
    return _EXTRA


def _base_store() -> Dict[str, dict]:
    global _BASE
    if _BASE is None:
        _BASE = _load_json(PEOPLE_FILE)
    return _BASE


def reload() -> None:
    """丢掉内存缓存（测试/运维用）。"""
    global _EXTRA, _BASE
    _EXTRA = None
    _BASE = None


def _save() -> bool:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = EXTRA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_extra_store(), f, ensure_ascii=False, indent=1)
        os.replace(tmp, EXTRA_FILE)
        return True
    except Exception:
        return False


# ── 读 ──────────────────────────────────────────────────────────
def get(uid) -> Dict[str, Any]:
    """合并后的档案：{uid, name, names, alias, accounts, facts, groups, last_seen}。"""
    u = str(uid or "").strip()
    base = dict(_base_store().get(u) or {})
    ext = dict(_extra_store().get(u) or {})
    names = dict(ext.get("names") or {})
    return {
        "uid": u,
        # 新层写过全局名就以新层为准（改名是最近发生的事）
        "name": str(names.get("global") or base.get("name") or ""),
        "names": names,
        "alias": str(ext.get("alias") or ""),
        "accounts": [str(x) for x in (ext.get("accounts") or [])],
        "facts": list(ext.get("facts") or []),
        "notes": list(ext.get("notes") or []),
        "groups": [str(g) for g in (base.get("groups") or [])],
        "last_seen": base.get("last_seen") or 0,
    }


def canonical(uid) -> str:
    """同一个人有多个号时，归一到主号（没关联就是自己）。"""
    u = str(uid or "").strip()
    rec = _extra_store().get(u) or {}
    return str(rec.get("primary") or u)


def same_person(a, b) -> bool:
    return canonical(a) == canonical(b)


def display(uid, group_id="") -> str:
    """给模型看的名字：别名 > 本群名片 > 全局昵称 > 裸号。"""
    p = get(uid)
    if p["alias"]:
        return p["alias"]
    if group_id:
        card = (p["names"] or {}).get(f"群:{group_id}")
        if card:
            return card
    return p["name"] or p["uid"]


# ── 写 ──────────────────────────────────────────────────────────
def note_name(uid, name: str, group_id="") -> bool:
    """记下一个名字。group_id 给了就记群名片，否则记全局昵称。"""
    u, n = str(uid or "").strip(), str(name or "").strip()
    if not u or not n:
        return False
    rec = _extra_store().setdefault(u, {})
    names = rec.setdefault("names", {})
    key = f"群:{str(group_id)}" if str(group_id or "").strip() else "global"
    if names.get(key) == n:
        return False
    names[key] = n
    _save()
    return True


def set_alias(uid, alias: str) -> bool:
    u, a = str(uid or "").strip(), str(alias or "").strip()
    if not u or not a:
        return False
    rec = _extra_store().setdefault(u, {})
    if rec.get("alias") == a:
        return False
    rec["alias"] = a
    _save()
    return True


def add_fact(uid, key: str, value: str, *, scope: str = "global",
             src: str = "") -> bool:
    """记一条关于这个人的事实（同 key 覆盖）。scope=private 只在私聊注入。"""
    u = str(uid or "").strip()
    k, v = str(key or "").strip(), str(value or "").strip()
    if not u or not k or not v:
        return False
    sc = "private" if str(scope) == "private" else "global"
    rec = _extra_store().setdefault(u, {})
    facts = [f for f in (rec.get("facts") or []) if isinstance(f, dict)]
    facts = [f for f in facts if str(f.get("k")) != k]
    facts.append({"k": k, "v": v[:MAX_NOTE_LEN], "scope": sc,
                  "ts": time.time(), "src": str(src or "")[:40]})
    rec["facts"] = facts[-MAX_FACTS:]
    _save()
    return True


def link_account(uid, other) -> bool:
    """把两个号标记成"同一个人"。只能由本人明说时调用，不猜。"""
    a, b = str(uid or "").strip(), str(other or "").strip()
    if not a or not b or a == b:
        return False
    for x, y in ((a, b), (b, a)):
        rec = _extra_store().setdefault(x, {})
        acc = [str(t) for t in (rec.get("accounts") or [])]
        if y not in acc:
            acc.append(y)
        rec["accounts"] = acc
        rec["primary"] = a
    _save()
    return True


def unlink_account(uid, other) -> bool:
    a, b = str(uid or "").strip(), str(other or "").strip()
    if not a or not b:
        return False
    changed = False
    for x, y in ((a, b), (b, a)):
        rec = _extra_store().get(x) or {}
        acc = [t for t in (rec.get("accounts") or []) if str(t) != y]
        if len(acc) != len(rec.get("accounts") or []):
            rec["accounts"] = acc
            rec.pop("primary", None)
            changed = True
    if changed:
        _save()
    return changed


# ── 给模型的档案块 ──────────────────────────────────────────────
def card(uid, group_id="", *, private: bool = False,
         max_facts: int = 6) -> str:
    """一段【人物档案】。没有值得说的就返回空串（零 token）。"""
    p = get(uid)
    if not p["uid"]:
        return ""
    bits: List[str] = []
    who = p["name"] or p["uid"]
    card_name = (p["names"] or {}).get(f"群:{group_id}") if group_id else ""
    head = f"{who}({p['uid']})"
    if card_name and card_name != who:
        head += f"，在本群叫「{card_name}」"
    if p["alias"] and p["alias"] != who:
        head += f"，你平时叫他「{p['alias']}」"
    bits.append(head)

    others = [a for a in p["accounts"] if a != p["uid"]]
    if others:
        bits.append("他还有这些号（同一个人，别当两个人）：" + "、".join(others))

    facts = []
    for f in p["facts"]:
        if not isinstance(f, dict):
            continue
        if f.get("scope") == "private" and not private:
            continue
        k, v = str(f.get("k") or ""), str(f.get("v") or "")
        if k and v:
            facts.append(f"{k}={v}")
        if len(facts) >= max_facts:
            break
    if facts:
        bits.append("你记得：" + "；".join(facts))
    if len(bits) <= 1 and not facts:
        return ""
    return "【人物档案】" + "。".join(bits) + "。"


# ── 冲突检测（同群重名）────────────────────────────────────────
def same_name_uids(group_id, name: str) -> List[str]:
    """本群里显示名完全相同的不同 QQ（重名时模型必须靠号区分）。"""
    n = str(name or "").strip()
    if not n:
        return []
    hits = []
    for uid in _base_store():
        p = get(uid)
        shown = (p["names"] or {}).get(f"群:{group_id}") or p["name"]
        if shown == n:
            hits.append(str(uid))
    return sorted(hits)


__all__ = ["get", "canonical", "same_person", "display", "note_name", "set_alias",
           "add_fact", "link_account", "unlink_account", "card", "reload",
           "same_name_uids", "PEOPLE_FILE", "EXTRA_FILE"]
