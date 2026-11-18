# -*- coding: utf-8 -*-
"""Contracts for removing cue-card prompting, image narration and memory noise."""
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp(prefix=".codex-humanness-", dir=ROOT)
os.environ["EGGBLOOM_SUPER_MEMORY_DB"] = os.path.join(TMP, "super_memory.db")
os.environ["EGGBLOOM_CHAT_STYLE_DB"] = os.path.join(TMP, "chat_style.db")

import de_ai  # noqa: E402
import self_actions as sa  # noqa: E402
from plugins.ego import chat_style as cs  # noqa: E402
from plugins.ego import personality_engine as pe  # noqa: E402
from plugins.ego import super_memory as sm  # noqa: E402
from plugins.group_chat_decider import decider as legacy_decider  # noqa: E402
from plugins.group_chat_decider import DecisionV3  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name + ("" if not extra else f"  {extra}"))


decision = DecisionV3(
    atmosphere="轻松", strategy="先反问再吐槽",
    strategy_detail="第一句反问，第二句吐槽",
    suggested_tone="毒舌", hook_points=["反问干嘛", "假装生气"],
    topic="游戏", topic_continuation=True, emotion="happy",
)
block = decision.to_prompt_injection()
check("live decider keeps objective atmosphere", "当前氛围：轻松" in block, block)
check("live decider drops strategy", "怎么接" not in block and "反问再吐槽" not in block, block)
check("live decider drops hook points", "可以接的点" not in block and "假装生气" not in block, block)

legacy = legacy_decider.Decision(atmosphere="安静", strategy="不要回复")
legacy_block = legacy.to_prompt_injection()
check("legacy decider uses the same fact-only contract",
      "怎么接" not in legacy_block and "当前氛围：安静" in legacy_block,
      legacy_block)

rules = de_ai.de_ai_rule_block({})
check("de-AI rules prohibit image narration",
      "禁止当旁白复述画面" in rules and "直接给自己的反应" in rules, rules)
persona = open(os.path.join(ROOT, "plugins", "personality.txt"),
               encoding="utf-8").read()
check("personality also carries image reaction rule",
      "【看图别报菜名】" in persona and "直接给反应" in persona)

src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8",
           errors="replace").read()
check("visual prompt preserves quoted lines",
      "原样摘录最核心的一句" in src and "不要改写成新闻摘要" in src)
check("video frames use timeline labels",
      "[视频{_stage}]" in src and "_stage = (\"开头\", \"前段\", \"中段\", \"后段\")" in src)
check("video frames no longer leak photo numbering", "[第{_i + 1}/{len(_sheet_list)}张]" not in src)

check("collect is not long-term action memory", "collect" not in sa.PERSIST_KINDS)
check("collect is suppressed from self-action prompt", "collect" in sa.QUIET_KINDS)
check("collect is suppressed from super-memory prompt",
      "collect" in sm.PROMPT_HIDDEN_EVENT_TYPES, sm.PROMPT_HIDDEN_EVENT_TYPES)
sm.add_action_event("把群里的一张图收进了表情库：得意", scope="group",
                    scope_id="noise-scope", event_type="collect", importance=0.9)
collect_recall = sm._memory.recall_key_memories(
    "group", "noise-scope", current_msg="", limit=10)
check("collect event is filtered at recall",
      all("[collect]" not in x.get("content", "") for x in collect_recall),
      collect_recall)

now = time.time()
check("style filter rejects image placeholders",
      not cs._style_value_usable("图片")
      and not cs._style_value_usable("[图片")
      and not cs._style_value_usable("都没取到画面"))
check("style filter keeps real wording", cs._style_value_usable("确实是这样"))

state_group = pe._get_state_id("u1", "g1", {"sender": "u1"})
state_private = pe._get_state_id("u1", "", {"sender": "u1"})
check("group and private conversation states are isolated",
      state_group != state_private and state_group.startswith("group_")
      and state_private.startswith("private_"), (state_group, state_private))
state = pe.ConversationState("__humanness_topic_test__")
check("visual system block is not a topic",
      state._extract_topic("【视觉识别 · 群聊图片内容】\n- 一张猫图") == "",
      state._extract_topic("【视觉识别 · 群聊图片内容】\n- 一张猫图"))
state._path = lambda: os.path.join(ROOT, ".tmp-humanness-state.json")
try:
    state.update("今天去吃饭吗", "")
finally:
    try:
        os.remove(state._path())
    except OSError:
        pass
shutil.rmtree(TMP, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for item in FAIL:
    print("  FAIL", item)
sys.exit(1 if FAIL else 0)
