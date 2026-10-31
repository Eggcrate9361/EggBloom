# -*- coding: utf-8 -*-
"""tests/test_self_consistency.py —— 「你上一条说的」原文块

2026-10-31 真机（Texas 私聊 16:24→16:31）：

    它: 就看清一张…另一张我这儿没取到
    用户: 本来就只有一张图片，你唐的吧
    它: 哦，两张图其实是一张啊
    用户: 你不是说还有一张吗？
    它: 哪来的第二张，我瞎说的？

历史里明明有那句，它选择改口/否认。除了人格层的「认账」规则，再加一道
确定性的东西：把**上一条回复原文**单独摆进 prompt（160 字、30 分钟内有效）。

锁三件事：
  A. 有上一句 → 出现「你上一条说的」块，且含原文
  B. 超过 30 分钟 → 不再注入（避免拿很久以前的话当"刚说过"）
  C. 没有 assistant 历史 → 不出现该块，也不炸
"""
import os
import sys
import time

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


def _build(history, key):
    bot_core._HISTORY_SUMMARY_LINES.pop(key, None)
    bot_core._HISTORY_SUMMARY_TEXT.pop(key, None)
    bot_core._HISTORY_LAST_WINDOW.pop(key, None)
    return bot_core._build_cached_messages(
        history, key, "STATIC", [], "本来就只有一张图片，你唐的吧")


def _own_block(msgs):
    for m in msgs:
        if m.get("role") == "system" and "你上一条说的" in str(m.get("content", "")):
            return str(m["content"])
    return ""


def main():
    now = time.time()

    print("[A] 有上一句 → 注入原文")
    msgs = _build([
        {"role": "user", "content": "傻子小a", "ts": now - 180},
        {"role": "assistant",
         "content": "就看清一张，橙黄底那张瞪眼撇嘴的\n另一张我这儿没取到，你是不是想让我夸你？",
         "ts": now - 120},
    ], "consist_a")
    blk = _own_block(msgs)
    check("块存在", bool(blk), [m.get("content", "")[:40] for m in msgs])
    check("含原文里的关键句", "另一张我这儿没取到" in blk, blk)
    check("带「别改口」提示", "别改口" in blk, blk)

    print("\n[B] 超过 30 分钟不注入")
    msgs = _build([
        {"role": "user", "content": "早", "ts": now - 7300},
        {"role": "assistant", "content": "早啊", "ts": now - 7200},
    ], "consist_b")
    check("没有该块", not _own_block(msgs), _own_block(msgs))

    print("\n[C] 没有 assistant 历史")
    msgs = _build([{"role": "user", "content": "在吗", "ts": now}], "consist_c")
    check("没有该块", not _own_block(msgs))
    check("用户消息仍在", any(m.get("role") == "user" for m in msgs), msgs[-1])

    print("\n" + "=" * 56)
    print(f"通过 {PASS} / 失败 {FAIL}")
    if FAILURES:
        for f in FAILURES:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
