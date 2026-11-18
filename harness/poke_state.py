# -*- coding: utf-8 -*-
"""按「群 + 用户」保存戳一戳习惯和短反馈。"""
from __future__ import annotations

import copy
import json
import os
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("EGGBLOOM_USER_DATA") or os.path.join(ROOT, "data")
DEFAULT_PATH = os.environ.get("EGGBLOOM_POKE_STATE") or os.path.join(DATA_DIR, "poke_policy.json")

_LOCK = threading.RLock()
_CACHE = {"path": "", "mtime": None, "data": {}}

_POSITIVE = ("哈哈", "笑死", "好玩", "继续", "可爱", "贴贴", "晚安", "么么")
_NEGATIVE = ("别戳", "不要戳", "别闹", "烦", "滚", "讨厌", "生气", "别碰")


def _path():
    return os.environ.get("EGGBLOOM_POKE_STATE") or DEFAULT_PATH


def scope_key(scene: str, user_id: str, group_id: str = "") -> str:
    scene = str(scene or "private").strip().lower()
    uid = str(user_id or "").strip()
    if scene == "group":
        return f"group:{str(group_id or '').strip()}:{uid}"
    return f"private:{uid}"


def _blank():
    return {
        "pokes": [],
        "last_event_ts": 0.0,
        "last_event_id": "",
        "last_action": "",
        "last_action_ts": 0.0,
        "feedback": {},
        "pending": [],
    }


def _load():
    path = _path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    with _LOCK:
        if _CACHE["path"] == path and _CACHE["mtime"] == mtime:
            return _CACHE["data"]
        data = {}
        if mtime is not None:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                if isinstance(obj, dict):
                    data = obj
            except Exception:
                data = {}
        _CACHE.update({"path": path, "mtime": mtime, "data": data})
        return data


def _save(data):
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    with _LOCK:
        _CACHE.update({"path": path, "mtime": os.path.getmtime(path), "data": data})


def _entry(data, key):
    row = data.get(key)
    if not isinstance(row, dict):
        row = _blank()
        data[key] = row
    for k, v in _blank().items():
        row.setdefault(k, copy.deepcopy(v))
    return row


def begin_event(key: str, now: float | None = None, dedupe_sec: float = 30.0,
                event_id: str = "") -> dict:
    """登记一次 poke 事件；只有显式事件 ID 相同才标记 duplicate。"""
    ts = float(now if now is not None else time.time())
    with _LOCK:
        data = _load()
        row = _entry(data, str(key))
        eid = str(event_id or "")
        duplicate = bool(
            eid and row.get("last_event_id") == eid
            and ts - float(row.get("last_event_ts") or 0) < dedupe_sec
        )
        row["last_event_ts"] = ts
        if eid:
            row["last_event_id"] = eid
        if not duplicate:
            recent = [x for x in (row.get("pokes") or []) if ts - float(x) <= 120.0]
            recent.append(ts)
            row["pokes"] = recent[-12:]
            if row.get("last_action") == "poke" and ts - float(row.get("last_action_ts") or 0) <= 120:
                _bump(row, "poke", 0.06)
        _save(data)
        return snapshot_from_row(row, now=ts, duplicate=duplicate)


def snapshot(key: str, now: float | None = None) -> dict:
    ts = float(now if now is not None else time.time())
    with _LOCK:
        data = _load()
        row = _entry(data, str(key))
        return snapshot_from_row(row, now=ts)


def snapshot_from_row(row: dict, now: float | None = None, duplicate: bool = False) -> dict:
    ts = float(now if now is not None else time.time())
    pokes = [float(x) for x in (row.get("pokes") or []) if str(x).strip()]
    pokes = [x for x in pokes if ts - x <= 120.0]
    gap = (ts - pokes[-2]) if len(pokes) >= 2 else None
    last_gap = (ts - float(row.get("last_action_ts") or 0)) if row.get("last_action") else None
    return {
        "poke_count": len(pokes),
        "seconds_since_previous_poke": gap,
        "last_action": str(row.get("last_action") or ""),
        "seconds_since_last_action": last_gap,
        "feedback": dict(row.get("feedback") or {}),
        "duplicate": bool(duplicate),
    }


def can_poke(key: str, now: float | None = None, min_interval_sec: float = 60.0) -> bool:
    ts = float(now if now is not None else time.time())
    with _LOCK:
        data = _load()
        row = _entry(data, str(key))
        return not (row.get("last_action") == "poke"
                    and ts - float(row.get("last_action_ts") or 0) < float(min_interval_sec))


def record_action(key: str, action: str, now: float | None = None,
                  emotion: str = "", reason: str = "") -> dict:
    ts = float(now if now is not None else time.time())
    action = str(action or "skip")
    with _LOCK:
        data = _load()
        row = _entry(data, str(key))
        row["last_action"] = action
        row["last_action_ts"] = ts
        pending = list(row.get("pending") or [])
        pending.append({"action": action, "ts": ts, "seen": 0,
                        "emotion": str(emotion or ""), "reason": str(reason or "")})
        row["pending"] = pending[-6:]
        _save(data)
        return snapshot_from_row(row, now=ts)


def _bump(row, action, delta):
    fb = dict(row.get("feedback") or {})
    fb[action] = round(max(-1.0, min(1.0, float(fb.get(action, 0.0)) + float(delta))), 3)
    row["feedback"] = fb


def observe_message(key: str, text: str, now: float | None = None) -> dict:
    """观察动作后同一个人接下来的消息；按 5 条窗口更新动作偏好。"""
    ts = float(now if now is not None else time.time())
    msg = str(text or "").strip()
    if not msg:
        return {}
    with _LOCK:
        data = _load()
        row = _entry(data, str(key))
        changed = _observe_row(row, msg)
        if changed:
            _save(data)
        return snapshot_from_row(row, now=ts)


def _observe_row(row, text):
    msg = str(text or "").strip()
    hit = ""
    if any(w in msg for w in _NEGATIVE):
        hit = "negative"
    elif any(w in msg for w in _POSITIVE):
        hit = "positive"
    kept = []
    changed = False
    for item in row.get("pending") or []:
        if not isinstance(item, dict):
            continue
        item["seen"] = int(item.get("seen") or 0) + 1
        changed = True
        action = str(item.get("action") or "")
        if action and hit == "positive":
            _bump(row, action, 0.18)
        elif action and hit == "negative":
            _bump(row, action, -0.5)
        if int(item.get("seen") or 0) >= 5:
            if action and not hit:
                _bump(row, action, -0.05)
            continue
        kept.append(item)
    row["pending"] = kept
    return changed


def observe_group_activity(group_id: str, sender: str, text: str,
                           now: float | None = None) -> dict:
    """群里的任意消息推进该群所有待观察动作；只有目标本人发言才判接梗/反感。"""
    ts = float(now if now is not None else time.time())
    gid = str(group_id or "")
    uid = str(sender or "")
    prefix = f"group:{gid}:"
    with _LOCK:
        data = _load()
        changed = False
        for key, row in list(data.items()):
            if not str(key).startswith(prefix) or not isinstance(row, dict):
                continue
            target = str(key)[len(prefix):]
            changed = _observe_row(row, text if target == uid else "") or changed
        if changed:
            _save(data)
        key = scope_key("group", uid, gid)
        return snapshot_from_row(_entry(data, key), now=ts)
