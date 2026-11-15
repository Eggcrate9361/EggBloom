# -*- coding: utf-8 -*-
"""工具全量注入（2026-10-30）。

用户问："工具为什么是写死的，不是想用就用吗"
旧逻辑：按消息关键词猜分组注入 → 没命中就给不出工具（真机「存」/「发我收的图」）。

新逻辑：按**场景许可**全量给，用不用由模型判断（tools_full_inject_enable）。

钉死：
  1. 私聊包含 write/proc/desktop 组
  2. 群聊排除 write/proc/desktop，但包含 group 组
  3. 旧关键词模式仍可切回（配置开关）

运行：python tests/test_tool_injection.py
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
from harness import tool_registry as tr  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


def sch(n):
    return {"type": "function", "function": {"name": n, "parameters": {"type": "object", "properties": {}}}}


tr.register("_t_work", sch("_t_work"), groups=(tr.GROUP_WORK,), source="test")
tr.register("_t_write", sch("_t_write"), groups=(tr.GROUP_WRITE,), source="test")
tr.register("_t_proc", sch("_t_proc"), groups=(tr.GROUP_PROC,), source="test")
tr.register("_t_desktop", sch("_t_desktop"), groups=(tr.GROUP_DESKTOP,), source="test")
tr.register("_t_group", sch("_t_group"), groups=(tr.GROUP_GROUP,), source="test")

p = set(bot_core._tools_subset_for_scene(False))
g = set(bot_core._tools_subset_for_scene(True))

check("私聊有 write", "_t_write" in p, sorted(p))
check("私聊有 proc", "_t_proc" in p)
check("私聊有 desktop", "_t_desktop" in p)
check("私聊有 work", "_t_work" in p)

check("群聊没有 write", "_t_write" not in g, sorted(g))
check("群聊没有 proc", "_t_proc" not in g)
check("群聊没有 desktop", "_t_desktop" not in g)
check("群聊有 group", "_t_group" in g)
check("群聊有 work", "_t_work" in g)

check("config 默认开启", bot_core._config.get("tools_full_inject_enable", True) is True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
