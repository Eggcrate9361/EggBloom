# -*- coding: utf-8 -*-
"""harness/outbox.py —— 出站任务的**落盘队列**（1.6.0 刀 5 第二版，A 方案第 1 条）。

## 为什么要有它

`harness/outbound.py` 是内存队列：进程一挂，没发出去的那几条就**凭空消失**，
而且"发没发出去"只有内存里的统计，事后查不了。

真机踩过：重启期间 bot 生成好的回复直接没了；协议层抖一下重发，用户收到两条。

## 语义（与 Super-agent 原型的 Outbox/EFFECTS 对齐）

- **按会话 FIFO**：同一个 `chat` 串行，不同会话互不堵；
- **至少一次 + 幂等键**：`dedupe_key` 相同且仍在队列里 → 不再追加；
- **回执**：每条的 `attempts` / `last_error` / `retcode` 落库，`stats()` 可查；
- **退避**：失败后 `next_attempt = now + min(60, 2**attempts)`；
- **重启恢复**：`recover()` 把 `sending`（上次进程死在这里）和到期的 `pending`
  重新排进队列 —— 宁可重发一次，也不能静默丢。

## 用法

    from harness import outbox
    ob = outbox.Outbox()          # 默认 data/outbox.sqlite3
    jid = ob.add(chat, kind, target, payload, is_group=True)
    ob.due(chat) / ob.mark_sent(jid, resp) / ob.mark_failed(jid, err)
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

#: 默认路径：<app>/data/outbox.sqlite3（bot 自己目录，不写用户文档区）
#: 默认路径：<app>/data/outbox.sqlite3；环境变量可覆盖（测试必须隔离，
#: 否则一个用例落盘的待发任务会被下一个用例"恢复"出来重发）。
_DEFAULT = os.environ.get("EGGBLOOM_OUTBOX_DB") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "outbox.sqlite3",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id        TEXT PRIMARY KEY,
    seq       INTEGER NOT NULL,
    chat      TEXT NOT NULL,
    kind      TEXT NOT NULL,
    target    TEXT NOT NULL,
    is_group  INTEGER NOT NULL DEFAULT 0,
    payload   TEXT NOT NULL,
    dedupe    TEXT NOT NULL DEFAULT '',
    created   REAL NOT NULL,
    attempts  INTEGER NOT NULL DEFAULT 0,
    next_at   REAL NOT NULL DEFAULT 0,
    status    TEXT NOT NULL DEFAULT 'pending',
    retcode   INTEGER,
    last_err  TEXT NOT NULL DEFAULT '',
    updated   REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_jobs_chat ON jobs(chat, status, seq);
CREATE INDEX IF NOT EXISTS idx_jobs_dedupe ON jobs(chat, dedupe, status);
"""

MAX_ATTEMPTS = 5


class Outbox:
    def __init__(self, path: str = ""):
        self.path = str(path or _DEFAULT)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        self._seq = self._max_seq()

    # ── 基础 ───────────────────────────────────────────────────
    def _max_seq(self) -> int:
        try:
            row = self._conn.execute("SELECT COALESCE(MAX(seq), 0) FROM jobs").fetchone()
            return int(row[0] or 0)
        except Exception:
            return 0

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    # ── 写 ─────────────────────────────────────────────────────
    def add(self, chat: str, kind: str, target: str, payload: dict,
            *, is_group: bool = False, dedupe: str = "") -> str:
        """入队一条；同一会话里 dedupe 相同且未完成的，直接返回已有 id。"""
        chat = str(chat or target or "")
        with self._lock:
            if dedupe:
                row = self._conn.execute(
                    "SELECT id FROM jobs WHERE chat=? AND dedupe=? "
                    "AND status IN ('pending','sending') LIMIT 1",
                    (chat, dedupe),
                ).fetchone()
                if row:
                    return str(row[0])
            self._seq += 1
            jid = uuid.uuid4().hex[:16]
            now = time.time()
            self._conn.execute(
                "INSERT INTO jobs(id, seq, chat, kind, target, is_group, payload, dedupe,"
                " created, attempts, next_at, status, updated)"
                " VALUES(?,?,?,?,?,?,?,?,?,0,?, 'pending', ?)",
                (jid, self._seq, chat, str(kind or "text"), str(target or ""),
                 1 if is_group else 0,
                 json.dumps(payload or {}, ensure_ascii=False),
                 str(dedupe or ""), now, now, now),
            )
            self._conn.commit()
            return jid

    def mark_sending(self, jid: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status='sending', attempts=attempts+1, updated=? WHERE id=?",
                (time.time(), str(jid)),
            )
            self._conn.commit()

    def mark_sent(self, jid: str, resp: Optional[dict] = None) -> None:
        rc = None
        try:
            rc = int((resp or {}).get("retcode", 0))
        except Exception:
            rc = None
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status='sent', retcode=?, last_err='', updated=? WHERE id=?",
                (rc, time.time(), str(jid)),
            )
            self._conn.commit()

    def mark_failed(self, jid: str, err: str = "", resp: Optional[dict] = None,
                    *, backoff: bool = True) -> str:
        """失败落库。还有重试机会 → 回 pending 并排下次时间；否则 dead。"""
        rc = None
        try:
            rc = int((resp or {}).get("retcode", -1))
        except Exception:
            rc = -1
        with self._lock:
            row = self._conn.execute(
                "SELECT attempts FROM jobs WHERE id=?", (str(jid),)).fetchone()
            attempts = int(row[0] or 0) if row else 0
            if backoff and attempts < MAX_ATTEMPTS:
                delay = min(60.0, float(2 ** max(1, attempts)))
                self._conn.execute(
                    "UPDATE jobs SET status='pending', next_at=?, retcode=?, last_err=?,"
                    " updated=? WHERE id=?",
                    (time.time() + delay, rc, str(err or "")[:200], time.time(), str(jid)),
                )
                status = "pending"
            else:
                self._conn.execute(
                    "UPDATE jobs SET status='dead', retcode=?, last_err=?, updated=?"
                    " WHERE id=?",
                    (rc, str(err or "")[:200], time.time(), str(jid)),
                )
                status = "dead"
            self._conn.commit()
            return status

    # ── 读 ─────────────────────────────────────────────────────
    def due(self, chat: Optional[str] = None, *, limit: int = 50) -> List[Dict[str, Any]]:
        """到点的 pending 任务（按 chat+seq 有序）。chat=None 表示全都要。"""
        now = time.time()
        sql = ("SELECT * FROM jobs WHERE status='pending' AND next_at<=?"
               + (" AND chat=?" if chat else "") + " ORDER BY seq LIMIT ?")
        args: tuple = (now, str(chat), int(limit)) if chat else (now, int(limit))
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [self._row(r) for r in rows]

    def chats_with_pending(self) -> List[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT chat FROM jobs WHERE status='pending' ORDER BY chat"
            ).fetchall()
        return [str(r[0]) for r in rows]

    def recover(self) -> int:
        """重启恢复：sending（上次死在这）→ pending；返回恢复条数。"""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE jobs SET status='pending', next_at=? WHERE status='sending'",
                (time.time(),),
            )
            self._conn.commit()
            return int(cur.rowcount or 0)

    def stats(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        with self._lock:
            for row in self._conn.execute(
                    "SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall():
                out[str(row[0])] = int(row[1])
        out["total"] = sum(v for k, v in out.items())
        return out

    def purge_done(self, keep_seconds: float = 86400 * 3) -> int:
        """清掉太老的已发/已死记录（默认留 3 天，够查回执）。"""
        cutoff = time.time() - float(keep_seconds or 0)
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM jobs WHERE status IN ('sent','dead') AND updated<?",
                (cutoff,),
            )
            self._conn.commit()
            return int(cur.rowcount or 0)

    @staticmethod
    def _row(r) -> Dict[str, Any]:
        try:
            payload = json.loads(r["payload"] or "{}")
        except Exception:
            payload = {}
        return {
            "id": str(r["id"]), "seq": int(r["seq"]), "chat": str(r["chat"]),
            "kind": str(r["kind"]), "target": str(r["target"]),
            "is_group": bool(r["is_group"]), "payload": payload,
            "created": float(r["created"]), "attempts": int(r["attempts"]),
            "next_at": float(r["next_at"]), "status": str(r["status"]),
            "retcode": r["retcode"], "last_err": str(r["last_err"] or ""),
        }


# ── 单例（bot 侧直接用，测试可传自己的 path）─────────────────────
_DEFAULT_OB: Optional[Outbox] = None
_OB_LOCK = threading.Lock()


def default(path: str = "") -> Outbox:
    global _DEFAULT_OB
    with _OB_LOCK:
        if _DEFAULT_OB is None:
            _DEFAULT_OB = Outbox(path or os.environ.get("EGGBLOOM_OUTBOX_DB") or _DEFAULT)
        return _DEFAULT_OB


def reset_default() -> None:
    """测试用：丢掉单例（并关闭连接）。"""
    global _DEFAULT_OB
    with _OB_LOCK:
        if _DEFAULT_OB is not None:
            _DEFAULT_OB.close()
        _DEFAULT_OB = None


__all__ = ["Outbox", "default", "reset_default", "MAX_ATTEMPTS", "_DEFAULT"]
