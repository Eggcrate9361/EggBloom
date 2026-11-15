# -*- coding: utf-8 -*-
"""tests/test_proactive_closing.py —— 明确收尾就别再主动开口（2026-11-04）

用户原话：「如果没有明确语义结束话题的时候，应该开口」。反过来说：**明确结束就别开口**。
现在的失败形态是：对方说了「晚安」，过几个小时 bot 冒出来问「还聊不聊」—— 那是打扰。

判据只认**明确的收尾语**；「嗯」「哦」这种只是没接话，不算结束，仍然可以开口。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import plugins.proactive_talk as PT  # noqa: E402

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


def main():
    print("[A] 明确收尾 → 不再开口")
    for s in ("晚安", "我睡了", "先这样吧", "88", "下了", "去忙了", "回头聊",
              "不聊了", "我走了", "收工"):
        check(f"「{s}」算结束", PT.looks_finished(s) is True, s)

    print("\n[B] 没接话 ≠ 结束，仍可开口")
    for s in ("嗯", "哦", "好", "行吧", "？", "", "哈哈"):
        check(f"「{s}」不算结束", PT.looks_finished(s) is False, s)

    print("\n[C] 长句里提到收尾词不算（防止误杀）")
    long_ok = "我今天下午三点开会开到六点然后还去看了个电影回来路上堵了半小时"
    check("长句不判结束", PT.looks_finished(long_ok) is False, len(long_ok))
    check("长句里含\"晚安\"也不判", PT.looks_finished("他跟我说晚安的时候我还在地上躺着呢") is False)

    print("\n[D] 判据存在且被用上")
    src = open(PT.__file__, encoding="utf-8", errors="replace").read()
    check("★ 定义了 looks_finished", "def looks_finished(" in src)
    check("★ 主动开口前会查", "looks_finished(last_user)" in src)
    check("★ force（手动触发）不受限", "if not force:" in src)
    check("★ 跳过有日志可查", "上次明确收尾" in src)

    print("\n[E] 调性：失去感，不是\"还聊不聊\"")
    check("★ 提示词里点了失落", "失落" in src)
    check("★ 明确不许问\"还聊不聊\"", "还聊不聊" in src)
    check("不许把压力甩给对方", "把压力甩给对方" in src)

    print("\n" + "=" * 60)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
