# -*- coding: utf-8 -*-
"""1.5.10 H5：trace_id 埋点（入站生成 → 发送透传；只加字段）。"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [OK]   " if ok else "  [FAIL] ") + name + ("" if ok else f"  {detail}"))


print("\n[1] trace 生成与线程内传递")
tid = bot_core._new_trace_id()
check("12 位十六进制", bool(re.fullmatch(r"[0-9a-f]{12}", tid)), tid)
check("两次不同", bot_core._new_trace_id() != bot_core._new_trace_id())
check("_set_trace 返回同一 id", bot_core._set_trace("abc123") == "abc123")
check("_cur_trace 读回", bot_core._cur_trace() == "abc123", bot_core._cur_trace())
check("_trace_suffix 形如 ' trace=abc123'", bot_core._trace_suffix() == " trace=abc123", bot_core._trace_suffix())

print("\n[2] 出站事件带同一个 trace")
events = []


class _PM:
    def emit_event(self, name, payload=None):
        events.append((name, dict(payload or {})))
        return []


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps({"retcode": 0}).encode("utf-8")


orig = (bot_core.get_plugin_manager, bot_core.REQUESTS_OK, bot_core.urllib.request.urlopen)
bot_core.get_plugin_manager = lambda: _PM()
bot_core.REQUESTS_OK = False
bot_core.urllib.request.urlopen = lambda *a, **k: _Resp()
try:
    bot_core._set_trace("trace0000001")
    bot_core.api_call("send_group_msg", {"group_id": 829854136, "message": "hi"})
finally:
    bot_core.get_plugin_manager, bot_core.REQUESTS_OK, bot_core.urllib.request.urlopen = orig

replied = [e for e in events if e[0] == "bot.replied"]
check("★ bot.replied 载荷带 trace_id", replied and replied[0][1].get("trace_id") == "trace0000001", events)

print("\n[3] 入站与日志接线（源码断言，防回退）")
src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
check("★ message.in 载荷带 trace_id", '"trace_id": _tid,' in src)
check("★ 新批次写 trace_id", '"trace_id": _tid,' in src)
check("★ 处理批次时设置线程 trace", '_set_trace(str(buffer.get("trace_id")' in src)
check("★ 决策放行日志带 trace 后缀", '{_trace_suffix()}' in src)

print("\n" + "=" * 56)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：", FAIL)
sys.exit(1 if FAIL else 0)
