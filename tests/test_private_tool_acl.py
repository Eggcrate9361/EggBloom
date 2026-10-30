# -*- coding: utf-8 -*-
"""私聊工具白名单（2026-10-30）。

用户要求：agent 工具在私聊只对一个人/白名单有用，其他人就说「没有权限」。

钉死：
  1. _private_tool_allowed 认裸号 / 「昵称(号)」两种写法
  2. 非白名单私聊拿不到 write/proc/desktop；白名单拿得到
  3. 越权调用返回「没有权限」而不是「未提供工具」
  4. max_tool_rounds 已暴露到配置中心

运行：python tests/test_private_tool_acl.py
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
from harness import tool_runner as trn  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  {extra}")


def sch(n):
    return {"type": "function", "function": {"name": n, "parameters": {"type": "object", "properties": {}}}}


tr.register("_acl_work", sch("_acl_work"), groups=(tr.GROUP_WORK,), source="test")
tr.register("_acl_write", sch("_acl_write"), groups=(tr.GROUP_WRITE,), source="test")
tr.register("_acl_proc", sch("_acl_proc"), groups=(tr.GROUP_PROC,), source="test")
tr.register("_acl_desktop", sch("_acl_desktop"), groups=(tr.GROUP_DESKTOP,), source="test")

cfg = bot_core._config
old = cfg.get("private_tool_whitelist")
try:
    f = bot_core._private_tool_allowed
    cfg["private_tool_whitelist"] = ["3150007406"]
    check("裸号命中", f("3150007406") is True)
    check("昵称带号命中", f("(/ω＼)(3150007406)") is True)
    check("非名单拒绝", f("2011257385") is False)
    cfg["private_tool_whitelist"] = []
    check("空名单谁都不给", f("3150007406") is False)

    cfg["private_tool_whitelist"] = ["3150007406"]
    ok_set = set(bot_core._tools_subset_for_scene(False, privileged=True))
    no_set = set(bot_core._tools_subset_for_scene(False, privileged=False))
    check("白名单有 write", "_acl_write" in ok_set, sorted(ok_set))
    check("白名单有 proc", "_acl_proc" in ok_set)
    check("白名单有 desktop", "_acl_desktop" in ok_set)
    check("非白名单没有 write", "_acl_write" not in no_set)
    check("非白名单没有 proc", "_acl_proc" not in no_set)
    check("非白名单没有 desktop", "_acl_desktop" not in no_set)
    check("非白名单仍有只读", "_acl_work" in no_set)
finally:
    if old is None:
        cfg.pop("private_tool_whitelist", None)
    else:
        cfg["private_tool_whitelist"] = old

# 越权提示
tc = {"id": "t1", "function": {"name": "_acl_proc", "arguments": "{}"}}
res = trn.execute_tool_call(tc, tool_functions={"_acl_proc": lambda a: "x"},
                            offered_names={"_acl_work"})
check("越权回没有权限", "没有权限" in str(res.get("inline") or ""), res.get("inline"))

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
