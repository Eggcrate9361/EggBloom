# -*- coding: utf-8 -*-
"""tests/test_extract_at.py —— @ 提及不许从文本里消失（2026-10-14 指代不清根因）

线上实证（23:17 群[20001]）：占位群友C发「@示例昵称 你不要和 @某某 说话」，
事件段=['at','text','at','text']，extract_text 抽出来却是
「你不要和␣␣说话」—— 旧版 extract_text 没有 at 分支，@ 段整个被丢，
被 @ 的人从文本里蒸发，模型根本不知道"和谁说话"说的是谁 → 答非所问。

钉死：
  ① at 段（带 nickname）→ 渲染 @昵称，位置保序；
  ② at 段（只有 qq）→ 走昵称缓存解析；缓存没有 → 裸号兜底；
  ③ @全体成员 → "@全体成员"；
  ④ 畸形 at 段（无昵称无qq）→ 不产生空"@"；
  ⑤ Bot 被 @ 时文本里保留 @Bot昵称（is_at_bot 判定不受影响，走段不走文本）。
"""
import os
import sys

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


def main():
    bot_core.QQ_NICK_CACHE["10000005"] = "占位群友C"
    bot_core.QQ_NICK_CACHE["10000006"] = "占位昵称2"

    print("\n=== ① 事故复现：夹在句中的第三人 @ 必须保留 ===")
    msg = {"message": [
        {"type": "at", "data": {"qq": "10000", "nickname": "示例昵称"}},
        {"type": "text", "data": {"text": "你不要和"}},
        {"type": "at", "data": {"qq": "10000005", "nickname": "占位群友C"}},
        {"type": "text", "data": {"text": "说话"}},
    ]}
    t = bot_core.extract_text(msg)
    check("★ 第三人@被保留（旧版这里变成空洞）", "占位群友C" in t, repr(t))
    check("★ 位置保序：和@占位群友C 说话", "和 @占位群友C 说话" in t, repr(t))
    check("Bot 的 @ 也在（is_at_bot 走段判定，文本带不带都行）",
          "示例昵称" in t, repr(t))

    print("\n=== ② 无 nickname 字段 → 昵称缓存解析 ===")
    t2 = bot_core.extract_text(
        {"message": [{"type": "at", "data": {"qq": "10000006"}},
                     {"type": "text", "data": {"text": "走吗"}}]})
    check("缓存命中 → @昵称", "@占位昵称2" in t2, repr(t2))
    t2b = bot_core.extract_text(
        {"message": [{"type": "at", "data": {"qq": "999999999"}},
                     {"type": "text", "data": {"text": "在吗"}}]})
    check("缓存没有 → 裸号兜底（绝不丢）", "@999999999" in t2b, repr(t2b))

    print("\n=== ③ @全体成员 ===")
    t3 = bot_core.extract_text(
        {"message": [{"type": "at", "data": {"qq": "all"}},
                     {"type": "text", "data": {"text": "开会"}}]})
    check("qq=all → @全体成员", "@全体成员" in t3, repr(t3))

    print("\n=== ④ 畸形 at 段不产生空@ ===")
    t4 = bot_core.extract_text(
        {"message": [{"type": "at", "data": {}},
                     {"type": "text", "data": {"text": "哈喽"}}]})
    check("无昵称无qq → 不出现光杆@", "@ " not in t4 and not t4.startswith("@"),
          repr(t4))

    print("\n=== ⑤ NT 形态 atElement 也认 ===")
    t5 = bot_core.extract_text(
        {"message": [{"elementType": 1, "textElement": {"content": "看这个"}},
                     {"atElement": {"targetUid": "123", "memberName": "张三"}}]})
    check("atElement.memberName → @张三", "@张三" in t5, repr(t5))

    print("\n=== ⑥ 配套：'@我 且 @别人' 不算定向他人（不能被吞） ===")
    check("命令我+提到别人 → 不跳过",
          not bot_core._is_at_others("@AAA 你不要和 @占位群友C 说话"))
    check("纯群友互@ → 仍然定向他人",
          bot_core._is_at_others("@占位群友C 你怎么看"))

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
