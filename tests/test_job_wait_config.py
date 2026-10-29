# -*- coding: utf-8 -*-
"""1.5.10 H6：job_output 等待上限可配（默认 5s 行为不变）。"""
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import harness.tool_proc as tp  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [OK]   " if ok else "  [FAIL] ") + name + ("" if ok else f"  {detail}"))


print("\n[1] 默认 5s（与改前一致）")
check("缺省 → 5000ms", tp._job_wait_ms(None) == 5000, tp._job_wait_ms(None))
check("传 3000 → 3000ms", tp._job_wait_ms(3000) == 3000, tp._job_wait_ms(3000))
check("传超大 → 夹到默认上限 5000ms", tp._job_wait_ms(999999) == 5000, tp._job_wait_ms(999999))
check("负数 → 0", tp._job_wait_ms(-5) == 0, tp._job_wait_ms(-5))
check("非法值 → 默认", tp._job_wait_ms("abc") == 5000, tp._job_wait_ms("abc"))

print("\n[2] 环境变量放开上限（1.6.0 job_wait 用）")
old_env = os.environ.get("EGGBLOOM_JOB_WAIT_MAX_MS")
try:
    os.environ["EGGBLOOM_JOB_WAIT_MAX_MS"] = "60000"
    tp2 = importlib.reload(tp)
    check("上限改 60000ms", tp2.JOB_WAIT_MAX_MS == 60000, tp2.JOB_WAIT_MAX_MS)
    check("默认仍 5000ms（行为不变）", tp2._job_wait_ms(None) == 5000, tp2._job_wait_ms(None))
    check("大值不再被 5s 夹死", tp2._job_wait_ms(30000) == 30000, tp2._job_wait_ms(30000))
finally:
    if old_env is None:
        os.environ.pop("EGGBLOOM_JOB_WAIT_MAX_MS", None)
    else:
        os.environ["EGGBLOOM_JOB_WAIT_MAX_MS"] = old_env
    importlib.reload(tp)

print("\n" + "=" * 56)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：", FAIL)
sys.exit(1 if FAIL else 0)
