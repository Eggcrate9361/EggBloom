# -*- coding: utf-8 -*-
"""harness/task_center.py —— 统一任务中心（1.6.0「四件套」之一）。

## 为什么要有它

仓库里原本有四套各自为政、互不可见的状态机：

    harness/tool_proc.py    _JOBS（纯内存 dict）+ job-<hex>.json 落盘
    harness/ticket.py       SQLite tickets（工单状态机）
    harness/outbox.py       SQLite jobs（出站队列状态机）
    harness/aigc_task.py    data/media/**/<task_id>.json（产物元数据）

它们各自发号、各自恢复、各自记状态。最要命的是 `tool_proc._JOBS` 是**纯内存表**：
进程一重启表就空了，`job_wait(job_id)` 直接「没有后台任务」—— 明明子进程还在跑、
日志还在涨，却接不上。这正是本层要修的头号问题。

## 本层做什么

把四套状态**收口到全局唯一 `job_id`**：一张 SQLite 总表登记每个任务的来源、
外部 id、状态、产物/日志路径、pid。各子系统通过 `register/update` 把状态镜像进来，
对外只暴露一套 `submit / wait / resume / cancel / list / recover`。

关键性质：**状态与运行态都落盘**。`get/wait` 只认磁盘上的那一行，不认进程内存 ——
所以「换一个进程、凭同一个 job_id 续等」是天然成立的（另一个进程打开同一个库即可）。
这跟 `tool_proc._restore_jobs`（只在 import 时读一次）不冲突：后者是**本进程**的
Popen 句柄缓存，前者是**跨进程**的续等注册表。

## 复用与隔离

- 默认库 `<app>/data/task_center.sqlite3`，可用 `EGGBLOOM_TASKCENTER_DB` 覆盖；
  `default(path)` / `reset_default()` 与其它 harness 数据层同款，方便用例之间隔离。
- 每个操作开一条新连接（`timeout=10`），线程/进程都能并发读写，不共享 sqlite 连接。
- 纪律与 ticket/outbox 一致：**不 import 主程序、不联网**；`recover()` 里才惰性
  import 其它子系统，避免加载期循环依赖。
"""
from __future__ import annotations

import json
import inspect
import os
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

# ── 状态 ────────────────────────────────────────────────────────
ST_QUEUED = "queued"
ST_RUNNING = "running"
ST_PAUSED = "paused"        # 重启后原 running 落到这里（可 resume，不是 failed）
ST_DONE = "done"
ST_FAILED = "failed"
ST_TIMEOUT = "timeout"
ST_CANCELLED = "cancelled"
ST_KILLED = "killed"
ST_BLOCKED = "blocked"

TERMINAL = (ST_DONE, ST_FAILED, ST_TIMEOUT, ST_CANCELLED, ST_KILLED)
RECOVERABLE = (ST_QUEUED, ST_RUNNING, ST_PAUSED, ST_FAILED, ST_TIMEOUT, ST_BLOCKED)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    kind        TEXT NOT NULL DEFAULT 'generic',
    source      TEXT NOT NULL DEFAULT 'generic',
    external_id TEXT NOT NULL DEFAULT '',
    label       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'queued',
    payload     TEXT NOT NULL DEFAULT '{}',
    result      TEXT NOT NULL DEFAULT '',
    error       TEXT NOT NULL DEFAULT '',
    meta        TEXT NOT NULL DEFAULT '{}',
    pid         INTEGER NOT NULL DEFAULT 0,
    log_path    TEXT NOT NULL DEFAULT '',
    attempts    INTEGER NOT NULL DEFAULT 0,
    created     REAL NOT NULL DEFAULT 0,
    updated     REAL NOT NULL DEFAULT 0,
    started     REAL NOT NULL DEFAULT 0,
    ended       REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tc_status ON jobs(status, updated);
CREATE INDEX IF NOT EXISTS idx_tc_source ON jobs(source, created);
"""

_COLS = ("job_id", "kind", "source", "external_id", "label", "status",
         "payload", "result", "error", "meta", "pid", "log_path", "attempts",
         "created", "updated", "started", "ended")


def _now() -> float:
    return time.time()


def _dump(x) -> str:
    """payload/meta 统一序列化：dict/list → json；其它 → str。"""
    if isinstance(x, str):
        return x
    try:
        return json.dumps(x if x is not None else {}, ensure_ascii=False, default=str)
    except Exception:
        return "{}"


def _load(s, *, default=None):
    try:
        return json.loads(s or "{}")
    except Exception:
        return {} if default is None else default


def _text(x) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    try:
        return json.dumps(x, ensure_ascii=False, default=str)
    except Exception:
        return str(x)


def _mirror_changed(cur: Optional[dict], fields: dict) -> bool:
    if cur is None:
        return True
    for key, value in fields.items():
        old = cur.get(key)
        if key in ("payload", "meta"):
            try:
                old = json.loads(old) if isinstance(old, str) else old
            except Exception:
                pass
        if old != value:
            return True
    return False


def _default_path() -> str:
    env = os.environ.get("EGGBLOOM_TASKCENTER_DB")
    if env:
        return str(env)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "data", "task_center.sqlite3")


class TaskCenter:
    """统一任务总表。一个实例一条 SQLite 连接串（路径）。"""

    def __init__(self, path: str = ""):
        self.path = str(path or _default_path())
        self._lock = threading.RLock()
        self._cancel_events: Dict[str, threading.Event] = {}
        self._init()

    # ── 基础设施 ──────────────────────────────────────────────
    def _conn(self):
        d = os.path.dirname(os.path.abspath(self.path))
        if d:
            os.makedirs(d, exist_ok=True)
        c = __import__("sqlite3").connect(self.path, timeout=10)
        c.row_factory = __import__("sqlite3").Row
        return c

    def _init(self) -> None:
        with self._lock:
            c = self._conn()
            try:
                c.executescript(_SCHEMA)
                c.commit()
            finally:
                c.close()

    @staticmethod
    def _row(r) -> Dict[str, Any]:
        d = {k: r[k] for k in _COLS}
        d["payload"] = _load(d.get("payload"), default={})
        d["meta"] = _load(d.get("meta"), default={})
        d["handback"] = False
        return d

    # ── 读 ───────────────────────────────────────────────────
    def get(self, job_id: str) -> Optional[dict]:
        j = str(job_id or "").strip()
        if not j:
            return None
        with self._lock:
            c = self._conn()
            try:
                r = c.execute("SELECT * FROM jobs WHERE job_id = ?", (j,)).fetchone()
                return self._row(r) if r else None
            finally:
                c.close()

    def list(self, *, status: str = "", source: str = "", limit: int = 100) -> List[dict]:
        sql = "SELECT * FROM jobs"
        cond, args = [], []
        if status:
            cond.append("status = ?")
            args.append(str(status))
        if source:
            cond.append("source = ?")
            args.append(str(source))
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY created DESC LIMIT ?"
        args.append(max(1, int(limit)))
        with self._lock:
            c = self._conn()
            try:
                return [self._row(r) for r in c.execute(sql, args)]
            finally:
                c.close()

    def stats(self) -> Dict[str, int]:
        with self._lock:
            c = self._conn()
            try:
                rows = c.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status").fetchall()
                out = {str(r["status"]): int(r["n"]) for r in rows}
                out["total"] = sum(v for k, v in out.items() if k != "total")
                return out
            finally:
                c.close()

    # ── 写 ───────────────────────────────────────────────────
    def _insert(self, rec: dict) -> dict:
        with self._lock:
            c = self._conn()
            try:
                c.execute(
                    f"INSERT OR IGNORE INTO jobs ({', '.join(_COLS)}) "
                    f"VALUES ({', '.join('?' * len(_COLS))})",
                    tuple(rec.get(k) for k in _COLS))
                c.commit()
            finally:
                c.close()
        return self.get(rec["job_id"])

    def register(self, job_id: str, **fields) -> dict:
        """upsert 一条记录（供四套子系统把已有任务镜像进总表）。

        job_id 已存在 → 只更新传进来的字段；不存在 → 按默认值补齐全行再插入。
        这正是「收口到全局唯一 job_id」的入口：外部子系统的 id 原样当 job_id 用，
        或显式传 external_id 保留原系统的号。
        """
        j = str(job_id or "").strip()
        if not j:
            return {}
        known = {k: v for k, v in fields.items() if k in _COLS and k != "job_id"}
        cur = self.get(j)
        if cur is None:
            now = _now()
            rec = {k: (0.0 if k in ("created", "updated", "started", "ended")
                       else (0 if k in ("pid", "attempts") else "")) for k in _COLS}
            rec.update({"job_id": j, "kind": "generic", "source": "generic",
                        "status": ST_QUEUED, "payload": "{}", "meta": "{}",
                        "created": now, "updated": now})
            rec.update(known)
            rec["payload"] = _dump(rec.get("payload"))
            rec["meta"] = _dump(rec.get("meta"))
            return self._insert(rec)
        # 更新：payload/meta 允许传 dict，统一转字符串
        for k in ("payload", "meta"):
            if k in known and not isinstance(known[k], str):
                known[k] = _dump(known[k])
        known.setdefault("updated", _now())
        cols = ", ".join(f"{k} = ?" for k in known)
        with self._lock:
            c = self._conn()
            try:
                c.execute(f"UPDATE jobs SET {cols} WHERE job_id = ?",
                          [*known.values(), j])
                c.commit()
            finally:
                c.close()
        return self.get(j)

    def update(self, job_id: str, **fields) -> Optional[dict]:
        """改状态/结果。找不到返回 None（不静默造一条）。"""
        j = str(job_id or "").strip()
        if not j or self.get(j) is None:
            return None
        known = {k: v for k, v in fields.items() if k in _COLS and k != "job_id"}
        for k in ("payload", "meta"):
            if k in known and not isinstance(known[k], str):
                known[k] = _dump(known[k])
        known["updated"] = _now()
        if not known:
            return self.get(j)
        cols = ", ".join(f"{k} = ?" for k in known)
        with self._lock:
            c = self._conn()
            try:
                c.execute(f"UPDATE jobs SET {cols} WHERE job_id = ?",
                          [*known.values(), j])
                c.commit()
            finally:
                c.close()
        return self.get(j)

    # ── 提交 / 异步执行 ──────────────────────────────────────
    def submit(self, *, kind: str = "generic", source: str = "generic",
               label: str = "", payload: Any = None, job_id: str = "",
               external_id: str = "", meta: Optional[dict] = None,
               run_fn=None, on_done=None, timeout: float = 0.0,
               pid: int = 0, log_path: str = "") -> str:
        """登记一个任务并返回**全局唯一 job_id**。

        run_fn 不为空时，后台线程执行 `run_fn(payload)`，状态走
        queued → running → done/failed，并回调 on_done(record)。
        不传 run_fn 时只登记（其它进程/子系统随后用 update 推进状态）——
        这正是跨进程续等的典型用法。
        """
        j = str(job_id or "").strip() or ("job-" + uuid.uuid4().hex[:12])
        now = _now()
        rec = {
            "job_id": j, "kind": str(kind or "generic"),
            "source": str(source or "generic"),
            "external_id": str(external_id or ""), "label": str(label or ""),
            "status": ST_QUEUED, "payload": _dump(payload),
            "result": "", "error": "", "meta": _dump(meta or {}),
            "pid": int(pid or 0), "log_path": str(log_path or ""),
            "attempts": 0, "created": now, "updated": now,
            "started": 0.0, "ended": 0.0,
        }
        with self._lock:
            c = self._conn()
            try:
                cur = c.execute(
                    f"INSERT OR IGNORE INTO jobs ({', '.join(_COLS)}) "
                    f"VALUES ({', '.join('?' * len(_COLS))})",
                    tuple(rec[k] for k in _COLS))
                c.commit()
                if int(cur.rowcount or 0) != 1:
                    raise ValueError(f"job_id 已存在：{j}")
            finally:
                c.close()
        if run_fn is not None:
            self._run_async(j, run_fn, payload, on_done, timeout)
        return j

    def _run_async(self, job_id: str, run_fn, payload, on_done, timeout: float) -> None:
        cancel_event = threading.Event()
        with self._lock:
            self._cancel_events[job_id] = cancel_event

        def runner():
            if not self._transition(job_id, {ST_QUEUED, ST_PAUSED},
                                    status=ST_RUNNING, started=_now(),
                                    attempts=self._bump(job_id)):
                if on_done:
                    try:
                        on_done(self.get(job_id))
                    except Exception:
                        pass
                with self._lock:
                    self._cancel_events.pop(job_id, None)
                return
            done = threading.Event()
            holder: Dict[str, Any] = {}

            def work():
                try:
                    out = self._call_executor(run_fn, payload, cancel_event)
                    holder["result"] = out
                except Exception as e:
                    holder["error"] = f"{type(e).__name__}: {e}"
                finally:
                    done.set()

            worker = threading.Thread(target=work, daemon=True,
                                      name=f"taskcenter-work-{job_id}")
            try:
                worker.start()
                wait_s = max(0.0, float(timeout or 0))
                if wait_s > 0:
                    completed = done.wait(wait_s)
                else:
                    while not done.is_set() and not cancel_event.is_set():
                        done.wait(0.05)
                    completed = done.is_set()
                if cancel_event.is_set():
                    pass
                elif not completed:
                    cancel_event.set()
                    self._transition(job_id, {ST_RUNNING}, status=ST_TIMEOUT,
                                     error=f"执行超时（>{wait_s:g}s，后台执行未终止）",
                                     ended=_now())
                elif holder.get("error"):
                    self._transition(job_id, {ST_RUNNING}, status=ST_FAILED,
                                     error=holder["error"], ended=_now())
                else:
                    self._transition(job_id, {ST_RUNNING}, status=ST_DONE,
                                     result=_text(holder.get("result")),
                                     ended=_now())
            except Exception as e:
                self._transition(job_id, {ST_RUNNING}, status=ST_FAILED,
                                 error=f"{type(e).__name__}: {e}", ended=_now())
            rec = self.get(job_id)
            if on_done:
                try:
                    on_done(rec)
                except Exception:
                    pass
            with self._lock:
                self._cancel_events.pop(job_id, None)
        try:
            threading.Thread(target=runner, daemon=True,
                             name=f"taskcenter-{job_id}").start()
        except Exception as e:
            self.update(job_id, status=ST_FAILED,
                        error=f"线程启动失败: {e}", ended=_now())
            with self._lock:
                self._cancel_events.pop(job_id, None)

    def _bump(self, job_id: str) -> int:
        cur = self.get(job_id)
        return int((cur or {}).get("attempts") or 0) + 1

    def _transition(self, job_id: str, expected, **fields) -> bool:
        """Apply a state update only when the current status is expected."""
        known = {k: v for k, v in fields.items() if k in _COLS and k != "job_id"}
        for k in ("payload", "meta"):
            if k in known and not isinstance(known[k], str):
                known[k] = _dump(known[k])
        known["updated"] = _now()
        expected = tuple(str(x) for x in (expected or ()))
        if not expected:
            return False
        marks = ", ".join("?" for _ in expected)
        cols = ", ".join(f"{k} = ?" for k in known)
        with self._lock:
            c = self._conn()
            try:
                cur = c.execute(
                    f"UPDATE jobs SET {cols} WHERE job_id = ? AND status IN ({marks})",
                    [*known.values(), str(job_id or ""), *expected])
                c.commit()
                return int(cur.rowcount or 0) == 1
            finally:
                c.close()

    @staticmethod
    def _call_executor(run_fn, payload, cancel_event):
        """Pass a cancellation token when the executor explicitly accepts it."""
        try:
            params = inspect.signature(run_fn).parameters
        except (TypeError, ValueError):
            params = {}
        kwargs = {}
        if "cancel_event" in params:
            kwargs["cancel_event"] = cancel_event
        elif "should_cancel" in params:
            kwargs["should_cancel"] = cancel_event.is_set
        return run_fn(payload, **kwargs)

    # ── wait / resume / cancel ────────────────────────────────
    def wait(self, job_id: str, *, timeout: float = 30.0, poll: float = 0.1,
             until: str = "", log_reader=None) -> Optional[dict]:
        """凭 job_id 等到终态（跨进程：只认磁盘那一行）。

        - `until` 传正则：在 result 或 log_reader(job_id) 的尾部命中即提前返回；
        - 到点还没结束 → 如实返回当前（仍是 running 的）记录，不假装结束。
        """
        j = str(job_id or "").strip()
        if self.get(j) is None:
            return None
        try:
            pat = re.compile(until) if until else None
        except re.error:
            pat = None
        deadline = _now() + max(0.0, float(timeout or 0))
        while True:
            rec = self.get(j)
            if rec is None:
                return None
            if rec["status"] in TERMINAL:
                return rec
            if pat is not None:
                tail = rec.get("result") or ""
                if log_reader:
                    try:
                        tail += "\n" + str(log_reader(j) or "")
                    except Exception:
                        pass
                if pat.search(tail):
                    return rec
            if _now() >= deadline:
                return rec
            time.sleep(max(0.01, float(poll or 0.1)))

    def resume(self, job_id: str, *, run_fn=None, payload: Any = None,
               on_done=None, timeout: float = 0.0) -> Optional[dict]:
        """恢复一个可恢复任务：默认把它拉回 queued；给了 run_fn 就重新执行。

        终态（done/cancelled/killed）默认不复活；要强制重跑请显式传 run_fn。
        """
        j = str(job_id or "").strip()
        rec = self.get(j)
        if rec is None:
            return None
        if run_fn is None and rec["status"] in (ST_DONE, ST_CANCELLED, ST_KILLED):
            return rec
        with self._lock:
            event = self._cancel_events.get(j)
            if event is not None:
                event.clear()
        self.update(j, status=ST_QUEUED, result="", error="", ended=0.0)
        if run_fn is not None:
            self._run_async(j, run_fn,
                            payload if payload is not None else rec.get("payload"),
                            on_done, timeout)
        return self.get(j)

    def cancel(self, job_id: str, reason: str = "") -> bool:
        j = str(job_id or "").strip()
        rec = self.get(j)
        if rec is None:
            return False
        if rec["status"] == ST_CANCELLED:
            return True                      # 幂等：已经取消过
        if rec["status"] in TERMINAL:
            return False                     # 已结束的任务不覆盖成 cancelled
        with self._lock:
            event = self._cancel_events.get(j)
            if event is None:
                event = threading.Event()
                self._cancel_events[j] = event
            event.set()
        return self._transition(
            j, {ST_QUEUED, ST_RUNNING, ST_PAUSED},
            status=ST_CANCELLED, ended=_now(),
            error=str(reason or "")[:200])

    # ── 统一恢复 ─────────────────────────────────────────────
    def recover(self, *, reason: str = "") -> Dict[str, int]:
        """重启后的**统一收尸口**：把四套状态机的 recover 收口到一次调用。

        返回各子系统收掉/恢复的条数。子系统的对外方法签名一律不动，
        这里只做「统一调度」。找不到子系统时该项记 0，不影响其它项。
        """
        out = {"ticket": 0, "outbox": 0, "subagent": 0, "task_center": 0,
               "aigc": 0, "tool_proc": 0}
        why = str(reason or "").strip() or "进程重启，原执行者已不存在"
        try:
            from . import ticket
            out["ticket"] = int(ticket.recover_running_default(reason=why) or 0)
        except Exception:
            pass
        try:
            from . import outbox
            out["outbox"] = int(outbox.recover_default() or 0)
        except Exception:
            pass
        try:
            from . import subagent
            out["subagent"] = int(subagent.recover(reason=why) or 0)
        except Exception:
            pass
        # 统一收口：把四套状态机的落盘记录镜像进总表（全局唯一 job_id）。
        # aigc_task.py 不在可改清单，用只读扫盘；ticket/outbox 用各自读接口。
        try:
            mir = self.mirror_sources()
            out["mirrored_ticket"] = mir["ticket"]
            out["mirrored_outbox"] = mir["outbox"]
            out["aigc"] = mir["aigc"]
        except Exception:
            pass
        # 总表自身的 running（无主）→ paused：可 resume，绝不标 failed
        for rec in self.list(status=ST_RUNNING, limit=1000):
            if rec["source"] in ("generic", "subagent", "workflow", "aigc"):
                self.update(rec["job_id"], status=ST_PAUSED, error=why)
                out["task_center"] += 1
        return out

    def mirror_sources(self) -> Dict[str, int]:
        """把 ticket / outbox / aigc 三套落盘状态机**只读**地收口进总表。

        这样「四套状态机」（tool_proc 已由 `_persist_job` 主动登记，subagent 由
        `submit` 主动登记）最终都在**同一个 job_id 命名空间**里可查。
        幂等：已登记的不重复计。返回各来源本次新镜像的条数。
        """
        out = {"ticket": 0, "outbox": 0, "aigc": 0}
        _TICKET_ST = {
            "new": ST_QUEUED, "open": ST_QUEUED, "assigned": ST_QUEUED,
            "running": ST_RUNNING, "blocked": ST_BLOCKED, "done": ST_DONE,
            "failed": ST_FAILED, "cancelled": ST_CANCELLED,
        }
        try:
            from . import ticket
            for t in ticket.default().list(limit=10000):
                tid = str(t.get("id") or "")
                if not tid:
                    continue
                fields = {
                    "label": str(t.get("goal") or ""),
                    "status": _TICKET_ST.get(str(t.get("state") or ""), ST_QUEUED),
                    "meta": {"assignee": t.get("assignee") or "",
                             "role": t.get("role") or "",
                             "handback": bool(t.get("handback"))},
                    "created": float(t.get("created") or 0),
                    "updated": float(t.get("updated") or 0),
                    "attempts": int(t.get("attempts") or 0),
                }
                changed = _mirror_changed(self.get(tid), fields)
                self.register(
                    tid, kind="ticket", source="ticket", external_id=tid,
                    **fields)
                if changed:
                    out["ticket"] += 1
        except Exception:
            pass
        _OUTBOX_ST = {"pending": ST_QUEUED, "sending": ST_RUNNING,
                      "sent": ST_DONE, "dead": ST_FAILED}
        try:
            from . import outbox
            for j in outbox.default().list_all(limit=10000):
                jid = str(j.get("id") or "")
                if not jid:
                    continue
                fields = {
                    "kind": str(j.get("kind") or "outbox"),
                    "label": f"{j.get('kind')}→{j.get('target')}",
                    "status": _OUTBOX_ST.get(str(j.get("status") or ""), ST_QUEUED),
                    "payload": j.get("payload") or {},
                    "error": str(j.get("last_err") or ""),
                    "created": float(j.get("created") or 0),
                    "updated": float(j.get("next_at") or 0),
                    "attempts": int(j.get("attempts") or 0),
                }
                changed = _mirror_changed(self.get(jid), fields)
                self.register(
                    jid, source="outbox", external_id=jid, **fields)
                if changed:
                    out["outbox"] += 1
        except Exception:
            pass
        try:
            out["aigc"] = int(self.mirror_aigc() or 0)
        except Exception:
            pass
        return out

    def mirror_aigc(self, root: str = "") -> int:
        """把 aigc_task 落盘的任务元数据**只读**地收口进总表。

        aigc_task.py 不在本轮可改清单内，不能给它加 register 调用；好在它的
        持久化格式是公开约定（产物目录里的同名 .json），这里直接扫盘即可。
        返回本次新镜像的条数（幂等：已登记的不重复计）。
        """
        base = str(root or "").strip()
        if not base:
            try:
                from . import aigc_task
                base = aigc_task.get_service().root
            except Exception:
                base = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "media")
        if not os.path.isdir(base):
            return 0
        n = 0
        for kind in sorted(os.listdir(base)):
            kdir = os.path.join(base, kind)
            if not os.path.isdir(kdir):
                continue
            for day in sorted(os.listdir(kdir)):
                ddir = os.path.join(kdir, day)
                if not os.path.isdir(ddir):
                    continue
                for name in sorted(os.listdir(ddir)):
                    if not name.endswith(".json"):
                        continue
                    try:
                        with open(os.path.join(ddir, name), "r", encoding="utf-8") as f:
                            t = json.load(f)
                    except Exception:
                        continue
                    tid = str(t.get("id") or name[:-5])
                    st = str(t.get("status") or "")
                    status = (ST_DONE if st == "done" else
                              ST_FAILED if st in ("failed", "rejected") else
                              ST_RUNNING if st == "running" else ST_QUEUED)
                    fields = {
                        "kind": kind,
                        "label": str(t.get("id") or tid),
                        "status": status,
                        "payload": {"params": t.get("params") or {}},
                        "result": str(t.get("artifact") or ""),
                        "error": str(t.get("error") or ""),
                        "meta": {"aigc_status": st,
                                 "meta_path": os.path.join(ddir, name)},
                        "created": float(t.get("created_at") or _now()),
                        "started": float(t.get("started_at") or 0),
                        "ended": float(t.get("finished_at") or 0),
                    }
                    changed = _mirror_changed(self.get(tid), fields)
                    self.register(
                        tid, source="aigc", external_id=tid, **fields)
                    if changed:
                        n += 1
        return n


# ── 进程级单例 ─────────────────────────────────────────────────
_DEFAULT: Optional[TaskCenter] = None
_DEFAULT_LOCK = threading.Lock()


def default(path: str = "") -> TaskCenter:
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = TaskCenter(path)
        return _DEFAULT


def reset_default() -> None:
    """测试用：丢掉单例（不清库）。"""
    global _DEFAULT
    with _DEFAULT_LOCK:
        _DEFAULT = None


# ── 模块级便捷函数（全部转发到 default()）───────────────────────
def submit(**kw) -> str:
    return default().submit(**kw)


def register(job_id: str, **fields) -> dict:
    return default().register(job_id, **fields)


def get(job_id: str) -> Optional[dict]:
    return default().get(job_id)


def update(job_id: str, **fields) -> Optional[dict]:
    return default().update(job_id, **fields)


def wait(job_id: str, **kw) -> Optional[dict]:
    return default().wait(job_id, **kw)


def resume(job_id: str, **kw) -> Optional[dict]:
    return default().resume(job_id, **kw)


def cancel(job_id: str, reason: str = "") -> bool:
    return default().cancel(job_id, reason)


def list(**kw) -> List[dict]:
    """模块级 `list`（与要求的 submit/wait/resume/cancel/list/recover 对齐）。"""
    return default().list(**kw)


def list_jobs(**kw) -> List[dict]:
    """`list` 的别名（避免与内置 list 混淆时可用）。"""
    return default().list(**kw)


def stats() -> Dict[str, int]:
    return default().stats()


def recover(**kw) -> Dict[str, int]:
    return default().recover(**kw)


def mirror_aigc(root: str = "") -> int:
    return default().mirror_aigc(root)


__all__ = [
    "ST_QUEUED", "ST_RUNNING", "ST_PAUSED", "ST_DONE", "ST_FAILED",
    "ST_TIMEOUT", "ST_CANCELLED", "ST_KILLED", "ST_BLOCKED",
    "TERMINAL", "RECOVERABLE", "TaskCenter",
    "default", "reset_default", "submit", "register", "get", "update",
    "wait", "resume", "cancel", "list", "list_jobs", "stats", "recover",
    "mirror_aigc",
]
