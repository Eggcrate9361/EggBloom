# -*- coding: utf-8 -*-
"""撤回反馈的关联判定（2026-10-30）。

用户原话：不要什么撤回都回。
规则：只有"截断了 bot 的回复"或"bot 最近在这个群说过话"才回。

运行：python tests/test_recall_scope.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


f = bot_core._recall_should_fire
now = time.time()

check("截断回复 → 必回", f(buffer_cancelled=True, last_bot_msg=0, now=now))
check("bot 60 秒前说过话 → 回", f(buffer_cancelled=False, last_bot_msg=now - 60, now=now))
check("bot 10 分钟前说过话 → 不回",
      not f(buffer_cancelled=False, last_bot_msg=now - 600, now=now))
check("从没说过话 → 不回", not f(buffer_cancelled=False, last_bot_msg=0, now=now))
check("窗口下限 10 秒（window=0 时刚说完仍回）",
      f(buffer_cancelled=False, last_bot_msg=now - 5, now=now, window=0))
check("脏数据不炸",
      f(buffer_cancelled=False, last_bot_msg="bad", now=now) is False)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
