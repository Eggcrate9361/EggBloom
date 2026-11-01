# -*- coding: utf-8 -*-
"""tests/test_job_wait.py —— 后台任务「一次调用等到结束」（1.6.0 P3.2 前置）

设计依据：`分离与真实-设计.md` §11.1 ——
20 分钟的下载应该只消耗 **1 次**工具调用，而不是 200 次 `job_output` 轮询。

锁五件事：
  A. 内联命令能起（不必先 ws_write 脚本）
  B. `job_wait` 一次调用就等到进程结束，并带回输出
  C. `until` 正则命中提前返回
  D. 超时（还在跑）如实返回 running，不假装结束
  E. 任务号不存在 → 明确报错
"""
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from harness import tool_proc as tp  # noqa: E402
from harness import workspace as ws  # noqa: E402

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


def _jid(out: str) -> str:
    return out.split("已在后台启动 ")[1].split("（")[0].strip()


def main():
    ws.set_root(tempfile.mkdtemp(prefix="eb_job_"))
    py = sys.executable

    print("[A] 内联命令")
    out = tp.job_start({
        "cmd": [py, "-u", "-c",
                "import time\n"
                "for i in range(3):\n"
                "    print('tick', i, flush=True)\n"
                "    time.sleep(0.3)\n"],
        "label": "inline-ok"})
    check("返回任务号", "已在后台启动" in out, out[:80])
    jid = _jid(out)

    print("\n[B] job_wait 一次等到结束")
    t0 = time.time()
    res = tp.job_wait({"job_id": jid, "max_sec": 20})
    cost = time.time() - t0
    check("状态 finished", "[finished]" in res, res[:200])
    check("带回输出", "tick 0" in res and "tick 2" in res, res[:300])
    check("一次调用内完成", cost < 10, cost)

    print("\n[C] until 正则命中提前返回")
    out2 = tp.job_start({
        "cmd": [py, "-u", "-c",
                "import time\n"
                "print('start', flush=True)\n"
                "time.sleep(0.4)\n"
                "print('READY', flush=True)\n"
                "time.sleep(5)\n"],
        "label": "regex-ok"})
    jid2 = _jid(out2)
    t1 = time.time()
    res2 = tp.job_wait({"job_id": jid2, "max_sec": 15, "until": "READY"})
    cost2 = time.time() - t1
    check("命中 READY", "READY" in res2 and "命中" in res2, res2[:200])
    check("提前返回（没等到 5 秒后）", cost2 < 4.0, cost2)
    tp.job_kill({"job_id": jid2, "reason": "测试"})

    print("\n[D] 超时如实返回 running")
    out3 = tp.job_start({
        "cmd": [py, "-u", "-c", "import time; time.sleep(6)"],
        "label": "slow"})
    jid3 = _jid(out3)
    t2 = time.time()
    res3 = tp.job_wait({"job_id": jid3, "max_sec": 1.5})
    cost3 = time.time() - t2
    check("说还在跑", "running" in res3 and "还没结束" in res3, res3[:200])
    check("按时返回", cost3 < 4.5, cost3)
    tp.job_kill({"job_id": jid3, "reason": "测试"})

    print("\n[E] 任务号不存在")
    res4 = tp.job_wait({"job_id": "job-nope", "max_sec": 1})
    check("明确报错", "没有后台任务" in res4, res4[:100])

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
