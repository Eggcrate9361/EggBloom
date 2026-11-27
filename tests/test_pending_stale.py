# -*- coding: utf-8 -*-
"""tests/test_pending_stale.py —— 等待状态会过期（2026-11-27）

背景：`pending` 没有自然失效机制。生产里某个群最后一条是 60 小时前说的，
`pending` 仍为 True，于是每小时产出一条 no_reply 去问模型，一天 20 次调用
换回 20 个 skip。

判据：
  [A] 刚说完（8 分钟～6 小时）→ 产出 no_reply
  [B] 超过 6 小时 → 不再产出（那条对话已经结束）
  [C] 对方已经回过（last_user 晚于 last_bot）→ 不产出
  [D] 不满最小间隔（8 分钟）→ 不产出
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

from autonomy import ledger as L  # noqa: E402

PASS, FAIL = 0, 0
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


NOW = 1_000_000.0
MIN_S = 8 * 60.0


def state(bot_ago_s, user_ago_s=None, text="行，那我先睡了"):
    if user_ago_s is None:
        user_ago_s = bot_ago_s + 600
    return {"scene": "private", "target": "1000000006", "pending": True,
            "last_bot_ts": NOW - bot_ago_s, "last_user_ts": NOW - user_ago_s,
            "pending_since": NOW - bot_ago_s, "pending_trace": "t1",
            "last_bot_text": text}


def kinds(st):
    obs = L.scan(st, NOW, no_reply_s=MIN_S, at_dropped_s=180.0, promise_s=600.0)
    return [o["kind"] for o in obs]


def main():
    print("=== A) 正常窗口内 ===")
    check("A1 8 分钟后 → no_reply", "no_reply" in kinds(state(8 * 60)), kinds(state(8 * 60)))
    check("A2 1 小时后 → no_reply", "no_reply" in kinds(state(3600)))

    print("\n=== B) 过期 ===")
    check("B1 6 小时 01 分 → 不提", "no_reply" not in kinds(state(6 * 3600 + 60)))
    check("B2 60 小时 → 不提", "no_reply" not in kinds(state(60 * 3600)))

    print("\n=== C) 对方已经回过 ===")
    st = state(3600, user_ago_s=60)
    check("C1 最后一句是对方说的 → 不提", "no_reply" not in kinds(st), kinds(st))

    print("\n=== D) 不满最小间隔 ===")
    check("D1 5 分钟 → 不提", "no_reply" not in kinds(state(5 * 60)))

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for x in FAILURES:
        print(f"  FAIL  {x}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
