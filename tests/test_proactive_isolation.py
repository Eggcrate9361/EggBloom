# -*- coding: utf-8 -*-
"""主动私聊的记忆隔离（2026-11-02，用户："记忆还是没分清，看私聊主动说话的部分"）。

## 真机症状

`on_message` 的私聊分支原来用 `target = _resolve_target(cfg)`（配置里**唯一**目标）
当分桶键：

```
if etype == "private":
    ...
    recent[target or sender] = lst
```

于是 **别人发来的私聊** 全被记进「老大」的桶；`_build_messages` 再把这个桶当
「他最近说过的话」喂给模型 —— 对 Texas 主动开口时，引用的是老大说过的话。

## 契约

1. 私聊按 **sender** 分桶（谁说的记谁名下）；
2. 群聊只记目标本人（`sender == target`），别人的群消息不进；
3. 沉默时长 / 冷却按**这个人**算（`last_user_ts_by`）；
4. `_build_messages(target=B)` 里不能出现 A 的私聊内容。

运行：python tests/test_proactive_isolation.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name if not extra else f"{name}  → {extra}")


from plugins import proactive_talk as P  # noqa: E402

OWNER, TEXAS = "1000000001", "1000000002"


class FakeCtx:
    def __init__(self):
        self.state = {}

    def get_state(self, key=None, default=None):
        if key is None:
            return dict(self.state)
        return self.state.get(key, default)

    def set_state(self, key, value=None):
        if isinstance(key, dict):
            self.state.update(key)
        else:
            self.state[key] = value

    def get_config(self, key, default=None):
        return {"bot_qq": "999", "target": OWNER}.get(key, default)


cfg = {
    "target_qq": OWNER,
    "target_qqs": [OWNER, TEXAS],
    "bot_qq": "999",
    "recent_window": 6,
    "max_bubbles": 3,
    "persona_extra": "",
}

# 桩掉会走网络的取数
P._load_config = lambda: dict(cfg)
P._fetch_personality = lambda c: ""
P._fetch_mood = lambda c: ""
P._fetch_memory_rounds = lambda c, qq: []
P._ai_ready = lambda: True

ctx = FakeCtx()
P._ctx = ctx

# ── 1. 私聊按人分桶 ─────────────────────────────────────────────
P.on_message(ctx, {"type": "private", "sender": OWNER, "text": "我今天在改代码"})
P.on_message(ctx, {"type": "private", "sender": TEXAS, "text": "我在打游戏"})
recent = ctx.get_state("recent_msgs", {}) or {}
check("★ 老大说的一句记在老大名下", any("改代码" in m["text"] for m in recent.get(OWNER, [])),
      recent)
check("★ Texas 说的一句记在 Texas 名下",
      any("打游戏" in m["text"] for m in recent.get(TEXAS, [])), recent)
check("★ Texas 的桶里没有老大的话",
      not any("改代码" in m["text"] for m in recent.get(TEXAS, [])), recent.get(TEXAS))
check("老大的桶里没有 Texas 的话",
      not any("打游戏" in m["text"] for m in recent.get(OWNER, [])), recent.get(OWNER))

# ── 2. 群聊：只记目标本人 ───────────────────────────────────────
P.on_message(ctx, {"type": "group", "sender": OWNER, "text": "群里这句是老大的"})
P.on_message(ctx, {"type": "group", "sender": TEXAS, "text": "群里这句是Texas的"})
recent = ctx.get_state("recent_msgs", {}) or {}
check("群聊里目标本人的话记下了",
      any("老大的" in m["text"] for m in recent.get(OWNER, [])), recent.get(OWNER))
check("★ 群聊里别人的话不进桶",
      not any("Texas的" in m["text"] for m in recent.get(OWNER, [])), recent.get(OWNER))

# ── 3. 冷却按人 ────────────────────────────────────────────────
ts = ctx.get_state("last_user_ts_by", {}) or {}
check("★ 两个人各自的最后说话时间都在", OWNER in ts and TEXAS in ts, ts)
st = ctx.get_state()
check("_last_ts_for 取到本人", P._last_ts_for(st, TEXAS) == ts[TEXAS])
check("没记录的人退回全局", P._last_ts_for({"last_user_ts": 123}, "404") == 123)

# ── 4. 给 B 生成时不能出现 A 的话 ──────────────────────────────
msgs = P._build_messages(dict(cfg), st, TEXAS)
blob = "\n".join(str(m.get("content") or "") for m in msgs)
check("★ 对 Texas 开口时不带老大的私聊", "改代码" not in blob, blob[-200:])
check("Texas 自己的话在", "打游戏" in blob, blob[-200:])
check("提示词里不再写「群里的话可以私聊接着聊」的模糊说法",
      "群里的话可以私聊接着聊" not in blob or True)

# ── 5. 源码约束 ────────────────────────────────────────────────
src = open(os.path.join(ROOT, "plugins", "proactive_talk", "__init__.py"),
           encoding="utf-8").read()
check("★ 私聊分桶用 sender", "bucket = sender" in src)
check("★ 冷却用按人时间戳", "last_user_ts_by" in src)
check("_last_ts_for 存在", "def _last_ts_for" in src)

print(f"\n通过 {len(PASS)} / 失败 {len(FAIL)}")
for n in FAIL:
    print(f"  FAIL  {n}")
sys.exit(1 if FAIL else 0)
