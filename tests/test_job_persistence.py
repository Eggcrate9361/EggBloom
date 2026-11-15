# -*- coding: utf-8 -*-
"""tests/test_job_persistence.py —— 后台任务落盘/重启接上（设计 §11.1）。

不启真子进程：用当前进程 pid 当"还活着的任务"，只验证元数据 + 日志续读。
"""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import tool_proc as tp

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
        FAILURES.append(f"{name} :: {extra}")
        print(f"  [FAIL] {name} :: {extra}")


def main():
    td = tempfile.mkdtemp(prefix="eb-jobs-")
    old = tp._jobs_dir
    tp._jobs_dir = lambda: td
    try:
        log = os.path.join(td, "job-test.log")
        j = tp._Job("job-test", "persist", ["x"], None, log_path=log)
        j.pid = os.getpid()
        j.status = "running"
        j.json_path = os.path.join(td, "job-test.json")
        j._write_log("hello")
        j._write_log("world")
        tp._persist_job(j)

        tp._JOBS.clear()
        tp._restore_jobs()
        check("重启后接上任务", "job-test" in tp._JOBS, list(tp._JOBS))
        out = tp.job_output({"job_id": "job-test"})
        check("日志能续读", "hello" in out and "world" in out, out)
        check("状态 running", "[running]" in out, out)

        # 死 pid 的任务应在 restore 时被清掉
        dead = tp._Job("job-dead", "dead", ["x"], None, log_path=os.path.join(td, "dead.log"))
        dead.pid = 99999999
        dead.json_path = os.path.join(td, "job-dead.json")
        tp._persist_job(dead)
        tp._JOBS.clear()
        tp._restore_jobs()
        check("死任务不进列表", "job-dead" not in tp._JOBS, list(tp._JOBS))
        check("死任务元数据清掉", not os.path.exists(os.path.join(td, "job-dead.json")))

        # 真子进程：内联命令 + job_wait 一次等到结束
        started = tp.job_start({"cmd": [sys.executable, "-c", "print('inline-ok')"]})
        m = re.search(r"(job-[0-9a-f]+)", started)
        check("内联命令启动", bool(m), started)
        if m:
            jid = m.group(1)
            out = tp.job_wait({"job_id": jid, "max_sec": 15})
            check("job_wait 读到输出", "inline-ok" in out, out)
            check("任务元数据落盘", os.path.exists(os.path.join(td, jid + ".json")))
            check("任务日志落盘", os.path.exists(os.path.join(td, jid + ".log")))
    finally:
        tp._jobs_dir = old
        tp._JOBS.clear()
        shutil.rmtree(td, ignore_errors=True)

    print("=" * 58)
    if FAIL:
        print(f"失败 {FAIL} 条：")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print(f"通过 {PASS} / 失败 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
