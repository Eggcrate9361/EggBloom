# -*- coding: utf-8 -*-
"""tests/test_trace_fields.py —— 账本新增字段：prompt 指纹与回复对象

判据：
  [A] `messages_digest` 只受 role/content/顺序影响，12 位十六进制
  [B] `note_prompt` 落一条 `PROMPT`，带 hash / chars / msgs / model，不含正文
  [C] `SENT` 能带 `reply_to` / `quote_id` / `merged`；空串字段被跳过
  [D] 主链真的在调：prompt 指纹写在 `_call_chat_sync`，reply_to 写在两处 SENT
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from _sandbox import sandbox_neurobase  # noqa: E402

sandbox_neurobase()

import harness.turn_trace as T  # noqa: E402

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


def last_for(turn):
    for rec in reversed(T.tail(200)):
        if str(rec.get("turn")) == turn:
            return rec
    return {}


def main():
    msgs = [{"role": "system", "content": "你是小a"},
            {"role": "user", "content": "在吗"}]

    print("=== A) prompt 指纹 ===")
    h1 = T.messages_digest(msgs)
    check("A1 同输入同指纹", h1 == T.messages_digest(list(msgs)), h1)
    check("A2 长度 12 且为十六进制",
          len(h1) == 12 and re.fullmatch(r"[0-9a-f]{12}", h1) is not None, h1)
    check("A3 改正文指纹变",
          T.messages_digest([dict(msgs[0]), {"role": "user", "content": "在吗?"}]) != h1)
    check("A4 改 role 指纹变",
          T.messages_digest([{"role": "user", "content": "你是小a"}, msgs[1]]) != h1)
    check("A5 换顺序指纹变",
          T.messages_digest([msgs[1], msgs[0]]) != h1)
    check("A6 非 dict 元素被忽略",
          T.messages_digest(list(msgs) + ["噪声", None]) == h1)
    check("A7 空列表不抛", isinstance(T.messages_digest([]), str))

    print("\n=== B) PROMPT 落盘 ===")
    tid = "ddddeeeeffff"
    ok = T.note_prompt(tid, msgs, model="deepseek-flash")
    rec = last_for(tid)
    check("B1 返回真", ok is True)
    check("B2 阶段是 PROMPT", rec.get("phase") == "PROMPT", rec)
    check("B3 带指纹", rec.get("hash") == h1, rec.get("hash"))
    check("B4 带字符数与条数",
          rec.get("chars") == sum(len(m["content"]) for m in msgs)
          and rec.get("msgs") == 2, rec)
    check("B5 带模型名", rec.get("model") == "deepseek-flash", rec.get("model"))
    check("B6 不落正文",
          "你是小a" not in str(rec) and "在吗" not in str(rec), rec)
    check("B7 PROMPT 不是终态", "PROMPT" not in T._TERMINAL_PHASES)

    print("\n=== C) SENT 的回复对象 ===")
    tid2 = "111122223333"
    T.note(tid2, "SENT", scene="group", target="g1", text_len=5,
           reply_to="900001", quote_id="", merged=0)
    r2 = last_for(tid2)
    check("C1 reply_to 落盘", r2.get("reply_to") == "900001", r2)
    check("C2 空 quote_id 被跳过", "quote_id" not in r2, r2)
    check("C3 merged=0 仍然保留", r2.get("merged") == 0, r2)

    print("\n=== D) 主链调用点 ===")
    src = open(os.path.join(ROOT, "bot_core.py"), encoding="utf-8").read()
    check("D1 模型调用处记指纹", "_note_prompt(turn_id, messages, model_name)" in src)
    check("D2 群聊 SENT 带 reply_to",
          'reply_to=str(buffer.get("message_id") or "")' in src)
    check("D3 私聊 SENT 带 reply_to（优先真实 id）",
          'msg.get("_reply_to_msg_id")' in src)
    check("D4 群聊 SENT 带引用与合并数",
          'quote_id=str(_quote_id or "")' in src and 'merged=int(buffer.get("merged_n") or 0)' in src)

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    for x in FAILURES:
        print(f"  FAIL  {x}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
