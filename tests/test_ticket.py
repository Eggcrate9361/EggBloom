# -*- coding: utf-8 -*-
"""tests/test_ticket.py —— 工单：Ticket / Assignment / Report（2026-11-03）

执行总表 §7.2 第 3 条的要求逐条对应用例：

  A. 状态机：合法流转放行、非法流转**拒绝并说明原因**（不许静默改状态）
  B. 开单与派活：goal 必填；重复派活记一次尝试
  C. 卡住转人工：block 带原因 + handback；resume 接手后清干净
  D. 回执：只追加不改状态；收工时落一条 final
  E. 落盘：**换一个实例读同一个文件**仍是 running（= 重启可续的前提）
  F. 重启收尸：recover_running 把 running 全转 blocked(handback)，并留一条回执
  G. 与 subagent 对齐：mirror_subagent 幂等、状态映射对得上
  H. 隔离：EGGBLOOM_TICKET_DB 决定落盘位置（用例之间不许互相看见对方的工单）

F 是本轮的关键：`harness/subagent.py` 现在是"重启就把 running 标成 failed"，
工单要的恰恰相反 —— 进程没了不等于任务没了，得进"该谁接手"的队列。
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import ticket as tk  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def _tmpdb(tag):
    d = tempfile.mkdtemp(prefix=f"eggticket_{tag}_")
    return os.path.join(d, "tickets.sqlite3")


def main():
    print("[A] 状态机：合法流转放行，非法流转拒绝并说明原因")
    check("new → open", tk.can_transition("new", "open")[0] is True)
    check("new → assigned", tk.can_transition("new", "assigned")[0] is True)
    check("assigned → running", tk.can_transition("assigned", "running")[0] is True)
    check("running → blocked", tk.can_transition("running", "blocked")[0] is True)
    check("blocked → assigned（接手）", tk.can_transition("blocked", "assigned")[0] is True)
    check("failed → open（重开）", tk.can_transition("failed", "open")[0] is True)
    ok, why = tk.can_transition("done", "running")
    check("★ done 是终态，不许倒回去", ok is False and "终态" in why, why)
    ok2, why2 = tk.can_transition("cancelled", "open")
    check("★ cancelled 也是终态", ok2 is False and "终态" in why2, why2)
    check("不认识的状态要报出来", tk.can_transition("什么玩意", "open")[0] is False)

    db = _tmpdb("basic")
    s = tk.TicketStore(db)

    print("\n[B] 开单与派活")
    check("goal 必填", s.open("   ").get("ok") is False)
    r = s.open("把下载目录按类型归档")
    check("开单成功", r.get("ok") is True, r)
    tid = r["ticket"]["id"]
    check("默认落在 open", r["ticket"]["state"] == tk.ST_OPEN, r["ticket"]["state"])
    check("没人接时 attempts=0", r["ticket"]["attempts"] == 0, r["ticket"]["attempts"])
    t = s.assign(tid, "subagent")
    check("派活 → assigned", t.get("ok") and t["ticket"]["state"] == tk.ST_ASSIGNED, t)
    check("派活记一次尝试", t["ticket"]["attempts"] == 1, t["ticket"]["attempts"])
    t2 = s.assign(tid, "worker-b")
    check("改派也算一次尝试", t2["ticket"]["attempts"] == 2, t2["ticket"]["attempts"])
    check("改派换了人", t2["ticket"]["assignee"] == "worker-b", t2["ticket"]["assignee"])
    check("空 assignee 被拒", s.assign(tid, " ").get("ok") is False)
    check("不存在的工单被拒", s.assign("tk不存在", "x").get("ok") is False)

    print("\n[C] 卡住转人工 + 接手")
    s.start(tid)
    cur = s.get(tid)
    check("启动 → running", cur["state"] == tk.ST_RUNNING, cur["state"])
    b = s.block(tid, "缺 API key，装不了依赖")
    check("卡住 → blocked", b.get("ok") and b["ticket"]["state"] == tk.ST_BLOCKED, b)
    check("★ 默认 handback=转人工", b["ticket"]["handback"] is True, b["ticket"]["handback"])
    check("原因留在单上", "API key" in b["ticket"]["blocked_reason"], b["ticket"]["blocked_reason"])
    rep = s.get(tid)["reports"]
    check("卡住自动留一条回执", any(x["kind"] == tk.REPORT_BLOCKED for x in rep), rep)
    bad = s.resume(tid + "x")
    check("resume 不存在的单被拒", bad.get("ok") is False)
    rr = s.resume(tid, assignee="人-老大")
    check("接手 → assigned", rr.get("ok") and rr["ticket"]["state"] == tk.ST_ASSIGNED, rr)
    check("接手后 handback 清掉", rr["ticket"]["handback"] is False)
    check("接手后原因清掉", rr["ticket"]["blocked_reason"] == "")
    check("接手算新的一次尝试", rr["ticket"]["attempts"] == 3, rr["ticket"]["attempts"])
    check("非 blocked 不能 resume", s.resume(tid).get("ok") is False)

    print("\n[D] 回执：只追加，不改状态")
    before = s.get(tid)["state"]
    s.report(tid, "已下载 3 个文件")
    s.report(tid, "还剩 2 个")
    after = s.get(tid)
    check("回执不改状态", after["state"] == before, (before, after["state"]))
    check("回执累计（卡住 1 + 进度 2）", len(after["reports"]) == 3, len(after["reports"]))
    fin = s.finish(tid, summary="归档完成，共 12 个文件", artifacts=["data/a.txt"], cost=0.02)
    check("收工 → done", fin.get("ok") and fin["ticket"]["state"] == tk.ST_DONE, fin)
    last = s.get(tid)["reports"][-1]
    check("最后一条是 final", last["kind"] == tk.REPORT_FINAL, last["kind"])
    check("final 带产物", last["artifacts"] == ["data/a.txt"], last["artifacts"])
    check("final 带成本", abs(last["cost"] - 0.02) < 1e-9, last["cost"])
    check("★ done 之后不能再启动", s.start(tid).get("ok") is False)

    print("\n[E] 落盘：换个实例读同一文件")
    db2 = _tmpdb("persist")
    a = tk.TicketStore(db2)
    r2 = a.open("长任务：下载 1.7G 模型")
    tid2 = r2["ticket"]["id"]
    a.assign(tid2, "subagent")
    a.start(tid2)
    a.report(tid2, "下载到 60%")
    b2 = tk.TicketStore(db2)                 # ← 模拟重启：新实例、同一个库
    got = b2.get(tid2)
    check("★ 重启后工单还在", got is not None and got["id"] == tid2)
    check("★ 重启后仍是 running", got["state"] == tk.ST_RUNNING, got["state"])
    check("重启后回执还在", any("60%" in x["summary"] for x in got["reports"]), got["reports"])

    print("\n[F] 重启收尸：running → blocked(handback)")
    db3 = _tmpdb("recover")
    c = tk.TicketStore(db3)
    ids = []
    for i in range(2):
        rr2 = c.open(f"任务{i}")
        c.assign(rr2["ticket"]["id"], "subagent")
        c.start(rr2["ticket"]["id"])
        ids.append(rr2["ticket"]["id"])
    done = c.open("已经完成的")
    c.assign(done["ticket"]["id"], "subagent")
    c.start(done["ticket"]["id"])
    c.finish(done["ticket"]["id"], summary="ok")

    n = c.recover_running()
    check("收掉 2 张", n == 2, n)
    for i in ids:
        t = c.get(i)
        check(f"{i} 转 blocked", t["state"] == tk.ST_BLOCKED, t["state"])
        check(f"{i} 标了转人工", t["handback"] is True)
        check(f"{i} 原因写明重启", "重启" in t["blocked_reason"], t["blocked_reason"])
    check("★ 已完成的不会被收", c.get(done["ticket"]["id"])["state"] == tk.ST_DONE)
    check("再跑一次收尸不再动", c.recover_running() == 0)
    check("stats 数得清", c.stats().get(tk.ST_BLOCKED) == 2, c.stats())

    print("\n[G] 与 subagent 对齐")
    db4 = _tmpdb("mirror")
    m = tk.TicketStore(db4)
    rec = {"task_id": "tABC123", "goal": "查天气", "state": "queued",
           "result": "", "error": "", "meta": {"trace": "tr-1"}}
    g1 = tk.mirror_subagent(rec, store=m)
    check("镜像建单成功", g1.get("ok") is True, g1)
    check("★ 沿用 subagent 的 task_id", g1["ticket"]["id"] == "tABC123", g1["ticket"]["id"])
    check("queued → assigned", g1["ticket"]["state"] == tk.ST_ASSIGNED, g1["ticket"]["state"])
    check("trace 带过来", g1["ticket"]["trace"] == "tr-1", g1["ticket"]["trace"])

    rec2 = dict(rec, state="running")
    g2 = tk.mirror_subagent(rec2, store=m)
    check("running → running", g2["ticket"]["state"] == tk.ST_RUNNING, g2["ticket"]["state"])

    rec3 = dict(rec, state="done", result="明天多云 12~20℃")
    g3 = tk.mirror_subagent(rec3, store=m)
    check("done → done", g3["ticket"]["state"] == tk.ST_DONE, g3["ticket"]["state"])
    check("结果落进回执",
          any("多云" in x["summary"] for x in g3["ticket"]["reports"]), g3["ticket"]["reports"])

    g4 = tk.mirror_subagent(rec3, store=m)
    check("★ 重复镜像幂等（不报错、不重复建单）",
          g4.get("ok") is True and g4["ticket"]["id"] == "tABC123", g4)
    check("重复镜像不会多开单", len(m.list(limit=99)) == 1, len(m.list(limit=99)))

    g5 = tk.mirror_subagent({"task_id": "tT", "goal": "x", "state": "timeout", "error": "排队超时"},
                            store=m)
    check("timeout → failed", g5["ticket"]["state"] == tk.ST_FAILED, g5["ticket"]["state"])
    check("没有 task_id 被拒", tk.mirror_subagent({"goal": "x"}, store=m).get("ok") is False)

    print("\n[H] 隔离：EGGBLOOM_TICKET_DB")
    db5 = _tmpdb("env")
    old = os.environ.get("EGGBLOOM_TICKET_DB")
    os.environ["EGGBLOOM_TICKET_DB"] = db5
    try:
        tk.reset_default()
        d1 = tk.default()
        check("默认实例认环境变量", os.path.abspath(d1.path) == os.path.abspath(db5), d1.path)
        d1.open("环境变量里的工单")
        tk.reset_default()
        d2 = tk.default()
        check("重建默认实例仍指向同一库", d2.list(limit=5) != [])
    finally:
        if old is None:
            os.environ.pop("EGGBLOOM_TICKET_DB", None)
        else:
            os.environ["EGGBLOOM_TICKET_DB"] = old
        tk.reset_default()

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
