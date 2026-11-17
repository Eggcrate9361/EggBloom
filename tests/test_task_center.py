# -*- coding: utf-8 -*-
"""harness/task_center.py 单测（1.6.0 统一任务中心）。

钉死：submit/wait/resume/cancel/list/recover 全套；全局唯一 job_id；
**跨进程凭 job_id 续等**（换实例、换 OS 进程都能接上）；recover() 统一收尸；
以及最关键的「tool_proc._JOBS 清空后 job_wait 仍能靠总表接回」。

不联网、不碰真实模型/ffmpeg；只用临时目录与真实短命令子进程。
运行：python tests/test_task_center.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="taskcenter_test_")
# 三个数据层全部隔离到临时目录，互不污染、也不碰仓库里的默认库
os.environ["EGGBLOOM_TASKCENTER_DB"] = os.path.join(TMP, "tc.sqlite3")
os.environ["EGGBLOOM_TICKET_DB"] = os.path.join(TMP, "tickets.sqlite3")
os.environ["EGGBLOOM_OUTBOX_DB"] = os.path.join(TMP, "outbox.sqlite3")

from harness import task_center as tc  # noqa: E402
from harness import ticket, outbox     # noqa: E402

tc.reset_default()
ticket.reset_default()
outbox.reset_default()

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


try:
    # ══ 1. submit + wait（异步执行体）════════════════════════════
    jid = tc.submit(kind="demo", source="generic", label="算个乘法",
                    payload={"a": 6, "b": 7},
                    run_fn=lambda p: p["a"] * p["b"])
    check("submit 返回全局唯一 job_id", isinstance(jid, str) and jid.startswith("job-"), jid)
    rec = tc.wait(jid, timeout=10, poll=0.05)
    check("wait 等到终态 done", rec and rec["status"] == "done", rec and rec["status"])
    check("结果落库", rec and rec["result"] == "42", rec and rec["result"])
    check("get 按 job_id 取到", (tc.get(jid) or {}).get("label") == "算个乘法")

    # 抛异常 → failed
    jid2 = tc.submit(kind="demo", label="会炸", run_fn=lambda p: (_ for _ in ()).throw(RuntimeError("炸了")))
    r2 = tc.wait(jid2, timeout=10, poll=0.05)
    check("异常 → failed", r2 and r2["status"] == "failed", r2 and r2["status"])
    check("错误信息保留", r2 and "炸了" in r2["error"], r2 and r2["error"])

    # ══ 2. 换实例读同一个库 = 模拟重启（跨进程续等的前提）══════════
    other = tc.TaskCenter(os.environ["EGGBLOOM_TASKCENTER_DB"])
    check("★ 新实例仍看得到同一 job_id", (other.get(jid) or {}).get("status") == "done")
    check("★ 新实例 wait 立刻返回终态", (other.wait(jid, timeout=5) or {}).get("status") == "done")

    # ══ 3. 真正的跨进程续等：另起一个 OS 进程把任务推到 done ═══════
    jid3 = tc.submit(kind="demo", label="等别的进程完成", payload={})  # 无 run_fn，先 queued
    check("不带执行体时是 queued", (tc.get(jid3) or {}).get("status") == "queued")
    child_code = (
        "import sys, time\n"
        f"sys.path.insert(0, {ROOT!r})\n"
        "from harness import task_center as m\n"
        f"time.sleep(0.3)\n"
        f"m.default().update({jid3!r}, status='done', result='from-child', ended=time.time())\n"
    )
    env = dict(os.environ)
    proc = subprocess.Popen([sys.executable, "-c", child_code], cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    r3 = tc.wait(jid3, timeout=20, poll=0.05)      # 父进程凭 job_id 续等
    out, err = proc.communicate(timeout=30)
    check("★ 跨进程：子进程写、父进程 wait 接上", r3 and r3["status"] == "done", r3 and r3["status"])
    check("★ 跨进程结果正确", r3 and r3["result"] == "from-child", r3 and r3["result"])
    check("子进程没报错", proc.returncode == 0, err.strip()[:200])

    # ══ 4. until 正则提前返回 ════════════════════════════════════
    jid4 = tc.submit(kind="demo", label="等标记", payload={})
    tc.update(jid4, result="line1\nREADY\nline3")
    r4 = tc.wait(jid4, timeout=1, poll=0.05, until="READY")
    check("until 命中即返回（仍是 queued）", r4 and r4["status"] == "queued")

    # ══ 5. resume / cancel ═══════════════════════════════════════
    jid5 = tc.submit(kind="demo", label="挂起", payload={})
    tc.update(jid5, status="paused")
    tc.resume(jid5, run_fn=lambda p: "再来一次")
    r5 = tc.wait(jid5, timeout=10, poll=0.05)
    check("resume 重跑 → done", r5 and r5["status"] == "done", r5 and r5["status"])
    check("resume 结果正确", r5 and r5["result"] == "再来一次", r5 and r5["result"])
    check("终态无 run_fn 不复活", tc.resume(jid, run_fn=None) is not None and tc.get(jid)["status"] == "done")

    jid6 = tc.submit(kind="demo", label="要取消", payload={})
    check("cancel 生效", tc.cancel(jid6, "测试") is True and tc.get(jid6)["status"] == "cancelled")
    check("cancel 幂等", tc.cancel(jid6) is True)
    check("cancel 已完成任务被拒", tc.cancel(jid) is False)
    check("cancel 不存在的 id 被拒", tc.cancel("job-nope") is False)

    # 重复 job_id 不允许覆盖；执行超时和取消必须保留终态
    jid_keep = tc.submit(kind="demo", label="保留旧结果", payload={})
    tc.update(jid_keep, status="done", result="OLD")
    try:
        tc.submit(kind="demo", job_id=jid_keep, label="覆盖", run_fn=lambda p: "NEW")
        dup_rejected = False
    except ValueError:
        dup_rejected = True
    check("★ 重复 job_id 被拒绝", dup_rejected)
    check("★ 重复提交不覆盖旧结果",
          tc.get(jid_keep)["status"] == "done" and tc.get(jid_keep)["result"] == "OLD",
          tc.get(jid_keep))

    jid_timeout = tc.submit(kind="demo", label="执行超时",
                            run_fn=lambda p: (time.sleep(0.5) or "LATE"),
                            timeout=0.1)
    rto = tc.wait(jid_timeout, timeout=1, poll=0.02)
    check("★ 执行阶段超时进入 timeout", rto and rto["status"] == "timeout", rto)
    time.sleep(0.55)
    check("★ 超时后晚到结果不覆盖终态",
          tc.get(jid_timeout)["status"] == "timeout", tc.get(jid_timeout))

    def _cancel_work(_payload, cancel_event=None):
        deadline = time.time() + 3
        while time.time() < deadline and not cancel_event.is_set():
            time.sleep(0.02)
        return "stopped"

    jid_cancel = tc.submit(kind="demo", label="协作取消",
                           run_fn=_cancel_work, timeout=5)
    deadline = time.time() + 2
    while time.time() < deadline and (tc.get(jid_cancel) or {}).get("status") != "running":
        time.sleep(0.01)
    tc.cancel(jid_cancel, "测试取消")
    time.sleep(0.15)
    check("★ worker 完成后不覆盖 cancelled",
          tc.get(jid_cancel)["status"] == "cancelled", tc.get(jid_cancel))

    # ══ 6. list / stats ════════════════════════════════════════════
    lst = tc.list_jobs(status="done")
    check("list 按状态过滤", all(x["status"] == "done" for x in lst) and len(lst) >= 2, len(lst))
    check("list 按来源过滤", all(x["source"] == "generic" for x in tc.list_jobs(source="generic")))
    check("模块级 list() 与 list_jobs() 都可用",
          len(tc.list(status="done")) == len(tc.list_jobs(status="done")) >= 2)
    check("stats 有 total", tc.stats().get("total", 0) >= 6, tc.stats())

    # ══ 7. recover() 统一收尸（ticket/outbox/subagent/总表）══════════
    s = ticket.default()
    tkid = s.open("会卡住的工单")["ticket"]["id"]
    s.assign(tkid, "subagent")
    s.start(tkid)
    ob = outbox.default()
    oid = ob.add("chat-x", "text", "t", {"text": "hi"})
    ob.mark_sending(oid)
    tc.register("tSTALE", source="subagent", label="崩在 running", status="running")
    tc.register("jobNOOWNER", source="generic", label="无主 running", status="running")

    out_rec = tc.recover(reason="单测重启")
    check("recover 收掉 1 张 running 工单", out_rec.get("ticket") == 1, out_rec)
    check("工单 → blocked", s.get(tkid)["state"] == "blocked")
    check("recover 恢复 1 条 sending", out_rec.get("outbox") == 1, out_rec)
    check("outbox sending → pending", any(x["id"] == oid for x in ob.due("chat-x")))
    check("recover 收掉 subagent running", out_rec.get("subagent") == 1, out_rec)
    check("subagent running → paused（不是 failed）", tc.get("tSTALE")["status"] == "paused")
    check("总表无主 running → paused", tc.get("jobNOOWNER")["status"] == "paused")
    check("★ recover 把 ticket 收口进总表（同一 job_id）",
          out_rec.get("mirrored_ticket") == 1
          and (tc.get(tkid) or {}).get("source") == "ticket", out_rec)
    check("★ recover 把 outbox 收口进总表（同一 job_id）",
          out_rec.get("mirrored_outbox") == 1
          and (tc.get(oid) or {}).get("source") == "outbox", out_rec)
    s.cancel(tkid, "单测取消")
    mir2 = tc.default().mirror_sources()
    check("★ 来源状态变化后镜像会更新",
          mir2.get("ticket") >= 1
          and (tc.get(tkid) or {}).get("status") == "cancelled",
          (mir2, tc.get(tkid)))

    # ══ 8. aigc 只读镜像（aigc_task.py 不可改，扫它落盘的元数据）════
    media = os.path.join(TMP, "media")
    d = os.path.join(media, "image", "20260102")
    os.makedirs(d, exist_ok=True)
    meta = {"id": "aigc_img_1", "kind": "image", "status": "done",
            "artifact": os.path.join(d, "x.png"), "created_at": 1.0}
    with open(os.path.join(d, "aigc_img_1.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f)
    n = tc.mirror_aigc(media)
    check("mirror_aigc 收进总表", n == 1 and (tc.get("aigc_img_1") or {}).get("source") == "aigc", n)
    check("mirror_aigc 幂等", tc.mirror_aigc(media) == 0)

    # ══ 9. 关键修复：_JOBS 清空后 job_wait 凭 job_id 跨进程续等 ═══════
    from harness import tool_proc as tp  # noqa: E402
    from harness import workspace as ws  # noqa: E402
    jobs_dir = os.path.join(TMP, "jobs")
    os.makedirs(jobs_dir, exist_ok=True)
    old_jobs_dir = tp._jobs_dir
    tp._jobs_dir = lambda: jobs_dir
    ws.set_root(TMP)
    tp._JOBS.clear()
    try:
        out_start = tp.job_start({
            "cmd": [sys.executable, "-u", "-c",
                    "import time\n"
                    "print('HELLO-JOB', flush=True)\n"
                    "time.sleep(0.5)\n"
                    "print('BYE-JOB', flush=True)\n"],
            "label": "续等任务"})
        m = re.search(r"job-[0-9a-f]+", out_start)
        check("内联命令起后台任务", bool(m), out_start[:80])
        if m:
            jcid = m.group(0)
            check("job_start 已登记进总表", (tc.get(jcid) or {}).get("source") == "tool_proc")
            # 模拟进程重启：清空纯内存表（原来的痛点）
            tp._JOBS.clear()
            res = tp.job_wait({"job_id": jcid, "max_sec": 15})
            check("★ 清空 _JOBS 后 job_wait 仍接得上（finished）", "[finished]" in res, res[:160])
            check("★ 续等带回完整输出", "HELLO-JOB" in res and "BYE-JOB" in res, res[:200])
            check("★ 元数据与日志都落盘",
                  os.path.exists(os.path.join(jobs_dir, jcid + ".json"))
                  and os.path.exists(os.path.join(jobs_dir, jcid + ".log")))
            check("job_list 含可续等任务", jcid in tp.job_list())
    finally:
        tp._jobs_dir = old_jobs_dir
        tp._JOBS.clear()

    # 不存在的 id 仍明确报错
    check("job_wait 未知 id 明确报错", "没有后台任务" in tp.job_wait({"job_id": "job-nope", "max_sec": 1}))

    # ══ 10. 四套状态机最终都在同一个 job_id 命名空间里 ═══════════════
    sources = {r["source"] for r in tc.list_jobs(limit=1000)}
    check("★ 四套来源 + generic 收口到同一 job_id 命名空间",
          {"tool_proc", "subagent", "ticket", "outbox", "aigc"} <= sources, sources)
finally:
    tc.reset_default()
    ticket.reset_default()
    outbox.reset_default()
    shutil.rmtree(TMP, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
