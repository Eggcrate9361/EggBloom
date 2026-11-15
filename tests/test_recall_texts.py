# -*- coding: utf-8 -*-
"""撤回反馈话术归一化（bot_core._recall_texts，2026-10-30）。

真机 18:43：`撤回反馈发送失败: 'dict' object has no attribute 'split'`。
根因：配置页把话术存成 `[{"text": "...", "disabled": false}]`，
旧代码直接 `random.choice` 选出 dict，交给 smart_send_group 就炸。

钉死：
  1. 纯字符串列表原样可用
  2. dict 结构取 text，disabled 的跳过
  3. 混合结构也能过滤出纯文本
  4. 空/None → 空列表（调用方回落到默认话术）

运行：python tests/test_recall_texts.py
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

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


f = bot_core._recall_texts
check("纯字符串", f(["a", "b"]) == ["a", "b"])
check("过滤空串", f(["a", "", "  "]) == ["a"])
check("dict 取 text", f([{"text": "x", "disabled": False}]) == ["x"])
check("disabled 跳过", f([{"text": "x", "disabled": False},
                          {"text": "y", "disabled": True}]) == ["x"])
check("混合结构", f(["a", {"text": "b"}, {"disabled": True}]) == ["a", "b"])
check("None → 空", f(None) == [])
check("空列表 → 空", f([]) == [])
check("非序列元素跳过", f([123, None, "ok"]) == ["ok"])

# 模拟真机配置：取出来的每个都能安全 .split
items = [{"text": "看到了 晚了", "disabled": False},
         {"text": "来不及了 嘿嘿", "disabled": False}]
out = f(items)
check("结果都是 str", all(isinstance(x, str) for x in out), out)
check("结果能 split", all(len(x.split()) >= 1 for x in out), out)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
