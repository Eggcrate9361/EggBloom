# -*- coding: utf-8 -*-
"""撤回反馈只有一个负责人，旧预置文案按完整匹配迁移。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import recall_contract as C  # noqa: E402
import config  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name + ("" if not extra else f"  {extra}"))


check("core 只归主链", C.core_owns("core") and not C.plugin_owns("core")
      and not C.proactivity_owns("core"))
check("plugin 只归插件", C.plugin_owns("plugin") and not C.core_owns("plugin")
      and not C.proactivity_owns("plugin"))
check("proactivity 只归主动性",
      C.proactivity_owns("proactivity") and not C.core_owns("proactivity")
      and not C.plugin_owns("proactivity"))
check("缺省和未知值回落主链",
      C.core_owns("") and C.core_owns(None) and C.core_owns("typo"))

cfg = {
    "recall_feedback_pre_messages": [
        {"text": x, "disabled": False} for x in C.LEGACY_TRUNCATED_PRESET
    ],
    "recall_feedback_post_messages": list(C.SEEN_PRESET),
    "recall_private_post_messages": ["我自己写的撤回话"],
}
C.migrate_legacy_copy(cfg, set(cfg))
check("★ 完整等于旧预置时迁移到新文案",
      tuple(cfg["recall_feedback_pre_messages"]) == C.TRUNCATED_PRESET,
      cfg["recall_feedback_pre_messages"])
check("★ 已经是新文案时保持不变",
      tuple(cfg["recall_feedback_post_messages"]) == C.SEEN_PRESET)
check("★ 用户自定义列表不迁移",
      cfg["recall_private_post_messages"] == ["我自己写的撤回话"],
      cfg["recall_private_post_messages"])

harsh = ("心虚", "别撤回", "撤晚了", "算你识相", "别费劲")
all_copy = C.TRUNCATED_PRESET + C.SEEN_PRESET + C.PRIVATE_PRESET
check("★ 默认文案不含施压/挑衅词",
      not any(word in text for text in all_copy for word in harsh), all_copy)
check("★ DEFAULT_CONFIG 使用共享文案",
      tuple(config.DEFAULT_CONFIG["recall_feedback_pre_messages"]) == C.TRUNCATED_PRESET
      and tuple(config.DEFAULT_CONFIG["recall_feedback_post_messages"]) == C.SEEN_PRESET
      and tuple(config.DEFAULT_CONFIG["recall_private_post_messages"]) == C.PRIVATE_PRESET)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for item in FAIL:
    print("  FAIL", item)
sys.exit(1 if FAIL else 0)
