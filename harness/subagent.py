# -*- coding: utf-8 -*-
"""harness/subagent.py —— 后台子 agent 任务队列（2026-10-30）。

## 为什么

QQ 里 agent 一接管回复，就和聊天模型打架：

- 占位句是固定的（"等下，我看看"），人格被冲掉；
- 任务汇报式回答顶掉正常聊天；
- 用户说话得等任务跑完。

按用户的设计：**聊天模型继续聊，任务派给后台子 agent，干完了再报**。

## 契约

    submit(goal, run_fn=..., on_done=..., timeout=120) -> task_id
    status(task_id=None) -> {task_id, state, goal, result, error, ...}

- 后台线程执行 `run_fn(goal)`，返回值就是结果文本；
- `on_done(task)` 在**执行线程里**回调（宿主负责发消息/落库）；
- 状态机：queued → running → done / failed / timeout / cancelled；
- 并发上限 2，排队；单任务超时（默认 120s）→ timeout，不杀线程（Python 没法优雅杀），
  但结果丢弃；
- 账本落 `data/subagent_tasks.jsonl`，重启后仍可查最近任务。

## 不做什么

- 不做嵌套派活（子 agent 里再派子 agent）：`in_worker` 标记拦掉；
- 不做跨进程恢复（重启后 running 的标成 failed）。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
LOG_FILE = os.path.join(DATA_DIR, "subagent_tasks.jsonl")
MAX_CONCURRENT = 2
MAX_KEEP = 100
DEFAULT_TIMEOUT = 120.0

_LOCK = threading.Lock()
_TASKS: "OrderedDict[str, dict]" = OrderedDict()
_SEM = threading.BoundedSemaphore(MAX_CONCURRENT)
_ACTIVE = threading.local()


def set_logger(fn) -> None:
    global _LOG
    if callable(fn):
        _LOG = fn


_LOG = print


def _now() -> float:
    return time.time()


def _persist(rec: dict) -> None:
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _trim() -> None:
    with _LOCK:
        while len(_TASKS) > MAX_KEEP:
            _TASKS.popitem(last=False)


def submit(goal: str, *, run_fn: Callable[[str], str],
           on_done: Optional[Callable[[dict], Any]] = None,
           timeout: float = DEFAULT_TIMEOUT, meta: Optional[dict] = None) -> str:
    """派一个后台任务，返回 task_id。"""
    tid = "t" + uuid.uuid4().hex[:10]
    rec = {
        "task_id": tid,
        "goal": str(goal or "")[:500],
        "state": "queued",
        "result": "",
        "error": "",
        "ts": _now(),
        "started": 0.0,
        "ended": 0.0,
        "meta": dict(meta or {}),
    }
    with _LOCK:
        _TASKS[tid] = rec
    _persist(rec)

    # 子 agent 里不许再派活（判断必须发生在**调用方线程**：
    # 新线程里 thread-local 是空的，交给 _runner 判断会被绕过）。
    if getattr(_ACTIVE, "in_worker", False):
        _finish(tid, state="failed", error="子 agent 里不允许再派活")
        return tid

    def _runner():
        if getattr(_ACTIVE, "in_worker", False):
            _finish(tid, state="failed", error="子 agent 里不允许再派活")
            return
        if not _SEM.acquire(timeout=timeout):
            _finish(tid, state="timeout", error="排队超时")
            return
        _ACTIVE.in_worker = True
        try:
            with _LOCK:
                rec["state"] = "running"
                rec["started"] = _now()
            _persist(dict(rec, state="running"))
            out = run_fn(str(goal or ""))
            _finish(tid, state="done", result=str(out or ""))
        except Exception as e:
            _finish(tid, state="failed", error=f"{type(e).__name__}: {e}")
        finally:
            _ACTIVE.in_worker = False
            try:
                _SEM.release()
            except Exception:
                pass
        if on_done:
            try:
                on_done(status(tid) or {})
            except Exception as e:
                try:
                    _LOG(f"[子agent] on_done 回调失败: {e}")
                except Exception:
                    pass

    try:
        threading.Thread(target=_runner, daemon=True, name=f"subagent-{tid}").start()
    except Exception as e:
        _finish(tid, state="failed", error=f"线程启动失败: {e}")
    return tid


def _finish(tid: str, *, state: str, result: str = "", error: str = "") -> None:
    with _LOCK:
        rec = _TASKS.get(tid)
        if not rec:
            return
        rec["state"] = state
        rec["result"] = str(result or "")[:4000]
        rec["error"] = str(error or "")[:500]
        rec["ended"] = _now()
        snap = dict(rec)
    _persist(snap)
    _trim()


def status(task_id: str = "") -> Any:
    """单查 / 列表（最近的在前）。"""
    with _LOCK:
        if task_id:
            rec = _TASKS.get(str(task_id))
            return dict(rec) if rec else {}
        return [dict(r) for r in list(_TASKS.values())[::-1][:20]]


def pending() -> List[dict]:
    with _LOCK:
        return [dict(r) for r in _TASKS.values() if r.get("state") in ("queued", "running")]


def reset() -> None:
    """测试用：清内存任务（不动文件）。"""
    with _LOCK:
        _TASKS.clear()


__all__ = ["submit", "status", "pending", "reset", "set_logger",
           "MAX_CONCURRENT", "DEFAULT_TIMEOUT", "LOG_FILE"]
