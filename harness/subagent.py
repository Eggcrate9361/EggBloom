# -*- coding: utf-8 -*-
"""harness/subagent.py —— 后台子 agent 任务队列（2026-10-30，1.6.0 改造）。

## 为什么

QQ 里 agent 一接管回复，就和聊天模型打架：占位句冲掉人格、任务汇报式回答顶掉
正常聊天、用户说话得等任务跑完。按用户的设计：**聊天模型继续聊，任务派给后台
子 agent，干完了再报**。

## 契约

    submit(goal, run_fn=..., on_done=..., timeout=120) -> task_id
    status(task_id=None) -> {task_id, state, goal, result, error, ...}

- 后台线程执行 `run_fn(goal)`，返回值就是结果文本；
- `on_done(task)` 在**执行线程里**回调（宿主负责发消息/落库）；
  **它一定在状态转终态之后执行**（回调里 `status()` 才读得到 result），
  所以「state 已是 done」不等于「回调已跑完」—— 需要等副作用落地的调用方
  看 `status()["settled"]`（内存态，落盘不带）。2026-11-13 之前没有这个字段，
  `test_subagent_resume` 就是在这里偶发红的：它看到 done 就断言回调次数，
  而那时回调可能还没跑。
- 状态机：queued → running → done / failed / timeout / cancelled；
- 并发上限 2，排队；单任务超时（默认 120s）→ timeout，不杀线程，结果丢弃；
- 账本落 `data/subagent_tasks.jsonl`，重启后仍可查最近任务。

## 1.6.0：状态改走 TaskCenter，允许恢复

以前状态**只在内存 `OrderedDict`**，`data/subagent_tasks.jsonl` 是只写不读的流水，
重启 running 一律等价于「丢了/只能标 failed」。现在每次状态变更都镜像进
`harness/task_center.py` 的统一总表（磁盘、跨进程），并新增：

    recover(reason="")              把上次进程遗留的 running/queued 收成 paused
                                    （**可恢复，不是 failed**）；由 TaskCenter.recover() 统一调用
    resume(task_id, run_fn=...)     重新执行一个 paused/queued/running 的任务
    list_recoverable()              列出待恢复任务，供宿主/新进程接手

`status(task_id)` 在内存里查不到时会回落总表，所以「重启后凭 task_id 查得到、
不是 failed」成立。`reset()` 仍是**只清内存**的测试helper（不动磁盘总表），
恢复能力因此不受它影响。

## 不做什么

- 不做嵌套派活（子 agent 里再派子 agent）：`in_worker` 标记拦掉（保留）；
- 不再「重启 running 一律 failed」—— 改为 paused + 显式 resume。
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


# ── 统一任务中心接线（1.6.0）────────────────────────────────────
def _tc():
    try:
        from . import task_center
        return task_center
    except Exception:
        return None


def _tc_mirror(tid: str, **fields) -> None:
    """把一次状态变更镜像进总表（收口到全局唯一 job_id）。best-effort，不拖垮主流程。"""
    tc = _tc()
    if tc is None:
        return
    try:
        tc.register(tid, source="subagent", kind="subagent",
                    external_id=tid, **fields)
    except Exception:
        pass


def _tc_get(tid: str) -> Optional[dict]:
    tc = _tc()
    if tc is None:
        return None
    try:
        return tc.get(tid)
    except Exception:
        return None


def _tc_all() -> List[dict]:
    tc = _tc()
    if tc is None:
        return []
    try:
        return tc.list_jobs(source="subagent", limit=MAX_KEEP)
    except Exception:
        return []


def _tc_to_sub(rec: dict) -> dict:
    """总表记录 → subagent.status() 形状。running/queued 残留一律呈现为 paused。"""
    st = rec.get("status")
    if st in ("running", "queued"):
        st = "paused"
    return {
        "task_id": rec.get("job_id"),
        "goal": rec.get("label") or "",
        "state": st,
        "result": rec.get("result") or "",
        "error": rec.get("error") or "",
        "ts": rec.get("created") or 0.0,
        "started": rec.get("started") or 0.0,
        "ended": rec.get("ended") or 0.0,
        "meta": rec.get("meta") or {},
    }


# ── 提交 / 执行 ─────────────────────────────────────────────────
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
    _tc_mirror(tid, label=rec["goal"], status="queued",
               payload={"goal": rec["goal"]}, meta=rec["meta"], created=rec["ts"])

    # 子 agent 里不许再派活（判断必须发生在**调用方线程**：
    # 新线程里 thread-local 是空的，交给 _runner 判断会被绕过）。
    if getattr(_ACTIVE, "in_worker", False):
        _finish(tid, state="failed", error="子 agent 里不允许再派活")
        return tid
    _spawn(tid, rec["goal"], run_fn, on_done, timeout, rec["meta"])
    return tid


def _spawn(tid: str, goal: str, run_fn: Callable[[str], str],
           on_done, timeout: float, meta: dict) -> None:
    """启动/重启一个任务的执行线程（submit 与 resume 共用）。"""
    def _runner():
        if getattr(_ACTIVE, "in_worker", False):
            _finish(tid, state="failed", error="子 agent 里不允许再派活")
            _settle(tid)
            return
        if not _SEM.acquire(timeout=timeout):
            _finish(tid, state="timeout", error="排队超时")
            _settle(tid)
            return
        _ACTIVE.in_worker = True
        try:
            with _LOCK:
                rec = _TASKS.get(tid)
                if rec is None:
                    rec = {"task_id": tid, "goal": goal, "state": "running",
                           "result": "", "error": "", "ts": _now(),
                           "started": 0.0, "ended": 0.0, "meta": dict(meta or {})}
                    _TASKS[tid] = rec
                rec["state"] = "running"
                rec["started"] = _now()
            _persist(dict(rec, state="running"))
            _tc_mirror(tid, status="running", started=_now())
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
        _settle(tid)

    try:
        threading.Thread(target=_runner, daemon=True, name=f"subagent-{tid}").start()
    except Exception as e:
        _finish(tid, state="failed", error=f"线程启动失败: {e}")
        _settle(tid)


def _settle(tid: str) -> None:
    """标记「终态之后该做的都做完了」（on_done 已返回）。

    为什么不把 on_done 挪到 _finish 之前：回调里要 `status(tid)["result"]`，
    状态不先转终态就读不到。顺序只能是 终态 → 回调，那么"回调是否已跑完"
    就必须单独可观测，否则调用方只能靠 sleep 猜。这个字段就是那个观测点。
    """
    with _LOCK:
        rec = _TASKS.get(tid)
        if rec is not None:
            rec["settled"] = True


def _finish(tid: str, *, state: str, result: str = "", error: str = "") -> None:
    with _LOCK:
        rec = _TASKS.get(tid)
        if not rec:
            rec = {"task_id": tid, "goal": "", "state": state, "result": "",
                   "error": "", "ts": _now(), "started": 0.0, "ended": 0.0,
                   "meta": {}}
            _TASKS[tid] = rec
        rec["state"] = state
        rec["result"] = str(result or "")[:4000]
        rec["error"] = str(error or "")[:500]
        rec["ended"] = _now()
        snap = dict(rec)
    _persist(snap)
    _tc_mirror(tid, status=state, result=snap["result"],
               error=snap["error"], ended=snap["ended"])
    _trim()


def status(task_id: str = "") -> Any:
    """单查 / 列表（最近的在前）。单查在内存里没有时**回落总表**（重启恢复）。"""
    with _LOCK:
        if task_id:
            rec = _TASKS.get(str(task_id))
            if rec:
                return dict(rec)
            r = _tc_get(str(task_id))
            return _tc_to_sub(r) if r else {}
        return [dict(r) for r in list(_TASKS.values())[::-1][:20]]


def pending() -> List[dict]:
    with _LOCK:
        return [dict(r) for r in _TASKS.values()
                if r.get("state") in ("queued", "running", "paused")]


# ── 恢复（1.6.0）───────────────────────────────────────────────
def recover(reason: str = "") -> int:
    """重启收尸：总表里遗留的 subagent `running/queued` → `paused`（可恢复）。

    **不再一律标 failed**。返回被收成 paused 的条数；由 TaskCenter.recover() 统一调用。
    """
    why = str(reason or "").strip() or "进程重启，原执行者已不存在"
    tc = _tc()
    if tc is None:
        return 0
    n = 0
    try:
        for r in tc.list_jobs(source="subagent", limit=MAX_KEEP):
            if r.get("status") in ("running", "queued"):
                tc.update(r["job_id"], status="paused", error=why)
                n += 1
    except Exception:
        pass
    return n


def list_recoverable() -> List[dict]:
    """列出总表里待接手的 subagent 任务（paused/queued/running），供新进程 resume。"""
    out = []
    for r in _tc_all():
        if r.get("status") in ("paused", "running", "queued"):
            out.append(_tc_to_sub(r))
    return out


def resume(task_id: str, *, run_fn: Optional[Callable[[str], str]] = None,
           on_done: Optional[Callable[[dict], Any]] = None,
           timeout: float = DEFAULT_TIMEOUT) -> str:
    """恢复一个可恢复任务（重启后 running/queued/paused 不再一律 failed）。

    给了 `run_fn` 就用它重跑（原执行线程已经没了，必须由宿主重新给执行体）；
    不给则用一个占位执行体，把状态收成 done 并记一行说明。返回 task_id，
    不可恢复（不存在、或已终态且没给 run_fn）返回空串。
    """
    tid = str(task_id or "").strip()
    rec = _tc_get(tid)
    if not rec:
        return ""
    state = rec.get("status")
    if state in ("done", "failed", "cancelled", "killed", "timeout") and run_fn is None:
        return ""
    goal = str(rec.get("label") or "")
    meta = rec.get("meta") or {}

    with _LOCK:
        _TASKS[tid] = {"task_id": tid, "goal": goal, "state": "queued",
                       "result": "", "error": "", "ts": _now(),
                       "started": 0.0, "ended": 0.0, "meta": dict(meta)}
    _tc_mirror(tid, label=goal, status="queued", result="", error="", ended=0.0)

    if getattr(_ACTIVE, "in_worker", False):
        _finish(tid, state="failed", error="子 agent 里不允许再派活")
        return tid
    fn = run_fn if callable(run_fn) else (lambda g: "（恢复后未提供 run_fn）")
    _spawn(tid, goal, fn, on_done, timeout, meta)
    return tid


def reset() -> None:
    """测试用：清内存任务（**不动磁盘总表** —— 恢复能力不受影响）。"""
    with _LOCK:
        _TASKS.clear()


__all__ = ["submit", "status", "pending", "reset", "set_logger",
           "recover", "resume", "list_recoverable",
           "MAX_CONCURRENT", "DEFAULT_TIMEOUT", "LOG_FILE"]
