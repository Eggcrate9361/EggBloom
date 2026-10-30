# -*- coding: utf-8 -*-
"""self_actions.py —— 行为账本：它知道自己干过什么（2026-10-30）。

## 为什么

模型有几件事是**别人告诉它**的（谁说了什么、时间、记忆），但"我刚刚做了什么"
一直是断的：

- 发完表情包，history 里有一行，但它下一轮不一定把自己当成"发图的人"；
- 戳了谁、把谁的图收进了表情库、发了张图 —— 它没有账，问起来只能猜；
- 于是会出现"我没发图啊""我没戳你"这种自相矛盾。

这一层只做一件事：**它自己的动作也进账**，并且下一轮能看到一小段。

## 边界

- 只记**对外可见的动作**（发图/发表情包/戳/采集/主动开口），不记内部思考与工具调用
  （工具调用另有 trace）；
- 不进人格、不做评价，只陈述"何时、在哪、做了什么"；
- 保留 N 条（默认 200），落 `data/self_actions.jsonl`，按日可读；
- 说的时候**不指名来源**：捡了谁的表情包只说"从群里捡的"，不把 A 的图归到 A 头上。
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from typing import Iterable, List, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
LOG_FILE = os.path.join(DATA_DIR, "self_actions.jsonl")
MAX_KEEP = 200
MAX_FILE_LINES = 5000

_LOCK = threading.Lock()
_MEM: Optional[deque] = None
_LOG = print


def set_logger(fn) -> None:
    global _LOG
    if callable(fn):
        _LOG = fn


def _load() -> deque:
    global _MEM
    if _MEM is not None:
        return _MEM
    d = deque(maxlen=MAX_KEEP)
    try:
        if os.path.isfile(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()[-MAX_KEEP:]
            for ln in lines:
                try:
                    rec = json.loads(ln)
                    if isinstance(rec, dict):
                        d.append(rec)
                except Exception:
                    continue
    except Exception:
        pass
    _MEM = d
    return d


def _rotate_if_big() -> None:
    try:
        if os.path.isfile(LOG_FILE) and os.path.getsize(LOG_FILE) > 2 * 1024 * 1024:
            os.replace(LOG_FILE, LOG_FILE + ".1")
    except Exception:
        pass


def record(kind: str, *, scene: str = "", target: str = "",
           detail: str = "", ts: Optional[float] = None) -> dict:
    """记一条自己的动作。返回这条记录（失败也返回，不抛）。"""
    rec = {"ts": float(ts if ts is not None else time.time()),
           "kind": str(kind or "")[:24],
           "scene": str(scene or "")[:12],
           "target": str(target or "")[:24],
           "detail": str(detail or "")[:80]}
    try:
        with _LOCK:
            d = _load()
            d.append(rec)
            os.makedirs(DATA_DIR, exist_ok=True)
            _rotate_if_big()
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return rec


def recent(*, scene: str = "", target: str = "", minutes: float = 15.0,
           limit: int = 5, now: Optional[float] = None) -> List[dict]:
    """最近的动作（可按场景/对象过滤）。"""
    cutoff = (now if now is not None else time.time()) - max(1.0, float(minutes)) * 60.0
    rows = []
    with _LOCK:
        items = list(_load())
    for rec in items:
        try:
            if float(rec.get("ts") or 0) < cutoff:
                continue          # 不 break：ts 可能乱序（补记/测试会传旧时间）
        except Exception:
            continue
        if scene and str(rec.get("scene") or "") not in ("", scene):
            continue
        if target and str(rec.get("target") or "") not in ("", target):
            continue
        rows.append(rec)
    rows.sort(key=lambda r: float(r.get("ts") or 0))
    return rows[-max(1, int(limit)):]


def _all_records(max_lines: int = 5000) -> List[dict]:
    """读整本账（文件比内存 deque 长时，检索要能看到更早的）。"""
    rows = []
    try:
        if os.path.isfile(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()[-max_lines:]
            for ln in lines:
                try:
                    rec = json.loads(ln)
                    if isinstance(rec, dict):
                        rows.append(rec)
                except Exception:
                    continue
    except Exception:
        pass
    if not rows:
        with _LOCK:
            rows = list(_load())
    return rows


def search(*, query: str = "", kind: str = "", scene: str = "",
           target: str = "", hours: Optional[float] = None,
           limit: int = 10, now: Optional[float] = None) -> List[dict]:
    """检索行为账本。`hours=None` = 不限时间；不传 query = 全量按时间倒序。"""
    q = str(query or "").strip().lower()
    cutoff = None
    if hours is not None:
        cutoff = (now if now is not None else time.time()) - max(0.1, float(hours)) * 3600.0
    rows = []
    for rec in _all_records():
        try:
            ts = float(rec.get("ts") or 0)
        except Exception:
            continue
        if cutoff is not None and ts < cutoff:
            continue
        if kind and str(rec.get("kind") or "") != kind:
            continue
        if scene and str(rec.get("scene") or "") != scene:
            continue
        if target and str(rec.get("target") or "") != target:
            continue
        if q:
            hay = (str(rec.get("detail") or "") + " " + str(rec.get("kind") or "")).lower()
            if q not in hay:
                continue
        rows.append(rec)
    rows.sort(key=lambda r: float(r.get("ts") or 0), reverse=True)
    return rows[:max(1, int(limit))]


_KIND_WORD = {
    "say": "说了一句",
    "meme": "发了一张表情包",
    "image": "发了一张图",
    "poke": "戳了对方",
    "collect": "把群里的一张图收进了表情库",
    "task": "派了个活给子 agent",
    "proactive": "主动开了口",
    "file": "发了一个文件",
}


def render(*, scene: str = "", target: str = "", minutes: float = 15.0,
           limit: int = 6, now: Optional[float] = None,
           include_group_say: bool = False) -> str:
    """一段【你刚做过】。没有动作就返回空串（零 token）。

    `include_group_say`：私聊里问"你刚在群里说什么了"时，把自己的群发言
    也带进来（只带**自己说的**，不带群友的话）。
    """
    rows = recent(scene=scene, target=target, minutes=minutes, limit=limit, now=now)
    if include_group_say and scene == "private":
        extra = [r for r in recent(scene="group", minutes=minutes, limit=3, now=now)
                 if str(r.get("kind") or "") == "say"]
        if extra:
            rows = sorted(rows + extra, key=lambda r: float(r.get("ts") or 0))[-limit:]
    if not rows:
        return ""
    lines = []
    for r in rows:
        kind = str(r.get("kind") or "")
        word = _KIND_WORD.get(kind, kind or "做了点什么")
        detail = str(r.get("detail") or "").strip()
        try:
            hm = time.strftime("%H:%M", time.localtime(float(r.get("ts") or 0)))
        except Exception:
            hm = ""
        line = f"- {hm} 你{word}"
        if detail:
            line += f"（{detail}）"
        lines.append(line)
    return ("【你刚做过的事】（都是你自己干的；要查更早/更多的，用 self_history）\n"
            + "\n".join(lines))


__all__ = ["record", "recent", "search", "render", "set_logger", "LOG_FILE"]
