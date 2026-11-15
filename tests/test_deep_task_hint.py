# -*- coding: utf-8 -*-
"""「通读/审查」请求要引导到 deep_task（2026-11-02 真机）。

## 事故

私聊「帮我看看 EGG BLOOM 的蒸馏插件有没有什么问题」，第二次尝试时模型：

```
09:04:57 工具调用: ['ws_glob', 'ws_glob']    第1轮
09:04:58 工具调用: ['ws_glob']               第2轮
09:04:59 工具调用: ['ws_glob', 'ws_glob']    第3轮
09:05:00 [工具轮次] 达到上限 3 轮，收尾
```

5 次列目录、**一次 ws_read 都没做**，最后只能回"我还没读进去，你等我"。
deep_task 当时已经可用（刀 3a + 修好的执行表），但模型没用。

## 契约

`_deep_task_hint(user_msg, tools_for_call)`：
- 有 deep_task 工具 **且** 用户话里是"通读/审查/挑毛病/看看代码"这类 → 给一句系统提示，
  明确"第一轮就调 deep_task，别 glob"；
- 否则返回空串（零 token，绝不每轮都塞）。

运行：python tests/test_deep_task_hint.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core as bc  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


TOOLS_ON = ["ws_read", "ws_glob", "deep_task"]

# ── 1. 该给的时候给 ─────────────────────────────────────────────────
CASES = [
    "帮我看看EGGBLOOM的蒸馏插件有没有什么问题",
    "再帮我看看eggbloom的蒸馏插件有没有什么问题",
    "你审查一下这个插件的代码",
    "挑挑毛病",
    "这个实现有没有bug",
    "review 一下 distill_engine",
    "检查一下实现",
    "通读一遍插件",
]
for msg in CASES:
    h = bc._deep_task_hint(msg, TOOLS_ON)
    check(f"命中：{msg[:16]}", "deep_task" in h and "第一轮" in h, h[:40])

# ── 2. 不该给的时候不给（零 token）──────────────────────────────────
for msg in ["在吗", "今天好累", "给我讲个笑话", "帮我画一张猫", ""]:
    h = bc._deep_task_hint(msg, TOOLS_ON)
    check(f"不命中：{msg or '(空)'}", h == "", h[:40])

# ── 3. 没有 deep_task 工具时永远不给（别让模型去调不存在的工具）──────
h = bc._deep_task_hint("你审查一下这个插件的代码", ["ws_read", "ws_glob"])
check("★ 工具不在列表里 → 不给提示", h == "", h[:40])
check("工具列表为空/None 也不炸",
      bc._deep_task_hint("审查代码", None) == "" and bc._deep_task_hint("审查代码", []) == "")

# ── 4. 提示里带上轮次上限（模型知道预算有多紧）──────────────────────
backup = dict(bc._config)
try:
    bc._config.update({"max_tool_rounds": 6})
    check("★ 提示里写出真实轮次上限", "6 轮" in bc._deep_task_hint("审查代码", TOOLS_ON),
          bc._deep_task_hint("审查代码", TOOLS_ON)[-30:])
    bc._config.update({"max_tool_rounds": 3})
    check("上限改了提示跟着变", "3 轮" in bc._deep_task_hint("审查代码", TOOLS_ON))
finally:
    bc._config.clear()
    bc._config.update(backup)

# ── 5. 工具描述本身也写了"第一轮就用" ───────────────────────────────
from harness import tool_deep as td  # noqa: E402

desc = td.DEEP_TOOLS[0]["function"]["description"]
check("★ 工具描述提到「第一轮就用」", "第一轮就用" in desc, desc[:50])
check("工具描述提醒别先 glob", "ws_glob" in desc, desc[:80])

# ── 6. 主链确实用了这个判据（防回退成内联正则）──────────────────────
src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
check("主链调用 _deep_task_hint", "_deep_task_hint(user_msg, tools_for_call)" in src)
check("主链里不再有那段内联正则",
      "这类「通读多个文件再判断」的活" not in src.split("def _deep_task_hint")[0])

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
