# -*- coding: utf-8 -*-
"""派活工具（harness/tool_task.py，2026-10-30）。

钉死：
  1. dispatch_task 带上当前对话上下文交给宿主
  2. 没有 dispatcher 时如实失败（不假装派出去了）
  3. task_status 能查单条/列表
  4. 两个工具都注册进 work 组

运行：python tests/test_tool_task.py
"""
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="tooltask_test_")
from harness import subagent as sa  # noqa: E402
from harness import tool_task as tt  # noqa: E402

sa.DATA_DIR = TMP
sa.LOG_FILE = os.path.join(TMP, "subagent_tasks.jsonl")
sa.reset()

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


def wait_done(tid, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = sa.status(tid)
        if st.get("state") in ("done", "failed"):
            return st
        time.sleep(0.05)
    return sa.status(tid)


try:
    # 未接线 → 失败
    tt.set_dispatcher(None)
    r = tt.dispatch_task("查天气")
    check("未接线如实失败", r.get("ok") is False and "接线" in str(r.get("error")), r)

    # 接线 + 上下文
    seen = {}

    def fake_dispatch(goal, ctx):
        seen.update({"goal": goal, "ctx": dict(ctx)})
        return sa.submit(goal, run_fn=lambda g: "做完了：" + g)

    tt.set_dispatcher(fake_dispatch)
    tt.set_context(sender="10001", is_group=False, group_id="")
    r2 = tt.dispatch_task("查一下明天天气")
    check("派活返回 task_id", r2.get("ok") and r2.get("task_id"), r2)
    check("带上了上下文", seen.get("ctx", {}).get("sender") == "10001", seen)
    st = wait_done(r2["task_id"])
    check("任务完成", st.get("state") == "done", st)

    q = tt.task_status(r2["task_id"])
    check("按号查得到", q.get("ok") and q["task"].get("result"), q)
    q2 = tt.task_status()
    check("不传号列全部", q2.get("ok") and isinstance(q2.get("tasks"), list), q2)
    q3 = tt.task_status("不存在")
    check("错号如实失败", q3.get("ok") is False, q3)

    # 注册
    fns = tt.register_all()
    check("注册返回函数表", set(fns) == {"dispatch_task", "task_status"}, fns)
    from harness import tool_registry as tr
    names = set(tr.all_names())
    check("dispatch_task 已注册", "dispatch_task" in names)
    check("task_status 已注册", "task_status" in names)
    check("归在 work 组", "dispatch_task" in tr.names_in(tr.GROUP_WORK), tr.names_in(tr.GROUP_WORK))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
