# -*- coding: utf-8 -*-
"""启动冷却的日志一致性（2026-10-30 真机复盘）。

## 现象（14:13:01–14:13:43）

    完毕，开始监听消息        ← 冷却还没开始就先报"完毕"
    冷却中 1/5/10/15/20       ← 倒计时线程在初始化之后才启动
    完毕                      ← 主循环按 startup_time 到点
    冷却中 25s/30s、30s/30s   ← 倒计时还在数，与真实冷却无关

读起来像"重启了一次"或"冷却被重置"。功能没坏（闸都用 startup_time），
但这条日志是"到底能不能说话"的唯一线索，必须只有一个时钟。

## 钉死
  1. 打点规则只有一处：1、5、10…，最后一行留给主循环的"完毕"
  2. 源码里只剩**一处**"启动冷却完毕"
  3. 倒计时用的是 `startup_time`（不是自增计数器）

运行：python tests/test_startup_cooldown.py
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


tick = bot_core._cooldown_log_tick
check("第 1 秒打一行", tick(1, 30) is True)
check("第 5/10/15/20/25 秒打", all(tick(n, 30) for n in (5, 10, 15, 20, 25)))
check("第 2 秒不打", tick(2, 30) is False)
check("第 6 秒不打", tick(6, 30) is False)
check("最后一秒留给主循环", tick(30, 30) is False, tick(30, 30))
check("越界不打", tick(31, 30) is False and tick(-1, 30) is False)
check("冷却为 0 不打", tick(1, 0) is False)

src = io.open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
check("源码里只剩一处「启动冷却完毕」",
      src.count('"启动冷却完毕，开始监听消息"') == 1,
      src.count('"启动冷却完毕，开始监听消息"'))
i = src.find("def _countdown()")
seg = src[i:i + 1200] if i > 0 else ""
check("倒计时按 startup_time 算", "startup_time" in seg, seg[:200])
check("倒计时不再用 range(1, ...) 自增",
      "range(1, STARTUP_COOLDOWN" not in seg, "还有自增计数器")

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
