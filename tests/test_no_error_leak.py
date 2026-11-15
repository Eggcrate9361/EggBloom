# -*- coding: utf-8 -*-
"""不把工具报错原文发给用户 + 免费视觉链默认关（2026-10-30）。

真机：bot 在私聊里直接复读「重复/过大/格式不支持」，用户不希望看到报错消息。

钉死：
  1. 回复清洗删掉工具报错原文，其余保留
  2. 只剩报错时清洗成空串（调用方当无声）
  3. meme_save 连续失败 3 次 → 冷却 10 分钟，文案是用户可读的
  4. image_free_lane_enable 默认 False

运行：python tests/test_no_error_leak.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core  # noqa: E402
import reply_budget  # noqa: E402
import meme_collect as mc  # noqa: E402
from harness import tool_meme as tm  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


f = reply_budget.strip_tool_errors
check("删报错行留正文", f("行\n重复/过大/格式不支持\n好") == "行\n好", f("行\n重复/过大/格式不支持\n好"))
check("删 JSON 残片", f('{"ok": false, "saved": []}') == "")
check("删异常", f("工具执行异常: TypeError: x") == "")

check("整条链路：锚+报错 → 空", bot_core._strip_context_marks("21:12｜重复/过大/格式不支持") == "")
check("整条链路：锚+正文保留", bot_core._strip_context_marks("21:12｜还是那句") == "还是那句")

# meme_save 冷却
orig = mc.maybe_collect
try:
    mc.maybe_collect = lambda p, **kw: ""
    tm._FAIL_COUNT = 0
    tm._FAIL_UNTIL = 0.0
    tm.set_turn(images=["C:/tmp/a.jpg"], is_group=False, sender="1")
    for _ in range(3):
        r = tm.meme_save()
    check("失败三次后冷却", r.get("cooldown") is True, r)
    check("冷却文案可读", "先别试" in str(r.get("note") or ""), r.get("note"))
    check("失败文案不含技术词", "格式" not in str(r.get("note") or ""), r.get("note"))
finally:
    mc.maybe_collect = orig
    tm._FAIL_COUNT = 0
    tm._FAIL_UNTIL = 0.0

check("免费视觉链默认关", bot_core._config.get("image_free_lane_enable", False) is False)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
