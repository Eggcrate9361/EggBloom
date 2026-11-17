# -*- coding: utf-8 -*-
"""group_chat_decider state integrity regression checks."""
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import plugins.group_chat_decider as G  # noqa: E402

TMP = tempfile.mkdtemp(prefix="group_decider_state_")
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name + ("" if not detail else f"  {detail}"))


try:
    db = G.GroupChatDB(os.path.join(TMP, "profiles.db"))
    db.update_group_profile("1000000003", reply_density=0.75)
    profile = db.get_group_profile("1000000003")
    check("★ 已有群画像能返回完整字段",
          profile.get("group_id") == "1000000003"
          and profile.get("reply_density") == 0.75, profile)

    G._get_ai_client = lambda: None
    G._decision_cache.clear()
    G._pending_reply.clear()
    G._last_reply_time.clear()
    G._bot_spoke_times.clear()
    now = time.time()
    base = {
        "recent_msgs": [(now - 3, "u1", "同一句话")],
        "bot_replies": [],
        "bot_qq": "10000",
        "bot_name": "小a",
        "group_id": "1000000003",
        "history": [],
        "message_id": "m-same",
    }
    G.before_reply(message="同一句话", group_id="1000000003",
                   context=dict(base, sender="1000000001", user_id="1000000001"))
    G.before_reply(message="同一句话", group_id="1000000003",
                   context=dict(base, sender="1000000002", user_id="1000000002"))
    senders = {key[4] for key in G._decision_cache}
    check("★ 相同文本不同发送者不会共用决策缓存",
          len(G._decision_cache) >= 2
          and {"1000000001", "1000000002"} <= senders,
          (len(G._decision_cache), senders))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for item in FAIL:
    print("  FAIL", item)
sys.exit(1 if FAIL else 0)
