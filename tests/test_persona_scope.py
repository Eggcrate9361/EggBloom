# -*- coding: utf-8 -*-
"""tests/test_persona_scope.py —— 分场景的干活边界不许串场（2026-11-27）

背景：用户反馈"AI 味太重"，定位到技术/求助类问题会切成代码评审腔。
决定分场景处理：群里不接活，私聊可以聊（尤其是她自己的实现）。

判据：
  [A] 群聊 prompt 有「群里不接活」，没有「私聊能聊活」
  [B] 私聊 prompt 有「私聊能聊活」，没有「群里不接活」
  [C] 两条都要求"不列点/不写代码块"，避免又变回助手
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import bot_core as bc  # noqa: E402

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


def text_of(is_group):
    return "\n".join(v for _k, v in bc.render_prompt_segments(is_group))


def main():
    g, p = text_of(True), text_of(False)

    print("=== A) 群聊 ===")
    check("A1 有「群里不接活」", "群里不接活" in g)
    check("A2 没有「私聊能聊活」", "私聊能聊活" not in g)
    check("A3 有「这我哪懂，你自己 debug」", "你自己 debug" in g)

    print("\n=== B) 私聊 ===")
    check("B1 有「私聊能聊活」", "私聊能聊活" in p)
    check("B2 没有「群里不接活」", "群里不接活" not in p)

    print("\n=== C) 两边的共同约束 ===")
    for where, t in (("群", g), ("私", p)):
        check("C-%s 不列点" % where, "不列点" in t)
        check("C-%s 不写代码块" % where, "不写代码块" in t)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for x in FAILURES:
        print(f"  FAIL  {x}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
