# -*- coding: utf-8 -*-
"""tests/test_meme_collect_trigger.py —— 群表情包采集的「该不该收」判定

2026-10-31 真机：用户在群里发的表情包一张都没进库（`collected/` 里只有 10-30
的三张），自助排查发现 `extract_images` 里只认 `message_type == "group"`，
而**轮询路径**（`get_group_msg_history`）回来的消息没有这个字段 —— 于是群里
发图永远走不进去。这条链在真机上是静默的（失败被 try/except 吞掉），
所以必须有测试盯着判据本身。

锁四件事：
  A. 事件推送（message_type=group）→ 群
  B. 轮询路径（只有 _type=_group_id）→ 群
  C. 私聊模板（message_type=private）→ 不是群
  D. 脏输入（None / 字符串 / 空 dict）→ 不是群，且不抛异常

运行：python tests/test_meme_collect_trigger.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

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
        FAILURES.append(name + (f"  → {extra}" if extra else ""))
        print(f"  [FAIL] {name}  {extra}")


def main():
    f = bot_core._is_group_msg

    print("[A] 事件推送路径")
    check("message_type=group", f({"message_type": "group", "group_id": "829854136"}) is True)
    check("只有 message_type（无 id 也认）", f({"message_type": "group"}) is True)

    print("\n[B] 轮询路径（没有 message_type）")
    check("_type=_type", f({"_type": "group", "_group_id": "829854136"}) is True)
    check("只有 _group_id", f({"_group_id": "829854136"}) is True)
    check("只有 group_id", f({"group_id": "829854136"}) is True)
    check("回归：纯轮询结构（真机复现）",
          f({"message_id": 123, "time": 1793400000,
             "sender": {"user_id": 3150007406},
             "_type": "group", "_group_id": "829854136",
             "message": [{"type": "image", "data": {"url": "x.jpg"}}]}) is True)

    print("\n[C] 私聊")
    check("message_type=private", f({"message_type": "private", "user_id": "1"}) is False)
    check("私聊合并重新入队的模板",
          f({"message_type": "private", "sender": {"user_id": "1"}, "message": []}) is False)
    check("空 group_id 不算群", f({"group_id": ""}) is False)

    print("\n[D] 脏输入")
    check("None", f(None) is False)
    check("字符串", f("group") is False)
    check("空 dict", f({}) is False)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        print("失败项：")
        for x in FAILURES:
            print("  -", x)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
