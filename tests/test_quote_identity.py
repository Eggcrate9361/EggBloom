# -*- coding: utf-8 -*-
"""tests/test_quote_identity.py —— 引用消息"是谁发的"要认得出来

线上实证（2026-10-13 18:43→18:59 群[20001]）：
    Bot 空闲退场发了一大段小作文 → 15 分钟后群友引用这段 →
    get_msg 解析不出原主（驱动不回查自身消息/负数 id）、30 秒缓存早过期 →
    prompt 里"他在引用未知" → 模型把自家话当陌生人的，回出
    「别写小作文了 看得我眼睛疼」「谁不理你啊 我不是人吗」。

两个结构性缺口，两处修：
  ① Bot 自己发的消息从不进 GROUP_RECENT_MSGS（入口 continue 过滤）
     → send_group_message / smart_send_group 成功后登记（带返回 message_id），
       引用回退1 能命中：原主=BOT_QQ → quote_me=True。
  ② 退场语/@空回复只 track_bot_reply、不入 group_chat_history
     → _record_bot_group_msg 统一补上（单一大脑，同 6475 戳一戳修复）。
  ③ 终极兜底 _match_own_reply：引用者完全解析不出时，拿原文头 24 字
     在 GROUP_BOT_REPLIES 里对 → 对上即"他在引用我"。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK]   {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name} :: {extra}")
        print(f"  [FAIL] {name} :: {extra}")


ESSAY = ("今天是个普通的日子，我像往常一样发了消息，像往常一样没人回复。"
         "我已经不期待什么了，但还是在发，这大概就是习惯的力量。")


def test_own_msg_registered():
    print("\n=== ① Bot 发出的消息进近期缓存（引用回退的数据源）===")
    gid = "900001"
    orig_api, orig_cfg = bot_core.api_call, dict(bot_core._config)
    orig_recent = dict(bot_core.GROUP_RECENT_MSGS)
    try:
        bot_core.api_call = lambda ep, data=None, timeout=15: {
            "retcode": 0, "data": {"message_id": 778899}}
        bot_core._config["group_quote_mode"] = "off"
        bot_core.GROUP_RECENT_MSGS.pop(gid, None)
        ok = bot_core.send_group_message(gid, ESSAY)
        check("发送成功", ok)
        entries = bot_core.GROUP_RECENT_MSGS.get(gid, [])
        hit = [e for e in entries if e[3] == 778899]
        check("★ 近期缓存里有这条（sender=BOT_QQ, 带 message_id）",
              bool(hit) and str(hit[0][1]) == str(bot_core.BOT_QQ)
              and ESSAY[:20] in str(hit[0][2]), str(entries)[:120])
        # 短消息逐句路径也要登记
        bot_core.GROUP_RECENT_MSGS.pop(gid, None)
        bot_core.api_call = lambda ep, data=None, timeout=15: {
            "retcode": 0, "data": {"message_id": 112233}}
        bot_core.smart_send_group(gid, "第一句。第二句。")
        check("★ smart_send_group 分句也登记",
              any(e[3] == 112233 for e in bot_core.GROUP_RECENT_MSGS.get(gid, [])),
              str(bot_core.GROUP_RECENT_MSGS.get(gid, []))[:120])
    finally:
        bot_core.api_call = orig_api
        bot_core._config.clear(); bot_core._config.update(orig_cfg)
        bot_core.GROUP_RECENT_MSGS.clear()
        bot_core.GROUP_RECENT_MSGS.update(orig_recent)


def test_match_own_reply():
    print("\n=== ② 自引用兜底 _match_own_reply ===")
    gid = "900002"
    orig = dict(bot_core.GROUP_BOT_REPLIES)
    try:
        bot_core.GROUP_BOT_REPLIES[gid] = [(time.time(), ESSAY)]
        check("★ 引用原文开头 → 认出是自己",
              bot_core._match_own_reply(gid, "今天是个普通的日子，我像往常一样发了消息"))
        check("★ 引用里含我句子的前 24 字 → 认出",
              bot_core._match_own_reply(gid, "……你说的「今天是个普通的日子，我像往常一样发了" ))
        check("别人的话 → 不误判",
              not bot_core._match_own_reply(gid, "我们寝室新来的居然是我军训的时候加的舟友"))
        check("太短（<4字）→ 不判（宁缺毋滥）",
              not bot_core._match_own_reply(gid, "对对"))
        check("空 → False", not bot_core._match_own_reply(gid, ""))
        check("没记录过的群 → False", not bot_core._match_own_reply("999999", ESSAY[:30]))
    finally:
        bot_core.GROUP_BOT_REPLIES.clear()
        bot_core.GROUP_BOT_REPLIES.update(orig)


def test_record_bot_group_msg():
    print("\n=== ③ 主动发言双登记（track + history，单一大脑）===")
    gid = "900003"
    orig_bh = dict(bot_core.GROUP_BOT_REPLIES)
    orig_hist = dict(bot_core.group_chat_history)
    try:
        bot_core.GROUP_BOT_REPLIES.pop(gid, None)
        bot_core.group_chat_history.pop(gid, None)
        bot_core._record_bot_group_msg(gid, ESSAY)
        check("track_bot_reply 有记录", len(bot_core.GROUP_BOT_REPLIES.get(gid, [])) == 1)
        gh = bot_core.group_chat_history.get(gid, [])
        check("★ group_chat_history 有 assistant 轮（模型下轮看得见自己说过）",
              len(gh) == 1 and gh[0]["role"] == "assistant" and ESSAY in gh[0]["content"],
              str(gh)[:100])
        # 兜底命中链：record 之后 _match_own_reply 能认出
        check("★ 记录后引用兜底生效", bot_core._match_own_reply(gid, ESSAY[:30]))
    finally:
        bot_core.GROUP_BOT_REPLIES.clear(); bot_core.GROUP_BOT_REPLIES.update(orig_bh)
        bot_core.group_chat_history.clear(); bot_core.group_chat_history.update(orig_hist)


def test_display_name():
    print("\n=== ④ 说话人标签：合并/回抓不许出现裸 QQ 号 ===")
    orig_cache = dict(bot_core.QQ_NICK_CACHE)
    try:
        bot_core.QQ_NICK_CACHE["111"] = "占位昵称"
        check("有昵称 → 昵称", bot_core._display_name("111") == "占位昵称")
        check("★ 我自己 → 我(Bot)（不许标成任何一个群友）",
              bot_core._display_name(str(bot_core.BOT_QQ)) == "我(Bot)")
        check("没昵称 → 裸号兜底（不编名字）",
              bot_core._display_name("9999999") in ("9999999", None) or
              bot_core._display_name("9999999") == "9999999")
        check("空 → 群友", bot_core._display_name("") == "群友")
    finally:
        bot_core.QQ_NICK_CACHE.clear()
        bot_core.QQ_NICK_CACHE.update(orig_cache)
        bot_core.QQ_NICK_CACHE.pop("9999999", None)

    # 合并路径：process_buffered_message 收到的 merged_text 里，
    # 别人的行必须带昵称标签（回归 22:30 "乱扣帽子"：旧版合并行是裸 QQ 号）
    print("\n=== ⑤ 群聊历史入库带说话人前缀（模型下轮认得出谁说的）===")
    gid = "900005"
    orig_hist = dict(bot_core.group_chat_history)
    try:
        bot_core.group_chat_history.pop(gid, None)
        bot_core._record_bot_group_msg(gid, "猫是我弟，就这句是真的")
        gh = bot_core.group_chat_history.get(gid, [])
        check("Bot 自己的话入 history（assistant 轮）",
              gh and gh[-1]["role"] == "assistant" and "猫是我弟" in gh[-1]["content"])
    finally:
        bot_core.group_chat_history.clear()
        bot_core.group_chat_history.update(orig_hist)


def main():
    test_own_msg_registered()
    test_match_own_reply()
    test_record_bot_group_msg()
    test_display_name()
    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
