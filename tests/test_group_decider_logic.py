# -*- coding: utf-8 -*-
"""群聊决策重写（2026-10-10）端到端仿真。
复现沙漏日志里的真实场景 + 全部新闸门，不发任何真实 AI 请求。"""
import os
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import plugins.group_chat_decider as G
G._get_ai_client = lambda: None   # 确定性：走规则兜底路径，不发真实请求

from plugins.group_chat_decider import (
    before_reply, after_reply, decide,
    _last_reply_time, _last_reply_target, _pending_reply,
    _bot_spoke_times, _decision_cache,
)

BOT = "10000"
U1 = "10003"   # 示例用户 用户
U2 = "10000005"   # 占位群友C
U3 = "10000003"   # 占位昵称
GID = "20001"

def fresh():
    _last_reply_time.clear(); _last_reply_target.clear()
    _pending_reply.clear(); _bot_spoke_times.clear(); _decision_cache.clear()

def mk_ctx(msgs, sender=U1, **flags):
    c = {"recent_msgs": list(msgs), "bot_replies": [], "bot_qq": BOT, "bot_name": "小a",
         "sender": sender, "user_id": sender, "history": [], "group_id": GID}
    c.update(flags)
    return c

def run(msg, ctx):
    r = before_reply(message=msg, group_id=GID, context=ctx)
    return (r or {}).get("action", "None(放行)"), str((r or {}).get("reason", ""))[:60]

class OneOf(tuple):
    """表示"可接受的一组值"（普通 tuple 仍是"期望值"，别混）。"""

results = []
# 2026-10-13：不插话 = skip 或 observe（后者是"过会儿再看一眼"，见 observe_before_silence_seconds）
NOT_SPEAKING = OneOf(("skip", "observe"))

def check(name, got, want):
    ok = (got in want) if isinstance(want, OneOf) else (got == want)
    results.append((name, ok, got, want))
    print(("✓" if ok else "✗"), name, "→", got, ("" if ok else f"(期望 {want})"))

# ══ 场景 0：正常问答（用户直接问 Bot，无触发标志但消息点名"你"）══
fresh(); now = time.time()
msgs0 = [(now-120, U1, "你能评价一下某市那个铁路学院吗？")]
a, why = run(msgs0[0][2], mk_ctx(msgs0))
# 无 is_at_bot、无"小a"称呼 → _bot_asked=False；软信号问句 + AI 不可用 → 规则兜底放行
check("0 普通问句·AI不可用兜底", a, "inject")

# ══ 场景 1：Bot 刚回复过（真实发出），非对话者插问 → 冷却 skip ══
fresh(); now = time.time()
_last_reply_time[GID] = now - 30            # 30 秒前真实发出
_last_reply_target[GID] = (U1, now - 30)    # 当时在回 U1
msgs1 = [(now-40, U1, "铁路学院咋样"), (now-30, BOT, "还行吧专科能打"), (now-5, U2, "你觉得南华好还是铁路好？")]
a, why = run("你觉得南华好还是铁路好？", mk_ctx(msgs1, sender=U2))
check("1 冷却期·非对话者", a, NOT_SPEAKING)

# ══ 场景 2：对话者本人追问 → 豁免放行（治"接不上话"）══
fresh(); now = time.time()
_last_reply_time[GID] = now - 26
_last_reply_target[GID] = (U1, now - 26)
msgs2 = [(now-40, U1, "铁路学院咋样"), (now-26, BOT, "还行吧专科能打"), (now-2, U1, "那你觉得南华大学好还是铁路学院好？")]
a, why = run(msgs2[-1][2], mk_ctx(msgs2, sender=U1))
check("2 对话者追问·豁免", a, "inject")

# ══ 场景 3：决策已放行、回复还在生成（预占），又来消息 → skip（连答两次根路径）══
fresh(); now = time.time()
_pending_reply[GID] = now - 8               # 8 秒前刚决策放行，回复还在生成
msgs3 = [(now-10, U1, "铁路学院咋样"), (now-8, U1, "再说点")]
a, why = run("再说点", mk_ctx(msgs3, sender=U2))
check("3 预占期·非对话者", a, NOT_SPEAKING)

# ══ 场景 4：防刷屏闸——30 秒窗口内 Bot 已发 3 条 → skip ══
fresh(); now = time.time()
_bot_spoke_times[GID] = [now-5, now-15, now-25]
_last_reply_time[GID] = now-5
_last_reply_target[GID] = (U3, now-5)
msgs4 = [(now-6, U3, "哈哈"), (now-1, U3, "确实")]
a, why = run("确实", mk_ctx(msgs4, sender=U3))
check("4 防刷屏上限", a, NOT_SPEAKING)

# ══ 场景 5：消息点名 Bot → 硬放行（即使非问句）══
fresh(); now = time.time()
msgs5 = [(now-5, U1, "小a 你看看这个图")]
a, why = run("小a 你看看这个图", mk_ctx(msgs5))
check("5 点名Bot·硬放行", a, "inject")

# ══ 场景 6：用户之间互聊（非问句、无点名）→ AI 不可用兜底静默 ══
fresh(); now = time.time()
msgs6 = [(now-60, U1, "我昨天去网吧了"), (now-40, U2, "充了多少"), (now-20, U1, "冲30送30"), (now-3, U2, "晚上开黑不")]
a, why = run("晚上开黑不", mk_ctx(msgs6, sender=U2))
check("6 用户互聊不插嘴", a, NOT_SPEAKING)

# ══ 场景 7：纯无意义 → skip ══
fresh(); now = time.time()
msgs7 = [(now-3, U1, "……")]
a, why = run("……", mk_ctx(msgs7))
check("7 无意义", a, "skip")   # 无意义连观察都不排

# ══ 场景 8：同消息二次派发（主链双 before_reply）→ 缓存复用，结果一致且不重跑 ══
fresh(); now = time.time()
msgs8 = [(now-3, U1, "小a 在吗？")]
ctx8 = mk_ctx(msgs8)
r1 = before_reply(message=msgs8[-1][2], group_id=GID, context=ctx8)
n_before = len(_decision_cache)
r2 = before_reply(message=msgs8[-1][2], group_id=GID, context=ctx8)
a2 = (r2 or {}).get("action", "None(放行)")
check("8 二次派发缓存复用", a2, "inject")

# ══ 场景 9：after_reply 时钟统一——发出后 _last_reply_time/pending 清账 ══
fresh(); now = time.time()
_pending_reply[GID] = now - 5
after_reply(message="铁路学院咋样", group_id=GID, reply="还行吧", context=mk_ctx([(now-5, U1, "铁路学院咋样")]))
pend_cleared = GID not in _pending_reply
lr_fresh = abs(_last_reply_time.get(GID, 0) - now) < 2
tgt_ok = _last_reply_target.get(GID, ("",0))[0] == U1
check("9 发出后时钟三件套", (pend_cleared, lr_fresh, tgt_ok), (True, True, True))

print("\n===== 汇总 =====")
bad = [r for r in results if not r[1]]
if bad:
    for n in bad: print("FAIL:", n[0], "got", n[2], "want", n[3])
    sys.exit(1)
print(f"全部 {len(results)} 个场景通过 ✓")
