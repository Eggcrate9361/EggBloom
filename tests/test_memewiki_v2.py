# -*- coding: utf-8 -*-
"""Local meme understanding, risk filtering, injection and learning."""
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

TD = tempfile.mkdtemp(prefix=".codex-memewiki-v2-", dir=ROOT)
os.environ["EGGBLOOM_MEMEWIKI_DB"] = os.path.join(TD, "memewiki.db")

import plugins.memewiki as mw  # noqa: E402
from plugins.memewiki import store  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name + ("" if not extra else f"  {extra}"))


class Ctx:
    def __init__(self, cfg=None):
        self.cfg = {"meme_density": 70}
        self.cfg.update(cfg or {})
        self.logs = []

    def get_config(self, key=None, default=None):
        return self.cfg if key is None else self.cfg.get(key, default)

    def log(self, msg, level="info"):
        self.logs.append(str(msg))


try:
    memes = store.default()
    check("种子至少 50 条", len(memes._entries) >= 50, len(memes._entries))
    exact = memes.match("yyds", question=True)
    check("精确梗命中", exact and exact[0].word == "yyds", exact)
    alias = memes.match("永远的神太强了", question=False)
    check("别名命中", alias and alias[0].word == "yyds", alias)
    check("普通“确实”不误触发", memes.match("确实是这样", question=False) == [])
    check("单字“寄”不命中普通快递语境",
          memes.match("我去寄快递", question=False) == [])
    check("单字“唐”不命中普通历史语境",
          memes.match("唐朝历史真长", question=False) == [])
    check("问句“典是什么梗”能命中",
          any(x.word == "典" for x in memes.match("典是什么梗", question=True)))
    check("显式问我“前排”时不被停用词挡住",
          any(x.word == "前排" for x in memes.match("前排是什么梗", question=True)))
    t0 = time.perf_counter()
    for _ in range(200):
        memes.match("我今天真的有点绝绝子", question=False)
    avg_ms = (time.perf_counter() - t0) * 1000 / 200
    check("★ 本地匹配平均低于 5ms", avg_ms < 5.0, f"{avg_ms:.3f}ms")

    ctx = Ctx()
    mw._pending.clear()
    result = mw.on_before_reply(ctx, {
        "message": "我今天有点绝绝子了",
        "group_id": "1000000003",
        "sender": "1000000004",
        "scope": "group",
        "context": {"relation_tier": "好",
                    "mood": {"budget_shift": 0}},
    })
    text = (result or {}).get("content") or ""
    check("using a meme injects context",
          result and result.get("action") == "inject" and "绝绝子" in text, result)
    check("meme context does not force the group decider to reply",
          result and result.get("decision") is False, result)
    check("injection gives usage rather than dictionary only",
          "怎么用" in text and "不要科普" in text, text)
    check("injection <=300 chars", len(text) <= 300, len(text))

    explained = mw.on_before_reply(ctx, {
        "message": "绝绝子就是无语的意思",
        "group_id": "1000000003",
        "sender": "1000000004",
        "scope": "group",
        "context": {"relation_tier": "好"},
    })
    explained_text = (explained or {}).get("content") or ""
    check("★ 群友在解释梗时不要求重复",
          "懂了就行" in explained_text and "不用重复科普" in explained_text,
          explained_text)

    question = mw.on_before_reply(ctx, {
        "message": "绝绝子是什么梗",
        "group_id": "1000000003",
        "sender": "1000000004",
        "scope": "group",
        "context": {"relation_tier": "浅"},
    })
    qtext = (question or {}).get("content") or ""
    check("question asks for a direct natural answer",
          "直接答" in qtext and "不要编出处" in qtext, qtext)

    shallow = mw.on_before_reply(ctx, {
        "message": "你也太唐了",
        "group_id": "1000000009",
        "sender": "1000000008",
        "scope": "group",
        "context": {"relation_tier": "浅"},
    })
    check("★ 黑称在浅关系群不注入", shallow is None, shallow)
    close = mw.on_before_reply(ctx, {
        "message": "你也太唐了",
        "group_id": "1000000003",
        "sender": "1000000004",
        "scope": "group",
        "context": {"relation_tier": "特别好"},
    })
    check("★ 熟人先用了黑称才允许跟", close is not None, close)
    medium_risk = mw.on_before_reply(ctx, {
        "message": "你也太下头了",
        "group_id": "1000000010",
        "sender": "1000000011",
        "scope": "group",
        "context": {"relation_tier": "一般"},
    })
    check("风险 1 梗在一般关系里不注入", medium_risk is None, medium_risk)
    close_risk = mw.on_before_reply(ctx, {
        "message": "你也太下头了",
        "group_id": "1000000003",
        "sender": "1000000004",
        "scope": "group",
        "context": {"relation_tier": "好"},
    })
    check("风险 1 梗在熟人先用后可跟", close_risk is not None, close_risk)

    before = memes.group_stats("绝绝子", "1000000003")
    mw._pending["1000000003"] = {
        "items": [x.as_dict() for x in memes.match("绝绝子", question=False)],
        "ts": 0,
    }
    mw.on_after_reply(ctx, {
        "message": "绝绝子", "group_id": "1000000003",
        "sender": "1000000004", "scope": "group",
    }, "我也绝绝子了")
    attempted = memes.group_stats("绝绝子", "1000000003")
    check("★ 投递确认前不记本群使用",
          attempted["used"] == before["used"], (before, attempted))
    mw.on_event(ctx, "bot.replied", {
        "group_id": "1000000003", "text": "我也绝绝子了",
    })
    after = memes.group_stats("绝绝子", "1000000003")
    check("reply use records group usage",
          after["used"] == before["used"] + 1, (before, after))
    mw.on_event(ctx, "message.in", {
        "text": "哈哈 确实", "group_id": "1000000003",
        "sender": "1000000004",
    })
    reacted = memes.group_stats("绝绝子", "1000000003")
    check("positive reaction increases good score",
          reacted["good"] > after["good"], (after, reacted))
    memes.record_use("yyds", "1000000003")
    usage_ctx = mw.on_before_reply(ctx, {
        "message": "yyds太强了",
        "group_id": "1000000003",
        "sender": "1000000004",
        "scope": "group",
        "context": {"relation_tier": "好"},
    })
    check("★ 本群使用次数进入当轮上下文",
          usage_ctx is not None and "本群用过" in (usage_ctx.get("content") or ""),
          usage_ctx)

    left = memes.add_learn("甲乙", "测试排序甲", "甲乙就是测试排序甲",
                           "ranking")
    right = memes.add_learn("丙丁", "测试排序丙", "丙丁就是测试排序丙",
                            "ranking")
    check("ranking fixtures accepted",
          memes.review_learn(left, "accept")
          and memes.review_learn(right, "accept"))
    memes.record_use("甲乙", "rank-scope")
    memes.update_feedback("甲乙", "rank-scope", 2)
    memes.record_use("丙丁", "rank-scope")
    memes.update_feedback("丙丁", "rank-scope", -2)
    ranked = memes.match("甲乙丙丁", question=False, scope_id="rank-scope")
    check("★ 群反馈参与本地排序",
          bool(ranked) and ranked[0].word == "甲乙",
          [(x.word, x.score) for x in ranked])

    private_event = {
        "message": "我今天也绝绝子了",
        "group_id": "",
        "scope": "private",
        "context": {"sender": "private-user"},
    }
    private_injection = mw.on_before_reply(ctx, private_event)
    check("private injection available", private_injection is not None)
    private_before = memes.group_stats("绝绝子", "private-user")
    mw.on_after_reply(ctx, private_event, "那我也绝绝子了")
    mw.on_event(ctx, "bot.replied", {
        "group_id": "", "user_id": "private-user",
        "text": "那我也绝绝子了",
    })
    private_after = memes.group_stats("绝绝子", "private-user")
    check("★ 私聊以会话对象为反馈作用域",
          private_after["used"] == private_before["used"] + 1,
          (private_before, private_after))
    mw.on_event(ctx, "message.in", {
        "text": "我去听音乐了", "group_id": "",
        "sender": "private-user",
    })
    music = memes.group_stats("绝绝子", "private-user")
    check("“音乐”不会被误判成梗反馈", music["good"] == private_after["good"],
          (private_after, music))
    mw.on_event(ctx, "message.in", {
        "text": "听不懂", "group_id": "",
        "sender": "private-user",
    })
    one_bad = memes.group_stats("绝绝子", "private-user")
    mw.on_event(ctx, "message.in", {
        "text": "还是听不懂", "group_id": "",
        "sender": "private-user",
    })
    two_bad = memes.group_stats("绝绝子", "private-user")
    check("★ 连续两次负面反馈才降权",
          one_bad["good"] == private_after["good"]
          and two_bad["good"] == private_after["good"] - 1,
          (private_after, one_bad, two_bad))
    memes.record_use("绝绝子", "silent-user")
    mw._awaiting["silent-user"] = {
        "words": ["绝绝子"], "seen": 0, "bad": 0, "ts": time.time(),
    }
    silent_before = memes.group_stats("绝绝子", "silent-user")
    for _ in range(5):
        mw.on_event(ctx, "message.in", {
            "text": "说点别的", "group_id": "",
            "sender": "silent-user",
        })
    silent_after = memes.group_stats("绝绝子", "silent-user")
    check("连续五条没有接梗时降权",
          silent_after["good"] == silent_before["good"] - 1,
          (silent_before, silent_after))

    learned = memes.add_learn(
        "测试新梗", "表示非常震惊", "测试新梗就是表示非常震惊",
        "1000000003")
    check("explicit explanation becomes pending", bool(learned), learned)
    pending = memes.list_learn("pending")
    check("pending learn visible", any(x["word"] == "测试新梗" for x in pending), pending)
    check("review accepts pending learn",
          memes.review_learn(learned, "accept") and memes.get("测试新梗") is not None)
    check("accepted learn is locally queryable",
          "测试新梗" in mw.query("测试新梗"))

    orig_search = mw.wiki.search
    try:
        mw.wiki.search = lambda *a, **k: (_ for _ in ()).throw(
            mw.wiki.WikiError("offline"))
        offline = mw.act_status({})
    finally:
        mw.wiki.search = orig_search
    check("★ 上游离线时状态仍返回本地词库",
          offline.get("ok") and offline.get("local_count", 0) >= 50
          and offline.get("local_entries") == 1
          and offline.get("network_hit") is False,
          offline)

    hooks = mw.PLUGIN_HOOKS
    check("typed before_reply registered", callable(hooks.get("on_before_reply")))
    check("feedback after_reply registered", callable(hooks.get("on_after_reply")))
    check("learning event registered", callable(hooks.get("on_event")))
    check("manifest upgraded to ABI v2",
          mw.PLUGIN_MANIFEST.get("api_version") == 2
          and mw.PLUGIN_MANIFEST.get("version") == "2.0.0")
finally:
    store.reset_default()
    mw._pending.clear()
    mw._delivery.clear()
    mw._awaiting.clear()
    shutil.rmtree(TD, ignore_errors=True)

print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
for item in FAIL:
    print("  FAIL", item)
sys.exit(1 if FAIL else 0)
