# -*- coding: utf-8 -*-
"""harness/ticket.py —— 工单：Ticket / Assignment / Report（2026-11-03）

执行总表 §7.2 第 3 条：把 `deep_task` 这类后台任务落成**工单** ——
落盘、重启可续、卡住转人工（blocked → handback）、结果有回执（Report），
并与 Outbox 的 job 语义对齐。

## 为什么不在 subagent 上打补丁

`harness/subagent.py` 现在是"跑完就忘"：状态只在内存 `OrderedDict` 里，
`data/subagent_tasks.jsonl` 是**只写不读**的流水，重启就把 running 标成 failed ——
它自己注释里写明"不做跨进程恢复"。那套东西服务的是"派个活，几分钟后报一句"，
够用；工单要的是另一件事：**这条任务现在归谁、卡在哪、谁该接手、干完了交什么**。

所以这里是一张独立的表，`subagent` 的记录可以**镜像**进来（`mirror_subagent`），
两条路径共用一份工单账，而不是把 subagent 改造成半工单。

## 状态机

    new ──open──> open ──assign──> assigned ──start──> running ──finish──> done
                                     ↑                    │  │
                                     │                    │  └──fail──> failed ──reopen──> open
                                     └────resume──────── blocked <──block──┘
                                                          （handback=转人工）

- `blocked` 是**唯一**的"卡住"状态，带 `blocked_reason` 与 `handback`；
  重启时把 `running` 全部收成 `blocked(handback=True)` —— 进程没了，
  原来的执行者就不存在了，硬说"还在跑"是骗人；
- `done/failed/cancelled` 是终态；`failed` 可以由人重开（`reopen`），
  因为"失败"常常是环境问题而不是任务本身不可能。

## 纪律

- **不 import 主程序**、不起线程、不联网 —— 纯数据层，能离线单测；
- 落盘用 SQLite（与 `harness/outbox.py` 同一套做法），
  路径可用 `EGGBLOOM_TICKET_DB` 覆盖（用例之间必须隔离，否则互相看见对方的工单）；
- 所有状态变更都过 `can_transition()`，非法流转直接拒绝并说明原因 ——
  不静默改状态，否则"它说干完了"和"账上写着干完了"又变成两回事。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

#: 工单状态
ST_NEW = "new"
ST_OPEN = "open"
ST_ASSIGNED = "assigned"
ST_RUNNING = "running"
ST_BLOCKED = "blocked"
ST_DONE = "done"
ST_FAILED = "failed"
ST_CANCELLED = "cancelled"

STATES = (ST_NEW, ST_OPEN, ST_ASSIGNED, ST_RUNNING, ST_BLOCKED, ST_DONE, ST_FAILED, ST_CANCELLED)

#: 终态（不再接受任何流转，`reopen` 除外）
TERMINAL = (ST_DONE, ST_CANCELLED)

#: 允许的流转。**唯一来源** —— 别在别处再写一份 if。
#: 宽松到处都有出口，只守住两条真规矩：`done/cancelled` 是终态、`failed` 可由人重开。
TRANSITIONS: Dict[str, tuple] = {
    ST_NEW: (ST_OPEN, ST_ASSIGNED, ST_CANCELLED),
    ST_OPEN: (ST_ASSIGNED, ST_RUNNING, ST_DONE, ST_FAILED, ST_CANCELLED),
    ST_ASSIGNED: (ST_RUNNING, ST_DONE, ST_FAILED, ST_BLOCKED, ST_CANCELLED),
    ST_RUNNING: (ST_DONE, ST_FAILED, ST_BLOCKED, ST_CANCELLED),
    ST_BLOCKED: (ST_ASSIGNED, ST_RUNNING, ST_DONE, ST_FAILED, ST_CANCELLED),
    ST_DONE: (),
    ST_FAILED: (ST_OPEN, ST_ASSIGNED),
    ST_CANCELLED: (),
}

#: 回执种类
REPORT_PROGRESS = "progress"
REPORT_FINAL = "final"
REPORT_BLOCKED = "blocked"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id             TEXT PRIMARY KEY,
    goal           TEXT NOT NULL DEFAULT '',
    state          TEXT NOT NULL DEFAULT 'new',
    assignee       TEXT NOT NULL DEFAULT '',
    role           TEXT NOT NULL DEFAULT '',
    attempts       INTEGER NOT NULL DEFAULT 0,
    created        REAL NOT NULL DEFAULT 0,
    updated        REAL NOT NULL DEFAULT 0,
    blocked_reason TEXT NOT NULL DEFAULT '',
    handback       INTEGER NOT NULL DEFAULT 0,
    trace          TEXT NOT NULL DEFAULT '',
    meta           TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS ticket_reports (
    seq       INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket    TEXT NOT NULL,
    kind      TEXT NOT NULL DEFAULT 'progress',
    summary   TEXT NOT NULL DEFAULT '',
    artifacts TEXT NOT NULL DEFAULT '[]',
    cost      REAL NOT NULL DEFAULT 0,
    ts        REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ticket_reports ON ticket_reports(ticket, seq);
CREATE INDEX IF NOT EXISTS idx_tickets_state ON tickets(state, updated);
"""

_COLS = ("id", "goal", "state", "assignee", "role", "attempts",
         "created", "updated", "blocked_reason", "handback", "trace", "meta")


def _now() -> float:
    return time.time()


def can_transition(src: str, dst: str) -> tuple:
    """`(能不能走, 为什么不能)`。非法流转要给出人能看懂的原因。"""
    s = str(src or "").strip()
    d = str(dst or "").strip()
    if s not in TRANSITIONS:
        return False, f"源状态不认识：{s or '(空)'}"
    if d not in STATES:
        return False, f"目标状态不认识：{d or '(空)'}"
    if d in TRANSITIONS[s]:
        return True, ""
    return False, f"{s} → {d} 不是合法流转（允许：{', '.join(TRANSITIONS[s]) or '无，终态'}）"


def _row(r) -> Dict[str, Any]:
    d = {k: r[k] for k in _COLS}
    d["handback"] = bool(d.get("handback"))
    try:
        d["meta"] = json.loads(d.get("meta") or "{}")
    except Exception:
        d["meta"] = {}
    d["reports"] = []
    return d


class TicketStore:
    """工单库。一个实例一条连接串（SQLite 文件路径）。"""

    def __init__(self, path: str = ""):
        self.path = str(path or os.environ.get("EGGBLOOM_TICKET_DB") or "").strip()
        if not self.path:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.path = os.path.join(root, "data", "tickets.sqlite3")
        self._lock = threading.RLock()
        self._init()

    # ── 基础设施 ────────────────────────────────────
    def _conn(self):
        d = os.path.dirname(os.path.abspath(self.path))
        if d:
            os.makedirs(d, exist_ok=True)
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def _init(self) -> None:
        with self._lock:
            c = self._conn()
            try:
                c.executescript(_SCHEMA)
                c.commit()
            finally:
                c.close()

    # ── 读 ─────────────────────────────────────────
    def get(self, tid: str, *, with_reports: bool = True) -> Optional[dict]:
        """取一张工单；`with_reports=False` 时只取主表（列表用，省一次查询）。"""
        t = str(tid or "").strip()
        if not t:
            return None
        with self._lock:
            c = self._conn()
            try:
                r = c.execute("SELECT * FROM tickets WHERE id = ?", (t,)).fetchone()
                if not r:
                    return None
                out = _row(r)
                if with_reports:
                    out["reports"] = self._reports(c, t)
                return out
            finally:
                c.close()

    @staticmethod
    def _reports(c, tid: str) -> List[dict]:
        rows = c.execute(
            "SELECT seq, kind, summary, artifacts, cost, ts FROM ticket_reports "
            "WHERE ticket = ? ORDER BY seq", (tid,)).fetchall()
        out = []
        for r in rows:
            try:
                arts = json.loads(r["artifacts"] or "[]")
            except Exception:
                arts = []
            out.append({"seq": r["seq"], "kind": r["kind"], "summary": r["summary"],
                        "artifacts": arts, "cost": r["cost"], "ts": r["ts"]})
        return out

    def list(self, *, state: str = "", limit: int = 20) -> List[dict]:
        """最近的在前；`state` 可传单个状态。"""
        sql = "SELECT * FROM tickets"
        args: tuple = ()
        if state:
            sql += " WHERE state = ?"
            args = (str(state),)
        sql += " ORDER BY updated DESC LIMIT ?"
        with self._lock:
            c = self._conn()
            try:
                return [_row(r) for r in c.execute(sql, args + (max(1, int(limit)),))]
            finally:
                c.close()

    def stats(self) -> Dict[str, int]:
        with self._lock:
            c = self._conn()
            try:
                rows = c.execute("SELECT state, COUNT(*) n FROM tickets GROUP BY state").fetchall()
                return {r["state"]: int(r["n"]) for r in rows}
            finally:
                c.close()

    # ── 写 ─────────────────────────────────────────
    def _patch(self, tid: str, **fields) -> bool:
        if not fields:
            return False
        cols = ", ".join(f"{k} = ?" for k in fields)
        args = list(fields.values()) + [str(tid)]
        with self._lock:
            c = self._conn()
            try:
                cur = c.execute(f"UPDATE tickets SET {cols} WHERE id = ?", args)
                c.commit()
                return cur.rowcount > 0
            finally:
                c.close()

    def _move(self, tid: str, dst: str, **fields) -> dict:
        """过状态机再改；非法流转**返回错误而不是抛**（工具层不该把异常抛给模型）。"""
        t = str(tid or "").strip()
        if not t:
            return {"ok": False, "error": "工单号为空"}
        cur = self.get(t, with_reports=False)
        if not cur:
            return {"ok": False, "error": f"没有这张工单：{t}"}
        ok, why = can_transition(cur["state"], dst)
        if not ok:
            return {"ok": False, "error": why, "from": cur["state"], "to": dst}
        fields.setdefault("updated", _now())
        self._patch(t, state=dst, **fields)
        return {"ok": True, "ticket": self.get(t, with_reports=False),
                "from": cur["state"], "to": dst}

    def open(self, goal: str, *, assignee: str = "", role: str = "",
             trace: str = "", meta: Optional[dict] = None,
             state: str = ST_OPEN, tid: str = "") -> dict:
        """开一张工单。默认直接进 `open`（等派活）；`new` 留给"先登记后细化"。

        `tid` 是给镜像用的（`mirror_subagent` 要沿用 subagent 的 task_id），
        正常开单留空 —— 单号由库里生成，别让调用方自己编。
        """
        g = str(goal or "").strip()
        if not g:
            return {"ok": False, "error": "goal 不能为空"}
        if state not in (ST_NEW, ST_OPEN):
            return {"ok": False, "error": f"开单只能是 new / open，收到 {state}"}
        tid = str(tid or "").strip() or ("tk" + uuid.uuid4().hex[:10])
        if self.get(tid, with_reports=False):
            return {"ok": False, "error": f"工单号已存在：{tid}"}
        now = _now()
        rec = {
            "id": tid, "goal": g[:500], "state": state,
            "assignee": str(assignee or ""), "role": str(role or ""),
            "attempts": 1 if assignee else 0,
            "created": now, "updated": now,
            "blocked_reason": "", "handback": 0, "trace": str(trace or ""),
            "meta": json.dumps(meta or {}, ensure_ascii=False),
        }
        with self._lock:
            c = self._conn()
            try:
                c.execute(
                    f"INSERT INTO tickets ({', '.join(_COLS)}) "
                    f"VALUES ({', '.join('?' * len(_COLS))})",
                    tuple(rec[k] for k in _COLS))
                c.commit()
            finally:
                c.close()
        return {"ok": True, "ticket": self.get(tid, with_reports=False)}

    def assign(self, tid: str, assignee: str, *, role: str = "") -> dict:
        """派给谁。重复派给同一个人也算一次尝试（`attempts + 1`）。"""
        a = str(assignee or "").strip()
        if not a:
            return {"ok": False, "error": "assignee 不能为空"}
        cur = self.get(tid, with_reports=False)
        if not cur:
            return {"ok": False, "error": f"没有这张工单：{tid}"}
        fields = {"assignee": a,
                  "role": str(role or cur.get("role") or ""),
                  "attempts": int(cur.get("attempts") or 0) + 1}
        if cur["state"] == ST_ASSIGNED:
            # 已在 assigned：只改派那个人，不动状态机（assigned → assigned 不是流转）
            fields["updated"] = _now()
            self._patch(cur["id"], **fields)
            return {"ok": True, "ticket": self.get(cur["id"], with_reports=False)}
        return self._move(cur["id"], ST_ASSIGNED, **fields)

    def start(self, tid: str) -> dict:
        return self._move(tid, ST_RUNNING)

    def block(self, tid: str, reason: str, *, handback: bool = True) -> dict:
        """卡住。`handback=True` = 转人工（默认）——卡住却没人接手，等于静默失败。"""
        r = str(reason or "").strip() or "未说明原因"
        res = self._move(tid, ST_BLOCKED, blocked_reason=r[:300], handback=1 if handback else 0)
        if res.get("ok"):
            self.report(tid, f"卡住：{r}", kind=REPORT_BLOCKED)
        return res

    def resume(self, tid: str, *, assignee: str = "", role: str = "") -> dict:
        """解开 blocked：有人接手了。不给 assignee 就沿用原来的。"""
        cur = self.get(tid, with_reports=False)
        if not cur:
            return {"ok": False, "error": f"没有这张工单：{tid}"}
        if cur["state"] != ST_BLOCKED:
            return {"ok": False, "error": f"只有 blocked 能 resume，现在是 {cur['state']}"}
        a = str(assignee or cur.get("assignee") or "").strip()
        fields = {"blocked_reason": "", "handback": 0}
        if a:
            fields["assignee"] = a
            fields["attempts"] = int(cur.get("attempts") or 0) + 1
        if role:
            fields["role"] = str(role)
        return self._move(tid, ST_ASSIGNED, **fields)

    def report(self, tid: str, summary: str, *, kind: str = REPORT_PROGRESS,
               artifacts: Optional[list] = None, cost: float = 0.0) -> dict:
        """加一条回执。回执只追加，不改工单状态。"""
        t = str(tid or "").strip()
        if not t:
            return {"ok": False, "error": "工单号为空"}
        if not self.get(t, with_reports=False):
            return {"ok": False, "error": f"没有这张工单：{t}"}
        k = kind if kind in (REPORT_PROGRESS, REPORT_FINAL, REPORT_BLOCKED) else REPORT_PROGRESS
        with self._lock:
            c = self._conn()
            try:
                c.execute(
                    "INSERT INTO ticket_reports (ticket, kind, summary, artifacts, cost, ts) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (t, k, str(summary or "")[:4000],
                     json.dumps(list(artifacts or []), ensure_ascii=False),
                     float(cost or 0), _now()))
                c.commit()
            finally:
                c.close()
        self._patch(t, updated=_now())
        return {"ok": True, "reports": len(self._reports_of(t))}

    def _reports_of(self, tid: str) -> list:
        with self._lock:
            c = self._conn()
            try:
                return self._reports(c, tid)
            finally:
                c.close()

    def finish(self, tid: str, *, summary: str = "", ok: bool = True,
               artifacts: Optional[list] = None, cost: float = 0.0) -> dict:
        """收工：写一条 final 回执，再进终态。"""
        res = self._move(tid, ST_DONE if ok else ST_FAILED)
        if not res.get("ok"):
            return res
        if summary or artifacts:
            self.report(tid, summary or ("完成" if ok else "失败"),
                        kind=REPORT_FINAL, artifacts=artifacts, cost=cost)
        return {"ok": True, "ticket": self.get(tid)}

    def reopen(self, tid: str) -> dict:
        """把 failed 重开成 open（人工决定再试一次）。"""
        return self._move(tid, ST_OPEN)

    def cancel(self, tid: str, reason: str = "") -> dict:
        res = self._move(tid, ST_CANCELLED)
        if res.get("ok") and reason:
            self.report(tid, f"取消：{reason}")
        return res

    def recover_running(self, *, reason: str = "") -> int:
        """重启后的收尸：`running` 一律转 `blocked(handback=True)`。

        进程都没了还说"在跑"是骗人；转 blocked 才能出现在"该谁接手"的队列里。
        返回被收掉的张数。
        """
        why = str(reason or "").strip() or "进程重启，原执行者已不存在"
        n = 0
        for t in self.list(state=ST_RUNNING, limit=1000):
            if self.block(t["id"], why).get("ok"):
                n += 1
        return n


# ── 默认实例 ──────────────────────────────────────────────

_DEFAULT: Optional[TicketStore] = None
_DEFAULT_LOCK = threading.Lock()


def default(path: str = "") -> TicketStore:
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = TicketStore(path)
        return _DEFAULT


def reset_default() -> None:
    """测试用：丢掉单例（不清库）。"""
    global _DEFAULT
    with _DEFAULT_LOCK:
        _DEFAULT = None


# ── 与 subagent 对齐 ──────────────────────────────────────

#: subagent 状态 → 工单状态
_SUB_MAP = {
    "queued": ST_ASSIGNED,
    "running": ST_RUNNING,
    "done": ST_DONE,
    "failed": ST_FAILED,
    "timeout": ST_FAILED,
    "cancelled": ST_CANCELLED,
}


def mirror_subagent(rec: dict, store: Optional[TicketStore] = None) -> dict:
    """把一条 `subagent` 记录镜像成工单（幂等：同一 task_id 只建一次）。

    **不接线**也能用：宿主拿到 `subagent.submit()` 的记录后调一次即可。
    这样 `subagent` 保持"跑完就忘"的轻量语义，工单账另存一份可续的。
    """
    r = rec if isinstance(rec, dict) else {}
    tid = str(r.get("task_id") or "").strip()
    if not tid:
        return {"ok": False, "error": "记录里没有 task_id"}
    st = store or default()
    want = _SUB_MAP.get(str(r.get("state") or ""), ST_ASSIGNED)
    cur = st.get(tid, with_reports=False)
    if not cur:
        opened = st.open(str(r.get("goal") or ""), trace=str((r.get("meta") or {}).get("trace") or ""),
                         meta={"from": "subagent", "task_id": tid},
                         state=ST_OPEN, tid=tid)
        if not opened.get("ok"):
            return opened
        st.assign(tid, str((r.get("meta") or {}).get("assignee") or "subagent"))
        cur = st.get(tid, with_reports=False)
    if cur and cur["state"] != want:
        if want == ST_RUNNING:
            st.start(tid)
        elif want in (ST_DONE, ST_FAILED):
            st.finish(tid, summary=str(r.get("result") or ""), ok=(want == ST_DONE),
                      artifacts=[], cost=0.0)
        elif want == ST_CANCELLED:
            st.cancel(tid, str(r.get("error") or ""))
        elif want == ST_ASSIGNED and cur["state"] in (ST_NEW, ST_OPEN):
            st.assign(tid, "subagent")
        else:
            st.block(tid, f"subagent 状态 {r.get('state')} 无法映射")
    return {"ok": True, "ticket": st.get(tid)}


__all__ = [
    "ST_NEW", "ST_OPEN", "ST_ASSIGNED", "ST_RUNNING", "ST_BLOCKED",
    "ST_DONE", "ST_FAILED", "ST_CANCELLED", "STATES", "TERMINAL", "TRANSITIONS",
    "REPORT_PROGRESS", "REPORT_FINAL", "REPORT_BLOCKED",
    "can_transition", "TicketStore", "default", "reset_default", "mirror_subagent",
]
