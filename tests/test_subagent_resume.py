# -*- coding: utf-8 -*-
"""harness/subagent.py 1.6.0 改造单测：状态走 TaskCenter + 允许恢复。

钉死：
  A. 原有契约不破：submit→done、异常→failed、on_done、嵌套派活禁止
  B. 重启后 running 不再一律 failed：清内存后凭 task_id 仍查得到，且是 paused
  C. recover() 把遗留 running/queued 收成 paused（可恢复），list_recoverable 列得出
  D. resume(task_id, run_fn=...) 重新执行 → done，总表同步
  E. TaskCenter.recover() 统一调用 subagent.recover()
  F. 真正的跨进程：另一个 OS 进程凭同一 task_id 读到的是 paused，不是 failed
  G. resume 的边界：未知 id / 终态无 run_fn 不复活

不联网、不碰真实模型/ffmpeg；只用临时目录与极短子进程。
运行：python tests/test_subagent_resume.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="subagent_resume_test_")
os.environ["EGGBLOOM_TASKCENTER_DB"] = os.path.join(TMP, "tc.sqlite3")
os.environ["EGGBLOOM_TICKET_DB"] = os.path.join(TMP, "tickets.sqlite3")
os.environ["EGGBLOOM_OUTBOX_DB"] = os.path.join(TMP, "outbox.sqlite3")

from harness import task_center as tc  # noqa: E402
from harness import subagent as sa     # noqa: E402

tc.reset_default()
sa.reset()
sa.DATA_DIR = TMP
sa.LOG_FILE = os.path.join(TMP, "subagent_tasks.jsonl")

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


def wait_done(tid, timeout=5.0, settled=False):
    """等到终态；settled=True 时再等 on_done 回调返回。

    只等 state 是不够的：`on_done` 必须在状态转终态**之后**执行（回调里要读
    result），所以 done 与回调返回之间存在窗口。2026-11-13 之前这里只等 state，
    于是「on_done 仍被调用一次」偶发红 —— 实测把回调写成 sleep 0.25s 就能
    100% 复现（state=done 时回调次数 0）。#90 的根因在测试这一侧，不是产品顺序。
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = sa.status(tid)
        if st.get("state") in ("done", "failed", "timeout", "cancelled"):
            if not settled or st.get("settled"):
                return st
        time.sleep(0.05)
    return sa.status(tid)


try:
    # ══ A. 原有契约不破 ═══════════════════════════════════════════
    got = []
    tid = sa.submit("算 1+1", run_fn=lambda g: "2", on_done=got.append)
    st = wait_done(tid, settled=True)
    check("submit → done", st.get("state") == "done", st)
    check("状态镜像进 TaskCenter", (tc.get(tid) or {}).get("status") == "done")
    check("on_done 仍被调用一次", len(got) == 1 and got[0].get("task_id") == tid)
    check("status 单查", sa.status(tid).get("state") == "done")
    check("status 列表含任务", any(x.get("task_id") == tid for x in sa.status()))

    def boom(g):
        raise RuntimeError("炸了")

    tid2 = sa.submit("会失败", run_fn=boom)
    # settled=True：镜像进总表发生在内存转终态**之后**，只等 state 会抢在写库前读
    st2 = wait_done(tid2, settled=True)
    check("异常 → failed", st2.get("state") == "failed", st2)
    check("异常镜像进 TaskCenter", (tc.get(tid2) or {}).get("status") == "failed")

    def nested(g):
        inner = sa.submit("内部", run_fn=lambda x: "x")
        return wait_done(inner).get("state")

    tid3 = sa.submit("外层", run_fn=nested)
    st3 = wait_done(tid3)
    check("★ 嵌套派活仍被禁止", st3.get("result") == "failed", st3)

    # ══ B. 重启后 running 不再一律 failed ══════════════════════════
    tc.register("tCRASH", source="subagent", kind="subagent", label="崩在 running 的活",
                status="running", payload={"goal": "崩在 running 的活"}, meta={"trace": "tr-9"})
    sa.reset()                       # 模拟进程重启：只剩磁盘总表
    s_crash = sa.status("tCRASH")
    check("★ 重启后凭 task_id 仍查得到", bool(s_crash) and s_crash.get("task_id") == "tCRASH", s_crash)
    check("★ 重启后不是 failed", s_crash.get("state") != "failed", s_crash.get("state"))
    check("★ 以可恢复的 paused 呈现", s_crash.get("state") == "paused", s_crash.get("state"))
    check("goal/meta 一起恢复", s_crash.get("goal") == "崩在 running 的活"
          and s_crash.get("meta", {}).get("trace") == "tr-9")

    # ══ C. recover / list_recoverable ═════════════════════════════
    tc.register("tQUEUED", source="subagent", label="排队时也崩了", status="queued")
    n = sa.recover(reason="单测重启")
    check("recover 收掉 running+queued 两条", n == 2, n)
    check("running → paused", (tc.get("tCRASH") or {}).get("status") == "paused")
    check("queued → paused", (tc.get("tQUEUED") or {}).get("status") == "paused")
    rec_ids = [r["task_id"] for r in sa.list_recoverable()]
    check("list_recoverable 列出待接手任务", "tCRASH" in rec_ids and "tQUEUED" in rec_ids, rec_ids)
    check("★ recover 不再产生 failed",
          (tc.get("tCRASH") or {}).get("status") != "failed")
    check("再 recover 一次不再动", sa.recover() == 0)

    # ══ D. resume 重新执行 ═══════════════════════════════════════
    rid = sa.resume("tCRASH", run_fn=lambda g: "RECOVERED-" + g)
    check("resume 返回 task_id", rid == "tCRASH", rid)
    r_done = wait_done("tCRASH", settled=True)
    check("★ resume 后 done", r_done.get("state") == "done", r_done)
    check("★ resume 用新执行体产出结果",
          r_done.get("result") == "RECOVERED-崩在 running 的活", r_done.get("result"))
    check("★ 总表状态同步为 done", (tc.get("tCRASH") or {}).get("status") == "done")
    check("resume 后不再是 recoverable",
          "tCRASH" not in [r["task_id"] for r in sa.list_recoverable()])

    # ══ E. TaskCenter.recover() 统一调用 subagent.recover ════════════
    tc.register("tSTALE2", source="subagent", label="又崩一条", status="running")
    out = tc.recover(reason="统一收尸")
    check("TaskCenter.recover 调了 subagent.recover", out.get("subagent") == 1, out)
    check("统一收尸后是 paused", (tc.get("tSTALE2") or {}).get("status") == "paused")

    # ══ F. 真正的跨进程：另一个 OS 进程读到的是 paused，不是 failed ══
    tc.register("tCROSS", source="subagent", kind="subagent", label="跨进程遗留", status="running")
    child_code = (
        "import sys\n"
        f"sys.path.insert(0, {ROOT!r})\n"
        "from harness import subagent as m\n"
        f"print(m.status('tCROSS').get('state'))\n"
    )
    cp = subprocess.run([sys.executable, "-c", child_code], cwd=ROOT,
                        env=dict(os.environ), capture_output=True, text=True, timeout=30)
    check("★ 另一进程凭同一 task_id 读到 paused（不是 failed）",
          cp.stdout.strip() == "paused", cp.stdout.strip() or cp.stderr[:160])

    # ══ G. resume 边界 ════════════════════════════════════════════
    check("resume 未知 id → 空串", sa.resume("t-nope", run_fn=lambda g: "x") == "")
    tc.register("tNOEXEC", source="subagent", label="等待重新绑定", status="paused")
    check("★ 无 run_fn 的 resume 不伪装成功",
          sa.resume("tNOEXEC", run_fn=None) == "tNOEXEC"
          and (tc.get("tNOEXEC") or {}).get("status") == "paused",
          tc.get("tNOEXEC"))
    check("resume 已 done 且无 run_fn → 空串（不无脑复活）",
          sa.resume("tCRASH", run_fn=None) == "")
    check("已 done 的任务状态没被改", (tc.get("tCRASH") or {}).get("status") == "done")
    check("status 未知 id → 空 dict", sa.status("t-nope") == {})
finally:
    tc.reset_default()
    sa.reset()
    shutil.rmtree(TMP, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
