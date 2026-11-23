# -*- coding: utf-8 -*-
"""tests/test_private_reply_target.py —— 私聊合批要留住「回的是哪一条」

判据：
  [A] 合批入队用的仍是 `_merged_...` 合成 id（入口去重要靠它唯一）
  [B] 真实 message_id 走 `_reply_to_msg_id` 单独带出，不被合成 id 顶掉
  [C] 连发多条时，带的是**最后一条**的真实 id
  [D] 账本写入点优先用 `_reply_to_msg_id`

为什么有这条：2026-11-23 生产账本里 4 条私聊的 `reply_to` 全是
`_merged_<uid>_<ms>`，只能指到「哪一批」，回不到「哪一条」。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import bot_core as B  # noqa: E402

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


class FakeQ:
    def __init__(self):
        self.items = []

    def put(self, x):
        self.items.append(x)


def main():
    print("=== A/B) 合成 id 与真实 id 分家 ===")
    q = FakeQ()
    orig_q = B.MSG_QUEUE
    B.MSG_QUEUE = q
    sender = "900000001"
    B.PRIVATE_MSG_BUFFER.clear()
    try:
        m1 = {"message_id": 111222333, "message": [], "sender": {"user_id": sender}}
        B._buffer_private_message(sender, "第一句", [], m1)
        rec = B.PRIVATE_MSG_BUFFER.get(sender) or {}
        check("A1 缓冲区记下真实 id", rec.get("last_msg_id") == "111222333", rec)
        timer = rec.get("timer")
        B._private_flush(sender)
        if timer:
            timer.cancel()
        check("B1 真的入队了一条", len(q.items) == 1, len(q.items))
        ev = q.items[0] if q.items else {}
        check("A2 message_id 仍是合成 id（去重需要唯一）",
              str(ev.get("message_id") or "").startswith("_merged_"), ev.get("message_id"))
        check("B2 真实 id 单独带出", ev.get("_reply_to_msg_id") == "111222333",
              ev.get("_reply_to_msg_id"))

        print("\n=== C) 连发多条取最后一条 ===")
        q.items.clear()
        B.PRIVATE_MSG_BUFFER.clear()
        B._buffer_private_message(sender, "第一句", [], {"message_id": 100001})
        B._buffer_private_message(sender, "第二句", [], {"message_id": 100002})
        B._buffer_private_message(sender, "第三句", [], {"message_id": 100003})
        rec = B.PRIVATE_MSG_BUFFER.get(sender) or {}
        t2 = rec.get("timer")
        B._private_flush(sender)
        if t2:
            t2.cancel()
        ev = q.items[0] if q.items else {}
        check("C1 带的是最后一条", ev.get("_reply_to_msg_id") == "100003",
              ev.get("_reply_to_msg_id"))
        check("C2 三条合成一条正文", "第三句" in str(ev.get("message")), ev.get("message"))
    finally:
        B.MSG_QUEUE = orig_q
        B.PRIVATE_MSG_BUFFER.clear()

    print("\n=== D) 账本写入点 ===")
    src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("D1 SENT 优先取 _reply_to_msg_id", 'msg.get("_reply_to_msg_id")' in src)
    check("D2 合批时写入该字段", '"_reply_to_msg_id": str(rec.get("last_msg_id") or "")' in src)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for x in FAILURES:
        print(f"  FAIL  {x}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
