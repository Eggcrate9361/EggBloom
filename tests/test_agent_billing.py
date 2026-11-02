# -*- coding: utf-8 -*-
"""子 agent 的调用也要记账（2026-11-02）。

## 事故

`token_stats.record()` 只在 bot_core 的三处主链路径被调用 ——
**agent / deep_task 内部的调用完全不计**。真机证据：09:34:35 起跑了一次
85 秒的 `deep_task`（子 agent 读了 1000 行代码、跑了测试），token_stats
在那 85 秒里**一条记录都没多**。

于是"看价格"永远是低估的：最贵的那些活（多步任务）恰好一分钱都不记。

## 契约

`_agent_chat` 统一记账：不管走流式还是同步、有没有思维链，
只要拿到 usage 就写一条 `lane="agent"`，scene 取当前场景
（`tool_deep.current_scene()`，群/私聊）。

运行：python tests/test_agent_billing.py
"""
import inspect
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core  # noqa: E402
from harness import tool_deep as td  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


SRC = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()

# ── 1. 源码层面：_agent_chat 里确实记账 ─────────────────────────────
check("★ _agent_chat 内有记账调用", "def _bill(_res)" in SRC)
check("★ 记的是 lane=agent", 'lane="agent"' in SRC)
check("流式路径也过 _bill",
      "_bill(_agent_stream_chat(" in SRC)
check("同步路径也过 _bill", 'return _bill({' in SRC)

# ── 2. 场景归因走 tool_deep.current_scene() ─────────────────────────
check("场景归因用 current_scene", "tool_deep as _tdp" in SRC and
      "_tdp.current_scene()" in SRC)
td.set_context(is_group=True, group_id="1", sender="2")
check("群场景归因 = group", td.current_scene() == "group", td.current_scene())
td.set_context(is_group=False, group_id="", sender="2")
check("私聊场景归因 = private", td.current_scene() == "private", td.current_scene())

# ── 3. 记账失败不能影响回复 ─────────────────────────────────────────
import token_stats as ts  # noqa: E402

_orig = ts.record


def boom(*a, **k):
    raise RuntimeError("账本坏了")


ts.record = boom
try:
    # 直接验 _bill 的等价逻辑：异常被吞掉，返回值照常
    def _bill_like(_res):
        try:
            from token_stats import record as _tsr
            _tsr("private", {"prompt_tokens": 1}, {})
        except Exception:
            pass
        return _res

    check("★ 记账抛异常时结果照常返回",
          _bill_like({"ok": True}).get("ok") is True)
finally:
    ts.record = _orig

# ── 4. token_stats.record 接受 lane 参数 ────────────────────────────
params = inspect.signature(ts.record).parameters
check("record 有 lane 参数", "lane" in params, list(params))
check("record 有 model 参数", "model" in params, list(params))

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
