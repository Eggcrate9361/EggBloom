# -*- coding: utf-8 -*-
"""tests/test_quote_reply.py —— 群回复可以带**引用**（对标 qq-bridge 的 qq_reply）

用户 2026-10-13：「另一个 AI 可以直接引用别人的对话然后回复」→ 给了
https://github.com/Derpyu520/qq-bridge（dsh + SnowLuma OneBot v11 的桥）。
研究结论：它发消息时带 OneBot 的 `reply` 段（qq_reply / replyToMessageId）。

本机实测（SnowLuma 1.14.10）踩出来的两个坑，都锁进测试：
  1. **发引用要 `data.id`（QQ message_id）**，不是驱动内部元素名 `replySeq`。
     给 replySeq → 驱动 `fromSegment` 走 intOr(data.id)=0 → 打
     `could not quote the target message` 并**静默丢掉引用**（retcode 仍为 0）。
  2. 引用必须走**段数组**形式；顺带要把文本里的 [CQ:at,qq=..] 转成真正的 at 段，
     否则段数组里的 CQ 码会被当字面量显示。

何时引用（配置 group_quote_mode）：
  off=不引用 / auto=只在对象明确时（合并了多条历史、或对方本来就发了引用）/ always=每条都引用
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import bot_core  # noqa: E402

# 停掉 bot_core 带起来的守护线程：否则测试全过、退出码却是 0xC0000409
# （解释器关闭时 daemon 线程还在写 stdout，抢锁失败 → 拖垮退出码）。
# 详见 tests/_quiet_exit.py 开头的说明。
sys.path.insert(0, HERE)
from _quiet_exit import quiet_exit  # noqa: E402
quiet_exit()

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


def main():
    print("=== ① 引用段构造：data.id 而不是 replySeq ===")
    out = bot_core.quote_message_payload("我在的", 123456)
    check("返回段数组", isinstance(out, list), type(out).__name__)
    check("★ 第一段是 reply", bool(out) and out[0].get("type") == "reply", str(out[:1])[:80])
    check("★ 用 data.id（不是 replySeq）",
          bool(out) and "id" in out[0].get("data", {}) and "replySeq" not in out[0].get("data", {}),
          str(out[:1])[:90])
    check("id 是字符串且等于传入值",
          bool(out) and str(out[0]["data"]["id"]) == "123456", str(out[:1])[:90])
    check("后面跟着文本段", any(x.get("type") == "text" and "我在的" in x["data"]["text"] for x in out[1:]),
          str(out[1:])[:90])

    # 不引用时必须原样返回字符串（保持今天的行为不变）
    check("reply_id=0 → 原样返回字符串", bot_core.quote_message_payload("普通回复", 0) == "普通回复")
    check("reply_id=None → 原样返回字符串", bot_core.quote_message_payload("普通回复", None) == "普通回复")

    print("\n=== ② CQ 码要转成真段（段数组里 CQ 不会自动解析）===")
    segs = bot_core.text_to_segments("[CQ:at,qq=10000] 你说的对")
    kinds = [x["type"] for x in segs]
    check("at 变成 at 段", "at" in kinds, str(kinds))
    at_seg = next((x for x in segs if x["type"] == "at"), None)
    check("at 段带 qq", bool(at_seg) and str(at_seg["data"].get("qq")) == "10000", str(at_seg)[:80])
    check("at 后面的正文仍是文本段", any(x["type"] == "text" and "你说的对" in x["data"]["text"] for x in segs),
          str(segs)[:120])
    segs2 = bot_core.text_to_segments("[CQ:face,id=14] 早")
    check("face 变成 face 段", any(x["type"] == "face" for x in segs2), str(segs2)[:90])
    segs3 = bot_core.text_to_segments("没有 CQ 的普通话")
    check("无 CQ → 单个文本段", len(segs3) == 1 and segs3[0]["type"] == "text", str(segs3)[:80])
    segs4 = bot_core.text_to_segments("[CQ:image,file=a.jpg,url=http://x] 图没了")
    check("未知 CQ 原样留作文本（不丢字）", any("[CQ:image" in x["data"]["text"] for x in segs4 if x["type"] == "text"),
          str(segs4)[:120])

    print("\n=== ③ 何时引用（group_quote_mode）===")
    orig_cfg = dict(bot_core._config)
    try:
        # off
        bot_core._config["group_quote_mode"] = "off"
        check("off → 永不引用", bot_core.quote_target_for({"message_id": 55, "merged_n": 3}) == 0)
        # always
        bot_core._config["group_quote_mode"] = "always"
        check("always → 总是引用（单条也引）",
              bot_core.quote_target_for({"message_id": 55, "merged_n": 0}) == 55)
        # auto
        bot_core._config["group_quote_mode"] = "auto"
        check("auto · 单条且无引用 → 不引用（跟 qq-bridge 同判据）",
              bot_core.quote_target_for({"message_id": 55, "merged_n": 0, "has_quote": False}) == 0)
        check("auto · 合并了多条历史 → 引用",
              bot_core.quote_target_for({"message_id": 55, "merged_n": 2, "has_quote": False}) == 55)
        check("auto · 对方本来就发了引用 → 引用",
              bot_core.quote_target_for({"message_id": 55, "merged_n": 0, "has_quote": True}) == 55)
        check("没有 message_id → 0", bot_core.quote_target_for({"merged_n": 3}) == 0)
        check("空 buffer → 0", bot_core.quote_target_for(None) == 0)
    finally:
        bot_core._config.clear()
        bot_core._config.update(orig_cfg)

    print("\n=== ④ 发送链路真的把引用带上（只挂第一条）===")
    sent = []
    orig_api = bot_core.api_call
    orig_sleep = bot_core.time.sleep
    try:
        bot_core.api_call = lambda ep, data=None, timeout=15: (
            sent.append((ep, data.get("message"))) or {"retcode": 0})
        bot_core.time.sleep = lambda *_a, **_k: None
        # 短消息拆句：第一条带引用，后面不带
        sent.clear()
        bot_core.smart_send_group("123", "第一句。第二句。", reply_id=999)
        first = sent[0][1] if sent else None
        check("★ 第一条是带引用的段数组", isinstance(first, list) and first[0]["type"] == "reply",
              str(first)[:80])
        rest = [m for _e, m in sent[1:] if isinstance(m, list)]
        check("后续分句不再带引用（避免刷屏）", all(
            not (isinstance(m, list) and m and m[0].get("type") == "reply") for m in rest), str(sent[1:])[:100])
        # 不引用时 payload 仍是字符串（今天的行为）
        sent.clear()
        bot_core.smart_send_group("123", "就一句话", reply_id=0)
        check("不引用时仍是纯字符串 payload", isinstance(sent[0][1], str), str(sent[0][1])[:60])
        # 长消息（一次性发送）也带引用
        sent.clear()
        bot_core.smart_send_group("123", "长" * 60, reply_id=777)
        check("长消息一次性发送也带引用",
              isinstance(sent[0][1], list) and sent[0][1][0]["data"].get("id") == "777", str(sent[0][1])[:90])
    finally:
        bot_core.api_call = orig_api
        bot_core.time.sleep = orig_sleep

    print("\n" + "=" * 58)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
