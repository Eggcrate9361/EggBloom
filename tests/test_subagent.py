# -*- coding: utf-8 -*-
"""后台子 agent 任务队列（harness/subagent.py，2026-10-30）。

用户的设计：QQ 里聊天模型继续聊，活派给后台子 agent，干完再报。

钉死：
  1. submit → running → done，结果能取到
  2. 抛异常 → failed，带错误
  3. on_done 被调用一次
  4. 子 agent 里不许再派活（防递归）
  5. status 单查/列表

运行：python tests/test_subagent.py
"""
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="subagent_test_")
from harness import subagent as sa  # noqa: E402

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
        if st.get("state") in ("done", "failed", "timeout", "cancelled"):
            return st
        time.sleep(0.05)
    return sa.status(tid)


try:
    got = []
    tid = sa.submit("算个 1+1", run_fn=lambda g: "2", on_done=got.append)
    st = wait_done(tid)
    check("任务跑完", st.get("state") == "done", st)
    check("结果正确", st.get("result") == "2", st.get("result"))
    check("on_done 被调用", len(got) == 1 and got[0].get("task_id") == tid, got)
    check("status 单查", sa.status(tid).get("state") == "done")
    check("列表包含任务", any(x.get("task_id") == tid for x in sa.status()))

    def boom(g):
        raise RuntimeError("炸了")

    tid2 = sa.submit("会失败", run_fn=boom)
    st2 = wait_done(tid2)
    check("异常 → failed", st2.get("state") == "failed", st2)
    check("带错误信息", "炸了" in str(st2.get("error")), st2.get("error"))

    # 递归派活被拦
    def nested(g):
        inner = sa.submit("内部", run_fn=lambda x: "x")
        st_in = wait_done(inner)
        return st_in.get("state")

    tid3 = sa.submit("外层", run_fn=nested)
    st3 = wait_done(tid3)
    # 内层任务应被判 failed；外层正常结束，结果是 "failed" 这个状态字符串
    check("子 agent 里不能派活", st3.get("result") == "failed", st3)

    check("文件落盘", os.path.isfile(sa.LOG_FILE), sa.LOG_FILE)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
