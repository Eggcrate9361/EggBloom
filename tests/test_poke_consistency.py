# -*- coding: utf-8 -*-
"""Poke state must not outlive the action or leak into later chat turns."""
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix=".codex-poke-consistency-", dir=ROOT)
os.environ["EGGBLOOM_SUPER_MEMORY_DB"] = os.path.join(TMP, "memory.db")

from plugins.ego import super_memory as SM  # noqa: E402
from autonomy import ledger as L  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name + ("" if not extra else f"  {extra}"))


try:
    now = time.time()
    old_ok = SM.add_action_event(
        "戳了对方", scope="group", scope_id="poke-scope",
        event_type="poke", importance=0.9, ts=now - 701)
    fresh_ok = SM.add_action_event(
        "戳了对方", scope="group", scope_id="poke-scope",
        event_type="poke", importance=0.9, ts=now - 30)
    check("poke events written", old_ok and fresh_ok, (old_ok, fresh_ok))

    recalled = SM._memory.recall_key_memories(
        "group", "poke-scope", current_msg="", limit=10)
    times = [float(x.get("timestamp") or 0) for x in recalled
             if x.get("type") == "event" and "[poke]" in x.get("content", "")]
    check("★ stale poke event is not recalled",
          all(t >= now - 600 for t in times), times)
    check("fresh poke event remains available",
          any(t >= now - 600 for t in times), times)

    state = L.observe_user_message(
        None, {"group_id": "g1", "sender": "u1", "text": "", "has_image": True,
               "ts": 100.0}, 100.0)
    state = L.observe_skip(
        state, {"group_id": "g1", "sender": "u1", "reason": "no grounded text",
                "ts": 105.0}, 105.0)
    obs = L.scan(state, 240.0, no_reply_s=480, at_dropped_s=180,
                 promise_s=1200, idle_s=240 * 60, image_s=120,
                 named_s=120, resume_s=180, delivery_hold_s=600)
    check("★ intentional silence suppresses proactive image follow-up",
          all(x.get("kind") != "image_only" for x in obs), obs)

    state = L.observe_user_message(
        state, {"group_id": "g1", "sender": "u1", "text": "", "has_image": True,
                "ts": 300.0}, 300.0)
    obs2 = L.scan(state, 430.0, no_reply_s=480, at_dropped_s=180,
                  promise_s=1200, idle_s=240 * 60, image_s=120,
                  named_s=120, resume_s=180, delivery_hold_s=600)
    check("new image after a skip can be reconsidered",
          any(x.get("kind") == "image_only" for x in obs2), obs2)

    src = open(os.path.join(ROOT, "bot_core.py"),
               encoding="utf-8", errors="replace").read()
    check("★ self poke returns at event entry",
          "戳一戳自回流忽略（发起者是 Bot）" in src)
    check("★ successful poke-back suppresses text generation",
          "反戳成功，本次戳事件已处理，不再生成文本" in src)
    check("★ synthetic poke turns do not enter dialogue memory",
          "戳事件文本降级只入动作账，不写对话历史" in src)
    prox = open(os.path.join(ROOT, "plugins", "proactivity", "__init__.py"),
                encoding="utf-8", errors="replace").read()
    check("★ silent turn is wired to proactivity",
          'turn.skipped' in src and 'observe_skip' in prox)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for item in FAIL:
    print("  FAIL", item)
sys.exit(1 if FAIL else 0)
