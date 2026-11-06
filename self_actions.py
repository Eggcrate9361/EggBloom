# -*- coding: utf-8 -*-
"""self_actions.py —— 行为账本：它知道自己干过什么（2026-10-30）。

## 为什么

模型有几件事是**别人告诉它**的（谁说了什么、时间、记忆），但"我刚刚做了什么"
一直是断的：

- 发完表情包，history 里有一行，但它下一轮不一定把自己当成"发图的人"；
- 戳了谁、把谁的图收进了表情库、发了张图 —— 它没有账，问起来只能猜；
- 于是会出现"我没发图啊""我没戳你"这种自相矛盾。

这一层只做一件事：**它自己的动作也进账**，并且下一轮能看到一小段。

## 2026-11-01 晚补：光有"一小段"不够

用户原话：「bot 什么时候存了什么表情包… 他知道自己是什么时候从谁存的什么样的
表情包… 很多事件因为是纯代码没有进入记忆，bot 自己做了，但它不知道」。

- 15 分钟的窗口只够"刚做过"；问"昨天那张呢"就答不上来；
- 而且部分动作**根本没有记账**（`meme_save` 走工具、纯代码，一条都没有）。

所以这一层现在做两件事：
1. `record()` 记进短期账本（原样，供 `render()` 注入"你刚做过"）；
2. **`PERSIST_KINDS` 里的动作同时写进记忆库事件**（`events` 表，经
   `harness.memory_api.remember("event")`）—— 这样它隔天也能被召回；
3. 记录里新增 `source`（这张图是谁那儿来的）/ `ref`（图库相对路径）/
   `group_id`，回答"从谁那儿存的、存的哪张"时有据可依。

## 边界

- 只记**对外可见的动作**（发图/发表情包/存图/戳/采集/主动开口/派活），不记内部思考；
- 不把"说了一句话"写进记忆库（那是 messages/历史的活，写进去会淹掉真正的事件）；
- 保留 N 条（默认 200），落 `data/self_actions.jsonl`，按日可读；
- 记忆库写失败**不影响动作本身**，也不抛。
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

#: 哪些动作值得**进长期记忆**（写 super_memory 的 events 表）。
#: 不含 `say`：说话本身已在历史/消息表里，再写一遍会把真正的事件挤掉。
PERSIST_KINDS = frozenset({
    "meme_save", "meme_send", "meme", "collect", "poke",
    "task", "image", "file", "job", "proactive",
    "edit", "run", "admin",
})

#: 各类动作写进记忆时的重要度（影响衰减/保留层级，见 super_memory._classify_tier）
PERSIST_IMPORTANCE = {
    "meme_save": 0.7, "collect": 0.7, "task": 0.65,
    "poke": 0.6, "image": 0.6, "file": 0.6, "job": 0.6,
    "meme": 0.5, "meme_send": 0.5, "proactive": 0.5,
    "edit": 0.6, "run": 0.5, "admin": 0.6,
}

#: 关掉长期写入（排障/测试用）：`set_persist(False)`
_PERSIST_ON = True

_LOCK = threading.Lock()
_MEM: Optional[deque] = None
_LOG = print


def set_logger(fn) -> None:
    global _LOG
    if callable(fn):
        _LOG = fn


def set_persist(on: bool) -> None:
    """临时关掉"写进记忆库"（短期账本照记）。"""
    global _PERSIST_ON
    _PERSIST_ON = bool(on)


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


def _in_test_main() -> bool:
    """当前进程是不是"跑某个 tests/test_*.py"。

    ★ 为什么要有这道闸：`record()` 现在会写记忆库，而测试里大量 record 假动作
      （`sa.record("meme", target="777")`）。2026-11-01 实测：跑一遍
      `test_self_actions` / `test_self_history`，仓库侧 `super_memory.db` 的
      events 表多了 6 条假事件 —— 测试把生产记忆写脏了。
      想真的测落库，设 `EGGBLOOM_ALLOW_ACTION_EVENTS=1`（并用
      `EGGBLOOM_SUPER_MEMORY_DB` 指到临时库）。
    """
    try:
        import sys
        if os.environ.get("EGGBLOOM_ALLOW_ACTION_EVENTS") == "1":
            return False
        return os.path.basename(sys.argv[0] or "").startswith("test_")
    except Exception:
        return False


def _persist(rec: dict) -> None:
    """把这条动作写进**长期记忆**（events 表），让隔天问起来也答得上。

    失败静默：记忆库没起来、插件没加载、sqlite 忙 —— 都不该影响动作本身。
    """
    kind = str(rec.get("kind") or "")
    if not _PERSIST_ON or kind not in PERSIST_KINDS:
        return
    if _in_test_main():
        return
    scene = str(rec.get("scene") or "")
    scope = "private" if scene == "private" else "group"
    scope_id = str(rec.get("group_id") or rec.get("target") or "")
    if not scope_id:
        return
    src = str(rec.get("source") or "")
    detail = str(rec.get("detail") or "")
    word = _KIND_WORD.get(kind, kind)
    summary = f"{word}{('：' + detail) if detail else ''}"
    if src and src != scope_id:
        summary += f"（来自 {src}）"
    try:
        from harness.memory_api import remember
        ok = remember("event", uid=(src or scope_id), text=summary, scene=scene,
                      group_id=scope_id,
                      meta={"scope": scope, "scope_id": scope_id,
                            "user_id": (src if scene != "private" else scope_id),
                            "event_type": kind,
                            "importance": PERSIST_IMPORTANCE.get(kind, 0.6)})
    except Exception as e:
        try:
            _LOG(f"[行为] 写记忆失败（不影响动作）: {e}")
        except Exception:
            pass
        return
    if ok:
        try:
            _LOG(f"[行为] 入记忆：{summary[:60]}")
        except Exception:
            pass


def record(kind: str, *, scene: str = "", target: str = "",
           detail: str = "", ts: Optional[float] = None,
           source: str = "", ref: str = "", group_id: str = "",
           persist: bool = True) -> dict:
    """记一条自己的动作。返回这条记录（失败也返回，不抛）。

    参数：
      source  —— 这动作跟谁有关（表情包是谁发的、戳的是谁）
      ref     —— 稳定的引用（图库里的相对路径 / 任务号），供事后精确找回
      persist —— 是否同时写进长期记忆（默认按 PERSIST_KINDS 判断）
    """
    rec = {"ts": float(ts if ts is not None else time.time()),
           "kind": str(kind or "")[:24],
           "scene": str(scene or "")[:12],
           "target": str(target or "")[:24],
           "detail": str(detail or "")[:120]}
    if source:
        rec["source"] = str(source or "")[:24]
    if ref:
        rec["ref"] = str(ref or "")[:80]
    if group_id:
        rec["group_id"] = str(group_id or "")[:24]
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
    if persist:
        _persist(rec)
    return rec


# ── 工具层专用：不知道自己在哪个场景，由主链每轮告诉它 ──────────────
# 工具函数（ws_write / py_run / 群管理）只拿得到自己的参数，不知道"这轮是群里
# 还是私聊、对谁"。让它们各自猜会猜错（群里记成私聊，target 记成群号）。
# 所以主链在**每轮开始时**调 `set_turn()`，工具只调 `record_tool()`。
_TURN = {"scene": "", "target": "", "group_id": ""}


def set_turn(*, scene: str = "", target: str = "", group_id: str = "") -> None:
    """记下这一轮在跟谁说话（主链调用；工具只读）。"""
    _TURN["scene"] = str(scene or "")[:12]
    _TURN["target"] = str(target or "")[:24]
    _TURN["group_id"] = str(group_id or "")[:24]


def record_tool(kind: str, detail: str = "", ref: str = "", **kw) -> dict:
    """工具动作入账（edit / run / admin）。失败不抛，绝不影响工具本身。"""
    try:
        ctx = dict(_TURN)
        return record(kind,
                      scene=kw.pop("scene", ctx["scene"]),
                      target=kw.pop("target", ctx["target"]),
                      detail=detail, ref=ref,
                      group_id=kw.pop("group_id", ctx["group_id"]), **kw)
    except Exception:
        return {"kind": str(kind or "")}


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
    "meme_send": "发了一张表情包",
    "meme_save": "存了一张表情包",
    "image": "发了一张图",
    "poke": "戳了对方",
    "collect": "把群里的一张图收进了表情库",
    "task": "派了个活给后台子 agent",
    "job": "起了一个后台任务",
    "proactive": "主动开了口",
    "file": "发了一个文件",
    # 2026-11-06：工具层的三类动作。以前它们只在当轮的工具消息里，
    # 下一轮问"你改过哪个文件"就说不清了。
    "edit": "改了文件",
    "run": "跑了一段代码",
    "admin": "动了群设置",
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
        src = str(r.get("source") or "").strip()
        try:
            hm = time.strftime("%H:%M", time.localtime(float(r.get("ts") or 0)))
        except Exception:
            hm = ""
        line = f"- {hm} 你{word}"
        if detail:
            line += f"（{detail}）"
        # 用户 2026-11-01：「他知道自己是什么时候**从谁**存的」——
        # 来源照记；只在动作真有 source 时才出现（不是每句都挂个人名）。
        if src and src != str(r.get("target") or ""):
            line += f"〔来源 {src}〕"
        lines.append(line)
    return ("【你刚做过的事】（都是你自己干的；要查更早/更多的，用 self_history）\n"
            + "\n".join(lines))


__all__ = ["record", "recent", "search", "render", "set_logger", "LOG_FILE"]
