# -*- coding: utf-8 -*-
"""tests/test_private_policy.py —— 私聊判定表（S2b-1）

判据：
  [A] 合批阶段：该攒就 WAIT，不攒就放行
  [B] 三个冷却阶段：边界值精确（还有 0.001s 就是冷却中，等于 0 就放行）
  [C] 未知阶段不猜，放行（调用方原逻辑兜底）
  [D] 原因串可机读（冷却类以「冷却：」开头）
  [E] 四个调用点都接了这张表（防止以后又散回 if）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from harness.action_policy import Action  # noqa: E402
from harness.private_policy import decide_private  # noqa: E402

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


def main():
    d = decide_private

    print("=== A) 合批 ===")
    check("A1 该攒 → WAIT", d(stage="buffer", should_buffer=True).action is Action.WAIT)
    check("A2 不该攒 → TEXT", d(stage="buffer", should_buffer=False).action is Action.TEXT)
    check("A3 WAIT 原因写清楚", d(stage="buffer", should_buffer=True).reason == "合批窗口")

    print("\n=== B) 冷却边界 ===")
    check("B1 撤回：还剩 0.1s → SKIP", d(stage="recall", recall_left=0.1).action is Action.SKIP)
    check("B2 撤回：刚好 0 → TEXT", d(stage="recall", recall_left=0).action is Action.TEXT)
    check("B3 撤回：负数（早过了）→ TEXT", d(stage="recall", recall_left=-3).action is Action.TEXT)
    check("B4 开机：还剩 2s → SKIP", d(stage="startup", startup_left=2).action is Action.SKIP)
    check("B5 开机：0 → TEXT", d(stage="startup", startup_left=0).action is Action.TEXT)
    check("B6 速率：还剩 3.2s → SKIP", d(stage="rate", rate_left=3.2).action is Action.SKIP)
    check("B7 速率：0 → TEXT", d(stage="rate", rate_left=0).action is Action.TEXT)

    print("\n=== C) 未知阶段 ===")
    check("C1 不认识的 stage 放行", d(stage="whatever").action is Action.TEXT)
    check("C2 空 stage 放行", d(stage="").action is Action.TEXT)

    print("\n=== D) 原因可分组 ===")
    for st, kw in (("recall", {"recall_left": 1.0}),
                   ("startup", {"startup_left": 1}),
                   ("rate", {"rate_left": 1.0})):
        r = d(stage=st, **kw).reason
        check("D-%s 以「冷却：」开头" % st, r.startswith("冷却："), r)

    print("\n=== E) 接线 ===")
    src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("E1 四个调用点", src.count("_PP.decide_private(") == 4,
          src.count("_PP.decide_private("))
    for stage in ("buffer", "recall", "startup", "rate"):
        check("E2-%s 阶段传对了" % stage, ('stage="%s"' % stage) in src)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for x in FAILURES:
        print(f"  FAIL  {x}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
