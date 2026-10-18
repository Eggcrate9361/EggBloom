# -*- coding: utf-8 -*-
"""tests/test_decider_pacing.py —— 节奏：接话窗口 / 潜水前观察 / 划走权

用户 2026-10-13 认可 qq-bridge 的「等、回与沉睡前观察」思路，要求**接入决策插件**。
它解决的是我们最不像人的三件事：
  · 回完就结束 → 群友追问要等下一轮才可能被接上；
  · 判了不接就彻底消失 → 话题转向我们时已经没戏了；
  · 缓冲里的历史被当成待办清单 → 逐条点评、总结群聊。

本测试锁死三条：
  ① 接话窗口：after_reply 真回过之后的一段时间内，同一个人在追问 → 直接放行；
  ② 潜水前观察：判"不接"时返回 action=observe（宿主过 N 秒再看一眼），
     observe_retry 时不再排第二次观察（不会轮询）；
  ③ 划走权：prompt 里明确"只有 [当前] 是对你说的，历史可以划走"。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core  # noqa: E402
import plugins.group_chat_decider as G  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []
GID = "_test_pacing_"
BOT = "10000"
U1 = "10001"
U2 = "10002"


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name} :: {extra}")
        print(f"  [FAIL] {name} :: {extra}")


def fresh():
    G._followup_until.clear()
    G._last_reply_target.clear()
    G._last_reply_time.clear()
    G._pending_reply.clear()
    G._bot_spoke_times.clear()
    G._decision_cache.clear()


def ctx(sender=U1, observe_retry=False, **flags):
    c = {"recent_msgs": [], "bot_replies": [], "bot_qq": BOT, "bot_name": "小a",
         "bot_nicknames": ["小a"], "sender": sender, "user_id": sender,
         "history": [], "group_id": GID, "is_at_bot": False, "has_xiao_a": False,
         "has_bot_nick": False, "has_quote": False, "has_text_at": False,
         "in_session": False, "observe_retry": observe_retry}
    c.update(flags)
    return c


class StubAI:
    """会正常决策的桩 AI：返回"这轮不插话"（观察用例要走**正常价值判断**那条路，
    不能用降级路径 —— 降级本来就该直接 skip、不排观察）。"""

    def __init__(self, reply=False):
        self.reply = reply

    def call(self, messages, tools=None, stream=False, max_tokens=300):
        import json as _j
        return (_j.dumps({
            "should_reply": self.reply, "reply": self.reply,
            "reason": "群里在聊别的，我先看着",
            "atmosphere": "闲聊", "atmosphere_confidence": 0.8,
            "strategy": "潜水", "strategy_detail": "先看着",
        }, ensure_ascii=False), None, None)


def main():
    orig_client = G._get_ai_client
    G._get_ai_client = lambda: None      # 默认不发真实请求
    try:
        # ── ① 接话窗口 ──
        print("=== ① 接话窗口（回完等追问）===")
        fresh()
        # 模拟"我们刚回过 U1"
        G._last_reply_target[GID] = (U1, time.time())
        G._last_reply_time[GID] = time.time()
        G._followup_until[GID] = time.time() + 45
        d = G.decide("为什么啊", GID, ctx(U1))
        check("同一个人短追问 → 直接放行", bool(d and d.should_reply), str(d)[:90] if d else "None")
        check("理由标成[接话]", bool(d and "[接话]" in d.reason), d.reason if d else "")
        check("窗口用掉即收（不会一直开着）", G._followup_until.get(GID, 0) == 0,
              str(G._followup_until.get(GID)))

        fresh()
        G._last_reply_target[GID] = (U1, time.time())
        G._followup_until[GID] = time.time() + 45
        d2 = G.decide("小a在吗", GID, ctx(U1))
        check("叫名字也算追问 → 放行", bool(d2 and d2.should_reply), str(d2)[:90] if d2 else "None")

        fresh()
        G._last_reply_target[GID] = (U2, time.time())     # 我们刚回的是 U2
        G._followup_until[GID] = time.time() + 45
        d3 = G.decide("为什么啊", GID, ctx(U1))
        check("不是刚回的那位 → 不走接话窗口", not (d3 and "[接话]" in d3.reason),
              d3.reason if d3 else "None")

        fresh()
        G._last_reply_target[GID] = (U1, time.time())
        G._followup_until[GID] = time.time() - 1            # 窗口已过
        d4 = G.decide("为什么啊", GID, ctx(U1))
        check("窗口过期 → 不再走接话", not (d4 and "[接话]" in d4.reason),
              d4.reason if d4 else "None")

        # after_reply 应开窗
        fresh()
        G.after_reply(message="我在的", group_id=GID, reply="在的呀",
                      context={"recent_msgs": [(time.time(), U1, "在吗")], "bot_qq": BOT})
        check("★ after_reply 真回过 → 自动开接话窗口", G._followup_until.get(GID, 0) > time.time(),
              str(G._followup_until.get(GID)))

        # ── ② 潜水前观察 ──
        print("\n=== ② 潜水前观察（判不接 → 过会儿再看一眼）===")
        fresh()
        G._get_ai_client = lambda: StubAI(reply=False)      # 正常决策：这轮不插话
        r = G.before_reply(message="今天群里好热闹", group_id=GID, context=ctx(U2))
        act = (r or {}).get("action")
        check("★ 不插话时返回 observe 而不是直接 skip", act == "observe", f"{act} / {str(r)[:110]}")
        check("observe 带 after 秒数", int((r or {}).get("after") or 0) > 0, str((r or {}).get("after")))
        # 关键场景：**同一段文本**的二次决策（若缓存键不区分 observe_retry，
        # 这里会命中第一次的 observe → 宿主再排一次 → 无限循环）
        r2 = G.before_reply(message="今天群里好热闹", group_id=GID, context=ctx(U2, observe_retry=True))
        check("★ observe_retry 不再排观察（不会轮询）", (r2 or {}).get("action") != "observe",
              str((r2 or {}).get("action")))
        # 无意义内容不观察（不值得等）
        fresh()
        G._get_ai_client = lambda: StubAI(reply=False)
        r3 = G.before_reply(message="嗯", group_id=GID, context=ctx(U2))
        check("纯无意义内容不排观察", (r3 or {}).get("action") == "skip", str((r3 or {}).get("action")))

        # ── ③ 划走权（prompt）──
        print("\n=== ③ 划走权写进 prompt ===")
        pr = G._build_ai_prompt("刚才那条", ctx(U1), {"topic": "闲聊", "conversation_chain": "isolated"})
        check("prompt 明确只有[当前]是对你说的", "[当前]" in pr and "背景" in pr, pr[-260:])
        check("prompt 禁止逐条点评/总结", ("逐条点评" in pr) and ("总结" in pr), pr[-260:])

        # ── ④ 宿主侧：观察期间有新消息就作废 ──
        print("\n=== ④ 宿主：观察期间有新消息 → 观察作废 ===")
        bot_core.GROUP_OBSERVE.clear()
        with bot_core._buffer_lock:
            bot_core.GROUP_MSG_BUFFER.clear()
        bot_core._schedule_observe(GID, "[某人] 闲聊", 300, "测试")   # 长窗口，不真等
        check("观察已登记", GID in bot_core.GROUP_OBSERVE, str(list(bot_core.GROUP_OBSERVE)))
        with bot_core._buffer_lock:                      # 模拟窗口期内来了新消息
            bot_core.GROUP_MSG_BUFFER[GID] = {"text": "[某人] 新消息", "timer": None}
        bot_core._observe_second_look(GID)
        check("有新消息 → 观察被清掉且不重复处理", GID not in bot_core.GROUP_OBSERVE,
              str(list(bot_core.GROUP_OBSERVE)))
        with bot_core._buffer_lock:
            bot_core.GROUP_MSG_BUFFER.clear()
        # 降级路径（AI 客户端缺失）不该排观察：重看一遍也还是坏的决定器
        fresh()
        G._get_ai_client = lambda: None
        r4 = G.before_reply(message="随便聊聊", group_id=GID, context=ctx(U2))
        check("决策器降级时不再排观察", (r4 or {}).get("action") == "skip",
              f"{(r4 or {}).get('action')} / {str((r4 or {}).get('reason'))[:60]}")

        # 无人说话时：观察会重新过一次决策（这里只验证缓冲被重建 + observe_retry 标记）
        bot_core._schedule_observe(GID, "[某人] 闲聊", 300, "测试")
        rec = bot_core.GROUP_OBSERVE.pop(GID, None)
        check("无人说话 → 保留原文用于二次决策", bool(rec and rec.get("text")), str(rec)[:80])
        bot_core.GROUP_OBSERVE.clear()
    finally:
        G._get_ai_client = orig_client

    print("\n" + "=" * 58)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
