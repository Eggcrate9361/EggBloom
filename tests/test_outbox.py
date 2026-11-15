# -*- coding: utf-8 -*-
"""Outbox v2 —— 出站落盘队列（1.6.0 A 方案第 1 条）。

## 为什么

`harness/outbound.py` 原先是纯内存队列：进程一挂，没发出去的消息**凭空消失**；
"发没发出去"只有内存计数，事后查不了；重发还可能让用户收到两条。

## 契约（本用例钉死）

1. `add()` 落库、`mark_sending/sent/failed` 写回执；
2. 同一会话里 `dedupe` 相同且未完成 → 返回同一个 id（不重复入队）；
3. 失败还有机会 → 回 `pending` 并排 `next_at`（退避）；超过 MAX_ATTEMPTS → `dead`；
4. `recover()` 把 `sending`（上次进程死在这）改回 `pending`；
5. **重启不丢**：关掉连接、重开同一个库，pending 还在；
6. `stats()` 按状态计数；`purge_done()` 只清老的已发/已死。

运行：python tests/test_outbox.py
"""
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import outbox  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


tmp = tempfile.mkdtemp()
db = os.path.join(tmp, "ob.sqlite3")
try:
    ob = outbox.Outbox(db)

    # ── 1. 基本写入 ────────────────────────────────────────────
    j1 = ob.add("p:10001", "text", "10001", {"text": "你好"}, dedupe="d1")
    check("add 返回 id", bool(j1), j1)
    check("刚入队是 pending", ob.due("p:10001")[0]["id"] == j1)
    check("别的会话看不到", ob.due("p:10002") == [])

    # ── 2. 幂等键 ──────────────────────────────────────────────
    j2 = ob.add("p:10001", "text", "10001", {"text": "你好"}, dedupe="d1")
    check("★ 同会话同 dedupe 不重复入队", j2 == j1, (j1, j2))
    j3 = ob.add("p:10002", "text", "10002", {"text": "你好"}, dedupe="d1")
    check("别的会话同 dedupe 不受影响", j3 != j1)

    # ── 3. 回执 ────────────────────────────────────────────────
    ob.mark_sending(j1)
    check("mark_sending 后不在 due 里", all(r["id"] != j1 for r in ob.due("p:10001")))
    ob.mark_sent(j1, {"retcode": 0})
    st = ob.stats()
    check("★ 发出去记成 sent", st.get("sent") == 1, st)

    # ── 4. 失败退避 / 死信 ────────────────────────────────────
    ob.mark_sending(j3)
    s1 = ob.mark_failed(j3, "网络炸了")
    check("第一次失败回 pending（等退避）", s1 == "pending", s1)
    check("退避期间 not due", all(r["id"] != j3 for r in ob.due("p:10002")))

    # 手动把 next_at 拨到现在，再连续失败到死信
    with ob._lock:
        ob._conn.execute("UPDATE jobs SET next_at=0 WHERE id=?", (j3,))
        ob._conn.commit()
    status = "pending"
    for _ in range(outbox.MAX_ATTEMPTS + 2):
        with ob._lock:
            ob._conn.execute("UPDATE jobs SET next_at=0 WHERE id=?", (j3,))
            ob._conn.commit()
        ob.mark_sending(j3)
        status = ob.mark_failed(j3, "还是炸")
        if status == "dead":
            break
    check("★ 重试到上限后变 dead", status == "dead", status)
    check("dead 不再被取出", all(r["id"] != j3 for r in ob.due("p:10002")))

    # ── 5. 重启恢复 ────────────────────────────────────────────
    j4 = ob.add("p:10003", "image", "10003", {"file": "a.gif"})
    ob.mark_sending(j4)                     # 模拟"发到一半进程死了"
    n = ob.recover()
    check("★ recover 把 sending 拉回 pending", n >= 1, n)
    check("恢复后能被取出", any(r["id"] == j4 for r in ob.due("p:10003")))

    j5 = ob.add("p:10004", "text", "10004", {"text": "重启前没发"})
    ob.close()

    ob2 = outbox.Outbox(db)                 # 模拟进程重启：重开同一个库
    ids = [r["id"] for r in ob2.due()]
    check("★ 重启后 pending 还在", j5 in ids, ids)
    check("重启后 id 不重复", len(ids) == len(set(ids)), len(ids))

    # ── 6. 清理 ────────────────────────────────────────────────
    with ob2._lock:
        ob2._conn.execute("UPDATE jobs SET updated=0 WHERE status IN ('sent','dead')")
        ob2._conn.commit()
    removed = ob2.purge_done(keep_seconds=3600)
    check("★ purge 只清老的已完", removed >= 1, removed)
    check("pending 不被 purge", any(r["id"] == j5 for r in ob2.due()))
    ob2.close()
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ── 7. outbound 接线（源码约束）─────────────────────────────────
src = open(os.path.join(ROOT, "harness", "outbound.py"), encoding="utf-8").read()
check("★ 提交时落盘", "_ob.add(" in src)
check("★ 成功/失败写回执", "mark_sent(" in src and "mark_failed(" in src)
check("★ worker 起来时恢复", "_recover_from_disk()" in src and "ob.recover()" in src)
check("stats 带落盘统计", 'out["db"]' in src)

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
