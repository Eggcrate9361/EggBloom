# -*- coding: utf-8 -*-
"""tests/test_typing_delay.py —— 「假装打字」的等待要可配置、可归零

判据：
  [A] 延迟 = min(上限, 字数 ÷ 字速)，两个键都生效
  [B] 任一个键为 0 / 负数 → 不等待（排障时能一键关掉）
  [C] 群聊与私聊两条发送路径都读 `reply_typing_delay_max`，没有写死的 4.0
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
    f = B._typing_delay

    print("=== A) 公式与两个键 ===")
    check("A1 短句按字数算", abs(f(12, 6, 4) - 2.0) < 1e-6, f(12, 6, 4))
    check("A2 长句撞上限", abs(f(60, 6, 4) - 4.0) < 1e-6, f(60, 6, 4))
    check("A3 上限调低立刻生效", abs(f(60, 6, 2.5) - 2.5) < 1e-6, f(60, 6, 2.5))
    check("A4 字速调高立刻生效", abs(f(24, 12, 4) - 2.0) < 1e-6, f(24, 12, 4))
    check("A5 上限低但字数更少时取字数",
          abs(f(6, 6, 4) - 1.0) < 1e-6, f(6, 6, 4))

    print("\n=== B) 归零与脏值 ===")
    check("B1 字速 0 → 不等", f(60, 0, 4) == 0.0, f(60, 0, 4))
    check("B2 上限 0 → 不等", f(60, 6, 0) == 0.0, f(60, 6, 0))
    check("B3 负上限 → 不等", f(60, 6, -1) == 0.0, f(60, 6, -1))
    check("B4 空文本 → 不等", f(0, 6, 4) == 0.0, f(0, 6, 4))
    check("B5 脏值不抛", f(None, None, None) == 0.0, f(None, None, None))

    print("\n=== C) 两条发送路径都走配置 ===")
    src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("C1 两处都读 reply_typing_delay_max",
          src.count('reply_typing_delay_max') >= 2, src.count("reply_typing_delay_max"))
    check("C2 两处都调用统一函数", src.count("_type_delay = _typing_delay(") >= 2,
          src.count("_type_delay = _typing_delay("))
    check("C3 没有残留写死的 4.0 上限",
          "min(4.0," not in src.replace("cap = float(_config", ""), "min(4.0,")

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for x in FAILURES:
        print(f"  FAIL  {x}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
