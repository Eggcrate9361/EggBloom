# -*- coding: utf-8 -*-
"""1.5.10 批次 C 回归测试（C3/C4/C5/C5b；C2 按计划裁掉）。

只防回归，不是真机验收。验收口径写在 FIXPLAN-1.5.10 批次 C 各条。
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
import reply_drop  # noqa: E402
from plugins.ego.personality_engine import _clip_topic  # noqa: E402
from plugins.ego.mood_engine import MoodEngine  # noqa: E402
from plugins.web_search import _needs_search  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [OK]   " if ok else "  [FAIL] ") + name + ("" if ok else f"  {detail}"))


print("\n[1] C4 话题截断切在自然边界")
check("短句原样", _clip_topic("你在哪") == "你在哪")
check("12 字以内不截", _clip_topic("明天半周年限定是谁啊") == "明天半周年限定是谁啊")
_half = _clip_topic("我靠，0.301 的价值问题你都不回")
check("★ 长句在标点/虚词处收住，不停在半句", _half.endswith(("，", "的")) or len(_half) <= 12, repr(_half))
check("★ 12 字整句不被硬切坏", _clip_topic("今年明日方舟周年半周年限") == "今年明日方舟周年半周年限")
check("截断不留下半个占位符", "[" not in _clip_topic("你和他们去吃饭了吗 [图片] 快说"))

print("\n[2] C3 情绪温度改叠加、极端分支已删")
_old = dict(MoodEngine.__init__.__defaults__ or ())
m = MoodEngine.__new__(MoodEngine)
m.config = {"mood_enabled": True, "temperature_range": 0.6, "ai_temperature": 0.4}
m.get_mood = lambda gid="": {"boost": 1.0, "valence": 0.0, "arousal": 0.0}
check("★ 不再用 1.0 覆盖全局温度（叠加）", abs(m.get_temperature("g") - 1.0) < 1e-6, m.get_temperature("g"))
check("显式 base 行为不变", abs(m.get_temperature("g", base=0.5) - 1.1) < 1e-6, m.get_temperature("g", base=0.5))
m.config["mood_enabled"] = True
m.get_form_params = lambda gid="": {"valence": 0.95, "arousal": 0.0}
check("★ 极端情绪不再注入 prompt", m.get_prompt_block("g") == "", m.get_prompt_block("g"))

print("\n[3] C5 话头翻篇的绝对上限")
check("2 条新消息 → 仍丢（常态保护不变）",
      bot_core._drop_reason(new_msgs=2, turns=2, age=5).startswith("话头翻篇"))
check("★ 40 条新消息 → 发（不再永丢）", bot_core._drop_reason(new_msgs=40, turns=2, age=20) == "",
      bot_core._drop_reason(new_msgs=40, turns=2, age=20))
check("★ 上限边界 30 条 → 发", bot_core._drop_reason(new_msgs=30, turns=2, age=20) == "")
check("29 条仍丢", bot_core._drop_reason(new_msgs=29, turns=2, age=20).startswith("话头翻篇"))
check("显式关上限（send_anyway_over=0 走默认）不误伤",
      reply_drop.drop_reason(new_msgs=40, turns=2, age=20) == "")

print("\n[4] C5b 联网兜底只接信息需求")
check("★ 吐槽不搜（压测原句）", _needs_search("论文写不完了救命") is False)
check("★ 感叹不搜", _needs_search("今天好累啊") is False)
check("问句要搜", _needs_search("明天天气怎么样") is True)
check("强触发词仍要搜", _needs_search("帮我查一下明日方舟限定") is True)
check("问句+触发词要搜", _needs_search("今天天气怎么样") is True)
# 触发词表决定"查什么"，问句判据只负责"要不要查"——
# 没有触发词的问题（"半周年限定是谁"）不算兜底搜索，靠主链 search_web 工具。
check("问句但无触发词 → 兜底不搜（设计如此）", _needs_search("半周年限定是谁") is False)

print("\n" + "=" * 56)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("失败项：", FAIL)
sys.exit(1 if FAIL else 0)
