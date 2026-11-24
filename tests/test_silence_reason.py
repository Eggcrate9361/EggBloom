# -*- coding: utf-8 -*-
"""tests/test_silence_reason.py —— 静默必须留原因（P1-4）

判据：
  [A] `_report_turn_skipped` 真的写出一条 SKIPPED，带 scene/target/why
  [B] 私聊三类冷却（撤回反馈 / 开机 / 速率）都走这条出口
  [C] 原因串能被机器分组（以「冷却」开头）
  [D] 不写空原因：没传 reason 时有兜底文字
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core as B  # noqa: E402
import harness.turn_trace as T  # noqa: E402

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


def last_for(turn):
    for rec in reversed(T.tail(300)):
        if str(rec.get("turn")) == turn:
            return rec
    return {}


def main():
    print("=== A/B) 冷却走到统一出口 ===")
    tid = "c0c0c0c0c0c0"
    B._set_trace(tid)
    B._report_turn_skipped(scene="private", target="900000002",
                           reason="冷却：私聊速率 3.2s", sender="900000002")
    rec = last_for(tid)
    check("A1 写出 SKIPPED", rec.get("phase") == "SKIPPED", rec)
    check("A2 场景是私聊", rec.get("scene") == "private", rec)
    check("A3 带收件人", rec.get("target") == "900000002", rec)
    check("C1 原因可按前缀分组", str(rec.get("why") or "").startswith("冷却"), rec.get("why"))

    print("\n=== D) 空原因兜底 ===")
    tid2 = "d0d0d0d0d0d0"
    B._set_trace(tid2)
    B._report_turn_skipped(scene="private", target="900000002", reason="")
    rec2 = last_for(tid2)
    check("D1 没有空 why", bool(rec2.get("why")), rec2)

    print("\n=== B) 三处冷却都接了出口 ===")
    src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    for label, needle in (
            ("撤回反馈", 'reason=f"冷却：撤回反馈'),
            ("开机", 'reason=f"冷却：开机'),
            ("私聊速率", 'reason=f"冷却：私聊速率')):
        check(f"B-{label} 冷却留痕", needle in src)

    # 这三处都必须紧邻 _report_turn_skipped 与 continue，防止以后被人删掉
    import re
    blocks = re.findall(r"冷却中[^\n]*\n(?:[^\n]*\n){0,6}?\s*continue", src)
    withreason = [b for b in blocks if "_report_turn_skipped" in b]
    check("B4 冷却分支里跳过前都记了原因", len(blocks) >= 3 and len(withreason) >= 3,
          f"冷却分支 {len(blocks)} 个，带原因 {len(withreason)} 个")

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for x in FAILURES:
        print(f"  FAIL  {x}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
