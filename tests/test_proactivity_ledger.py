# -*- coding: utf-8 -*-
"""tests/test_proactivity_ledger.py —— 观测层（离线，注入时钟）

判据：
  [A] bot.replied 私聊带 user_id → 记到正确的会话，pending=True
  [B] 多气泡（同 trace_id）只算一轮等待，不是等两次
  [C] 对方回一句 → pending 清空、未兑现话头清空
  [D] 超时扫描：未回应 / 被@没回 / 未兑现话头各自只在条件成立时出现
  [E] dedupe_key 稳定且不同会话不串
  [F] 撤回是即时事件，不走超时
  [G] Ledger 持久化：重启后"我在等回复"还在
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

from harness.proactivity import ledger as L  # noqa: E402

PASS, FAIL = 0, 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def main():
    print("=== A) 私聊事件要能找到收件人 ===")
    t = 1000.0
    ev = {"scene": "private", "user_id": "1000000006", "text": "醒得挺早啊你",
          "ts": t, "trace_id": "tr-1"}
    s = L.observe_bot_reply(None, ev, t)
    check("key 是 private:QQ", s["key"] == "private:1000000006", s["key"])
    check("pending=True", s["pending"] is True, s)
    check("pending_since=发消息时间", s["pending_since"] == t, s["pending_since"])
    check("group_id 为空时不误记成群", s["scene"] == "private", s["scene"])

    g = L.observe_bot_reply(None, {"scene": "group", "group_id": "900000005",
                                   "text": "群里那句", "ts": t, "trace_id": "g1"}, t)
    check("群聊 key 独立", g["key"] == "group:900000005", g["key"])

    print("\n=== B) 多气泡是一轮 ===")
    s2 = L.observe_bot_reply(s, {"scene": "private", "user_id": "1000000006",
                                 "text": "我这刚处理完一堆破事",
                                 "ts": t + 2, "trace_id": "tr-1"}, t + 2)
    check("第二条气泡没重开一个等待窗口", s2["pending_since"] == t, s2["pending_since"])
    s3 = L.observe_bot_reply(s2, {"scene": "private", "user_id": "1000000006",
                                  "text": "另一轮", "ts": t + 200, "trace_id": "tr-2"}, t + 200)
    check("换 trace_id / 隔太久算新一轮", s3["pending_since"] == t + 200,
          s3["pending_since"])

    print("\n=== C) 对方回一句就结清 ===")
    s4 = L.observe_user_message(s3, {"sender": "1000000006", "text": "你怎么知道我醒来了",
                                     "ts": t + 210}, t + 210)
    check("pending=False", s4["pending"] is False, s4)
    check("last_user 记下了", s4["last_user_text"] == "你怎么知道我醒来了", s4)

    print("\n=== D) 超时扫描 ===")
    args = {"no_reply_s": 480.0, "at_dropped_s": 180.0, "promise_s": 1200.0}
    check("刚发完不触发", L.scan(s3, t + 210, **args) == [], L.scan(s3, t + 210, **args))
    obs = L.scan(s3, t + 700, **args)
    check("超时后出 no_reply", any(o["kind"] == "no_reply" for o in obs), obs)
    check("evidence 带原文", any("醒得挺早" in str(o["evidence"]) or
                                 "另一轮" in str(o["evidence"]) for o in obs), obs)

    at = L.observe_user_message(None, {"group_id": "", "sender": "1000000006",
                                       "text": "小a 在吗", "at_bot": True,
                                       "ts": t}, t)
    check("刚被@不触发（还没超时）", not any(o["kind"] == "at_dropped"
                                            for o in L.scan(at, t + 10, **args)),
          L.scan(at, t + 10, **args))
    at2 = L.scan(at, t + 200, **args)
    check("超时后出 at_dropped", any(o["kind"] == "at_dropped" for o in at2), at2)
    cleared = L.observe_bot_reply(at, {"scene": "private", "user_id": "1000000006",
                                       "text": "在", "ts": t + 20, "trace_id": "a1"}, t + 20)
    check("bot 回了就清掉被@的债",
          not any(o["kind"] == "at_dropped" for o in L.scan(cleared, t + 400, **args)),
          L.scan(cleared, t + 400, **args))

    print("\n=== D2) 明确投递失败 ≠ 没看见 ===")
    failed = L.observe_delivery(
        at, {"scene": "group", "target": "1000000006",
             "text": "我已经回了", "ts": t + 10}, t + 10, ok=False)
    held = L.scan(failed, t + 200, **dict(args, delivery_hold_s=600))
    check("★ 发送失败冷却期内不生成 at_dropped", held == [], held)
    check("失败标记带原文", failed.get("delivery_failed_text") == "我已经回了", failed)
    later = L.scan(failed, t + 700, **dict(args, delivery_hold_s=600))
    check("失败保留超过保护期后才允许重新判断",
          any(o["kind"] == "at_dropped" for o in later), later)
    delivered = L.observe_delivery(
        failed, {"scene": "group", "target": "1000000006",
                 "text": "我已经回了", "ts": t + 20}, t + 20, ok=True)
    check("★ 成功投递清除失败保护", not delivered.get("delivery_failed_ts"), delivered)

    pr = L.observe_bot_reply(None, {"scene": "private", "user_id": "1000000006",
                                    "text": "我等下发你那张图", "ts": t,
                                    "trace_id": "p1"}, t)
    check("识别出「等下发你」是未兑现话头", len(pr["promises"]) == 1, pr["promises"])
    pro = L.scan(pr, t + 1300, **args)
    check("超时后出 pending_topic", any(o["kind"] == "pending_topic" for o in pro), pro)
    settled = L.observe_user_message(pr, {"sender": "1000000006", "text": "收到",
                                          "ts": t + 60}, t + 60)
    check("对方接话后话头不再追", len(L.scan(settled, t + 1300, **args)) == 0,
          L.scan(settled, t + 1300, **args))

    print("\n=== E) dedupe 与会话隔离 ===")
    o1 = L.scan(s3, t + 700, **args)[0]
    o2 = L.scan(s3, t + 900, **args)[0]
    check("同一轮的 dedupe_key 稳定", o1["dedupe_key"] == o2["dedupe_key"],
          (o1["dedupe_key"], o2["dedupe_key"]))
    go = L.scan(g, t + 700, **args)
    check("群聊候选不会串到私聊 key", all("group:" in o["dedupe_key"] for o in go), go)

    print("\n=== F) 撤回即时 ===")
    r = L.recall_observation({"group_id": "", "operator": "1000000006",
                              "message_id": "123", "ts": t,
                              "notice_type": "friend_recall"}, t)
    check("返回 recall 候选", r and r["kind"] == "recall", r)
    check("dedupe 带 message_id", r and "123" in r["dedupe_key"], r)
    check("没有收件人时返回 None", L.recall_observation({"ts": t}, t) is None)

    print("\n=== H) ⑤ 冷场：合并了开口与戳一戳 ===")
    idle_args = dict(args, idle_s=14400.0)
    quiet = L.observe_user_message(None, {"sender": "1000000006", "text": "那我先下了",
                                          "ts": t}, t)
    check("刚说完不触发冷场", not any(o["kind"] == "idle_contact"
                                      for o in L.scan(quiet, t + 600, **idle_args)))
    idles = L.scan(quiet, t + 15000, **idle_args)
    check("静默 4 小时后产出 idle_contact",
          any(o["kind"] == "idle_contact" for o in idles), idles)
    io0 = next(o for o in idles if o["kind"] == "idle_contact")
    check("冷场优先级最低", io0["priority"] == 20, io0["priority"])
    check("evidence 带最后一句话", "那我先下了" in str(io0["evidence"]), io0["evidence"])
    check("与 no_reply 互斥（pending 时不产冷场）",
          not any(o["kind"] == "idle_contact" for o in L.scan(s3, t + 15000, **idle_args)),
          L.scan(s3, t + 15000, **idle_args))
    idles2 = L.scan(quiet, t + 16000, **idle_args)
    io1 = next(o for o in idles2 if o["kind"] == "idle_contact")
    check("同一小时内 dedupe_key 稳定（tick 不会重复戳）",
          io0["dedupe_key"] == io1["dedupe_key"],
          (io0["dedupe_key"], io1["dedupe_key"]))
    check("idle_s=0 时不启用", not any(o["kind"] == "idle_contact"
                                      for o in L.scan(quiet, t + 15000, **args)))

    print("\n=== G) 持久化 ===")
    d = tempfile.mkdtemp(prefix="pro_ledger_")
    path = os.path.join(d, "ledger.json")
    lg = L.Ledger(path)
    lg.put(s3)
    lg.mark_fired(o1["dedupe_key"], t + 700)
    check("save 成功", lg.save() is True)
    lg2 = L.Ledger(path)
    check("重启后状态还在", lg2.get("private", "1000000006")["pending"] is True,
          lg2.get("private", "1000000006"))
    check("重启后去重还在", lg2.already_fired(o1["dedupe_key"], 3600.0, t + 900) is True)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for x in FAILURES:
        print(f"  FAIL  {x}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
