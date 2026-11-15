# -*- coding: utf-8 -*-
"""预设 vs 主模型 + 取消超时拦截（2026-10-30）。

用户要求：
  · 不要再做回复超时拦截（agent 可能跑十分钟）
  · 撤回反馈/空闲退场不要写死：开=预设不调模型；关=主模型现说

钉死：
  1. reply_stale_drop_enable 默认关 → 多老都照发
  2. 打开旧开关 → 仍按原规则丢
  3. _model_oneliner 只取第一行、失败返回空串

运行：python tests/test_preset_mode.py
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


cfg = bot_core._config
old = cfg.get("reply_stale_drop_enable")

try:
    cfg["reply_stale_drop_enable"] = False
    check("默认不拦超时", bot_core._drop_reason(age=999, new_msgs=0, addressed=False) == "")

    cfg["reply_stale_drop_enable"] = True
    r = bot_core._drop_reason(age=999, new_msgs=0, addressed=False)
    check("开旧开关仍能丢", "过时" in str(r), r)
finally:
    if old is None:
        cfg.pop("reply_stale_drop_enable", None)
    else:
        cfg["reply_stale_drop_enable"] = old

# 模型一句话
orig = bot_core._call_chat_sync
try:
    bot_core._call_chat_sync = lambda *a, **k: {"content": "行吧 你撤你的\n第二行不该出现"}
    check("只取第一行", bot_core._model_oneliner("测试") == "行吧 你撤你的")

    def boom(*a, **k):
        raise RuntimeError("模型挂了")

    bot_core._call_chat_sync = boom
    check("失败返回空串", bot_core._model_oneliner("测试") == "")
finally:
    bot_core._call_chat_sync = orig

# 配置键存在
check("撤回预设开关有默认", "recall_feedback_use_preset" in cfg)
check("退场预设开关有默认", "group_idle_exit_use_preset" in cfg)
check("会话闸开关有默认", "group_session_gate_enable" in cfg)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
