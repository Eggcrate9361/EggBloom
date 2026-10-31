# -*- coding: utf-8 -*-
"""tests/test_sleep_state_prompt.py —— 睡觉状态：事实在代码，话术在人格文件

P0-2 收口（2026-10-31）之前，`_inject_sleep_state` 把"回复要短、带困意、别长篇"
整段写在 bot_core 里 —— 人格文件改不动它，等于**第二个人格源**。现在：

    代码只报这一轮的事实：你在睡觉、是被叫醒的
    怎么说：人格文件 `### 睡觉` 块里的【睡着的时候】

本文件锁三件事：
  A. 静默时段命中 → 注入里既有事实标签，也有人格文件里的原话
  B. 非静默时段 → 一个字都不注入（零 token）
  C. bot_core 里不再出现那段话术（防止有人手快又写回代码里）

运行：python tests/test_sleep_state_prompt.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bot_core  # noqa: E402

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


def _with_quiet(flag):
    """把 in_quiet_hours 临时钉成 flag。"""
    from harness import time_ctx
    old = time_ctx.in_quiet_hours
    time_ctx.in_quiet_hours = lambda *a, **k: flag
    return lambda: setattr(time_ctx, "in_quiet_hours", old)


def main():
    from harness import time_ctx

    print("[A] 静默时段命中 → 事实 + 人格话术")
    restore = _with_quiet(True)
    try:
        parts = []
        bot_core._inject_sleep_state(parts, is_group=True)
        t = parts[0] if parts else ""
        check("注入了", bool(t), parts)
        check("带事实标签【你正在睡觉】", "【你正在睡觉】" in t, t)
        print("     取人格文件里的原话（证明话术不在代码里）")
        rule = bot_core._persona_rule_segment("睡觉")
        check("人格文件有 `### 睡觉` 块", bool(rule.strip()), repr(rule[:40]))
        check("注入内容包含该段原话", rule.strip() in t, t)
    finally:
        restore()

    print("\n[B] 非静默时段 → 零注入")
    restore = _with_quiet(False)
    try:
        parts = []
        bot_core._inject_sleep_state(parts, is_group=False)
        check("什么都没注入", parts == [], parts)
    finally:
        restore()

    print("\n[C] 话术不许回写进代码")
    src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    for phrase in ("回复要短，一两句就够", "语气带着困意", "有点迟钝"):
        check(f"bot_core 里没有「{phrase}」", phrase not in src)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
